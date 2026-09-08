"""The output retention gate: one cone retry, then deliver flagged, never park."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.batch import recovery_worker as worker_module
from scripts.batch.recovery_worker import QueueRow, ReadyRecord, RecoveryWorker, ReleaseCandidate
from tests.test_recovery_worker import _attempt, _candidate, _config, _stub_ready


def _manifest_with_retention(task_dir: Path, *, unexplained: list[str], pruned: int = 0,
                             manual: list[dict] | None = None, auto: int = 4) -> None:
    manual = manual or []
    retained = auto - len(unexplained) - pruned - len(manual)
    record = {
        "schema_version": "output-retention/v1",
        "threshold": 6.0,
        "candidates_listed": auto + 2,
        "candidates_auto_included": auto,
        "retained": retained,
        "pruned": pruned,
        "manual_excluded": len(manual),
        "adjudicated_excluded": 0,
        "unexplained_excluded": len(unexplained),
        "reason_codes": sorted({"output_mismatch"} if pruned else set()),
        "bands": {
            "retained": [f"S!R{i}" for i in range(retained)],
            "pruned": [{"band": f"S!P{i}", "reasons": ["output_mismatch"]} for i in range(pruned)],
            "manual_excluded": manual,
            "adjudicated_excluded": [],
            "unexplained_excluded": list(unexplained),
        },
    }
    (task_dir / "tests" / "segmentation_generation_manifest.json").write_text(
        json.dumps({"schema_version": "segmentation-generation/v2", "output_retention": record}),
        encoding="utf-8",
    )


def _worker_with_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, attempt_count=3):
    config, rows, records = _config(tmp_path, attempt_count=attempt_count)
    row = rows[0]

    def launcher(**kwargs: object) -> dict[str, object]:
        Path(str(kwargs["report_path"])).write_text("report\n", encoding="utf-8")
        return {"status": "completed", "return_code": 0}

    worker = RecoveryWorker(config, investigator_launcher=launcher)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path, amd=False), source_sha256=row.run_source_sha256)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "assert_canonical_grader_provenance", lambda *_a: None)
    return config, row, worker, candidate


def test_unexplained_exclusion_gets_one_cone_retry_then_ships_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, row, worker, candidate = _worker_with_candidate(tmp_path, monkeypatch)
    # The agent dropped an auto-included output with no marker, twice.
    _manifest_with_retention(candidate.task_dir, unexplained=["S!X1"])
    candidate = replace(candidate, bundle_sha256=worker_module.tree_sha256(candidate.task_dir))
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    seen: list[dict[str, object]] = []
    delivered: list[dict[str, object]] = []

    def run_generation(_row: QueueRow, _record: ReadyRecord, _inventory, prior, index: int):
        seen.append({
            "attempt": index,
            "ladder": worker._prompt_ladder_index(index),
            "cone_retry": dict(worker._cone_retry or {}),
            "paperwork": worker._paperwork_retry,
            "prompt": worker._generation_prompt(row, tmp_path / "wb.xlsx", None, prior, index),
        })
        return _attempt(tmp_path, index + 1, "published\nRECOVERY_USED: NO\n")

    def approve(*_args: object, **_kwargs: object) -> dict[str, object]:
        delivered.append(dict(worker._active_output_retention or {}))
        return {"status": "test"}

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_approve_and_deliver", approve)

    result = worker.process(row)

    assert result["status"] == "generated"
    assert [item["attempt"] for item in seen] == [0, 1]
    # The retry stays on the same ladder step and is not a paperwork retry.
    assert [item["ladder"] for item in seen] == [0, 0]
    assert seen[1]["paperwork"] is None
    assert seen[1]["cone_retry"]["gate"] == worker_module.RETENTION_GATE
    assert any("S!X1" in str(f) for f in seen[1]["cone_retry"]["findings"])
    assert "output retention gate" in seen[1]["prompt"]
    assert "--prune-failing-outputs" in seen[1]["prompt"]
    assert "# excluded:" in seen[1]["prompt"]
    # Second failure: ship anyway, visibly.
    assert len(delivered) == 1
    assert delivered[0]["gate"] == "override_after_retry"
    assert delivered[0]["cone_retry_used"] == 1
    checkpoint = json.loads((config.state / "checkpoints" / f"{row.workbook_id}.json").read_text())
    assert checkpoint["cone_retry_count"] == 1
    assert checkpoint.get("paperwork_retry_count", 0) == 0


def test_clean_cone_passes_the_gate_without_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, row, worker, candidate = _worker_with_candidate(tmp_path, monkeypatch)
    _manifest_with_retention(
        candidate.task_dir,
        unexplained=[],
        pruned=2,
        manual=[{"band": "S!M0", "code": "non_financial", "base": "non_financial", "note": "kind=label"}],
    )
    candidate = replace(candidate, bundle_sha256=worker_module.tree_sha256(candidate.task_dir))
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    attempts: list[int] = []
    delivered: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker, "_run_generation",
        lambda *args, **_k: (attempts.append(int(args[-1])), _attempt(tmp_path, 1, "published\nRECOVERY_USED: NO\n"))[1],
    )
    monkeypatch.setattr(
        worker, "_approve_and_deliver",
        lambda *_a, **_k: (delivered.append(dict(worker._active_output_retention or {})), {"status": "test"})[1],
    )

    result = worker.process(row)

    assert result["status"] == "generated"
    assert attempts == [0]
    assert delivered[0]["gate"] == "pass"
    assert delivered[0]["cone_retry_used"] == 0
    summary = worker._retention_summary()
    assert summary["pruned"] == 2 and summary["manual_excluded"] == 1
    assert summary["retained"] == 1


def test_memory_exclusion_needs_a_recorded_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, row, worker, candidate = _worker_with_candidate(tmp_path, monkeypatch)
    _manifest_with_retention(
        candidate.task_dir,
        unexplained=[],
        manual=[{"band": "S!M0", "code": "memory", "base": "memory", "note": "peak 120 GiB"}],
    )
    gate = worker._output_retention_gate(row, candidate)
    assert any("no matching attempt evidence" in f for f in gate["findings"])

    # A durable attempt record showing the OOM kill is the only accepted proof.
    attempt_dir = config.state / "generation" / "attempts" / row.workbook_id / "attempt-0001"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "result.json").write_text(json.dumps({
        "status": "failed",
        "execution_limits": {"limit_reason": "cgroup_oom", "memory_events": {"oom_kill": 1}},
    }), encoding="utf-8")
    gate = worker._output_retention_gate(row, candidate)
    assert gate["findings"] == []
    assert gate["resource_evidence"] == {"memory": True, "budget": False}

    (attempt_dir / "result.json").write_text(json.dumps({"status": "timed_out"}), encoding="utf-8")
    assert worker._output_retention_gate(row, candidate)["resource_evidence"]["budget"] is True


def test_bundle_without_a_record_is_not_retried_but_is_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Legacy fixture bundles carry a non-JSON manifest placeholder.
    config, row, worker, candidate = _worker_with_candidate(tmp_path, monkeypatch)
    gate = worker._output_retention_gate(row, candidate)
    assert gate["record"] is None and gate["findings"] == [] and gate["no_record"] is True
    delivered: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker, "_run_generation",
        lambda *args, **_k: _attempt(tmp_path, 1, "published\nRECOVERY_USED: NO\n"),
    )
    monkeypatch.setattr(
        worker, "_approve_and_deliver",
        lambda *_a, **_k: (delivered.append(dict(worker._active_output_retention or {})), {"status": "test"})[1],
    )
    assert worker.process(row)["status"] == "generated"
    assert delivered[0]["gate"] == "no_record"


def test_amd_must_name_dropped_outputs_only_when_there_are_any(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    worker_module.validate_recovery_amd(candidate.task_dir, None)  # no record: no new term
    _manifest_with_retention(candidate.task_dir, unexplained=[], pruned=1)
    with pytest.raises(worker_module.RecoveryError, match="pruned/excluded outputs"):
        worker_module.validate_recovery_amd(candidate.task_dir, None)
    amd = candidate.task_dir / "a.md"
    amd.write_text(
        amd.read_text(encoding="utf-8")
        + "\n## Pruned outputs\nPruned S!P0 (output_mismatch) by xl_segment --prune-failing-outputs.\n",
        encoding="utf-8",
    )
    worker_module.validate_recovery_amd(candidate.task_dir, None)
    _manifest_with_retention(candidate.task_dir, unexplained=[], pruned=2)
    with pytest.raises(worker_module.RecoveryError, match="S!P1"):
        worker_module.validate_recovery_amd(candidate.task_dir, None)


def test_delivery_sidecar_carries_the_retention_summary(tmp_path: Path) -> None:
    from scripts.batch.recovery_worker import deliver_bundle

    config, rows, _ = _config(tmp_path)
    candidate = replace(_candidate(tmp_path, amd=False), source_sha256=rows[0].run_source_sha256)
    summary = {"gate": "pass", "cone_retry_used": 0, "retained": 3, "candidates_auto_included": 4}
    sidecar = deliver_bundle(
        config, rows[0], candidate,
        modified=False, verification_report=None, verification_attempt=None,
        output_retention=summary,
    )
    assert sidecar["output_retention"] == summary
    on_disk = json.loads(
        (config.output / rows[0].batch_id / f"{rows[0].workbook_id}-outputs.verification.json").read_text()
    )
    assert on_disk["output_retention"]["gate"] == "pass"
    # A sidecar with the new key is still a valid delivery for the resume path.
    assert worker_module._read_valid_delivery(config, rows[0]) is not None


def test_ladder_step_d_is_prune_not_shrink() -> None:
    step = worker_module.RECOVERY_LADDER[3]
    assert "--prune-failing-outputs" in step
    assert "smaller" not in step
    assert worker_module.MAX_CONE_RETRIES == 1
