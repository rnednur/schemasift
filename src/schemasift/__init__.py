"""SchemaSift public API."""

from .models import (
    ColumnMetadata,
    DatabaseSchema,
    Relationship,
    Rule,
    SchemaSelectionRequest,
    SchemaSelectionResult,
    SelectionOptions,
    TableMetadata,
)
from .selector import SchemaSelector
from .config import load_selector

__all__ = [
    "ColumnMetadata", "DatabaseSchema", "Relationship", "Rule",
    "SchemaSelectionRequest", "SchemaSelectionResult", "SchemaSelector",
    "SelectionOptions", "TableMetadata", "load_selector",
]

__version__ = "0.1.0"
