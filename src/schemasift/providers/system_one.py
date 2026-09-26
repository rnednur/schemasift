from __future__ import annotations

import time
from typing import Any, Sequence

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from schemasift.exceptions import ProviderError
from schemasift.models import CandidateDecision, Classification, Probabilities
from .base import Candidate, ProviderBatchResult, ProviderCapabilities


CLASS_CRITERIA = {
    "DIRECT": "Directly required to answer the question.",
    "POSSIBLE": "Potentially useful, structurally needed, or uncertain; missing it could prevent a correct answer.",
    "UNLIKELY": "Unlikely to help answer the question.",
}


class _TransientProviderError(Exception):
    """An outbound failure that is safe to retry."""


class SystemOneProvider:
    adapter = "system_one"
    capabilities = ProviderCapabilities(maximum_questions=5000)

    def __init__(self, *, name: str, base_url: str, model: str,
                 api_key: str | None = None, endpoint: str = "/v1/systemone",
                 auth_header: str = "Authorization", auth_scheme: str = "Bearer",
                 timeout_ms: int = 10_000, max_retries: int = 2,
                 input_cost_per_million: float | None = None,
                 client: httpx.AsyncClient | None = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.input_cost_per_million = input_cost_per_million
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_ms / 1000),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        value = f"{self.auth_scheme} {self.api_key}".strip()
        return {self.auth_header: value}

    async def classify(self, *, question: str, evidence: str | None,
                       candidates: Sequence[Candidate], context: dict[str, Any]) -> ProviderBatchResult:
        questions = {
            f"candidate_{index}": {
                "type": "choice",
                "instructions": (
                    f"Classify this {candidate.kind} for high-recall schema selection: "
                    f"{candidate.name}. Missing required schema is much more costly than retaining extra schema."
                ),
                "criteria": CLASS_CRITERIA,
            }
            for index, candidate in enumerate(candidates)
        }
        state = {"question": question, "evidence": evidence or "",
                 "candidates": [{"id": f"candidate_{i}", "name": c.name,
                                 "kind": c.kind, "metadata": c.metadata}
                                for i, c in enumerate(candidates)],
                 "context": context}
        response, latency = await self._request({"model": self.model, "state": state,
                                                 "questions": questions})
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise ProviderError("System One response is missing answers")
        decisions = []
        for index, candidate in enumerate(candidates):
            key = f"candidate_{index}"
            answer = answers.get(key)
            if not isinstance(answer, dict):
                raise ProviderError(f"System One response is missing {key}")
            probabilities = _probabilities(answer.get("probabilities"))
            try:
                classification = Classification(str(answer["choice"]).upper())
            except (KeyError, ValueError) as exc:
                raise ProviderError(f"invalid choice answer for {key}: {answer}") from exc
            decisions.append(CandidateDecision(
                candidate_id=candidate.id, classification=classification,
                probabilities=probabilities, confidence=answer.get("confidence"),
            ))
        usage = response.get("usage") or {}
        return ProviderBatchResult(
            decisions, response.get("model"), usage.get("input_tokens"),
            usage.get("output_tokens"), self._cost(usage), latency,
            response.get("request_id"), response,
        )

    async def classify_roles(self, *, question: str, evidence: str | None,
                             candidates: Sequence[Candidate], roles: Sequence[str],
                             context: dict[str, Any]) -> ProviderBatchResult:
        questions = {}
        mapping = {}
        for candidate_index, candidate in enumerate(candidates):
            for role_index, role in enumerate(roles):
                key = f"role_{candidate_index}_{role_index}"
                mapping[key] = (candidate.id, role)
                questions[key] = {
                    "type": "noul",
                    "instructions": f"Will column {candidate.name} be needed in SQL role {role}?",
                }
        state = {"question": question, "evidence": evidence or "",
                 "columns": [{"id": c.id, "name": c.name, "metadata": c.metadata}
                             for c in candidates], "context": context}
        response, latency = await self._request({"model": self.model, "state": state,
                                                 "questions": questions})
        answers = response.get("answers") or {}
        role_values: dict[str, dict[str, float]] = {candidate.id: {} for candidate in candidates}
        for key, (candidate_id, role) in mapping.items():
            answer = answers.get(key)
            if not isinstance(answer, dict) or "noul" not in answer:
                raise ProviderError(f"System One response is missing valid role answer {key}")
            role_values[candidate_id][role] = float(answer["noul"])
        decisions = [CandidateDecision(candidate_id=c.id,
                                       classification=Classification.POSSIBLE,
                                       roles=role_values[c.id]) for c in candidates]
        usage = response.get("usage") or {}
        return ProviderBatchResult(decisions, response.get("model"),
                                   usage.get("input_tokens"), usage.get("output_tokens"),
                                   self._cost(usage), latency, response.get("request_id"), response)

    def _cost(self, usage: dict[str, Any]) -> float | None:
        if usage.get("cost") is not None:
            return float(usage["cost"])
        if self.input_cost_per_million is None or usage.get("input_tokens") is None:
            return None
        return float(usage["input_tokens"]) * self.input_cost_per_million / 1_000_000

    async def _request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_random_exponential(multiplier=.5, max=5),
            retry=retry_if_exception_type(_TransientProviderError),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    try:
                        response = await self.client.post(
                            self.base_url + self.endpoint,
                            json=payload,
                            headers=self._headers(),
                        )
                    except (httpx.TimeoutException, httpx.NetworkError) as exc:
                        raise _TransientProviderError(str(exc)) from exc
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        raise _TransientProviderError(
                            f"HTTP {response.status_code}: {response.text[:500]}"
                        )
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError("provider returned invalid JSON") from exc
                    if not isinstance(data, dict):
                        raise ProviderError("provider returned non-object JSON")
                    return data, (time.perf_counter() - started) * 1000
        except _TransientProviderError as exc:
            raise ProviderError(
                f"provider {self.name} failed after {self.max_retries + 1} attempts: {exc}"
            ) from exc
        raise ProviderError(f"provider {self.name} failed without a response")

    async def health(self) -> bool:
        try:
            response = await self.client.get(self.base_url + "/v1/models", headers=self._headers())
            return response.is_success
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _probabilities(value: Any) -> Probabilities:
    if not isinstance(value, dict):
        raise ProviderError("choice answer has no probability distribution")
    normalized = {str(key).upper(): float(probability) for key, probability in value.items()}
    try:
        return Probabilities(direct=normalized["DIRECT"], possible=normalized["POSSIBLE"],
                             unlikely=normalized["UNLIKELY"])
    except KeyError as exc:
        raise ProviderError(f"choice probabilities missing class: {normalized}") from exc
