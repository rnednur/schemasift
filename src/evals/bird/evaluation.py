from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def selection_metrics(gold: set[str] | frozenset[str], selected: set[str], available_count: int) -> dict[str, float | int]:
    retained = len(set(gold) & selected)
    return {
        "gold_count": len(gold),
        "gold_retained": retained,
        "recall": retained / len(gold) if gold else 1.0,
        "precision": retained / len(selected) if selected else (1.0 if not gold else 0.0),
        "reduction": 1.0 - (len(selected) / available_count) if available_count else 0.0,
    }


@dataclass(frozen=True)
class ExecutionResult:
    rows: tuple[tuple[Any, ...], ...] = ()
    error: str | None = None


def execute_readonly(database: str | Path, sql: str) -> ExecutionResult:
    connection = sqlite3.connect(f"file:{Path(database)}?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = tuple(tuple(_normalize(value) for value in row) for row in connection.execute(sql).fetchall())
        return ExecutionResult(rows=rows)
    except sqlite3.Error as exc:
        return ExecutionResult(error=str(exc))
    finally:
        connection.close()


def execution_correct(gold: ExecutionResult, generated: ExecutionResult) -> bool:
    if gold.error or generated.error:
        return False
    return sorted(gold.rows, key=repr) == sorted(generated.rows, key=repr)


def _normalize(value: Any) -> Any:
    return round(value, 8) if isinstance(value, float) else value
