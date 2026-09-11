import csv
import json
import re
from pathlib import Path
import zipfile

import openpyxl
from openpyxl.worksheet.formula import DataTableFormula
import pytest

from xl_presentation_tables import materialize, verify_materialization, check_exclusion


def source_fixture(tmp_path, *, answer_in_table=False, in_cone=False):
    source = tmp_path / "0001.xlsx"
    book = openpyxl.Workbook()
    book.calculation.fullCalcOnLoad = False
    sheet = book.active
    sheet.title = "S"
    sheet["A1"] = 2
    sheet["B1"] = "=D3+1" if in_cone else "=A1*3"
    sheet["D3"] = DataTableFormula(ref="D3:E3", r1="A1")
    sheet["E3"] = 9
    book.save(source)
    book.close()
    with zipfile.ZipFile(source) as archive:
        parts = {i.filename:archive.read(i.filename) for i in archive.infolist()}
    xml = parts["xl/worksheets/sheet1.xml"].decode()
    xml = re.sub(r'<v\s*/>', '<v></v>', xml)
    xml = xml.replace('<f>A1*3</f><v></v>', '<f>A1*3</f><v>6</v>')
    xml = xml.replace('<f>D3+1</f><v></v>', '<f>D3+1</f><v>6</v>')
    xml = re.sub(r'(<f\b[^>]*r1="A1"[^>]*(?:/>|></f>))<v></v>',
                 lambda m: m.group(1) + '<v>' + ('6' if answer_in_table else '5') + '</v>', xml)
    parts["xl/worksheets/sheet1.xml"] = xml.encode()
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    curation = tmp_path / "curation.toml"
    curation.write_text('[[output]]\nband = "S!B1"\ninclude = true\nname = "Result"\n')
    return source, curation


def test_presentation_table_requires_live_proof_and_rejects_altered_evidence(tmp_path):
    source, curation = source_fixture(tmp_path)
    candidate, receipt = materialize(source, "0001", curation, tmp_path / "normalized")
    assert verify_materialization(source, candidate, curation, receipt)["status"] == "verified"
    assert receipt["ordered_outputs"] == ["S!B1"]
    receipt["tables"] = []
    with pytest.raises(ValueError, match="evidence"):
        verify_materialization(source, candidate, curation, receipt)


def test_resume_reuses_only_current_hash_bound_table_evidence(tmp_path, monkeypatch):
    from scripts.corpus_build import reusable_table_candidate
    from scripts.corpus import digest
    from xl_seg import publication
    source, curation = source_fixture(tmp_path)
    candidate, receipt = materialize(source, '0001', curation, tmp_path / 'normalized')
    manifest = candidate.parent / 'manifest.json'
    record = dict(original_sha256=digest(source), candidate=str(candidate),
                  candidate_sha256=digest(candidate), curation=str(curation),
                  manifest=str(manifest), manifest_sha256=digest(manifest),
                  policy_version=receipt['policy_version'])
    ledger = {'build_history': [{'normalization': record}]}
    item = {'path': str(source), 'sha256': digest(source)}
    assert reusable_table_candidate(item, curation, ledger) == record
    changed = tmp_path / 'changed.py'
    changed.write_text('new verifier')
    with monkeypatch.context() as patch:
        patch.setattr(publication, 'verifier_paths', lambda: (changed,))
        assert reusable_table_candidate(item, curation, ledger) is None
    original_curation = curation.read_bytes()
    curation.write_text('different selection')
    assert reusable_table_candidate(item, curation, ledger) is None
    curation.write_bytes(original_curation)
    manifest.write_text('{}')
    assert reusable_table_candidate(item, curation, ledger) is None


def test_materialization_rejects_changed_verifier_or_parser(tmp_path, monkeypatch):
    from xl_seg import publication
    import xl_source_publication as source_publication
    source, curation = source_fixture(tmp_path)
    candidate, receipt = materialize(source, "0001", curation, tmp_path / "normalized")
    assert receipt["policy_version"] == "presentation-data-tables/v2"
    replacement = tmp_path / "changed.py"
    replacement.write_text("changed implementation")
    with monkeypatch.context() as patch:
        patch.setattr(publication, "verifier_paths", lambda: (replacement,))
        with pytest.raises(ValueError, match="current evidence"):
            verify_materialization(source, candidate, curation, receipt)
    with monkeypatch.context() as patch:
        patch.setattr(source_publication, "_default_builder_paths", lambda: (replacement,))
        with pytest.raises(ValueError, match="AST builder changed"):
            verify_materialization(source, candidate, curation, receipt)
    assert verify_materialization(source, candidate, curation, receipt)["status"] == "verified"


def test_legacy_materialization_without_current_proof_cannot_be_reapproved(tmp_path):
    from xl_volatile_formula_remediation import canonical_bytes, sha256_bytes
    source, curation = source_fixture(tmp_path)
    candidate, receipt = materialize(source, "0001", curation, tmp_path / "normalized")
    receipt.pop("inspection_ast_provenance")
    receipt["policy_version"] = "presentation-data-tables/v1"
    receipt["manifest_sha256"] = sha256_bytes(canonical_bytes(
        {key: value for key, value in receipt.items() if key != "manifest_sha256"}))
    with pytest.raises(ValueError, match="evidence"):
        verify_materialization(source, candidate, curation, receipt)


def test_verifier_fingerprint_covers_parser_and_all_segmentation_modules():
    from xl_seg import publication
    paths = set(publication.verifier_paths())
    root = Path(publication.__file__).resolve().parent
    assert root.parent / "xl_ast_graph.py" in paths
    assert root.parent / "xl_segment.py" in paths
    assert set(root.glob("*.py")) <= paths
    assert {"condense.py", "frontier.py", "model.py", "partition.py"} <= {p.name for p in paths}


def test_transformed_inventory_binds_original_and_revalidates_full_proof(tmp_path):
    from xl_source_inventory import (transformed_workbook_record,
        assemble_inventory_manifest, validate_inventory_manifest, SourceInventoryError)
    from xl_inventory_approval import original_source_ledger
    from xl_source_health import sha256_file
    source, curation = source_fixture(tmp_path)
    candidate, receipt = materialize(source, '0001', curation, tmp_path / 'normalized')
    record = transformed_workbook_record('0001', source, candidate, curation, receipt)
    manifest = assemble_inventory_manifest([record])
    ledger = original_source_ledger(manifest)[0]
    assert ledger['original_source']['sha256'] == sha256_file(source)
    assert ledger['source_sha256'] == sha256_file(candidate)
    assert ledger['original_source']['sha256'] != ledger['source_sha256']
    # Rehashing an altered receipt cannot replace the independent gate.
    from xl_source_inventory import _object_hash
    bad = json.loads(json.dumps(record))
    bad['transformation']['receipt']['ordered_outputs'] = []
    bad['transformation']['receipt_sha256'] = _object_hash(bad['transformation']['receipt'])
    with pytest.raises(SourceInventoryError, match='transformation'):
        assemble_inventory_manifest([bad])
    source.unlink()
    with pytest.raises(SourceInventoryError, match='transformation'):
        validate_inventory_manifest(manifest)


def test_in_cone_sensitivity_result_cannot_be_supplied_as_input(tmp_path):
    source, curation = source_fixture(tmp_path, in_cone=True)
    with pytest.raises((ValueError, SystemExit)):
        materialize(source, "0001", curation, tmp_path / "normalized")
    nodes = list(csv.DictReader((tmp_path / "normalized/ast/0001/nodes.csv").open()))
    assert all(next(n for n in nodes if n["id"] == cell)["kind"] == "formula" for cell in ("S!D3", "S!E3"))


def test_out_of_cone_table_still_cannot_disclose_answer(tmp_path):
    source, curation = source_fixture(tmp_path, answer_in_table=True)
    with pytest.raises(ValueError, match="discloses a graded"):
        materialize(source, "0001", curation, tmp_path / "normalized")


def test_materialization_requires_retained_targets(tmp_path):
    source, _ = source_fixture(tmp_path)
    with pytest.raises(ValueError, match="retained curation"):
        materialize(source, "0001", tmp_path / "missing", tmp_path / "normalized")
