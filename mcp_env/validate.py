"""Validate isolation and solvability of a generated MCP bundle.

Checks that every required file exists, that no evaluation-only key leaked
into the runtime data, that every task resolves to exactly one evidence row
under its full dimensional filter, and that every metric has at least one
conflicting distractor for broad queries.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS = {"gold", "gold_evidence", "is_truth", "correct_answer",
                  "target_value", "supported"}

TOKEN_RE = re.compile(r"[a-z0-9.]+")


def server_matches(actual: Any, expected: str) -> bool:
    """Replica of the served Store.matches token-containment semantics."""
    left = " ".join(TOKEN_RE.findall(str(actual).casefold()))
    right = " ".join(TOKEN_RE.findall(str(expected).casefold()))
    return left == right or right in left


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
    chained = 0
    for task in tasks:
        evidence = task["evidence"]
        if evidence["document_id"] not in document_ids or \
                evidence["record_id"] not in record_ids:
            raise ValueError("Missing evidence for task %s" % task["task_id"])
        wanted = task["required_dimensions"]
        # Filter with the live server's token-containment semantics, so an
        # "alternate unit" that merely contains the true unit cannot slip a
        # distractor into a fully-filtered query.
        exact = [
            row for row in records
            if row["dataset_id"] == evidence["dataset_id"]
            and all(server_matches(row[field], str(value))
                    for field, value in wanted.items())
        ]
        # Resolution rule: among the rows matching every dimension, exactly one
        # release is unsuperseded and it must be the evidence record.
        current = [row for row in exact if not row.get("superseded_by")]
        if len(current) != 1 or current[0]["id"] != evidence["record_id"]:
            raise ValueError(
                "Task %s does not resolve to one unsuperseded evidence row"
                % task["task_id"])
        # Every stale release must chain (acyclically) to the evidence release.
        by_release = {row["release"]: row for row in exact}
        for row in exact:
            seen = set()
            walk = row
            while walk.get("superseded_by"):
                if walk["release"] in seen:
                    raise ValueError(
                        "Task %s has a supersession cycle" % task["task_id"])
                seen.add(walk["release"])
                successor = by_release.get(walk["superseded_by"])
                if successor is None:
                    raise ValueError(
                        "Task %s: release %s supersedes an unknown release"
                        % (task["task_id"], walk["release"]))
                walk = successor
            if walk["id"] != evidence["record_id"]:
                raise ValueError(
                    "Task %s: chain from %s does not end at the evidence row"
                    % (task["task_id"], row["release"]))
        if len(exact) > 1:
            chained += 1
        # A stale release must not silently agree with the current value.
        stale_values = {json.dumps(row["value"], sort_keys=True)
                        for row in exact if row.get("superseded_by")}
        if json.dumps(current[0]["value"], sort_keys=True) in stale_values:
            raise ValueError(
                "Task %s has a stale release with the current value"
                % task["task_id"])
        metric = current[0]["metric"]
        broad = [row for row in records
                 if row["dataset_id"] == evidence["dataset_id"]
                 and row["metric"] == metric]
        if len({json.dumps(row["value"], sort_keys=True) for row in broad}) < 2:
            raise ValueError(
                "Task %s has no conflicting metric distractor" % task["task_id"])
        ambiguous_broad += 1

    return {"valid": True, "tasks": len(tasks), "documents": len(documents),
            "records": len(records),
            "broad_queries_with_conflicts": ambiguous_broad,
            "tasks_with_provenance_chains": chained}
