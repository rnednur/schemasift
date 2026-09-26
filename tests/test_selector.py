from __future__ import annotations

import asyncio
import unittest

from schemasift.models import (
    BatchingOptions, ColumnMetadata, DatabaseSchema, Relationship,
    SchemaSelectionRequest, SelectionBudget, SelectionOptions, TableMetadata,
)
from schemasift.exceptions import BudgetExceeded
from schemasift.providers.base import Candidate, ProviderBatchResult, ProviderCapabilities
from schemasift.providers.lexical import LexicalProvider
from schemasift.selector import SchemaSelector
from schemasift.tracing import MemoryTraceSink


class TrackingProvider(LexicalProvider):
    capabilities = ProviderCapabilities(maximum_questions=120)

    def __init__(self):
        super().__init__()
        self.calls: list[int] = []
        self.active = 0
        self.max_active = 0

    async def classify(self, **kwargs):
        self.calls.append(len(kwargs["candidates"]))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(.01)
        try:
            return await super().classify(**kwargs)
        finally:
            self.active -= 1


class FailingProvider(TrackingProvider):
    async def classify(self, **kwargs):
        if kwargs["candidates"] and kwargs["candidates"][0].kind == "column" \
                and kwargs["candidates"][0].name.endswith("attribute_48"):
            raise RuntimeError("simulated provider failure")
        return await super().classify(**kwargs)


def schema(column_count: int = 4) -> DatabaseSchema:
    customer_columns = [
        ColumnMetadata(name="customer_id", type="INTEGER", primary_key=True),
        ColumnMetadata(name="currency", type="TEXT", description="billing currency",
                       samples=["EUR", "CZK"]),
    ]
    customer_columns.extend(ColumnMetadata(name=f"attribute_{i}", type="TEXT")
                            for i in range(max(0, column_count - 2)))
    return DatabaseSchema(
        database="shop",
        tables=[
            TableMetadata(name="customers", description="customer accounts", columns=customer_columns),
            TableMetadata(name="orders", description="customer purchases", columns=[
                ColumnMetadata(name="order_id", primary_key=True),
                ColumnMetadata(name="customer_id", foreign_key=True),
                ColumnMetadata(name="amount", type="REAL"),
            ]),
        ],
        relationships=[Relationship(from_table="orders", from_column="customer_id",
                                    to_table="customers", to_column="customer_id")],
    )


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_selects_relevant_schema_and_structural_keys(self):
        trace = MemoryTraceSink()
        selector = SchemaSelector({"lexical": LexicalProvider()}, default_provider="lexical",
                                  trace_sink=trace)
        request = SchemaSelectionRequest(
            question="What ratio of customers use EUR currency?", schema=schema(),
            options=SelectionOptions(provider="lexical"),
        )
        result = await selector.select(request)
        self.assertEqual([table.name for table in result.selected_tables], ["customers"])
        names = {column.qualified_name for column in result.selected_columns}
        self.assertIn("customers.currency", names)
        self.assertIn("customers.customer_id", names)
        self.assertTrue(any(event["event"] == "provider.batch.completed" for event in trace.events))

    async def test_two_hundred_columns_are_batched_concurrently(self):
        provider = TrackingProvider()
        selector = SchemaSelector({"local": provider}, default_provider="local")
        data = schema(200)
        data.tables = data.tables[:1]
        data.relationships = []
        request = SchemaSelectionRequest(
            question="Which customer has attribute 199?", schema=data,
            options=SelectionOptions(
                provider="local", preserve_structural_columns=False,
                batching=BatchingOptions(max_candidates_per_request=50,
                                         max_questions_per_request=120,
                                         max_input_bytes=60_000, concurrency=4),
            ),
        )
        result = await selector.select(request)
        # One table batch followed by four 50-column batches.
        self.assertEqual(provider.calls[0], 1)
        self.assertEqual(provider.calls[1:], [50, 50, 50, 50])
        self.assertEqual(result.execution.column_requests, 4)
        self.assertGreaterEqual(provider.max_active, 2)

    async def test_relationship_bridge_is_added(self):
        data = schema()
        data.tables.append(TableMetadata(name="products", description="product catalog", columns=[
            ColumnMetadata(name="product_id", primary_key=True),
        ]))
        data.tables[1].columns.append(ColumnMetadata(name="product_id", foreign_key=True))
        data.relationships.append(Relationship(from_table="orders", from_column="product_id",
                                               to_table="products", to_column="product_id"))
        class BridgeProvider(LexicalProvider):
            async def classify(self, **kwargs):
                result = await super().classify(**kwargs)
                if kwargs["candidates"] and kwargs["candidates"][0].kind == "table":
                    for decision in result.decisions:
                        if decision.candidate_id == "table:orders":
                            from schemasift.models import Classification, Probabilities
                            decision.classification = Classification.UNLIKELY
                            decision.probabilities = Probabilities(direct=.01, possible=.01, unlikely=.98)
                        else:
                            from schemasift.models import Classification, Probabilities
                            decision.classification = Classification.DIRECT
                            decision.probabilities = Probabilities(direct=.98, possible=.01, unlikely=.01)
                return result
        selector = SchemaSelector({"lexical": BridgeProvider()}, default_provider="lexical")
        result = await selector.select(SchemaSelectionRequest(
            question="Show customer products", schema=data,
            options=SelectionOptions(provider="lexical"),
        ))
        names = {table.name for table in result.selected_tables}
        self.assertEqual(names, {"customers", "orders", "products"})
        bridge = next(table for table in result.selected_tables if table.name == "orders")
        self.assertIn("relationship_bridge", bridge.selection_sources)

    async def test_partial_failure_retains_unevaluated_candidates(self):
        provider = FailingProvider()
        selector = SchemaSelector({"local": provider}, default_provider="local")
        data = schema(102)
        data.tables = data.tables[:1]
        data.relationships = []
        result = await selector.select(SchemaSelectionRequest(
            question="customer currency", schema=data,
            options=SelectionOptions(
                provider="local", on_partial_failure="retain_unevaluated",
                preserve_structural_columns=False,
                batching=BatchingOptions(max_candidates_per_request=50,
                                         max_questions_per_request=120,
                                         max_input_bytes=60_000, concurrency=3),
            ),
        ))
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.warnings)
        retained = {column.qualified_name: column for column in result.selected_columns}
        self.assertIn("customers.attribute_48", retained)
        self.assertIn("provider_failure_retained",
                      retained["customers.attribute_48"].selection_sources)

    async def test_total_timeout_budget(self):
        provider = TrackingProvider()
        selector = SchemaSelector({"local": provider}, default_provider="local")
        with self.assertRaises(BudgetExceeded):
            await selector.select(SchemaSelectionRequest(
                question="customer currency", schema=schema(),
                options=SelectionOptions(provider="local",
                                         budget=SelectionBudget(timeout_ms=1)),
            ))
