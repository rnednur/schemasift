from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    data_type: str = ""
    description: str = ""
    samples: tuple[Any, ...] = ()
    primary_key: bool = False
    foreign_key: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.table}.{self.name}"


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class Table:
    name: str
    description: str = ""
    columns: list[Column] = field(default_factory=list)


@dataclass
class Schema:
    database_id: str
    tables: list[Table]
    relationships: list[Relationship] = field(default_factory=list)

    def table_map(self) -> dict[str, Table]:
        return {table.name.lower(): table for table in self.tables}

    def all_columns(self) -> list[Column]:
        return [column for table in self.tables for column in table.columns]


@dataclass(frozen=True)
class Question:
    question_id: str
    database_id: str
    question: str
    gold_sql: str
    evidence: str = ""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_seconds: float = 0.0
    estimated_cost: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
            self.latency_seconds + other.latency_seconds,
            self.estimated_cost + other.estimated_cost,
        )


@dataclass
class Selection:
    direct: set[str] = field(default_factory=set)
    possible: set[str] = field(default_factory=set)
    unlikely: set[str] = field(default_factory=set)
    structural: set[str] = field(default_factory=set)
    usage: Usage = field(default_factory=Usage)

    def retained(self, classes: set[str]) -> set[str]:
        result = set(self.structural)
        if "D" in classes:
            result |= self.direct
        if "P" in classes:
            result |= self.possible
        return result


@dataclass
class RunRecord:
    values: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.values


def as_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value

