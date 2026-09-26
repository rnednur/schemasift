from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from schemasift.providers.base import Candidate
from schemasift.providers.system_one import SystemOneProvider
from schemasift.providers.replay import ReplayProvider


class SystemOneTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_system_one_contract(self):
        async def handler(request: httpx.Request):
            payload = json.loads(request.content)
            answers = {
                key: {"type": "choice", "choice": "DIRECT", "confidence": .9,
                      "probabilities": {"DIRECT": .9, "POSSIBLE": .08, "UNLIKELY": .02}}
                for key in payload["questions"]
            }
            return httpx.Response(200, json={"model": "jev-test", "answers": answers,
                                             "usage": {"input_tokens": 12, "output_tokens": 3}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SystemOneProvider(name="test", base_url="https://example.test",
                                     model="jev-test", input_cost_per_million=.042,
                                     client=client)
        result = await provider.classify(
            question="find customers", evidence=None,
            candidates=[Candidate("table:customers", "table", "customers", {})], context={},
        )
        self.assertEqual(result.decisions[0].candidate_id, "table:customers")
        self.assertEqual(result.decisions[0].classification.value, "DIRECT")
        self.assertEqual(result.input_tokens, 12)
        self.assertAlmostEqual(result.estimated_cost_usd, 12 * .042 / 1_000_000)
        await client.aclose()

    async def test_retries_transient_status_only(self):
        attempts = 0

        async def handler(request: httpx.Request):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503, text="temporarily unavailable")
            payload = json.loads(request.content)
            answers = {
                key: {"type": "choice", "choice": "DIRECT",
                      "probabilities": {"DIRECT": .9, "POSSIBLE": .08, "UNLIKELY": .02}}
                for key in payload["questions"]
            }
            return httpx.Response(200, json={"model": "jev-test", "answers": answers})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SystemOneProvider(name="test", base_url="https://example.test",
                                     model="jev-test", max_retries=2, client=client)
        result = await provider.classify(
            question="find customers", evidence=None,
            candidates=[Candidate("table:customers", "table", "customers", {})], context={},
        )
        self.assertEqual(attempts, 3)
        self.assertEqual(result.decisions[0].classification.value, "DIRECT")
        await client.aclose()

    async def test_does_not_retry_permanent_client_error(self):
        attempts = 0

        async def handler(request: httpx.Request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, text="invalid API key")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SystemOneProvider(name="test", base_url="https://example.test",
                                     model="jev-test", max_retries=3, client=client)
        with self.assertRaises(httpx.HTTPStatusError):
            await provider.classify(
                question="find customers", evidence=None,
                candidates=[Candidate("table:customers", "table", "customers", {})], context={},
            )
        self.assertEqual(attempts, 1)
        await client.aclose()

    async def test_replay_reads_trace_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text(json.dumps({
                "event": "provider.batch.completed",
                "decisions": [{"candidate_id": "table:customers",
                               "classification": "DIRECT",
                               "probabilities": {"direct": .9, "possible": .08,
                                                 "unlikely": .02}}],
            }) + "\n")
            provider = ReplayProvider(path)
            result = await provider.classify(
                question="customers", evidence=None,
                candidates=[Candidate("table:customers", "table", "customers", {})], context={},
            )
            self.assertEqual(result.decisions[0].classification.value, "DIRECT")
