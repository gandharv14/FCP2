"""Validate isolation and solvability of a generated MCP bundle.

Checks that every required file exists, that no evaluation-only key leaked
into the runtime data, that every task resolves to exactly one evidence row
under its full dimensional filter, and that every metric has at least one
conflicting distractor for broad queries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS = {"gold", "gold_evidence", "is_truth", "correct_answer",
                  "target_value", "supported"}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(key).casefold() for key in value)
        for child in value.values():
            result.update(keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(keys(child))
    return result


def validate(root: Path) -> dict[str, Any]:
    required = [
        root / "runtime/server.json",
        root / "runtime/sources.json",
        root / "runtime/datasets.json",
        root / "runtime/documents.jsonl",
        root / "runtime/records.jsonl",
        root / "eval/tasks.jsonl",
        root / "server.py",
        root / "Dockerfile",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Missing required files: %s" % missing)

    runtime_objects = [load(root / "runtime/server.json"),
                       load(root / "runtime/sources.json"),
                       load(root / "runtime/datasets.json")]
    documents = read_jsonl(root / "runtime/documents.jsonl")
    records = read_jsonl(root / "runtime/records.jsonl")
    tasks = read_jsonl(root / "eval/tasks.jsonl")
    leaked = set().union(
        *(keys(value) for value in runtime_objects + documents + records)
    ) & FORBIDDEN_KEYS
    if leaked:
        raise ValueError("Evaluation keys leaked into runtime: %s" % sorted(leaked))

    document_ids = {row["id"] for row in documents}
    record_ids = {row["id"] for row in records}
    ambiguous_broad = 0
    for task in tasks:
        evidence = task["evidence"]
        if evidence["document_id"] not in document_ids or \
                evidence["record_id"] not in record_ids:
            raise ValueError("Missing evidence for task %s" % task["task_id"])
        wanted = task["required_dimensions"]
        exact = [
            row for row in records
            if row["dataset_id"] == evidence["dataset_id"]
            and all(str(row[field]).casefold() == str(value).casefold()
                    for field, value in wanted.items())
        ]
        if len(exact) != 1 or exact[0]["id"] != evidence["record_id"]:
            raise ValueError(
                "Task %s does not resolve to one evidence row" % task["task_id"])
        metric = exact[0]["metric"]
        broad = [row for row in records
                 if row["dataset_id"] == evidence["dataset_id"]
                 and row["metric"] == metric]
        if len({json.dumps(row["value"], sort_keys=True) for row in broad}) < 2:
            raise ValueError(
                "Task %s has no conflicting metric distractor" % task["task_id"])
        ambiguous_broad += 1

    return {"valid": True, "tasks": len(tasks), "documents": len(documents),
            "records": len(records),
            "broad_queries_with_conflicts": ambiguous_broad}
