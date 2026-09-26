from __future__ import annotations

from schemasift.models import (
    BatchingOptions, ColumnMetadata, DatabaseSchema, Relationship as SiftRelationship,
    SchemaSelectionRequest, SelectionOptions, TableMetadata, TraceContext,
)

from .models import Question, Schema


def to_schemasift_schema(schema: Schema) -> DatabaseSchema:
    return DatabaseSchema(
        database=schema.database_id,
        tables=[TableMetadata(
            name=table.name,
            description=table.description or None,
            columns=[ColumnMetadata(
                name=column.name, type=column.data_type or None,
                description=column.description or None,
                samples=list(column.samples), primary_key=column.primary_key,
                foreign_key=column.foreign_key,
            ) for column in table.columns],
        ) for table in schema.tables],
        relationships=[SiftRelationship(
            from_table=relationship.from_table, from_column=relationship.from_column,
            to_table=relationship.to_table, to_column=relationship.to_column,
        ) for relationship in schema.relationships],
    )


def to_selection_request(question: Question, schema: Schema, *, provider: str,
                         include_roles: bool = True, batch_size: int = 50,
                         concurrency: int = 4) -> SchemaSelectionRequest:
    return SchemaSelectionRequest(
        question=question.question, evidence=question.evidence or None,
        schema=to_schemasift_schema(schema),
        options=SelectionOptions(
            provider=provider, include_roles=include_roles,
            preserve_structural_columns=True, preserve_relationship_bridges=True,
            batching=BatchingOptions(max_candidates_per_request=batch_size,
                                     max_questions_per_request=120,
                                     max_input_bytes=60_000, concurrency=concurrency),
        ),
        trace=TraceContext(question_id=question.question_id),
    )

