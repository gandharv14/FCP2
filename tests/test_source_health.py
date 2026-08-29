from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from xl_seg.evaluate import workbook_calculation_metadata
from xl_source_health import (
    SourceHealthError,
    atomic_write_report,
    inspect_workbook,
    read_report,
)


def _workbook(
    path: Path,
    *,
    formula: str = "A1+1",
    cache: str | None = "2",
    calc: str = 'calcMode="auto"',
    external: bool = False,
    defined_name: str | None = None,
) -> None:
    value = "" if cache is None else f"<v>{cache}</v>"
    formula_text = "[1]Sheet1!A1" if external else formula
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships"><sheets><sheet name="Sheet1" '
        'sheetId="1" r:id="rId1"/></sheets>'
        + (
            f'<definedNames><definedName name="Clock">{defined_name}</definedName>'
            "</definedNames>"
            if defined_name is not None
            else ""
        )
        + f"<calcPr {calc}/></workbook>"
    )
    relationships = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
        'main"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1">'
        f"<f>{formula_text}</f>{value}</c></row></sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"/>',
        )
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def test_calculation_metadata_includes_recalc_flags(tmp_path):
    source = tmp_path / "flags.xlsx"
    _workbook(
        source,
        calc='calcMode="manual" fullCalcOnLoad="1" forceFullCalc="false"',
    )

    metadata = workbook_calculation_metadata(source)

    assert metadata.calc_mode == "manual"
    assert metadata.calc_mode_origin == "explicit"
    assert metadata.full_calc_on_load is True
    assert metadata.force_full_calc is False


def test_missing_formula_cache_routes_to_recalc_without_proving_stale(tmp_path):
    source = tmp_path / "missing-cache.xlsx"
    _workbook(source, cache=None)

    report = inspect_workbook(source)

    assert report["route"] == "recalc_candidate"
    assert report["counts"]["formula_caches_missing"] == 1
    assert report["proven_stale_cache"] is False
    assert (
        report["diagnostics"]["classification"]["disposition"]
        == "recalc_required"
    )


def test_external_formula_is_unsupported_and_report_is_bound(tmp_path):
    source = tmp_path / "external.xlsx"
    _workbook(source, external=True)
    report_path = tmp_path / "health.json"
    report = inspect_workbook(source)
    atomic_write_report(report_path, report)

    assert report["route"] == "unsupported"
    assert "external_links_present" in report["reason_codes"]
    assert read_report(report_path, source) == report

    tampered = json.loads(report_path.read_text(encoding="utf-8"))
    tampered["route"] = "pass"
    report_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(SourceHealthError, match="report_sha256"):
        read_report(report_path, source)


def test_volatile_formula_is_unsupported_not_recalc_candidate(tmp_path):
    source = tmp_path / "volatile.xlsx"
    _workbook(source, formula="NOW()")

    report = inspect_workbook(source)

    assert report["route"] == "unsupported"
    assert report["reason_codes"] == ["volatile_formulas_present"]
    assert report["proven_stale_cache"] is False


def test_volatile_defined_name_is_unsupported(tmp_path):
    source = tmp_path / "defined-name.xlsx"
    _workbook(source, defined_name="NOW()")

    report = inspect_workbook(source)

    assert report["route"] == "unsupported"
    assert report["counts"]["defined_name_formulas"] == 1
    assert report["reason_codes"] == ["volatile_formulas_present"]


def test_unknown_iteration_metadata_is_insufficient_evidence(tmp_path):
    source = tmp_path / "unknown-iteration.xlsx"
    _workbook(source, calc='calcMode="auto" iterate="maybe"')

    report = inspect_workbook(source)

    assert report["route"] == "insufficient_evidence"
    assert "calculation_settings_unrecognized" in report["reason_codes"]


def test_symlink_is_rejected_without_hashing_target(tmp_path):
    target = tmp_path / "target.xlsx"
    _workbook(target)
    linked = tmp_path / "linked.xlsx"
    os.symlink(target, linked)

    report = inspect_workbook(linked)

    assert report["route"] == "insufficient_evidence"
    assert report["reason_codes"] == ["source_symlink"]
    assert report["source_sha256"] is None


def test_duplicate_zip_members_fail_closed(tmp_path):
    source = tmp_path / "duplicate.xlsx"
    _workbook(source)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")

    report = inspect_workbook(source)

    assert report["route"] == "insufficient_evidence"
    assert report["reason_codes"] == ["ooxml_unreadable"]

