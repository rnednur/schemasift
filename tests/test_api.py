from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from schemasift.api import create_app
from schemasift.config import ProviderConfig, SchemaSiftConfig, TracingConfig
from schemasift.models import SchemaSelectionResult


class ApiTests(unittest.TestCase):
    def test_older_response_without_confidence_is_accepted(self):
        data = {
            "request_id": "r", "trace_id": "t", "schema_fingerprint": "sha256:x",
            "selected_tables": [], "selected_columns": [], "relationships": [],
            "compiled_schema": {"database": "db", "tables": []},
            "metrics": {"available_tables": 0, "selected_tables": 0,
                        "available_columns": 0, "selected_columns": 0,
                        "table_reduction": 0, "column_reduction": 0},
            "provider": {"requested": "p", "used": "p", "adapter": "a",
                         "requested_model": "m"},
            "usage": {}, "timing": {"total_ms": 1}, "execution": {},
        }
        result = SchemaSelectionResult.model_validate(data)
        self.assertEqual(result.confidence.level, "low")
        self.assertIn("confidence_not_returned_by_server", result.confidence.risk_reasons)

    def test_health_providers_and_selection(self):
        config = SchemaSiftConfig(
            default_provider="local",
            providers={"local": ProviderConfig(adapter="lexical", model="lexical-v1")},
            tracing=TracingConfig(enabled=False),
        )
        with TestClient(create_app(config)) as client:
            self.assertEqual(client.get("/", follow_redirects=False).status_code, 307)
            health = client.get("/health").json()
            self.assertEqual(health["status"], "ok")
            self.assertIn("selection_confidence", health["features"])
            self.assertEqual(client.get("/v1/providers").status_code, 200)
            response = client.post("/v1/schema/select", json={
                "question": "customer currency",
                "schema": {"database": "db", "tables": [{
                    "name": "customers", "columns": [
                        {"name": "id", "primary_key": True},
                        {"name": "currency", "description": "billing currency"},
                    ],
                }]},
                "options": {"provider": "local"},
            })
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["status"], "complete")
            self.assertEqual(data["provider"]["used"], "local")
            self.assertIn(data["confidence"]["level"], {"low", "medium", "high"})
            self.assertFalse(data["confidence"]["calibrated"])
            self.assertEqual({column["qualified_name"] for column in data["selected_columns"]},
                             {"customers.id", "customers.currency"})
