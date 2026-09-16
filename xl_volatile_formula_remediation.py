"""Deterministically remediate a closed set of volatile Excel constructs.

The tool edits proved OOXML formula/name spans and unused external-book graphs,
preserves cached values and all untouched package-member bytes, and fails closed
when a construct is not mechanically proven safe.
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
    get_column_letter,
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
TOOL_VERSION = "volatile-formula-remediation/v14"
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
    (?<![A-Z0-9_.])(?:_xlfn\.)?OFFSET\s*\(
      \s*(?P<qualifier>(?:\x27(?:[^\x27\[\]]|\x27\x27)+\x27|[A-Z_][A-Z0-9_.]*)!)?(?P<reference>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*,\s*
      (?P<rows>\$?[A-Z]{1,3}\$?[1-9][0-9]*|[-+]?[0-9]+|[A-Z_][A-Z0-9_.]*)\s*,\s*(?P<zero_column>0)?\s*
    \)
    """
)
OFFSET_IDENTITY_RE = re.compile(
    r"(?ix)(?<![A-Z0-9_.])(?:_xlfn\.)?OFFSET\s*\(\s*"
    r"(?P<reference>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*,\s*0\s*,\s*0\s*\)"
)
OFFSET_CONSTANT_RE = re.compile(
    r"(?ix)(?<![A-Z0-9_.])(?:_xlfn\.)?OFFSET\s*\(\s*"
    r"(?P<reference>(?P<column_absolute>\$?)(?P<column>[A-Z]{1,3})"
    r"(?P<row_absolute>\$?)(?P<row>[1-9][0-9]*))\s*,\s*"
    r"(?P<rows>[-+]?[0-9]+)\s*,\s*(?P<columns>[-+]?[0-9]+)\s*\)"
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
            cell_type = _attrs(record["open_tag"]).get("t")
            if value_match is None and cell_type != "inlineStr":
                continue
            value = (ElementTree.fromstring(value_match.group(0)).text or "") if value_match else ""
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


def _name_tokens(value: str, available: set[str], *, literal_builtin_names=frozenset()) -> set[str]:
    # Excel string literals do not contain executable name references.
    code = re.sub(r'"(?:[^"]|"")*"', " ", value)
    # A quoted worksheet qualifier is not a defined name. Keep the operand
    # after !, which can itself be a legitimate sheet-scoped defined name.
    code = re.sub(r"'(?:[^']|'')*'\s*!", " !", code)
    # The old token regex split $B$17 into B and 17, making an unrelated
    # defined name B appear live. Mask only complete in-bounds A1 addresses;
    # out-of-grid identifiers can be legitimate defined names.
    def cell_reference(match):
        column, row = match.group(1), int(match.group(2))
        if column_index_from_string(column.upper()) <= 16384 and row <= 1048576:
            return " "
        return match.group(0)
    code = re.sub(
        r"(?i)(?<![A-Z0-9_.\\$])\$?([A-Z]{1,3})\$?([1-9][0-9]*)(?![A-Z0-9_.\\$])",
        cell_reference, code,
    )
    return {
        match.group(1).casefold()
        for match in NAME_TOKEN_RE.finditer(code)
        if match.group(1).casefold() in available
        and not (match.group(1).casefold() in literal_builtin_names
                 and re.match(r'\s*\(', code[match.end():])
                 and not code[:match.start()].rstrip().endswith('!'))
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

    # Built-in print settings are live presentation metadata, but an error
    # literal cannot describe a print region. Only remove these broken entries
    # when no computation anywhere refers to the name, including other names.
    explicitly_referenced = set()
    all_formulas = [*external_formula_texts, *(r['text'] for r in records)]
    # A workbook-global external print area cannot select a local sheet's
    # printable cells. Remove only literal rectangles, with no possible
    # computational name use. Local print settings remain untouched.
    dynamic_names = any(re.search(r'(?i)(?:INDIRECT|OFFSET|EVALUATE|GET\.CELL)\s*\(', f)
                        for f in all_formulas)
    # YEAR(date) invokes a built-in, whereas YEAR or Sheet!YEAR reads a name.
    # Limit disambiguation to global names containing a literal external cell;
    # callable/LAMBDA names, local scopes and dynamic resolution stay live.
    from openpyxl.utils import FORMULAE
    literal_builtin_names = set()
    if not dynamic_names:
        for name, definitions in by_name.items():
            if name.upper() in FORMULAE and all(
                    r['local_sheet_id'] is None and re.fullmatch(
                        r"(?:\[[1-9][0-9]*\][A-Za-z_][A-Za-z0-9_ .]*|'\[[1-9][0-9]*\](?:[^']|'')+')!"
                        r"\$?[A-Z]{1,3}\$?[1-9][0-9]*", r['text'].strip())
                    for r in definitions):
                literal_builtin_names.add(name)
    def references(formula):
        return _name_tokens(formula, available, literal_builtin_names=literal_builtin_names)
    for formula in all_formulas:
        explicitly_referenced.update(references(formula))

    live = {
        name for name in available
        if name.startswith("_xlnm.")
    }
    for formula in external_formula_texts:
        live.update(references(formula))

    queue = deque(sorted(live))
    while queue:
        name = queue.popleft()
        for record in by_name.get(name, []):
            for dependency in references(record["text"]):
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
        external_print = re.fullmatch(
            r"(?:\[[1-9][0-9]*\][A-Za-z_][A-Za-z0-9_ .]*|'\[[1-9][0-9]*\](?:[^']|'')+')!"
            r"\$([A-Z]{1,3})\$([1-9][0-9]*):\$([A-Z]{1,3})\$([1-9][0-9]*)",
            record['text'].strip())
        if (record['name'].casefold() == '_xlnm.print_area'
                and record['local_sheet_id'] is None and external_print
                and not dynamic_names and record['name'].casefold() not in explicitly_referenced
                and all(column_index_from_string(external_print.group(i)) <= 16384 for i in (1, 3))
                and all(int(external_print.group(i)) <= 1048576 for i in (2, 4))):
            removals.append({
                'kind': 'remove_dead_defined_name', 'part': 'xl/workbook.xml',
                'index': record['index'], 'name': record['name'],
                'local_sheet_id': None,
                'before_sha256': sha256_bytes(raw_match.group(0).encode('utf-8')),
                'rule_id': 'unreferenced-global-external-print-area/v1',
            })
            continue
        if (record['name'].casefold() in {'_xlnm.print_area', '_xlnm.print_titles'}
                and record['text'].strip().upper() in {'#REF!', '#N/A'}
                and record['name'].casefold() not in explicitly_referenced):
            removals.append({
                'kind': 'remove_dead_defined_name', 'part': 'xl/workbook.xml',
                'index': record['index'], 'name': record['name'],
                'local_sheet_id': record['local_sheet_id'],
                'before_sha256': sha256_bytes(raw_match.group(0).encode('utf-8')),
                'rule_id': 'unreferenced-error-print-setting/v1',
            })
            continue
        events = _formula_restriction_events(
            record["text"],
            scope="defined_name",
            location=f"DEFINED_NAME:{record['name']}",
        )
        restricted = any(event.get("allowed") is not True for event in events)
        false_external = any(event.get("event") == "false_external_detection" for event in events)
        broken = "#REF!" in record["text"].upper()
        if not (restricted or broken or false_external):
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
    for name, raw in members.items():
        if name == "xl/workbook.xml":
            continue
        if not name.endswith(".xml"):
            continue
        # A regex opening at <f .../> can swallow several cells up to the next
        # </f>, making XML element names appear to be live defined names.
        root = ElementTree.fromstring(raw)
        values.extend(node.text or '' for node in root.iter()
                      if node.tag.rsplit('}', 1)[-1].casefold() in {'f', 'formula', 'formula1', 'formula2'})
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

    def resolved(target):
        nonlocal substitutions
        if "!" not in target:
            target = "'" + sheet_name.replace("'", "''") + "'!" + target
        target_match = A1_TARGET_RE.fullmatch(target)
        if target_match is None or "[" in target or "]" in target:
            raise RemediationError(f"INDIRECT target is not internal A1: {target!r}")
        target_sheet = (
            target_match.group("quoted").replace("''", "'")
            if target_match.group("quoted") is not None
            else target_match.group("plain")
        )
        if target_sheet not in sheet_names:
            raise RemediationError(f"INDIRECT target sheet is missing: {target_sheet}")
        addresses = []
        for endpoint in target_match.group("address").split(":"):
            column, row = coordinate_from_string(endpoint.replace("$", "").upper())
            if not (1 <= column_index_from_string(column) <= 16_384 and 1 <= row <= 1_048_576):
                raise RemediationError(f"INDIRECT target is out of bounds: {target!r}")
            addresses.append(f"${column}${row}")
        substitutions += 1
        return "'" + target_sheet.replace("'", "''") + "'!" + ":".join(addresses)

    def replace(match: re.Match) -> str:
        sheet_cell = match.group("sheet_cell").replace("$", "").upper()
        address_cell = match.group("address_cell").replace("$", "").upper()
        values = static_values.get(sheet_name, {})
        if sheet_cell not in values or address_cell not in values:
            raise RemediationError(f"INDIRECT operands are not static in {sheet_name}: {sheet_cell}, {address_cell}")
        return resolved(f"{values[sheet_cell]}!{values[address_cell]}")

    rewritten = _sub_executable(INDIRECT_RE, replace, formula)
    simple = re.compile(r'(?:_xlfn\.)?INDIRECT\s*\(\s*(?P<value>"(?:[^"]|"")*"|\$?[A-Z]{1,3}\$?[1-9][0-9]*)\s*(?:,\s*(?:TRUE|1)\s*)?\)', re.I)

    def replace_simple(match):
        value = match.group("value")
        if value.startswith('"'):
            target = value[1:-1].replace('""', '"')
        else:
            target = static_values.get(sheet_name, {}).get(value.replace("$", "").upper())
        if not isinstance(target, str):
            raise RemediationError("INDIRECT address is not a static text input")
        return resolved(target)

    return _sub_executable(simple, replace_simple, rewritten), substitutions


def _sub_executable(pattern, replacement, formula):
    """Never rewrite function-looking text inside Excel string literals."""
    spans = [match.span() for match in re.finditer(r'"(?:[^"]|"")*"', formula)]
    return pattern.sub(lambda m: m.group(0) if any(a <= m.start() < b for a, b in spans)
                       else replacement(m), formula)


def _rewrite_offset(formula: str, *, named_rows=(), used_named=None, used_zero=None, sheet_name=None, used_qualified=None) -> tuple[str, int]:
    substitutions = 0

    def replace(match: re.Match) -> str:
        nonlocal substitutions
        reference = match.group("reference")
        rows = match.group("rows")
        zero = match.group("zero_column") is not None
        qualifier = match.group('qualifier') or ''
        if qualifier:
            name = qualifier[:-1]
            if name.startswith("'"):name = name[1:-1].replace("''", "'")
            if not zero or sheet_name is None or name != sheet_name:
                return match.group(0)
        if zero:
            # Only the existing dynamic scalar-cell row rule gains the
            # equivalent literal-zero column spelling. Names and expressions
            # require their own source-backed policy.
            if re.fullmatch(r'(?i)\$?[A-Z]{1,3}\$?[1-9][0-9]*', rows) is None:
                return match.group(0)
            row_coordinate = rows.replace('$', '').upper()
            row_number, row_column = coordinate_to_tuple(row_coordinate)
            if not (1 <= row_number <= 1048576 and 1 <= row_column <= 16384):
                return match.group(0)
        if re.fullmatch(r'(?i)\$?[A-Z]{1,3}\$?[1-9][0-9]*|[-+]?[0-9]+', rows) is None:
            if rows.casefold() not in named_rows:
                return match.group(0)
            if used_named is not None:
                used_named.add(rows.casefold())
        column = re.match(r"(\$?)[A-Z]{1,3}", reference, re.IGNORECASE)
        if column is None:
            raise RemediationError(f"OFFSET reference is not scalar A1: {reference}")
        column_ref = column.group(0)
        base_row = int(re.search(r'[1-9][0-9]*$', reference).group(0))
        if not (1 <= column_index_from_string(column_ref.replace('$', '').upper()) <= 16384 and 1 <= base_row <= 1048576):
            return match.group(0)
        substitutions += 1
        if qualifier and used_qualified is not None:used_qualified.add(qualifier)
        if zero and used_zero is not None:
            used_zero.add(True)
        return (
            f"INDEX({qualifier}{column_ref}:{column_ref},"
            f"ROW({qualifier}{reference})+({rows}))"
        )

    rewritten = _sub_executable(OFFSET_BLANK_COLUMN_RE, replace, formula)
    return rewritten, substitutions


def _scalar_named_rows(members, sheet_name):
    """Resolve Excel local-over-global names only to one same-workbook cell."""
    workbook = ElementTree.fromstring(members['xl/workbook.xml'])
    sheets = [name for name, _ in _sheet_parts(members)]
    if sheet_name not in sheets:
        return {}
    scope = str(sheets.index(sheet_name)); groups = defaultdict(list)
    for node in workbook.findall('.//{*}definedName'):
        name = node.attrib.get('name', '')
        if re.fullmatch(r'(?i)[A-Z_][A-Z0-9_.]*', name) and name.casefold() not in {'r','c','true','false'}:
            if node.attrib.get('localSheetId') in (None, scope):
                groups[name.casefold()].append(node)
    result = {}
    pattern = r"(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[A-Za-z_][A-Za-z0-9_.]*))!(?P<cell>\$?[A-Za-z]{1,3}\$?[1-9][0-9]*)"
    for name, nodes in groups.items():
        local = [n for n in nodes if n.attrib.get('localSheetId') == scope]
        selected = local if local else [n for n in nodes if 'localSheetId' not in n.attrib]
        if len(selected) != 1:
            continue
        node = selected[0]; raw = node.text or ''; match = re.fullmatch(pattern, raw)
        if not match:
            continue
        target_sheet = (match['quoted'].replace("''", "'") if match['quoted'] is not None else match['plain'])
        matching = [n for n in sheets if n.casefold() == target_sheet.casefold()]
        if len(matching) != 1 or any(c in target_sheet for c in '[]'):
            continue
        cell = match['cell']; cell_match = re.fullmatch(r'\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)', cell)
        if not(1 <= column_index_from_string(cell_match[1].upper()) <= 16384 and 1 <= int(cell_match[2]) <= 1048576):
            continue
        result[name] = dict(name=node.attrib['name'], local_sheet_id=node.attrib.get('localSheetId'),
                            definition=raw, sheet=matching[0], cell=cell)
    return result


def _source_sheet_name(formula: str, sheet_name: str) -> str | None:
    """Resolve only a complete current-sheet-name extraction from source metadata.

    No saved cache, filesystem basename, or current Excel session participates.
    Every CELL reference must be the same local, in-grid scalar reference.
    """
    pieces = re.findall(r'"(?:[^"]|"")*"|[^"]+', formula)
    if ''.join(pieces) != formula:
        return None
    compact = ''.join(p if p.startswith('"') else re.sub(r'\s+', '', p)
                      for p in pieces)
    cell = r'CELL\("filename",(?P<reference>\$?[A-Z]{1,3}\$?[1-9][0-9]*)\)'
    same = r'CELL\("filename",(?P=reference)\)'
    boundary = r'FIND\("\]",' + same + r'\)'
    length = r'(?:255|LEN\(' + same + r'\)-' + boundary + r')'
    match = re.fullmatch(r'MID\(' + cell + ',' + boundary + r'\+1,' + length + r'\)',
                         compact, re.IGNORECASE)
    if match is None or not 0 < len(sheet_name) <= 31:
        return None
    reference = match['reference'].replace('$', '')
    row, column = coordinate_to_tuple(reference)
    if row > 1048576 or column > 16384:
        return None
    return '"' + sheet_name.replace('"', '""') + '"'


def _rewrite_identity_offset(formula: str) -> tuple[str, int]:
    """An unchanged, scalar reference stays a reference, including in ranges.

    Only literal zero displacements with omitted size arguments are admitted.
    Relative/absolute addressing is preserved for shared-formula translation.
    """
    substitutions = 0

    def replace(match: re.Match) -> str:
        nonlocal substitutions
        reference = match.group("reference")
        column, row = coordinate_from_string(reference.replace("$", "").upper())
        if column_index_from_string(column) > 16384 or row > 1048576:
            return match.group(0)
        substitutions += 1
        return reference

    return _sub_executable(OFFSET_IDENTITY_RE, replace, formula), substitutions


def _rewrite_constant_offset(formula: str) -> tuple[str, int]:
    """Resolve an integer displacement of a scalar reference, never its value."""
    substitutions = 0

    def replace(match: re.Match) -> str:
        nonlocal substitutions
        column = column_index_from_string(match.group('column').upper())
        row = int(match.group('row'))
        if not (1 <= column <= 16384 and 1 <= row <= 1048576):
            return match.group(0)
        target_column = column + int(match.group('columns'))
        target_row = row + int(match.group('rows'))
        if not (1 <= target_column <= 16384 and 1 <= target_row <= 1048576):
            return match.group(0)
        substitutions += 1
        return (match.group('column_absolute') + get_column_letter(target_column)
                + match.group('row_absolute') + str(target_row))

    return _sub_executable(OFFSET_CONSTANT_RE, replace, formula), substitutions


def _dead_external_package(members: dict[str, bytes], removals: list[dict],
                           formula_actions: list[dict] = ()) -> list[dict]:
    """Remove a complete unused external-book graph, with no link renumbering.

    Be deliberately conservative: any bracket in retained XML text/attributes,
    dynamic reference formula, unknown relationship or binary workbook part
    keeps the entire external graph. Formula/name/cache bytes remain intact.
    """
    parts = {p for p in members if p.startswith('xl/externalLinks/')}
    if not parts:
        return []
    workbook = _rewrite_defined_names(members['xl/workbook.xml'].decode(), removals)
    root = ElementTree.fromstring(workbook)
    refs = root.findall('{*}externalReferences')
    if len(refs) != 1 or any(n.tag.rsplit('}', 1)[-1] != 'externalReference' for n in refs[0]):
        return []
    ids = [n.get('{'+DOC_REL_NS+'}id') for n in refs[0]]
    if not ids or None in ids or len(set(ids)) != len(ids):
        return []
    relpart = 'xl/_rels/workbook.xml.rels'
    rels = ElementTree.fromstring(members[relpart])
    selected = [n for n in rels if n.get('Id') in ids]
    if len(selected) != len(ids):
        return []
    books = set()
    for node in selected:
        target = posixpath.normpath(posixpath.join('xl', node.get('Target', '')))
        if (node.get('Type') != DOC_REL_NS+'/externalLink' or node.get('TargetMode', '') not in ('', 'Internal')
                or not re.fullmatch(r'xl/externalLinks/externalLink[1-9][0-9]*\.xml', target)):
            return []
        books.add(target)
    expected = books | {posixpath.join(posixpath.dirname(p), '_rels', posixpath.basename(p)+'.rels') for p in books}
    if parts != expected or len(books) != len(ids):
        return []
    for book in books:
        tree = ElementTree.fromstring(members[book])
        children = list(tree)
        if len(children) != 1 or children[0].tag.rsplit('}', 1)[-1] != 'externalBook':
            return []
        relation = ElementTree.fromstring(members[posixpath.join(posixpath.dirname(book), '_rels', posixpath.basename(book)+'.rels')])
        if (len(relation) != 1 or relation[0].get('Type') not in {
                    DOC_REL_NS+'/externalLinkPath',
                    'http://schemas.microsoft.com/office/2006/relationships/xlExternalLinkPath/xlPathMissing'}
                or relation[0].get('TargetMode') != 'External'
                or children[0].get('{'+DOC_REL_NS+'}id') != relation[0].get('Id')):
            return []
    # Closed byte-span edits avoid XML reserialization of unrelated evidence.
    def subtract(raw, tag, predicate, count):
        pattern = re.compile(r'<(?:[\w.-]+:)?'+tag+r'\b[^>]*/>')
        matches = [m for m in pattern.finditer(raw) if predicate(ElementTree.fromstring(m.group()))]
        if len(matches) != count:
            raise RemediationError('external package XML span mismatch')
        for m in reversed(matches):
            raw = raw[:m.start()] + raw[m.end():]
        return raw
    span = re.compile(r'<(?:[\w.-]+:)?externalReferences\b[^>]*>.*?</(?:[\w.-]+:)?externalReferences>', re.S)
    if len(span.findall(workbook)) != 1:
        return []
    updated = {'xl/workbook.xml': span.sub('', workbook)}
    updated[relpart] = subtract(members[relpart].decode(), 'Relationship', lambda n: n.get('Id') in ids, len(ids))
    updated['[Content_Types].xml'] = subtract(members['[Content_Types].xml'].decode(), 'Override',
        lambda n: n.get('PartName', '').lstrip('/') in books, len(books))
    # Assess the exact closed rewrites that this same plan will apply. An
    # already-proven scalar OFFSET must not keep an otherwise unused graph.
    # Unchanged dynamic references and all non-formula package uses still block.
    projected = {}
    grouped = defaultdict(list)
    for action in formula_actions:
        if action['part'].startswith('xl/worksheets/'):
            grouped[action['part']].append(action)
    for part, actions in grouped.items():
        projected[part] = _rewrite_sheet(members[part].decode(), actions)
    for part, raw in members.items():
        if part in parts:
            continue
        if part.startswith('xl/media/') and part.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff')):
            continue
        if re.fullmatch(r'xl/printerSettings/printerSettings[1-9][0-9]*\.bin', part):
            continue
        if part.startswith('xl/') and not part.endswith(('.xml', '.rels')):
            return []
        if not part.endswith(('.xml', '.rels')):
            continue
        tree = ElementTree.fromstring(updated.get(part, projected.get(part, raw)))
        for node in tree.iter():
            # Number-format brackets and cell-style names are display syntax.
            display_text = node.tag == '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'
            values = ['' if display_text else node.text or '', *(v for k, v in node.attrib.items()
                      if not (node.tag.rsplit('}', 1)[-1] == 'numFmt' and k == 'formatCode')
                      and not (part == 'xl/styles.xml' and k == 'name'
                               and node.tag == '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}cellStyle'))]
            if any('[' in v or 'externalLinks/' in v or '/externalLink' in v for v in values):
                return []
            if re.search(r'(?i)(?:INDIRECT|OFFSET)\s*\(', node.text or ''):
                return []
    return [dict(kind='prune_dead_external_package', rule_id='dead-external-book-package/v1', part='xl/workbook.xml',
                 before_members={p: sha256_bytes(members[p]) for p in sorted(parts | set(updated))},
                 removed_members=sorted(parts), replacement_members=updated)]


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
    shared_groups = defaultdict(list)
    for (sheet_name, coordinate), record in expanded.items():
        if record['attributes'].get('t') == 'shared':
            shared_groups[(sheet_name, record['attributes'].get('si'))].append(
                (coordinate, record['expanded']))
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
    named_rows_cache = {}
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
        # FIND selects the first closing bracket in CELL's saved path. A
        # bracket in a directory or workbook filename would change the result.
        sheet_literal = (None if ']' in str(source)
                         else _source_sheet_name(raw_formula, sheet_name))
        attributes = record['attributes']
        if (sheet_literal is not None and attributes.get('t') != 'shared'
                and (attributes.get('t') != 'array' or attributes.get('ref') == coordinate)):
            actions.append({
                'kind': 'replace_formula',
                'rule_id': 'cell-filename-current-sheet-name/v1',
                'part': part_by_sheet[sheet_name], 'sheet': sheet_name,
                'coordinate': coordinate,
                'raw_formula_sha256': sha256_bytes(raw_formula.encode('utf-8')),
                'expanded_formula_sha256': sha256_bytes(formula.encode('utf-8')),
                'after_formula': sheet_literal,
                'after_formula_sha256': sha256_bytes(sheet_literal.encode('utf-8')),
                'make_ordinary': True, 'substitutions': 1,
            })
            continue
        if re.search(r"(?i)(?:_xlfn\.)?OFFSET\s*\(", raw_formula):
            rewritten, identities = _rewrite_identity_offset(raw_formula)
            rewritten, constants = _rewrite_constant_offset(rewritten)
            if sheet_name not in named_rows_cache:
                named_rows_cache[sheet_name] = _scalar_named_rows(members, sheet_name)
            named_rows = named_rows_cache[sheet_name]; used_named = set(); used_zero = set(); used_qualified = set()
            rewritten, substitutions = _rewrite_offset(rewritten, named_rows=named_rows, used_named=used_named, used_zero=used_zero, sheet_name=sheet_name, used_qualified=used_qualified)
            substitutions += identities + constants
            if used_zero and attributes.get('t') == 'array' and attributes.get('ref') != coordinate:
                unresolved.append(dict(kind='offset_array_scope_unresolved',
                    location=f'{sheet_name}!{coordinate}',
                    reason='dynamic scalar-cell OFFSET does not authorize a multi-cell array rewrite'))
                continue
            if (used_named or used_zero) and record['attributes'].get('t') == 'shared':
                group = shared_groups[(sheet_name, record['attributes'].get('si'))]
                for shared_coordinate, shared_formula in group:
                    shared_formula, _ = _rewrite_identity_offset(shared_formula)
                    shared_formula, _ = _rewrite_constant_offset(shared_formula)
                    shared_used = set(); shared_zero = set(); shared_qualified = set()
                    shared_after, _ = _rewrite_offset(shared_formula, named_rows=named_rows, used_named=shared_used, used_zero=shared_zero, sheet_name=sheet_name, used_qualified=shared_qualified)
                    if shared_used != used_named or shared_zero != used_zero or shared_qualified != used_qualified or re.search(r'(?i)(?:_xlfn\.)?OFFSET\s*\(', shared_after):
                        unresolved.append(dict(kind='offset_shared_boundary_unresolved',
                            location=f'{sheet_name}!{shared_coordinate}',
                            reason='shared dynamic-row OFFSET does not retain valid scalar references',
                            formula_sha256=sha256_bytes(shared_formula.encode('utf-8'))))
            if constants and record['attributes'].get('t') == 'shared':
                # A valid displaced target does not make an invalid original
                # base reference valid. Check every existing shared destination
                # before retaining the group's shared representation.
                group = shared_groups[(sheet_name, record['attributes'].get('si'))]
                for shared_coordinate, shared_formula in group:
                    shared_formula, _ = _rewrite_identity_offset(shared_formula)
                    _, shared_constants = _rewrite_constant_offset(shared_formula)
                    if shared_constants != constants:
                        unresolved.append({
                            'kind': 'offset_shared_boundary_unresolved',
                            'location': f'{sheet_name}!{shared_coordinate}',
                            'reason': 'shared copy does not retain valid scalar base and target references',
                            'formula_sha256': sha256_bytes(shared_formula.encode('utf-8')),
                        })
            if substitutions == 0 or re.search(
                r"(?i)(?:_xlfn\.)?OFFSET\s*\(", rewritten
            ):
                unresolved.append({
                    "kind": "offset_unresolved",
                    "location": f"{sheet_name}!{coordinate}",
                    "reason": "only scalar OFFSET with literal integer displacements or the blank-column rule is eligible",
                    "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                })
                continue
            actions.append({
                "kind": "replace_formula",
                "rule_id": ("offset-same-sheet-scalar-cell-row-zero-column/v1" if used_qualified
                            else "offset-scalar-cell-row-zero-column/v1" if used_zero
                            else "offset-blank-column-scalar-name/v1" if used_named
                            else "offset-constant-scalar-and-blank-column/v3" if constants
                            else "offset-scalar-identity-and-blank-column/v2" if identities
                            else "offset-blank-column-to-index/v1"),
                "part": part_by_sheet[sheet_name],
                "sheet": sheet_name,
                "coordinate": coordinate,
                "raw_formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                "expanded_formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
                "after_formula": rewritten,
                "after_formula_sha256": sha256_bytes(rewritten.encode("utf-8")),
                "make_ordinary": False,
                "substitutions": substitutions,
                **({'named_row_operands': {n:named_rows[n] for n in sorted(used_named)}} if used_named else {}),
                **({'same_sheet_qualifiers': sorted(used_qualified)} if used_qualified else {}),
            })
            continue

        if TODAY_RE.fullmatch(raw_formula):
            # A saved cache, even in an unused presentation cell, does not
            # establish the workbook's intended valuation/as-of date. Keep this
            # source blocked until independent, hash-bound date evidence exists.
            unresolved.append({
                "kind": "today_unresolved",
                "location": f"{sheet_name}!{coordinate}",
                "reason": "original as-of meaning is not independently established",
                "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
            })
            continue

        if VOLATILE_RE.search(raw_formula):
            unresolved.append({
                "kind": "volatile_formula_unresolved",
                "location": f"{sheet_name}!{coordinate}",
                "reason": "volatile formula is outside the closed rewrite taxonomy",
                "formula_sha256": sha256_bytes(raw_formula.encode("utf-8")),
            })

    actions.extend(_dead_external_package(members, removals, actions))
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
    # Removing proven-unused names need not freeze or rewrite a worksheet's
    # clock. Keep its exact formula, cache and metadata, and leave qualification
    # to the restricted-source runtime/output-cone certificate. This is only a
    # metadata cleanup: mixed formula rewrites retain the existing closed rules.
    preserved = []
    if (actions and unresolved
            and all(a['kind'] == 'remove_dead_defined_name' for a in actions)
            and all(u['kind'] in {'today_unresolved', 'volatile_formula_unresolved'}
                    for u in unresolved)):
        for item in unresolved:
            location = item['location']
            originals = [record for (sheet, cell), record in expanded.items()
                         if sheet + '!' + cell == location]
            events = [e for e in before_health.get('restriction_events', [])
                      if e.get('location') == location]
            if (len(originals) != 1
                    or not re.fullmatch(r'(?i)\s*\+?\s*(?:_xlfn\.)?TODAY\s*\(\s*\)\s*', originals[0]['expanded'])
                    or len(events) != 1 or events[0].get('allowed') is not True
                    or events[0].get('scope') != 'worksheet'
                    or events[0].get('reason') != 'worksheet_today'):
                preserved = []
                break
            preserved.append(dict(location=location, formula_sha256=item['formula_sha256'],
                                  restriction_event=events[0],
                                  disposition='unchanged_requires_runtime_cone_certificate'))
        if len(preserved) == len(unresolved):
            unresolved = []
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
        **({'preserved_worksheet_restrictions': preserved} if preserved else {}),
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
                if info.filename in members:
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
    package_actions = []
    for action in plan["actions"]:
        if action['kind'] == 'prune_dead_external_package':
            package_actions.append(action)
        else:
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
    removed = {}
    for action in package_actions:
        if any(sha256_bytes(before[p]) != h for p, h in action['before_members'].items()):
            raise RemediationError('external package action changed')
        for part, xml in action['replacement_members'].items():
            after[part] = xml.encode('utf-8')
            changed[part] = dict(before_sha256=sha256_bytes(before[part]), after_sha256=sha256_bytes(after[part]))
        for part in action['removed_members']:
            removed[part] = sha256_bytes(after.pop(part))
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
        untouched = sorted(set(before) - set(changed) - set(removed))
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
                    "before_members",
                    "removed_members",
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
            "removed_members": removed,
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
    removed = {p: h for a in plan['actions'] if a['kind'] == 'prune_dead_external_package'
               for p, h in a['before_members'].items() if p in a['removed_members']}
    if set(before) - set(after) != set(removed) or set(after) - set(before):
        raise RemediationError("OOXML member set changed")
    if removed != manifest.get('removed_members', {}) or any(sha256_bytes(before[p]) != h for p, h in removed.items()):
        raise RemediationError('removed external package evidence changed')
    for action in plan['actions']:
        if action['kind'] == 'prune_dead_external_package':
            if any(after.get(p) != xml.encode('utf-8') for p, xml in action['replacement_members'].items()):
                raise RemediationError('external package replacement differs from original plan')
    if _value_ledger(before) != _value_ledger(after):
        raise RemediationError("formula cache/value ledger changed")
    changed = {
        name: {
            "before_sha256": sha256_bytes(before[name]),
            "after_sha256": sha256_bytes(after[name]),
        }
        for name in after if before[name] != after[name]
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
