from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import pytest

from xl_volatile_formula_remediation import (
    RemediationError,
    apply_plan,
    build_plan,
    verify_manifest,
)


def _workbook(path: Path, *, dynamic: bool = False) -> None:
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships"><sheets>'
        '<sheet name="Model" sheetId="1" r:id="rId1"/>'
        '<sheet name="Target 1" sheetId="2" r:id="rId2"/>'
        '<sheet name="Target 2" sheetId="3" r:id="rId3"/>'
        '</sheets><definedNames>'
        '<definedName name="Dead">OFFSET(#REF!,0,0)</definedName>'
        '<definedName name="_xlnm.Print_Area" localSheetId="0">'
        'Model!$A$1:$E$3</definedName>'
        '</definedNames><calcPr calcMode="auto"/></workbook>'
    )
    relationships = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet3.xml"/>'
        '</Relationships>'
    )
    indirect = (
        "INDIRECT(A1)"
        if dynamic
        else 'INDIRECT(C$1&amp;"!"&amp;$A2)'
    )
    model = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        '<row r="1">'
        '<c r="C1" t="s"><v>0</v></c>'
        '<c r="D1" t="s"><v>1</v></c>'
        '<c r="E1"><f>TODAY()</f><v>45200</v></c>'
        '</row><row r="2">'
        '<c r="A2" t="s"><v>2</v></c>'
        f'<c r="C2"><f t="shared" si="1" ref="C2:D2">{indirect}</f><v>5</v></c>'
        '<c r="D2"><f t="shared" si="1"/><v>6</v></c>'
        '</row><row r="3">'
        '<c r="A3"><v>0</v></c>'
        '<c r="C3"><f t="shared" si="2" ref="C3:D3">'
        'OFFSET(C$3,$A$3,)</f><v>5</v></c>'
        '<c r="D3"><f t="shared" si="2"/><v>6</v></c>'
        '</row></sheetData></worksheet>'
    )
    target = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData><row r="1">'
        '<c r="A1"><v>5</v></c></row></sheetData></worksheet>'
    )
    target_two = target.replace("<v>5</v>", "<v>6</v>")
    shared_strings = (
        '<sst xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" count="3" uniqueCount="3">'
        '<si><t>Target 1</t></si><si><t>Target 2</t></si><si><t>A1</t></si>'
        '</sst>'
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="xml" '
        'ContentType="application/xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", model)
        archive.writestr("xl/worksheets/sheet2.xml", target)
        archive.writestr("xl/worksheets/sheet3.xml", target_two)


def test_closed_rewrites_preserve_caches_and_untouched_members(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    _workbook(source)

    plan = build_plan(source)

    assert plan["status"] == "eligible"
    assert plan["unresolved"] == []
    assert plan["action_counts"] == {
        "dead-defined-name/v1": 1,
        "indirect-static-concat-to-a1/v1": 2,
        "offset-blank-column-to-index/v1": 1,
        "unreferenced-today-to-as-of-constant/v1": 1,
    }

    manifest = apply_plan(source, plan, output)
    verification = verify_manifest(source, output, plan, manifest)

    assert verification["status"] == "verified"
    assert verification["route"] == "pass"
    assert set(manifest["changed_members"]) == {
        "xl/workbook.xml",
        "xl/worksheets/sheet1.xml",
    }
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        for name in (
            "[Content_Types].xml",
            "xl/_rels/workbook.xml.rels",
            "xl/sharedStrings.xml",
            "xl/worksheets/sheet2.xml",
            "xl/worksheets/sheet3.xml",
        ):
            assert before.read(name) == after.read(name)
        source_sheet = before.read("xl/worksheets/sheet1.xml")
        output_sheet = after.read("xl/worksheets/sheet1.xml")
        for cache in (b"<v>45200</v>", b"<v>5</v>", b"<v>6</v>"):
            assert source_sheet.count(cache) == output_sheet.count(cache)
        assert b"INDIRECT" not in output_sheet
        assert b"OFFSET" not in output_sheet
        assert b"TODAY" not in output_sheet
        assert b"'Target 1'!A1" in output_sheet
        assert b"'Target 2'!A1" in output_sheet
        assert b"INDEX(" in output_sheet
        assert b'name="Dead"' not in after.read("xl/workbook.xml")
        assert b'_xlnm.Print_Area' in after.read("xl/workbook.xml")


def test_dynamic_indirect_fails_closed_without_output(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    _workbook(source, dynamic=True)

    plan = build_plan(source)

    assert plan["status"] == "ineligible"
    assert any(item["kind"] == "indirect_unresolved" for item in plan["unresolved"])
    with pytest.raises(RemediationError, match="not eligible"):
        apply_plan(source, plan, output)
    assert not output.exists()


def test_tampered_manifest_is_rejected(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    _workbook(source)
    plan = build_plan(source)
    manifest = apply_plan(source, plan, output)
    tampered = copy.deepcopy(manifest)
    tampered["output"]["sha256"] = "0" * 64

    with pytest.raises(RemediationError, match="invalid or tampered"):
        verify_manifest(source, output, plan, tampered)


def test_harbor_skills_bind_intake_remediation_contract():
    root = Path(__file__).resolve().parents[1]
    fixer = (
        root / ".cursor/skills/harbor-volatile-formula-fixer/SKILL.md"
    ).read_text(encoding="utf-8")
    orchestrator = (
        root / ".cursor/skills/harbor-orchestrator/SKILL.md"
    ).read_text(encoding="utf-8")
    warden = (
        root / ".cursor/skills/harbor-source-warden/SKILL.md"
    ).read_text(encoding="utf-8")
    creator = (
        root / ".cursor/skills/create-harbor-task/SKILL.md"
    ).read_text(encoding="utf-8")

    assert len(fixer.splitlines()) < 500
    assert "xl_volatile_formula_remediation.py plan" in fixer
    assert "never overwrite the discovered source" in fixer.lower()
    for binding in (
        "original_raw_source",
        "source_health_before",
        "source_remediation_plan",
        "source_remediation_manifest",
    ):
        assert binding in orchestrator
    assert "xl_volatile_formula_remediation.py verify" in warden
    assert "RAW_SOURCE_FILE=\"$SOURCE_REMEDIATION_RUN/$WB.xlsx\"" in creator
