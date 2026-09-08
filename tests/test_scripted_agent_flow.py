"""End-to-end row flows with the scripted agent binary (tests/harness/scripted_agent.py).

Unlike test_recovery_worker.py, nothing between the worker and the agent is
monkeypatched: generation, the fairness verifier and the incident
investigator are real subprocesses speaking stream-json through
hardened_runner and the default investigator launcher. Only release
inspection is stubbed (the fake agent does not build a real release).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.batch import recovery_worker as worker_module
from scripts.batch.recovery_worker import RecoveryWorker

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_recovery_worker as base  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "harness" / "scripted_agent.py"


def _minimal_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "ready"
    workbook.save(path)
    workbook.close()


def _scripted_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs):
    config, rows, records = base._config(tmp_path, **kwargs)
    # Replace the inert `agent` stub with the scripted one.
    shutil.copy2(HARNESS, config.agent_binary)
    config.agent_binary.chmod(0o755)
    # hardened_runner validates the published workbook behind the ready
    # record before launching, so give each row a real (tiny) OOXML file.
    published = config.state / "published"
    published.mkdir(parents=True, exist_ok=True)
    from scripts.batch.hardened_runner import ReadyRecord

    for row in rows:
        workbook = published / f"{row.workbook_id}.xlsx"
        _minimal_xlsx(workbook)
        records[row.workbook_id] = ReadyRecord.create(
            workbook_id=row.workbook_id,
            source_file=f"{row.workbook_id}.xlsx",
            source_sha256=row.run_source_sha256,
            xlsx_file=f"{row.workbook_id}.xlsx",
            xlsx_sha256=worker_module.sha256_file(workbook),
            xlsx_size=workbook.stat().st_size,
            conversion_engine="test",
        )
    monkeypatch.setenv("SCRIPTED_AGENT_LOG", str(tmp_path / "agents.jsonl"))
    for name in list(os.environ):
        if name.startswith("SCRIPTED_") and name != "SCRIPTED_AGENT_LOG":
            monkeypatch.delenv(name)
    return config, rows, records


def _agents(tmp_path: Path) -> list[str]:
    path = tmp_path / "agents.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line)["role"] for line in path.read_text().splitlines() if line.strip()]


def _modified_candidate(tmp_path: Path, row):
    candidate = base._candidate(tmp_path)
    amd = candidate.task_dir / "a.md"
    amd.write_text(amd.read_text(encoding="utf-8") + "\nChanged files: xl_seg/emit.py\n", encoding="utf-8")
    return replace(
        candidate,
        source_sha256=row.run_source_sha256,
        bundle_sha256=worker_module.tree_sha256(candidate.task_dir),
    )


def test_modified_row_passes_verifier_and_delivers_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _scripted_config(tmp_path, monkeypatch)
    row = rows[0]
    worker = RecoveryWorker(config)
    base._stub_ready(worker, records, monkeypatch)
    candidate = _modified_candidate(tmp_path, row)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")
    monkeypatch.setenv("SCRIPTED_GENERATION_TOUCH", "xl_seg/emit.py")
    monkeypatch.setenv("SCRIPTED_FAIRNESS", "PASS")

    result = worker.process(row)

    assert result["status"] == "generated", result
    # A modified bundle gets the post-delivery recurrence review; it is
    # analysis only and the row is still generated.
    assert _agents(tmp_path) == ["generation", "verifier", "investigator"]
    assert result["incident_report"]["verdict"]["route"] == "worktree_fix"
    sidecar = json.loads((config.output / row.batch_id / "0001-outputs.verification.json").read_text())
    assert sidecar["modified"] is True
    assert sidecar["fairness_checks"]["verdict"] == "PASS"
    # The command log came from the real attempt stream written by hardened_runner.
    evidence = json.loads(Path(sidecar["recovery_evidence"]["state_path"]).read_text())
    commands = [c["command"] for c in evidence["attempts"][0]["shell_commands"]]
    assert commands == ["python xl_source_health.py --workbook 0001", "bash build_one.sh 0001"]
    assert evidence["attempts"][0]["file_writes"] == ["xl_seg/emit.py"]
    assert "xl_seg/emit.py" in evidence["changed_files"]
    # The trailing "No follow-up is needed." message did not break the verdict.
    report = (config.output / row.batch_id / "0001-outputs.fairness.md").read_text()
    assert worker_module.fairness_passed(report)


def test_structured_fairness_fail_routes_through_real_investigator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _scripted_config(tmp_path, monkeypatch)
    row = rows[0]
    worker = RecoveryWorker(config)
    base._stub_ready(worker, records, monkeypatch)
    candidate = _modified_candidate(tmp_path, row)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")
    monkeypatch.setenv("SCRIPTED_GENERATION_TOUCH", "xl_seg/emit.py")
    monkeypatch.setenv("SCRIPTED_FAIRNESS", "FAIL")
    monkeypatch.setenv("SCRIPTED_FAIRNESS_CHECK", "answer_leakage")
    monkeypatch.setenv("SCRIPTED_INCIDENT_ROUTE", "worktree_fix")

    result = worker.process(row)

    assert result["status"] == "fairness_retry"
    assert _agents(tmp_path) == ["generation", "verifier", "investigator"]
    assert result["fairness_verdict"]["checks"]["answer_leakage"] == "fail"
    assert result["incident_report"]["verdict"]["route"] == "worktree_fix"
    assert result["incident_report"]["worker_fix_needed"] is False
    classified = worker_module.classify_failure(result)
    assert (classified["class"], classified["subclass"]) == ("fairness_leak", "scripted_defect")
    worker._record_row_close(row, result)
    record = base._failure_record(config, "0001")
    assert record["class"] == "fairness_leak" and record["gate"] == 6
    assert not (config.output / row.batch_id / "0001-outputs").exists()


def test_controller_route_parks_row_and_raises_exit_code_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _scripted_config(tmp_path, monkeypatch, attempt_count=1, row_attempt_cap=2)
    worker = RecoveryWorker(config)
    base._stub_ready(worker, records, monkeypatch)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(TypeError("'NoneType' object is not subscriptable")),
    )
    monkeypatch.setenv("SCRIPTED_INCIDENT_ROUTE", "controller")

    ledger = worker.run()

    assert ledger["records"][0]["status"] == "worker_fix_needed"
    assert _agents(tmp_path).count("generation") == 1  # parked immediately, no retry
    assert worker.run_state["controller_fingerprints"]
    assert worker_module.exit_code_for(ledger, worker.run_state) == worker_module.EXIT_CONTROLLER_FIX
    record = base._failure_record(config, "0001")
    assert record["class"] == "controller"
    # The lane fix step would pick this up.
    from scripts.batch import lane_fix

    found = lane_fix.collect_controller_failures(config.state)
    assert [item["workbook_id"] for item in found] == ["0001"]
    assert "scripted cause" in found[0]["incident_report"]


def test_bare_investigator_verdict_never_parks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _scripted_config(tmp_path, monkeypatch, attempt_count=1, row_attempt_cap=1)
    worker = RecoveryWorker(config)
    base._stub_ready(worker, records, monkeypatch)
    monkeypatch.setattr(
        worker, "_inspect_release", lambda _row: (_ for _ in ()).throw(TypeError("boom"))
    )
    monkeypatch.setenv("SCRIPTED_INCIDENT_ROUTE", "controller")
    monkeypatch.setenv("SCRIPTED_INCIDENT_BARE", "1")

    result = worker.process(rows[0])

    assert result["status"] == "recovery_pending"
    assert result.get("worker_fix_incident") is None
    incidents = result.get("incident_reports") or [result.get("incident_report")]
    assert incidents and all(item["worker_fix_needed"] is False for item in incidents)
    assert incidents[-1]["verdict_error"] == "missing_block"


def test_verifier_without_block_is_rerun_once_then_recorded_as_verifier_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _scripted_config(tmp_path, monkeypatch)
    row = rows[0]
    worker = RecoveryWorker(config)
    base._stub_ready(worker, records, monkeypatch)
    candidate = _modified_candidate(tmp_path, row)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")
    monkeypatch.setenv("SCRIPTED_GENERATION_TOUCH", "xl_seg/emit.py")
    monkeypatch.setenv("SCRIPTED_FAIRNESS", "NOBLOCK")

    result = worker.process(row)

    assert result["status"] == "recovery_pending"
    assert result["verifier_error"] == "missing_block"
    assert _agents(tmp_path) == ["generation", "verifier", "verifier"]
    assert worker_module.classify_failure(result)["class"] == "verifier_error"
