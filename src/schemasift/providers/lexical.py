from __future__ import annotations

import re
import time
from typing import Any, Sequence

from schemasift.models import CandidateDecision, Classification, Probabilities
from .base import Candidate, ProviderBatchResult, ProviderCapabilities


def _terms(value: Any) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", str(value).casefold()) if len(term) > 2}


class LexicalProvider:
    """Fast deterministic baseline and offline development provider."""

    adapter = "lexical"
    capabilities = ProviderCapabilities(supports_usage=False)

    def __init__(self, name: str = "lexical", model: str = "lexical-v1"):
        self.name = name
        self.model = model

    async def classify(self, *, question: str, evidence: str | None,
                       candidates: Sequence[Candidate], context: dict[str, Any]) -> ProviderBatchResult:
        started = time.perf_counter()
        query = _terms(question) | _terms(evidence or "")
        decisions = []
        for candidate in candidates:
            terms = _terms(candidate.name) | _terms(candidate.metadata)
            overlap = len(query & terms)
            structural = bool(candidate.metadata.get("primary_key") or candidate.metadata.get("foreign_key"))
            if overlap:
                probabilities = Probabilities(direct=.82, possible=.15, unlikely=.03)
                classification = Classification.DIRECT
            elif structural:
                probabilities = Probabilities(direct=.05, possible=.65, unlikely=.30)
                classification = Classification.POSSIBLE
            else:
                probabilities = Probabilities(direct=.02, possible=.18, unlikely=.80)
                classification = Classification.UNLIKELY
            decisions.append(CandidateDecision(candidate_id=candidate.id,
                                                classification=classification,
                                                probabilities=probabilities,
                                                confidence=max(probabilities.direct,
                                                               probabilities.possible,
                                                               probabilities.unlikely)))
        return ProviderBatchResult(decisions, self.model,
                                   latency_ms=(time.perf_counter() - started) * 1000)

    async def classify_roles(self, *, question: str, evidence: str | None,
                             candidates: Sequence[Candidate], roles: Sequence[str],
                             context: dict[str, Any]) -> ProviderBatchResult:
        result = await self.classify(question=question, evidence=evidence,
                                     candidates=candidates, context=context)
        for decision, candidate in zip(result.decisions, candidates):
            name = candidate.name.casefold()
            decision.roles = {
                role: _role_probability(role, question.casefold(), name,
                                        candidate.metadata) for role in roles
            }
        return result

    async def health(self) -> bool:
        return True


def _role_probability(role: str, question: str, name: str, metadata: dict[str, Any]) -> float:
    if role == "JOIN" and (metadata.get("primary_key") or metadata.get("foreign_key")):
        return .8
    if role == "TIME" and any(word in name for word in ("date", "time", "year", "month")):
        return .8
    if role == "AGGREGATE" and any(word in question for word in ("total", "average", "count", "ratio")):
        return .55
    return .2

