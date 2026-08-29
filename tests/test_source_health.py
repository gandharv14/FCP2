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
from xl_source_inventory import (
    SourceInventoryError,
    build_inventory_manifest,
    main as inventory_main,
    read_workbook_ids,
    validate_inventory_manifest,
)


def _workbook(
    path: Path,
    *,
    formula: str = "A1+1",
    cache: str | None = "2",
    calc: str = 'calcMode="auto"',
    external: bool = False,
    defined_name: str | None = None,
    sheet_xml: str | None = None,
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
    sheet = sheet_xml or (
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


def _shared_offset_workbook(path: Path) -> None:
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
        'main"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1">'
        '<f t="shared" si="0" ref="B1:B2">OFFSET(A1,0,0)</f><v>1</v></c>'
        '</row><row r="2"><c r="A2"><v>2</v></c><c r="B2">'
        '<f t="shared" si="0"/><v>2</v></c></row></sheetData></worksheet>'
    )
    _workbook(path, sheet_xml=sheet)


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


def test_allowlisted_volatile_formula_routes_to_restricted_pass(tmp_path):
    source = tmp_path / "volatile.xlsx"
    _workbook(source, formula="NOW()")

    report = inspect_workbook(source)

    assert report["route"] == "restricted_pass"
    assert report["reason_codes"] == ["worksheet_now"]
    assert report["restriction_events"] == [{
        "allowed": True,
        "event": "volatile_function",
        "function": "NOW",
        "location": "Sheet1!B1",
        "reason": "worksheet_now",
        "scope": "worksheet",
        "token_offset": 0,
    }]
    assert report["proven_stale_cache"] is False


def test_shared_formula_follower_is_expanded_and_classified(tmp_path):
    source = tmp_path / "shared-offset.xlsx"
    _shared_offset_workbook(source)

    report = inspect_workbook(source)

    assert report["route"] == "restricted_pass"
    assert report["reason_codes"] == ["worksheet_offset"]
    assert report["counts"]["formula_cells"] == 2
    assert report["counts"]["shared_formula_followers_expanded"] == 1
    assert report["counts"]["shared_formula_followers_unexpanded"] == 0
    assert report["restriction_events"] == [
        {
            "allowed": True,
            "event": "volatile_function",
            "function": "OFFSET",
            "location": "Sheet1!B1",
            "reason": "worksheet_offset",
            "scope": "worksheet",
            "token_offset": 0,
        },
        {
            "allowed": True,
            "event": "volatile_function",
            "function": "OFFSET",
            "location": "Sheet1!B2",
            "reason": "worksheet_offset",
            "scope": "worksheet",
            "token_offset": 0,
        },
    ]


def test_shared_formula_without_master_is_explicitly_unsupported(tmp_path):
    source = tmp_path / "orphan-shared.xlsx"
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
        'main"><sheetData><row r="1"><c r="B1">'
        '<f t="shared" si="7"/><v>1</v></c></row></sheetData></worksheet>'
    )
    _workbook(source, sheet_xml=sheet)

    report = inspect_workbook(source)

    assert report["route"] == "unsupported"
    assert report["reason_codes"] == ["shared_formula_followers_unexpanded"]
    assert report["counts"]["shared_formula_followers_expanded"] == 0
    assert report["counts"]["shared_formula_followers_unexpanded"] == 1
    assert report["restriction_events"] == [{
        "allowed": False,
        "event": "shared_formula_follower_unexpanded",
        "location": "Sheet1!B1",
        "reason": "shared_formula_follower_unexpanded",
        "scope": "worksheet",
        "shared_index": "7",
        "token_offset": 0,
    }]


def test_volatile_defined_name_is_unsupported(tmp_path):
    source = tmp_path / "defined-name.xlsx"
    _workbook(source, defined_name="NOW()")

    report = inspect_workbook(source)

    assert report["route"] == "unsupported"
    assert report["counts"]["defined_name_formulas"] == 1
    assert report["reason_codes"] == ["unsupported_volatile_formulas_present"]


@pytest.mark.parametrize(
    ("formula", "route", "event"),
    [
        ('"label [1]"', "restricted_pass", "false_external_detection"),
        ("SUM(Table1[Amount])", "unsupported", "structured_reference"),
        ("'[Book.xlsx]Sheet 1'!A1", "unsupported", "external_workbook_reference"),
        ('INDIRECT("A1")', "restricted_pass", "volatile_function"),
        ('INDIRECT("R1C1",FALSE)', "unsupported", "volatile_function"),
        ('CELL("filename",A1)', "restricted_pass", "volatile_function"),
        ('CELL("address",A1)', "unsupported", "volatile_function"),
    ],
)
def test_formula_tokens_are_cross_checked(tmp_path, formula, route, event):
    source = tmp_path / "tokens.xlsx"
    _workbook(source, formula=formula)

    report = inspect_workbook(source)

    assert report["route"] == route
    assert report["restriction_events"][0]["event"] == event


def test_restricted_plus_missing_cache_is_unsupported(tmp_path):
    source = tmp_path / "mixed.xlsx"
    _workbook(source, formula="OFFSET(A1,0,0)", cache=None)

    report = inspect_workbook(source)

    assert report["route"] == "unsupported"
    assert "mixed_restricted_recalc" in report["reason_codes"]


def test_restriction_ledger_is_complete_not_sample_limited(tmp_path):
    source = tmp_path / "many.xlsx"
    _workbook(source)
    with zipfile.ZipFile(source, "a") as archive:
        for index in range(25):
            archive.writestr(
                f"xl/externalLinks/externalLink{index}.xml",
                '<externalLink xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"/>',
            )

    report = inspect_workbook(source)
    events = [
        item for item in report["restriction_events"]
        if item["event"] == "package_external_link_part"
    ]

    assert len(events) == 25
    assert report["restriction_events_sha256"]


def test_inventory_is_deterministic_and_does_not_mutate_sources(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "0001.xlsx"
    _workbook(source)
    original = source.read_bytes()
    report_root = tmp_path / "reports"
    report_root.mkdir()
    (report_root / "0001.json").write_text('{"status":"ok"}\n')

    first = build_inventory_manifest(
        source_root,
        workbook_ids=["0001"],
        report_roots=[report_root],
    )
    second = build_inventory_manifest(
        source_root,
        workbook_ids=["0001"],
        report_roots=[report_root],
    )

    assert first == second
    assert first["workbooks"][0]["workbook_id"] == "0001"
    assert first["workbooks"][0]["health_report"] == inspect_workbook(source)
    assert first["workbooks"][0]["classification"] == "conversion_unverified"
    assert source.read_bytes() == original
    assert validate_inventory_manifest(
        first,
        source_root=source_root,
        expected_workbook_ids=["0001"],
    ) == first


def test_inventory_selects_exact_explicit_cohort(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    for workbook_id in ("0001", "0002", "0003"):
        _workbook(source_root / f"{workbook_id}.xlsx")
    ids_path = tmp_path / "cohort.json"
    ids_path.write_text(json.dumps({"workbook_ids": ["0003", "0001"]}))

    requested = read_workbook_ids(ids_path)
    manifest = build_inventory_manifest(
        source_root,
        workbook_ids=requested,
    )

    assert manifest["cohort"]["workbook_ids"] == ["0001", "0003"]
    assert [item["workbook_id"] for item in manifest["workbooks"]] == [
        "0001",
        "0003",
    ]
    assert manifest["cohort"]["size"] == 2
    assert manifest["cohort"]["cohort_sha256"]


def test_inventory_cli_uses_id_manifest_instead_of_directory_scan(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    _workbook(source_root / "0001.xlsx")
    _workbook(source_root / "0002.xlsx")
    ids_path = tmp_path / "ids.txt"
    ids_path.write_text("0002\n", encoding="utf-8")
    output = tmp_path / "inventory.json"

    result = inventory_main([
        str(source_root),
        "-o",
        str(output),
        "--id-manifest",
        str(ids_path),
    ])
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert result == 0
    assert manifest["cohort"]["workbook_ids"] == ["0002"]


def test_inventory_rejects_missing_duplicate_and_unexpected_ids(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    _workbook(source_root / "0001.xlsx")

    with pytest.raises(SourceInventoryError, match="explicit workbook cohort"):
        build_inventory_manifest(source_root)
    with pytest.raises(SourceInventoryError, match="duplicate"):
        build_inventory_manifest(
            source_root,
            workbook_ids=["0001", "0001"],
        )
    with pytest.raises(SourceInventoryError, match="absent"):
        build_inventory_manifest(source_root, workbook_ids=["9999"])
    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir()
    _workbook(duplicate_root / "0001.xlsx")
    with pytest.raises(SourceInventoryError, match="ambiguous"):
        build_inventory_manifest(tmp_path, workbook_ids=["0001"])

    manifest = build_inventory_manifest(source_root, workbook_ids=["0001"])
    with pytest.raises(SourceInventoryError, match="expected_cohort"):
        validate_inventory_manifest(
            manifest,
            expected_workbook_ids=["0001", "0002"],
        )


def test_inventory_validation_detects_source_drift(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "0001.xlsx"
    _workbook(source)
    manifest = build_inventory_manifest(source_root, workbook_ids=["0001"])

    _workbook(source, formula="A1+2", cache="3")

    with pytest.raises(SourceInventoryError, match="source_binding"):
        validate_inventory_manifest(manifest, source_root=source_root)


def test_inventory_embeds_complete_live_health_report(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "0001.xlsx"
    _workbook(source)
    with zipfile.ZipFile(source, "a") as archive:
        for index in range(25):
            archive.writestr(
                f"xl/externalLinks/externalLink{index}.xml",
                '<externalLink xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"/>',
            )

    manifest = build_inventory_manifest(source_root, workbook_ids=["0001"])
    record = manifest["workbooks"][0]
    health = inspect_workbook(source)

    assert record["health_report"] == health
    assert len(record["health_report"]["restriction_events"]) == 25
    assert record["restriction_events_sha256"] == health[
        "restriction_events_sha256"
    ]
    assert record["function_counts"] == health["volatile_functions"]


def test_conversion_report_does_not_claim_equivalence(tmp_path):
    source_root = tmp_path / "sources"
    legacy_root = tmp_path / "legacy"
    report_root = tmp_path / "reports"
    source_root.mkdir()
    legacy_root.mkdir()
    report_root.mkdir()
    _workbook(source_root / "0001.xlsx")
    (legacy_root / "0001.xls").write_bytes(b"legacy workbook bytes")
    (report_root / "0001-conversion.json").write_text(
        '{"claimed_equivalent":true}\n',
        encoding="utf-8",
    )

    manifest = build_inventory_manifest(
        source_root,
        workbook_ids=["0001"],
        legacy_source_roots=[legacy_root],
        conversion_report_roots=[report_root],
    )
    record = manifest["workbooks"][0]

    assert record["classification"] == "conversion_unverified"
    assert record["conversion"]["status"] == "conversion_unverified"
    assert record["conversion"]["original_source"]["sha256"]
    assert record["conversion"]["reports"][0]["sha256"]


def test_colocated_legacy_source_marks_xlsx_conversion_unverified(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    _workbook(source_root / "0001.xlsx")
    (source_root / "0001.xlsm").write_bytes(b"legacy macro workbook bytes")

    manifest = build_inventory_manifest(
        source_root,
        workbook_ids=["0001"],
        legacy_source_roots=[source_root],
    )

    record = manifest["workbooks"][0]
    assert record["classification"] == "conversion_unverified"
    assert record["conversion"]["original_source"]["path"] == "0001.xlsm"


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

