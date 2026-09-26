from __future__ import annotations

import json
import re

from .model_client import ModelClient
from .models import Column, Question, Schema, Selection, Usage


TABLE_SYSTEM = """You are a high-recall database table selector. Classify every table as D (direct), P (possible), or N (unlikely). Missing a required table is significantly more costly than retaining an unnecessary one. When uncertain between P and N, select P. Return only JSON: {\"D\":[],\"P\":[],\"N\":[]}."""

COLUMN_SYSTEM = """You are a high-recall database column selector. Classify every supplied qualified column as D (direct), P (possible), or N (unlikely). Join, filter, grouping, time, and measure columns may be essential. When uncertain between P and N, select P. Return only JSON: {\"D\":[],\"P\":[],\"N\":[]}."""


def select_tables(question: Question, schema: Schema, client: ModelClient | None) -> Selection:
    if client is None:
        return _heuristic_tables(question.question, schema)
    inventory = [{"name": t.name, "description": t.description} for t in schema.tables]
    response = client.complete_json(system=TABLE_SYSTEM, user=json.dumps({
        "question": question.question, "evidence": question.evidence, "tables": inventory
    }))
    return _selection_from_json(response.data, {t.name for t in schema.tables}, response.usage)


def select_columns(question: Question, schema: Schema, retained_tables: set[str],
                   client: ModelClient | None, preserve_structural: bool = True) -> Selection:
    columns = [c for t in schema.tables if t.name in retained_tables for c in t.columns]
    if client is None:
        selection = _heuristic_columns(question.question + " " + question.evidence, columns)
    else:
        payload = [{
            "column": c.qualified_name, "type": c.data_type,
            "description": c.description, "samples": list(c.samples)
        } for c in columns]
        response = client.complete_json(system=COLUMN_SYSTEM, user=json.dumps({
            "question": question.question, "evidence": question.evidence, "columns": payload
        }, default=str))
        selection = _selection_from_json(response.data, {c.qualified_name for c in columns}, response.usage)
    if preserve_structural:
        selection.structural |= structural_columns(schema, retained_tables)
    return selection


def structural_columns(schema: Schema, tables: set[str]) -> set[str]:
    result = {c.qualified_name for t in schema.tables if t.name in tables
              for c in t.columns if c.primary_key or c.foreign_key}
    for relationship in schema.relationships:
        if relationship.from_table in tables and relationship.to_table in tables:
            result.add(f"{relationship.from_table}.{relationship.from_column}")
            result.add(f"{relationship.to_table}.{relationship.to_column}")
    return result


def _selection_from_json(data: dict, universe: set[str], usage: Usage) -> Selection:
    normalized = {item.lower(): item for item in universe}
    groups: dict[str, set[str]] = {}
    seen: set[str] = set()
    for label in ("D", "P", "N"):
        values = data.get(label, data.get({"D": "direct", "P": "possible", "N": "unlikely"}[label], []))
        groups[label] = {normalized[str(value).lower()] for value in values if str(value).lower() in normalized}
        groups[label] -= seen
        seen |= groups[label]
    groups["N"] |= universe - seen
    return Selection(groups["D"], groups["P"], groups["N"], usage=usage)


def _terms(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _heuristic_tables(question: str, schema: Schema) -> Selection:
    query = _terms(question)
    direct, possible, unlikely = set(), set(), set()
    for table in schema.tables:
        table_terms = _terms(table.name + " " + table.description)
        column_terms = set().union(*(_terms(c.name) for c in table.columns)) if table.columns else set()
        if query & table_terms:
            direct.add(table.name)
        elif query & column_terms:
            possible.add(table.name)
        else:
            unlikely.add(table.name)
    return Selection(direct, possible, unlikely)


def _heuristic_columns(question: str, columns: list[Column]) -> Selection:
    query = _terms(question)
    direct, possible, unlikely = set(), set(), set()
    for column in columns:
        terms = _terms(column.name + " " + column.description + " " + " ".join(map(str, column.samples)))
        if query & terms:
            direct.add(column.qualified_name)
        elif column.primary_key or column.foreign_key:
            possible.add(column.qualified_name)
        else:
            unlikely.add(column.qualified_name)
    return Selection(direct, possible, unlikely)

