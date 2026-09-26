from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from schemasift.models import CandidateDecision


@dataclass(frozen=True)
class Candidate:
    id: str
    kind: str
    name: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_probabilities: bool = True
    supports_roles: bool = True
    supports_usage: bool = True
    maximum_questions: int | None = None
    maximum_input_bytes: int | None = None


@dataclass
class ProviderBatchResult:
    decisions: list[CandidateDecision]
    returned_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: float = 0
    request_id: str | None = None
    raw: dict[str, Any] | None = None


class DecisionProvider(Protocol):
    name: str
    adapter: str
    model: str
    capabilities: ProviderCapabilities

    async def classify(
        self,
        *,
        question: str,
        evidence: str | None,
        candidates: Sequence[Candidate],
        context: dict[str, Any],
    ) -> ProviderBatchResult: ...

    async def classify_roles(
        self,
        *,
        question: str,
        evidence: str | None,
        candidates: Sequence[Candidate],
        roles: Sequence[str],
        context: dict[str, Any],
    ) -> ProviderBatchResult: ...

    async def health(self) -> bool: ...

