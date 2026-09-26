from __future__ import annotations

import json
from typing import Iterable, Sequence

from .exceptions import BudgetExceeded
from .providers.base import Candidate


def partition_candidates(candidates: Sequence[Candidate], *, max_candidates: int,
                         max_questions: int, max_input_bytes: int,
                         questions_per_candidate: int = 1) -> list[list[Candidate]]:
    batches: list[list[Candidate]] = []
    current: list[Candidate] = []
    current_bytes = 2
    effective_count = min(max_candidates, max(1, max_questions // questions_per_candidate))
    for candidate in candidates:
        size = len(json.dumps({"id": candidate.id, "name": candidate.name,
                               "metadata": candidate.metadata}, default=str,
                              ensure_ascii=False).encode())
        if size > max_input_bytes:
            raise BudgetExceeded(f"candidate {candidate.id} exceeds the per-request byte budget")
        if current and (len(current) >= effective_count or current_bytes + size > max_input_bytes):
            batches.append(current)
            current = []
            current_bytes = 2
        current.append(candidate)
        current_bytes += size
    if current:
        batches.append(current)
    return batches

