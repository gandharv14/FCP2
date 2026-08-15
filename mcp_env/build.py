"""Build a dense, deterministic MCP research environment from a normalized spec.

For every atomic variable the builder emits one supported record plus a ring of
distractors that each differ on at least one meaningful dimension (scenario,
basis, period, unit, status). No distractor shares every required dimension
with the supported record, so a fully-disambiguated query resolves to exactly
one row while broad queries return conflicting candidates.

Output layout::

    <output>/runtime/   served through MCP (sources, documents, datasets, records)
    <output>/eval/      tasks.jsonl - prompts, typed answers, evidence ids; NEVER shipped
    <output>/environment.json

The spec contract is documented in ``references/scenario-schema.md`` of the
originating skill; this vendored copy additionally accepts per-variable
``workbook`` metadata (cell refs and the raw cell value) and ``alternatives``
(categorical distractor values).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

SCENARIOS = ["Management case", "Lender case", "Downside case", "Observed", "Preliminary"]
BASES = ["real prices", "nominal prices", "pre-tax", "post-tax", "constant-price basis"]
STATUSES = ["draft", "superseded", "indicative", "revised", "provisional"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:72] or "item"


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12]
    return "%s_%s" % (prefix, digest)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def validate_spec(spec: dict[str, Any]) -> None:
    if not spec.get("environment_id") or not isinstance(spec.get("seed"), int):
        raise ValueError("environment_id and integer seed are required")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError("variables must be a non-empty list")
    required = ["id", "name", "value", "unit", "entity", "period", "scenario",
                "basis", "status", "question", "sources"]
    ids: set[str] = set()
    dimensions: set[tuple[str, ...]] = set()
    for index, variable in enumerate(variables):
        missing = [key for key in required if key not in variable]
        if missing:
            raise ValueError("variables[%d] is missing: %s" % (index, ", ".join(missing)))
        if variable["id"] in ids:
            raise ValueError("Duplicate variable id: %s" % variable["id"])
        ids.add(variable["id"])
        if isinstance(variable["value"], str) and (
                ";" in variable["value"] or "\u2248" in variable["value"]):
            raise ValueError(
                "%s has a compound value; split it into atomic variables" % variable["id"])
        if not variable["sources"]:
            raise ValueError("%s requires at least one source" % variable["id"])
        for source in variable["sources"]:
            if not source.get("name") or not source.get("url") or not source.get("role"):
                raise ValueError(
                    "Every source for %s needs name, url, and role" % variable["id"])
        key = tuple(str(variable[field]).casefold()
                    for field in ["entity", "name", "period", "scenario",
                                  "basis", "unit", "status"])
        if key in dimensions:
            raise ValueError(
                "Two variables claim the same complete dimensions: %s" % variable["id"])
        dimensions.add(key)


def perturb(variable: dict[str, Any], index: int) -> Any:
    """A plausible-but-wrong value for a distractor record."""
    alternatives = variable.get("alternatives") or []
    if alternatives:
        return alternatives[index % len(alternatives)]
    value = variable["value"]
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + [1, -1, 2, -2, 5, -5][index % 6]
    if isinstance(value, float):
        return round(value * [0.9, 1.1, 0.96, 1.04, 0.75, 1.25][index % 6], 6)
    date = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if date:
        year = int(date.group(1)) + [1, -1, 2, -2][index % 4]
        return "%04d-%s-%s" % (year, date.group(2), date.group(3))
    return "%s (%s)" % (value, ["prior", "provisional", "alternative"][index % 3])


def alternate_period(period: str, index: int) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", period)
    if not match:
        return period
    year = int(match.group()) + [1, -1, 2, -2][index % 4]
    return period[:match.start()] + str(year) + period[match.end():]


def alternate_unit(unit: str, index: int) -> str:
    lower = unit.casefold()
    if lower in {"percent", "%"}:
        return "basis points" if index % 2 else "decimal fraction"
    if "eur" in lower or "\u20ac" in lower:
        return "GBP million"
    if "gbp" in lower or "\u00a3" in lower:
        return "EUR million"
    return "alternate %s" % unit


def source_id(source: dict[str, Any]) -> str:
    parsed = urlparse(source["url"])
    return slug(parsed.netloc or parsed.path or source["name"])


def display_value(value: Any, unit: str) -> str:
    if isinstance(value, float):
        text = "%.12g" % value
    else:
        text = str(value)
    if str(unit).casefold() == "percent":
        return "%s%%" % text
    return "%s %s" % (text, unit)


def make_record(dataset_id: str, source: str, document: str,
                variable: dict[str, Any], value: Any, row_key: str) -> dict[str, Any]:
    return {
        "id": stable_id("row", dataset_id, row_key),
        "dataset_id": dataset_id,
        "source_id": source,
        "document_id": document,
        "entity": variable["entity"],
        "metric": variable["name"],
        "metric_aliases": variable.get("aliases", []),
        "period": str(variable["period"]),
        "scenario": variable["scenario"],
        "basis": variable["basis"],
        "unit": variable["unit"],
        "status": variable["status"],
        "value": value,
    }


def make_document(doc_id: str, source: dict[str, Any], row: dict[str, Any],
                  kind: str, published: str) -> dict[str, Any]:
    return {
        "id": doc_id,
        "source_id": source_id(source),
        "title": "%s \u2014 %s \u2014 %s (%s)" % (
            row["entity"], row["metric"], row["period"], row["scenario"]),
        "kind": kind,
        "published_at": published,
        "url": "mock://%s/documents/%s" % (source_id(source), doc_id),
        "content": (
            "%s | %s release\n\n"
            "Entity: %s\nMetric: %s\nPeriod: %s\n"
            "Scenario: %s\nBasis: %s\nUnit: %s\n"
            "Status: %s\nReported value: %s\n\n"
            "Use this observation only when every dimension matches the "
            "research question. Related releases may contain nearby values "
            "that are not interchangeable."
            % (source["name"], row["status"], row["entity"], row["metric"],
               row["period"], row["scenario"], row["basis"], row["unit"],
               row["status"], display_value(row["value"], row["unit"]))
        ),
        "related_dataset_id": row["dataset_id"],
    }


def build(spec: dict[str, Any], output: Path) -> dict[str, Any]:
    validate_spec(spec)
    runtime = output / "runtime"
    evaluation = output / "eval"
    runtime.mkdir(parents=True, exist_ok=True)
    evaluation.mkdir(parents=True, exist_ok=True)

    rng = random.Random(spec["seed"])
    sources_by_id: dict[str, dict[str, Any]] = {}
    datasets_by_id: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    distractor_count = max(8, min(int(spec.get("distractors_per_variable", 18)), 100))

    def dimension_key(variable: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(variable[field]).casefold()
                     for field in ["name", "entity", "period", "scenario",
                                   "basis", "unit", "status"])

    # No distractor may share every required dimension with ANY supported
    # record: a scenario-flipped distractor of one variable must not land on
    # another variable's exact dimensions.
    supported_keys = {dimension_key(variable) for variable in spec["variables"]}

    for variable in spec["variables"]:
        for source in variable["sources"]:
            sid = source_id(source)
            sources_by_id.setdefault(sid, {
                "id": sid,
                "name": source["name"],
                "kind": source.get("kind", "publisher"),
                "role": source["role"],
                "origin_url": source["url"],
                "description": source.get(
                    "description",
                    "Synthetic research collection shaped from %s metadata."
                    % source["name"]),
            })
        primary = variable["sources"][0]
        sid = source_id(primary)
        dataset_id = stable_id("dataset", spec["environment_id"], sid)
        datasets_by_id.setdefault(dataset_id, {
            "id": dataset_id,
            "source_id": sid,
            "name": "%s versioned indicators" % primary["name"],
            "description": ("Records vary by entity, metric, period, scenario, "
                            "basis, unit, and status. Filter all relevant dimensions."),
            "dimensions": ["entity", "metric", "period", "scenario", "basis",
                           "unit", "status"],
        })

        doc_id = stable_id("doc", spec["environment_id"], variable["id"], "supported")
        supported = make_record(dataset_id, sid, doc_id, variable,
                                variable["value"], "%s-supported" % variable["id"])
        records.append(supported)
        documents.append(make_document(doc_id, primary, supported, "data-release",
                                       variable.get("published_at", "2019-12-18")))

        tasks.append({
            "task_id": variable["id"],
            "prompt": variable["question"],
            "answer": {"value": variable["value"], "unit": variable["unit"],
                       "tolerance": variable.get("tolerance", 0)},
            "required_dimensions": {
                "metric": variable["name"],
                **{field: variable[field]
                   for field in ["entity", "period", "scenario",
                                 "basis", "unit", "status"]},
            },
            "evidence": {"source_id": sid, "dataset_id": dataset_id,
                         "record_id": supported["id"], "document_id": doc_id},
            "workbook": variable.get("workbook", {}),
        })

        for index in range(distractor_count):
            altered = deepcopy(variable)
            mode = index % 5
            if mode == 0:
                altered["scenario"] = SCENARIOS[(index + 1) % len(SCENARIOS)]
            elif mode == 1:
                altered["basis"] = BASES[(index + 1) % len(BASES)]
            elif mode == 2:
                changed = alternate_period(str(variable["period"]), index)
                altered["period"] = changed
                if changed == str(variable["period"]):
                    altered["status"] = STATUSES[index % len(STATUSES)]
            elif mode == 3:
                altered["unit"] = alternate_unit(str(variable["unit"]), index)
            else:
                altered["status"] = STATUSES[index % len(STATUSES)]
            if dimension_key(altered) in supported_keys:
                altered["status"] = next(
                    status for status in STATUSES
                    if dimension_key({**altered, "status": status})
                    not in supported_keys)
            alt_source = variable["sources"][index % len(variable["sources"])]
            alt_sid = source_id(alt_source)
            alt_doc = stable_id("doc", spec["environment_id"], variable["id"],
                                "alternative", index)
            alt_record = make_record(dataset_id, alt_sid, alt_doc, altered,
                                     perturb(variable, index),
                                     "%s-alternative-%d" % (variable["id"], index))
            records.append(alt_record)
            documents.append(make_document(
                alt_doc, alt_source, alt_record,
                ["archive", "market-note", "technical-annex", "data-release"][index % 4],
                "%d-06-30" % (2017 + index % 8)))

    rng.shuffle(documents)
    rng.shuffle(records)
    server_config = {
        "name": "%s-research-service" % spec["environment_id"],
        "version": "1.0.0",
        "instructions": ("Discover sources and datasets, search and fetch evidence, "
                         "then filter records by every dimension relevant to the "
                         "question. Broad queries can return conflicting values."),
    }
    write_json(runtime / "server.json", server_config)
    write_json(runtime / "sources.json",
               sorted(sources_by_id.values(), key=lambda row: row["id"]))
    write_json(runtime / "datasets.json",
               sorted(datasets_by_id.values(), key=lambda row: row["id"]))
    write_jsonl(runtime / "documents.jsonl", documents)
    write_jsonl(runtime / "records.jsonl", records)
    write_jsonl(evaluation / "tasks.jsonl", tasks)
    write_json(output / "environment.json", {
        "environment_id": spec["environment_id"], "seed": spec["seed"],
        "tasks": len(tasks), "documents": len(documents), "records": len(records)})
    return {"tasks": len(tasks), "documents": len(documents),
            "records": len(records), "output": str(output)}
