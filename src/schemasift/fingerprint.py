from __future__ import annotations

import hashlib
import json

from .models import DatabaseSchema


def schema_fingerprint(schema: DatabaseSchema) -> str:
    canonical = json.dumps(schema.model_dump(mode="json", exclude_none=True),
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

