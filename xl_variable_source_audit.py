#!/usr/bin/env python3
"""Generate auditable variable-to-source Markdown from an inputs workbook.

The deterministic half of this stage extracts compact input-band summaries from
``seg_out/<workbook>/segments.json`` and the corresponding inputs-only workbook.
GPT 5.6 Sol then selects externally sourced candidates and proposes plausible
source classes through Labelbox's LiteLLM endpoint.

The model never writes workbook cell references or values itself: every
inventory row carries a stable ``row_id`` and the model cites those ids only.
Deterministic code resolves each cited row_id back to the inventory's
qualified reference and raw values before emitting the final Markdown table,
so invented references are impossible by construction. The final table keeps
the historical ``| Variable | Workbook cells and values | Plausible external
source(s) |`` column set consumed by ``xl_variable_mcp.py import``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.request
from pathlib import Path

import openpyxl
from openpyxl.utils import range_boundaries

from xl_artifact_paths import resolve_workbook_artifact
from xl_task_build import DEFAULT_PROJECT_ID, PROD_ENDPOINT, read_env_key


GENERATOR_VERSION = "1.3.0"
PROMPT_VERSION = "variable-source-audit-v3"
DEFAULT_MODEL = "openai/gpt-5.6-sol"
MAX_CHUNK_CHARS = 100_000

MODEL_TABLE_HEADER = (
    "| Variable | Inventory row IDs | Plausible external source(s) |"
)
FINAL_TABLE_HEADER = (
    "| Variable | Workbook cells and values | Plausible external source(s) |"
)

SYSTEM_PROMPT = """\
You are GPT 5.6 Sol producing a review artifact for a financial-spreadsheet
pipeline. The user message contains workbook metadata and deterministic NDJSON
inventory rows extracted from an inputs-only workbook. Every inventory row
carries a stable identifier in its "row_id" field (for example "R0001").

Identify inputs that are plausibly externally sourced: market and macro data,
tax or regulatory rates, contractual and financing terms, asset facts,
historical accounting balances, transaction terms, and operating data that
would normally come from filings, contracts, advisers, or authoritative data
providers. Account for EVERY supplied inventory row. For labels, blank cells,
calculations or provably internal model choices, retain the row with an explicit
exclusion disposition instead of omitting it. Historical supplied-workbook
facts may have private/internal provenance in this synthetic research setting;
missing public URLs alone do not establish that an input is ineligible.

Return exactly one Markdown table with these columns:
| Variable | Inventory row IDs | Plausible external source(s) |

Rules:
1. Cite inventory rows only by their supplied row_id. The "Inventory row IDs"
   column must contain nothing but row_ids separated by commas. Never write
   workbook cell references or workbook values anywhere in the table; the
   pipeline deterministically resolves each cited row_id back to its exact
   cells and values.
2. Group related period series or repeated assumptions by citing several
   row_ids on one row when that improves auditability. Cite each row_id
   exactly once in the whole table, and cite only row_ids from the current batch.
   Do not drop rows because they appear uninteresting or have uncertain provenance.
   For an excluded source recommendation, use "Excluded: <specific reason>" in
   the Variable column and explain the evidence in the source column. Do not use
   a generic "not externally sourced" reason. These rows remain visible for
   downstream independent cell-level research-eligibility review; an audit
   exclusion alone never authorizes removing a task input.
3. Sources are candidates, not proof. Say when exact-value provenance is
   unverified or likely private/internal.
4. Prefer authoritative organizations, filings, contracts, and official data
   portals. Add a URL only when confident it is the canonical public page;
   otherwise name the source without fabricating a link.
5. Do not include a preamble, headings, commentary, or fenced code block.
If all supplied rows are excluded, cite every row with its justified exclusion.
Only an empty inventory may use exactly one sentinel row:
| No externally sourced candidates identified | — | — |"""


def _json_value(value):
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _value_summary(values):
    values = [_json_value(value) for value in values if value is not None]
    if not values:
        return {"count": 0}
    if all(value == values[0] for value in values):
        return {"count": len(values), "value": values[0]}
    if len(values) <= 16:
        return {"count": len(values), "values": values}
    numeric = [
        float(value) for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    summary = {
        "count": len(values),
        "first": values[:3],
        "last": values[-3:],
        "distinct_count": len({json.dumps(value, sort_keys=True)
                               for value in values}),
    }
    if len(numeric) == len(values):
        summary.update({"minimum": min(numeric), "maximum": max(numeric)})
    return summary


def _inventory_reference(book, ref):
    """Decode a single machine cell reference without stripping sheet identity."""
    from openpyxl.utils.cell import coordinate_to_tuple
    sheet, separator, coord = ref.rpartition("!")
    if not separator or not sheet:
        return None
    if sheet.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')*'", sheet):
            raise ValueError("invalid quoted inventory sheet reference")
        sheet = sheet[1:-1].replace("''", "'")
    if sheet not in book.sheetnames:
        return None
    if not re.fullmatch(r"\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,6}", coord):
        raise ValueError("inventory reference must identify one bounded cell")
    row, column = coordinate_to_tuple(coord.replace("$", ""))
    if row > 1048576 or column > 16384:
        raise ValueError("inventory reference outside Excel cell bounds")
    return sheet, row, column


def _cell_value(book, ref):
    selected = _inventory_reference(book, ref)
    if selected is None:
        return None
    sheet, row, column = selected
    return book[sheet].cell(row, column).value


def _selected_cell_values(book, references):
    """Read selected values in one sparse XML pass per requested sheet.

    Use the same typed parser/configuration as ReadOnlyWorksheet, avoiding its
    rectangular empty-cell expansion and a complete XML reparse for every ref.
    Formula values retain the caller's data_only mode; no value is synthesized.
    """
    from openpyxl.worksheet._reader import WorkSheetParser
    wanted, resolved = {}, {}
    for ref in references:
        target = _inventory_reference(book, ref)
        resolved[ref] = target
        if target is not None:
            sheet, row, column = target
            wanted.setdefault(sheet, set()).add((row, column))
    observed = {}
    for sheet, coordinates in wanted.items():
        worksheet = book[sheet]
        with worksheet._get_source() as source:
            parser = WorkSheetParser(source, worksheet._shared_strings,
                data_only=book.data_only, epoch=book.epoch,
                date_formats=book._date_formats,
                timedelta_formats=book._timedelta_formats)
            prior_row = 0
            for row_number, row in parser.parse():
                if row_number <= prior_row:
                    raise ValueError("ambiguous or unordered inventory worksheet rows")
                prior_row = row_number
                # Parser values already include shared strings, dates, errors,
                # booleans and the original formula cache. Keep native types.
                for cell in row:
                    key = (cell['row'], cell['column'])
                    if key in coordinates:
                        observed[(sheet, *key)] = cell['value']
    return {ref: observed.get(target) if target is not None else None
            for ref, target in resolved.items()}


def build_inventory(workbook, artifact, seg_dir, *, segmentation=None):
    """Return a compact, deterministic inventory of relevant input bands.

    Every row carries a stable ``row_id`` ("R0001", ...) assigned in sorted
    inventory order; the model cites these ids instead of writing references.
    """
    segments = json.loads(
        (Path(seg_dir) / "segments.json").read_text(encoding="utf-8")
    )
    book = openpyxl.load_workbook(artifact, data_only=True, read_only=True)
    try:
        rows = []
        seen = set()
        candidates = list(segments.get("inputs") or [])
        candidates.extend(segments.get("embedded_literals") or [])
        selected_entries = []
        for entry in candidates:
            band = str(entry.get("band") or "")
            if not band or band in seen or entry.get("kind") == "label":
                continue
            seen.add(band)
            selected_entries.append(entry)
        selected_values = _selected_cell_values(book,
            [ref for entry in selected_entries for ref in (entry.get("cells") or [])])
        for entry in selected_entries:
            band = str(entry.get("band") or "")
            cells = entry.get("cells") or []
            values = [selected_values[ref] for ref in cells]
            summary = _value_summary(values)
            # Empty formula-host bands carry no auditable supplied value. A
            # promoted literal has its hardcoded value in the label/band.
            if not values and entry.get("kind") != "literal":
                continue
            rows.append({
                "band": band,
                "sheet": entry.get("sheet") or band.partition("!")[0],
                "label": entry.get("label") or "",
                "kind": entry.get("kind") or "",
                "value_type": entry.get("vtype") or "",
                "value_summary": summary,
            })
        rows.sort(key=lambda row: (
            str(row["label"]).casefold(), str(row["sheet"]).casefold(),
            str(row["band"]),
        ))
        rows = [
            {"row_id": "R%04d" % index, **row}
            for index, row in enumerate(rows, 1)
        ]
        props = book.properties
        metadata = {
            "workbook": workbook,
            "artifact": Path(artifact).name,
            "sheet_names": list(book.sheetnames),
            "document_properties": {
                key: _json_value(getattr(props, key, None))
                for key in ("title", "subject", "creator", "description",
                            "keywords", "category", "company")
                if getattr(props, key, None)
            },
        }
        if segmentation is not None:
            metadata["segmentation"] = segmentation
        return {"metadata": metadata, "variables": rows}
    finally:
        book.close()


def _split_chunks(inventory, max_chars=MAX_CHUNK_CHARS):
    header = json.dumps(inventory["metadata"], ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"))
    chunks, current, size = [], [], len(header)
    for row in inventory["variables"]:
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if current and size + len(encoded) + 1 > max_chars:
            chunks.append(current)
            current, size = [], len(header)
        current.append(encoded)
        size += len(encoded) + 1
    if current or not chunks:
        chunks.append(current)
    return [(header, rows) for rows in chunks]


def _call_model(endpoint, api_key, model, project_id, messages):
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "x-labelbox-context": json.dumps({"project_id": project_id}),
        },
    )
    from xl_transport import read_response
    payload = json.loads(read_response(request, timeout=600).decode("utf-8"))
    choice = payload["choices"][0]
    return choice["message"]["content"].strip(), choice.get("finish_reason", "")


def _request_config_fingerprint(endpoint, model, project_id):
    """Hash non-secret routing configuration without prompt or workbook data."""
    return _canonical_hash({
        "endpoint": endpoint.rstrip("/"),
        "model": model,
        "project_id": project_id,
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
    })


def _http_error_details(exc):
    """Return bounded gateway diagnostics without retaining request content."""
    details = {
        "status": getattr(exc, "code", None),
        "reason": str(getattr(exc, "reason", "") or ""),
    }
    try:
        raw = exc.read(4096)
    except (AttributeError, OSError):
        raw = b""
    if not raw:
        return details
    details["response_sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return details
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return details
    safe = {
        key: value
        for key in ("type", "param", "code")
        if isinstance((value := error.get(key)), (str, int, float, bool))
    }
    message = error.get("message")
    if (
        isinstance(message, str)
        and len(message) <= 512
        and "\n" not in message
        and not re.search(r"\bR\d{4}\b|Workbook metadata:|![A-Z]{1,3}\d+", message)
    ):
        safe["message"] = re.sub(
            r"(?i)\b(bearer|api[_-]?key)\s*[:=]?\s*\S+",
            r"\1 [REDACTED]",
            message,
        )
    if safe:
        details["response_error"] = safe
    return details


def _clean_table(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    match = re.search(
        r"(?im)^\|\s*Variable\s*\|\s*Inventory row IDs\s*\|"
        r"\s*Plausible external source\(s\)\s*\|",
        text,
    )
    if not match:
        raise ValueError("model response did not contain the required table")
    return text[match.start():].strip()


ROW_ID_RE = re.compile(r"\bR\d{4,}\b")
# Text allowed in the row-id column once every cited row_id is removed.
_ROW_ID_FILLER_RE = re.compile(r"^[\s,;`*—–-]*$")


def _table_data_rows(table):
    """Return [variable, ids/cells, source] triples for each data row."""
    rows = []
    for line in table.splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        columns = [column.strip() for column in re.split(r"(?<!\\)\|", stripped[1:-1])]
        if len(columns) < 3:
            continue
        if all(set(column) <= set("-: ") for column in columns):
            continue
        rows.append(columns[:3])
    return rows


def validate_row_ids(table, batch_row_ids, inventory_row_ids):
    """Return violations for row_id citations in the raw model table.

    The model must cite inventory rows only by row_id: unknown, duplicate, and
    out-of-batch ids fail, as does any other text (such as a hand-written cell
    reference) in the row-id column.
    """
    if not isinstance(table, str) or table.strip().splitlines()[0] != MODEL_TABLE_HEADER:
        return ["raw audit table header is invalid"]
    for line in table.strip().splitlines()[1:]:
        if (not line.strip().startswith("|") or not line.strip().endswith("|")
                or len(re.split(r"(?<!\\)\|", line.strip()[1:-1])) != 3):
            return ["raw audit table must contain exactly three columns"]
    data_rows = _table_data_rows(table)
    if not data_rows:
        return ["table contains no data rows"]
    violations = []
    cited = set()
    rows_with_ids = 0
    for variable, id_column, _ in data_rows:
        row_ids = ROW_ID_RE.findall(id_column)
        if row_ids:
            rows_with_ids += 1
        leftover = ROW_ID_RE.sub("", id_column)
        if not _ROW_ID_FILLER_RE.match(leftover):
            violations.append(
                "row_id column may cite only supplied row_ids, found %r "
                "(row %s)" % (leftover.strip(" \t,;`*"), variable))
        for row_id in row_ids:
            if row_id in cited:
                violations.append("duplicate row_id citation: %s" % row_id)
            cited.add(row_id)
            if row_id not in inventory_row_ids:
                violations.append(
                    "unknown row_id: %s (row %s)" % (row_id, variable))
            elif row_id not in batch_row_ids:
                violations.append(
                    "row_id not in this batch: %s (row %s)"
                    % (row_id, variable))
    # Complete coverage is mandatory even when the model recommends no sources.
    missing = sorted(set(batch_row_ids) - cited)
    if missing:
        violations.append("missing inventory row dispositions: " + ", ".join(missing))
    for variable, _, source in data_rows:
        if variable.lower().startswith("excluded"):
            reason = variable.partition(":")[2].strip()
            if not reason or reason.casefold() in {"none", "n/a", "not externally sourced", "not a candidate"}:
                violations.append("excluded row requires a specific reason")
            if not source.strip(" -—–") or source.strip().casefold() in {"none", "n/a"}:
                violations.append("excluded row requires an evidence explanation")
    sentinel = (not batch_row_ids and len(data_rows) == 1 and rows_with_ids == 0
                and data_rows[0][0].strip() == "No externally sourced candidates identified")
    if not sentinel:
        for variable, id_column, _ in data_rows:
            if not ROW_ID_RE.search(id_column):
                violations.append("row cites no row_id: %s" % variable)
    return list(dict.fromkeys(violations))


def _validate_inventory_schema(inventory):
    if not isinstance(inventory, dict) or not isinstance(inventory.get("metadata"), dict):
        return ["audit inventory schema is invalid"]
    metadata, rows = inventory["metadata"], inventory.get("variables")
    if (not isinstance(rows, list) or not isinstance(metadata.get("workbook"), str)
            or not metadata["workbook"] or not isinstance(metadata.get("artifact"), str)
            or not metadata["artifact"] or not isinstance(metadata.get("sheet_names"), list)
            or not metadata["sheet_names"]
            or any(not isinstance(name, str) or not name for name in metadata["sheet_names"])):
        return ["audit inventory scope is invalid"]
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("row_id"), str) or not ROW_ID_RE.fullmatch(row["row_id"]):
            return ["audit inventory row identity is invalid"]
        if row["row_id"] in seen:
            return ["duplicate audit inventory row_id: " + row["row_id"]]
        seen.add(row["row_id"])
        if (not isinstance(row.get("band"), str) or not row["band"]
                or not isinstance(row.get("value_summary"), dict)):
            return ["audit inventory row schema is invalid"]
        count = row["value_summary"].get("count")
        if type(count) is not int or count < 0:
            return ["audit inventory value summary is invalid"]
    return []


def _coverage_dispositions(coverage):
    included, excluded = [], []
    for batch in coverage["batches"]:
        for variable, ids, _ in _table_data_rows(batch["model_table"]):
            target = excluded if variable.casefold().startswith("excluded:") else included
            target.extend(ROW_ID_RE.findall(ids))
    return {"included_row_ids": included, "excluded_row_ids": excluded,
            "included_inventory_rows": len(included), "excluded_inventory_rows": len(excluded)}


def validate_audit_coverage(text, inventory, coverage):
    """Read-only full row disposition and final-table reconstruction check."""
    faults = _validate_inventory_schema(inventory)
    if faults:
        return faults
    if not isinstance(text, str):
        return ["audit text is invalid"]
    if not isinstance(coverage, dict) or coverage.get("schema_version") != "variable-source-audit-coverage/v1":
        return ["missing or unsupported audit coverage evidence"]
    if coverage.get("inventory_sha256") != _canonical_hash(inventory):
        return ["coverage inventory binding changed"]
    batches = coverage.get("batches")
    expected_batches = _split_chunks(inventory)
    if not isinstance(batches, list) or len(batches) != len(expected_batches):
        return ["audit coverage batch count changed"]
    all_ids = {row["row_id"] for row in inventory["variables"]}
    rows_by_id = {row["row_id"]: row for row in inventory["variables"]}
    faults, resolved_rows = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and (not line.endswith("|") or len(re.split(r"(?<!\\)\|", line[1:-1])) != 3):
            faults.append("final audit table must contain exactly three columns")
    for batch, (_, encoded_rows) in zip(batches, expected_batches):
        expected_ids = [json.loads(row)["row_id"] for row in encoded_rows]
        if not isinstance(batch, dict) or batch.get("row_ids") != expected_ids:
            faults.append("audit coverage batch row IDs changed")
            continue
        table = batch.get("model_table")
        if not isinstance(table, str):
            faults.append("audit coverage raw table missing")
            continue
        violations = validate_row_ids(table, set(expected_ids), all_ids)
        faults.extend(violations)
        if violations:
            continue
        resolved = resolve_table(table, rows_by_id)
        faults.extend(validate_table(resolved, encoded_rows, inventory["metadata"]))
        resolved_rows.extend(_table_data_rows(resolved))
    headers = {tuple(_table_data_rows("ignored\n" + header)[0])
               for header in (MODEL_TABLE_HEADER, FINAL_TABLE_HEADER)}
    actual_rows = [row for row in _table_data_rows(text) if tuple(row) not in headers]
    if actual_rows != resolved_rows:
        faults.append("final audit rows disagree with complete raw coverage")
    return list(dict.fromkeys(faults))


class AuditRevalidationRequired(ValueError):
    """Intact historical evidence does not establish the current audit contract."""


def validate_saved_audit_coverage(metadata_path, inventory_path, markdown_path, *, coverage_path=None,
                                  model=DEFAULT_MODEL, endpoint=PROD_ENDPOINT, project_id=DEFAULT_PROJECT_ID):
    """Validate saved coverage without service calls or file mutations.

    Missing coverage is unproven evidence, never evidence that the workbook is invalid.
    """
    metadata_path, inventory_path, markdown_path = map(Path, (metadata_path, inventory_path, markdown_path))
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("audit metadata is missing or unreadable") from exc
    if not isinstance(metadata, dict) or metadata.get("status") != "complete":
        raise ValueError("audit metadata is not complete")
    expected_config = _request_config_fingerprint(endpoint, model, project_id)
    if (metadata.get("generator_version") != GENERATOR_VERSION or metadata.get("prompt_version") != PROMPT_VERSION
            or metadata.get("generator_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise AuditRevalidationRequired("audit generator or prompt version requires revalidation")
    if (metadata.get("model") != model or not isinstance(metadata.get("endpoint"), str) or metadata["endpoint"].rstrip("/") != endpoint.rstrip("/")
            or metadata.get("project_id") != project_id or metadata.get("request_config_sha256") != expected_config
            or metadata.get("system_prompt_sha256") != hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()):
        raise AuditRevalidationRequired("audit request/model/prompt configuration requires revalidation")
    if (not isinstance(metadata.get("coverage_path"), str) or not metadata["coverage_path"]
            or not isinstance(metadata.get("coverage_sha256"), str) or not metadata["coverage_sha256"]):
        raise ValueError("missing bound audit coverage evidence")
    if coverage_path is None:
        suffix = ".metadata.json"
        if not metadata_path.name.endswith(suffix):
            raise ValueError("audit metadata filename cannot resolve coverage")
        coverage_path = metadata_path.with_name(metadata_path.name[:-len(suffix)] + ".coverage.json")
    coverage_path = Path(coverage_path)
    if Path(metadata["coverage_path"]).resolve() != coverage_path.resolve():
        raise ValueError("audit coverage path changed")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        text = markdown_path.read_text(encoding="utf-8")
        raw = coverage_path.read_bytes()
        coverage = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("audit coverage evidence is missing or unreadable") from exc
    if metadata["coverage_sha256"] != hashlib.sha256(raw).hexdigest():
        raise ValueError("audit coverage hash changed")
    if metadata.get("inventory_sha256") != _canonical_hash(inventory):
        raise ValueError("audit inventory hash changed")
    if metadata.get("markdown_sha256") != hashlib.sha256(markdown_path.read_bytes()).hexdigest():
        raise ValueError("audit text hash changed")
    try:
        faults = validate_audit_coverage(text, inventory, coverage)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("audit coverage structure is invalid") from exc
    if faults:
        raise ValueError("; ".join(faults))
    _validate_saved_attempts(metadata, inventory, coverage, endpoint, model, project_id)
    return {"passed": True, "inventory_rows": len(inventory["variables"]),
            **_coverage_dispositions(coverage),
            "coverage_path": str(coverage_path), "coverage_sha256": metadata["coverage_sha256"]}


def _saved_coverage_valid(metadata_path, inventory_path, markdown_path, coverage_path, **config):
    try:
        validate_saved_audit_coverage(metadata_path, inventory_path, markdown_path,
                                      coverage_path=coverage_path, **config)
        return True
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False


def _base_messages(header, rows, index, total):
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content":
        "Workbook metadata:\n%s\n\nInventory batch %d of %d (NDJSON):\n%s" % (header, index, total, "\n".join(rows))}]


def _correction_messages(text, violations):
    return [{"role": "assistant", "content": text}, {"role": "user", "content":
            "Correct the table. Cite inventory rows only by their supplied row_id and fix these problems:\n- "
            + "\n- ".join(violations[:20])}]


def _attempt_scope_config(inventory_hash, endpoint, model, project_id):
    return {"inventory_sha256": inventory_hash, "request_config_sha256": _request_config_fingerprint(endpoint, model, project_id),
            "generator_version": GENERATOR_VERSION, "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "prompt_version": PROMPT_VERSION,
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()}


def _open_attempt_scope(output_dir, scope, limit, metadata_path, inherited_budget, inventory):
    directory = output_dir / ".audit-attempts" / _canonical_hash(scope)
    policy_path = directory / "policy.json"
    if policy_path.exists():
        try:
            policy = json.loads(policy_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("audit attempt policy is unreadable") from exc
        if (not isinstance(policy, dict) or policy.get("schema_version") != "audit-attempt-policy/v1" or policy.get("scope") != scope
                or type(policy.get("limit")) is not int or policy["limit"] not in (1, 2)):
            raise ValueError("audit attempt policy is invalid")
        consumed = _inherited_batch_counts(policy.get("inherited_budget"), inventory)
        if inherited_budget is not None and inherited_budget != policy.get("inherited_budget"):
            raise ValueError("audit inherited budget binding cannot change")
        if limit > policy["limit"]:
            raise ValueError("audit semantic budget cannot be increased")
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("audit attempt policy is missing from existing history")
        if metadata_path.exists():
            try:
                previous = json.loads(metadata_path.read_text())
            except (OSError, ValueError):
                previous = {}
            if previous.get("attempt_scope") == scope:
                raise ValueError("audit attempt history is missing; budget cannot be reset")
        directory.mkdir(parents=True, exist_ok=True)
        _inherited_batch_counts(inherited_budget, inventory)
        policy = {"schema_version": "audit-attempt-policy/v1", "scope": scope, "limit": limit,
                  "inherited_budget": inherited_budget, "submitted_counts": {}}
        _atomic_text(policy_path, json.dumps(policy, sort_keys=True) + "\n")
    if not isinstance(policy.get("submitted_counts"), dict) or any(type(count) is not int or not 0 <= count <= 2 for count in policy["submitted_counts"].values()):
        raise ValueError("audit persisted attempt counts are invalid")
    return directory, policy


def _request_paths(directory, index):
    batch = directory / ("batch-%04d" % index)
    batch.mkdir(exist_ok=True)
    paths = sorted(batch.glob("*.request.json"))
    if [path.name for path in paths] != ["%02d.request.json" % i for i in range(1, len(paths) + 1)]:
        raise ValueError("audit attempt numbering is invalid")
    return batch, paths


def _file_binding(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _read_bound_json(binding):
    if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
        raise ValueError("audit attempt evidence is missing")
    try:
        raw = Path(binding["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
            raise ValueError("audit attempt evidence hash changed")
        return json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("audit attempt evidence is unreadable") from exc


def _validate_inherited_budget(binding, workbook):
    """Count real prior calls from a hash-bound receipt; never fabricate a journal."""
    if binding is None:
        return []
    receipt = _read_bound_json(binding)
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != "audit-inherited-budget/v1"
            or receipt.get("workbook") != workbook or not isinstance(receipt.get("attempts"), list)
            or not 1 <= len(receipt["attempts"])):
        raise ValueError("audit inherited budget receipt is invalid")
    seen, original_batches = set(), []
    for attempt in receipt["attempts"]:
        if not isinstance(attempt, dict):
            raise ValueError("audit inherited attempt is invalid")
        request = _read_bound_json(attempt.get("request"))
        response = _read_bound_json(attempt.get("response"))
        identity = (attempt["request"]["path"], attempt["request"]["sha256"])
        if identity in seen:
            raise ValueError("duplicate inherited audit request")
        seen.add(identity)
        if (not isinstance(request, dict) or not isinstance(request.get("model"), str)
                or not isinstance(request.get("endpoint"), str)
                or not isinstance(request.get("messages"), list) or len(request["messages"]) < 2
                or not isinstance(response, dict) or not isinstance(response.get("content"), str)
                or not isinstance(response.get("finish_reason"), str)):
            raise ValueError("audit inherited request/response is invalid")
        user = request["messages"][1]
        try:
            if user.get("role") != "user" or not user["content"].startswith("Workbook metadata:\n"):
                raise ValueError("missing original audit metadata")
            metadata, _ = json.JSONDecoder().raw_decode(user["content"].split("\n", 1)[1])
            if metadata.get("workbook") != workbook:
                raise ValueError("workbook mismatch")
            match = re.search(r"\n\nInventory batch (\d+) of (\d+) \(NDJSON\):\n", user["content"])
            if not match:
                raise ValueError("missing original audit batch")
            original_rows = [json.loads(line) for line in user["content"][match.end():].splitlines() if line.strip()]
            ids = [row["row_id"] for row in original_rows]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate original batch identities")
            original_batches.append(original_rows)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError("inherited audit request is not bound to this workbook") from exc
    return original_batches


def _inherited_batch_counts(binding, inventory):
    batches = [[json.loads(row) for row in encoded] for _, encoded in _split_chunks(inventory)]
    identities = [{(row["row_id"], row["band"]) for row in batch} for batch in batches]
    counts = {index: 0 for index in range(1, len(batches) + 1)}
    for original in _validate_inherited_budget(binding, inventory["metadata"]["workbook"]):
        try:
            original_ids = {(row["row_id"], row["band"]) for row in original}
        except (KeyError, TypeError) as exc:
            raise ValueError("inherited audit batch identities are invalid") from exc
        matches = [index for index, ids in enumerate(identities, 1) if ids == original_ids]
        if len(matches) != 1:
            raise ValueError("inherited audit batch cannot map exactly; rebatching needs explicit budget reconciliation")
        counts[matches[0]] += 1
    if any(value > 2 for value in counts.values()):
        raise ValueError("inherited audit batch exceeds semantic budget")
    return counts


def _validate_saved_attempts(metadata, inventory, coverage, endpoint, model, project_id):
    scope = _attempt_scope_config(_canonical_hash(inventory), endpoint, model, project_id)
    if metadata.get("attempt_scope") != scope:
        raise ValueError("audit attempt scope changed")
    policy = _read_bound_json(metadata.get("attempt_policy"))
    if (not isinstance(policy, dict) or policy.get("schema_version") != "audit-attempt-policy/v1"
            or policy.get("scope") != scope or type(policy.get("limit")) is not int or policy["limit"] not in (1, 2)):
        raise ValueError("audit attempt policy is invalid")
    consumed = _inherited_batch_counts(policy.get("inherited_budget"), inventory)
    if not isinstance(policy.get("submitted_counts"), dict) or set(policy["submitted_counts"]) != {str(i) for i in consumed}:
        raise ValueError("audit persisted batch counts are incomplete or unknown")
    for index, ((header, rows), batch) in enumerate(zip(_split_chunks(inventory), coverage["batches"]), 1):
        attempt = batch.get("attempt")
        if (type(attempt) is not int or not 1 <= attempt <= min(policy["limit"], 2 - consumed[index])
                or not isinstance(policy.get("submitted_counts"), dict) or policy["submitted_counts"].get(str(index)) != attempt):
            raise ValueError("audit attempt exceeds semantic budget")
        request, response = _read_bound_json(batch.get("request")), _read_bound_json(batch.get("response"))
        expected = {"endpoint": endpoint.rstrip("/"), "model": model, "project_id": project_id,
                    "scope": scope, "batch": index, "attempt": attempt}
        if not isinstance(request, dict) or any(request.get(k) != v for k, v in expected.items()):
            raise ValueError("audit saved request configuration changed")
        messages = request.get("messages")
        if not isinstance(messages, list) or messages[:2] != _base_messages(header, rows, index, len(coverage["batches"])):
            raise ValueError("audit saved request prompt/inventory changed")
        if (not isinstance(response, dict) or response.get("request_sha256") != batch["request"]["sha256"]
                or not isinstance(response.get("content"), str) or _clean_table(response["content"]) != batch["model_table"]):
            raise ValueError("audit raw table differs from bound model response")
        if response.get("finish_reason") != "stop":
            raise ValueError("audit model completion is incomplete")
        history = batch.get("history")
        if not isinstance(history, list) or len(history) != attempt:
            raise ValueError("audit semantic attempt history is incomplete")
        for number, binding in enumerate(history, 1):
            prior = _read_bound_json(binding)
            if (not isinstance(prior, dict) or prior.get("scope") != scope
                    or prior.get("batch") != index or prior.get("attempt") != number):
                raise ValueError("audit semantic attempt history changed")
        if history[-1] != batch["request"]:
            raise ValueError("audit accepted attempt is not final history entry")
        response_history = batch.get("response_history")
        if not isinstance(response_history, list) or len(response_history) != attempt or response_history[-1] != batch["response"]:
            raise ValueError("audit response history is incomplete")
        prior_responses = []
        for old_request, old_response in zip(history, response_history):
            if old_response is None:
                prior_responses.append(None)
                continue
            item = _read_bound_json(old_response)
            if not isinstance(item, dict) or item.get("request_sha256") != old_request["sha256"] or not isinstance(item.get("content"), str):
                raise ValueError("audit response history binding changed")
            prior_responses.append(item)
        if len(messages) > 2:
            expected_messages = _base_messages(header, rows, index, len(coverage["batches"]))
            for old in prior_responses[:-1]:
                if old is None:
                    raise ValueError("audit correction lacks a received response")
                old_table = _clean_table(old["content"])
                row_ids = {json.loads(row)["row_id"] for row in rows}
                violations = validate_row_ids(old_table, row_ids, {row["row_id"] for row in inventory["variables"]})
                if not violations:
                    violations = validate_table(resolve_table(old_table, {row["row_id"]: row for row in inventory["variables"]}), rows, inventory["metadata"])
                if not violations:
                    raise ValueError("audit correction does not follow a rejected response")
                expected_messages.extend(_correction_messages(old["content"], violations))
            if messages != expected_messages:
                raise ValueError("audit correction instructions changed")



def _summary_text(summary):
    """Render a value_summary using only tokens present in the NDJSON row."""
    count = summary.get("count", 0)
    if not count:
        return "(no supplied value)"
    if "value" in summary:
        text = "`%s`" % json.dumps(summary["value"], ensure_ascii=False)
        return text if count == 1 else "%s (×%d)" % (text, count)
    if "values" in summary:
        return "`%s`" % json.dumps(summary["values"], ensure_ascii=False)
    parts = ["%d values" % count]
    if "minimum" in summary:
        parts.append("min %s, max %s" % (
            json.dumps(summary["minimum"]), json.dumps(summary["maximum"])))
    return "`%s` … `%s` (%s)" % (
        json.dumps(summary.get("first", []), ensure_ascii=False),
        json.dumps(summary.get("last", []), ensure_ascii=False),
        "; ".join(parts),
    )


def _resolved_cells_text(row):
    text = "`%s`" % row["band"]
    label = "<br>".join(str(row.get("label") or "").strip().splitlines())
    if label:
        text += " %s" % label
    return ("%s: %s" % (text, _summary_text(row["value_summary"]))
            ).replace("|", "\\|")


def resolve_table(model_table, rows_by_id):
    """Deterministically rewrite cited row_ids into the downstream table.

    The model only picks row_ids; this function writes every qualified
    reference and raw value from the inventory, emitting the historical
    ``| Variable | Workbook cells and values | ... |`` column set.
    """
    lines = [FINAL_TABLE_HEADER, "|---|---|---|"]
    for variable, id_column, source in _table_data_rows(model_table):
        row_ids = ROW_ID_RE.findall(id_column)
        resolved = "<br>".join(
            _resolved_cells_text(rows_by_id[row_id]) for row_id in row_ids
        ) or "—"
        lines.append("| %s | %s | %s |" % (variable, resolved, source))
    return "\n".join(lines)


VALUE_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[-+]?(?:\d+\.\d+|\d+)(?:[eE][-+]?\d+)?"
)


def _normalized_number(token):
    try:
        return decimal.Decimal(token).normalize()
    except decimal.InvalidOperation:
        return None


def validate_table(table, encoded_rows, workbook_metadata):
    """Return hallucination violations for refs and values in an audit table.

    Applied to the FINAL resolved table: since deterministic code now writes
    every reference and value from the inventory, these checks serve as
    invariants that the row_id resolution cannot drift from the inventory.
    """
    rows = [json.loads(row) for row in encoded_rows]
    allowed_cells = set()
    for row in rows:
        band = str(row["band"]).split("#lit=", 1)[0]
        sheet, _, coords = band.rpartition("!")
        sheet = sheet.strip("'")
        try:
            min_col, min_row, max_col, max_row = range_boundaries(coords)
        except ValueError:
            continue
        for row_num in range(min_row, max_row + 1):
            for col_num in range(min_col, max_col + 1):
                allowed_cells.add((sheet, row_num, col_num))

    violations = []
    sheets = sorted(workbook_metadata.get("sheet_names") or [],
                    key=len, reverse=True)
    if sheets:
        alternatives = "|".join(
            "(?:%s|'%s')" % (re.escape(sheet), re.escape(sheet))
            for sheet in sheets
        )
        ref_re = re.compile(
            r"(?<![A-Za-z0-9_])(?P<sheet>%s)!"
            r"(?P<coords>[A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?)"
            % alternatives
        )
        for match in ref_re.finditer(table):
            sheet = match.group("sheet").strip("'")
            try:
                min_col, min_row, max_col, max_row = range_boundaries(
                    match.group("coords")
                )
            except ValueError:
                violations.append("invalid reference: %s" % match.group(0))
                continue
            unknown = [
                (row_num, col_num)
                for row_num in range(min_row, max_row + 1)
                for col_num in range(min_col, max_col + 1)
                if (sheet, row_num, col_num) not in allowed_cells
            ]
            if unknown:
                violations.append("reference not in inventory: %s"
                                  % match.group(0))

    allowed_numbers = {
        value for value in (
            _normalized_number(token)
            for token in VALUE_NUMBER_RE.findall("\n".join(encoded_rows))
        )
        if value is not None
    }
    for line in table.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        columns = line.strip().strip("|").split("|")
        if len(columns) < 3 or "Workbook cells and values" in columns[1]:
            continue
        for token in VALUE_NUMBER_RE.findall(columns[1]):
            value = _normalized_number(token)
            if value is not None and value not in allowed_numbers:
                violations.append(
                    "value not in inventory: %s (row %s)"
                    % (token, columns[0].strip())
                )
    return list(dict.fromkeys(violations))


def _canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_text(path, text):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _inventory_matches(path, expected_hash):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return isinstance(value, dict) and _canonical_hash(value) == expected_hash
    except (OSError, ValueError):
        return False


def _publish_inventory(path, inventory):
    # A cache consumer must not churn byte-bound downstream evidence merely
    # because another caller constructed equivalent dictionaries in a new order.
    if not _inventory_matches(path, _canonical_hash(inventory)):
        _atomic_text(path, json.dumps(inventory, sort_keys=True, indent=2,
                                      ensure_ascii=False) + "\n")


def publish_inventory_only(workbook, inventory, output_dir):
    """Serialize inventory-only writes with audits; invalidate changed evidence first."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "%s-inputs-variable-sources" % workbook
    path = output_dir / (stem + ".inventory.json")
    with (output_dir / (stem + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inventory_hash = _canonical_hash(inventory)
        if not _inventory_matches(path, inventory_hash):
            _atomic_text(output_dir / (stem + ".metadata.json"), json.dumps({
                "status": "incomplete", "workbook": workbook,
                "reason": "inventory_only_update_requires_audit",
                "inventory_sha256": inventory_hash,
            }, sort_keys=True) + "\n")
            _publish_inventory(path, inventory)
    return path


def generate_audit(
        workbook, artifact, seg_dir, output_dir, api_key, **options):
    """Serialize evidence publication and reuse for this workbook."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / ("%s-inputs-variable-sources.lock" % workbook)).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _generate_audit(workbook, artifact, seg_dir, output_dir, api_key, **options)


def _generate_audit(
        workbook, artifact, seg_dir, output_dir, api_key, *,
        model=DEFAULT_MODEL, endpoint=PROD_ENDPOINT,
        project_id=DEFAULT_PROJECT_ID, refresh=False, log=print,
        segmentation=None, semantic_attempt_limit=2, inherited_budget=None):
    """Generate (or reuse) the Markdown audit and return its metadata."""
    if type(semantic_attempt_limit) is not int or semantic_attempt_limit not in (1, 2):
        raise ValueError("semantic_attempt_limit must be 1 or 2; limits cannot be reset here")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "%s-inputs-variable-sources" % workbook
    markdown_path = output_dir / (stem + ".md")
    inventory_path = output_dir / (stem + ".inventory.json")
    metadata_path = output_dir / (stem + ".metadata.json")
    coverage_path = output_dir / (stem + ".coverage.json")

    inventory = build_inventory(
        workbook, artifact, seg_dir, segmentation=segmentation
    )
    inventory_hash = _canonical_hash(inventory)
    cached_inventory_matches = _inventory_matches(inventory_path, inventory_hash)

    if not refresh and markdown_path.is_file() and metadata_path.is_file():
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        if (
            previous.get("status") == "complete"
            and _saved_coverage_valid(metadata_path, inventory_path, markdown_path, coverage_path,
                                      model=model, endpoint=endpoint, project_id=project_id)
            and cached_inventory_matches
            and previous.get("inventory_sha256") == inventory_hash
            and previous.get("model") == model
            and previous.get("prompt_version") == PROMPT_VERSION
            and previous.get("generator_version") == GENERATOR_VERSION
            and previous.get("request_config_sha256") == _request_config_fingerprint(
                endpoint, model, project_id)
            and previous.get("markdown_sha256") == hashlib.sha256(
                markdown_path.read_bytes()).hexdigest()
            and not validate_table(
                markdown_path.read_text(encoding="utf-8"),
                [json.dumps(row) for row in inventory["variables"]], inventory["metadata"])
            and (previous.get("validation") or {}).get("status") == "passed"
        ):
            result = dict(previous)
            result["cache_hit"] = True
            log("variable-source audit reused: %s" % markdown_path)
            return result

    if not api_key:
        raise RuntimeError(
            "no lbx_labelbox_api_key found; use --no-variable-source-audit to opt out"
        )

    scope = _attempt_scope_config(inventory_hash, endpoint, model, project_id)
    attempt_dir, attempt_policy = _open_attempt_scope(output_dir, scope, semantic_attempt_limit, metadata_path, inherited_budget, inventory)
    inherited_counts = _inherited_batch_counts(attempt_policy.get("inherited_budget"), inventory)
    previous_status = None
    if metadata_path.exists():
        try:
            previous_status = json.loads(metadata_path.read_text()).get("status")
        except (OSError, ValueError, AttributeError):
            pass
    _atomic_text(metadata_path, json.dumps({
        "attempt_scope": scope,
        "status": "in_progress", "workbook": workbook,
        "inventory_sha256": inventory_hash,
        "generator_version": GENERATOR_VERSION,
    }, sort_keys=True) + "\n")
    _publish_inventory(inventory_path, inventory)

    chunks = _split_chunks(inventory)
    all_row_ids = {row["row_id"] for row in inventory["variables"]}
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tables, finishes, coverage_batches = [], [], []
    new_api_calls = 0
    replayed_responses = 0
    try:
        for index, (header, rows) in enumerate(chunks, 1):
            rows_by_id = {
                decoded["row_id"]: decoded
                for decoded in (json.loads(row) for row in rows)
            }
            log("variable-source audit %s: GPT batch %d/%d" % (workbook, index, len(chunks)))
            messages = _base_messages(header, rows, index, len(chunks))
            batch_dir, prior_requests = _request_paths(attempt_dir, index)
            if len(prior_requests) != attempt_policy["submitted_counts"].get(str(index), 0):
                raise ValueError("audit request history is incomplete; consumed budget cannot be reset")
            batch_limit = min(attempt_policy["limit"], 2 - inherited_counts[index])
            if len(prior_requests) > batch_limit:
                raise ValueError("audit semantic budget history exceeds limit")
            # A completed response surviving interruption may be reconstructed without
            # another model call, but only after all current response gates pass.
            replay = None
            if previous_status != "complete" and prior_requests:
                response_path = prior_requests[-1].with_name(prior_requests[-1].name.replace(".request.", ".response."))
                if response_path.exists():
                    response = json.loads(response_path.read_text())
                    request = json.loads(prior_requests[-1].read_text())
                    if (response.get("request_sha256") != hashlib.sha256(prior_requests[-1].read_bytes()).hexdigest()
                            or request.get("scope") != scope or request.get("messages", [])[:2] != messages):
                        raise ValueError("audit interrupted response binding changed")
                    if response.get("finish_reason") != "stop":
                        raise ValueError("audit model completion is incomplete")
                    try:
                        candidate = _clean_table(response["content"])
                        violations = validate_row_ids(candidate, set(rows_by_id), all_row_ids)
                        if not violations:
                            violations = validate_table(resolve_table(candidate, rows_by_id), rows, inventory["metadata"])
                        if not violations:
                            replay = (response["content"], response.get("finish_reason", ""))
                    except (ValueError, KeyError):
                        pass
            available = min(semantic_attempt_limit, batch_limit - len(prior_requests))
            if replay is None and available <= 0:
                raise ValueError("audit semantic budget exhausted; persisted attempts cannot be reset")
            for attempt in range(1 if replay else available):
                if replay is not None:
                    text, finish = replay
                    replayed_responses += 1
                    request_path = prior_requests[-1]
                    response_path = request_path.with_name(request_path.name.replace(".request.", ".response."))
                else:
                    number = len(prior_requests) + 1
                    request_path = batch_dir / ("%02d.request.json" % number)
                    response_path = batch_dir / ("%02d.response.json" % number)
                    request = {"endpoint": endpoint.rstrip("/"), "model": model, "project_id": project_id,
                               "scope": scope, "batch": index, "attempt": number, "messages": messages}
                    _atomic_text(request_path, json.dumps(request, sort_keys=True) + "\n")
                    prior_requests.append(request_path)
                    attempt_policy["submitted_counts"][str(index)] = number
                    _atomic_text(attempt_dir / "policy.json", json.dumps(attempt_policy, sort_keys=True) + "\n")
                    new_api_calls += 1
                    text, finish = _call_model(endpoint, api_key, model, project_id, messages)
                    _atomic_text(response_path, json.dumps({"request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
                                 "content": text, "finish_reason": finish}, sort_keys=True) + "\n")
                finishes.append(finish)
                if finish != "stop":
                    raise ValueError("audit model completion is incomplete")
                model_table = _clean_table(text)
                violations = validate_row_ids(
                    model_table, set(rows_by_id), all_row_ids
                )
                if not violations:
                    table = resolve_table(model_table, rows_by_id)
                    violations = validate_table(
                        table, rows, inventory["metadata"]
                    )
                if not violations:
                    tables.append(table)
                    coverage_batches.append({"row_ids": list(rows_by_id), "model_table": model_table,
                                             "attempt": len(prior_requests), "request": _file_binding(request_path),
                                             "response": _file_binding(response_path),
                                             "history": [_file_binding(path) for path in prior_requests],
                                             "response_history": [_file_binding(path.with_name(path.name.replace(".request.", ".response.")))
                                                 if path.with_name(path.name.replace(".request.", ".response.")).exists() else None
                                                 for path in prior_requests]})
                    break
                if attempt + 1 == available:
                    raise ValueError("; ".join(violations[:8]))
                log("variable-source audit %s: batch %d rejected; retrying"
                    % (workbook, index))
                messages.extend(_correction_messages(text, violations))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError,
            json.JSONDecodeError, ValueError) as exc:
        failure = {
            "schema_version": 1,
            "generator_version": GENERATOR_VERSION,
            "prompt_version": PROMPT_VERSION,
            "row_id_resolution": True,
            "status": "failed",
            "attempt_scope": scope,
            "api_calls": new_api_calls,
            "replayed_responses": replayed_responses,
            "semantic_attempt_limit": semantic_attempt_limit,
            "workbook": workbook,
            "model": model,
            "endpoint": endpoint,
            "project_id": project_id,
            "request_config_sha256": _request_config_fingerprint(
                endpoint, model, project_id
            ),
            "inventory_sha256": inventory_hash,
            "error": str(exc),
            "generated_at": generated_at,
        }
        if isinstance(exc, urllib.error.HTTPError):
            failure["http_error"] = _http_error_details(exc)
        _atomic_text(metadata_path, json.dumps(failure, indent=2) + "\n")
        raise RuntimeError("variable-source audit failed: %s" % exc) from exc

    warning = (
        "This model-generated audit maps workbook-supplied values to plausible "
        "sources. It does not prove that any source supplied the exact value; "
        "review source claims before using the table as evidence."
    )
    sections = [
        "# `%s` — variable values and plausible external sources"
        % Path(artifact).name,
        "",
        "Generated by `%s` through the Labelbox LiteLLM proxy on %s."
        % (model, generated_at),
        "",
        "> **Audit warning:** %s" % warning,
        "",
    ]
    for index, table in enumerate(tables, 1):
        if len(tables) > 1:
            sections.extend(["## Inventory batch %d of %d"
                             % (index, len(tables)), ""])
        sections.extend([table, ""])
    markdown_text = "\n".join(sections).rstrip() + "\n"
    coverage = {"schema_version": "variable-source-audit-coverage/v1",
                "inventory_sha256": inventory_hash, "batches": coverage_batches}
    coverage_faults = validate_audit_coverage(markdown_text, inventory, coverage)
    if coverage_faults:
        raise RuntimeError("audit coverage failed: " + "; ".join(coverage_faults))
    _atomic_text(coverage_path, json.dumps(coverage, indent=2) + "\n")
    _atomic_text(markdown_path, markdown_text)

    metadata = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "row_id_resolution": True,
        "status": "complete",
        "attempt_scope": scope,
        "attempt_policy": _file_binding(attempt_dir / "policy.json"),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "workbook": workbook,
        "artifact": str(artifact),
        "model": model,
        "endpoint": endpoint,
        "project_id": project_id,
        "generated_at": generated_at,
        "inventory_sha256": inventory_hash,
        "inventory_rows": len(inventory["variables"]),
        "api_calls": new_api_calls,
            "replayed_responses": replayed_responses,
        "semantic_attempt_limit": semantic_attempt_limit,
        "request_config_sha256": _request_config_fingerprint(endpoint, model, project_id),
        "markdown_sha256": hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
        "coverage_path": str(coverage_path),
        "coverage_sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
        "finish_reasons": finishes,
        "validation": {
            "status": "passed",
            "checks": ["complete_inventory_dispositions", "row_id_citations", "qualified_cell_refs",
                       "workbook_value_numbers"],
        },
        "markdown": str(markdown_path),
        "inventory": str(inventory_path),
        "cache_hit": False,
    }
    _validate_saved_attempts(metadata, inventory, coverage, endpoint, model, project_id)
    _atomic_text(metadata_path, json.dumps(metadata, indent=2) + "\n")
    return metadata


def resolve_segmentation_directory(
        seg_root, workbook, *, segmentation_generation_id=None,
        source_generation_root=None, source_generation_id=None):
    """Resolve a pinned inactive generation, or the explicit legacy directory."""
    seg_dir = Path(seg_root) / workbook
    pinned = (
        segmentation_generation_id,
        source_generation_root,
        source_generation_id,
    )
    if not any(pinned):
        return seg_dir, None
    if not all(pinned):
        raise RuntimeError(
            "pinned audit requires segmentation generation ID, source "
            "generation root, and source generation ID"
        )

    from xl_seg.publication import resolve_generation_by_id
    from xl_source_publication import resolve_source_generation_by_id

    source_dir, source_manifest = resolve_source_generation_by_id(
        Path(source_generation_root) / workbook,
        source_generation_id,
    )
    generation_dir, generation_manifest = resolve_generation_by_id(
        seg_dir,
        segmentation_generation_id,
        source_generation_dir=source_dir,
        require_pass=True,
    )
    return generation_dir, {
        "generation_id": generation_manifest["generation_id"],
        "inventory_approval_sha256": (
            source_manifest.get("bindings") or {}
        ).get("inventory_approval_sha256"),
        "manifest_sha256": hashlib.sha256(
            (generation_dir / "generation-manifest.json").read_bytes()
        ).hexdigest(),
        "recalc_signals_sha256": (
            source_manifest.get("bindings") or {}
        ).get("recalc_signals_sha256"),
        "source_generation_id": source_manifest["generation_id"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate variable/source audit Markdown with GPT 5.6 Sol"
    )
    parser.add_argument("workbooks", nargs="+")
    parser.add_argument("--inputs-root", default="inputs_out")
    parser.add_argument("--seg-root", default="seg_out")
    parser.add_argument("--segmentation-generation-id")
    parser.add_argument("--source-generation-root")
    parser.add_argument("--source-generation-id")
    parser.add_argument("--audit-root", default="runs")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=PROD_ENDPOINT)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--inherited-budget", help="hash-bound receipt of prior actual audit request/response evidence")
    parser.add_argument("--inherited-budget-sha256")
    parser.add_argument("--semantic-attempt-limit", type=int, choices=(1, 2), default=2,
                        help="remaining attempts per batch; never restores attempts already consumed")
    parser.add_argument(
        "--inventory-only", action="store_true",
        help="write deterministic inventories without calling the model",
    )
    args = parser.parse_args(argv)

    if args.segmentation_generation_id and len(args.workbooks) != 1:
        parser.error("a pinned segmentation generation requires one workbook")

    if bool(args.inherited_budget) != bool(args.inherited_budget_sha256):
        parser.error("--inherited-budget and --inherited-budget-sha256 must be paired")
    inherited_budget = ({"path": str(Path(args.inherited_budget).resolve()), "sha256": args.inherited_budget_sha256}
                        if args.inherited_budget else None)
    api_key = "" if args.inventory_only else read_env_key(args.env_file)
    results = []
    for workbook in args.workbooks:
        artifact = resolve_workbook_artifact(
            args.inputs_root, workbook, "%s-inputs")
        output_dir = Path(args.audit_root) / ("%s-variable-sources" % workbook)
        try:
            seg_dir, segmentation = resolve_segmentation_directory(
                args.seg_root,
                workbook,
                segmentation_generation_id=args.segmentation_generation_id,
                source_generation_root=args.source_generation_root,
                source_generation_id=args.source_generation_id,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            parser.error(str(exc))
        if args.inventory_only:
            output_dir.mkdir(parents=True, exist_ok=True)
            inventory = build_inventory(
                workbook,
                artifact,
                seg_dir,
                segmentation=segmentation,
            )
            path = publish_inventory_only(workbook, inventory, output_dir)
            results.append({"workbook": workbook, "inventory": str(path),
                            "rows": len(inventory["variables"])})
        else:
            results.append(generate_audit(
                workbook, artifact, seg_dir, output_dir,
                api_key, model=args.model, endpoint=args.endpoint,
                project_id=args.project_id, refresh=args.refresh,
                semantic_attempt_limit=args.semantic_attempt_limit, inherited_budget=inherited_budget,
                segmentation=segmentation,
            ))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
