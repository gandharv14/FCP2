"""Deterministically remediate a closed set of volatile Excel constructs.

The tool edits OOXML formula/name spans only, preserves cached values and all
untouched package-member bytes, and fails closed when a volatile construct is
not mechanically proven safe.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict, deque
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    coordinate_to_tuple,
    range_boundaries,
)

from xl_source_health import (
    _formula_restriction_events,
    inspect_workbook,
    validate_ooxml_package,
)


PLAN_SCHEMA = "volatile-formula-remediation-plan/v1"
MANIFEST_SCHEMA = "volatile-formula-remediation-manifest/v1"
INSTALL_SCHEMA = "volatile-formula-remediation-install/v1"
TOOL_VERSION = "volatile-formula-remediation/v1"
MAX_ACTIONS = 100_000
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
VOLATILE_RE = re.compile(
    r"(?i)(?<![A-Z0-9_.])(?:_xlfn\.)?"
    r"(?:INDIRECT|OFFSET|TODAY|NOW|RAND|RANDBETWEEN|INFO|CELL)\s*\("
)
INDIRECT_RE = re.compile(
    r"""(?ix)
    (?:_xlfn\.)?INDIRECT\s*\(
      \s*(?P<sheet_cell>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*
      &\s*"!"\s*&\s*
      (?P<address_cell>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*
      (?:,\s*(?:TRUE|1)\s*)?
    \)
    """
)
OFFSET_BLANK_COLUMN_RE = re.compile(
    r"""(?ix)
    (?:_xlfn\.)?OFFSET\s*\(
      \s*(?P<reference>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*,\s*
      (?P<rows>\$?[A-Z]{1,3}\$?[1-9][0-9]*|[-+]?[0-9]+)\s*,\s*
    \)
    """
)
TODAY_RE = re.compile(r"(?i)^\s*(?:_xlfn\.)?TODAY\s*\(\s*\)\s*$")
CELL_BLOCK_RE = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?c\b(?![^>]*/>)[^>]*>"
    r".*?</(?:[A-Za-z_][\w.-]*:)?c>",
    re.DOTALL,
)
CELL_REF_ATTR_RE = re.compile(r'\br="([^"]+)"')
FORMULA_TAG_RE = re.compile(
    r"<(?P<prefix>[A-Za-z_][\w.-]*:)?f(?P<attrs>[^>]*)>"
    r"(?P<text>.*?)</(?P=prefix)?f>|"
    r"<(?P<self_prefix>[A-Za-z_][\w.-]*:)?f(?P<self_attrs>[^>]*)/>",
    re.DOTALL,
)
VALUE_TAG_RE = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?v(?:\s[^>]*)?>.*?</(?:[A-Za-z_][\w.-]*:)?v>",
    re.DOTALL,
)
DEFINED_NAME_RE = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?definedName\b[^>]*>.*?"
    r"</(?:[A-Za-z_][\w.-]*:)?definedName>",
    re.DOTALL,
)
NAME_TOKEN_RE = re.compile(r"(?i)(?<![A-Z0-9_.\\])([A-Z_\\][A-Z0-9_.\\]*)(?![A-Z0-9_.\\])")
A1_TARGET_RE = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^!]+))!"
    r"(?P<address>\$?[A-Z]{1,3}\$?[1-9][0-9]*"
    r"(?::\$?[A-Z]{1,3}\$?[1-9][0-9]*)?)$",
    re.IGNORECASE,
)


class RemediationError(ValueError):
    """The source cannot be remediated under the closed deterministic policy."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def object_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, value: bytes, *, exclusive: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise RemediationError(f"destination already exists: {path}")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and path.exists():
            raise RemediationError(f"destination already exists: {path}")
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    _atomic_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
            "utf-8"
        ),
        exclusive=exclusive,
    )


def _unsigned_hash(value: dict, field: str) -> str:
    unsigned = dict(value)
    unsigned.pop(field, None)
    return object_hash(unsigned)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemediationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RemediationError(f"JSON document must be an object: {path}")
    return value


def _package(path: Path) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    validate_ooxml_package(path)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        return infos, {info.filename: archive.read(info.filename) for info in infos}


def _sheet_parts(members: dict[str, bytes]) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(members["xl/workbook.xml"])
    relationships = ElementTree.fromstring(members["xl/_rels/workbook.xml.rels"])
    targets = {
        item.attrib.get("Id"): item.attrib.get("Target", "")
        for item in relationships.findall(f"{{{REL_NS}}}Relationship")
    }
    result = []
    for sheet in workbook.findall(".//{*}sheet"):
        relationship_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
        target = targets.get(relationship_id, "")
        if not target:
            raise RemediationError(f"missing worksheet relationship: {relationship_id}")
        part = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        if part not in members:
            raise RemediationError(f"missing worksheet part: {part}")
        result.append((sheet.attrib.get("name", ""), part))
    return result


def _cell_blocks(xml: str) -> dict[str, dict]:
    result = {}
    for match in CELL_BLOCK_RE.finditer(xml):
        block = match.group(0)
        open_end = block.find(">")
        reference = CELL_REF_ATTR_RE.search(block[: open_end + 1])
        if reference is None:
            continue
        coordinate = reference.group(1)
        formula = FORMULA_TAG_RE.search(block)
        value = VALUE_TAG_RE.search(block)
        result[coordinate] = {
            "start": match.start(),
            "end": match.end(),
            "block": block,
            "open_tag": block[: open_end + 1],
            "formula": formula,
            "value_xml": value.group(0) if value is not None else None,
        }
    return result


def _formula_record(formula: re.Match | None) -> dict | None:
    if formula is None:
        return None
    if formula.group("attrs") is not None:
        return {
            "prefix": formula.group("prefix") or "",
            "attrs": formula.group("attrs") or "",
            "text": html.unescape(formula.group("text") or ""),
            "self_closing": False,
            "start": formula.start(),
            "end": formula.end(),
        }
    return {
        "prefix": formula.group("self_prefix") or "",
        "attrs": formula.group("self_attrs") or "",
        "text": "",
        "self_closing": True,
        "start": formula.start(),
        "end": formula.end(),
    }


def _attrs(value: str) -> dict[str, str]:
    return dict(re.findall(r'([A-Za-z_:][\w:.-]*)="([^"]*)"', value))


def _shared_strings(members: dict[str, bytes]) -> list[str]:
    raw = members.get("xl/sharedStrings.xml")
    if raw is None:
        return []
    root = ElementTree.fromstring(raw)
    return ["".join(node.text or "" for node in item.findall(".//{*}t")) for item in root.findall(".//{*}si")]


def _static_cell_values(
    sheet_xml: dict[str, str],
    shared_strings: list[str],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for sheet_name, xml in sheet_xml.items():
        values = {}
        for coordinate, record in _cell_blocks(xml).items():
            if record["formula"] is not None:
                continue
            value_match = VALUE_TAG_RE.search(record["block"])
            if value_match is None:
                continue
            value_element = ElementTree.fromstring(value_match.group(0))
            value = value_element.text or ""
            cell_type = _attrs(record["open_tag"]).get("t")
            if cell_type == "s":
                try:
                    value = shared_strings[int(value)]
                except (IndexError, ValueError) as exc:
                    raise RemediationError(
                        f"invalid shared string at {sheet_name}!{coordinate}"
                    ) from exc
            elif cell_type == "inlineStr":
                cell_element = ElementTree.fromstring(record["block"])
                value = "".join(node.text or "" for node in cell_element.findall(".//{*}t"))
            values[coordinate.replace("$", "").upper()] = value
        result[sheet_name] = values
    return result


def _expanded_formulas(
    sheet_xml: dict[str, str],
) -> dict[tuple[str, str], dict]:
    result = {}
    for sheet_name, xml in sheet_xml.items():
        cells = _cell_blocks(xml)
        masters: dict[str, list[tuple[str, str, tuple[int, int, int, int] | None]]] = (
            defaultdict(list)
        )
        for coordinate, cell in cells.items():
            formula = _formula_record(cell["formula"])
            if formula is None:
                continue
            attributes = _attrs(formula["attrs"])
            if (
                attributes.get("t") == "shared"
                and attributes.get("si")
                and formula["text"].strip()
            ):
                reference = attributes.get("ref")
                try:
                    bounds = range_boundaries(reference) if reference else None
                except ValueError:
                    bounds = None
                masters[attributes["si"]].append(
                    (coordinate, formula["text"], bounds)
                )
        for coordinate, cell in cells.items():
            formula = _formula_record(cell["formula"])
            if formula is None:
                continue
            attributes = _attrs(formula["attrs"])
            text = formula["text"]
            expanded = text
            if attributes.get("t") == "shared" and not text.strip():
                candidates = masters.get(attributes.get("si"), [])
                row, column = coordinate_to_tuple(coordinate)
                containing = [
                    item for item in candidates
                    if item[2] is not None
                    and item[2][0] <= column <= item[2][2]
                    and item[2][1] <= row <= item[2][3]
                ]
                master = (
                    containing[0]
                    if len(containing) == 1
                    else candidates[0]
                    if len(candidates) == 1
                    else None
                )
                if master is not None and VOLATILE_RE.search(master[1]):
                    try:
                        expanded = Translator(
                            "=" + master[1].lstrip("="), origin=master[0]
                        ).translate_formula(coordinate)[1:]
                    except TranslatorError as exc:
                        raise RemediationError(
                            f"volatile shared formula cannot expand at "
                            f"{sheet_name}!{coordinate}"
                        ) from exc
                else:
                    # Unrelated shared groups can legally reference a master in
                    # another worksheet XML fragment after producer-specific
                    # serialization. They are not inputs to this remediator.
                    expanded = ""
            result[(sheet_name, coordinate)] = {
                **formula,
                "attributes": attributes,
                "expanded": expanded,
            }
    return result


def _name_tokens(value: str, available: set[str]) -> set[str]:
    # Excel string literals do not contain executable name references.
    code = re.sub(r'"(?:[^"]|"")*"', " ", value)
    return {
        match.group(1).casefold()
        for match in NAME_TOKEN_RE.finditer(code)
        if match.group(1).casefold() in available
    }


def _defined_name_plan(
    workbook_xml: bytes,
    external_formula_texts: list[str],
) -> tuple[list[dict], list[dict]]:
    root = ElementTree.fromstring(workbook_xml)
    elements = root.findall(".//{*}definedName")
    records = []
    for index, element in enumerate(elements):
        records.append({
            "index": index,
            "name": element.attrib.get("name", ""),
            "local_sheet_id": element.attrib.get("localSheetId"),
            "text": element.text or "",
        })
    available = {record["name"].casefold() for record in records if record["name"]}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_name[record["name"].casefold()].append(record)

    live = {
        name for name in available
        if name.startswith("_xlnm.")
    }
    for formula in external_formula_texts:
        live.update(_name_tokens(formula, available))

    queue = deque(sorted(live))
    while queue:
        name = queue.popleft()
        for record in by_name.get(name, []):
            for dependency in _name_tokens(record["text"], available):
                if dependency not in live:
                    live.add(dependency)
                    queue.append(dependency)

    removals = []
    unresolved = []
    raw_text = workbook_xml.decode("utf-8")
    raw_matches = list(DEFINED_NAME_RE.finditer(raw_text))
    if len(raw_matches) != len(records):
        raise RemediationError(
            "defined-name XML spans do not match parsed defined-name records"
        )
    for record, raw_match in zip(records, raw_matches):
        events = _formula_restriction_events(
            record["text"],
            scope="defined_name",
            location=f"DEFINED_NAME:{record['name']}",
        )
        restricted = any(event.get("allowed") is not True for event in events)
        broken = "#REF!" in record["text"].upper()
        if not (restricted or broken):
            continue
        item = {
            "kind": "remove_dead_defined_name",
            "part": "xl/workbook.xml",
            "index": record["index"],
            "name": record["name"],
            "local_sheet_id": record["local_sheet_id"],
            "before_sha256": sha256_bytes(raw_match.group(0).encode("utf-8")),
            "rule_id": "dead-defined-name/v1",
        }
        if record["name"].casefold() in live:
            if restricted:
                unresolved.append({
                    "kind": "live_restricted_defined_name",
                    "name": record["name"],
                    "local_sheet_id": record["local_sheet_id"],
                    "formula_sha256": sha256_bytes(record["text"].encode("utf-8")),
                })
        else:
            removals.append(item)
    return removals, unresolved


def _formula_texts_from_other_parts(
    members: dict[str, bytes],
    worksheet_parts: set[str],
) -> list[str]:
    values = []
    formula_element = re.compile(
        r"<(?:[A-Za-z_][\w.-]*:)?(?:f|formula|formula1|formula2)\b[^>]*>"
        r"(.*?)</(?:[A-Za-z_][\w.-]*:)?(?:f|formula|formula1|formula2)>",
        re.DOTALL | re.IGNORECASE,
    )
    for name, raw in members.items():
        if name == "xl/workbook.xml" or name in worksheet_parts:
            continue
        if not name.endswith(".xml"):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        values.extend(html.unescape(match.group(1)) for match in formula_element.finditer(text))
    return values


def _cell_reference_used(
    sheet_name: str,
    coordinate: str,
    current_key: tuple[str, str],
    expanded: dict[tuple[str, str], dict],
    defined_name_texts: list[str],
    other_formulas: list[str],
) -> bool:
    column, row = coordinate_from_string(coordinate.replace("$", ""))
    cell = rf"\$?{re.escape(column)}\$?{row}(?![0-9])"
    local = re.compile(rf"(?i)(?<![A-Z0-9_.]){cell}")
    quoted_sheet = re.escape("'" + sheet_name.replace("'", "''") + "'")
    plain_sheet = re.escape(sheet_name)
    qualified = re.compile(
        rf"(?i)(?:{quoted_sheet}|{plain_sheet})!{cell}"
    )
    for key, formula in expanded.items():
        if key == current_key:
            continue
        text = formula["expanded"]
        if qualified.search(text) or (key[0] == sheet_name and local.search(text)):
            return True
    return any(qualified.search(text) for text in defined_name_texts + other_formulas)


def _resolve_indirect(
    formula: str,
    sheet_name: str,
    static_values: dict[str, dict[str, str]],
    sheet_names: set[str],
) -> tuple[str, int]:
    substitutions = 0

    def replace(match: re.Match) -> str:
        nonlocal substitutions
        sheet_cell = match.group("sheet_cell").replace("$", "").upper()
        address_cell = match.group("address_cell").replace("$", "").upper()
        values = static_values.get(sheet_name, {})
        if sheet_cell not in values or address_cell not in values:
            raise RemediationError(
                f"INDIRECT operands are not static in {sheet_name}: "
                f"{sheet_cell}, {address_cell}"
            )
        target = f"{values[sheet_cell]}!{values[address_cell]}"
        target_match = A1_TARGET_RE.fullmatch(target)
        if target_match is None:
            raise RemediationError(f"INDIRECT target is not internal A1: {target!r}")
        target_sheet = (
            (target_match.group("quoted") or "").replace("''", "'")
            if target_match.group("quoted") is not None
            else (target_match.group("plain") or "")
        )
        if target_sheet not in sheet_names:
            raise RemediationError(f"INDIRECT target sheet is missing: {target_sheet}")
        address = target_match.group("address")
        start = address.split(":", 1)[0].replace("$", "")
        column, row = coordinate_from_string(start)
        if not (1 <= column_index_from_string(column) <= 16_384 and 1 <= row <= 1_048_576):
            raise RemediationError(f"INDIRECT target is out of bounds: {target!r}")
        substitutions += 1
        return f"'{target_sheet.replace(chr(39), chr(39) * 2)}'!{address}"

    rewritten = INDIRECT_RE.sub(replace, formula)
    return rewritten, substitutions


def _rewrite_offset(formula: str) -> tuple[str, int]:
    substitutions = 0

    def replace(match: re.Match) -> str:
        nonlocal substitutions
        reference = match.group("reference")
        rows = match.group("rows")
        column = re.match(r"(\$?)[A-Z]{1,3}", reference, re.IGNORECASE)
        if column is None:
            raise RemediationError(f"OFFSET reference is not scalar A1: {reference}")
        column_ref = column.group(0)
        substitutions += 1
        return (
            f"INDEX({column_ref}:{column_ref},"
            f"ROW({reference})+({rows}))"
        )

    rewritten = OFFSET_BLANK_COLUMN_RE.sub(replace, formula)
    return rewritten, substitutions


def build_plan(source_path: str | Path) -> dict:
    source = Path(source_path).resolve()
    if not source.is_file() or source.is_symlink() or source.suffix.casefold() != ".xlsx":
        raise RemediationError("source must be a regular non-symlink .xlsx file")
    infos, members = _package(source)
    sheets = _sheet_parts(members)
    sheet_xml = {
        sheet_name: members[part].decode("utf-8")
        for sheet_name, part in sheets
    }
    part_by_sheet = dict(sheets)
    expanded = _expanded_formulas(sheet_xml)
    static_values = _static_cell_values(sheet_xml, _shared_strings(members))
    sheet_names = {sheet_name for sheet_name, _ in sheets}
    workbook_root = ElementTree.fromstring(members["xl/workbook.xml"])
    defined_name_texts = [
        item.text or "" for item in workbook_root.findall(".//{*}definedName")
    ]
    other_formulas = _formula_texts_from_other_parts(
        members, {part for _, part in sheets}
    )
    formula_texts = [
        record["expanded"] for record in expanded.values()
    ] + other_formulas
    removals, unresolved_names = _defined_name_plan(
        members["xl/workbook.xml"], formula_texts
    )
    actions = list(removals)
    unresolved = list(unresolved_names)

    indirect_groups = {
        key for key, record in expanded.items()
        if re.search(r"(?i)(?:_xlfn\.)?INDIRECT\s*\(", record["expanded"])
    }
    for key, record in sorted(expanded.items()):
        sheet_name, coordinate = key
        formula = record["expanded"]
        if key in indirect_groups:
            try:
                rewritten, substitutions = _resolve_indirect(
                    formula, sheet_name, static_values, sheet_names
                )
            except RemediationError as exc:
                unresolved.append({
                    "kind": "indirect_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": str(exc),
                    "formula_sha256": sha256_bytes(formula.encode("utf-8")),
                })
                continue
            if substitutions == 0 or re.search(
                r"(?i)(?:_xlfn\.)?INDIRECT\s*\(", rewritten
            ):
                unresolved.append({
                    "kind": "indirect_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "unsupported INDIRECT shape remains",
                    "formula_sha256": sha256_bytes(formula.encode("utf-8")),
                })
                continue
            actions.append({
                "kind": "replace_formula",
                "rule_id": "indirect-static-concat-to-a1/v1",
                "part": part_by_sheet[sheet_name],
                "sheet": sheet_name,
                "coordinate": coordinate,
                "raw_formula_sha256": sha256_bytes(record["text"].encode("utf-8")),
                "expanded_formula_sha256": sha256_bytes(formula.encode("utf-8")),
                "after_formula": rewritten,
                "after_formula_sha256": sha256_bytes(rewritten.encode("utf-8")),
                "make_ordinary": True,
                "substitutions": substitutions,
            })
            continue

        raw_formula = record["text"]
        if not raw_formula.strip():
            continue
        if re.search(r"(?i)(?:_xlfn\.)?OFFSET\s*\(", raw_formula):
            rewritten, substitutions = _rewrite_offset(raw_formula)
            if substitutions == 0 or re.search(
                r"(?i)(?:_xlfn\.)?OFFSET\s*\(", rewritten
            ):
                unresolved.append({
                    "kind": "offset_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "only scalar OFFSET(reference, rows, blank-column) is eligible",
                    "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                })
                continue
            actions.append({
                "kind": "replace_formula",
                "rule_id": "offset-blank-column-to-index/v1",
                "part": part_by_sheet[sheet_name],
                "sheet": sheet_name,
                "coordinate": coordinate,
                "raw_formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                "expanded_formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                "after_formula": rewritten,
                "after_formula_sha256": sha256_bytes(rewritten.encode("utf-8")),
                "make_ordinary": False,
                "substitutions": substitutions,
            })
            continue

        if TODAY_RE.fullmatch(raw_formula):
            if record["attributes"].get("t") in {"shared", "array", "dataTable"}:
                unresolved.append({
                    "kind": "today_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "TODAY formula is not ordinary",
                    "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                })
                continue
            cell = _cell_blocks(sheet_xml[sheet_name])[coordinate]
            if cell["value_xml"] is None:
                unresolved.append({
                    "kind": "today_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "TODAY formula has no cached as-of date",
                    "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                })
                continue
            if _cell_reference_used(
                sheet_name,
                coordinate,
                key,
                expanded,
                defined_name_texts,
                other_formulas,
            ):
                unresolved.append({
                    "kind": "today_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "TODAY cell has live dependents",
                    "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                })
                continue
            actions.append({
                "kind": "freeze_cached_today",
                "rule_id": "unreferenced-today-to-as-of-constant/v1",
                "part": part_by_sheet[sheet_name],
                "sheet": sheet_name,
                "coordinate": coordinate,
                "raw_formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                "cached_value_sha256": sha256_bytes(
                    cell["value_xml"].encode("utf-8")
                ),
            })
            continue

        if VOLATILE_RE.search(raw_formula):
            unresolved.append({
                "kind": "volatile_formula_unresolved",
                "location": f"{sheet_name}!{coordinate}",
                "reason": "volatile formula is outside the closed rewrite taxonomy",
                "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
            })

    actions.sort(
        key=lambda item: (
            item["part"],
            item.get("sheet", ""),
            item.get("coordinate", ""),
            item.get("index", -1),
            item["kind"],
        )
    )
    unresolved.sort(key=lambda item: canonical_bytes(item))
    if len(actions) > MAX_ACTIONS:
        unresolved.append({
            "kind": "budget_exceeded",
            "actions": len(actions),
            "maximum": MAX_ACTIONS,
        })
    before_health = inspect_workbook(source)
    counts = Counter(action["rule_id"] for action in actions)
    plan = {
        "schema_version": PLAN_SCHEMA,
        "tool_version": TOOL_VERSION,
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "size_bytes": source.stat().st_size,
        },
        "before_health": {
            "route": before_health["route"],
            "reason_codes": before_health["reason_codes"],
            "report_sha256": before_health["report_sha256"],
        },
        "actions": actions,
        "action_counts": dict(sorted(counts.items())),
        "unresolved": unresolved,
        "status": "eligible" if actions and not unresolved else "ineligible",
    }
    return {**plan, "plan_sha256": _unsigned_hash(plan, "plan_sha256")}


def validate_plan(plan: dict, source_path: str | Path) -> dict:
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("plan_sha256") != _unsigned_hash(plan, "plan_sha256")
    ):
        raise RemediationError("remediation plan is invalid or tampered")
    source = Path(source_path).resolve()
    if (
        plan.get("source", {}).get("path") != str(source)
        or plan["source"].get("sha256") != sha256_file(source)
        or plan["source"].get("size_bytes") != source.stat().st_size
    ):
        raise RemediationError("remediation plan source binding changed")
    fresh = build_plan(source)
    if fresh != plan:
        raise RemediationError("remediation plan does not match fresh analysis")
    if plan["status"] != "eligible" or plan["unresolved"]:
        raise RemediationError("remediation plan is not eligible")
    return plan


def _rewrite_defined_names(xml: str, actions: list[dict]) -> str:
    matches = list(DEFINED_NAME_RE.finditer(xml))
    replacements = []
    for action in actions:
        index = action["index"]
        if not (0 <= index < len(matches)):
            raise RemediationError("defined-name action index is stale")
        match = matches[index]
        raw = match.group(0)
        if sha256_bytes(raw.encode("utf-8")) != action["before_sha256"]:
            raise RemediationError("defined-name action hash is stale")
        replacements.append((match.start(), match.end(), ""))
    for start, end, replacement in sorted(replacements, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    return xml


def _formula_tag(formula: dict, after: str, make_ordinary: bool) -> str:
    attributes = formula["attrs"]
    if make_ordinary:
        attributes = re.sub(r'\s+(?:t|si|ref)="[^"]*"', "", attributes)
    prefix = formula["prefix"]
    return f"<{prefix}f{attributes}>{escape(after)}</{prefix}f>"


def _rewrite_sheet(xml: str, actions: list[dict]) -> str:
    cells = _cell_blocks(xml)
    replacements = []
    for action in actions:
        coordinate = action["coordinate"]
        cell = cells.get(coordinate)
        if cell is None:
            raise RemediationError(f"formula cell disappeared: {coordinate}")
        formula = _formula_record(cell["formula"])
        if formula is None:
            raise RemediationError(f"formula disappeared: {coordinate}")
        if sha256_bytes(formula["text"].encode("utf-8")) != action["raw_formula_sha256"]:
            raise RemediationError(f"formula action is stale: {coordinate}")
        block = cell["block"]
        if action["kind"] == "freeze_cached_today":
            updated = block[: formula["start"]] + block[formula["end"] :]
        else:
            replacement = _formula_tag(
                formula,
                action["after_formula"],
                action["make_ordinary"],
            )
            updated = block[: formula["start"]] + replacement + block[formula["end"] :]
        replacements.append((cell["start"], cell["end"], updated))
    for start, end, replacement in sorted(replacements, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    return xml


def _value_ledger(members: dict[str, bytes]) -> dict[str, str | None]:
    result = {}
    for sheet_name, part in _sheet_parts(members):
        xml = members[part].decode("utf-8")
        for coordinate, cell in _cell_blocks(xml).items():
            result[f"{sheet_name}!{coordinate}"] = cell["value_xml"]
    return result


def _write_package(
    infos: list[zipfile.ZipInfo],
    members: dict[str, bytes],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(temporary_path, "w") as archive:
            for info in infos:
                archive.writestr(copy.copy(info), members[info.filename])
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        if output.exists():
            raise RemediationError(f"output already exists: {output}")
        os.replace(temporary_path, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def apply_plan(
    source_path: str | Path,
    plan: dict,
    output_path: str | Path,
) -> dict:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    validate_plan(plan, source)
    if output.exists():
        raise RemediationError(f"output already exists: {output}")
    infos, before = _package(source)
    after = dict(before)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for action in plan["actions"]:
        grouped[action["part"]].append(action)
    changed = {}
    for part, actions in sorted(grouped.items()):
        original = before[part]
        xml = original.decode("utf-8")
        if part == "xl/workbook.xml":
            updated = _rewrite_defined_names(xml, actions)
        else:
            updated = _rewrite_sheet(xml, actions)
        after[part] = updated.encode("utf-8")
        if after[part] != original:
            changed[part] = {
                "before_sha256": sha256_bytes(original),
                "after_sha256": sha256_bytes(after[part]),
            }
    if not changed:
        raise RemediationError("eligible plan produced no changed OOXML members")
    if _value_ledger(before) != _value_ledger(after):
        raise RemediationError("formula cache/value ledger changed during remediation")
    _write_package(infos, after, output)
    try:
        after_health = inspect_workbook(output)
        remaining = [
            event for event in after_health.get("restriction_events", [])
            if event.get("allowed") is not True
            and event.get("event") in {
                "volatile_function",
                "external_workbook_reference",
                "package_external_link_part",
                "package_external_link_relationship",
            }
        ]
        if remaining:
            raise RemediationError(
                "remediation left unsupported volatile or external references"
            )
        if after_health["route"] in {"unsupported", "insufficient_evidence"}:
            raise RemediationError(
                f"remediated source route is {after_health['route']}: "
                f"{after_health['reason_codes']}"
            )
        untouched = sorted(set(before) - set(changed))
        if any(before[name] != after[name] for name in untouched):
            raise RemediationError("untouched OOXML member changed")
        action_evidence = [
            {
                key: action[key]
                for key in (
                    "kind",
                    "rule_id",
                    "part",
                    "sheet",
                    "coordinate",
                    "name",
                    "local_sheet_id",
                    "before_sha256",
                    "raw_formula_sha256",
                    "expanded_formula_sha256",
                    "after_formula_sha256",
                    "cached_value_sha256",
                    "substitutions",
                )
                if key in action
            }
            for action in plan["actions"]
        ]
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "tool_version": TOOL_VERSION,
            "plan_sha256": plan["plan_sha256"],
            "source": {
                "path": str(source),
                "sha256": sha256_file(source),
                "size_bytes": source.stat().st_size,
            },
            "output": {
                "path": str(output),
                "sha256": sha256_file(output),
                "size_bytes": output.stat().st_size,
            },
            "before_health": plan["before_health"],
            "after_health": {
                "route": after_health["route"],
                "reason_codes": after_health["reason_codes"],
                "report_sha256": after_health["report_sha256"],
            },
            "actions": action_evidence,
            "action_counts": plan["action_counts"],
            "changed_members": changed,
            "member_count": len(before),
            "untouched_member_count": len(untouched),
            "untouched_members_sha256": object_hash({
                name: sha256_bytes(before[name]) for name in untouched
            }),
            "value_ledger_sha256": object_hash(_value_ledger(before)),
        }
        return {
            **manifest,
            "manifest_sha256": _unsigned_hash(manifest, "manifest_sha256"),
        }
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def verify_manifest(
    source_path: str | Path,
    output_path: str | Path,
    plan: dict,
    manifest: dict,
) -> dict:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    validate_plan(plan, source)
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("tool_version") != TOOL_VERSION
        or manifest.get("manifest_sha256")
        != _unsigned_hash(manifest, "manifest_sha256")
    ):
        raise RemediationError("remediation manifest is invalid or tampered")
    if (
        manifest.get("plan_sha256") != plan["plan_sha256"]
        or manifest.get("source", {}).get("sha256") != sha256_file(source)
        or manifest.get("output", {}).get("path") != str(output)
        or manifest.get("output", {}).get("sha256") != sha256_file(output)
    ):
        raise RemediationError("remediation manifest bindings changed")
    _, before = _package(source)
    _, after = _package(output)
    if set(before) != set(after):
        raise RemediationError("OOXML member set changed")
    if _value_ledger(before) != _value_ledger(after):
        raise RemediationError("formula cache/value ledger changed")
    changed = {
        name: {
            "before_sha256": sha256_bytes(before[name]),
            "after_sha256": sha256_bytes(after[name]),
        }
        for name in before if before[name] != after[name]
    }
    if changed != manifest["changed_members"]:
        raise RemediationError("changed OOXML member proof does not match")
    health = inspect_workbook(output)
    if (
        health["route"] != manifest["after_health"]["route"]
        or health["reason_codes"] != manifest["after_health"]["reason_codes"]
        or health["report_sha256"] != manifest["after_health"]["report_sha256"]
    ):
        raise RemediationError("fresh output health does not match manifest")
    return {
        "status": "verified",
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(output),
        "manifest_sha256": manifest["manifest_sha256"],
        "route": health["route"],
        "reason_codes": health["reason_codes"],
    }


def install_candidate(
    source_path: str | Path,
    candidate_path: str | Path,
    plan: dict,
    manifest: dict,
    backup_root: str | Path,
) -> dict:
    source = Path(source_path).resolve()
    candidate = Path(candidate_path).resolve()
    verification = verify_manifest(source, candidate, plan, manifest)
    backup = (
        Path(backup_root).resolve()
        / source.stem
        / f"{verification['source_sha256']}.xlsx"
    )
    if backup.exists():
        if sha256_file(backup) != verification["source_sha256"]:
            raise RemediationError("existing backup has the wrong hash")
    else:
        backup.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{backup.name}.", suffix=".tmp", dir=backup.parent
        )
        temporary_path = Path(temporary)
        try:
            with source.open("rb") as source_handle, os.fdopen(
                descriptor, "wb"
            ) as target:
                shutil.copyfileobj(source_handle, target)
                target.flush()
                os.fsync(target.fileno())
            if sha256_file(temporary_path) != verification["source_sha256"]:
                raise RemediationError("backup copy hash mismatch")
            os.replace(temporary_path, backup)
        finally:
            temporary_path.unlink(missing_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{source.name}.", suffix=".tmp", dir=source.parent
    )
    temporary_path = Path(temporary)
    try:
        with candidate.open("rb") as candidate_handle, os.fdopen(
            descriptor, "wb"
        ) as target:
            shutil.copyfileobj(candidate_handle, target)
            target.flush()
            os.fsync(target.fileno())
        if sha256_file(temporary_path) != verification["output_sha256"]:
            raise RemediationError("install copy hash mismatch")
        os.chmod(temporary_path, source.stat().st_mode & 0o777)
        os.replace(temporary_path, source)
        directory = os.open(source.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)
    if sha256_file(source) != verification["output_sha256"]:
        raise RemediationError("installed source hash mismatch")
    receipt = {
        "schema_version": INSTALL_SCHEMA,
        "tool_version": TOOL_VERSION,
        "source_path": str(source),
        "original_sha256": verification["source_sha256"],
        "installed_sha256": verification["output_sha256"],
        "backup_path": str(backup),
        "manifest_sha256": verification["manifest_sha256"],
        "route": verification["route"],
        "reason_codes": verification["reason_codes"],
    }
    return {
        **receipt,
        "receipt_sha256": _unsigned_hash(receipt, "receipt_sha256"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument("source")
    plan_command.add_argument("-o", "--output", required=True)
    apply_command = commands.add_parser("apply")
    apply_command.add_argument("source")
    apply_command.add_argument("--plan", required=True)
    apply_command.add_argument("--output", required=True)
    apply_command.add_argument("--manifest", required=True)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("source")
    verify_command.add_argument("candidate")
    verify_command.add_argument("--plan", required=True)
    verify_command.add_argument("--manifest", required=True)
    install_command = commands.add_parser("install")
    install_command.add_argument("source")
    install_command.add_argument("candidate")
    install_command.add_argument("--plan", required=True)
    install_command.add_argument("--manifest", required=True)
    install_command.add_argument("--backup-root", required=True)
    install_command.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(args.source)
            atomic_json(Path(args.output), plan)
            print(json.dumps({
                "status": plan["status"],
                "actions": len(plan["actions"]),
                "unresolved": len(plan["unresolved"]),
                "plan_sha256": plan["plan_sha256"],
            }, sort_keys=True))
            return 0 if plan["status"] == "eligible" else 2
        plan = _read_json(Path(args.plan))
        if args.command == "apply":
            manifest = apply_plan(args.source, plan, args.output)
            atomic_json(Path(args.manifest), manifest, exclusive=True)
            print(json.dumps({
                "status": "applied",
                "output_sha256": manifest["output"]["sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
                "route": manifest["after_health"]["route"],
            }, sort_keys=True))
            return 0
        manifest = _read_json(Path(args.manifest))
        if args.command == "verify":
            print(json.dumps(
                verify_manifest(args.source, args.candidate, plan, manifest),
                sort_keys=True,
            ))
            return 0
        receipt = install_candidate(
            args.source,
            args.candidate,
            plan,
            manifest,
            args.backup_root,
        )
        atomic_json(Path(args.receipt), receipt, exclusive=True)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (OSError, RemediationError, zipfile.BadZipFile) as exc:
        print(f"volatile remediation FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
