from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Schema


@dataclass(frozen=True)
class GoldSchema:
    tables: frozenset[str]
    columns: frozenset[str]
    roles: dict[str, frozenset[str]] | None = None


def extract_gold_schema(sql: str, schema: Schema) -> GoldSchema:
    """Extract required objects, preferring sqlglot and falling back conservatively."""
    try:
        from sqlglot import exp, parse_one  # type: ignore

        tree = parse_one(sql, read="sqlite")
        aliases: dict[str, str] = {}
        tables = set()
        for node in tree.find_all(exp.Table):
            name = node.name
            actual = schema.table_map().get(name.lower())
            # Derived-table and CTE aliases are SQL objects, not selectable schema tables.
            if actual:
                tables.add(actual.name)
                aliases[(node.alias or name).lower()] = actual.name
        columns = set()
        for node in tree.find_all(exp.Column):
            table = aliases.get(node.table.lower(), _canonical_table(node.table, schema)) if node.table else ""
            resolved = _resolve_column(table, node.name, tables, schema)
            if resolved:
                columns.add(resolved)
        roles = _extract_roles(tree, aliases, tables, schema, exp)
        return GoldSchema(frozenset(filter(None, tables)), frozenset(columns), roles)
    except Exception:
        return _fallback_extract(sql, schema)


def _extract_roles(tree, aliases: dict[str, str], tables: set[str], schema: Schema, exp) -> dict[str, frozenset[str]]:
    roles: dict[str, set[str]] = {
        "OUTPUT": set(), "FILTER": set(), "JOIN": set(), "GROUP_BY": set(),
        "ORDER_BY": set(), "AGGREGATE": set(),
    }
    for node in tree.find_all(exp.Column):
        table = aliases.get(node.table.lower(), _canonical_table(node.table, schema)) if node.table else ""
        column = _resolve_column(table, node.name, tables, schema)
        if not column:
            continue
        if node.find_ancestor(exp.Join):
            roles["JOIN"].add(column)
        if node.find_ancestor(exp.Where) or node.find_ancestor(exp.Having):
            roles["FILTER"].add(column)
        if node.find_ancestor(exp.Group):
            roles["GROUP_BY"].add(column)
        if node.find_ancestor(exp.Order):
            roles["ORDER_BY"].add(column)
        if node.find_ancestor(exp.AggFunc):
            roles["AGGREGATE"].add(column)
        select = node.find_ancestor(exp.Select)
        if select and any(node is candidate or node in list(candidate.walk()) for candidate in select.expressions):
            roles["OUTPUT"].add(column)
    return {name: frozenset(values) for name, values in roles.items()}


def _canonical_table(name: str, schema: Schema) -> str:
    if not name:
        return ""
    table = schema.table_map().get(name.lower())
    return table.name if table else name


def _resolve_column(table: str, name: str, used_tables: set[str], schema: Schema) -> str | None:
    if table:
        actual = schema.table_map().get(table.lower())
        if actual:
            for column in actual.columns:
                if column.name.lower() == name.lower():
                    return column.qualified_name
        # Unknown qualifiers are normally CTE/subquery aliases or derived columns.
        return None
    matches = [column.qualified_name for candidate in schema.tables if candidate.name in used_tables
               for column in candidate.columns if column.name.lower() == name.lower()]
    return matches[0] if len(matches) == 1 else None


def _fallback_extract(sql: str, schema: Schema) -> GoldSchema:
    clean = re.sub(r"--.*?$|/\*.*?\*/|'(?:''|[^'])*'", " ", sql, flags=re.M | re.S)
    aliases: dict[str, str] = {}
    tables: set[str] = set()
    for match in re.finditer(r"\b(?:FROM|JOIN)\s+([\w\"`\[\].]+)(?:\s+(?:AS\s+)?(?!ON\b|WHERE\b|JOIN\b|GROUP\b|ORDER\b|LIMIT\b)(\w+))?", clean, re.I):
        raw = match.group(1).strip('"`[]').split(".")[-1]
        table = _canonical_table(raw, schema)
        tables.add(table)
        aliases[(match.group(2) or raw).lower()] = table
    columns: set[str] = set()
    for qualifier, name in re.findall(r"\b(\w+)\.(\w+)\b", clean):
        table = aliases.get(qualifier.lower(), _canonical_table(qualifier, schema))
        resolved = _resolve_column(table, name, tables, schema)
        if resolved:
            columns.add(resolved)
    for table in tables:
        actual = schema.table_map().get(table.lower())
        if actual:
            for column in actual.columns:
                if re.search(rf"(?<![\w.]){re.escape(column.name)}(?!\w)", clean, re.I):
                    columns.add(column.qualified_name)
    return GoldSchema(frozenset(tables), frozenset(columns), _fallback_roles(clean, aliases, tables, schema))


def _fallback_roles(sql: str, aliases: dict[str, str], tables: set[str], schema: Schema) -> dict[str, frozenset[str]]:
    boundaries = {
        "OUTPUT": (r"\bSELECT\b", r"\bFROM\b"),
        "JOIN": (r"\bON\b", r"\b(?:JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b"),
        "FILTER": (r"\b(?:WHERE|HAVING)\b", r"\b(?:GROUP\s+BY|ORDER\s+BY|LIMIT)\b"),
        "GROUP_BY": (r"\bGROUP\s+BY\b", r"\b(?:HAVING|ORDER\s+BY|LIMIT)\b"),
        "ORDER_BY": (r"\bORDER\s+BY\b", r"\bLIMIT\b"),
    }
    roles: dict[str, set[str]] = {name: set() for name in (*boundaries, "AGGREGATE")}
    for role, (start, end) in boundaries.items():
        for match in re.finditer(start + r"(.*?)(?=" + end + r"|$)", sql, re.I | re.S):
            roles[role] |= _columns_in_fragment(match.group(1), aliases, tables, schema)
    for match in re.finditer(r"\b(?:SUM|AVG|COUNT|MIN|MAX)\s*\((.*?)\)", sql, re.I | re.S):
        roles["AGGREGATE"] |= _columns_in_fragment(match.group(1), aliases, tables, schema)
    return {name: frozenset(values) for name, values in roles.items()}


def _columns_in_fragment(fragment: str, aliases: dict[str, str], tables: set[str], schema: Schema) -> set[str]:
    result: set[str] = set()
    for qualifier, name in re.findall(r"[\"`\[]?(\w+)[\"`\]]?\s*\.\s*[\"`\[]?(\w+)[\"`\]]?", fragment):
        resolved = _resolve_column(aliases.get(qualifier.lower(), qualifier), name, tables, schema)
        if resolved:
            result.add(resolved)
    for table in tables:
        actual = schema.table_map().get(table.lower())
        if actual:
            for column in actual.columns:
                if re.search(rf"(?<![\w.]){re.escape(column.name)}(?!\w)", fragment, re.I):
                    result.add(column.qualified_name)
    return result
