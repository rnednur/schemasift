from __future__ import annotations

from collections import deque

from .models import DatabaseSchema, Relationship


def bridge_tables(schema: DatabaseSchema, selected: set[str]) -> set[str]:
    """Return shortest-path bridge tables connecting selected table pairs."""
    canonical = {table.name.casefold(): table.name for table in schema.tables}
    graph: dict[str, set[str]] = {table.name: set() for table in schema.tables}
    for relationship in schema.relationships:
        left = canonical.get(relationship.from_table.casefold())
        right = canonical.get(relationship.to_table.casefold())
        if left and right:
            graph[left].add(right)
            graph[right].add(left)
    result = set(selected)
    selected_list = sorted(selected)
    for index, source in enumerate(selected_list):
        for target in selected_list[index + 1:]:
            path = _shortest_path(graph, source, target)
            if path:
                result.update(path)
    return result


def _shortest_path(graph: dict[str, set[str]], source: str, target: str) -> list[str] | None:
    queue = deque([(source, [source])])
    visited = {source}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def selected_relationships(schema: DatabaseSchema, tables: set[str], columns: set[str]) -> list[Relationship]:
    result = []
    for relationship in schema.relationships:
        left = f"{relationship.from_table}.{relationship.from_column}"
        right = f"{relationship.to_table}.{relationship.to_column}"
        if (relationship.from_table in tables and relationship.to_table in tables
                and left in columns and right in columns):
            result.append(relationship)
    return result


def structural_columns(schema: DatabaseSchema, tables: set[str]) -> set[str]:
    columns = {
        f"{table.name}.{column.name}"
        for table in schema.tables if table.name in tables
        for column in table.columns if column.primary_key or column.foreign_key
    }
    for relationship in schema.relationships:
        if relationship.from_table in tables and relationship.to_table in tables:
            columns.add(f"{relationship.from_table}.{relationship.from_column}")
            columns.add(f"{relationship.to_table}.{relationship.to_column}")
    return columns


def rule_required_columns(schema: DatabaseSchema, tables: set[str]) -> set[str]:
    rules = list(schema.rules)
    for table in schema.tables:
        if table.name in tables:
            rules.extend(table.rules)
            for column in table.columns:
                rules.extend(column.rules)
    return {column for rule in rules if rule.always_apply for column in rule.required_columns}

