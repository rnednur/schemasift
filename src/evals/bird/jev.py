from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .models import Question, Schema, Usage


ROLE_NAMES = ("OUTPUT", "FILTER", "JOIN", "GROUP_BY", "ORDER_BY", "AGGREGATE")


@dataclass(frozen=True)
class JevPrediction:
    tables: frozenset[str]
    columns: frozenset[str]
    roles: dict[str, frozenset[str]]
    table_probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    column_probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    role_probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    usage: Usage = Usage()


class JevClient:
    """Minimal client for TypeSafe's documented POST /v1/systemone API."""

    def __init__(self, api_key: str | None = None, model: str = "jev-latest",
                 base_url: str = "https://api.typesafe.ai", retain: tuple[str, ...] = ("D", "P"),
                 role_threshold: float = 0.5):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        if not self.api_key:
            raise ValueError("Set TYPESAFE_API_KEY before running the Jev evaluation")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.retain = frozenset(retain)
        self.role_threshold = role_threshold

    def predict(self, question: Question, schema: Schema) -> JevPrediction:
        state = {
            "task": "Select schema objects needed to answer the database question. Optimize for recall.",
            "question": question.question,
            "evidence": question.evidence,
            "schema": [{"table": table.name, "description": table.description,
                        "columns": [{"name": c.name, "type": c.data_type,
                                     "description": c.description, "samples": list(c.samples),
                                     "primary_key": c.primary_key, "foreign_key": c.foreign_key}
                                    for c in table.columns]}
                       for table in schema.tables],
            "relationships": [r.__dict__ for r in schema.relationships],
        }
        questions: dict[str, dict] = {}
        table_keys: dict[str, str] = {}
        column_keys: dict[str, str] = {}
        role_keys: dict[str, tuple[str, str]] = {}
        criteria = {
            "D": "Directly required to answer the question.",
            "P": "Potentially useful or structurally needed; choose this when uncertain.",
            "N": "Unlikely to be useful.",
        }
        for ti, table in enumerate(schema.tables):
            key = f"table_{ti}"
            table_keys[key] = table.name
            questions[key] = {"type": "choice", "instructions": f"Classify table {table.name!r}.", "criteria": criteria}
            for ci, column in enumerate(table.columns):
                qualified = column.qualified_name
                ckey = f"column_{ti}_{ci}"
                column_keys[ckey] = qualified
                questions[ckey] = {"type": "choice", "instructions": f"Classify column {qualified!r}.", "criteria": criteria}
                for ri, role in enumerate(ROLE_NAMES):
                    rkey = f"role_{ti}_{ci}_{ri}"
                    role_keys[rkey] = (role, qualified)
                    questions[rkey] = {"type": "noul", "instructions": f"Will {qualified!r} serve the SQL role {role}?"}
        started = time.perf_counter()
        response = self._post({"model": self.model, "state": state, "questions": questions})
        elapsed = time.perf_counter() - started
        answers = response["answers"]
        table_probs = {name: answers[key]["probabilities"] for key, name in table_keys.items()}
        column_probs = {name: answers[key]["probabilities"] for key, name in column_keys.items()}
        selected_tables = {name for key, name in table_keys.items() if answers[key]["choice"] in self.retain}
        selected_columns = {name for key, name in column_keys.items() if answers[key]["choice"] in self.retain}
        role_probs: dict[str, dict[str, float]] = {role: {} for role in ROLE_NAMES}
        roles: dict[str, set[str]] = {role: set() for role in ROLE_NAMES}
        for key, (role, column) in role_keys.items():
            probability = float(answers[key]["noul"])
            role_probs[role][column] = probability
            if probability >= self.role_threshold:
                roles[role].add(column)
        usage = response.get("usage", {})
        return JevPrediction(
            frozenset(selected_tables), frozenset(selected_columns),
            {role: frozenset(values) for role, values in roles.items()},
            table_probs, column_probs, role_probs,
            Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                  latency_seconds=elapsed),
        )

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/v1/systemone", data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Jev API returned HTTP {exc.code}: {detail}") from exc

