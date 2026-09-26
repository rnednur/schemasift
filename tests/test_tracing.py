from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from schemasift.tracing import JsonlTraceSink


class JsonlTraceSinkTests(unittest.TestCase):
    def test_concurrent_writes_across_event_loops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            sink = JsonlTraceSink(path)

            async def write_batch(start: int):
                await asyncio.gather(*(
                    sink.emit({"event": "test", "index": index})
                    for index in range(start, start + 25)
                ))

            asyncio.run(write_batch(0))
            asyncio.run(write_batch(25))

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 50)
            self.assertEqual({row["index"] for row in rows}, set(range(50)))
