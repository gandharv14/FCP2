from __future__ import annotations

import json
import re
import shutil
import threading
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

import gen_normalizer
import xl_release_publication as release
import xl_output_task
import xl_inventory_approval
import xl_source_publication as source_publication
import xl_source_recalc as source_recalc
from xl_source_health import inspect_workbook
from xl_source_inventory import build_inventory_manifest
from xl_inventory_approval import approval_claims, object_hash
from xl_source_recalc import create_identity_documents
from xl_seg import diagnostics as segmentation_diagnostics
from xl_seg import model as segmentation_model
from xl_seg import publication as segmentation_publication
from xl_seg import restriction_cone


H = {
    name: character * 64
    for name, character in {
        "source": "1",
        "health": "2",
        "report": "3",
        "restriction": "4",
        "events": "5",
        "profile": "6",
        "cone": "7",
        "approval": "8",
        "signals": "9",
    }.items()
}


def _xlsx(
    path: Path,
    *,
    external=False,
    relationship_type: str | None = None,
    target_mode: str | None = None,
    bracket_text: str | None = None,
) -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("xl/workbook.xml", "<workbook/>")
        if external:
            package.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
        if bracket_text is not None:
            package.writestr(
                "xl/sharedStrings.xml",
                "<sst><si><t>" + bracket_text + "</t></si></sst>",
            )
        if relationship_type is not None or target_mode is not None:
            attributes = [
                'Id="rId1"',
                f'Type="{relationship_type or "urn:test:worksheet"}"',
                'Target="../outside.xlsx"',
            ]
            if target_mode is not None:
                attributes.append(f'TargetMode="{target_mode}"')
            package.writestr(
                "xl/_rels/workbook.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships"><Relationship '
                + " ".join(attributes)
                + "/></Relationships>",
            )


def _inject_formula_caches(path: Path, values: dict[str, str]) -> None:
    with zipfile.ZipFile(path) as source:
        parts = {
            item.filename: source.read(item.filename)
            for item in source.infolist()
        }
    worksheet = "xl/worksheets/sheet1.xml"
    xml = parts[worksheet]
    for coordinate, value in values.items():
        attribute = xml.find(f'r="{coordinate}"'.encode())
        assert attribute >= 0
        start = xml.rfind(b"<c", 0, attribute)
        close = xml.find(b"</c>", attribute)
        assert start >= 0 and close >= 0
        end = close + len(b"</c>")
        cell = xml[start:end]
        cached = f"<v>{value}</v>".encode()
        if re.search(rb"<v\b", cell):
            cell = re.sub(
                rb"<v\b[^/]*/>|<v>[^<]*</v>",
                cached,
                cell,
                count=1,
            )
        else:
            cell = cell.replace(b"</c>", cached + b"</c>", 1)
        xml = xml[:start] + cell + xml[end:]
    parts[worksheet] = xml
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
        for name, content in parts.items():
            destination.writestr(name, content)


def _freeze_fixture_legacy(case: dict) -> str:
    source_pointer = case["source_root"] / "current.json"
    source_pointer.write_text(json.dumps({
        "schema_version": "source-current/v1",
        "generation_id": case["source_id"],
    }))
    segmentation_pointer = case["segmentation_root"] / "current.json"
    segmentation_pointer.write_text(json.dumps({
        "schema_version": "segmentation-current/v1",
        "generation_id": case["segmentation_id"],
    }))
    legacy_task = case["source_root"].parent / "legacy-task"
    legacy_task.mkdir()
    (legacy_task / "instruction.md").write_text("legacy\n")
    _, snapshot = release.freeze_legacy_snapshot(
        case["release_root"],
        case["workbook"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
        task_dir=legacy_task,
    )
    return snapshot["snapshot_hash"]


def _fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    restricted=True,
    freeze_legacy=True,
):
    workbook = "case"
    source_root = tmp_path / "source"
    source_id = "a" * 64
    source_dir = source_root / "generations" / source_id
    (source_dir / "source").mkdir(parents=True)
    (source_dir / "ast" / workbook).mkdir(parents=True)
    _xlsx(source_dir / "source" / f"{workbook}.xlsx")
    (source_dir / "ast" / workbook / "nodes.csv").write_text("nodes\n")
    (source_dir / "ast" / workbook / "edges.csv").write_text("edges\n")
    source_bindings = {
        "source_sha256": H["source"],
        "health_sha256": H["health"],
        "health_report_sha256": H["report"],
    }
    if restricted:
        source_bindings.update({
            "restriction_evidence_sha256": H["restriction"],
            "restriction_events_sha256": H["events"],
            "restriction_profile_sha256": H["profile"],
        })
        if release.SOURCE_POLICY_VERSION != (
            release.PREVIOUS_SOURCE_POLICY_VERSION
        ):
            source_bindings.update({
                "inventory_approval_sha256": H["approval"],
                "recalc_signals_sha256": H["signals"],
            })
    source_manifest = {
        "schema_version": release.SOURCE_SCHEMA_VERSION,
        "generation_id": source_id,
        "layout": {
            "workbook_id": workbook,
            "source_root": "source",
            "source_workbook": f"source/{workbook}.xlsx",
            "ast_root": "ast",
            "ast_directory": f"ast/{workbook}",
        },
        "identity": {
            "policy_version": release.SOURCE_POLICY_VERSION,
            "policy_decision": {
                "route": "restricted_pass" if restricted else "pass",
            },
        },
        "bindings": source_bindings,
    }
    (source_dir / "generation-manifest.json").write_text(
        json.dumps(source_manifest) + "\n"
    )
    (source_dir / "health.json").write_text(json.dumps({
        "schema_version": release.SOURCE_HEALTH_SCHEMA_VERSION,
        "policy_version": release.SOURCE_POLICY_VERSION,
    }))

    segmentation_root = tmp_path / "seg"
    segmentation_id = "b" * 64
    segmentation_dir = segmentation_root / "generations" / segmentation_id
    segmentation_dir.mkdir(parents=True)
    policy = {
        "source_generation": {
            "generation_id": source_id,
            "source_sha256": H["source"],
            "health_report_sha256": H["report"],
            "policy_version": release.SOURCE_POLICY_VERSION,
            "route": "restricted_pass" if restricted else "pass",
        },
    }
    if restricted:
        policy.update({
            "restriction_evidence_sha256": H["restriction"],
            "restriction_events_sha256": H["events"],
            "restriction_profile_sha256": H["profile"],
            "cone_certificate_sha256": H["cone"],
        })
        if release.SOURCE_POLICY_VERSION != (
            release.PREVIOUS_SOURCE_POLICY_VERSION
        ):
            policy.update({
                "inventory_approval_sha256": H["approval"],
                "recalc_signals_sha256": H["signals"],
            })
        (segmentation_dir / "restriction-cone-certificate.json").write_text(
            json.dumps({
                "schema_version": release.CONE_SCHEMA_VERSION,
                "certificate_sha256": H["cone"],
            })
        )
    segmentation_manifest = {
        "schema_version": release.SEGMENTATION_SCHEMA_VERSION,
        "generation_id": segmentation_id,
        "verification_schema_version":
            release.SEGMENTATION_VERIFICATION_SCHEMA_VERSION,
        "source_policy_bindings": policy,
    }
    (segmentation_dir / "generation-manifest.json").write_text(
        json.dumps(segmentation_manifest) + "\n"
    )

    monkeypatch.setattr(
        "xl_source_publication.resolve_source_generation_by_id",
        lambda root, generation_id: (source_dir, source_manifest),
    )
    monkeypatch.setattr(
        "xl_seg.publication.resolve_generation_by_id",
        lambda root, generation_id, **kwargs:
            (segmentation_dir, segmentation_manifest),
    )
    task_stage = tmp_path / "task-stage"
    (task_stage / "environment").mkdir(parents=True)
    _xlsx(task_stage / "environment" / "case-inputs.xlsx")
    (task_stage / "instruction.md").write_text("Build it.\n")
    (task_stage / "tests").mkdir()
    (task_stage / "tests" / "answer_key.json").write_text("{}\n")
    task_bindings = {
        "source_generation_id": source_id,
        "source_manifest_sha256": release._sha256(
            source_dir / "generation-manifest.json"
        ),
        "source_health_sha256": H["health"],
        "source_health_report_sha256": H["report"],
        "restriction_evidence_sha256": (
            H["restriction"] if restricted else None
        ),
        "restriction_events_sha256": H["events"] if restricted else None,
        "restriction_profile_sha256": H["profile"] if restricted else None,
        "inventory_approval_sha256": (
            H["approval"]
            if restricted
            and release.SOURCE_POLICY_VERSION
            != release.PREVIOUS_SOURCE_POLICY_VERSION
            else None
        ),
        "recalc_signals_sha256": (
            H["signals"]
            if restricted
            and release.SOURCE_POLICY_VERSION
            != release.PREVIOUS_SOURCE_POLICY_VERSION
            else None
        ),
        "segmentation_generation_id": segmentation_id,
        "segmentation_manifest_sha256": release._sha256(
            segmentation_dir / "generation-manifest.json"
        ),
        "cone_certificate_sha256": H["cone"] if restricted else None,
    }
    release_root = tmp_path / "releases"
    task_dir, task_manifest = release.publish_task_generation(
        task_stage, release_root, workbook, bindings=task_bindings
    )
    case = {
        "workbook": workbook,
        "release_root": release_root,
        "source_root": source_root,
        "source_id": source_id,
        "segmentation_root": segmentation_root,
        "segmentation_id": segmentation_id,
        "task_id": task_manifest["generation_id"],
        "task_dir": task_dir,
    }
    if freeze_legacy:
        case["legacy_snapshot_hash"] = _freeze_fixture_legacy(case)
    return case


def _publish(case, **kwargs):
    return release.publish_release(
        case["release_root"],
        case["workbook"],
        source_root=case["source_root"],
        source_generation_id=case["source_id"],
        segmentation_root=case["segmentation_root"],
        segmentation_generation_id=case["segmentation_id"],
        task_generation_id=case["task_id"],
        expected_current_release_id=kwargs.pop("expected", None),
        legacy_snapshot_hash=kwargs.pop(
            "legacy_snapshot_hash",
            case.get("legacy_snapshot_hash"),
        ),
        **kwargs,
    )


def test_full_binding_preservation_and_first_cas(tmp_path, monkeypatch):
    case = _fixture(tmp_path, monkeypatch)
    directory, manifest = _publish(case)
    resolved, observed = release.resolve_current_release(
        case["release_root"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
    )
    assert resolved == directory
    assert observed == manifest
    assert manifest["prior_release_id"] is None
    assert manifest["bindings"]["restriction_evidence_sha256"] == H["restriction"]
    assert manifest["bindings"]["cone_certificate_sha256"] == H["cone"]


def test_previous_v2_source_policy_release_remains_valid(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        release, "SOURCE_POLICY_VERSION", release.PREVIOUS_SOURCE_POLICY_VERSION
    )
    monkeypatch.setattr(
        release,
        "SOURCE_HEALTH_SCHEMA_VERSION",
        release.PREVIOUS_SOURCE_HEALTH_SCHEMA_VERSION,
    )
    case = _fixture(tmp_path, monkeypatch)
    manifest = release.build_release_manifest(
        case["workbook"],
        source_root=case["source_root"],
        source_generation_id=case["source_id"],
        segmentation_root=case["segmentation_root"],
        segmentation_generation_id=case["segmentation_id"],
        task_root=case["release_root"],
        task_generation_id=case["task_id"],
        prior_release_id=None,
        legacy_snapshot_hash=case["legacy_snapshot_hash"],
        source_policy_version=release.PREVIOUS_SOURCE_POLICY_VERSION,
        source_health_schema_version=release.PREVIOUS_SOURCE_HEALTH_SCHEMA_VERSION,
    )
    directory = (
        case["release_root"] / "releases" / manifest["release_id"]
    )
    directory.mkdir(parents=True)
    (directory / "release-manifest.json").write_bytes(
        release._canonical_bytes(manifest)
    )
    monkeypatch.setattr(release, "SOURCE_POLICY_VERSION", "source-recalc-policy/v3")
    monkeypatch.setattr(
        release, "SOURCE_HEALTH_SCHEMA_VERSION", "xlsx-source-health/v3"
    )

    validated = release.validate_release(
        directory,
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
        task_root=case["release_root"],
    )

    assert validated == manifest
    assert manifest["versions"]["source_policy_version"] == (
        release.PREVIOUS_SOURCE_POLICY_VERSION
    )


def test_current_restricted_release_requires_approval_and_signal_bindings(
    tmp_path,
    monkeypatch,
):
    case = _fixture(tmp_path, monkeypatch)
    source_manifest_path = (
        case["source_root"]
        / "generations"
        / case["source_id"]
        / "generation-manifest.json"
    )
    source_manifest = json.loads(source_manifest_path.read_text())
    source_manifest["bindings"].pop("inventory_approval_sha256")
    source_manifest["bindings"].pop("recalc_signals_sha256")
    source_manifest_path.write_text(json.dumps(source_manifest) + "\n")
    monkeypatch.setattr(
        "xl_source_publication.resolve_source_generation_by_id",
        lambda _root, _generation_id: (
            source_manifest_path.parent,
            source_manifest,
        ),
    )

    with pytest.raises(
        release.ReleasePublicationError,
        match="current restricted source bindings are incomplete",
    ):
        _publish(case)


def test_first_cas_without_validated_legacy_snapshot_is_rejected(
    tmp_path, monkeypatch
):
    case = _fixture(tmp_path, monkeypatch, freeze_legacy=False)

    with pytest.raises(
        release.ReleasePublicationError,
        match="frozen legacy snapshot hash",
    ):
        _publish(case, legacy_snapshot_hash=None)

    assert not (case["release_root"] / "current-release.json").exists()


def test_first_cas_accepts_hash_bound_absent_legacy_snapshot(
    tmp_path, monkeypatch
):
    case = _fixture(tmp_path, monkeypatch, freeze_legacy=False)
    _, snapshot = release.freeze_absent_legacy_snapshot(
        case["release_root"],
        case["workbook"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
    )

    directory, manifest = _publish(
        case,
        legacy_snapshot_hash=snapshot["snapshot_hash"],
    )

    assert directory.is_dir()
    assert manifest["legacy_snapshot_hash"] == snapshot["snapshot_hash"]
    assert (case["release_root"] / "current-release.json").is_file()


def test_absent_legacy_snapshot_rejects_appearing_or_wrong_legacy_state(
    tmp_path, monkeypatch
):
    case = _fixture(tmp_path, monkeypatch, freeze_legacy=False)
    _, snapshot = release.freeze_absent_legacy_snapshot(
        case["release_root"],
        case["workbook"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
    )
    source_pointer = case["source_root"] / "current.json"
    source_pointer.write_text(
        '{"schema_version":"source-current/v1"}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePublicationError,
        match="legacy state appeared",
    ):
        _publish(
            case,
            legacy_snapshot_hash=snapshot["snapshot_hash"],
        )

    assert not (case["release_root"] / "current-release.json").exists()


def test_absent_legacy_snapshot_rejects_partial_legacy_tuple(
    tmp_path, monkeypatch
):
    case = _fixture(tmp_path, monkeypatch, freeze_legacy=False)
    source_pointer = case["source_root"] / "current.json"
    source_pointer.write_text(
        '{"schema_version":"source-current/v1"}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePublicationError,
        match="both legacy pointers absent",
    ):
        release.freeze_absent_legacy_snapshot(
            case["release_root"],
            case["workbook"],
            source_root=case["source_root"],
            segmentation_root=case["segmentation_root"],
        )


def test_end_to_end_release_uses_real_source_and_segmentation_resolvers(tmp_path):
    workbook_id = "real-case"
    source = tmp_path / f"{workbook_id}.xlsx"
    book = Workbook()
    book.active.title = "Sheet"
    book.active["A1"] = 1
    book.active["B1"] = 2
    book.save(source)
    ast = tmp_path / "ast"
    ast.mkdir()
    (ast / "nodes.csv").write_text("id\nSheet!B1\n")
    (ast / "edges.csv").write_text("source,target\n")
    request, result = create_identity_documents(source)
    source_root = tmp_path / "real-source"
    source_dir, source_manifest = source_publication.publish_source_generation(
        source,
        ast,
        source_root,
        request=request,
        result=result,
        health=inspect_workbook(source),
        builder_args=["--production"],
        activate=False,
    )
    source_path = source_dir / source_manifest["layout"]["source_workbook"]
    ast_dir = source_dir / source_manifest["layout"]["ast_directory"]

    segmentation_root = tmp_path / "real-segmentation"
    segmentation_root.mkdir()
    curation = segmentation_root / "curation.toml"
    curation.write_text(
        '[[output]]\nband = "out"\nsheet = "Sheet"\n'
        'label = "Output"\nscore = 10\ninclude = true\nname = "Output"\n'
    )
    fingerprints, missing = segmentation_publication.evidence_fingerprints(
        source_path,
        ast_dir,
        curation,
        ["Sheet!B1"],
    )
    assert missing == []
    verification = {
        "schema_version": segmentation_diagnostics.SCHEMA_VERSION,
        "status": "pass",
        "disposition": "pass",
        "skipped": False,
        "passed": True,
        "primary_ownership": None,
        "forensic_primary_ownership": None,
        "operational_ownership": None,
        "blocking_reasons": [],
        "fingerprints": fingerprints,
        "generation_id": segmentation_diagnostics.generation_id(fingerprints),
        "counts": {"cache_reads": {"proof": 0}},
        "provenance": {
            "proof": {"strict": True},
            "runtime": {"closure": {"stabilized": True}},
        },
    }
    segmentation_publication.bind_source_generation(
        verification,
        source_manifest,
    )
    segmentation_publication.attach_generation_contract(verification)
    stage = segmentation_publication.make_staging_directory(
        segmentation_root,
        workbook_id,
    )
    (stage / "bands.csv").write_text("band,bucket\nout,output\n")
    (stage / "output_candidates.csv").write_text("rank,band\n1,out\n")
    (stage / "lineage.json").write_text("{}\n")
    (stage / "lineage").mkdir()
    (stage / "lineage" / "output.md").write_text("# Output\n")
    (stage / "segments.json").write_text(json.dumps({
        "outputs": [{"band": "out", "cells": ["Sheet!B1"]}],
        "verification": verification,
        "proof": {"closure": {"stabilized": True}},
    }))
    segmentation_dir, segmentation_manifest = (
        segmentation_publication.publish_generation(
            stage,
            segmentation_root,
            verification,
            ["Sheet!B1"],
            source_path=source_path,
            ast_dir=ast_dir,
            source_generation_dir=source_dir,
        )
    )

    source_bindings = source_manifest["bindings"]
    segmentation_policy = segmentation_manifest["source_policy_bindings"]
    task_bindings = {
        "source_generation_id": source_manifest["generation_id"],
        "source_manifest_sha256": release._sha256(
            source_dir / "generation-manifest.json"
        ),
        "source_health_sha256": source_bindings["health_sha256"],
        "source_health_report_sha256": source_bindings[
            "health_report_sha256"
        ],
        "restriction_evidence_sha256": None,
        "restriction_events_sha256": None,
        "restriction_profile_sha256": None,
        "segmentation_generation_id": segmentation_manifest["generation_id"],
        "segmentation_manifest_sha256": release._sha256(
            segmentation_dir / "generation-manifest.json"
        ),
        "cone_certificate_sha256": segmentation_policy.get(
            "cone_certificate_sha256"
        ),
    }
    task_stage = tmp_path / "real-task-stage"
    (task_stage / "environment").mkdir(parents=True)
    _xlsx(task_stage / "environment" / f"{workbook_id}-inputs.xlsx")
    (task_stage / "instruction.md").write_text("Build it.\n")
    release_root = tmp_path / "real-release"
    _, task_manifest = release.publish_task_generation(
        task_stage,
        release_root,
        workbook_id,
        bindings=task_bindings,
    )

    (source_root / "current.json").write_text(json.dumps({
        "schema_version": "source-current/v1",
        "generation_id": source_manifest["generation_id"],
    }))
    (segmentation_root / "current.json").write_text(json.dumps({
        "schema_version": "segmentation-current/v1",
        "generation_id": segmentation_manifest["generation_id"],
    }))
    legacy_task = tmp_path / "real-legacy-task"
    legacy_task.mkdir()
    (legacy_task / "instruction.md").write_text("legacy\n")
    _, snapshot = release.freeze_legacy_snapshot(
        release_root,
        workbook_id,
        source_root=source_root,
        segmentation_root=segmentation_root,
        task_dir=legacy_task,
    )

    directory, manifest = release.publish_release(
        release_root,
        workbook_id,
        source_root=source_root,
        source_generation_id=source_manifest["generation_id"],
        segmentation_root=segmentation_root,
        segmentation_generation_id=segmentation_manifest["generation_id"],
        task_generation_id=task_manifest["generation_id"],
        expected_current_release_id=None,
        legacy_snapshot_hash=snapshot["snapshot_hash"],
    )
    resolved, observed = release.resolve_current_release(
        release_root,
        source_root=source_root,
        segmentation_root=segmentation_root,
    )
    assert resolved == directory
    assert observed == manifest


def test_end_to_end_restricted_release_uses_real_validators_and_resolvers(
    tmp_path, monkeypatch
):
    workbook_id = "synthetic-restricted"
    cohort_root = tmp_path / "synthetic-cohort"
    cohort_root.mkdir()
    source = cohort_root / f"{workbook_id}.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet"
    sheet["A1"] = 1
    sheet["B1"] = "=A1+1"
    sheet["C1"] = "=NOW()"
    book.calculation.calcMode = "auto"
    book.calculation.fullCalcOnLoad = False
    book.calculation.forceFullCalc = False
    book.save(source)
    _inject_formula_caches(source, {"B1": "2", "C1": "45000"})

    cohort_ids = [workbook_id] + [
        f"synthetic-cohort-{index:03d}" for index in range(122)
    ]
    for cohort_id in cohort_ids[1:]:
        shutil.copyfile(source, cohort_root / f"{cohort_id}.xlsx")
    inventory = build_inventory_manifest(
        cohort_root,
        workbook_ids=cohort_ids,
    )
    batch_id = "synthetic-restricted-batch"
    approval = approval_claims(inventory, batch_id=batch_id)
    registry_core = {
        "schema_version": "source-inventory-approval-registry/v1",
        "approvals": [approval],
    }
    registry = {
        **registry_core,
        "registry_sha256": object_hash(registry_core),
    }
    registry_path = tmp_path / "approved-source-inventories.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(
        xl_inventory_approval,
        "DEFAULT_REGISTRY",
        registry_path,
    )

    health = inspect_workbook(source)
    assert health["route"] == "restricted_pass"
    assert health["restriction_events"] == [{
        "allowed": True,
        "event": "volatile_function",
        "function": "NOW",
        "location": "Sheet!C1",
        "reason": "worksheet_now",
        "scope": "worksheet",
        "token_offset": 0,
    }]
    source_root = tmp_path / "restricted-source"
    source_dir, source_manifest = source_recalc.prepare_source_generation(
        source,
        workbook_id,
        source_root,
        health=health,
        inventory=inventory,
        inventory_batch_id=batch_id,
    )
    source_path = source_dir / source_manifest["layout"]["source_workbook"]
    ast_dir = source_dir / source_manifest["layout"]["ast_directory"]
    graph = segmentation_model.load(ast_dir, workbook_id)
    assert graph.capabilities and all(graph.capabilities.values())
    assert graph.integrity_errors == []
    assert graph.nodes["Sheet!B1"].formula == "=A1+1"
    assert graph.nodes["Sheet!C1"].formula == "=NOW()"

    segmentation_root = tmp_path / "restricted-segmentation"
    segmentation_root.mkdir()
    curation = segmentation_root / "curation.toml"
    curation.write_text(
        '[[output]]\nband = "out"\nsheet = "Sheet"\n'
        'label = "Output"\nscore = 10\ninclude = true\nname = "Output"\n',
        encoding="utf-8",
    )
    fingerprints, missing = segmentation_publication.evidence_fingerprints(
        source_path,
        ast_dir,
        curation,
        ["Sheet!B1"],
    )
    assert missing == []
    proof = {
        "runtime_radj": {"Sheet!B1": ["Sheet!A1"]},
        "resolved_targets": {},
        "resolved_operation_targets": {},
        "closure": {
            "stabilized": True,
            "targets_stable": True,
        },
    }
    verification = {
        "schema_version": segmentation_diagnostics.SCHEMA_VERSION,
        "status": "pass",
        "disposition": "pass",
        "skipped": False,
        "passed": True,
        "primary_ownership": None,
        "forensic_primary_ownership": None,
        "operational_ownership": None,
        "blocking_reasons": [],
        "fingerprints": fingerprints,
        "generation_id": segmentation_diagnostics.generation_id(fingerprints),
        "ordered_output_cells": ["Sheet!B1"],
        "counts": {"cache_reads": {"proof": 0}},
        "provenance": {
            "proof": {"strict": True},
            "runtime": {"closure": proof["closure"]},
        },
    }
    certificate = restriction_cone.build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=["Sheet!B1"],
        segmentation_fingerprints=fingerprints,
    )
    assert certificate["events"][0]["host_location"] == "Sheet!C1"
    assert certificate["events"][0]["in_output_cone"] is False
    assert certificate["events"][0]["disposition"] == (
        "allowed_only_outside_output_cone"
    )
    segmentation_publication.bind_source_generation(
        verification,
        source_manifest,
        certificate=certificate,
    )
    segmentation_publication.attach_generation_contract(verification)
    restriction_cone.validate_certificate(
        certificate,
        source_generation_dir=source_dir,
        segmentation_artifact={
            "verification": verification,
            "proof": proof,
        },
    )

    stage = segmentation_publication.make_staging_directory(
        segmentation_root,
        workbook_id,
    )
    (stage / "bands.csv").write_text(
        "band,bucket\nout,output\n",
        encoding="utf-8",
    )
    (stage / "output_candidates.csv").write_text(
        "rank,band\n1,out\n",
        encoding="utf-8",
    )
    (stage / "lineage.json").write_text("{}\n", encoding="utf-8")
    (stage / "lineage").mkdir()
    (stage / "lineage" / "output.md").write_text(
        "# Output\n",
        encoding="utf-8",
    )
    (stage / "restriction-cone-certificate.json").write_bytes(
        restriction_cone.certificate_bytes(certificate)
    )
    (stage / "segments.json").write_text(
        json.dumps({
            "outputs": [{"band": "out", "cells": ["Sheet!B1"]}],
            "verification": verification,
            "proof": proof,
        }),
        encoding="utf-8",
    )
    segmentation_dir, segmentation_manifest = (
        segmentation_publication.publish_generation(
            stage,
            segmentation_root,
            verification,
            ["Sheet!B1"],
            source_generation_dir=source_dir,
        )
    )
    assert not (source_root / "current.json").exists()
    assert not (segmentation_root / "current.json").exists()
    resolved_segmentation, resolved_segmentation_manifest = (
        segmentation_publication.resolve_generation_by_id(
            segmentation_root,
            segmentation_manifest["generation_id"],
            require_pass=True,
            source_generation_dir=source_dir,
        )
    )
    assert resolved_segmentation == segmentation_dir
    assert resolved_segmentation_manifest == segmentation_manifest

    source_bindings = source_manifest["bindings"]
    segmentation_policy = segmentation_manifest["source_policy_bindings"]
    task_bindings = {
        "source_generation_id": source_manifest["generation_id"],
        "source_manifest_sha256": release._sha256(
            source_dir / "generation-manifest.json"
        ),
        "source_health_sha256": source_bindings["health_sha256"],
        "source_health_report_sha256": source_bindings[
            "health_report_sha256"
        ],
        "restriction_evidence_sha256": source_bindings[
            "restriction_evidence_sha256"
        ],
        "restriction_events_sha256": source_bindings[
            "restriction_events_sha256"
        ],
        "restriction_profile_sha256": source_bindings[
            "restriction_profile_sha256"
        ],
        "inventory_approval_sha256": source_bindings[
            "inventory_approval_sha256"
        ],
        "recalc_signals_sha256": source_bindings[
            "recalc_signals_sha256"
        ],
        "segmentation_generation_id": segmentation_manifest["generation_id"],
        "segmentation_manifest_sha256": release._sha256(
            segmentation_dir / "generation-manifest.json"
        ),
        "cone_certificate_sha256": segmentation_policy[
            "cone_certificate_sha256"
        ],
    }
    task_stage = tmp_path / "restricted-task-stage"
    (task_stage / "environment").mkdir(parents=True)
    shutil.copyfile(
        source_path,
        task_stage / "environment" / f"{workbook_id}-inputs.xlsx",
    )
    (task_stage / "instruction.md").write_text(
        "Rebuild the ordinary output.\n",
        encoding="utf-8",
    )
    release_root = tmp_path / "restricted-release"
    _, task_manifest = release.publish_task_generation(
        task_stage,
        release_root,
        workbook_id,
        bindings=task_bindings,
    )

    legacy_source_root = tmp_path / "legacy-source"
    legacy_segmentation_root = tmp_path / "legacy-segmentation"
    legacy_source_root.mkdir()
    legacy_segmentation_root.mkdir()
    (legacy_source_root / "current.json").write_text(json.dumps({
        "schema_version": "source-current/v1",
        "generation_id": "legacy-source",
    }))
    (legacy_segmentation_root / "current.json").write_text(json.dumps({
        "schema_version": "segmentation-current/v1",
        "generation_id": "legacy-segmentation",
    }))
    legacy_task = tmp_path / "restricted-legacy-task"
    legacy_task.mkdir()
    (legacy_task / "instruction.md").write_text(
        "Legacy task.\n",
        encoding="utf-8",
    )
    _, snapshot = release.freeze_legacy_snapshot(
        release_root,
        workbook_id,
        source_root=legacy_source_root,
        segmentation_root=legacy_segmentation_root,
        task_dir=legacy_task,
    )
    assert not (source_root / "current.json").exists()
    assert not (segmentation_root / "current.json").exists()

    release_dir, release_manifest = release.publish_release(
        release_root,
        workbook_id,
        source_root=source_root,
        source_generation_id=source_manifest["generation_id"],
        segmentation_root=segmentation_root,
        segmentation_generation_id=segmentation_manifest["generation_id"],
        task_generation_id=task_manifest["generation_id"],
        expected_current_release_id=None,
        legacy_snapshot_hash=snapshot["snapshot_hash"],
    )
    resolved_release, resolved_release_manifest = (
        release.resolve_current_release(
            release_root,
            source_root=source_root,
            segmentation_root=segmentation_root,
        )
    )
    assert resolved_release == release_dir
    assert resolved_release_manifest == release_manifest
    assert release_manifest["prior_release_id"] is None
    assert release_manifest["bindings"]["cone_certificate_sha256"] == (
        certificate["certificate_sha256"]
    )


@pytest.mark.parametrize(
    "phase,pointer_exists",
    [
        ("before_immutable_release_write", False),
        ("after_immutable_release_write", False),
        ("before_pointer_cas", False),
        ("after_pointer_cas", True),
        ("compatibility_materialization", True),
    ],
)
def test_fault_hooks_never_expose_partial_release(
    tmp_path, monkeypatch, phase, pointer_exists
):
    case = _fixture(tmp_path, monkeypatch)

    def fault(observed):
        if observed == phase:
            raise RuntimeError(phase)

    with pytest.raises(RuntimeError, match=phase):
        _publish(case, fault=fault)
    pointer = case["release_root"] / "current-release.json"
    assert pointer.exists() is pointer_exists
    if pointer_exists:
        release.resolve_current_release(
            case["release_root"],
            source_root=case["source_root"],
            segmentation_root=case["segmentation_root"],
        )


def test_stale_pointer_cas_and_readers_see_complete_tuple(tmp_path, monkeypatch):
    case = _fixture(tmp_path, monkeypatch)
    _, first = _publish(case)
    with pytest.raises(release.ReleasePublicationError, match="stale"):
        _publish(case, expected=None)

    results = []

    def read():
        _, value = release.resolve_current_release(
            case["release_root"],
            source_root=case["source_root"],
            segmentation_root=case["segmentation_root"],
        )
        results.append(value["release_id"])

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [first["release_id"]] * 8


def test_successor_cas_rejects_tampered_current_release(tmp_path, monkeypatch):
    case = _fixture(tmp_path, monkeypatch)
    current_dir, first = _publish(case)
    (current_dir / "release-manifest.json").write_text("{}\n")

    with pytest.raises(release.ReleasePublicationError):
        _publish(
            case,
            expected=first["release_id"],
            legacy_snapshot_hash=None,
        )


def test_unknown_version_and_binding_downgrade_rejected(tmp_path, monkeypatch):
    case = _fixture(tmp_path, monkeypatch)
    directory, _ = _publish(case)
    path = directory / "release-manifest.json"
    value = json.loads(path.read_text())
    value["identity"]["versions"]["source_policy_version"] = "source-recalc-policy/v1"
    path.write_text(json.dumps(value))
    with pytest.raises(release.ReleasePublicationError):
        release.resolve_current_release(
            case["release_root"],
            source_root=case["source_root"],
            segmentation_root=case["segmentation_root"],
        )


def test_external_package_artifacts_are_blocked(tmp_path):
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    _xlsx(task / "environment" / "case-inputs.xlsx", external=True)
    with pytest.raises(release.ReleasePublicationError, match="external"):
        release.publish_task_generation(
            task,
            tmp_path / "release",
            "case",
            bindings={
                "source_generation_id": "a" * 64,
                "segmentation_generation_id": "b" * 64,
            },
        )


@pytest.mark.parametrize(
    "relationship_type,target_mode",
    [
        (
            "http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/externalLink",
            None,
        ),
        ("urn:test:worksheet", "External"),
    ],
)
def test_relationship_only_external_workbooks_are_blocked(
    tmp_path, relationship_type, target_mode
):
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    _xlsx(
        task / "environment" / "case-inputs.xlsx",
        relationship_type=relationship_type,
        target_mode=target_mode,
    )

    with pytest.raises(release.ReleasePublicationError, match="relationship"):
        release.publish_task_generation(
            task,
            tmp_path / "release",
            "case",
            bindings={
                "source_generation_id": "a" * 64,
                "segmentation_generation_id": "b" * 64,
            },
        )


def test_bracket_text_without_external_relationship_is_allowed(tmp_path):
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    _xlsx(
        task / "environment" / "case-inputs.xlsx",
        bracket_text="[Budget scenario]",
    )

    directory, _ = release.publish_task_generation(
        task,
        tmp_path / "release",
        "case",
        bindings={
            "source_generation_id": "a" * 64,
            "segmentation_generation_id": "b" * 64,
        },
    )
    assert directory.is_dir()


def test_mask_task_and_normalizer_preserve_pipeline_bindings(
    tmp_path, monkeypatch
):
    generation_id = "b" * 64
    generation = tmp_path / "seg-generation"
    generation.mkdir()
    (generation / "generation-manifest.json").write_text("{}\n")
    inputs = tmp_path / "case-inputs.xlsx"
    inputs.write_bytes(b"masked")
    bindings = {
        "release_id": None,
        "source_generation_id": "a" * 64,
        "source_manifest_sha256": "1" * 64,
        "source_health_sha256": "2" * 64,
        "source_health_report_sha256": "3" * 64,
        "restriction_evidence_sha256": "4" * 64,
        "restriction_events_sha256": "5" * 64,
        "restriction_profile_sha256": "6" * 64,
        "segmentation_generation_id": generation_id,
        "segmentation_manifest_sha256": "7" * 64,
        "cone_certificate_sha256": "8" * 64,
        "task_generation_id": None,
        "task_generation_hash": None,
        "task_manifest_sha256": None,
    }
    sidecar = segmentation_publication.write_inputs_sidecar(
        inputs,
        generation,
        {"generation_id": generation_id},
        pipeline_bindings=bindings,
    )
    assert segmentation_publication.validate_inputs_sidecar(
        inputs,
        expected_generation_id=generation_id,
        generation_dir=generation,
        expected_pipeline_bindings=bindings,
    )["pipeline_bindings"] == bindings

    task = tmp_path / "task"
    xl_output_task.emit(
        task,
        "case",
        "model",
        inputs,
        "instruction",
        {"Sheet!B1": 1},
        [{"name": "Output", "band": "Sheet!B1", "refs": ["Sheet!B1"]}],
        {},
        audit_root=tmp_path / "audit",
        inputs_generation=json.loads(sidecar.read_text()),
        inputs_generation_path=sidecar,
        pipeline_bindings=bindings,
    )
    assert json.loads(
        (task / "tests" / "pipeline_bindings.json").read_text()
    ) == bindings

    monkeypatch.chdir(tmp_path)
    run = tmp_path / "runs" / "1234-variable-sources"
    run.mkdir(parents=True)
    draft = {"rows": []}
    (run / "draft.json").write_text(json.dumps(draft))
    gen_normalizer.emit(
        "1234", draft, [], pipeline_bindings=bindings
    )
    generated = run / "normalize_1234.py"
    namespace = {"__name__": "__main__", "__file__": str(generated)}
    exec(generated.read_text(), namespace)
    assert json.loads(
        (run / "normalization_report.json").read_text()
    )["pipeline_bindings"] == bindings
    assert json.loads(
        (run / "normalized.json").read_text()
    )["pipeline_bindings"] == bindings


def test_legacy_snapshot_is_immutable_and_never_used_after_cas(
    tmp_path, monkeypatch
):
    case = _fixture(tmp_path, monkeypatch, freeze_legacy=False)
    source_pointer = case["source_root"] / "current.json"
    source_pointer.parent.mkdir(exist_ok=True)
    source_pointer.write_text(json.dumps({
        "schema_version": "source-current/v1",
        "generation_id": case["source_id"],
    }))
    seg_pointer = case["segmentation_root"] / "current.json"
    seg_pointer.parent.mkdir(exist_ok=True)
    seg_pointer.write_text(json.dumps({
        "schema_version": "segmentation-current/v1",
        "generation_id": case["segmentation_id"],
    }))
    legacy_task = tmp_path / "legacy-task"
    legacy_task.mkdir()
    (legacy_task / "instruction.md").write_text("legacy\n")
    _, snapshot = release.freeze_legacy_snapshot(
        case["release_root"],
        case["workbook"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
        task_dir=legacy_task,
    )
    effective = release.resolve_effective_release(
        case["release_root"],
        source_root=case["source_root"],
        segmentation_root=case["segmentation_root"],
    )
    assert effective["mode"] == "frozen-v1"
    source_pointer.write_text('{"schema_version":"source-current/v1","changed":true}')
    with pytest.raises(release.ReleasePublicationError, match="immutable"):
        release.freeze_legacy_snapshot(
            case["release_root"],
            case["workbook"],
            source_root=case["source_root"],
            segmentation_root=case["segmentation_root"],
            task_dir=legacy_task,
        )
    _publish(case, legacy_snapshot_hash=snapshot["snapshot_hash"])
    with pytest.raises(release.ReleasePublicationError, match="disabled"):
        release.resolve_legacy_snapshot(case["release_root"])
