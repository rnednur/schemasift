from __future__ import annotations

import json
import csv
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Column, Question, Relationship, Schema, Table


def load_questions(path: str | Path, limit: int | None = None) -> list[Question]:
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            f"Question file not found: {path}\n"
            "Download BIRD Mini-Dev and pass the actual mini_dev_sqlite.json path."
        )
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data.get("questions", [])
    questions = []
    for index, row in enumerate(rows):
        questions.append(Question(
            question_id=str(row.get("question_id", row.get("id", index))),
            database_id=str(row.get("db_id", row.get("database_id", ""))),
            question=str(row.get("question", "")),
            gold_sql=str(row.get("SQL", row.get("sql", row.get("gold_sql", "")))),
            evidence=str(row.get("evidence", "")),
        ))
        if limit is not None and len(questions) >= limit:
            break
    return questions


def find_database(root: str | Path, database_id: str) -> Path:
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(
            f"Database directory not found: {root}\n"
            "Pass the extracted BIRD dev_databases directory."
        )
    candidates = [
        root / database_id / f"{database_id}.sqlite",
        root / f"{database_id}.sqlite",
        root / database_id / f"{database_id}.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list(root.glob(f"**/{database_id}.sqlite"))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"SQLite database not found for {database_id!r} under {root}")


def inspect_sqlite(path: str | Path, database_id: str | None = None, sample_limit: int = 3) -> Schema:
    path = Path(path)
    descriptions = _load_column_descriptions(path.parent / "database_description")
    # BIRD databases are immutable benchmark artifacts. immutable=1 also avoids
    # SQLite trying to create/read WAL sidecars beside externally stored files.
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        names = [row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        relationships: list[Relationship] = []
        tables: list[Table] = []
        for name in names:
            quoted = name.replace('"', '""')
            info = list(connection.execute(f'PRAGMA table_info("{quoted}")'))
            foreign_rows = list(connection.execute(f'PRAGMA foreign_key_list("{quoted}")'))
            foreign_names = {str(row["from"]).lower() for row in foreign_rows}
            samples: dict[str, list[object]] = {str(row["name"]): [] for row in info}
            if sample_limit:
                for row in connection.execute(f'SELECT * FROM "{quoted}" LIMIT ?', (sample_limit,)):
                    for column_name in samples:
                        value = row[column_name]
                        if value is not None and value not in samples[column_name]:
                            samples[column_name].append(value)
            columns = [Column(
                table=name,
                name=str(row["name"]),
                data_type=str(row["type"] or ""),
                description=descriptions.get((name.lower(), str(row["name"]).lower()), ""),
                samples=tuple(samples[str(row["name"])]),
                primary_key=bool(row["pk"]),
                foreign_key=str(row["name"]).lower() in foreign_names,
            ) for row in info]
            tables.append(Table(name=name, columns=columns))
            for row in foreign_rows:
                target_table = str(row["table"])
                target_column = row["to"]
                if target_column is None:
                    target_quoted = target_table.replace('"', '""')
                    target_info = list(connection.execute(f'PRAGMA table_info("{target_quoted}")'))
                    primary_keys = sorted((item for item in target_info if item["pk"]),
                                          key=lambda item: item["pk"])
                    if not primary_keys:
                        continue
                    target_column = primary_keys[0]["name"]
                relationships.append(Relationship(
                    from_table=name,
                    from_column=str(row["from"]),
                    to_table=target_table,
                    to_column=str(target_column),
                ))
        return Schema(database_id or path.stem, tables, relationships)
    finally:
        connection.close()


def _load_column_descriptions(directory: Path) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    if not directory.is_dir():
        return result
    for path in directory.glob("*.csv"):
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    original = (row.get("original_column_name") or "").strip()
                    if not original:
                        continue
                    semantic_name = (row.get("column_name") or "").strip()
                    description = (row.get("column_description") or "").strip()
                    value_description = (row.get("value_description") or "").strip()
                    text = "; ".join(filter(None, [semantic_name, description, value_description]))
                    result[(path.stem.lower(), original.lower())] = text
        except (OSError, csv.Error, UnicodeError):
            # Metadata quality is measured separately; a malformed optional CSV
            # must not make the underlying SQLite benchmark unreadable.
            continue
    return result
