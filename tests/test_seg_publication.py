from __future__ import annotations

import json
from pathlib import Path

import pytest

import replay_segmentation_verification as replay
import xl_output_task
from replay_segmentation_verification import check_rollout_performance
from xl_seg import diagnostics, model, publication, restriction_cone


CURATION = """\
[[output]]
band = "out"
sheet = "Sheet"
label = "Output"
score = 10
include = true
name = "Output"
"""


def test_packaging_uses_same_pinned_audit_binding():
    context = {
        "bindings": {
            "source_generation_id": "source-id",
            "segmentation_generation_id": "segmentation-id",
            "segmentation_manifest_sha256": "a" * 64,
        }
    }

    assert xl_output_task.audit_segmentation_binding(context) == {
        "generation_id": "segmentation-id",
        "manifest_sha256": "a" * 64,
        "source_generation_id": "source-id",
    }
    assert xl_output_task.audit_segmentation_binding(None) is None


def _case(tmp_path: Path, name="case", *, status="pass"):
    source = tmp_path / f"{name}.xlsx"
    source.write_bytes(b"source-" + name.encode())
    ast = tmp_path / f"{name}-ast"
    ast.mkdir()
    (ast / "nodes.csv").write_text("nodes\n", encoding="utf-8")
    (ast / "edges.csv").write_text("edges\n", encoding="utf-8")
    seg = tmp_path / "seg" / name
    seg.mkdir(parents=True)
    (seg / "curation.toml").write_text(CURATION, encoding="utf-8")
    fingerprints, missing = publication.evidence_fingerprints(
        source,
        ast,
        seg / "curation.toml",
        ["Sheet!B1"],
    )
    assert missing == []
    verification = {
        "schema_version": diagnostics.SCHEMA_VERSION,
        "status": status,
        "disposition": "pass" if status == "pass" else "code_fix_required",
        "skipped": False,
        "passed": status == "pass",
        "primary_ownership": None if status == "pass" else "evaluator",
        "forensic_primary_ownership":
            None if status == "pass" else "evaluator",
        "operational_ownership": None if status == "pass" else "evaluator",
        "blocking_reasons": [] if status == "pass" else ["output_mismatch"],
        "fingerprints": fingerprints,
        "generation_id": diagnostics.generation_id(fingerprints),
        "counts": {"cache_reads": {"proof": 0}},
        "provenance": {
            "proof": {"strict": True},
            "runtime": {"closure": {"stabilized": True}},
        },
    }
    publication.attach_generation_contract(verification)
    return source, ast, seg, verification


def _stage(
    tmp_path: Path,
    name: str,
    verification: dict,
    *,
    proof: dict | None = None,
) -> Path:
    stage = publication.make_staging_directory(tmp_path / "seg", name)
    (stage / "bands.csv").write_text(
        "band,bucket\nout,output\n",
        encoding="utf-8",
    )
    (stage / "output_candidates.csv").write_text(
        "rank,band\n1,out\n",
        encoding="utf-8",
    )
    (stage / "lineage.json").write_text("{}\n", encoding="utf-8")
    lineage = stage / "lineage"
    lineage.mkdir()
    (lineage / "output.md").write_text("# Output\n", encoding="utf-8")
    (stage / "segments.json").write_text(json.dumps({
        "outputs": [{"band": "out", "cells": ["Sheet!B1"]}],
        "verification": verification,
        "proof": proof or {"closure": {"stabilized": True}},
    }, indent=2), encoding="utf-8")
    return stage


def _restricted_offset_graph():
    def cell(ref, kind, value, formula=""):
        sheet, row, col = model.split_ref(ref)
        return model.Node(
            id=ref,
            kind=kind,
            sheet=sheet,
            coordinate=ref.rpartition("!")[2],
            row=row,
            col=col,
            owner="",
            op="",
            op_kind="",
            arity="",
            expr="",
            label=ref,
            formula=formula,
            value=str(value),
            in_cycle=False,
            parse_status="ok" if kind == "formula" else "not_applicable",
        )

    def ast(node_id, kind, *, op="", arity="", value="", expr=""):
        return model.Node(
            id=node_id,
            kind=kind,
            sheet="Sheet",
            coordinate="",
            row=None,
            col=None,
            owner="Sheet!B1",
            op=op,
            op_kind="func" if kind == "op" else "const",
            arity=str(arity),
            expr=expr,
            label=op or value,
            formula="",
            value=str(value),
            in_cycle=False,
        )

    base = cell("Sheet!A1", "input", 0)
    output = cell(
        "Sheet!B1", "formula", 7, "=OFFSET(A1,0,2)"
    )
    target = cell("Sheet!C1", "input", 7)
    rows = ast("Sheet!B1#0:const", "const", value="0")
    columns = ast("Sheet!B1#1:const", "const", value="2")
    operation = ast(
        "Sheet!B1#2:OFFSET",
        "op",
        op="OFFSET",
        arity=3,
        expr="OFFSET(A1,0,2)",
    )
    edges = [
        model.Edge(
            source=source,
            target=operation.id,
            role="address" if index == 0 else "arg",
            arg_index=index,
            op="OFFSET",
            cell=output.id,
            ref="",
            via_range="",
            cross_sheet=False,
        )
        for index, source in enumerate([base.id, rows.id, columns.id])
    ]
    edges.append(model.Edge(
        source=operation.id,
        target=output.id,
        role="result",
        arg_index=0,
        op="OFFSET",
        cell=output.id,
        ref="",
        via_range="",
        cross_sheet=False,
    ))
    graph = model.Graph(
        "case",
        {
            node.id: node
            for node in [base, output, target, rows, columns, operation]
        },
        edges,
    )
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    return graph, output, target, operation


def test_atomic_publication_resolves_one_immutable_generation(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    curation_before = (seg / "curation.toml").read_bytes()
    stage = _stage(tmp_path, "case", verification)

    generation, manifest = publication.publish_generation(
        stage,
        seg,
        verification,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )
    resolved, resolved_manifest = publication.resolve_current_generation(
        seg,
        source_path=source,
        ast_dir=ast,
        require_pass=True,
        validate_live_evidence=True,
    )

    assert resolved == generation
    assert resolved_manifest == manifest
    assert (seg / "curation.toml").read_bytes() == curation_before
    assert (seg / "segments.json").resolve() == generation / "segments.json"
    assert manifest["curated_output_cells"]["ordered"] == ["Sheet!B1"]


def test_interrupted_staging_and_pointer_switch_never_select_partial(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    first_stage = _stage(tmp_path, "case", verification)
    first, first_manifest = publication.publish_generation(
        first_stage, seg, verification, ["Sheet!B1"],
        source_path=source, ast_dir=ast,
    )

    other_source = tmp_path / "case-v2.xlsx"
    other_source.write_bytes(b"source-v2")
    fingerprints, missing = publication.evidence_fingerprints(
        other_source,
        ast,
        seg / "curation.toml",
        ["Sheet!B1"],
    )
    assert missing == []
    second_verification = {
        **verification,
        "fingerprints": fingerprints,
        "generation_id": diagnostics.generation_id(fingerprints),
    }
    publication.attach_generation_contract(second_verification)
    second_stage = _stage(tmp_path, "case-v2", second_verification)

    def interrupt(phase):
        if phase == "before_pointer_switch":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        publication.publish_generation(
            second_stage,
            seg,
            second_verification,
            ["Sheet!B1"],
            fault=interrupt,
            source_path=other_source,
            ast_dir=ast,
        )

    resolved, manifest = publication.resolve_current_generation(seg)
    assert resolved == first
    assert manifest["generation_id"] == first_manifest["generation_id"]


def test_interrupted_stage_is_never_visible_as_current(tmp_path):
    _, _, seg, verification = _case(tmp_path)
    stage = _stage(tmp_path, "case", verification)

    def interrupt(phase):
        if phase == "staged":
            raise RuntimeError("staging interrupted")

    with pytest.raises(RuntimeError, match="staging interrupted"):
        publication.publish_generation(
            stage,
            seg,
            verification,
            ["Sheet!B1"],
            fault=interrupt,
            source_path=tmp_path / "case.xlsx",
            ast_dir=tmp_path / "case-ast",
        )

    assert stage.is_dir()
    assert not (seg / "current.json").exists()
    with pytest.raises(publication.GenerationValidationError):
        publication.resolve_current_generation(seg)


def test_tamper_and_mixed_generation_are_rejected(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    stage = _stage(tmp_path, "case", verification)
    generation, _ = publication.publish_generation(
        stage, seg, verification, ["Sheet!B1"],
        source_path=source, ast_dir=ast,
    )
    (generation / "bands.csv").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(publication.GenerationValidationError, match="tampered"):
        publication.resolve_current_generation(seg)

    pointer = json.loads((seg / "current.json").read_text(encoding="utf-8"))
    pointer["generation_id"] = "mixed"
    (seg / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(
        publication.GenerationValidationError,
        match="invalid generation_id",
    ):
        publication.resolve_current_generation(seg)


def test_first_nonpassing_generation_is_diagnostic_only(tmp_path):
    source, ast, seg, verification = _case(tmp_path, status="fail")
    stage = _stage(tmp_path, "case", verification)
    generation, _ = publication.publish_generation(
        stage, seg, verification, ["Sheet!B1"]
    )

    assert generation.is_dir()
    assert not (seg / "current.json").exists()
    with pytest.raises(publication.GenerationValidationError):
        publication.resolve_for_consumer(
            seg,
            mode="strict",
            source_path=source,
            ast_dir=ast,
            require_pass=True,
        )
    legacy, manifest = publication.resolve_for_consumer(seg, mode="legacy")
    assert legacy == seg
    assert manifest is None


def test_nonpassing_generation_does_not_replace_last_pass(tmp_path):
    source, ast, seg, passing = _case(tmp_path)
    first, first_manifest = publication.publish_generation(
        _stage(tmp_path, "passing", passing),
        seg,
        passing,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )
    other_source = tmp_path / "failed-source.xlsx"
    other_source.write_bytes(b"failed-source")
    fingerprints, missing = publication.evidence_fingerprints(
        other_source,
        ast,
        seg / "curation.toml",
        ["Sheet!B1"],
    )
    assert missing == []
    failing = {
        **passing,
        "status": "fail",
        "disposition": "code_fix_required",
        "passed": False,
        "primary_ownership": "evaluator",
        "forensic_primary_ownership": "evaluator",
        "operational_ownership": "evaluator",
        "blocking_reasons": ["output_mismatch"],
        "fingerprints": fingerprints,
        "generation_id": diagnostics.generation_id(fingerprints),
    }
    publication.attach_generation_contract(failing)
    failed, _ = publication.publish_generation(
        _stage(tmp_path, "failing", failing),
        seg,
        failing,
        ["Sheet!B1"],
    )

    current, manifest = publication.resolve_current_generation(seg)
    assert failed != first
    assert current == first
    assert manifest["generation_id"] == first_manifest["generation_id"]


def test_generation_id_is_recomputed_from_evidence(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    verification["generation_id"] = "a" * 64
    publication.attach_generation_contract(verification)
    stage = _stage(tmp_path, "wrong-id", verification)

    with pytest.raises(
        publication.GenerationValidationError,
        match="evidence fingerprints",
    ):
        publication.publish_generation(
            stage,
            seg,
            verification,
            ["Sheet!B1"],
            source_path=source,
            ast_dir=ast,
        )


def test_live_evidence_change_retains_generation_without_switching_pointer(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    stage = _stage(tmp_path, "stale-evidence", verification)
    source.write_bytes(b"changed-after-verification")

    with pytest.raises(
        publication.GenerationValidationError,
        match="current evidence does not match",
    ):
        publication.publish_generation(
            stage,
            seg,
            verification,
            ["Sheet!B1"],
            source_path=source,
            ast_dir=ast,
        )

    generation = (
        seg / "generations" / verification["generation_id"]
    )
    assert generation.is_dir()
    assert not (seg / "current.json").exists()


def test_strict_gate_requires_unskipped_consistent_strict_proof(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    verification["skipped"] = True
    stage = _stage(tmp_path, "not-strict", verification)
    payload = json.loads((stage / "segments.json").read_text(encoding="utf-8"))
    payload["proof"]["closure"]["stabilized"] = False
    (stage / "segments.json").write_text(json.dumps(payload), encoding="utf-8")

    generation, _ = publication.publish_generation(
        stage,
        seg,
        verification,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )

    assert generation.is_dir()
    assert not (seg / "current.json").exists()
    with pytest.raises(
        publication.GenerationValidationError,
        match="strict production verification gate failed",
    ):
        publication.validate_generation_directory(generation, require_pass=True)


def test_symlinks_inside_generation_are_rejected(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    stage = _stage(tmp_path, "symlink", verification)
    external = tmp_path / "external.json"
    external.write_text("{}\n", encoding="utf-8")
    (stage / "lineage.json").unlink()
    (stage / "lineage.json").symlink_to(external)

    with pytest.raises(
        publication.GenerationValidationError,
        match="symlink",
    ):
        publication.publish_generation(
            stage,
            seg,
            verification,
            ["Sheet!B1"],
            source_path=source,
            ast_dir=ast,
        )


def test_expected_generation_and_inputs_sidecar_detect_changes(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    generation, manifest = publication.publish_generation(
        _stage(tmp_path, "bound", verification),
        seg,
        verification,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )
    inputs = tmp_path / "case-inputs.xlsx"
    inputs.write_bytes(b"inputs")
    sidecar = publication.write_inputs_sidecar(inputs, generation, manifest)

    assert publication.validate_inputs_sidecar(
        inputs,
        expected_generation_id=manifest["generation_id"],
        generation_dir=generation,
    )["generation_id"] == manifest["generation_id"]
    with pytest.raises(
        publication.GenerationValidationError,
        match="current pointer changed",
    ):
        publication.resolve_current_generation(
            seg,
            expected_generation_id="b" * 64,
        )
    inputs.write_bytes(b"changed")
    with pytest.raises(
        publication.GenerationValidationError,
        match="inputs_sha256",
    ):
        publication.validate_inputs_sidecar(
            inputs,
            expected_generation_id=manifest["generation_id"],
            generation_dir=generation,
        )
    assert sidecar.name == "case-inputs.segmentation.json"


def test_existing_direct_legacy_artifacts_are_never_replaced(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    direct = {}
    for name in publication.LEGACY_ARTIFACTS:
        path = seg / name
        if name == "lineage":
            path.mkdir()
            (path / "old.txt").write_text("old", encoding="utf-8")
            direct[name] = (path / "old.txt").read_bytes()
        else:
            path.write_text(f"old-{name}", encoding="utf-8")
            direct[name] = path.read_bytes()

    publication.publish_generation(
        _stage(tmp_path, "legacy", verification),
        seg,
        verification,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )

    assert not (seg / "current").exists()
    for name, content in direct.items():
        path = seg / name
        actual = (
            (path / "old.txt").read_bytes()
            if name == "lineage"
            else path.read_bytes()
        )
        assert actual == content


def test_restricted_generation_requires_certificate_and_stays_inactive(
    tmp_path, monkeypatch
):
    _, _, seg, verification = _case(tmp_path)
    profile = {
        "schema_version": "source-restriction-profile/v2",
        "allowlist": ["worksheet_offset"],
        "immutable_cells": [],
    }
    events = [{
        "allowed": True,
        "event": "volatile_function",
        "function": "OFFSET",
        "location": "Sheet!B1",
        "reason": "worksheet_offset",
        "scope": "worksheet",
        "token_offset": 0,
    }]
    source_dir = tmp_path / "source-generation"
    source_dir.mkdir()
    health = {
        "route": "restricted_pass",
        "policy_version": "source-recalc-policy/v2",
        "report_sha256": "c" * 64,
        "restriction_profile": profile,
        "restriction_events": events,
        "restriction_events_sha256": restriction_cone.object_hash(events),
    }
    result = {
        "restriction": {
            "profile": profile,
            "restriction_events": events,
        }
    }
    source_manifest = {
        "schema_version": "source-generation/v2",
        "generation_id": "a" * 64,
        "layout": {
            "workbook_id": "case",
            "ast_directory": "ast/case",
        },
        "identity": {
            "policy_version": "source-recalc-policy/v2",
            "policy_decision": {
                "route": "restricted_pass",
                "restriction_profile": profile,
            },
        },
        "bindings": {
            "source_sha256": "b" * 64,
            "health_sha256": "9" * 64,
            "health_report_sha256": "c" * 64,
            "restriction_evidence_sha256": "d" * 64,
            "restriction_events_sha256": health[
                "restriction_events_sha256"
            ],
            "restriction_profile_sha256": restriction_cone.object_hash(
                profile
            ),
        },
    }
    (source_dir / "health.json").write_text(
        json.dumps(health), encoding="utf-8"
    )
    (source_dir / "result.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    (source_dir / "generation-manifest.json").write_text(
        json.dumps(source_manifest), encoding="utf-8"
    )
    monkeypatch.setattr(
        "xl_source_publication.validate_source_generation",
        lambda path: source_manifest,
    )
    graph, output, target, operation = _restricted_offset_graph()
    monkeypatch.setattr(
        restriction_cone.model, "load", lambda *args: graph
    )
    proof = {
        "runtime_radj": {output.id: [target.id]},
        "resolved_targets": {output.id: [target.id]},
        "resolved_operation_targets": {operation.id: [target.id]},
        "closure": {"stabilized": True, "targets_stable": True},
    }
    verification["ordered_output_cells"] = [output.id]
    verification["provenance"]["runtime"]["closure"] = proof["closure"]
    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=[output.id],
        segmentation_fingerprints=verification["fingerprints"],
    )
    publication.bind_source_generation(
        verification,
        source_manifest,
        certificate=certificate,
    )
    publication.attach_generation_contract(verification)

    missing_stage = _stage(tmp_path, "restricted-missing", verification)
    with pytest.raises(
        publication.GenerationValidationError,
        match="missing its cone certificate",
    ):
        publication.publish_generation(
            missing_stage,
            seg,
            verification,
            ["Sheet!B1"],
            source_generation_dir=source_dir,
        )

    stage = _stage(
        tmp_path, "restricted", verification, proof=proof
    )
    (stage / "restriction-cone-certificate.json").write_bytes(
        publication._canonical_bytes(certificate)
    )
    generation, manifest = publication.publish_generation(
        stage,
        seg,
        verification,
        ["Sheet!B1"],
        source_generation_dir=source_dir,
    )

    assert generation.is_dir()
    assert not (seg / "current.json").exists()
    resolved, resolved_manifest = publication.resolve_generation_by_id(
        seg,
        manifest["generation_id"],
        require_pass=True,
        source_generation_dir=source_dir,
    )
    assert resolved == generation
    assert resolved_manifest == manifest

    (generation / "restriction-cone-certificate.json").write_text(
        '{"tampered":true}\n', encoding="utf-8"
    )
    with pytest.raises(publication.GenerationValidationError, match="tampered"):
        publication.resolve_generation_by_id(seg, manifest["generation_id"])


def test_packaged_task_copies_inputs_generation_binding(tmp_path):
    source, ast, seg, verification = _case(tmp_path)
    generation, manifest = publication.publish_generation(
        _stage(tmp_path, "package", verification),
        seg,
        verification,
        ["Sheet!B1"],
        source_path=source,
        ast_dir=ast,
    )
    inputs = tmp_path / "case-inputs.xlsx"
    inputs.write_bytes(b"inputs")
    inputs_sidecar = publication.write_inputs_sidecar(
        inputs, generation, manifest
    )
    out = tmp_path / "task"

    xl_output_task.emit(
        out,
        "case",
        "model",
        inputs,
        "instruction",
        {"Sheet!B1": 1},
        [{"name": "Output", "band": "Sheet!B1", "refs": ["Sheet!B1"]}],
        {},
        audit_root=tmp_path / "audit",
        generation_manifest=manifest,
        generation_manifest_path=generation / "generation-manifest.json",
        inputs_generation=json.loads(
            inputs_sidecar.read_text(encoding="utf-8")
        ),
        inputs_generation_path=inputs_sidecar,
    )

    packaged = json.loads(
        (out / "tests" / "inputs_generation.json").read_text(encoding="utf-8")
    )
    assert packaged["generation_id"] == manifest["generation_id"]
    assert 'inputs_generation = "tests/inputs_generation.json"' in (
        out / "task.toml"
    ).read_text(encoding="utf-8")


def test_rollout_performance_gate_enforces_only_supplied_thresholds():
    baseline = {
        "benchmark": {
            "verifier_runtime_p95_s": 100.0,
            "peak_memory_bytes": 1000,
        }
    }
    passing = {
        "benchmark": {
            "verifier_runtime_p95_s": 125.0,
            "peak_memory_bytes": 1200,
        }
    }
    failing = {
        "benchmark": {
            "verifier_runtime_p95_s": 125.01,
            "peak_memory_bytes": 1201,
        }
    }

    assert check_rollout_performance(passing, baseline)["passed"] is True
    assert check_rollout_performance(failing, baseline)["passed"] is False


@pytest.mark.parametrize(
    ("forensic_owner", "expected_state"),
    [("insufficient", "completed"), ("evaluator", "failed")],
)
def test_replay_requires_expected_diagnostics_for_completion(
    tmp_path,
    monkeypatch,
    forensic_owner,
    expected_state,
):
    case_id = "0222"
    source = (
        tmp_path
        / "FCP Workbooks"
        / "5-2 300"
        / "Batch 002_300 Models_05.01.26"
        / f"{case_id}.xlsx"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen source")
    ast = tmp_path / "FCP2" / "ast_out" / case_id
    ast.mkdir(parents=True)
    (ast / "nodes.csv").write_text("nodes\n", encoding="utf-8")
    (ast / "edges.csv").write_text("edges\n", encoding="utf-8")
    original_seg = tmp_path / "FCP2" / "seg_out" / case_id
    original_seg.mkdir(parents=True)
    (original_seg / "curation.toml").write_text(
        CURATION,
        encoding="utf-8",
    )
    frozen = {
        path: path.read_bytes()
        for path in (
            source,
            ast / "nodes.csv",
            ast / "edges.csv",
            original_seg / "curation.toml",
        )
    }

    def fake_segment(wb, args):
        shadow_case = Path(args.out) / wb
        fingerprints, missing = publication.evidence_fingerprints(
            source,
            ast,
            shadow_case / "curation.toml",
            ["Sheet!B1"],
        )
        assert missing == []
        verification = {
            "schema_version": diagnostics.SCHEMA_VERSION,
            "status": "fail",
            "disposition": "insufficient_evidence",
            "primary_ownership": forensic_owner,
            "forensic_primary_ownership": forensic_owner,
            "operational_ownership": "insufficient",
            "cache_policy": "preserve_pending_diagnostics",
            "cache_policy_by_reason": [],
            "blocking_reasons": ["missing_required_proof_evidence"],
            "blocking_reason_details": [],
            "counts": {"cache_reads": {"proof": 0}},
            "samples": {},
            "skipped": False,
            "passed": False,
            "provenance": {
                "proof": {"strict": True},
                "runtime": {"closure": {"stabilized": True}},
            },
            "fingerprints": fingerprints,
            "generation_id": diagnostics.generation_id(fingerprints),
        }
        publication.attach_generation_contract(verification)
        stage = _stage(Path(args.out), wb, verification)
        generation, manifest = publication.publish_generation(
            stage,
            shadow_case,
            verification,
            ["Sheet!B1"],
            source_path=source,
            ast_dir=ast,
        )
        return {
            "verification": verification,
            "proof": {"closure": {"stabilized": True}},
            "benchmark": {
                "verifier_runtime_s": 1.0,
                "peak_memory_bytes": 100,
            },
            "generation": {
                "directory": str(generation),
                "generation_id": manifest["generation_id"],
            },
        }

    monkeypatch.setattr("xl_segment.segment", fake_segment)
    report = replay.execute_shadow_replay(
        replay.DEFAULT_MANIFEST,
        tmp_path,
        tmp_path / "shadow",
    )
    result = next(item for item in report["results"] if item["id"] == case_id)

    assert result["state"] == expected_state
    assert result["deterministic"] is True
    assert result["preserved_inputs"] is True
    assert result["expected_diagnostics_match"] is (
        expected_state == "completed"
    )
    assert len(result["runs"]) == 3
    assert report["benchmark"] == {
        "samples": 3,
        "verifier_runtime_p95_s": 1.0,
        "peak_memory_bytes": 100,
    }
    assert all(path.read_bytes() == content for path, content in frozen.items())
    assert all(
        item["state"] == "not_run"
        for item in report["results"]
        if item["id"] != case_id
    )
