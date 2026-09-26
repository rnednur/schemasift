from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def summarize(path: str | Path) -> str:
    groups: dict[str, list[dict]] = defaultdict(list)
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                groups[row["strategy"]].append(row)
    headers = ["Strategy", "Questions", "Table Recall", "Column Recall", "Column Precision",
               "Schema Reduction", "Confidence*", "Uncertain", "SQL Accuracy", "Cost", "Latency"]
    rows = []
    for strategy, items in sorted(groups.items()):
        average = lambda key: sum(float(item.get(key) or 0) for item in items) / len(items)
        def average_present(key):
            values = [float(item[key]) for item in items if item.get(key) is not None]
            return sum(values) / len(values) if values else None
        reduction_key = ("metadata_token_reduction_pct"
                         if "metadata_token_reduction_pct" in items[0] else "schema_reduction_pct")
        accuracy = (f"{average('execution_correct'):.1%}"
                    if "execution_correct" in items[0] else "N/A")
        latency_key = "total_latency" if "total_latency" in items[0] else "jev_latency"
        confidence = average_present("selection_confidence")
        uncertain = average_present("uncertain_exclusion_count")
        rows.append([strategy, str(len(items)), f"{average('table_recall'):.1%}",
                     f"{average('column_recall'):.1%}", f"{average('column_precision'):.1%}",
                     f"{average(reduction_key):.1f}%",
                     f"{confidence:.1%}" if confidence is not None else "N/A",
                     f"{uncertain:.1f}" if uncertain is not None else "N/A", accuracy,
                     f"{average('total_cost'):.6f}", f"{average(latency_key):.3f}s"])
    output = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] + ["---:"] * 10) + "|"]
    output.extend("| " + " | ".join(row) + " |" for row in rows)
    output.append("\n\\* Selection confidence is an uncalibrated routing signal, not expected recall.")
    return "\n".join(output)
