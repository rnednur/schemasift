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
from .client import SchemaSiftClient
from .config import load_selector
from .selector import SchemaSelector

__all__ = [
    "ColumnMetadata", "DatabaseSchema", "Relationship", "Rule",
    "SchemaSelectionRequest", "SchemaSelectionResult", "SchemaSelector",
    "SchemaSiftClient", "SelectionOptions", "TableMetadata", "load_selector",
]

__version__ = "0.1.0"
