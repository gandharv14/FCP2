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

from xl_seg import diagnostics
from xl_seg.evaluate import workbook_calculation_metadata


SCHEMA_VERSION = "xlsx-source-health/v1"
POLICY_VERSION = "source-recalc-policy/v1"
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
    "recalc_candidate",
    "unsupported",
    "insufficient_evidence",
})
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
_VOLATILE_RE = re.compile(
    r"(?i)(?<![A-Z0-9_.])(" + "|".join(sorted(VOLATILE_FUNCTIONS)) + r")\s*\("
)
_EXTERNAL_FORMULA_RE = re.compile(r"\[[^\]]+\]")
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class SourceHealthError(ValueError):
    """The workbook or health report could not be validated."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


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


def inspect_workbook(path: str | Path) -> dict:
    """Return deterministic observations and conservative routing evidence."""
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
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "engine_requirements": ENGINE_REQUIREMENTS,
        "source_sha256": source_hash,
        "source_size_bytes": source_size,
        "source": {
            "name": source.name,
            "size_bytes": source_size,
            "sha256": source_hash,
        },
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
            "diagnostics": _diagnostics_record(
                "insufficient_evidence", reasons, 0
            ),
        }
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
            missing_cache = 0
            data_tables = 0
            volatile = Counter()
            volatile_cells: list[str] = []
            external_formula_cells: list[str] = []
            for sheet_name, part in sheet_parts:
                root = _parse_xml(archive, part)
                for cell in root.findall(".//{*}c"):
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
                            cache_empty += 1
                        else:
                            cache_populated += 1
                    text = formula.text or ""
                    if formula.attrib.get("t") == "dataTable":
                        data_tables += 1
                    functions = sorted({
                        match.group(1).upper()
                        for match in _VOLATILE_RE.finditer(text)
                    })
                    for function in functions:
                        volatile[function] += 1
                    if functions:
                        volatile_cells.append(coordinate)
                    if _EXTERNAL_FORMULA_RE.search(text):
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
                functions = sorted({
                    match.group(1).upper()
                    for match in _VOLATILE_RE.finditer(text)
                })
                for function in functions:
                    volatile[function] += 1
                if functions:
                    volatile_cells.append(coordinate)
                if _EXTERNAL_FORMULA_RE.search(text):
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
                relationship_types.extend(
                    relation.attrib.get("Type", "")
                    for relation in relation_root.findall(
                        f"{{{_REL_NS}}}Relationship"
                    )
                )
            external_link_relations = sum(
                relation_type.endswith("/externalLink")
                for relation_type in relationship_types
            )
            ole_relations = sum(
                relation_type.endswith("/oleObject")
                for relation_type in relationship_types
            )
            connection_count = 0
            if "xl/connections.xml" in names:
                connection_count = len(
                    _parse_xml(archive, "xl/connections.xml").findall(
                        ".//{*}connection"
                    )
                )
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
        "formula_caches_missing": missing_cache,
        "external_links": len(external_link_parts),
        "external_link_relationships": external_link_relations,
        "external_formula_references": len(external_formula_cells),
        "connections": connection_count,
        "macro_parts": len(macro_parts),
        "ole_parts": len(ole_parts),
        "ole_relationships": ole_relations,
        "volatile_formula_cells": len(volatile_cells),
        "data_table_formulas": data_tables,
    }
    unsupported = []
    if macro_parts or macro_content_type:
        unsupported.append("macros_present")
    if ole_parts or ole_relations:
        unsupported.append("ole_objects_present")
    if external_link_parts or external_link_relations or external_formula_cells:
        unsupported.append("external_links_present")
    if "xl/connections.xml" in names:
        unsupported.append("connections_present")
    if data_tables:
        unsupported.append("data_tables_present")
    if volatile_cells:
        unsupported.append("volatile_formulas_present")

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

    if unsupported:
        route = "unsupported"
        reasons = unsupported
    elif incomplete:
        route = "insufficient_evidence"
        reasons = incomplete
    elif recalc:
        route = "recalc_candidate"
        reasons = recalc
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
    report["report_sha256"] = _report_id(report)
    return report


observe_workbook = inspect_workbook


def validate_report(report: dict, source_path: str | Path | None = None) -> dict:
    if not isinstance(report, dict):
        raise SourceHealthError("source-health report must be an object")
    failures = []
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append("schema_version")
    if report.get("policy_version") != POLICY_VERSION:
        failures.append("policy_version")
    if report.get("route") not in ROUTES:
        failures.append("route")
    if report.get("routing") != report.get("route"):
        failures.append("routing")
    if not isinstance(report.get("reason_codes"), list):
        failures.append("reason_codes")
    if report.get("report_sha256") != _report_id(report):
        failures.append("report_sha256")
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
