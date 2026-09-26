from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from schemasift.exceptions import ProviderError
from schemasift.models import CandidateDecision
from .base import Candidate, ProviderBatchResult, ProviderCapabilities


class ReplayProvider:
    adapter = "replay"
    capabilities = ProviderCapabilities()

    def __init__(self, records: dict[str, dict] | str | Path,
                 name: str = "replay", model: str = "replay-v1"):
        self.name = name
        self.model = model
        if isinstance(records, (str, Path)):
            self.records = _load_records(Path(records))
        else:
            self.records = records

    async def classify(self, *, question: str, evidence: str | None,
                       candidates: Sequence[Candidate], context: dict[str, Any]) -> ProviderBatchResult:
        decisions = []
        for candidate in candidates:
            record = self.records.get(candidate.id)
            if record is None:
                raise ProviderError(f"replay fixture has no decision for {candidate.id}")
            decisions.append(CandidateDecision.model_validate(record))
        return ProviderBatchResult(decisions, self.model)

    async def classify_roles(self, *, question: str, evidence: str | None,
                             candidates: Sequence[Candidate], roles: Sequence[str],
                             context: dict[str, Any]) -> ProviderBatchResult:
        return await self.classify(question=question, evidence=evidence,
                                   candidates=candidates, context=context)

    async def health(self) -> bool:
        return True


def _load_records(path: Path) -> dict[str, dict]:
    text = path.read_text()
    if path.suffix == ".jsonl":
        records: dict[str, dict] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            for item in event.get("decisions", []):
                candidate_id = item["candidate_id"]
                if candidate_id in records and item.get("roles"):
                    records[candidate_id]["roles"] = item["roles"]
                else:
                    records[candidate_id] = item
        return records
    data = json.loads(text)
    if isinstance(data, list):
        return {item["candidate_id"]: item for item in data}
    return data
