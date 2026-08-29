from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from replay_segmentation_verification import (
    DEFAULT_MANIFEST,
    EXPECTED_PRIMARY_COUNTS,
    build_replay_report,
    load_manifest,
)
from xl_seg import diagnostics, partition, project
from xl_seg.evaluate import CalculationMetadata, Evaluator
from xl_seg.model import Edge, Graph, Node
from xl_segment import _curated_output_identity, _verify


FORENSIC_IDS = {
    "0222", "0225", "0260", "0327", "0330", "0331", "0332", "0333", "0339",
    "0343", "0346", "0348", "0355", "0357", "0361", "0367", "0369", "0376",
    "0379", "0384", "0388", "0390", "0391", "0392", "0393", "0395", "0399",
    "0402", "0403", "0404", "0407", "0435", "0439", "0443", "0444", "0452",
    "0460", "0464", "0465", "0466", "0471", "0475", "0479", "0482", "0483",
    "0486", "0487", "0500", "0501", "0504", "0508", "0509", "0510",
}


def _node(node_id, kind, value):
    sheet, coordinate = node_id.split("!", 1)
    column = ord(coordinate[0]) - ord("A") + 1
    return Node(
        id=node_id,
        kind=kind,
        sheet=sheet,
        coordinate=coordinate,
        row=int(coordinate[1:]),
        col=column,
        owner="",
        op="",
        op_kind="",
        arity="",
        expr="",
        label=node_id,
        formula=(f"={value}" if kind == "formula" else ""),
        value=str(value),
        in_cycle=False,
    )


def _verification_fixture(output_count=1):
    source = _node("Sheet!A1", "input", 1)
    outputs = [_node(f"Sheet!B{row}", "formula", 1) for row in range(1, output_count + 1)]
    edges = [
        Edge(
            source=source.id,
            target=output.id,
            role="identity",
            arg_index=0,
            op="",
            cell=output.id,
            ref=source.id,
            via_range="",
            cross_sheet=False,
        )
        for output in outputs
    ]
    graph = Graph("book", {node.id: node for node in [source, *outputs]}, edges)
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    cg = project.build(graph)
    bands = {
        "input-band": SimpleNamespace(cells=[source.id]),
        **{
            f"output-band-{index}": SimpleNamespace(cells=[node.id])
            for index, node in enumerate(outputs)
        },
    }
    output_bands = set(bands) - {"input-band"}
    cd = SimpleNamespace(
        comp_members={
            "input-comp": {"input-band"},
            "output-comp": output_bands,
        }
    )
    part = SimpleNamespace(
        bucket={
            "input-comp": partition.INPUT,
            "output-comp": partition.OUTPUT,
        }
    )
    return graph, cg, SimpleNamespace(bands=bands), cd, part, source, outputs


class FakeExpectedCache:
    def __init__(self, value):
        self.value = value
        self.reads = {}

    def __call__(self, sheet, row, col):
        cell = f"{sheet}!B{row}"
        self.reads[cell] = self.reads.get(cell, 0) + 1
        return self.value


def _evidence_paths(tmp_path):
    source = tmp_path / "book.xlsx"
    source.write_bytes(b"workbook")
    ast_dir = tmp_path / "ast"
    ast_dir.mkdir()
    (ast_dir / "nodes.csv").write_text("nodes", encoding="utf-8")
    (ast_dir / "edges.csv").write_text("edges", encoding="utf-8")
    curation = tmp_path / "curation.toml"
    curation.write_text("[[output]]\n", encoding="utf-8")
    return source, ast_dir, curation


def test_strict_proof_never_calls_expected_value_oracle():
    calls = []
    graph = Graph("book", {}, [])
    evaluator = Evaluator(
        graph,
        project.CellGraph(graph, {}),
        oracle=lambda *address: calls.append(address) or 42,
        strict_proof=True,
    )
    evaluator._current_cell = "Sheet!A1"

    assert evaluator._read("Sheet!Z9") is None
    assert calls == []
    assert evaluator.oracle_reads == {}
    assert evaluator.missing_reads == {"Sheet!Z9": {"Sheet!A1"}}


def test_strict_proof_rejects_requested_formula_cached_values():
    formula = _node("Sheet!B1", "formula", 99)
    graph = Graph("book", {formula.id: formula}, [])
    result = Evaluator(
        graph,
        project.build(graph),
        strict_proof=True,
    ).run({formula.id})

    assert formula.id in result.unresolved
    assert result.values[formula.id].reason == "no-ast-root"
    rejected = result.seed_provenance["categories"]["formula_cache_rejected"]
    assert rejected == {"count": 1, "sample": [formula.id]}


def test_versioned_contract_reports_status_counts_and_provenance(tmp_path):
    graph, cg, bg, cd, part, source, outputs = _verification_fixture()
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=CalculationMetadata(available=True, iterate=False),
    ).run({source.id})
    source_path, ast_dir, curation = _evidence_paths(tmp_path)
    cache = FakeExpectedCache(1)

    report = _verify(
        cg,
        result,
        {source.id},
        {node.id for node in outputs},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=cache,
        calculation=CalculationMetadata(available=True, iterate=False),
        source_path=source_path,
        ast_dir=ast_dir,
        curation_path=curation,
    )

    assert report["schema_version"] == diagnostics.SCHEMA_VERSION
    assert report["status"] == "pass"
    assert report["disposition"] == "pass"
    assert report["cache_policy"] == "retain_as_oracle"
    assert report["blocking_reasons"] == []
    assert report["counts"]["outputs"]["eligible"] == 1
    assert report["counts"]["cache_reads"] == {
        "proof": 0,
        "post_proof_comparison": 1,
    }
    assert report["provenance"]["proof"]["strict"] is True
    assert report["missing_evidence"] == []
    assert report["generation_id"]


def test_strict_verification_rejects_missing_ast_capabilities(tmp_path):
    graph, cg, bg, cd, part, source, outputs = _verification_fixture()
    graph.capabilities = {
        "required_node_fields": False,
        "schema_version_v2": False,
    }
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=CalculationMetadata(available=True, iterate=False),
    ).run({source.id})
    source_path, ast_dir, curation = _evidence_paths(tmp_path)

    report = _verify(
        cg,
        result,
        {source.id},
        {node.id for node in outputs},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=FakeExpectedCache(1),
        calculation=CalculationMetadata(available=True, iterate=False),
        source_path=source_path,
        ast_dir=ast_dir,
        curation_path=curation,
    )

    assert report["status"] == "fail"
    assert report["disposition"] == "insufficient_evidence"
    assert report["passed"] is False
    assert "missing_required_proof_evidence" in report["blocking_reasons"]


def test_strict_verification_rejects_unknown_output_cone_operation(tmp_path):
    graph, cg, bg, cd, part, source, outputs = _verification_fixture()
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=CalculationMetadata(available=True, iterate=False),
    ).run({source.id})
    result.coverage["unknown_ops"] = {"UNSUPPORTED": 1}
    source_path, ast_dir, curation = _evidence_paths(tmp_path)

    report = _verify(
        cg,
        result,
        {source.id},
        {node.id for node in outputs},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=FakeExpectedCache(1),
        calculation=CalculationMetadata(available=True, iterate=False),
        source_path=source_path,
        ast_dir=ast_dir,
        curation_path=curation,
    )

    assert report["status"] == "fail"
    assert report["passed"] is False
    assert "unknown_operation" in report["blocking_reasons"]


def test_curated_output_identity_does_not_expand_scc_sibling_bands():
    bg = SimpleNamespace(bands={
        "chosen": SimpleNamespace(cells=["Sheet!A1"]),
        "sibling": SimpleNamespace(cells=["Sheet!B1"]),
    })
    cd = SimpleNamespace(
        comp_of={"chosen": "cycle", "sibling": "cycle"},
        comp_members={"cycle": {"chosen", "sibling"}},
    )
    entries = [{"band": "chosen", "include": True}]

    components, bands, cells, ordered = _curated_output_identity(
        entries, {"chosen"}, cd, bg
    )

    assert components == {"cycle"}
    assert bands == {"chosen"}
    assert cells == {"Sheet!A1"}
    assert ordered == ["Sheet!A1"]


def test_failure_counts_are_complete_but_samples_are_bounded(tmp_path):
    graph, cg, bg, cd, part, source, outputs = _verification_fixture(25)
    result = Evaluator(
        graph,
        cg,
        strict_proof=True,
        calculation=CalculationMetadata(available=True),
    ).run({source.id})
    source_path, ast_dir, curation = _evidence_paths(tmp_path)

    report = _verify(
        cg,
        result,
        {source.id},
        {node.id for node in outputs},
        part,
        cd,
        bg,
        SimpleNamespace(sample=10),
        expected_cache=FakeExpectedCache(2),
        calculation=CalculationMetadata(available=True),
        source_path=source_path,
        ast_dir=ast_dir,
        curation_path=curation,
    )

    assert report["status"] == "fail"
    assert report["counts"]["outputs"]["mismatch"] == 25
    assert report["counts"]["failures"] == {"complete": 25, "sampled": 20}
    assert len(report["samples"]["failure_records"]) == 20
    assert report["samples"]["failure_records"] == sorted(
        report["samples"]["failure_records"],
        key=lambda record: (record["cell"], record["verdict"]),
    )


def test_frozen_manifest_contains_all_forensic_ids_exactly_once():
    manifest = load_manifest(DEFAULT_MANIFEST)
    ids = [case["id"] for case in manifest["cases"]]

    assert len(ids) == 53
    assert len(set(ids)) == 53
    assert set(ids) == FORENSIC_IDS


def test_disposition_classifier_is_evidence_driven_and_fail_closed():
    clean = diagnostics.classify_disposition({"proof_clean": True})
    assert clean["disposition"] == "pass"
    assert clean["cache_policy"] == "retain_as_oracle"

    code = diagnostics.classify_disposition({
        "proof_clean": False,
        "reason_counts": {"output_mismatch": 2},
    })
    assert code["disposition"] == "code_fix_required"
    assert code["primary_ownership"] == "evaluator"
    assert code["cache_policy"] == "retain_as_oracle"

    missing = diagnostics.classify_disposition({
        "reason_counts": {"output_unresolved": 1},
        "missing_evidence": [{"kind": "ast_edges"}],
    })
    assert missing["disposition"] == "insufficient_evidence"
    assert missing["primary_ownership"] == "insufficient"
    assert missing["cache_policy"] == "preserve_pending_diagnostics"
    assert missing["reason_codes"] == [
        "missing_required_proof_evidence",
        "output_unresolved",
    ]

    implicit_recuration = diagnostics.classify_disposition({
        "reason_counts": {"output_mismatch": 1},
        "recuration": {
            "decision": "recurate",
            "scope": ["Sheet!A1"],
            "evidence": ["invalid_selection"],
            "prerequisites_satisfied": False,
        },
    })
    assert implicit_recuration["disposition"] == "code_fix_required"


def test_source_evidence_controls_disposition_and_cache_policy():
    non_unique = diagnostics.classify_disposition({
        "reason_counts": {
            "output_unresolved": 3,
            "active_cycle_iteration_disabled": 1,
        },
        "missing_evidence": [{"kind": "calculation_metadata"}],
        "source": {"demonstrated_non_unique": True},
    })
    assert non_unique["disposition"] == "quarantine"
    assert non_unique["cache_policy"] == "preserve_ignore_as_authority"
    assert non_unique["reason_codes"][0] == (
        "active_cycle_demonstrated_non_unique"
    )

    disabled_cycle = diagnostics.classify_disposition({
        "reason_counts": {
            "output_unresolved": 1,
            "active_cycle_iteration_disabled": 1,
        },
    })
    assert disabled_cycle["disposition"] == "recalc_required"
    assert (
        disabled_cycle["cache_policy"]
        == "refresh_after_authoritative_recalc"
    )

    stale = diagnostics.classify_disposition({
        "source": {"proven_stale_cache": True},
    })
    assert stale["disposition"] == "recalc_required"

    formula_free = diagnostics.classify_disposition({"formula_count": 0})
    assert formula_free["disposition"] == "quarantine"
    assert formula_free["cache_policy"] == "not_applicable"


def test_forensic_and_operational_ownership_are_independent():
    classification = diagnostics.classify_disposition({
        "reason_counts": {
            "output_mismatch": 2,
            "active_cycle_iteration_disabled": 1,
        },
    })

    assert classification["disposition"] == "recalc_required"
    assert classification["forensic_primary_ownership"] == "evaluator"
    assert classification["primary_ownership"] == "evaluator"
    assert classification["operational_ownership"] == "source/cache"


def test_recuration_requires_explicit_structured_completed_evidence():
    evidence = {
        "decision": "recurate",
        "scope": ["ERP row-level outputs"],
        "evidence": ["forensic_selection_invalidity_record"],
        "prerequisites_satisfied": True,
    }
    classification = diagnostics.classify_disposition({
        "recuration": evidence,
    })

    assert classification["disposition"] == "recurate_required"
    assert classification["reason_codes"] == ["explicit_recuration_required"]
    assert classification["cache_policy"] == "retain_as_oracle"


def test_not_run_preserves_evidence_pending_diagnostics():
    classification = diagnostics.classify_disposition({
        "executed": False,
        "reason_counts": {"verification_disabled": 1},
    })

    assert classification["disposition"] == "not_run"
    assert classification["cache_policy"] == "preserve_pending_diagnostics"


def test_frozen_manifest_expectations_are_complete_and_policy_safe():
    manifest = load_manifest(DEFAULT_MANIFEST)
    counts = Counter(
        case["expected_primary_ownership"] for case in manifest["cases"]
    )
    counts.update({owner: 0 for owner in EXPECTED_PRIMARY_COUNTS})

    assert dict(counts) == EXPECTED_PRIMARY_COUNTS
    assert all(
        case["expected_disposition"] in diagnostics.DISPOSITIONS
        and case["expected_cache_policy"] in diagnostics.CACHE_POLICIES
        for case in manifest["cases"]
    )
    by_id = {case["id"]: case for case in manifest["cases"]}
    for case_id in {"0260", "0392", "0439", "0465", "0482"}:
        assert by_id[case_id]["expected_disposition"] == "quarantine"
        assert (
            by_id[case_id]["expected_cache_policy"]
            == "preserve_ignore_as_authority"
        )
    assert by_id["0486"]["expected_disposition"] == "quarantine"
    assert by_id["0486"]["expected_cache_policy"] == "not_applicable"
    assert by_id["0404"]["expected_disposition"] == "recalc_required"
    assert (
        by_id["0404"]["expected_cache_policy"]
        == "refresh_after_authoritative_recalc"
    )
    assert by_id["0466"]["expected_disposition"] == "recurate_required"
    assert by_id["0466"]["recuration_prerequisite"] == "evaluator_fix"
    assert by_id["0395"]["expected_disposition"] == "insufficient_evidence"


def test_read_only_replay_report_is_deterministic_and_reports_missing(tmp_path):
    first = build_replay_report(DEFAULT_MANIFEST, tmp_path)
    second = build_replay_report(DEFAULT_MANIFEST, tmp_path)

    assert diagnostics.canonical_json(first) == diagnostics.canonical_json(second)
    assert first["case_count"] == 53
    assert first["counts"]["ready"] == 0
    assert first["counts"]["missing_required_inputs"] == 53
    assert all(case["state"] == "missing_artifacts" for case in first["cases"])


def test_replay_compares_existing_execution_diagnostics(tmp_path):
    segments = tmp_path / "FCP2" / "seg_out" / "0339" / "segments.json"
    segments.parent.mkdir(parents=True)
    segments.write_text(json.dumps({
        "verification": {
            "primary_ownership": "evaluator",
            "disposition": "code_fix_required",
            "cache_policy": "retain_as_oracle",
        },
    }), encoding="utf-8")

    report = build_replay_report(DEFAULT_MANIFEST, tmp_path)
    case = next(item for item in report["cases"] if item["id"] == "0339")

    assert report["counts"]["execution_artifacts"] == 1
    assert report["counts"]["execution_diagnostics_match"] == 1
    assert case["execution_diagnostics"]["comparison"]["all_match"] is True
    assert all(
        record["matches"]
        for record in case["execution_diagnostics"]["comparison"]["fields"].values()
    )
