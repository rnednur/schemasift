from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evals.bird.compiler import compile_context
from evals.bird.dataset import inspect_sqlite, load_questions
from evals.bird.evaluation import execute_readonly, execution_correct, selection_metrics
from evals.bird.gold_sql import extract_gold_schema
from evals.bird.jev import JevClient
from evals.bird.models import Question
from evals.bird.runner import ExperimentRunner
from evals.bird.schemasift_adapter import to_selection_request
from evals.bird.selector import structural_columns
from schemasift import SchemaSelector
from schemasift.providers import LexicalProvider


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        db_dir = root / "shop"
        db_dir.mkdir()
        self.db_path = db_dir / "shop.sqlite"
        connection = sqlite3.connect(self.db_path)
        connection.executescript("""
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, region TEXT);
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER,
              amount REAL, order_date TEXT,
              FOREIGN KEY(customer_id) REFERENCES customers(customer_id));
            INSERT INTO customers VALUES (1, 'east'), (2, 'west');
            INSERT INTO orders VALUES (1, 1, 10, '2024-01-01'), (2, 1, 15, '2024-02-01');
        """)
        connection.close()
        self.root = root
        self.schema = inspect_sqlite(self.db_path, "shop", 2)
        self.question = Question("q1", "shop", "total order amount by customer",
                                 "SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id")

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_and_structural_columns(self):
        self.assertEqual(len(self.schema.tables), 2)
        structural = structural_columns(self.schema, {"customers", "orders"})
        self.assertIn("orders.customer_id", structural)
        self.assertIn("customers.customer_id", structural)

    def test_gold_extraction_and_context(self):
        gold = extract_gold_schema(self.question.gold_sql, self.schema)
        self.assertEqual(gold.tables, frozenset({"orders"}))
        self.assertIn("orders.amount", gold.columns)
        self.assertIn("orders.amount", gold.roles["AGGREGATE"])
        self.assertIn("orders.customer_id", gold.roles["GROUP_BY"])
        context = compile_context(self.schema, {"orders"}, {"orders.amount"})
        self.assertIn("TABLE orders", context)
        self.assertNotIn("TABLE customers", context)

    def test_gold_extraction_ignores_derived_alias_columns(self):
        sql = ("SELECT T.total FROM (SELECT customer_id, SUM(amount) AS total "
               "FROM orders GROUP BY customer_id) AS T WHERE T.total > 10")
        gold = extract_gold_schema(sql, self.schema)
        self.assertEqual(gold.tables, frozenset({"orders"}))
        self.assertIn("orders.amount", gold.columns)
        self.assertIn("orders.customer_id", gold.columns)
        self.assertNotIn("T.total", gold.columns)

    def test_execution_and_metrics(self):
        result = execute_readonly(self.db_path, self.question.gold_sql)
        self.assertTrue(execution_correct(result, result))
        metrics = selection_metrics({"a", "b"}, {"a", "c"}, 4)
        self.assertEqual(metrics["recall"], .5)
        self.assertEqual(metrics["reduction"], .5)

    def test_runner_baseline(self):
        record = ExperimentRunner(self.root).run_question(self.question, "baseline_full", "test")
        self.assertTrue(record.values["execution_correct"])
        self.assertEqual(record.values["table_recall"], 1.0)

    def test_jev_response_mapping(self):
        class FakeJev(JevClient):
            def _post(self, payload):
                answers = {}
                for key, question in payload["questions"].items():
                    if question["type"] == "choice":
                        choice = "D" if key in {"table_1", "column_1_2"} else "N"
                        answers[key] = {"type": "choice", "choice": choice,
                                        "confidence": .9, "probabilities": {choice: .9}}
                    else:
                        answers[key] = {"type": "noul", "noul": .8 if key == "role_1_2_0" else .1}
                return {"model": "fake", "answers": answers,
                        "usage": {"input_tokens": 10, "output_tokens": 2}}

        prediction = FakeJev(api_key="test").predict(self.question, self.schema)
        self.assertIn("orders", prediction.tables)
        self.assertIn("orders.amount", prediction.columns)
        self.assertIn("orders.amount", prediction.roles["OUTPUT"])
        self.assertEqual(prediction.usage.input_tokens, 10)

    def test_schemasift_adapter(self):
        request = to_selection_request(self.question, self.schema, provider="local",
                                       include_roles=False)
        selector = SchemaSelector({"local": LexicalProvider(name="local")},
                                  default_provider="local")
        import asyncio
        result = asyncio.run(selector.select(request))
        self.assertEqual(result.provider.used, "local")
        self.assertIn("orders", {table.name for table in result.selected_tables})
        self.assertIn("orders.amount", {column.qualified_name for column in result.selected_columns})
        self.assertGreaterEqual(result.confidence.score, 0)


if __name__ == "__main__":
    unittest.main()
