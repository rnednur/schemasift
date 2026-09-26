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

The repository does not bundle BIRD's benchmark data (databases and questions).
For schema selection evaluation, use the official **BIRD Mini-Dev SQLite** package:

### Direct Download (Complete Package with SQLite Databases)

1. Download `MINIDEV.zip` from the official BIRD Google Drive link:
   👉 **[BIRD Mini-Dev Google Drive (MINIDEV.zip)](https://drive.google.com/file/d/13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG/view)**
   *(Also referenced on the official BIRD GitHub repository: [bird-bench/mini_dev](https://github.com/bird-bench/mini_dev#for-new-users))*

2. Unzip into a local directory of your choice (for example `~/code/minidev`):
   ```bash
   mkdir -p ~/code/minidev
   unzip MINIDEV.zip -d ~/code/minidev
   ```

3. Confirm these files and directories are present:
   ```text
   ~/code/minidev/MINIDEV/mini_dev_sqlite.json
   ~/code/minidev/MINIDEV/dev_databases/<database_id>/<database_id>.sqlite
   ```

### Alternative: Download Questions Only via Hugging Face

If you only need the question and gold-SQL JSON records:

```bash
python3 -m pip install -e '.[eval]'
python3 -m evals.bird.cli download-questions --output data/mini_dev_sqlite.json
```

*(Note: Hugging Face provides question records but not the SQLite `.sqlite` databases. The SQLite databases are required to inspect schema tables/columns and execute queries, so download `MINIDEV.zip` above for full evaluation).*

## Quick start

```bash
MINIDEV_DIR="$HOME/code/minidev/MINIDEV"

python3 -m evals.bird.cli inspect \
  --questions "$MINIDEV_DIR/mini_dev_sqlite.json" \
  --databases "$MINIDEV_DIR/dev_databases" \
  --limit 3

python3 -m evals.bird.cli run \
  --questions "$MINIDEV_DIR/mini_dev_sqlite.json" \
  --databases "$MINIDEV_DIR/dev_databases" \
  --config src/evals/bird/configs/phase1.json \
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
