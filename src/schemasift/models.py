from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Rule(StrictModel):
    id: str | None = None
    description: str
    expression: str | None = None
    applies_to: list[str] = Field(default_factory=list)
    required_columns: list[str] = Field(default_factory=list)
    always_apply: bool = False


class ColumnStatistics(StrictModel):
    distinct_count: int | None = Field(default=None, ge=0)
    null_fraction: float | None = Field(default=None, ge=0, le=1)
    minimum: Any | None = None
    maximum: Any | None = None


class ColumnMetadata(StrictModel):
    name: str = Field(min_length=1)
    type: str | None = None
    description: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    samples: list[Any] = Field(default_factory=list, max_length=20)
    statistics: ColumnStatistics | None = None
    primary_key: bool = False
    foreign_key: bool = False
    nullable: bool | None = None


class TableMetadata(StrictModel):
    name: str = Field(min_length=1)
    description: str | None = None
    grain: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    columns: list[ColumnMetadata]

    @model_validator(mode="after")
    def unique_columns(self) -> "TableMetadata":
        names = [column.name.casefold() for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"table {self.name!r} contains duplicate column names")
        return self


class Relationship(StrictModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    type: str | None = None
    description: str | None = None
    rules: list[Rule] = Field(default_factory=list)


class DatabaseSchema(StrictModel):
    database: str = Field(min_length=1)
    description: str | None = None
    rules: list[Rule] = Field(default_factory=list)
    tables: list[TableMetadata]
    relationships: list[Relationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "DatabaseSchema":
        table_names = {table.name.casefold(): table for table in self.tables}
        if len(table_names) != len(self.tables):
            raise ValueError("schema contains duplicate table names")
        columns = {(table.name.casefold(), column.name.casefold())
                   for table in self.tables for column in table.columns}
        for relationship in self.relationships:
            left = (relationship.from_table.casefold(), relationship.from_column.casefold())
            right = (relationship.to_table.casefold(), relationship.to_column.casefold())
            if left not in columns or right not in columns:
                raise ValueError(f"relationship references an unknown column: {relationship}")
        return self


class SelectionBudget(StrictModel):
    max_candidates: int = Field(default=5000, ge=1)
    max_model_requests: int = Field(default=100, ge=1)
    max_input_bytes: int = Field(default=60_000, ge=1000)
    max_estimated_cost_usd: float | None = Field(default=None, ge=0)
    timeout_ms: int = Field(default=10_000, ge=1)


class BatchingOptions(StrictModel):
    max_candidates_per_request: int = Field(default=50, ge=1)
    max_questions_per_request: int = Field(default=120, ge=1)
    max_input_bytes: int = Field(default=60_000, ge=1000)
    concurrency: int = Field(default=4, ge=1, le=64)


class SelectionOptions(StrictModel):
    provider: str = "default"
    retain_classes: set[Literal["DIRECT", "POSSIBLE"]] = Field(
        default_factory=lambda: {"DIRECT", "POSSIBLE"}
    )
    relevance_threshold: float | None = Field(default=None, ge=0, le=1)
    preserve_structural_columns: bool = True
    preserve_relationship_bridges: bool = True
    include_roles: bool = False
    role_threshold: float = Field(default=0.5, ge=0, le=1)
    max_columns: int | None = Field(default=None, ge=1)
    on_partial_failure: Literal["fail", "retain_unevaluated"] = "fail"
    batching: BatchingOptions = Field(default_factory=BatchingOptions)
    budget: SelectionBudget = Field(default_factory=SelectionBudget)


class TraceContext(StrictModel):
    run_id: str | None = None
    question_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class SchemaSelectionRequest(StrictModel):
    question: str = Field(min_length=1)
    evidence: str | None = None
    database_schema: DatabaseSchema = Field(alias="schema", serialization_alias="schema")
    options: SelectionOptions = Field(default_factory=SelectionOptions)
    trace: TraceContext = Field(default_factory=TraceContext)


class Classification(str, Enum):
    DIRECT = "DIRECT"
    POSSIBLE = "POSSIBLE"
    UNLIKELY = "UNLIKELY"
    STRUCTURAL = "STRUCTURAL"
    RULE_REQUIRED = "RULE_REQUIRED"


class Probabilities(StrictModel):
    direct: float = Field(ge=0, le=1)
    possible: float = Field(ge=0, le=1)
    unlikely: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def approximately_normalized(self) -> "Probabilities":
        if not 0.98 <= self.direct + self.possible + self.unlikely <= 1.02:
            raise ValueError("classification probabilities must sum to approximately 1")
        return self

    @property
    def relevance(self) -> float:
        return self.direct + self.possible


class CandidateDecision(StrictModel):
    candidate_id: str
    classification: Classification
    probabilities: Probabilities | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    selected: bool = False
    selection_sources: list[str] = Field(default_factory=list)
    roles: dict[str, float] = Field(default_factory=dict)


class SelectedTable(CandidateDecision):
    name: str


class SelectedColumn(CandidateDecision):
    table: str
    name: str
    qualified_name: str


class Usage(StrictModel):
    requests: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None


class Timing(StrictModel):
    total_ms: float
    validation_ms: float = 0
    table_selection_ms: float = 0
    column_selection_ms: float = 0
    role_selection_ms: float = 0
    structural_closure_ms: float = 0


class ProviderInfo(StrictModel):
    requested: str
    used: str
    adapter: str
    requested_model: str
    returned_models: list[str] = Field(default_factory=list)


class ExecutionInfo(StrictModel):
    table_requests: int = 0
    column_requests: int = 0
    role_requests: int = 0
    evaluated_tables: int = 0
    evaluated_columns: int = 0
    concurrency: int = 1


class SelectionMetrics(StrictModel):
    available_tables: int
    selected_tables: int
    available_columns: int
    selected_columns: int
    table_reduction: float = Field(ge=0, le=1)
    column_reduction: float = Field(ge=0, le=1)


class UncertainExclusion(StrictModel):
    candidate_id: str
    kind: Literal["table", "column"]
    classification: Classification
    relevance: float | None = Field(default=None, ge=0, le=1)
    provider_confidence: float | None = Field(default=None, ge=0, le=1)


class SelectionConfidence(StrictModel):
    score: float = Field(ge=0, le=1)
    level: Literal["low", "medium", "high"]
    calibrated: bool = False
    max_excluded_table_relevance: float | None = Field(default=None, ge=0, le=1)
    max_excluded_column_relevance: float | None = Field(default=None, ge=0, le=1)
    selection_margin: float | None = Field(default=None, ge=-1, le=1)
    uncertain_exclusion_count: int = Field(default=0, ge=0)
    uncertain_exclusions: list[UncertainExclusion] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)


def _legacy_confidence() -> SelectionConfidence:
    return SelectionConfidence(
        score=0.0,
        level="low",
        calibrated=False,
        risk_reasons=["confidence_not_returned_by_server"],
    )


class WarningMessage(StrictModel):
    code: str
    message: str
    candidate_ids: list[str] = Field(default_factory=list)


class SchemaSelectionResult(StrictModel):
    request_id: str
    trace_id: str
    schema_fingerprint: str
    status: Literal["complete", "partial"] = "complete"
    selected_tables: list[SelectedTable]
    selected_columns: list[SelectedColumn]
    relationships: list[Relationship]
    compiled_schema: DatabaseSchema
    metrics: SelectionMetrics
    # Default keeps newer clients compatible with older SchemaSift servers.
    confidence: SelectionConfidence = Field(default_factory=_legacy_confidence)
    warnings: list[WarningMessage] = Field(default_factory=list)
    provider: ProviderInfo
    usage: Usage
    timing: Timing
    execution: ExecutionInfo
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
