"""Observe an OOXML workbook without executing it or trusting cached values."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from xl_seg import diagnostics
from xl_seg.evaluate import workbook_calculation_metadata


SCHEMA_VERSION = "xlsx-source-health/v3"
POLICY_VERSION = "source-recalc-policy/v3"
PREVIOUS_SCHEMA_VERSION = "xlsx-source-health/v2"
PREVIOUS_POLICY_VERSION = "source-recalc-policy/v2"
LEGACY_SCHEMA_VERSION = "xlsx-source-health/v1"
LEGACY_POLICY_VERSION = "source-recalc-policy/v1"
ENGINE_REQUIREMENTS = {
    "authoritative_engine": "microsoft-excel",
    "isolated_session": True,
    "network_disabled": True,
    "macros_disabled": True,
    "add_ins_disabled": True,
    "link_updates_disabled": True,
    "prompts_suppressed": True,
}
ROUTES = frozenset({
    "pass",
    "restricted_pass",
    "restricted_recalc_pass",
    "recalc_candidate",
    "unsupported",
    "insufficient_evidence",
})
PREVIOUS_ROUTES = ROUTES - {"restricted_recalc_pass"}
LEGACY_ROUTES = PREVIOUS_ROUTES - {"restricted_pass"}
SAMPLE_LIMIT = 20
MAX_ZIP_MEMBERS = 50_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1_000
VOLATILE_FUNCTIONS = frozenset({
    "CELL",
    "INFO",
    "INDIRECT",
    "NOW",
    "OFFSET",
    "RAND",
    "RANDBETWEEN",
    "TODAY",
})
RESTRICTION_PROFILE = {
    "schema_version": "source-restriction-profile/v3",
    "allowlist": [
        "confirmed_false_external_detection",
        "worksheet_cell_filename",
        "worksheet_indirect_a1",
        "worksheet_now",
        "worksheet_offset",
        "worksheet_today",
    ],
}
PREVIOUS_RESTRICTION_PROFILE = {
    **RESTRICTION_PROFILE,
    "schema_version": "source-restriction-profile/v2",
}
_FUNCTION_RE = re.compile(
    r"(?i)(?<![A-Z0-9_.])(?:_xlfn\.)?("
    + "|".join(sorted(VOLATILE_FUNCTIONS))
    + r")\s*\("
)
_A1_REFERENCE_RE = re.compile(
    r"(?i)^(?:(?:'(?:[^']|'')+'|[A-Z_\\][A-Z0-9_.\\]*)!)?"
    r"\$?[A-Z]{1,3}\$?[1-9][0-9]*(?::\$?[A-Z]{1,3}\$?[1-9][0-9]*)?$"
)
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class SourceHealthError(ValueError):
    """The workbook or health report could not be validated."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _object_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _quoted_spans(formula: str) -> list[tuple[int, int, str]]:
    """Return Excel double-quoted string spans, honoring doubled quotes."""
    spans = []
    index = 0
    while index < len(formula):
        if formula[index] != '"':
            index += 1
            continue
        start = index
        index += 1
        value = []
        while index < len(formula):
            if formula[index] != '"':
                value.append(formula[index])
                index += 1
                continue
            if index + 1 < len(formula) and formula[index + 1] == '"':
                value.append('"')
                index += 2
                continue
            index += 1
            break
        spans.append((start, index, "".join(value)))
    return spans


def _code_mask(formula: str, spans: list[tuple[int, int, str]]) -> str:
    characters = list(formula)
    for start, end, _ in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _call_arguments(formula: str, open_index: int) -> list[str] | None:
    arguments = []
    start = open_index + 1
    depth = 1
    index = start
    quoted = False
    while index < len(formula):
        character = formula[index]
        if quoted:
            if character == '"' and index + 1 < len(formula) and formula[index + 1] == '"':
                index += 2
                continue
            if character == '"':
                quoted = False
            index += 1
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                arguments.append(formula[start:index].strip())
                return arguments
        elif character in {",", ";"} and depth == 1:
            arguments.append(formula[start:index].strip())
            start = index + 1
        index += 1
    return None


def _string_literal(argument: str) -> str | None:
    spans = _quoted_spans(argument)
    if len(spans) == 1 and spans[0][0] == 0 and spans[0][1] == len(argument):
        return spans[0][2]
    return None


def _volatile_decision(
    function: str,
    formula: str,
    open_index: int,
    scope: str,
) -> tuple[bool, str]:
    if scope != "worksheet":
        return False, "defined_name_dynamic"
    arguments = _call_arguments(formula, open_index)
    if arguments is None:
        return False, "unparsed_volatile_call"
    if function == "OFFSET":
        if 3 <= len(arguments) <= 5 and all(arguments[:3]):
            return True, "worksheet_offset"
        return False, "unparsed_volatile_call"
    if function in {"TODAY", "NOW"}:
        if arguments == [""]:
            return True, f"worksheet_{function.lower()}"
        return False, "unparsed_volatile_call"
    if function == "CELL":
        first = _string_literal(arguments[0]) if arguments else None
        if first is not None and first.casefold() == "filename":
            return True, "worksheet_cell_filename"
        return False, "cell_information_dynamic"
    if function == "INDIRECT":
        first = _string_literal(arguments[0]) if arguments else None
        a1_mode = (
            len(arguments) < 2
            or arguments[1].strip().upper() in {"TRUE", "1"}
        )
        if first is not None and a1_mode and _A1_REFERENCE_RE.fullmatch(first):
            return True, "worksheet_indirect_a1"
        return False, (
            "indirect_r1c1" if not a1_mode else "indirect_dynamic_or_non_a1"
        )
    if function in {"RAND", "RANDBETWEEN"}:
        return False, "random_function"
    if function == "INFO":
        return False, "information_function"
    return False, "unsupported_volatile_function"


def _formula_restriction_events(
    formula: str,
    *,
    scope: str,
    location: str,
) -> list[dict]:
    """Classify formula tokens without confusing strings and structured refs."""
    events = []
    spans = _quoted_spans(formula)
    code = _code_mask(formula, spans)
    external_literal_starts: set[int] = set()
    for match in _FUNCTION_RE.finditer(code):
        function = match.group(1).upper()
        open_index = code.find("(", match.start(), match.end())
        allowed, reason = _volatile_decision(
            function, formula, open_index, scope
        )
        events.append({
            "allowed": allowed,
            "event": "volatile_function",
            "function": function,
            "location": location,
            "reason": reason,
            "scope": scope,
            "token_offset": match.start(),
        })
        if function == "INDIRECT":
            arguments = _call_arguments(formula, open_index)
            first = _string_literal(arguments[0]) if arguments else None
            if first is not None and re.fullmatch(
                r"(?i)(?:'(?:[^']|'')*\[[^\]]+\](?:[^']|'')*'|"
                r"\[[^\]]+\](?:[A-Z_\\][A-Z0-9_.\\]*|'(?:[^']|'')*'))"
                r"!\$?[A-Z]{1,3}\$?[1-9][0-9]*",
                first,
            ):
                first_span = next(
                    (
                        start for start, _, value in spans
                        if start > open_index and value == first
                    ),
                    None,
                )
                if first_span is not None:
                    external_literal_starts.add(first_span)

    external_spans: set[tuple[int, int]] = set()
    external_pattern = re.compile(
        r"(?i)(?:'(?:[^']|'')*\[[^\]]+\](?:[^']|'')*'|"
        r"\[[^\]]+\](?:[A-Z_\\][A-Z0-9_.\\]*|'(?:[^']|'')*'))\s*!"
    )
    for match in external_pattern.finditer(code):
        external_spans.add((match.start(), match.end()))
        events.append({
            "allowed": False,
            "event": "external_workbook_reference",
            "location": location,
            "reason": "true_external_workbook_reference",
            "scope": scope,
            "token": formula[match.start():match.end()],
            "token_offset": match.start(),
        })
    for match in re.finditer(r"\[[^\]]+\]", code):
        if any(start <= match.start() < end for start, end in external_spans):
            continue
        content = match.group(0)[1:-1]
        previous = code[match.start() - 1] if match.start() else ""
        external_hint = (
            content.isdigit()
            or re.search(r"(?i)\.xls(?:x|m|b)?$", content) is not None
            or "/" in content
            or "\\" in content
        )
        if external_hint and not (previous.isalnum() or previous in {"_", "]"}):
            events.append({
                "allowed": False,
                "event": "external_workbook_reference",
                "location": location,
                "reason": "true_external_workbook_reference",
                "scope": scope,
                "token": match.group(0),
                "token_offset": match.start(),
            })
            continue
        events.append({
            "allowed": False,
            "event": "structured_reference",
            "location": location,
            "reason": "structured_reference_unresolved_by_ast",
            "scope": scope,
            "token": match.group(0),
            "token_offset": match.start(),
        })
    for start, _, value in spans:
        for match in re.finditer(r"\[[^\]]+\]", value):
            if start in external_literal_starts:
                events.append({
                    "allowed": False,
                    "event": "external_workbook_reference",
                    "location": location,
                    "reason": "true_external_workbook_reference",
                    "scope": scope,
                    "token": value,
                    "token_offset": start,
                })
                continue
            events.append({
                "allowed": True,
                "event": "false_external_detection",
                "location": location,
                "reason": "confirmed_false_external_detection",
                "scope": scope,
                "token": match.group(0),
                "token_offset": start + 1 + match.start(),
            })
    return events


def _sorted_events(events: list[dict]) -> list[dict]:
    return sorted(
        events,
        key=lambda item: (
            item.get("scope", ""),
            item.get("location", ""),
            item.get("event", ""),
            item.get("token_offset", -1),
            _object_hash(item),
        ),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    candidate = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceHealthError("source is not a regular file")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_regular_nofollow(path: Path):
    descriptor = os.open(
        Path(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    handle = os.fdopen(descriptor, "rb")
    metadata = os.fstat(handle.fileno())
    if not stat.S_ISREG(metadata.st_mode):
        handle.close()
        raise SourceHealthError("source is not a regular file")
    return handle


def validate_ooxml_package(path: str | Path) -> dict:
    """Reject unsafe, ambiguous, encrypted, or unbounded ZIP packages."""
    source = Path(path)
    try:
        with _open_regular_nofollow(source) as handle:
            with zipfile.ZipFile(handle) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                failures = []
                if len(infos) > MAX_ZIP_MEMBERS:
                    failures.append("member_count")
                if len(names) != len(set(names)):
                    failures.append("duplicate_member")
                expanded = 0
                for info in infos:
                    normalized = posixpath.normpath(info.filename.replace("\\", "/"))
                    if (
                        info.filename.startswith(("/", "\\"))
                        or normalized in {"", ".", ".."}
                        or normalized.startswith("../")
                    ):
                        failures.append("unsafe_member_path")
                    if info.flag_bits & 0x1:
                        failures.append("encrypted_member")
                    expanded += info.file_size
                    if (
                        info.file_size > 0
                        and (
                            info.compress_size <= 0
                            or info.file_size
                            > info.compress_size * MAX_COMPRESSION_RATIO
                        )
                    ):
                        failures.append("compression_ratio")
                if expanded > MAX_EXPANDED_BYTES:
                    failures.append("expanded_size")
                if failures:
                    raise SourceHealthError(
                        "unsafe OOXML package: " + ", ".join(sorted(set(failures)))
                    )
                corrupt = archive.testzip()
                if corrupt is not None:
                    raise SourceHealthError(
                        f"OOXML package CRC failed for {corrupt}"
                    )
                return {
                    "members": len(infos),
                    "expanded_bytes": expanded,
                }
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceHealthError(f"unreadable OOXML package: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_report(path: Path, report: dict) -> Path:
    """Durably replace one report; interrupted writes leave the old report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return path


def _parse_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError) as exc:
        raise SourceHealthError(f"unreadable OOXML part {name}: {exc}") from exc


def _workbook_sheet_parts(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = _parse_xml(archive, "xl/workbook.xml")
    relations = _parse_xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {
        relation.attrib.get("Id"): relation.attrib.get("Target", "")
        for relation in relations.findall(f"{{{_REL_NS}}}Relationship")
    }
    parts = []
    for sheet in workbook.findall(".//{*}sheet"):
        relationship_id = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = targets.get(relationship_id, "")
        if not target:
            raise SourceHealthError(
                f"worksheet relationship {relationship_id!r} is missing"
            )
        if target.startswith("/"):
            part = target.lstrip("/")
        else:
            part = posixpath.join("xl", target)
        normalized = posixpath.normpath(part)
        if normalized == ".." or normalized.startswith("../"):
            raise SourceHealthError("worksheet relationship escapes the package")
        parts.append((sheet.attrib.get("name", ""), normalized))
    return parts


def _calculation_record(path: Path) -> dict:
    metadata = workbook_calculation_metadata(path)
    return {
        "available": metadata.available,
        "reason": metadata.reason,
        "calc_mode": metadata.calc_mode,
        "calc_mode_origin": metadata.calc_mode_origin,
        "full_calc_on_load": metadata.full_calc_on_load,
        "full_calc_on_load_origin": metadata.full_calc_on_load_origin,
        "force_full_calc": metadata.force_full_calc,
        "force_full_calc_origin": metadata.force_full_calc_origin,
        "iterate": metadata.iterate,
        "iterate_origin": metadata.iterate_origin,
        "iterate_count": metadata.iterate_count,
        "iterate_count_origin": metadata.iterate_count_origin,
        "iterate_delta": metadata.iterate_delta,
        "iterate_delta_origin": metadata.iterate_delta_origin,
        "raw_calc_pr": metadata.raw_calc_pr,
    }


def _report_id(report: dict) -> str:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def _diagnostics_record(route: str, reasons: list[str], formula_count: int) -> dict:
    evidence: dict = {"executed": True, "reason_counts": {}}
    if route == "recalc_candidate":
        evidence["source"] = {"recalc_required": True}
    elif route == "restricted_pass":
        evidence["source"] = {"restricted_policy_pass": True}
    elif route == "insufficient_evidence":
        evidence["missing_evidence"] = reasons or ["source_health_incomplete"]
    elif route == "unsupported":
        evidence["executed"] = False
        evidence["reason_counts"] = {reason: 1 for reason in reasons}
    elif formula_count == 0:
        # A source-health pass says only that the package is internally safe to
        # route. It does not assert that a formula-free file is an output model.
        evidence["executed"] = False
    classification = diagnostics.classify_disposition(evidence)
    return {
        "schema_version": diagnostics.SCHEMA_VERSION,
        "evidence": evidence,
        "classification": classification,
    }


def inspect_workbook(
    path: str | Path,
    *,
    policy_version: str = POLICY_VERSION,
) -> dict:
    """Return deterministic observations and conservative routing evidence."""
    if policy_version not in {POLICY_VERSION, PREVIOUS_POLICY_VERSION}:
        raise SourceHealthError(
            f"unsupported observation policy: {policy_version}"
        )
    current_policy = policy_version == POLICY_VERSION
    source = Path(path)
    source_size = None
    source_hash = None
    source_problem = None
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            source_problem = "source_symlink"
        elif not stat.S_ISREG(metadata.st_mode):
            source_problem = "source_not_regular"
        elif source.suffix.casefold() != ".xlsx":
            source_problem = "unsupported_source_format"
        else:
            source_size = metadata.st_size
            source_hash = sha256_file(source)
    except FileNotFoundError:
        source_problem = "source_missing"
    except OSError:
        source_problem = "source_unreadable"
    base = {
        "schema_version": (
            SCHEMA_VERSION if current_policy else PREVIOUS_SCHEMA_VERSION
        ),
        "policy_version": policy_version,
        "engine_requirements": ENGINE_REQUIREMENTS,
        "source_sha256": source_hash,
        "source_size_bytes": source_size,
        "source": {
            "name": source.name,
            "size_bytes": source_size,
            "sha256": source_hash,
        },
        "restriction_profile": (
            RESTRICTION_PROFILE
            if current_policy
            else PREVIOUS_RESTRICTION_PROFILE
        ),
    }
    if source_problem is not None:
        reasons = [source_problem]
        report = {
            **base,
            "route": "insufficient_evidence",
            "routing": "insufficient_evidence",
            "reason_codes": reasons,
            "calculation": {"available": False},
            "counts": {},
            "samples": {},
            "restriction_events": [],
            "restriction_events_sha256": _object_hash([]),
            "diagnostics": _diagnostics_record(
                "insufficient_evidence", reasons, 0
            ),
        }
        if current_policy:
            report["recalc_signals"] = []
            report["recalc_signals_sha256"] = _object_hash([])
        report["report_sha256"] = _report_id(report)
        return report

    try:
        package_limits = validate_ooxml_package(source)
        with _open_regular_nofollow(source) as source_handle, zipfile.ZipFile(
            source_handle
        ) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                raise SourceHealthError("required OOXML workbook parts are missing")
            sheet_parts = _workbook_sheet_parts(archive)
            formula_count = 0
            cache_present = 0
            cache_populated = 0
            cache_empty = 0
            cache_blank_string = 0
            missing_cache = 0
            data_tables = 0
            shared_formula_followers_expanded = 0
            shared_formula_followers_inherited_unrestricted = 0
            shared_formula_followers_unexpanded = 0
            volatile = Counter()
            volatile_cells: list[str] = []
            external_formula_cells: list[str] = []
            restriction_events: list[dict] = []
            for sheet_name, part in sheet_parts:
                root = _parse_xml(archive, part)
                cells = root.findall(".//{*}c")
                shared_masters: dict[
                    str,
                    tuple[str, str, tuple[int, int, int, int], bool] | None,
                ] = {}
                for cell in cells:
                    formula = cell.find("{*}f")
                    if (
                        formula is None
                        or formula.attrib.get("t") != "shared"
                        or not (formula.text or "").strip()
                    ):
                        continue
                    shared_index = formula.attrib.get("si", "")
                    origin = cell.attrib.get("r", "")
                    reference = formula.attrib.get("ref", "")
                    if (
                        not shared_index
                        or not origin
                        or not reference
                        or shared_index in shared_masters
                    ):
                        shared_masters[shared_index] = None
                        continue
                    try:
                        bounds = range_boundaries(reference)
                        minimum_column, minimum_row, maximum_column, maximum_row = bounds
                        origin_row, origin_column = coordinate_to_tuple(origin)
                    except (TypeError, ValueError):
                        shared_masters[shared_index] = None
                        continue
                    if not (
                        minimum_row <= origin_row <= maximum_row
                        and minimum_column <= origin_column <= maximum_column
                    ):
                        shared_masters[shared_index] = None
                        continue
                    master_text = formula.text or ""
                    requires_translation = bool(_formula_restriction_events(
                        master_text,
                        scope="worksheet",
                        location=f"{sheet_name}!{origin}",
                    ))
                    shared_masters[shared_index] = (
                        origin,
                        master_text,
                        bounds,
                        requires_translation,
                    )

                for cell in cells:
                    formula = cell.find("{*}f")
                    if formula is None:
                        continue
                    formula_count += 1
                    coordinate = f"{sheet_name}!{cell.attrib.get('r', '')}"
                    value = cell.find("{*}v")
                    if value is None:
                        missing_cache += 1
                    else:
                        cache_present += 1
                        if value.text in (None, ""):
                            # A string-typed empty cache is the calculated
                            # result of formulas such as IF(...,"ERROR","").
                            # It is complete evidence, not a stale cache.
                            if cell.attrib.get("t") == "str":
                                cache_blank_string += 1
                                cache_populated += 1
                            else:
                                cache_empty += 1
                        else:
                            cache_populated += 1
                    text = formula.text or ""
                    if (
                        formula.attrib.get("t") == "shared"
                        and not text.strip()
                    ):
                        shared_index = formula.attrib.get("si", "")
                        master = shared_masters.get(shared_index)
                        try:
                            if master is None:
                                raise ValueError("shared formula master is unavailable")
                            (
                                origin,
                                master_text,
                                bounds,
                                requires_translation,
                            ) = master
                            (
                                minimum_column,
                                minimum_row,
                                maximum_column,
                                maximum_row,
                            ) = bounds
                            follower_row, follower_column = coordinate_to_tuple(
                                cell.attrib.get("r", "")
                            )
                            if not (
                                minimum_row <= follower_row <= maximum_row
                                and minimum_column <= follower_column <= maximum_column
                            ):
                                raise ValueError(
                                    "shared formula follower is outside master range"
                                )
                            if requires_translation:
                                translated = Translator(
                                    "=" + master_text.lstrip("="),
                                    origin=origin,
                                ).translate_formula(cell.attrib.get("r", ""))
                                if (
                                    not translated.startswith("=")
                                    or len(translated) == 1
                                ):
                                    raise ValueError(
                                        "shared formula translation is empty"
                                    )
                                text = translated[1:]
                            else:
                                # Translation changes references, not token kinds.
                                # A master with no restricted token cannot acquire
                                # one in a shared follower.
                                text = ""
                        except (KeyError, TypeError, ValueError, TranslatorError):
                            shared_formula_followers_unexpanded += 1
                            restriction_events.append({
                                "allowed": False,
                                "event": "shared_formula_follower_unexpanded",
                                "location": coordinate,
                                "reason": "shared_formula_follower_unexpanded",
                                "scope": "worksheet",
                                "shared_index": shared_index,
                                "token_offset": 0,
                            })
                        else:
                            if requires_translation:
                                shared_formula_followers_expanded += 1
                            else:
                                shared_formula_followers_inherited_unrestricted += 1
                    if formula.attrib.get("t") == "dataTable":
                        data_tables += 1
                        restriction_events.append({
                            "allowed": False,
                            "event": "data_table_formula",
                            "location": coordinate,
                            "reason": "data_table",
                            "scope": "worksheet",
                            "token_offset": 0,
                        })
                    formula_events = _formula_restriction_events(
                        text,
                        scope="worksheet",
                        location=coordinate,
                    )
                    restriction_events.extend(formula_events)
                    functions = sorted({
                        event["function"]
                        for event in formula_events
                        if event["event"] == "volatile_function"
                    })
                    for function in functions:
                        volatile[function] += 1
                    if functions:
                        volatile_cells.append(coordinate)
                    if any(
                        event["event"] == "external_workbook_reference"
                        for event in formula_events
                    ):
                        external_formula_cells.append(coordinate)

            workbook_root = _parse_xml(archive, "xl/workbook.xml")
            defined_name_formulas = 0
            for defined_name in workbook_root.findall(".//{*}definedName"):
                text = defined_name.text or ""
                if not text:
                    continue
                defined_name_formulas += 1
                coordinate = "DEFINED_NAME:" + defined_name.attrib.get(
                    "name", "<unnamed>"
                )
                formula_events = _formula_restriction_events(
                    text,
                    scope="defined_name",
                    location=coordinate,
                )
                restriction_events.extend(formula_events)
                functions = sorted({
                    event["function"]
                    for event in formula_events
                    if event["event"] == "volatile_function"
                })
                for function in functions:
                    volatile[function] += 1
                if functions:
                    volatile_cells.append(coordinate)
                if any(
                    event["event"] == "external_workbook_reference"
                    for event in formula_events
                ):
                    external_formula_cells.append(coordinate)

            external_link_parts = sorted(
                name for name in names if name.startswith("xl/externalLinks/")
                and name.endswith(".xml")
                and "/_rels/" not in name
            )
            relationship_types = []
            for relationship_part in sorted(
                name for name in names if name.endswith(".rels")
            ):
                relation_root = _parse_xml(archive, relationship_part)
                for relation in relation_root.findall(
                    f"{{{_REL_NS}}}Relationship"
                ):
                    relation_type = relation.attrib.get("Type", "")
                    relationship_types.append(relation_type)
                    if relation_type.endswith(
                        ("/externalLink", "/externalLinkPath")
                    ):
                        restriction_events.append({
                            "allowed": False,
                            "event": "package_external_link_relationship",
                            "location": relationship_part,
                            "reason": "package_external_link",
                            "relationship_id": relation.attrib.get("Id", ""),
                            "scope": "package",
                            "target": relation.attrib.get("Target", ""),
                            "target_mode": relation.attrib.get("TargetMode", ""),
                            "type": relation_type,
                        })
                    if relation_type.endswith("/oleObject"):
                        restriction_events.append({
                            "allowed": False,
                            "event": "package_ole_relationship",
                            "location": relationship_part,
                            "reason": "ole_object",
                            "relationship_id": relation.attrib.get("Id", ""),
                            "scope": "package",
                            "target": relation.attrib.get("Target", ""),
                            "target_mode": relation.attrib.get("TargetMode", ""),
                            "type": relation_type,
                        })
                    if relation_type.endswith("/vbaProject"):
                        restriction_events.append({
                            "allowed": False,
                            "event": "package_macro_relationship",
                            "location": relationship_part,
                            "reason": "macro",
                            "relationship_id": relation.attrib.get("Id", ""),
                            "scope": "package",
                            "target": relation.attrib.get("Target", ""),
                            "target_mode": relation.attrib.get("TargetMode", ""),
                            "type": relation_type,
                        })
                    if relation_type.endswith("/connections"):
                        restriction_events.append({
                            "allowed": False,
                            "event": "package_connection_relationship",
                            "location": relationship_part,
                            "reason": "connection",
                            "relationship_id": relation.attrib.get("Id", ""),
                            "scope": "package",
                            "target": relation.attrib.get("Target", ""),
                            "target_mode": relation.attrib.get("TargetMode", ""),
                            "type": relation_type,
                        })
            external_link_relations = sum(
                relation_type.endswith(("/externalLink", "/externalLinkPath"))
                for relation_type in relationship_types
            )
            ole_relations = sum(
                relation_type.endswith("/oleObject")
                for relation_type in relationship_types
            )
            macro_relations = sum(
                relation_type.endswith("/vbaProject")
                for relation_type in relationship_types
            )
            connection_relations = sum(
                relation_type.endswith("/connections")
                for relation_type in relationship_types
            )
            connection_count = 0
            if "xl/connections.xml" in names:
                connections = _parse_xml(
                    archive, "xl/connections.xml"
                ).findall(".//{*}connection")
                connection_count = len(connections)
                restriction_events.append({
                    "allowed": False,
                    "event": "package_connections_part",
                    "location": "xl/connections.xml",
                    "reason": "connection",
                    "scope": "package",
                })
                for index, connection in enumerate(connections):
                    restriction_events.append({
                        "allowed": False,
                        "event": "package_connection",
                        "location": "xl/connections.xml",
                        "reason": "connection",
                        "scope": "package",
                        "connection_id": connection.attrib.get("id", ""),
                        "connection_index": index,
                        "connection_name": connection.attrib.get("name", ""),
                    })
            macro_parts = sorted(
                name for name in names
                if name.lower().endswith(("vbaproject.bin", "vbadata.xml"))
            )
            content_types = archive.read("[Content_Types].xml").lower()
            macro_content_type = (
                b"macroenabled" in content_types
                or b"vbaproject" in content_types
            )
            ole_parts = sorted(
                name for name in names
                if name.startswith("xl/embeddings/")
                or "/oleObject" in name
            )
            for part in external_link_parts:
                restriction_events.append({
                    "allowed": False,
                    "event": "package_external_link_part",
                    "location": part,
                    "reason": "package_external_link",
                    "scope": "package",
                })
            for part in macro_parts:
                restriction_events.append({
                    "allowed": False,
                    "event": "package_macro_part",
                    "location": part,
                    "reason": "macro",
                    "scope": "package",
                })
            if macro_content_type:
                restriction_events.append({
                    "allowed": False,
                    "event": "package_macro_content_type",
                    "location": "[Content_Types].xml",
                    "reason": "macro",
                    "scope": "package",
                })
            for part in ole_parts:
                restriction_events.append({
                    "allowed": False,
                    "event": "package_ole_part",
                    "location": part,
                    "reason": "ole_object",
                    "scope": "package",
                })
            calculation = _calculation_record(source)
    except (
        OSError,
        zipfile.BadZipFile,
        SourceHealthError,
        ElementTree.ParseError,
    ) as exc:
        reasons = ["ooxml_unreadable"]
        report = {
            **base,
            "route": "insufficient_evidence",
            "routing": "insufficient_evidence",
            "reason_codes": reasons,
            "calculation": {"available": False, "reason": str(exc)},
            "counts": {},
            "samples": {"errors": [f"{type(exc).__name__}: {exc}"]},
            "restriction_events": [],
            "restriction_events_sha256": _object_hash([]),
            "diagnostics": _diagnostics_record(
                "insufficient_evidence", reasons, 0
            ),
        }
        report["report_sha256"] = _report_id(report)
        return report

    counts = {
        "package_members": package_limits["members"],
        "package_expanded_bytes": package_limits["expanded_bytes"],
        "sheets": len(sheet_parts),
        "formula_cells": formula_count,
        "defined_name_formulas": defined_name_formulas,
        "formula_caches_present": cache_present,
        "formula_caches_populated": cache_populated,
        "formula_caches_empty": cache_empty,
        "formula_caches_blank_string": cache_blank_string,
        "formula_caches_missing": missing_cache,
        "external_links": len(external_link_parts),
        "external_link_relationships": external_link_relations,
        "external_formula_references": len(external_formula_cells),
        "connections": connection_count,
        "connection_relationships": connection_relations,
        "macro_parts": len(macro_parts),
        "macro_relationships": macro_relations,
        "ole_parts": len(ole_parts),
        "ole_relationships": ole_relations,
        "volatile_formula_cells": len(volatile_cells),
        "data_table_formulas": data_tables,
        "shared_formula_followers_expanded": shared_formula_followers_expanded,
        "shared_formula_followers_inherited_unrestricted": (
            shared_formula_followers_inherited_unrestricted
        ),
        "shared_formula_followers_unexpanded": shared_formula_followers_unexpanded,
    }
    restriction_events = _sorted_events(restriction_events)
    disallowed_events = [
        event for event in restriction_events if event.get("allowed") is not True
    ]
    allowed_events = [
        event for event in restriction_events if event.get("allowed") is True
    ]
    unsupported = []
    if macro_parts or macro_content_type or macro_relations:
        unsupported.append("macros_present")
    if ole_parts or ole_relations:
        unsupported.append("ole_objects_present")
    if external_link_parts or external_link_relations or external_formula_cells:
        unsupported.append("external_links_present")
    if "xl/connections.xml" in names or connection_relations:
        unsupported.append("connections_present")
    if data_tables:
        unsupported.append("data_tables_present")
    if any(
        event["event"] == "structured_reference" for event in disallowed_events
    ):
        unsupported.append("structured_references_present")
    if any(
        event["event"] == "volatile_function" for event in disallowed_events
    ):
        unsupported.append("unsupported_volatile_formulas_present")
    if shared_formula_followers_unexpanded:
        unsupported.append("shared_formula_followers_unexpanded")

    incomplete = []
    unknown_calc = any(
        calculation.get(field) == "unknown"
        for field in (
            "calc_mode_origin",
            "full_calc_on_load_origin",
            "force_full_calc_origin",
            "iterate_origin",
            "iterate_count_origin",
            "iterate_delta_origin",
        )
    )
    if not calculation.get("available") or unknown_calc:
        incomplete.append("calculation_settings_unrecognized")

    recalc = []
    if formula_count and missing_cache:
        recalc.append("formula_cache_incomplete")
    if formula_count and cache_empty:
        recalc.append("formula_cache_empty")
    if formula_count and calculation.get("calc_mode") == "manual":
        recalc.append("manual_calculation_mode")
    if formula_count and calculation.get("full_calc_on_load") is True:
        recalc.append("full_calculation_on_load_requested")
    if formula_count and calculation.get("force_full_calc") is True:
        recalc.append("forced_full_calculation_requested")

    recalc_signals = []
    for signal in recalc:
        record = {
            "formula_cache_count": cache_present,
            "formula_cache_missing_count": missing_cache,
            "formula_count": formula_count,
            "signal": signal,
        }
        if signal in {
            "manual_calculation_mode",
            "full_calculation_on_load_requested",
            "forced_full_calculation_requested",
        }:
            record["calculation"] = calculation
        recalc_signals.append(record)
    recalc_signals = sorted(
        recalc_signals,
        key=lambda item: (
            item["signal"],
            item["formula_cache_missing_count"],
        ),
    )
    if not current_policy and allowed_events and recalc:
        unsupported.append("mixed_restricted_recalc")

    if unsupported or disallowed_events:
        route = "unsupported"
        reasons = unsupported or ["restriction_event_not_allowlisted"]
    elif incomplete:
        route = "insufficient_evidence"
        reasons = incomplete
    elif current_policy and allowed_events and recalc:
        route = "restricted_recalc_pass"
        reasons = sorted({
            "mixed_restricted_recalc",
            *[event["reason"] for event in allowed_events],
        })
    elif recalc:
        route = "recalc_candidate"
        reasons = recalc
    elif allowed_events:
        route = "restricted_pass"
        reasons = sorted({event["reason"] for event in allowed_events})
    else:
        route = "pass"
        reasons = []

    report = {
        **base,
        "route": route,
        "routing": route,
        "reason_codes": sorted(reasons),
        "calculation": calculation,
        "counts": counts,
        "volatile_functions": dict(sorted(volatile.items())),
        "restriction_events": restriction_events,
        "restriction_events_sha256": _object_hash(restriction_events),
        "samples": {
            "volatile_formula_cells": sorted(volatile_cells)[:SAMPLE_LIMIT],
            "external_formula_cells": sorted(external_formula_cells)[:SAMPLE_LIMIT],
            "external_link_parts": external_link_parts[:SAMPLE_LIMIT],
            "macro_parts": macro_parts[:SAMPLE_LIMIT],
            "ole_parts": ole_parts[:SAMPLE_LIMIT],
        },
        # Observation alone never proves that any populated cached value is stale.
        "proven_stale_cache": False,
        "formula_count": formula_count,
        "formula_cache_count": cache_present,
        "formula_cache_missing_count": missing_cache,
        "external_link_count": len(external_link_parts),
        "connection_count": connection_count,
        "diagnostics": _diagnostics_record(route, reasons, formula_count),
    }
    if current_policy:
        report["recalc_signals"] = recalc_signals
        report["recalc_signals_sha256"] = _object_hash(recalc_signals)
    report["report_sha256"] = _report_id(report)
    return report


observe_workbook = inspect_workbook


def validate_report(report: dict, source_path: str | Path | None = None) -> dict:
    if not isinstance(report, dict):
        raise SourceHealthError("source-health report must be an object")
    failures = []
    schema_version = report.get("schema_version")
    policy_version = report.get("policy_version")
    legacy = (
        schema_version == LEGACY_SCHEMA_VERSION
        and policy_version == LEGACY_POLICY_VERSION
    )
    previous = (
        schema_version == PREVIOUS_SCHEMA_VERSION
        and policy_version == PREVIOUS_POLICY_VERSION
    )
    current = (
        schema_version == SCHEMA_VERSION
        and policy_version == POLICY_VERSION
    )
    if not (legacy or previous or current):
        failures.append("schema_version")
    if not (legacy or previous or current):
        failures.append("policy_version")
    allowed_routes = (
        LEGACY_ROUTES
        if legacy
        else PREVIOUS_ROUTES
        if previous
        else ROUTES
    )
    if report.get("route") not in allowed_routes:
        failures.append("route")
    if report.get("routing") != report.get("route"):
        failures.append("routing")
    if not isinstance(report.get("reason_codes"), list):
        failures.append("reason_codes")
    if report.get("report_sha256") != _report_id(report):
        failures.append("report_sha256")
    if current or previous:
        events = report.get("restriction_events")
        profile = report.get("restriction_profile")
        if not isinstance(events, list):
            failures.append("restriction_events")
        elif report.get("restriction_events_sha256") != _object_hash(events):
            failures.append("restriction_events_sha256")
        expected_profile = (
            RESTRICTION_PROFILE if current else PREVIOUS_RESTRICTION_PROFILE
        )
        if profile != expected_profile:
            failures.append("restriction_profile")
        if report.get("route") in {
            "restricted_pass",
            "restricted_recalc_pass",
        } and (
            not events
            or any(event.get("allowed") is not True for event in events)
            or any(
                event.get("reason") not in expected_profile["allowlist"]
                for event in events
            )
        ):
            failures.append("restricted_pass_events")
        if current:
            signals = report.get("recalc_signals")
            if not isinstance(signals, list):
                failures.append("recalc_signals")
            elif report.get("recalc_signals_sha256") != _object_hash(signals):
                failures.append("recalc_signals_sha256")
            if report.get("route") == "restricted_recalc_pass" and (
                not signals
                or "mixed_restricted_recalc"
                not in report.get("reason_codes", [])
            ):
                failures.append("restricted_recalc_signals")
    source = report.get("source")
    if not isinstance(source, dict):
        failures.append("source")
    elif (
        report.get("source_sha256") != source.get("sha256")
        or report.get("source_size_bytes") != source.get("size_bytes")
    ):
        failures.append("source_aliases")
    elif source_path is not None:
        path = Path(source_path)
        if (
            not path.is_file()
            or path.is_symlink()
            or source.get("sha256") != sha256_file(path)
            or source.get("size_bytes") != path.stat().st_size
        ):
            failures.append("source_binding")
    diagnostics_record = report.get("diagnostics")
    if not isinstance(diagnostics_record, dict):
        failures.append("diagnostics")
    else:
        expected = diagnostics.classify_disposition(
            diagnostics_record.get("evidence") or {}
        )
        if diagnostics_record.get("classification") != expected:
            failures.append("diagnostics_classification")
    if failures:
        raise SourceHealthError(
            "source-health validation failed: " + ", ".join(failures)
        )
    return report


def read_report(path: str | Path, source_path: str | Path | None = None) -> dict:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceHealthError(f"cannot read source-health report: {exc}") from exc
    return validate_report(report, source_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    observe = commands.add_parser("observe")
    observe.add_argument("source")
    observe.add_argument("-o", "--output")
    validate = commands.add_parser("validate")
    validate.add_argument("report")
    validate.add_argument("--source")
    args = parser.parse_args(argv)
    try:
        if args.command == "observe":
            report = inspect_workbook(args.source)
            if args.output:
                atomic_write_report(Path(args.output), report)
            print(json.dumps(report, sort_keys=True))
            return 0
        report = read_report(args.report, args.source)
        print(json.dumps({
            "status": "valid",
            "route": report["route"],
            "source_sha256": report["source"]["sha256"],
            "report_sha256": report["report_sha256"],
        }, sort_keys=True))
        return 0
    except SourceHealthError as exc:
        print(f"source health FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
