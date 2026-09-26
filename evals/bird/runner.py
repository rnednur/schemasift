from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .compiler import compile_context
from .dataset import find_database, inspect_sqlite
from .evaluation import execute_readonly, execution_correct, selection_metrics
from .gold_sql import extract_gold_schema
from .model_client import estimate_tokens
from .models import Question, RunRecord, Schema, Usage
from .selector import select_columns, select_tables


class SqlGenerator(Protocol):
    def generate(self, question: Question, context: str) -> tuple[str, Usage]: ...


class GoldSqlGenerator:
    """Pipeline validation oracle. Never use its accuracy as an experiment result."""
    def generate(self, question: Question, context: str) -> tuple[str, Usage]:
        return question.gold_sql, Usage(input_tokens=estimate_tokens(context + question.question))


@dataclass
class ExperimentRunner:
    database_root: Path
    selector_client: object | None = None
    sql_generator: SqlGenerator | None = None
    retain: frozenset[str] = frozenset({"D", "P"})
    preserve_structural: bool = True
    include_descriptions: bool = True
    include_samples: bool = True
    include_relationships: bool = True
    sample_limit: int = 3

    def run_question(self, question: Question, strategy: str, run_id: str) -> RunRecord:
        db_path = find_database(self.database_root, question.database_id)
        schema = inspect_sqlite(db_path, question.database_id, self.sample_limit)
        gold = extract_gold_schema(question.gold_sql, schema)
        full_context = compile_context(schema, include_descriptions=self.include_descriptions,
                                       include_samples=self.include_samples,
                                       include_relationships=self.include_relationships)
        selector_usage = Usage()
        if strategy == "baseline_full":
            tables = {table.name for table in schema.tables}
            columns = {column.qualified_name for column in schema.all_columns()}
            context = full_context
        else:
            table_selection = select_tables(question, schema, self.selector_client)  # type: ignore[arg-type]
            tables = table_selection.retained(set(self.retain))
            column_selection = select_columns(question, schema, tables, self.selector_client,  # type: ignore[arg-type]
                                              self.preserve_structural)
            columns = column_selection.retained(set(self.retain))
            selector_usage = table_selection.usage + column_selection.usage
            context = compile_context(schema, tables, columns, self.include_descriptions,
                                      self.include_samples, self.include_relationships)
        started = time.perf_counter()
        generated_sql, sql_usage = (self.sql_generator or GoldSqlGenerator()).generate(question, context)
        if not sql_usage.latency_seconds:
            sql_usage = Usage(sql_usage.input_tokens, sql_usage.output_tokens, sql_usage.cached_tokens,
                              time.perf_counter() - started, sql_usage.estimated_cost)
        gold_result = execute_readonly(db_path, question.gold_sql)
        generated_result = execute_readonly(db_path, generated_sql)
        table_metrics = selection_metrics(set(gold.tables), tables, len(schema.tables))
        column_metrics = selection_metrics(set(gold.columns), columns, len(schema.all_columns()))
        full_tokens, compiled_tokens = estimate_tokens(full_context), estimate_tokens(context)
        return RunRecord({
            "run_id": run_id, "question_id": question.question_id,
            "database_id": question.database_id, "strategy": strategy,
            "question": question.question, "gold_sql": question.gold_sql,
            "available_table_count": len(schema.tables), "selected_table_count": len(tables),
            "available_column_count": len(schema.all_columns()), "selected_column_count": len(columns),
            "gold_table_count": table_metrics["gold_count"], "gold_tables_retained": table_metrics["gold_retained"],
            "table_recall": table_metrics["recall"], "table_precision": table_metrics["precision"],
            "gold_column_count": column_metrics["gold_count"], "gold_columns_retained": column_metrics["gold_retained"],
            "column_recall": column_metrics["recall"], "column_precision": column_metrics["precision"],
            "schema_reduction_pct": column_metrics["reduction"] * 100,
            "metadata_token_reduction_pct": (1 - compiled_tokens / full_tokens) * 100 if full_tokens else 0,
            "selector_input_tokens": selector_usage.input_tokens,
            "selector_output_tokens": selector_usage.output_tokens,
            "selector_cached_tokens": selector_usage.cached_tokens,
            "selector_latency": selector_usage.latency_seconds,
            "selector_cost": selector_usage.estimated_cost,
            "sql_input_tokens": sql_usage.input_tokens, "sql_cached_tokens": sql_usage.cached_tokens,
            "sql_output_tokens": sql_usage.output_tokens, "sql_latency": sql_usage.latency_seconds,
            "sql_cost": sql_usage.estimated_cost,
            "total_tokens": selector_usage.input_tokens + selector_usage.output_tokens + sql_usage.input_tokens + sql_usage.output_tokens,
            "total_cost": selector_usage.estimated_cost + sql_usage.estimated_cost,
            "total_latency": selector_usage.latency_seconds + sql_usage.latency_seconds,
            "selected_tables": sorted(tables), "selected_columns": sorted(columns),
            "gold_tables": sorted(gold.tables), "gold_columns": sorted(gold.columns),
            "generated_sql": generated_sql,
            "execution_correct": execution_correct(gold_result, generated_result),
            "gold_execution_error": gold_result.error, "generated_execution_error": generated_result.error,
        })


def append_record(path: str | Path, record: RunRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), default=str) + "\n")

