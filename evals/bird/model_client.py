from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .models import Usage


@dataclass(frozen=True)
class ModelResponse:
    data: dict[str, Any]
    usage: Usage


class ModelClient(Protocol):
    def complete_json(self, *, system: str, user: str) -> ModelResponse: ...


class ReplayModelClient:
    """Deterministic adapter for recorded model responses."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = iter(responses)

    def complete_json(self, *, system: str, user: str) -> ModelResponse:
        started = time.perf_counter()
        data = next(self.responses)
        return ModelResponse(data, Usage(
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(json.dumps(data)),
            latency_seconds=time.perf_counter() - started,
        ))


def estimate_tokens(text: str) -> int:
    # Stable provider-neutral estimate; API adapters should replace it with actual usage.
    return max(1, len(re.findall(r"\w+|[^\w\s]", text)))

