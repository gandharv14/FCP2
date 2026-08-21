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

# The exact-answer grader accepts max(1e-6, 1e-6 * |expected|); a wrong value
# inside a few of those bands solves the task. Builder guarantees a 10x floor.
GRADER_TOLERANCE = 1e-6

WORD_RE = re.compile(r"[a-z]{4,}")
EXCERPT_MARKER = "Short attributed excerpt"


def within_grader_band(candidate: Any, expected: Any) -> bool:
    if isinstance(candidate, bool) or isinstance(expected, bool):
        return candidate is expected
    if not isinstance(candidate, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return abs(float(candidate) - float(expected)) <= 10 * max(
        GRADER_TOLERANCE, GRADER_TOLERANCE * abs(float(expected)))


def content_words(document: dict[str, Any]) -> set[str]:
    """Alphabetic tokens of a document's own prose, excerpt text excluded.

    Excerpts rotate across a dataset's documents, so an excerpt attached to an
    answer document is not a searchable marker of correctness the way a
    structural phrase difference is."""
    content = str(document.get("content") or "")
    cut = content.find(EXCERPT_MARKER)
    if cut != -1:
        content = content[:cut]
    return set(WORD_RE.findall(content.casefold()))


def server_matches(actual: Any, expected: str) -> bool:
    """Replica of the served Store.matches token-aligned containment: the
    expected tokens must appear as a contiguous run of the actual tokens,
    and a value that tokenizes to nothing matches nothing."""
    left = TOKEN_RE.findall(str(actual).casefold())
    right = TOKEN_RE.findall(str(expected).casefold())
    if not right:
        return False
    if left == right:
        return True
    span = len(right)
    return any(left[i:i + span] == right for i in range(len(left) - span + 1))


def server_matches_field(row: dict[str, Any], field: str, expected: str) -> bool:
    """Replica of Store.query's per-field matching: the metric dimension also
    matches against each record's metric_aliases, exactly like the live
    server, so build-time resolution and served resolution cannot disagree."""
    if server_matches(row.get(field), expected):
        return True
    if field == "metric":
        return any(server_matches(alias, expected)
                   for alias in row.get("metric_aliases", []))
    return False


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


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def unique_ids(rows: list[dict[str, Any]], label: str) -> set[str]:
    ids = [str(row.get("id") or "") for row in rows]
    if any(not value for value in ids):
        raise ValueError("%s contains a row without an id" % label)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError("%s contains duplicate ids: %s" % (label, duplicates))
    return set(ids)


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

    server = load(root / "runtime/server.json")
    sources = load(root / "runtime/sources.json")
    datasets = load(root / "runtime/datasets.json")
    runtime_objects = [server, sources, datasets]
    documents = read_jsonl(root / "runtime/documents.jsonl")
    records = read_jsonl(root / "runtime/records.jsonl")
    tasks = read_jsonl(root / "eval/tasks.jsonl")
    leaked = set().union(
        *(keys(value) for value in runtime_objects + documents + records)
    ) & FORBIDDEN_KEYS
    if leaked:
        raise ValueError("Evaluation keys leaked into runtime: %s" % sorted(leaked))

    source_ids = unique_ids(sources, "sources")
    dataset_ids = unique_ids(datasets, "datasets")
    document_ids = unique_ids(documents, "documents")
    record_ids = unique_ids(records, "records")
    datasets_by_id = {row["id"]: row for row in datasets}
    documents_by_id = {row["id"]: row for row in documents}
    for source in sources:
        origin = str(source.get("origin_url") or "")
        if not re.match(r"^(?:https?|internal)://", origin):
            raise ValueError("Source %s has an invalid origin_url" % source["id"])
        source_metadata = {
            key: value for key, value in source.items() if key != "origin_url"
        }
        if any(re.search(r"https?://", value, re.IGNORECASE)
               for value in strings(source_metadata)):
            raise ValueError(
                "Source %s exposes a real URL outside origin_url" % source["id"])
    non_source_runtime = [server, datasets, documents, records]
    if any(re.search(r"https?://", value, re.IGNORECASE)
           for value in strings(non_source_runtime)):
        raise ValueError("Runtime exposes a real URL outside source origin_url")
    for dataset in datasets:
        if dataset.get("source_id") not in source_ids:
            raise ValueError("Dataset %s references an unknown source" % dataset["id"])
        if bool(dataset.get("profile_id")) != bool(dataset.get("dataset_key")):
            raise ValueError(
                "Profiled dataset %s needs profile_id and dataset_key" % dataset["id"])
        if dataset.get("profile_id"):
            labels = dataset.get("field_labels")
            if not isinstance(labels, dict):
                raise ValueError("Profiled dataset %s has invalid field labels"
                                 % dataset["id"])
    for document in documents:
        if document.get("source_id") not in source_ids:
            raise ValueError("Document %s references an unknown source" % document["id"])
        if document.get("related_dataset_id") not in dataset_ids:
            raise ValueError("Document %s references an unknown dataset"
                             % document["id"])
        expected_prefix = "mock://%s/documents/" % document["source_id"]
        if not str(document.get("url") or "").startswith(expected_prefix):
            raise ValueError("Document %s must use its mock:// source URL"
                             % document["id"])
    for record in records:
        if record.get("source_id") not in source_ids or \
                record.get("dataset_id") not in dataset_ids or \
                record.get("document_id") not in document_ids:
            raise ValueError("Record %s has a broken runtime reference" % record["id"])
        document = documents_by_id[record["document_id"]]
        if document["related_dataset_id"] != record["dataset_id"]:
            raise ValueError("Record %s document points to another dataset"
                             % record["id"])
    ambiguous_broad = 0
    chained = 0
    for task in tasks:
        evidence = task["evidence"]
        if evidence["document_id"] not in document_ids or \
                evidence["record_id"] not in record_ids or \
                evidence["dataset_id"] not in dataset_ids or \
                evidence["source_id"] not in source_ids:
            raise ValueError("Missing evidence for task %s" % task["task_id"])
        wanted = task["required_dimensions"]
        for field, value in wanted.items():
            if not TOKEN_RE.findall(str(value).casefold()):
                raise ValueError(
                    "Task %s dimension %r value %r contains no letters or "
                    "digits; the live server would reject it as a filter"
                    % (task["task_id"], field, value))
        # Filter with the live server's token-containment semantics (metric
        # aliases included), so an "alternate unit" that merely contains the
        # true unit cannot slip a distractor into a fully-filtered query and
        # an alias collision cannot make the served resolution ambiguous.
        exact = [
            row for row in records
            if row["dataset_id"] == evidence["dataset_id"]
            and all(server_matches_field(row, field, str(value))
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
        published = [row["published_at"] for row in exact]
        if len(set(published)) != len(published):
            raise ValueError(
                "Task %s has duplicate provenance publication dates"
                % task["task_id"])
        # Chains must be chronological on every path (the oracle enforces
        # this against the live server): a superseded release published
        # after its successor is a self-contradictory provenance story.
        for row in exact:
            successor = by_release.get(row.get("superseded_by") or "")
            if successor is not None and \
                    str(successor["published_at"]) <= str(row["published_at"]):
                raise ValueError(
                    "Task %s: release %s is not older than the release "
                    "that supersedes it (%s)"
                    % (task["task_id"], row["release"], successor["release"]))
        if datasets_by_id[evidence["dataset_id"]].get("profile_id"):
            if any(not row.get("release_label") for row in exact):
                raise ValueError(
                    "Task %s profiled releases are missing labels"
                    % task["task_id"])
            ordered = sorted(exact, key=lambda row: row["published_at"])
            if ordered[-1]["id"] != evidence["record_id"]:
                raise ValueError(
                    "Task %s profiled release cadence does not end at evidence"
                    % task["task_id"])
        if len(exact) > 1:
            chained += 1
        # A stale release must not silently agree with the current value --
        # neither exactly nor within the grader's acceptance band, where a
        # wrong pick would still be graded correct.
        stale_rows = [row for row in exact if row.get("superseded_by")]
        stale_values = {json.dumps(row["value"], sort_keys=True)
                        for row in stale_rows}
        if json.dumps(current[0]["value"], sort_keys=True) in stale_values:
            raise ValueError(
                "Task %s has a stale release with the current value"
                % task["task_id"])
        for row in stale_rows:
            if within_grader_band(row["value"], current[0]["value"]):
                raise ValueError(
                    "Task %s has a stale release within grader tolerance of "
                    "the current value (%r vs %r)"
                    % (task["task_id"], row["value"], current[0]["value"]))
        metric = current[0]["metric"]
        broad = [row for row in records
                 if row["dataset_id"] == evidence["dataset_id"]
                 and row["metric"] == metric]
        if len({json.dumps(row["value"], sort_keys=True) for row in broad}) < 2:
            raise ValueError(
                "Task %s has no conflicting metric distractor" % task["task_id"])
        ambiguous_broad += 1

    # No searchable phrase may mark the answer documents. Every alphabetic
    # token in a supported record's document prose must also occur either in
    # some non-answer document or in that record's own field values; a token
    # unique to the answer documents would let one search_documents call
    # reveal every answer.
    records_by_id = {row["id"]: row for row in records}
    supported_doc_ids = {task["evidence"]["document_id"] for task in tasks}
    supported_record_of = {task["evidence"]["document_id"]:
                           task["evidence"]["record_id"] for task in tasks}
    non_answer_words: set[str] = set()
    for document in documents:
        if document["id"] not in supported_doc_ids:
            non_answer_words |= content_words(document)
    for document in documents:
        if document["id"] not in supported_doc_ids:
            continue
        if "authoritative figure" in str(document.get("content") or ""):
            raise ValueError(
                "Document %s carries the 'authoritative figure' answer marker"
                % document["id"])
        record = records_by_id.get(supported_record_of[document["id"]], {})
        own = set(WORD_RE.findall(
            json.dumps(record, sort_keys=True).casefold()))
        unique = content_words(document) - non_answer_words - own
        if unique:
            raise ValueError(
                "Document %s contains tokens unique to answer documents: %s"
                % (document["id"], sorted(unique)[:8]))

    return {"valid": True, "tasks": len(tasks), "documents": len(documents),
            "records": len(records),
            "broad_queries_with_conflicts": ambiguous_broad,
            "tasks_with_provenance_chains": chained}
