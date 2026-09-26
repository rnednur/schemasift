from __future__ import annotations

import unittest

from pydantic import ValidationError

from schemasift.fingerprint import schema_fingerprint
from schemasift.models import ColumnMetadata, DatabaseSchema, Relationship, TableMetadata


class ModelTests(unittest.TestCase):
    def test_fingerprint_is_stable(self):
        schema = DatabaseSchema(database="db", tables=[
            TableMetadata(name="t", columns=[ColumnMetadata(name="id", primary_key=True)])
        ])
        self.assertEqual(schema_fingerprint(schema), schema_fingerprint(schema.model_copy(deep=True)))

    def test_unknown_relationship_is_rejected(self):
        with self.assertRaises(ValidationError):
            DatabaseSchema(database="db", tables=[
                TableMetadata(name="t", columns=[ColumnMetadata(name="id")])
            ], relationships=[Relationship(from_table="t", from_column="missing",
                                           to_table="t", to_column="id")])

