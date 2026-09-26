# BIRD Context Compiler & SchemaSift Evaluation

Evaluation harness for comparing full database context with a compiled,
question-specific schema. The architecture follows [`docs/bird_jev.md`](docs/bird_jev.md).

This module is SchemaSift's BIRD evaluation adapter. It includes BIRD
JSON/SQLite loading, clause-aware gold-SQL label extraction, execution comparison,
metrics, JSONL persistence, and a CLI. Table and column selection are performed by
the standalone SchemaSift library in-process or via its optional API.

## Install evaluation dependencies

From the repository root:

```bash
python3 -m pip install -e '.[eval,dev]'
```

## Download the benchmark data

The repository does not bundle BIRD's data. For an initial experiment, use the
official **BIRD Mini-Dev SQLite** package rather than the full 33 GB benchmark.

The question/SQL records can be downloaded directly through Hugging Face:

```bash
python3 -m evals.bird.cli download-questions
```

This is equivalent to calling `load_dataset("birdsql/bird_mini_dev")` and exports
the `mini_dev_sqlite` split in the JSON format consumed by this runner.

The Hugging Face dataset contains the records, but not the SQLite database files.
Download those separately from the complete package linked by the BIRD maintainers:

1. Download the complete Mini-Dev database package from the official BIRD Mini-Dev page:
   <https://github.com/bird-bench/mini_dev#for-new-users>
2. Extract it into a local directory (e.g. `/Users/.../MINIDEV`).
3. Confirm these paths exist:

```text
<mini_dev_root>/mini_dev_sqlite.json
<mini_dev_root>/dev_databases/<database_id>/<database_id>.sqlite
```

## Quick start

```bash
python3 -m evals.bird.cli inspect \
  --questions <mini_dev_root>/mini_dev_sqlite.json \
  --databases <mini_dev_root>/dev_databases \
  --limit 3

python3 -m evals.bird.cli run \
  --questions <mini_dev_root>/mini_dev_sqlite.json \
  --databases <mini_dev_root>/dev_databases \
  --config evals/bird/configs/phase1.json \
  --output runs/phase1.jsonl
```

The default `heuristic` backend makes the pipeline runnable without credentials.
It is a plumbing/smoke-test backend, not an experimental substitute for an LLM.

Run tests with:

```bash
pytest evals/bird/tests
```

## SchemaSift schema-linking experiment

Build the objective labels without making any model calls:

```bash
python3 -m evals.bird.cli gold-labels \
  --questions /Users/rnednur/code/minidev/MINIDEV/mini_dev_sqlite.json \
  --databases /Users/rnednur/code/minidev/MINIDEV/dev_databases \
  --output runs/mini-dev-gold-labels.jsonl
```

### In-Process Library Evaluation (Recommended)

To run Jev directly using SchemaSift as a library (no HTTP hop or server needed):

```bash
export TYPESAFE_API_KEY='...'
python3 -m evals.bird.cli evaluate-schemasift \
  --questions /Users/rnednur/code/minidev/MINIDEV/mini_dev_sqlite.json \
  --databases /Users/rnednur/code/minidev/MINIDEV/dev_databases \
  --output runs/mini-dev-jev-library.jsonl \
  --schemasift-config schemasift.yaml \
  --provider-name hosted_jev \
  --no-roles \
  --limit 10
```

To run offline with deterministic lexical baseline:

```bash
python3 -m evals.bird.cli evaluate-schemasift \
  --questions /Users/rnednur/code/minidev/MINIDEV/mini_dev_sqlite.json \
  --databases /Users/rnednur/code/minidev/MINIDEV/dev_databases \
  --output runs/mini-dev-lexical.jsonl \
  --adapter lexical --provider-name lexical --no-auth --no-roles
```

### Optional HTTP API Evaluation

To benchmark through the HTTP API instead of in-process library mode:

Start the server:
```bash
schemasift --config schemasift.yaml serve
```

And in another terminal:
```bash
python3 -m evals.bird.cli evaluate-schemasift \
  --questions /Users/rnednur/code/minidev/MINIDEV/mini_dev_sqlite.json \
  --databases /Users/rnednur/code/minidev/MINIDEV/dev_databases \
  --output runs/mini-dev-api.jsonl \
  --schemasift-api-url http://127.0.0.1:8080 \
  --provider-name hosted_jev \
  --limit 10
```

Each result stores table/column recall and precision, role metrics, calibrated
probabilities, and explicit lists of every missed gold table, column, and role.
