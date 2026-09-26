from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from .batching import partition_candidates
from .exceptions import BudgetExceeded, ProviderError
from .fingerprint import schema_fingerprint
from .models import (
    CandidateDecision, Classification, ExecutionInfo, ProviderInfo,
    SchemaSelectionRequest, SchemaSelectionResult, SelectedColumn, SelectedTable,
    SelectionConfidence, SelectionMetrics, TableMetadata, Timing, UncertainExclusion,
    Usage, WarningMessage,
)
from .providers.base import Candidate, DecisionProvider, ProviderBatchResult
from .relationships import (bridge_tables, rule_required_columns,
                            selected_relationships, structural_columns)
from .tracing import NullTraceSink, TraceSink


ROLES = ("OUTPUT", "FILTER", "JOIN", "GROUP_BY", "ORDER_BY", "AGGREGATE", "TIME", "GEO")


@dataclass
class _Aggregate:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0
    has_input: bool = False
    has_output: bool = False
    has_cost: bool = False
    models: set[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.models = set()

    def add(self, result: ProviderBatchResult) -> None:
        self.requests += 1
        if result.input_tokens is not None:
            self.has_input = True; self.input_tokens += result.input_tokens
        if result.output_tokens is not None:
            self.has_output = True; self.output_tokens += result.output_tokens
        if result.estimated_cost_usd is not None:
            self.has_cost = True; self.cost += result.estimated_cost_usd
        if result.returned_model:
            self.models.add(result.returned_model)


class SchemaSelector:
    def __init__(self, providers: dict[str, DecisionProvider], *, default_provider: str,
                 trace_sink: TraceSink | None = None):
        if default_provider not in providers:
            raise ValueError(f"default provider {default_provider!r} is not registered")
        self.providers = providers
        self.default_provider = default_provider
        self.trace_sink = trace_sink or NullTraceSink()

    async def close(self) -> None:
        """Close provider-owned network clients when the selector is no longer needed."""
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close:
                await close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()

    async def select(self, request: SchemaSelectionRequest) -> SchemaSelectionResult:
        try:
            return await asyncio.wait_for(
                self._select_impl(request), timeout=request.options.budget.timeout_ms / 1000
            )
        except asyncio.TimeoutError as exc:
            raise BudgetExceeded(
                f"selection exceeded timeout budget of {request.options.budget.timeout_ms} ms"
            ) from exc

    async def _select_impl(self, request: SchemaSelectionRequest) -> SchemaSelectionResult:
        started = time.perf_counter()
        request_id = "sel_" + uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        fingerprint = schema_fingerprint(request.database_schema)
        provider_name = request.options.provider
        if provider_name == "default":
            provider_name = self.default_provider
        try:
            provider = self.providers[provider_name]
        except KeyError as exc:
            raise ProviderError(f"unknown provider {provider_name!r}") from exc
        await self._trace("selection.started", request_id, trace_id, request, {
            "schema_fingerprint": fingerprint, "provider": provider_name,
        })
        aggregate = _Aggregate()
        execution = ExecutionInfo(concurrency=request.options.batching.concurrency)
        warnings: list[WarningMessage] = []

        table_candidates = [_table_candidate(table) for table in request.database_schema.tables]
        self._check_candidate_budget(request, len(table_candidates))
        table_started = time.perf_counter()
        table_decisions, table_batches = await self._classify(
            provider, request, table_candidates, {"database": request.database_schema.database,
                                                   "phase": "table_selection"},
            aggregate, trace_id, request_id, warnings,
        )
        table_ms = (time.perf_counter() - table_started) * 1000
        execution.table_requests = table_batches
        execution.evaluated_tables = len(table_decisions)
        selected_table_names = {decision.candidate_id.removeprefix("table:")
                                for decision in table_decisions if _retain(decision, request)}
        initial_tables = set(selected_table_names)
        if request.options.preserve_relationship_bridges:
            selected_table_names = bridge_tables(request.database_schema, selected_table_names)

        column_candidates = [
            _column_candidate(table, column)
            for table in request.database_schema.tables if table.name in selected_table_names
            for column in table.columns
        ]
        self._check_candidate_budget(request, len(table_candidates) + len(column_candidates))
        column_started = time.perf_counter()
        column_decisions, column_batches = await self._classify(
            provider, request, column_candidates,
            {"database": request.database_schema.database, "phase": "column_selection",
             "selected_tables": sorted(selected_table_names)},
            aggregate, trace_id, request_id,
            warnings,
        ) if column_candidates else ([], 0)
        column_ms = (time.perf_counter() - column_started) * 1000
        execution.column_requests = column_batches
        execution.evaluated_columns = len(column_decisions)
        selected_column_names = {decision.candidate_id.removeprefix("column:")
                                 for decision in column_decisions if _retain(decision, request)}

        closure_started = time.perf_counter()
        structural = structural_columns(request.database_schema, selected_table_names) \
            if request.options.preserve_structural_columns else set()
        required_by_rules = rule_required_columns(request.database_schema, selected_table_names)
        selected_column_names |= structural | required_by_rules
        if request.options.max_columns and len(selected_column_names) > request.options.max_columns:
            raise BudgetExceeded(
                f"selection produced {len(selected_column_names)} columns, exceeding max_columns="
                f"{request.options.max_columns}; refusing to drop required context silently"
            )
        closure_ms = (time.perf_counter() - closure_started) * 1000

        role_ms = 0.0
        role_batches = 0
        if request.options.include_roles and selected_column_names:
            role_candidates = [candidate for candidate in column_candidates
                               if candidate.id.removeprefix("column:") in selected_column_names]
            role_started = time.perf_counter()
            role_results, role_batches = await self._roles(
                provider, request, role_candidates,
                {"database": request.database_schema.database, "phase": "role_selection"},
                aggregate, trace_id, request_id,
            )
            role_ms = (time.perf_counter() - role_started) * 1000
            role_map = {decision.candidate_id: decision.roles for decision in role_results}
            for decision in column_decisions:
                decision.roles = role_map.get(decision.candidate_id, {})
        execution.role_requests = role_batches

        table_by_id = {decision.candidate_id: decision for decision in table_decisions}
        column_by_id = {decision.candidate_id: decision for decision in column_decisions}
        selected_tables = []
        for table in request.database_schema.tables:
            if table.name not in selected_table_names:
                continue
            key = f"table:{table.name}"
            decision = table_by_id.get(key)
            if decision is None or table.name not in initial_tables:
                decision = CandidateDecision(candidate_id=key, classification=Classification.STRUCTURAL,
                                             selected=True, selection_sources=["relationship_bridge"])
            else:
                decision.selected = True
                if "provider_failure_retained" not in decision.selection_sources:
                    decision.selection_sources.append("model")
            selected_tables.append(SelectedTable(**decision.model_dump(), name=table.name))

        selected_columns = []
        for table in request.database_schema.tables:
            if table.name not in selected_table_names:
                continue
            for column in table.columns:
                qualified = f"{table.name}.{column.name}"
                if qualified not in selected_column_names:
                    continue
                key = f"column:{qualified}"
                decision = column_by_id.get(key)
                if decision is None:
                    classification = (Classification.RULE_REQUIRED if qualified in required_by_rules
                                      else Classification.STRUCTURAL)
                    decision = CandidateDecision(candidate_id=key, classification=classification)
                decision.selected = True
                if (_retain(decision, request)
                        and "provider_failure_retained" not in decision.selection_sources):
                    decision.selection_sources.append("model")
                if qualified in structural: decision.selection_sources.append("structural")
                if qualified in required_by_rules: decision.selection_sources.append("rule")
                decision.selection_sources = list(dict.fromkeys(decision.selection_sources))
                selected_columns.append(SelectedColumn(**decision.model_dump(), table=table.name,
                                                       name=column.name, qualified_name=qualified))

        relationships = selected_relationships(request.database_schema, selected_table_names,
                                               selected_column_names)
        compiled_tables = [TableMetadata(
            **table.model_dump(exclude={"columns"}),
            columns=[column for column in table.columns
                     if f"{table.name}.{column.name}" in selected_column_names],
        ) for table in request.database_schema.tables if table.name in selected_table_names]
        compiled_schema = request.database_schema.model_copy(update={
            "tables": compiled_tables, "relationships": relationships,
        })
        available_columns = sum(len(table.columns) for table in request.database_schema.tables)
        confidence = _selection_confidence(table_decisions, column_decisions,
                                           selected_table_names, selected_column_names, warnings)
        total_ms = (time.perf_counter() - started) * 1000
        result = SchemaSelectionResult(
            request_id=request_id, trace_id=trace_id, schema_fingerprint=fingerprint,
            status="partial" if warnings else "complete",
            selected_tables=selected_tables, selected_columns=selected_columns,
            relationships=relationships, compiled_schema=compiled_schema,
            metrics=SelectionMetrics(
                available_tables=len(request.database_schema.tables),
                selected_tables=len(selected_tables), available_columns=available_columns,
                selected_columns=len(selected_columns),
                table_reduction=(1 - len(selected_tables) / len(request.database_schema.tables)
                                 if request.database_schema.tables else 0),
                column_reduction=(1 - len(selected_columns) / available_columns
                                  if available_columns else 0),
            ), confidence=confidence, warnings=warnings,
            provider=ProviderInfo(requested=request.options.provider, used=provider_name,
                                  adapter=provider.adapter, requested_model=provider.model,
                                  returned_models=sorted(aggregate.models)),
            usage=Usage(requests=aggregate.requests,
                        input_tokens=aggregate.input_tokens if aggregate.has_input else None,
                        output_tokens=aggregate.output_tokens if aggregate.has_output else None,
                        estimated_cost_usd=aggregate.cost if aggregate.has_cost else None),
            timing=Timing(total_ms=total_ms, table_selection_ms=table_ms,
                          column_selection_ms=column_ms, role_selection_ms=role_ms,
                          structural_closure_ms=closure_ms), execution=execution,
        )
        await self.trace_sink.emit({"event": "selection.completed", "trace_id": trace_id,
                                    "request_id": request_id,
                                    "status": result.status,
                                    "schema_fingerprint": fingerprint,
                                    "selected_tables": [table.name for table in selected_tables],
                                    "selected_columns": [column.qualified_name for column in selected_columns],
                                    "metrics": result.metrics.model_dump(mode="json"),
                                    "confidence": result.confidence.model_dump(mode="json"),
                                    "provider": result.provider.model_dump(mode="json"),
                                    "usage": result.usage.model_dump(mode="json"),
                                    "timing": result.timing.model_dump(mode="json"),
                                    "execution": result.execution.model_dump(mode="json"),
                                    "warnings": [warning.model_dump(mode="json") for warning in warnings]})
        return result

    def _check_candidate_budget(self, request: SchemaSelectionRequest, count: int) -> None:
        if count > request.options.budget.max_candidates:
            raise BudgetExceeded(f"candidate count {count} exceeds budget {request.options.budget.max_candidates}")

    async def _classify(self, provider: DecisionProvider, request: SchemaSelectionRequest,
                        candidates: Sequence[Candidate], context: dict[str, Any],
                        aggregate: _Aggregate, trace_id: str, request_id: str,
                        warnings: list[WarningMessage]) -> tuple[list[CandidateDecision], int]:
        batches = self._batches(request, provider, candidates)
        self._check_request_budget(request, aggregate.requests + len(batches))
        semaphore = asyncio.Semaphore(request.options.batching.concurrency)

        async def run(index: int, batch: list[Candidate]) -> ProviderBatchResult:
            async with semaphore:
                started = time.perf_counter()
                try:
                    result = await provider.classify(question=request.question,
                                                     evidence=request.evidence,
                                                     candidates=batch, context=context)
                except Exception as exc:
                    await self.trace_sink.emit({"event": "provider.batch.failed", "trace_id": trace_id,
                                                "request_id": request_id, "batch": index,
                                                "candidate_ids": [c.id for c in batch], "error": str(exc)})
                    raise
                await self.trace_sink.emit({"event": "provider.batch.completed", "trace_id": trace_id,
                                            "request_id": request_id, "batch": index,
                                            "candidate_ids": [c.id for c in batch],
                                            "latency_ms": (time.perf_counter() - started) * 1000,
                                            "decisions": [d.model_dump(mode="json") for d in result.decisions]})
                return result

        results = await asyncio.gather(*(run(i, batch) for i, batch in enumerate(batches)),
                                       return_exceptions=request.options.on_partial_failure == "retain_unevaluated")
        decisions: list[CandidateDecision] = []
        expected = {candidate.id for candidate in candidates}
        for batch, result in zip(batches, results):
            if isinstance(result, BaseException):
                ids = [candidate.id for candidate in batch]
                warnings.append(WarningMessage(
                    code="PROVIDER_BATCH_FAILED_RETAINED",
                    message=f"Provider batch failed; {len(ids)} unevaluated candidates were retained",
                    candidate_ids=ids,
                ))
                decisions.extend(CandidateDecision(
                    candidate_id=candidate.id, classification=Classification.POSSIBLE,
                    selection_sources=["provider_failure_retained"],
                ) for candidate in batch)
                continue
            aggregate.add(result); decisions.extend(result.decisions)
        self._check_cost_budget(request, aggregate)
        received = [decision.candidate_id for decision in decisions]
        if len(received) != len(set(received)) or set(received) != expected:
            raise ProviderError("provider decisions do not exactly match requested candidate IDs")
        return decisions, len(batches)

    async def _roles(self, provider: DecisionProvider, request: SchemaSelectionRequest,
                     candidates: Sequence[Candidate], context: dict[str, Any],
                     aggregate: _Aggregate, trace_id: str, request_id: str) -> tuple[list[CandidateDecision], int]:
        batches = self._batches(request, provider, candidates, questions_per_candidate=len(ROLES))
        self._check_request_budget(request, aggregate.requests + len(batches))
        semaphore = asyncio.Semaphore(request.options.batching.concurrency)
        async def run(index: int, batch: list[Candidate]) -> ProviderBatchResult:
            async with semaphore:
                result = await provider.classify_roles(question=request.question,
                                                       evidence=request.evidence,
                                                       candidates=batch, roles=ROLES, context=context)
                await self.trace_sink.emit({"event": "provider.roles.completed", "trace_id": trace_id,
                                            "request_id": request_id, "batch": index,
                                            "candidate_ids": [c.id for c in batch],
                                            "decisions": [d.model_dump(mode="json") for d in result.decisions]})
                return result
        results = await asyncio.gather(*(run(index, batch) for index, batch in enumerate(batches)))
        decisions = []
        for result in results:
            aggregate.add(result); decisions.extend(result.decisions)
        self._check_cost_budget(request, aggregate)
        return decisions, len(batches)

    def _batches(self, request: SchemaSelectionRequest, provider: DecisionProvider,
                 candidates: Sequence[Candidate], questions_per_candidate: int = 1) -> list[list[Candidate]]:
        options = request.options.batching
        max_questions = min(options.max_questions_per_request,
                            provider.capabilities.maximum_questions or options.max_questions_per_request)
        max_bytes = min(options.max_input_bytes, request.options.budget.max_input_bytes,
                        provider.capabilities.maximum_input_bytes or options.max_input_bytes)
        return partition_candidates(candidates, max_candidates=options.max_candidates_per_request,
                                    max_questions=max_questions, max_input_bytes=max_bytes,
                                    questions_per_candidate=questions_per_candidate)

    @staticmethod
    def _check_request_budget(request: SchemaSelectionRequest, count: int) -> None:
        if count > request.options.budget.max_model_requests:
            raise BudgetExceeded(f"model request count {count} exceeds budget "
                                 f"{request.options.budget.max_model_requests}")

    @staticmethod
    def _check_cost_budget(request: SchemaSelectionRequest, aggregate: _Aggregate) -> None:
        maximum = request.options.budget.max_estimated_cost_usd
        if maximum is not None and aggregate.has_cost and aggregate.cost > maximum:
            raise BudgetExceeded(f"reported provider cost {aggregate.cost:.8f} exceeds budget {maximum:.8f}")

    async def _trace(self, event: str, request_id: str, trace_id: str,
                     request: SchemaSelectionRequest, values: dict[str, Any]) -> None:
        await self.trace_sink.emit({"event": event, "trace_id": trace_id,
                                    "request_id": request_id,
                                    "run_id": request.trace.run_id,
                                    "question_id": request.trace.question_id, **values})


def _retain(decision: CandidateDecision, request: SchemaSelectionRequest) -> bool:
    threshold = request.options.relevance_threshold
    if threshold is not None and decision.probabilities is not None:
        return decision.probabilities.relevance >= threshold
    return decision.classification.value in request.options.retain_classes


def _selection_confidence(table_decisions: Sequence[CandidateDecision],
                          column_decisions: Sequence[CandidateDecision],
                          selected_tables: set[str], selected_columns: set[str],
                          warnings: list[WarningMessage]) -> SelectionConfidence:
    excluded: list[UncertainExclusion] = []
    selected_relevance: list[float] = []
    table_risk: list[float] = []
    column_risk: list[float] = []
    for kind, decisions, selected, prefix in (
        ("table", table_decisions, selected_tables, "table:"),
        ("column", column_decisions, selected_columns, "column:"),
    ):
        for decision in decisions:
            relevance = decision.probabilities.relevance if decision.probabilities else None
            name = decision.candidate_id.removeprefix(prefix)
            if name in selected:
                if relevance is not None: selected_relevance.append(relevance)
                continue
            if relevance is not None:
                (table_risk if kind == "table" else column_risk).append(relevance)
            if (relevance is not None and relevance >= .2) or \
                    (decision.confidence is not None and decision.confidence <= .5):
                excluded.append(UncertainExclusion(
                    candidate_id=decision.candidate_id, kind=kind,
                    classification=decision.classification, relevance=relevance,
                    provider_confidence=decision.confidence,
                ))
    excluded.sort(key=lambda item: item.relevance or 0, reverse=True)
    max_table = max(table_risk, default=None)
    max_column = max(column_risk, default=None)
    max_risk = max([value for value in (max_table, max_column) if value is not None], default=0)
    score = max(0.0, min(1.0, 1 - max_risk))
    if warnings: score = min(score, .49)
    level = "high" if score >= .8 else "medium" if score >= .5 else "low"
    margin = (min(selected_relevance) - max_risk) if selected_relevance else None
    reasons = []
    if max_table is not None and max_table >= .2: reasons.append("excluded_table_has_material_relevance")
    if max_column is not None and max_column >= .2: reasons.append("excluded_column_has_material_relevance")
    if warnings: reasons.append("provider_or_batch_warning")
    return SelectionConfidence(score=score, level=level,
        max_excluded_table_relevance=max_table, max_excluded_column_relevance=max_column,
        selection_margin=margin, uncertain_exclusion_count=len(excluded),
        uncertain_exclusions=excluded[:10], risk_reasons=reasons)


def _table_candidate(table) -> Candidate:
    return Candidate(f"table:{table.name}", "table", table.name, {
        "description": table.description, "grain": table.grain,
        "semantic_tags": table.semantic_tags,
        "rules": [rule.model_dump(exclude_none=True) for rule in table.rules],
        "columns": [column.name for column in table.columns],
    })


def _column_candidate(table, column) -> Candidate:
    qualified = f"{table.name}.{column.name}"
    return Candidate(f"column:{qualified}", "column", qualified, {
        "table": table.name, "table_description": table.description, "table_grain": table.grain,
        "name": column.name, "type": column.type, "description": column.description,
        "semantic_tags": column.semantic_tags,
        "rules": [rule.model_dump(exclude_none=True) for rule in column.rules],
        "samples": column.samples, "statistics": column.statistics.model_dump() if column.statistics else None,
        "primary_key": column.primary_key, "foreign_key": column.foreign_key,
    })
