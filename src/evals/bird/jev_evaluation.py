from __future__ import annotations

from pathlib import Path

from .dataset import find_database, inspect_sqlite
from .evaluation import selection_metrics
from .gold_sql import extract_gold_schema
from typing import Protocol
from schemasift.models import SchemaSelectionRequest, SchemaSelectionResult
from .models import Question, RunRecord


from .schemasift_adapter import to_selection_request


class SelectorClient(Protocol):
    async def select(self, request: SchemaSelectionRequest) -> SchemaSelectionResult: ...


async def evaluate_schemasift(question: Question, database_root: str | Path,
                              selector: SelectorClient, provider: str,
                              sample_limit: int = 3, run_id: str = "schemasift",
                              include_roles: bool = True) -> RunRecord:
    schema = inspect_sqlite(find_database(database_root, question.database_id),
                            question.database_id, sample_limit)
    gold = extract_gold_schema(question.gold_sql, schema)
    result = await selector.select(to_selection_request(question, schema, provider=provider,
                                                         include_roles=include_roles))
    predicted_tables = {table.name for table in result.selected_tables}
    predicted_columns = {column.qualified_name for column in result.selected_columns}
    predicted_roles = {
        role: {column.qualified_name for column in result.selected_columns
               if column.roles.get(role, 0) >= .5}
        for role in {"OUTPUT", "FILTER", "JOIN", "GROUP_BY", "ORDER_BY", "AGGREGATE"}
    }
    table_metrics = selection_metrics(set(gold.tables), predicted_tables, len(schema.tables))
    column_metrics = selection_metrics(set(gold.columns), predicted_columns, len(schema.all_columns()))
    gold_roles = gold.roles or {}
    role_metrics = {
        role: selection_metrics(set(gold_roles.get(role, set())), set(predicted_roles.get(role, set())),
                                len(schema.all_columns()))
        for role in predicted_roles
    }
    missed_columns = sorted(set(gold.columns) - predicted_columns)
    return RunRecord({
        "run_id": run_id, "question_id": question.question_id, "database_id": question.database_id,
        "question": question.question, "gold_sql": question.gold_sql, "strategy": "schemasift",
        "available_table_count": len(schema.tables), "available_column_count": len(schema.all_columns()),
        "selected_table_count": len(predicted_tables), "selected_column_count": len(predicted_columns),
        "gold_table_count": len(gold.tables), "gold_column_count": len(gold.columns),
        "gold_tables_retained": len(set(gold.tables) & predicted_tables),
        "gold_columns_retained": len(set(gold.columns) & predicted_columns),
        "selected_tables": sorted(predicted_tables), "selected_columns": sorted(predicted_columns),
        "gold_tables": sorted(gold.tables), "gold_columns": sorted(gold.columns),
        "table_recall": table_metrics["recall"], "table_precision": table_metrics["precision"],
        "column_recall": column_metrics["recall"], "column_precision": column_metrics["precision"],
        "schema_reduction_pct": column_metrics["reduction"] * 100,
        "gold_roles": {k: sorted(v) for k, v in gold_roles.items()},
        "predicted_roles": {k: sorted(v) for k, v in predicted_roles.items()},
        "role_metrics": role_metrics, "missed_gold_tables": sorted(set(gold.tables) - predicted_tables),
        "missed_gold_columns": missed_columns,
        "missed_gold_roles": {role: sorted(set(values) - set(predicted_roles.get(role, set())))
                              for role, values in gold_roles.items()},
        "table_probabilities": {table.name: table.probabilities.model_dump() if table.probabilities else None
                                for table in result.selected_tables},
        "column_probabilities": {column.qualified_name: column.probabilities.model_dump()
                                 if column.probabilities else None for column in result.selected_columns},
        "role_probabilities": {column.qualified_name: column.roles for column in result.selected_columns},
        "jev_input_tokens": result.usage.input_tokens,
        "jev_output_tokens": result.usage.output_tokens,
        "total_cost": result.usage.estimated_cost_usd,
        "jev_latency": result.timing.total_ms / 1000,
        "schemasift_trace_id": result.trace_id,
        "schemasift_request_id": result.request_id,
        "schemasift_status": result.status,
        "schemasift_execution": result.execution.model_dump(),
        "selection_confidence": result.confidence.score,
        "selection_confidence_level": result.confidence.level,
        "selection_confidence_calibrated": result.confidence.calibrated,
        "uncertain_exclusion_count": result.confidence.uncertain_exclusion_count,
        "uncertain_exclusions": [item.model_dump(mode="json")
                                 for item in result.confidence.uncertain_exclusions],
        "selection_risk_reasons": result.confidence.risk_reasons,
        "max_excluded_table_relevance": result.confidence.max_excluded_table_relevance,
        "max_excluded_column_relevance": result.confidence.max_excluded_column_relevance,
        "selection_margin": result.confidence.selection_margin,
    })
