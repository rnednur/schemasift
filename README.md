# SchemaSift

Fast, provider-neutral schema context selection for AI agents.

SchemaSift is primarily a Python library. It turns a natural-language question and a large database schema into a
compact, high-recall working schema. It supports hosted Jev, local
System-One-compatible servers, deterministic offline baselines, and custom providers.

## Use as a Python library (recommended)

```python
from schemasift import SchemaSelectionRequest, load_selector

async with load_selector("schemasift.yaml") as selector:
    result = await selector.select(SchemaSelectionRequest(
        question="Which customers pay in EUR?",
        schema={"database": "payments", "tables": [...]},
        options={"provider": "hosted_jev", "include_roles": False},
    ))
```

The library path avoids an HTTP hop and is the recommended integration for Python
applications. Selection, batching, retries, confidence, tracing, and provider adapters
all live in the library.

## What it guarantees

- Strict JSON request and response contracts.
- Two-stage table then column selection.
- Bounded, concurrent column batches (50 candidates and concurrency 4 by default).
- Structural preservation of primary keys, foreign keys, and selected relationships.
- Shortest-path relationship bridge tables.
- Optional multi-label SQL role prediction after column selection.
- Hard candidate, request, size, timeout, and output-column budgets, plus a
  provider-reported cost guard between stages.
- Per-batch traces and replay-friendly decision records.
- Provider URLs and credentials are deployment configuration, not untrusted request input.

Schema selection is probabilistic and does not guarantee sufficiency. Consumers should
retain a recovery path that can request hidden metadata.

## Install

```bash
python3 -m pip install -e '.[server,dev]'
```

## Configure

```bash
cp schemasift.example.yaml schemasift.yaml
```

The example includes three providers:

- `lexical`: offline deterministic smoke tests.
- `hosted_jev`: TypeSafe's hosted System One endpoint.
- `local_system_one`: any compatible local `/v1/systemone` server.

Credentials are read from environment variables:

```bash
export TYPESAFE_API_KEY='...'
```

Never put API keys in YAML committed to source control.

## Optional API service

```bash
schemasift --config schemasift.yaml serve
```

Endpoints:

```text
GET  /health
GET  /v1/providers
POST /v1/schema/select
GET  /docs
```

Opening `/` redirects to `/docs`. The service is a thin transport layer over the same
library and is useful for non-Python clients, centralized credentials, and shared
enterprise deployments.

## Example request

```json
{
  "question": "What is the ratio of customers paying in EUR versus CZK?",
  "schema": {
    "database": "payments",
    "rules": [],
    "tables": [
      {
        "name": "customers",
        "description": "One row per customer",
        "grain": "customer",
        "rules": [],
        "sample_rows": [],
        "columns": [
          {
            "name": "customer_id",
            "type": "INTEGER",
            "description": "Unique customer identifier",
            "primary_key": true
          },
          {
            "name": "currency",
            "type": "TEXT",
            "description": "Billing currency",
            "samples": ["EUR", "CZK"]
          }
        ]
      }
    ],
    "relationships": []
  },
  "options": {
    "provider": "hosted_jev",
    "preserve_structural_columns": true,
    "preserve_relationship_bridges": true,
    "include_roles": false,
    "batching": {
      "max_candidates_per_request": 50,
      "max_questions_per_request": 120,
      "max_input_bytes": 60000,
      "concurrency": 4
    }
  },
  "trace": {
    "run_id": "experiment-001",
    "question_id": "1471"
  }
}
```

All optional fields have defaults; table and column names are never rewritten in the
response.

## Command-line selection

```bash
schemasift --config schemasift.yaml select request.json
```

Use `-` to read JSON from standard input.

## Batching behavior

A 200-column candidate set with the defaults becomes four concurrent 50-column
provider requests. Splitting also respects question-count and serialized-byte limits.
Role classification, when enabled, runs only over retained columns and uses separate
batches because every column may have multiple roles.

If a provider batch fails, the default is to fail the request. Setting
`on_partial_failure` to `retain_unevaluated` produces a `partial` response and retains
every candidate from the failed batch; failed candidates are never silently treated as
irrelevant.

## Traces

JSONL tracing records:

- request, trace, run, and question identifiers;
- schema fingerprint and provider identity;
- candidate IDs and model probabilities;
- batch latency, model usage, and failures;
- final selections and structural provenance.

Raw authorization headers are never recorded. Descriptions, samples, and complete
provider payloads are not emitted by the built-in trace sink.

OpenTelemetry is an optional package boundary for deployments that want distributed
operational spans; JSONL remains the evaluation/replay source of truth.

## Provider contract

Providers implement two async operations:

```python
class DecisionProvider(Protocol):
    async def classify(...): ...
    async def classify_roles(...): ...
```

SchemaSift normalizes provider responses to `DIRECT`, `POSSIBLE`, and `UNLIKELY`
probabilities. A provider with meaningfully different request or response semantics
should receive a code adapter rather than brittle JSON-path configuration.

## Development & Testing

```bash
# Run all unit tests (including BIRD harness pipeline tests)
pytest
```

Offline tests never require an API key. Live-provider tests should be opt-in and use
separate credentials in CI.

## BIRD evaluation

SchemaSift includes a built-in BIRD evaluation harness under `src/evals/bird/` for measuring
schema selection recall, precision, and reduction against the BIRD benchmark.

### 1. Download BIRD Mini-Dev Benchmark

Download `MINIDEV.zip` from the official BIRD package link:
- **Download link**: [BIRD Mini-Dev SQLite (Google Drive)](https://drive.google.com/file/d/13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG/view)
- Unzip locally (e.g. into `~/code/minidev`):
  ```bash
  mkdir -p ~/code/minidev && unzip MINIDEV.zip -d ~/code/minidev
  ```
  This creates `mini_dev_sqlite.json` and the `dev_databases/` SQLite files.

### 2. In-Process Library Evaluation (Recommended)

Run evaluation against BIRD without starting a server:

```bash
MINIDEV_DIR="$HOME/code/minidev/MINIDEV"

# Deterministic baseline:
python3 -m evals.bird.cli evaluate-schemasift \
  --questions "$MINIDEV_DIR/mini_dev_sqlite.json" \
  --databases "$MINIDEV_DIR/dev_databases" \
  --output runs/mini-dev-lexical.jsonl \
  --adapter lexical --provider-name lexical --no-auth --no-roles

# Hosted Jev:
export TYPESAFE_API_KEY='...'
python3 -m evals.bird.cli evaluate-schemasift \
  --questions "$MINIDEV_DIR/mini_dev_sqlite.json" \
  --databases "$MINIDEV_DIR/dev_databases" \
  --output runs/mini-dev-jev.jsonl \
  --schemasift-config schemasift.yaml \
  --provider-name hosted_jev \
  --limit 10
```

### 3. Optional HTTP API Evaluation

To benchmark via the HTTP API boundary:

```bash
python3 -m evals.bird.cli evaluate-schemasift \
  --questions "$MINIDEV_DIR/mini_dev_sqlite.json" \
  --databases "$MINIDEV_DIR/dev_databases" \
  --output runs/mini-dev-api.jsonl \
  --schemasift-api-url http://127.0.0.1:8080 \
  --provider-name hosted_jev \
  --limit 10
```

See [`src/evals/bird/README.md`](src/evals/bird/README.md) for full benchmark documentation and details.
