from __future__ import annotations

from .models import Schema


def compile_context(schema: Schema, tables: set[str] | None = None,
                    columns: set[str] | None = None, include_descriptions: bool = True,
                    include_samples: bool = True, include_relationships: bool = True) -> str:
    selected_tables = tables or {table.name for table in schema.tables}
    lines = [f"DATABASE: {schema.database_id}", ""]
    for table in schema.tables:
        if table.name not in selected_tables:
            continue
        lines.append(f"TABLE {table.name}")
        if include_descriptions and table.description:
            lines.append(f"Purpose: {table.description}")
        for column in table.columns:
            if columns is not None and column.qualified_name not in columns:
                continue
            details = [column.data_type] if column.data_type else []
            if column.primary_key:
                details.append("PRIMARY KEY")
            if column.foreign_key:
                details.append("FOREIGN KEY")
            suffix = f" ({', '.join(details)})" if details else ""
            description = f" — {column.description}" if include_descriptions and column.description else ""
            samples = f" samples={list(column.samples)!r}" if include_samples and column.samples else ""
            lines.append(f"- {column.name}{suffix}{description}{samples}")
        lines.append("")
    if include_relationships:
        relevant = [r for r in schema.relationships
                    if r.from_table in selected_tables and r.to_table in selected_tables
                    and (columns is None or (f"{r.from_table}.{r.from_column}" in columns
                                             and f"{r.to_table}.{r.to_column}" in columns))]
        if relevant:
            lines.append("RELATIONSHIPS")
            lines.extend(f"- {r.from_table}.{r.from_column} -> {r.to_table}.{r.to_column}" for r in relevant)
    return "\n".join(lines).strip() + "\n"

