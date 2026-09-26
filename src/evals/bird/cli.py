from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .dataset import find_database, inspect_sqlite, load_questions
from .report import summarize
from .runner import ExperimentRunner, append_record


def _load_config(path: str | Path) -> dict:
    text = Path(path).read_text()
    if str(path).lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit("YAML config requires: pip install '.[yaml]'") from exc
        return yaml.safe_load(text)
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bird-context")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--questions", required=True)
    inspect_parser.add_argument("--databases", required=True)
    inspect_parser.add_argument("--limit", type=int, default=3)
    download_parser = subparsers.add_parser("download-questions")
    download_parser.add_argument("--output", default="data/mini_dev_data/mini_dev_sqlite.json")
    label_parser = subparsers.add_parser("gold-labels")
    label_parser.add_argument("--questions", required=True)
    label_parser.add_argument("--databases", required=True)
    label_parser.add_argument("--output", required=True)
    label_parser.add_argument("--limit", type=int)
    for command in ("evaluate-schemasift", "evaluate-jev"):
        jev_parser = subparsers.add_parser(command)
        jev_parser.add_argument("--questions", required=True)
        jev_parser.add_argument("--databases", required=True)
        jev_parser.add_argument("--output", required=True)
        jev_parser.add_argument("--limit", type=int, default=10)
        jev_parser.add_argument("--model", default="jev-latest")
        jev_parser.add_argument("--provider-url", default="https://api.typesafe.ai")
        jev_parser.add_argument("--endpoint", default="/v1/systemone")
        jev_parser.add_argument("--provider-name", default="jev")
        jev_parser.add_argument("--adapter", choices=["system_one", "lexical"],
                                default="system_one")
        jev_parser.add_argument("--schemasift-api-url",
                                help="Call a running SchemaSift API instead of using the library in-process")
        jev_parser.add_argument("--schemasift-config",
                                help="Load SchemaSift configuration and run the library in-process")
        jev_parser.add_argument("--api-key-env", default="TYPESAFE_API_KEY")
        jev_parser.add_argument("--no-auth", action="store_true")
        jev_parser.add_argument("--no-roles", action="store_true")
        jev_parser.add_argument("--resume", action="store_true",
                                help="Skip question IDs already present in the output file")
        jev_parser.add_argument("--fail-fast", action="store_true",
                                help="Stop on the first failed question instead of recording and continuing")
        jev_parser.add_argument("--input-cost-per-million", type=float,
                                help="Estimate cost when the provider returns tokens but no cost")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--questions", required=True)
    run_parser.add_argument("--databases", required=True)
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--output", required=True)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("results")
    args = parser.parse_args(argv)
    if args.command == "download-questions":
        try:
            from datasets import load_dataset  # type: ignore
        except ImportError as exc:
            parser.error("This command requires the Hugging Face package: python3 -m pip install '.[hf]'")
        dataset = load_dataset("birdsql/bird_mini_dev", split="mini_dev_sqlite")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps([dict(row) for row in dataset], ensure_ascii=False, indent=2))
        print(f"Saved {len(dataset)} questions to {output}")
        print("Note: SQLite database files are separate and still need to be downloaded.")
        return 0
    if args.command == "report":
        print(summarize(args.results))
        return 0
    if args.command == "gold-labels":
        from .dataset import inspect_sqlite
        from .gold_sql import extract_gold_schema
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        questions = load_questions(args.questions, args.limit)
        with output.open("w", encoding="utf-8") as handle:
            for question in questions:
                schema = inspect_sqlite(find_database(args.databases, question.database_id), question.database_id, 0)
                gold = extract_gold_schema(question.gold_sql, schema)
                row = {"question_id": question.question_id, "database_id": question.database_id,
                       "question": question.question, "gold_sql": question.gold_sql,
                       "gold_tables": sorted(gold.tables), "gold_columns": sorted(gold.columns),
                       "gold_roles": {k: sorted(v) for k, v in (gold.roles or {}).items()}}
                handle.write(json.dumps(row) + "\n")
        print(f"Saved {len(questions)} labeled questions to {output}")
        return 0
    if args.command in {"evaluate-schemasift", "evaluate-jev"}:
        from schemasift import SchemaSelector
        from schemasift.config import load_selector
        from schemasift.client import SchemaSiftClient
        from schemasift.providers import LexicalProvider, SystemOneProvider
        from schemasift.tracing import JsonlTraceSink
        from .jev_evaluation import evaluate_schemasift
        questions = load_questions(args.questions, args.limit)
        completed_ids: set[str] = set()
        if args.resume and Path(args.output).exists():
            with Path(args.output).open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        completed_ids.add(str(json.loads(line).get("question_id")))
            questions = [question for question in questions
                         if question.question_id not in completed_ids]
            print(f"Resuming: skipped {len(completed_ids)} completed questions; "
                  f"{len(questions)} remain")
        key = None if args.no_auth else os.environ.get(args.api_key_env)
        if args.schemasift_api_url and args.schemasift_config:
            parser.error("Use either --schemasift-api-url or --schemasift-config, not both")
        if args.adapter == "system_one" and not args.schemasift_api_url \
                and not args.schemasift_config \
                and not args.no_auth and not key:
            parser.error(f"Set {args.api_key_env} or pass --no-auth for a local provider")
        if args.schemasift_api_url:
            provider = None
            selector = SchemaSiftClient(args.schemasift_api_url)
            provider_name = args.provider_name
        elif args.schemasift_config:
            provider = None
            selector = load_selector(args.schemasift_config)
            provider_name = (selector.default_provider if args.provider_name == "jev"
                             else args.provider_name)
        else:
            provider = (LexicalProvider(name=args.provider_name, model="lexical-v1")
                        if args.adapter == "lexical" else
                        SystemOneProvider(name=args.provider_name, base_url=args.provider_url,
                                          endpoint=args.endpoint, model=args.model, api_key=key,
                                          input_cost_per_million=args.input_cost_per_million))
            selector = SchemaSelector(
                {args.provider_name: provider}, default_provider=args.provider_name,
                trace_sink=JsonlTraceSink(Path(args.output).with_suffix(".traces.jsonl")),
            )
            provider_name = args.provider_name
        async def run_evaluation():
            try:
                for question in questions:
                    # Roles are optional in SchemaSift; BIRD enables them by default.
                    try:
                        record = await evaluate_schemasift(question, args.databases, selector,
                                                           provider_name,
                                                           include_roles=not args.no_roles)
                    except Exception as exc:
                        if args.fail_fast:
                            raise
                        error_path = Path(args.output).with_suffix(".errors.jsonl")
                        error_path.parent.mkdir(parents=True, exist_ok=True)
                        with error_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps({
                                "question_id": question.question_id,
                                "database_id": question.database_id,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }) + "\n")
                        print(f"{question.question_id}: ERROR {type(exc).__name__}: {exc}")
                        continue
                    append_record(args.output, record)
                    values = record.values
                    print(
                        f"{question.question_id}: "
                        f"table recall={values['table_recall']:.1%} precision={values['table_precision']:.1%}; "
                        f"column recall={values['column_recall']:.1%} precision={values['column_precision']:.1%}; "
                        f"reduction={values['schema_reduction_pct']:.1f}%; "
                        f"confidence={values['selection_confidence']:.1%}"
                        f"({values['selection_confidence_level']}, uncalibrated) "
                        f"uncertain={values['uncertain_exclusion_count']}; "
                        f"columns={values['selected_column_count']}/{values['available_column_count']}; "
                        f"latency={values['jev_latency'] * 1000:.0f}ms; "
                        f"tokens={values.get('jev_input_tokens') or 0}+"
                        f"{values.get('jev_output_tokens') or 0}; "
                        f"cost=${values.get('total_cost') or 0:.6f}; "
                        f"status={values['schemasift_status']}"
                    )
            finally:
                close = getattr(selector, "close", None)
                if close:
                    await close()
        asyncio.run(run_evaluation())
        return 0
    try:
        questions = load_questions(args.questions, args.limit if args.command == "inspect" else None)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.command == "inspect":
        for question in questions:
            try:
                db = find_database(args.databases, question.database_id)
            except FileNotFoundError as exc:
                parser.error(str(exc))
            schema = inspect_sqlite(db, question.database_id, sample_limit=0)
            print(f"{question.question_id}\t{question.database_id}\t{len(schema.tables)} tables\t{len(schema.all_columns())} columns")
        return 0
    config = _load_config(args.config)
    selector = config.get("selector", {})
    context = config.get("context", {})
    runner = ExperimentRunner(
        Path(args.databases), retain=frozenset(selector.get("retain", ["D", "P"])),
        preserve_structural=selector.get("preserve_structural_columns", True),
        include_descriptions=context.get("include_descriptions", True),
        include_samples=context.get("include_samples", True),
        include_relationships=context.get("include_relationships", True),
        sample_limit=context.get("sample_limit", 3),
    )
    limit = config.get("sample_size")
    for question in questions[:limit]:
        for strategy in config.get("strategies", ["baseline_full"]):
            record = runner.run_question(question, strategy, config.get("run_id", "run"))
            append_record(args.output, record)
            print(f"{question.question_id} {strategy}: column recall={record.values['column_recall']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
