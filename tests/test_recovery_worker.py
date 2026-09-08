from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.batch import recovery_worker as worker_module
from scripts.batch.hardened_runner import ReadyRecord
from scripts.batch.memory_governor import BudgetTimeout
from scripts.batch.recovery_worker import (
    Config,
    PolicyViolation,
    QueueRow,
    RecoveryOrchestrationError,
    RecoveryWorker,
    ReleaseCandidate,
    create_isolated_worktree,
    deliver_bundle,
    tree_sha256,
    _assert_inspect_candidate,
    _combine_fairness_reports,
    _preserve_failed_release,
)


INVENTORY_DOCUMENT_HASH = "1" * 64
BASELINE_EMIT = "BASELINE_EMIT = True\n"
RECOVERY_OK_AFTER_NO_RELEASE = frozenset({"recovery_pending", "task_fix_needed"})


def _config(
    tmp_path: Path,
    *,
    workbook_ids: tuple[str, ...] = ("0001",),
    attempt_count: int = 2,
    governor: bool = False,
    host_bytes: int = 100 * 1024**3,
    lane_count: int = 2,
    min_free_disk_bytes: int = 0,
    disk_high_water_bytes: int = 0,
    row_attempt_cap: int = 4,
    task_limit: int = 0,
) -> tuple[Config, list[QueueRow], dict[str, ReadyRecord]]:
    repo = tmp_path / "baseline"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "xl_seg").mkdir()
    (repo / "xl_seg" / "emit.py").write_text(BASELINE_EMIT, encoding="utf-8")
    (repo / "run_grader.py").write_text("GRADER = True\n", encoding="utf-8")
    (repo / "grader" / "finance_grader").mkdir(parents=True)
    (repo / "grader" / "run_grader.py").write_text(
        "tests/run_grader.py\n", encoding="utf-8"
    )
    (repo / "grader" / "finance_grader" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (repo / "answer_key.json").write_text("{}\n", encoding="utf-8")
    (repo / "xl_source_health.py").write_text("HEALTH = True\n", encoding="utf-8")
    files = {
        "xl_seg/emit.py": {
            "sha256": worker_module.sha256_file(repo / "xl_seg" / "emit.py"),
            "protected": False,
        },
        "run_grader.py": {
            "sha256": worker_module.sha256_file(repo / "run_grader.py"),
        },
        "grader/run_grader.py": {
            "sha256": worker_module.sha256_file(repo / "grader" / "run_grader.py"),
        },
        "grader/finance_grader/__init__.py": {
            "sha256": worker_module.sha256_file(
                repo / "grader" / "finance_grader" / "__init__.py"
            ),
        },
        "answer_key.json": {
            "sha256": worker_module.sha256_file(repo / "answer_key.json"),
        },
        "xl_source_health.py": {
            "sha256": worker_module.sha256_file(repo / "xl_source_health.py"),
            "protected": False,
        },
    }
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"files": files}), encoding="utf-8")
    rows: list[QueueRow] = []
    records: dict[str, ReadyRecord] = {}
    inventory_records = []
    ledger = []
    for workbook_id in workbook_ids:
        source = tmp_path / f"{workbook_id}.xlsx"
        source.write_bytes(f"immutable-source-{workbook_id}".encode("utf-8"))
        source_hash = worker_module.sha256_file(source)
        rows.append(
            QueueRow(
                batch_id="batch-001",
                workbook_id=workbook_id,
                run_source_path=source,
                run_source_sha256=source_hash,
                source_format="xlsx",
                source_sha256=source_hash,
                source_size=source.stat().st_size,
            )
        )
        records[workbook_id] = ReadyRecord.create(
            workbook_id=workbook_id,
            source_file=f"{workbook_id}.xlsx",
            source_sha256=source_hash,
            xlsx_file=f"{workbook_id}.xlsx",
            xlsx_sha256=source_hash,
            xlsx_size=source.stat().st_size,
            conversion_engine="test",
        )
        inventory_records.append(
            {"workbook_id": workbook_id, "sha256": source_hash}
        )
        ledger.append(
            {
                "workbook_id": workbook_id,
                "source_sha256": source_hash,
                "original_source": {
                    "path": f"{workbook_id}.xlsx",
                    "sha256": source_hash,
                    "size_bytes": source.stat().st_size,
                },
            }
        )
    inventory = repo / "selected-inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "inventory_sha256": INVENTORY_DOCUMENT_HASH,
                "records": inventory_records,
            }
        ),
        encoding="utf-8",
    )
    files["selected-inventory.json"] = {
        "sha256": worker_module.sha256_file(inventory),
        "protected": False,
    }
    baseline.write_text(json.dumps({"files": files}), encoding="utf-8")
    approval_registry = tmp_path / "approval.json"
    approval_registry.write_text(
        json.dumps(
            {
                "approvals": [
                    {
                        "batch_id": "batch-002-restricted-native-19-v3",
                        "inventory_artifact_sha256": worker_module.sha256_file(
                            inventory
                        ),
                        "inventory_sha256": INVENTORY_DOCUMENT_HASH,
                        "batch_source_ledger": ledger,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            [
                {
                    **row.__dict__,
                    "run_source_path": str(row.run_source_path),
                }
                for row in rows
            ]
        ),
        encoding="utf-8",
    )
    agent = tmp_path / "agent"
    agent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    agent.chmod(0o755)
    config = Config(
        queue=queue,
        baseline_repo=repo,
        work_root=tmp_path / "worktrees",
        state=tmp_path / "state",
        output=tmp_path / "output",
        code_baseline=baseline,
        model="gpt-5.6-sol-high",
        timeout_seconds=30,
        attempt_count=attempt_count,
        task_limit=task_limit,
        agent_binary=agent,
        inventory_registry=approval_registry,
        approval_batch_id="batch-002-restricted-native-19-v3",
        inventory=inventory,
        large_workbooks=frozenset(),
        measured_timeout_seconds=7_200,
        large_timeout_seconds=14_400,
        # Default off, and always pointed at tmp_path when on, so a test run on
        # a Linux host never touches the real shared budget file under
        # /var/tmp/harbor-memory.
        memory_governor_enabled=governor,
        memory_budget_dir=tmp_path / "memory-budget",
        memory_host_bytes=host_bytes,
        memory_reserve_bytes=10 * 1024**3,
        lane_id="lane-a",
        lane_count=lane_count,
        # The fleet default floor is 120 GiB, which a dev box may not have.
        # Off unless a test is exercising the gate.
        min_free_disk_bytes=min_free_disk_bytes,
        disk_high_water_bytes=disk_high_water_bytes,
        # Small lifetime cap: immediate retry would otherwise run 18 stub
        # attempts per failing row in every test.
        row_attempt_cap=row_attempt_cap,
    )
    return config, rows, records


def _fairness_block(
    verdict: str = "PASS",
    failing: dict[str, tuple[str, int | None]] | None = None,
) -> str:
    """A recovery-fairness/v1 block; ``failing`` maps check -> (subclass, gate)."""

    failing = failing or {}
    checks = {name: ("fail" if name in failing else "pass") for name in worker_module.FAIRNESS_CHECKS}
    findings = [
        {"check": name, "subclass": subclass, "gate": gate, "artifact": "instruction.md", "detail": "x"}
        for name, (subclass, gate) in failing.items()
    ]
    body = json.dumps(
        {
            "schema_version": worker_module.FAIRNESS_BLOCK_SCHEMA,
            "verdict": verdict,
            "checks": checks,
            "findings": findings,
        },
        indent=2,
    )
    return f"```json\n{body}\n```"


def PASS_REPORT(prose: str = "Independent review.") -> str:
    return f"{prose}\n{_fairness_block('PASS')}\nFAIRNESS_VERDICT: PASS\n"


def FAIL_REPORT(
    prose: str = "Independent review.",
    failing: dict[str, tuple[str, int | None]] | None = None,
) -> str:
    failing = failing or {"solvability": ("units_percent_vs_ratio", 6)}
    return f"{prose}\n{_fairness_block('FAIL', failing)}\nFAIRNESS_VERDICT: FAIL\n"


def _incident_block(verdict: str, route: str, subclass: str, component: str = "", gate: int | None = None) -> str:
    body = json.dumps(
        {
            "schema_version": worker_module.INCIDENT_VERDICT_SCHEMA,
            "verdict": verdict,
            "route": route,
            "subclass": subclass,
            "gate": gate,
            "component": component,
            "cause": "x",
            "evidence": ["state/task-evidence/0001.json"],
            "fix_hint": "y",
        },
        indent=2,
    )
    return f"```json\n{body}\n```\n"


def CONTROLLER_VERDICT(subclass: str = "unexpected_exception", component: str = "recovery_worker.py") -> str:
    return _incident_block("WORKER_FIX_NEEDED", "controller", subclass, component)


def TASK_VERDICT(route: str = "worktree_fix", subclass: str = "segmentation", gate: int | None = 6) -> str:
    return _incident_block("TASK_LOCAL_OR_EXTERNAL", route, subclass, gate=gate)


def _failure_record(config: Config, workbook_id: str) -> dict[str, object]:
    return json.loads(
        (config.state / "failures" / f"{workbook_id}.json").read_text(encoding="utf-8")
    )


def _bundle(path: Path, workbook_id: str = "0001", *, amd: bool = True) -> None:
    (path / "environment").mkdir(parents=True)
    (path / "tests").mkdir()
    for relative in worker_module.REQUIRED_BUNDLE_DIRECTORIES:
        directory = path / relative
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    for relative in worker_module.REQUIRED_BUNDLE_FILES:
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{relative}\n", encoding="utf-8")
    (path / "environment" / f"{workbook_id}-inputs.xlsx").write_bytes(b"test-input")
    if amd:
        (path / "a.md").write_text(
            """
# Recovery record

## Exact blocker
The original blocker was a volatile external cell that failed the source-health
stage.

## Evidence and failing stage
Evidence is the failing stage log from source-health. The failing stage is
source-health publication.

## Exact fix
The exact fix is a disclosed deterministic assumption for that external cell.

## Changed files
Changed files: xl_seg/emit.py.

## Before / after
Before: gate FAIL. After: gate PASS.

## Commands
Commands: python -m xl_seg.emit --workbook 0001

## Verifier outcome
Verifier outcome is pending independent fairness.
""".strip()
            + "\n",
            encoding="utf-8",
        )


def _candidate(
    tmp_path: Path, workbook_id: str = "0001", *, amd: bool = True
) -> ReleaseCandidate:
    task = tmp_path / f"task-generation-{workbook_id}"
    _bundle(task, workbook_id, amd=amd)
    return ReleaseCandidate(
        release_id="a" * 64,
        release_dir=tmp_path / f"release-{workbook_id}",
        task_dir=task,
        task_generation_id="b" * 64,
        source_sha256="c" * 64,
        bundle_sha256=tree_sha256(task),
    )


def _attempt(
    tmp_path: Path,
    number: int,
    final: str,
    *,
    workbook_id: str = "0001",
    status: str = "completed",
) -> dict[str, object]:
    path = tmp_path / f"attempt-{workbook_id}-{number:04d}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "final.txt").write_text(final, encoding="utf-8")
    return {
        "workbook_id": workbook_id,
        "status": status,
        "attempt": path.name,
        "attempt_path": str(path),
        "return_code": 0,
    }


def _stub_ready(
    worker: RecoveryWorker,
    records: dict[str, ReadyRecord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker, "_prepare_ready", lambda row: records[row.workbook_id]
    )


def test_hard_blocker_does_not_complete_or_drop_later_queue_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=2
    )
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    attempts: dict[str, int] = {"0001": 0, "0002": 0}
    seen_worktrees: list[Path] = []

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        attempts[row.workbook_id] += 1
        if worker.active_worktree is not None:
            seen_worktrees.append(worker.active_worktree)
        return _attempt(
            tmp_path,
            attempts[row.workbook_id],
            "cannot proceed\nFULL_RERUN_BLOCKER: HARD\n",
            workbook_id=row.workbook_id,
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    ledger = worker.run()
    statuses = {row["workbook_id"]: row["status"] for row in ledger["records"]}

    # HARD is never a finish. The row is retried immediately, on the spot,
    # until the lifetime cap (4 here: two pickups of two attempts), then it
    # stays active while the lane moves on to the next row, which gets the
    # same treatment. Nothing is skipped and nothing is marked generated.
    assert statuses["0001"] in {"recovery_pending", "task_fix_needed"}
    assert statuses["0001"] != "generated"
    assert statuses["0002"] in {"recovery_pending", "task_fix_needed"}
    assert attempts["0001"] == config.row_attempt_cap
    assert attempts["0002"] == config.row_attempt_cap
    assert seen_worktrees[: config.row_attempt_cap] == [config.work_root / "task-0001"] * config.row_attempt_cap
    assert json.loads(config.queue.read_text())[1]["workbook_id"] == "0002"
    summary = json.loads((config.state / "summary.json").read_text())
    assert summary["tasks"]["0001"]["status"] != "generated"
    assert not (config.output / "batch-001" / "0001-outputs").exists()
    record = _failure_record(config, "0001")
    assert record["class"] == "retry_budget"
    assert record["lifetime_attempts"] == config.row_attempt_cap
    assert record["fixability"] == "exhausted"
    assert any(item["class"] == "agent_hard_blocker" for item in record["history"])


def test_generated_release_with_amd_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)

    monkeypatch.setattr(
        worker,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "published\nRECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 90, PASS_REPORT()
        ),
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)

    assert result["status"] == "generated"
    destination = config.output / row.batch_id / "0001-outputs"
    assert destination.is_dir()
    assert (destination / "a.md").is_file()
    assert "exact blocker" in (destination / "a.md").read_text().casefold()
    sidecar = json.loads(
        (config.output / row.batch_id / "0001-outputs.verification.json").read_text()
    )
    assert sidecar["bundle_sha256"] == candidate.bundle_sha256
    assert sidecar["fairness_verdict"] == "PASS"
    assert sidecar["fairness_checks"]["verdict"] == "PASS"
    assert set(sidecar["fairness_checks"]["checks"]) == set(worker_module.FAIRNESS_CHECKS)


def test_recovery_evidence_is_written_shown_to_verifier_and_bound_in_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    fixture = (Path(__file__).parent / "fixtures" / "stream_toolcall_v1.jsonl").read_text()
    prompts: list[str] = []

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        # Mimic hardened_runner: an attempt directory under state with the
        # agent stream, which is where the command log comes from.
        attempt_dir = config.state / "generation" / "attempts" / "0001" / "attempt-0001"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stream.jsonl").write_text(fixture, encoding="utf-8")
        (attempt_dir / "result.json").write_text(
            json.dumps({"status": "completed", "return_code": 0, "execution_limits": {"limit_reason": None}})
        )
        worktree = worker.active_worktree
        assert worktree is not None
        (worktree / "xl_seg" / "emit.py").write_text("PATCHED = True\n", encoding="utf-8")
        return _attempt(tmp_path, 1, "published\nRECOVERY_USED: YES\n")

    real_prompt = worker._verifier_prompt

    def verifier_prompt(*args: object, **kwargs: object) -> str:
        prompt = real_prompt(*args, **kwargs)
        prompts.append(prompt)
        return prompt

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker, "_verifier_prompt", verifier_prompt)
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        lambda *args, **kwargs: (worker._verifier_prompt(args[0], args[2], args[3]), _attempt(tmp_path, 90, PASS_REPORT()))[1],
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )
    # a.md must name the changed file for the deterministic gate.
    amd = candidate.task_dir / "a.md"
    amd.write_text(amd.read_text(encoding="utf-8") + "\nChanged files: xl_seg/emit.py\n", encoding="utf-8")
    candidate = replace(candidate, bundle_sha256=worker_module.tree_sha256(candidate.task_dir))
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)

    result = worker.process(row)

    assert result["status"] == "generated"
    evidence_path = config.state / "recovery-evidence" / "0001" / f"{candidate.bundle_sha256}.json"
    evidence = json.loads(evidence_path.read_text())
    assert evidence["schema_version"] == "recovery-evidence/v1"
    assert evidence["modified"] is True
    assert "xl_seg/emit.py" in evidence["changed_files"]
    commands = evidence["attempts"][0]["shell_commands"]
    assert [item["command"] for item in commands][:2] == [
        "python xl_source_health.py --workbook 0001",
        "bash build_one.sh 0001 --resume-after-naturalization",
    ]
    assert commands[1]["exit_code"] == 2
    assert evidence["attempts"][0]["file_writes"] == ["a.md", "xl_seg/emit.py"]
    assert evidence["instruction_mode"] == "naturalize"
    # The verifier was told where the evidence is and its hash.
    assert prompts and str(evidence_path) in prompts[-1]
    assert worker_module.sha256_file(evidence_path) in prompts[-1]
    # Delivery copies it next to the bundle and binds the hash in the sidecar.
    sidecar = json.loads(
        (config.output / row.batch_id / "0001-outputs.verification.json").read_text()
    )
    bound = sidecar["recovery_evidence"]
    delivered = config.output / row.batch_id / "0001-outputs.recovery-evidence.json"
    assert Path(bound["path"]) == delivered.resolve()
    assert bound["sha256"] == worker_module.sha256_file(delivered) == worker_module.sha256_file(evidence_path)
    # The bundle itself is unchanged: evidence lives beside it, never inside.
    assert not (config.output / row.batch_id / "0001-outputs" / "recovery-evidence.json").exists()


def test_next_task_sees_baseline_code_not_previous_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, workbook_ids=("0001", "0002"))
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    first_emit: dict[str, str] = {}
    first_worktree: Path | None = None

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal first_worktree
        worktree = worker.active_worktree
        assert worktree is not None
        emit = worktree / "xl_seg" / "emit.py"
        first_emit.setdefault(row.workbook_id, emit.read_text(encoding="utf-8"))
        if row.workbook_id == "0001":
            first_worktree = worktree
            emit.write_text("CHANGED_BY_FIRST_TASK = True\n", encoding="utf-8")
            assert (config.baseline_repo / "xl_seg" / "emit.py").read_text(
                encoding="utf-8"
            ) == BASELINE_EMIT
        return _attempt(
            tmp_path,
            1,
            "FULL_RERUN_BLOCKER: HARD\n",
            workbook_id=row.workbook_id,
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    worker.process(rows[0])
    worker.process(rows[1])

    assert first_emit == {"0001": BASELINE_EMIT, "0002": BASELINE_EMIT}
    assert first_worktree is not None
    assert first_worktree.exists()
    assert (config.baseline_repo / "xl_seg" / "emit.py").read_text(
        encoding="utf-8"
    ) == BASELINE_EMIT
    leftover = sorted(path.name for path in config.work_root.glob("task-*"))
    assert leftover == ["task-0001", "task-0002"]


def test_missing_inventory_raises_orchestration_error_not_hard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    inventory = config.inventory
    assert inventory is not None
    inventory.write_text(
        json.dumps(
            {"inventory_sha256": INVENTORY_DOCUMENT_HASH, "records": []}
        ),
        encoding="utf-8",
    )
    registry = json.loads(config.inventory_registry.read_text(encoding="utf-8"))
    registry["approvals"][0]["inventory_artifact_sha256"] = (
        worker_module.sha256_file(inventory)
    )
    registry["approvals"][0]["batch_source_ledger"] = []
    config.inventory_registry.write_text(json.dumps(registry), encoding="utf-8")
    worker = RecoveryWorker(config)

    with pytest.raises(RecoveryOrchestrationError, match="orchestration error"):
        worker.ensure_inventory(row)

    with pytest.raises(RecoveryOrchestrationError, match="absent from the pinned"):
        worker_module.ensure_inventory(
            row, config, selected_approval=worker.selected_approval
        )

    _stub_ready(worker, records, monkeypatch)
    seen: dict[str, object] = {}

    def run_generation(
        row: QueueRow,
        _record: object,
        inventory: object,
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        seen["inventory"] = inventory
        return _attempt(
            tmp_path,
            1,
            "recovering without pinned inventory\nRECOVERY_USED: YES\n",
            workbook_id=row.workbook_id,
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    result = worker.process(row)
    assert seen["inventory"] is None
    assert result["status"] in RECOVERY_OK_AFTER_NO_RELEASE
    assert result.get("queued") is True


def test_source_hash_drift_is_per_task_and_does_not_abort_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, workbook_ids=("0001", "0002"))
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    processed: list[str] = []

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        processed.append(row.workbook_id)
        if row.workbook_id == "0001":
            row.run_source_path.write_bytes(b"tampered-source-bytes")
        return _attempt(
            tmp_path,
            1,
            "RECOVERY_USED: NO\n",
            workbook_id=row.workbook_id,
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    ledger = worker.run()
    by_id = {row["workbook_id"]: row for row in ledger["records"]}

    # Source drift is a policy violation on this row only. The immediate
    # retry is refused before generation ("source drift before worktree
    # resume": the file on disk is still tampered), which is no progress, so
    # the lane stops retrying 0001 and moves on to 0002, whose own failure
    # ("no release") is retried to the cap.
    assert processed[0] == "0001"
    assert processed.count("0001") == 1
    assert processed.count("0002") == config.row_attempt_cap
    assert by_id["0001"]["status"] == "task_fix_needed"
    assert by_id["0001"]["policy_violation"] is True
    assert "source drift" in str(by_id["0001"]["error"])
    assert by_id["0002"]["status"] == "recovery_pending"
    assert json.loads(config.queue.read_text())[1]["workbook_id"] == "0002"
    record = _failure_record(config, "0001")
    assert record["class"] == "policy_violation"
    assert record["lifetime_attempts"] == 1
    # Two distinct violations (during generation, then before resume): the
    # earlier signature is kept in history rather than counted as a repeat.
    assert record["occurrence_count"] == 1
    assert [item["class"] for item in record["history"]] == ["policy_violation"]
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )
    assert evidence["policy_violation"] is True


def test_xl_seg_emit_edit_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        assert worker.active_worktree is not None
        emit = worker.active_worktree / "xl_seg" / "emit.py"
        emit.write_text("EMIT_PATCHED = True\n", encoding="utf-8")
        return _attempt(tmp_path, 1, "RECOVERY_USED: YES\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    result = worker.process(rows[0])

    assert result["status"] in RECOVERY_OK_AFTER_NO_RELEASE
    assert "policy" not in str(result.get("error", "")).casefold()
    assert (config.baseline_repo / "xl_seg" / "emit.py").read_text(
        encoding="utf-8"
    ) == BASELINE_EMIT


@pytest.mark.parametrize("relative", ["run_grader.py", "answer_key.json"])
def test_grader_or_answer_key_edit_is_policy_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    config, rows, records = _config(tmp_path)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        assert worker.active_worktree is not None
        target = worker.active_worktree / relative
        target.write_text("tampered-protected\n", encoding="utf-8")
        return _attempt(tmp_path, 1, "RECOVERY_USED: YES\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    with pytest.raises(PolicyViolation, match="protected files"):
        worker.process(rows[0])


def test_fairness_fail_is_fairness_retry_not_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)

    monkeypatch.setattr(
        worker,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "recovered\nRECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 90, FAIL_REPORT("Hidden assumption.", {"hidden_assumptions": ("undisclosed_input", 9)})
        ),
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)

    assert result["status"] == "fairness_retry"
    assert result["status"] != "generated"
    assert result["queued"] is True
    assert not (config.output / row.batch_id / "0001-outputs").exists()
    checkpoint = json.loads(
        (
            config.state
            / "checkpoints"
            / f"{row.workbook_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["fairness_result"]["passed"] is False
    assert "FAIRNESS_VERDICT: FAIL" in checkpoint["fairness_result"]["report"]


def test_hard_line_does_not_stop_remaining_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=3)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    prompts: list[int] = []

    def run_generation(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path,
        _prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        prompts.append(attempt_index)
        return _attempt(
            tmp_path,
            attempt_index + 1,
            "FULL_RERUN_BLOCKER: HARD\n",
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    result = worker.process(rows[0])

    assert prompts == [0, 1, 2]
    assert result["status"] == "task_fix_needed"
    assert result["saw_hard_blocker"] is True
    assert result["queued"] is True


def test_create_isolated_worktree_does_not_copy_venv(tmp_path: Path) -> None:
    config, _, _ = _config(tmp_path)
    venv = config.baseline_repo / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site.py").write_text("venv\n", encoding="utf-8")

    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    try:
        assert (worktree / ".venv").is_symlink()
        assert (worktree / ".venv" / "lib" / "site.py").read_text(
            encoding="utf-8"
        ) == "venv\n"
        assert (worktree / "xl_seg" / "emit.py").read_text(encoding="utf-8") == (
            BASELINE_EMIT
        )
    finally:
        worker_module.recycle_isolated_worktree(worktree, config.baseline_repo)
    assert not worktree.exists()


def test_deliver_bundle_requires_amd_when_modified(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=rows[0].run_source_sha256,
    )

    with pytest.raises(worker_module.RecoveryError, match="no final a.md"):
        deliver_bundle(
            config,
            rows[0],
            candidate,
            modified=True,
            verification_report=PASS_REPORT("ok"),
            verification_attempt=None,
        )


def test_variant_generated_output_dirs_are_ignored_in_protected_snapshot() -> None:
    assert worker_module._ignored_snapshot_path(
        Path("tasks_outputs_mcp/0004-outputs/tests/answer_key.json")
    )
    assert worker_module._ignored_snapshot_path(
        Path("tasks_outputs_mcp_assumption/0004-outputs/tests/run_grader.py")
    )
    assert worker_module._ignored_snapshot_path(
        Path("tasks_outputs_mcp_v3r4/0017-outputs/tests/answer_key.json")
    )
    assert not worker_module._ignored_snapshot_path(Path("grader/run_grader.py"))
    assert not worker_module._ignored_snapshot_path(
        Path("xl_source_health.py")
    )


def test_variant_staging_dirs_do_not_trip_protected_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        worktree = worker.active_worktree
        assert worktree is not None
        tests = (
            worktree
            / "tasks_outputs_mcp_v3"
            / f"{row.workbook_id}-outputs"
            / "tests"
        )
        tests.mkdir(parents=True)
        (tests / "answer_key.json").write_text('{"kind":"cell_value"}\n', encoding="utf-8")
        (tests / "run_grader.py").write_text("print(1)\n", encoding="utf-8")
        return _attempt(
            tmp_path, 1, "published\nRECOVERY_USED: YES\n", workbook_id=row.workbook_id
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 90, PASS_REPORT()
        ),
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)
    assert result["status"] == "generated"
    assert result.get("policy_violation") is not True


BASELINE_PUBLISHER = """
def resolve_current_release(*args, **kwargs):
    raise RuntimeError("baseline xl_release_publication used")


def resolve_task_generation_by_id(*args, **kwargs):
    raise RuntimeError("baseline xl_release_publication used")
"""

WORKTREE_PUBLISHER = """
from pathlib import Path

SOURCE_SHA = {sha!r}
GENERATION_ID = {gen!r}
RELEASE_ID = {rid!r}


def resolve_current_release(
    root, *, source_root, segmentation_root, task_root=None, expected_release_id=None
):
    root = Path(root)
    release = {{
        "release_id": RELEASE_ID,
        "workbook_id": root.name,
        "bindings": {{"source_sha256": SOURCE_SHA}},
        "task_generation": {{"generation_id": GENERATION_ID}},
    }}
    return root / "releases" / RELEASE_ID, release


def resolve_task_generation_by_id(root, generation_id):
    path = Path(root) / "task-generations" / generation_id
    return path, {{"generation_id": generation_id}}
"""


def _inspect_worktree(tmp_path: Path) -> tuple[RecoveryWorker, QueueRow, Path]:
    config, rows, _records = _config(tmp_path)
    row = rows[0]
    (config.baseline_repo / "xl_release_publication.py").write_text(
        BASELINE_PUBLISHER, encoding="utf-8"
    )
    worker = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, row.workbook_id
    )
    worker.active_worktree = worktree
    return worker, row, worktree


def _write_pointer(worktree: Path, workbook_id: str, release_id: str) -> Path:
    root = worktree / "release_out" / workbook_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "current-release.json"
    path.write_text(
        json.dumps(
            {"release_id": release_id, "release_path": f"releases/{release_id}"}
        ),
        encoding="utf-8",
    )
    return path


def _write_independent_release(
    worktree: Path, row: QueueRow
) -> tuple[str, str, Path]:
    release_root = worktree / "release_out" / row.workbook_id
    stage = worktree / "task-stage"
    _bundle(stage, row.workbook_id)
    artifacts = {}
    for path in sorted(stage.rglob("*")):
        if path.is_file():
            relative = path.relative_to(stage).as_posix()
            artifacts[relative] = {
                "algorithm": "sha256",
                "path": relative,
                "sha256": worker_module.sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
    task_identity = {
        "schema_version": "task-generation-identity/v2",
        "workbook_id": row.workbook_id,
        "bindings": {},
        "artifacts": artifacts,
    }
    generation_id = worker_module._sha256_bytes(
        worker_module._canonical_bytes(task_identity)
    )
    task = release_root / "task-generations" / generation_id
    task.parent.mkdir(parents=True)
    os.replace(stage, task)
    task_manifest = {
        "schema_version": "task-generation/v2",
        "generation_id": generation_id,
        "identity": task_identity,
        "bindings": {},
        "artifacts": artifacts,
    }
    task_manifest_path = task / "generation-manifest.json"
    task_manifest_path.write_bytes(worker_module._canonical_bytes(task_manifest))
    task_record = {
        "generation_id": generation_id,
        "generation_path": str(task.resolve()),
        "manifest_path": str(task_manifest_path.resolve()),
        "manifest_sha256": worker_module.sha256_file(task_manifest_path),
        "task_generation_sha256": generation_id,
    }
    release_identity = {
        "schema_version": "workbook-release-identity/v2",
        "workbook_id": row.workbook_id,
        "source_generation": {},
        "segmentation_generation": {},
        "task_generation": task_record,
        "bindings": {"source_sha256": row.run_source_sha256},
        "versions": {},
        "prior_release_id": None,
        "legacy_snapshot_hash": None,
    }
    release_id = worker_module._sha256_bytes(
        worker_module._canonical_bytes(release_identity)
    )
    release = release_root / "releases" / release_id
    release.mkdir(parents=True)
    release_manifest = {
        "schema_version": "workbook-release/v2",
        "release_id": release_id,
        "identity": release_identity,
        **{
            key: release_identity[key]
            for key in (
                "workbook_id",
                "source_generation",
                "segmentation_generation",
                "task_generation",
                "bindings",
                "versions",
                "prior_release_id",
                "legacy_snapshot_hash",
            )
        },
    }
    release_manifest_path = release / "release-manifest.json"
    release_manifest_path.write_bytes(
        worker_module._canonical_bytes(release_manifest)
    )
    pointer = {
        "schema_version": "workbook-current-release/v2",
        "release_id": release_id,
        "release_path": f"releases/{release_id}",
        "manifest_sha256": worker_module.sha256_file(release_manifest_path),
    }
    (release_root / "current-release.json").write_bytes(
        worker_module._canonical_bytes(pointer)
    )
    return release_id, generation_id, task


def test_inspect_uses_worktree_modules_not_baseline(tmp_path: Path) -> None:
    worker, row, worktree = _inspect_worktree(tmp_path)
    release_id, gen_id, task = _write_independent_release(worktree, row)
    (worktree / "xl_release_publication.py").write_text(
        WORKTREE_PUBLISHER.format(sha=row.run_source_sha256, gen=gen_id, rid=release_id),
        encoding="utf-8",
    )
    try:
        candidate = worker._inspect_release(row)
        assert candidate.release_id == release_id
        assert candidate.task_dir == task
        assert candidate.task_generation_id == gen_id
        assert candidate.source_sha256 == row.run_source_sha256
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_inspect_reports_worktree_resolve_error(tmp_path: Path) -> None:
    worker, row, worktree = _inspect_worktree(tmp_path)
    try:
        with pytest.raises(
            worker_module.RecoveryError,
            match="baseline xl_release_publication used",
        ):
            worker._inspect_release(row)
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_parse_inspect_output_prefers_json_error() -> None:
    with pytest.raises(
        worker_module.RecoveryError,
        match="current immutable release does not bind queue source",
    ):
        worker_module._parse_inspect_output(
            '{"ok": false, "error": "current immutable release does not bind queue source"}',
            "ignored stderr",
            2,
        )


def test_parse_inspect_output_ignores_leading_noise() -> None:
    data = worker_module._parse_inspect_output(
        'loading modules\n{"ok": true, "release_id": "x"}\n',
        "",
        0,
    )
    assert data["ok"] is True
    assert data["release_id"] == "x"


def test_inspect_requires_current_release_pointer(tmp_path: Path) -> None:
    worker, row, worktree = _inspect_worktree(tmp_path)
    gen_id = "b" * 64
    release_id = "a" * 64
    task = (
        worktree
        / "release_out"
        / row.workbook_id
        / "task-generations"
        / gen_id
    )
    _bundle(task, row.workbook_id)
    (worktree / "xl_release_publication.py").write_text(
        WORKTREE_PUBLISHER.format(sha=row.run_source_sha256, gen=gen_id, rid=release_id),
        encoding="utf-8",
    )
    try:
        with pytest.raises(
            worker_module.RecoveryError, match="current-release.json is absent"
        ):
            worker._inspect_release(row)
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_inspect_rejects_symlink_current_release_pointer(tmp_path: Path) -> None:
    worker, row, worktree = _inspect_worktree(tmp_path)
    gen_id = "b" * 64
    release_id = "a" * 64
    task = (
        worktree
        / "release_out"
        / row.workbook_id
        / "task-generations"
        / gen_id
    )
    _bundle(task, row.workbook_id)
    pointer = _write_pointer(worktree, row.workbook_id, release_id)
    target = pointer.with_name("pointer-target.json")
    target.write_text(pointer.read_text(encoding="utf-8"), encoding="utf-8")
    pointer.unlink()
    pointer.symlink_to(target)
    (worktree / "xl_release_publication.py").write_text(
        WORKTREE_PUBLISHER.format(sha=row.run_source_sha256, gen=gen_id, rid=release_id),
        encoding="utf-8",
    )
    try:
        with pytest.raises(
            worker_module.RecoveryError, match="current-release.json is a symlink"
        ):
            worker._inspect_release(row)
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_inspect_rejects_release_dir_outside_release_out(tmp_path: Path) -> None:
    worker, row, worktree = _inspect_worktree(tmp_path)
    gen_id = "b" * 64
    release_id = "a" * 64
    escaped = tmp_path / "escaped-task"
    _bundle(escaped, row.workbook_id)
    _write_pointer(worktree, row.workbook_id, release_id)
    escaped_release = tmp_path / "escaped-release"
    escaped_release.mkdir()
    (worktree / "xl_release_publication.py").write_text(
        (
            "from pathlib import Path\n"
            f"TASK = {str(escaped)!r}\n"
            f"RELEASE = {str(escaped_release)!r}\n"
            f"SOURCE_SHA = {row.run_source_sha256!r}\n"
            f"GENERATION_ID = {gen_id!r}\n"
            f"RELEASE_ID = {release_id!r}\n"
            "def resolve_current_release(root, **_kwargs):\n"
            "    return Path(RELEASE), {\n"
            "        'release_id': RELEASE_ID,\n"
            "        'workbook_id': Path(root).name,\n"
            "        'bindings': {'source_sha256': SOURCE_SHA},\n"
            "        'task_generation': {'generation_id': GENERATION_ID},\n"
            "    }\n"
            "def resolve_task_generation_by_id(root, generation_id):\n"
            "    return Path(TASK), {'generation_id': generation_id}\n"
        ),
        encoding="utf-8",
    )
    try:
        with pytest.raises(
            worker_module.RecoveryError, match="escapes release_out"
        ):
            worker._inspect_release(row)
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_assert_inspect_candidate_requires_pointer_bind(tmp_path: Path) -> None:
    release_root = tmp_path / "release_out" / "0001"
    release_root.mkdir(parents=True)
    (release_root / "current-release.json").write_text(
        json.dumps({"release_id": "a" * 64}),
        encoding="utf-8",
    )
    row = QueueRow(
        batch_id="batch-001",
        workbook_id="0001",
        run_source_path=tmp_path / "0001.xlsx",
        run_source_sha256="c" * 64,
        source_format="xlsx",
        source_sha256="c" * 64,
        source_size=1,
    )
    with pytest.raises(
        worker_module.RecoveryError, match="does not bind inspect release_id"
    ):
        _assert_inspect_candidate(
            row,
            tmp_path,
            {
                "release_id": "b" * 64,
                "release_dir": str(release_root / "releases" / ("b" * 64)),
                "task_dir": str(release_root / "task-generations" / ("b" * 64)),
            },
        )


def test_run_records_inspect_error_and_does_not_skip_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=2
    )
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        worktree = worker.active_worktree
        assert worktree is not None
        release = worktree / "release_out" / row.workbook_id
        release.mkdir(parents=True, exist_ok=True)
        (release / "current-release.json").write_text("{}\n", encoding="utf-8")
        return _attempt(
            tmp_path, 1, "published\nRECOVERY_USED: YES\n", workbook_id=row.workbook_id
        )

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(
            worker_module.RecoveryError("current immutable release does not bind")
        ),
    )

    ledger = worker.run()
    by_id = {row["workbook_id"]: row for row in ledger["records"]}
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )

    # Neither row delivers; both are retried on the spot to the cap and both
    # stay active. The task evidence keeps the inspect error; the failure
    # record classifies it and records the cap that finally parked the row.
    assert by_id["0002"]["status"] == "recovery_pending"
    assert by_id["0001"]["status"] != "generated"
    assert "does not bind" in str(evidence["inspect_error"])
    record = _failure_record(config, "0001")
    assert record["class"] == "retry_budget"
    inspect_history = [item for item in record["history"] if item["class"] == "inspect"]
    assert inspect_history and inspect_history[0]["subclass"] == "release_inspect"
    current = json.loads(
        (config.state / "failed-releases" / "0001" / "current.json").read_text()
    )
    assert (Path(current["snapshot_path"]) / "current-release.json").is_file()
    leftover = sorted((config.work_root).glob("task-*"))
    assert leftover == [config.work_root / "task-0001", config.work_root / "task-0002"]


def test_inspect_fail_preserves_release_after_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        worktree = worker.active_worktree
        assert worktree is not None
        release = worktree / "release_out" / rows[0].workbook_id
        release.mkdir(parents=True, exist_ok=True)
        (release / "note.txt").write_text("keep-me\n", encoding="utf-8")
        return _attempt(tmp_path, 1, "RECOVERY_USED: YES\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    result = worker.process(rows[0])
    assert result["status"] in RECOVERY_OK_AFTER_NO_RELEASE
    assert result["inspect_error"]
    current = json.loads(
        (config.state / "failed-releases" / "0001" / "current.json").read_text()
    )
    assert (Path(current["snapshot_path"]) / "note.txt").read_text(
        encoding="utf-8"
    ) == "keep-me\n"
    assert list(config.work_root.glob("task-*")) == [
        config.work_root / "task-0001"
    ]


def test_inspect_unexpected_error_does_not_burn_remaining_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=4)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    prompts: list[int] = []

    def run_generation(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path,
        _prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        prompts.append(attempt_index)
        return _attempt(tmp_path, attempt_index + 1, "RECOVERY_USED: NO\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(TypeError("inspect bug")),
    )

    result = worker.process(rows[0])
    assert prompts == [0]
    assert result["status"] in RECOVERY_OK_AFTER_NO_RELEASE
    assert "TypeError" in str(result["inspect_error"])


def test_run_unexpected_exception_writes_evidence_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, workbook_ids=("0001", "0002"))
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def boom(_row: QueueRow) -> dict[str, object]:
        raise RuntimeError("unexpected worker bug")

    monkeypatch.setattr(worker, "process", boom)
    ledger = worker.run()
    by_id = {row["workbook_id"]: row for row in ledger["records"]}
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )
    # An unexpected exception is the worker's own defect (class controller).
    # The row parks for the lane fix step instead of burning attempts, the
    # lane still tries the next row (which hits the same bug and parks too),
    # and the run signals exit 3 so the supervisor runs the fix step.
    assert by_id["0001"]["status"] == "worker_fix_needed"
    assert by_id["0002"]["status"] == "worker_fix_needed"
    assert by_id["0001"]["queued"] is True
    assert by_id["0001"]["unexpected"] is True
    assert by_id["0001"]["mechanical_controller_park"] is True
    assert "unexpected worker bug" in evidence["error"]
    assert evidence["unexpected"] is True
    assert _failure_record(config, "0001")["class"] == "controller"
    assert worker_module.exit_code_for(ledger, worker.run_state) == worker_module.EXIT_CONTROLLER_FIX
    # A worker restart on the same build leaves both parked; a new build
    # (overlay) releases them. Nothing is ever skipped or marked generated.
    second = RecoveryWorker(config)
    monkeypatch.setattr(second, "process", lambda row: {"workbook_id": row.workbook_id, "status": "generated"})
    assert [r["status"] for r in second.run()["records"]] == ["worker_fix_needed"] * 2
    third = RecoveryWorker(config)
    third.worker_overlay_id = "overlay-build"
    monkeypatch.setattr(third, "process", lambda row: {"workbook_id": row.workbook_id, "status": "generated"})
    assert [r["status"] for r in third.run()["records"]] == ["generated"] * 2
    # Delivered rows resolve their failure records.
    assert _failure_record(config, "0001")["status"] == "generated"
    assert _failure_record(config, "0001")["resolved_at"]


def test_agent_environment_uses_worktree_pythonpath(tmp_path: Path) -> None:
    worker, _row, worktree = _inspect_worktree(tmp_path)
    try:
        env = worker._agent_environment()
        assert env["PYTHONPATH"] == str(worktree)
        assert env["PYTHONNOUSERSITE"] == "1"
        assert "PYTHONHOME" not in env
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, worker.config.baseline_repo)


def test_fairness_report_file_used_when_final_txt_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    report = (
        config.state / "verification-inputs" / row.workbook_id / "fairness-report.md"
    )

    calls: list[str] = []

    def run_verifier(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls.append("verifier")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            PASS_REPORT(), encoding="utf-8"
        )
        return _attempt(tmp_path, 90, "wrote report only\n")

    monkeypatch.setattr(
        worker,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "recovered\nRECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker, "_run_verifier", run_verifier)
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)
    # The verifier wrote its verdict somewhere the controller does not read,
    # so the controller saw no structured block. That is a verifier error,
    # not a task verdict: one synchronous rerun, then the row stays active
    # with class verifier_error. No generation agent is sent after a phantom
    # defect and nothing is delivered.
    assert calls == ["verifier", "verifier"]
    assert result["status"] == "recovery_pending"
    assert result["verifier_error"] == "missing_block"
    checkpoint = json.loads((config.state / "checkpoints" / "0001.json").read_text())
    assert [item["reason"] for item in checkpoint["verifier_errors"]] == ["missing_block", "missing_block"]
    assert worker_module.classify_failure(result)["class"] == "verifier_error"
    assert not (config.output / "batch-001" / "0001-outputs").exists()


def test_fairness_block_and_line_must_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    reports = iter(
        [
            # Line says PASS, block says FAIL: unusable, rerun once.
            f"review\n{_fairness_block('FAIL', {'solvability': ('units', 6)})}\nFAIRNESS_VERDICT: PASS\n",
            # Rerun is a clean structured FAIL: a real task verdict.
            FAIL_REPORT("review", {"answer_leakage": ("key_in_instruction", 12)}),
        ]
    )
    monkeypatch.setattr(
        worker, "_run_verifier", lambda *_a, **_k: _attempt(tmp_path, 90, next(reports))
    )
    monkeypatch.setattr(
        worker,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(tmp_path, 1, "recovered\nRECOVERY_USED: YES\n"),
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)

    assert result["status"] == "fairness_retry"
    assert result["fairness_verdict"]["checks"]["answer_leakage"] == "fail"
    classified = worker_module.classify_failure(result)
    assert (classified["class"], classified["subclass"], classified["gate"]) == (
        "fairness_leak",
        "key_in_instruction",
        12,
    )
    checkpoint = json.loads((config.state / "checkpoints" / "0001.json").read_text())
    assert [item["reason"] for item in checkpoint["verifier_errors"]] == ["line_block_disagreement"]
    assert checkpoint["fairness_result"]["verdict"]["verdict"] == "FAIL"


def test_incident_verdict_rules() -> None:
    verdict = worker_module.incident_verdict
    # Bare last line with no block: never parks the row.
    block, fix, error = verdict("bad\nINCIDENT_VERDICT: WORKER_FIX_NEEDED\n")
    assert (block, fix, error) == (None, False, "missing_block")
    # Controller route + agreeing line: parks.
    block, fix, error = verdict("x\n" + CONTROLLER_VERDICT("lease_bug", "memory_governor.py") + "INCIDENT_VERDICT: WORKER_FIX_NEEDED\n")
    assert fix is True and error is None
    assert (block["route"], block["subclass"], block["component"]) == ("controller", "lease_bug", "memory_governor.py")
    # Task route + WORKER_FIX_NEEDED line: disagreement, task-side.
    block, fix, error = verdict("x\n" + TASK_VERDICT() + "INCIDENT_VERDICT: WORKER_FIX_NEEDED\n")
    assert fix is False and error == "line_block_disagreement"
    # Block itself inconsistent (controller route, task verdict): malformed.
    bad = _incident_block("TASK_LOCAL_OR_EXTERNAL", "controller", "x")
    assert worker_module.parse_incident_verdict(bad) is None
    # Unknown route: malformed.
    assert worker_module.parse_incident_verdict(_incident_block("TASK_LOCAL_OR_EXTERNAL", "magic", "x")) is None
    # Task route agreeing: usable, not parked, carries gate.
    block, fix, error = verdict("x\n" + TASK_VERDICT("paperwork", "amd_commands", 16) + "INCIDENT_VERDICT: TASK_LOCAL_OR_EXTERNAL\n")
    assert (fix, error, block["route"], block["gate"]) == (False, None, "paperwork", 16)


def test_incident_fingerprint_is_stable_across_attempt_specific_values(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    calls: list[str] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        calls.append("run")
        Path(str(kwargs["report_path"])).write_text(
            "Same defect.\n" + TASK_VERDICT() + "INCIDENT_VERDICT: TASK_LOCAL_OR_EXTERNAL\n",
            encoding="utf-8",
        )
        return {"status": "completed", "return_code": 0}

    recovery = RecoveryWorker(config, investigator_launcher=launcher)
    first = recovery._launch_incident(
        rows[0],
        "delivery",
        {"error": "RecoveryError: artifacts do not match manifest at /w/a/task-0001/releases/" + "a" * 64, "bundle_sha256": "1" * 64},
    )
    second = recovery._launch_incident(
        rows[0],
        "delivery",
        {"error": "RecoveryError: artifacts do not match manifest at /w/b/task-0001/releases/" + "b" * 64, "bundle_sha256": "2" * 64},
    )
    assert first["fingerprint"] == second["fingerprint"]
    assert second["deduplicated"] is True
    assert second["verdict"]["route"] == "worktree_fix"
    assert calls == ["run"]
    other = recovery._launch_incident(rows[0], "delivery", {"error": "RecoveryError: something else"})
    assert other["fingerprint"] != first["fingerprint"]


def test_reentry_text_points_the_retry_at_the_failing_gate(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    row = rows[0]
    assert worker._reentry_text(row) == ""
    # A fairness FAIL with a structured gate-6 finding.
    worker_module.write_failure_record(
        config.state,
        row,
        {
            "status": "fairness_retry",
            "fairness_verdict": {
                "checks": {"solvability": "fail"},
                "findings": [{"check": "solvability", "subclass": "units_percent_vs_ratio", "gate": 6}],
            },
        },
    )
    text = worker._reentry_text(row)
    assert "gate 6 (normalize and account for every row)" in text
    assert "subclass units_percent_vs_ratio" in text
    assert "Re-enter at gate 6" in text
    assert "Then rerun every later gate in order (public source profiles" in text
    assert "Do not rebuild artifacts from gates before the re-entry point" in text
    # Paperwork: gate 16, and the recipe names the recovery evidence.
    worker_module.write_failure_record(
        config.state,
        row,
        {
            "status": "recovery_pending",
            "delivery_error": "recovered task a.md is not a complete recovery record; changed files not named in a.md: x",
            "error": "recovered task a.md is not a complete recovery record; changed files not named in a.md: x",
        },
    )
    text = worker._reentry_text(row)
    assert "gate 16 (promotion and recovery record)" in text
    assert "recovery-evidence" in text
    assert "Then rerun every later gate" not in text
    # Class without a gate falls back to the class default; retry_budget says nothing.
    worker_module.write_failure_record(
        config.state, row, {"status": "recovery_pending", "last_attempt": {"status": "failed"}}
    )
    assert "gate 1 (preflight and AST)" in worker._reentry_text(row)
    worker_module.write_failure_record(
        config.state, row, {"status": "recovery_pending", "retry_budget_exhausted": True}
    )
    assert worker._reentry_text(row) == ""


def test_instruction_mode_is_pinned_on_the_checkpoint_and_prose_metadata_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = _candidate(tmp_path)
    # A valid task.toml with no prose table (the fixture default is a placeholder).
    (candidate.task_dir / "task.toml").write_text('[metadata]\nname = "x"\n', encoding="utf-8")
    candidate = replace(
        candidate,
        source_sha256=row.run_source_sha256,
        bundle_sha256=worker_module.tree_sha256(candidate.task_dir),
    )
    prompts: list[str] = []

    def run_generation(*args: object, **_kwargs: object) -> dict[str, object]:
        prompts.append(worker._generation_prompt(row, records["0001"], None, None, 0))
        return _attempt(tmp_path, 1, "published\nRECOVERY_USED: NO\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")

    # Lane launched in prose mode: the row records gdpval_prose on its first
    # attempt, and a bundle without the prose metadata table is a gate-13
    # instruction_metadata failure, decided by the controller before any
    # verifier is paid for.
    monkeypatch.setenv("HARBOR_GDPVAL_PROSE", "1")
    result = worker.process(row)
    checkpoint = json.loads((config.state / "checkpoints" / "0001.json").read_text())
    assert checkpoint["instruction_mode"] == "gdpval_prose"
    assert result["status"] == "recovery_pending"
    assert "prose_metadata_missing" in str(result["error"])
    classified = worker_module.classify_failure(result)
    assert (classified["class"], classified["gate"]) == ("instruction_metadata", 13)
    worker._record_row_close(row, result)  # what run() does after process()
    assert _failure_record(config, "0001")["instruction_mode"] == "gdpval_prose"
    assert "Gate 13 mode: GDPval prose" in prompts[-1]

    # The lane is relaunched without the flag: the recorded mode still wins,
    # for the prompt and for the metadata check.
    monkeypatch.delenv("HARBOR_GDPVAL_PROSE")
    assert worker._instruction_mode(row) == "gdpval_prose"
    result = worker.process(row)
    assert "prose_metadata_missing" in str(result["error"])
    assert "recorded on its first" in prompts[-1] and "lane environment says naturalize" in prompts[-1]


def test_naturalize_row_rejects_a_prose_metadata_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = _candidate(tmp_path)
    (candidate.task_dir / "task.toml").write_text(
        '[metadata.prose_instruction]\nmodel = "x"\n', encoding="utf-8"
    )
    candidate = replace(
        candidate,
        source_sha256=row.run_source_sha256,
        bundle_sha256=worker_module.tree_sha256(candidate.task_dir),
    )
    monkeypatch.setattr(
        worker, "_run_generation", lambda *a, **k: _attempt(tmp_path, 1, "published\nRECOVERY_USED: NO\n")
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.delenv("HARBOR_GDPVAL_PROSE", raising=False)

    result = worker.process(row)

    assert result["status"] == "recovery_pending"
    assert "wrong mode" in str(result["error"])
    assert worker_module.classify_failure(result)["class"] == "instruction_metadata"


def test_controller_class_after_a_real_attempt_parks_without_more_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Generation runs, then the worker's own inspect code raises a
    # non-RecoveryError. The mechanical class is controller: run() must park
    # the row after that single attempt (not retry generation against a
    # worker bug), move on to the next row, and signal exit 3.
    config, rows, records = _config(tmp_path, workbook_ids=("0001", "0002"), attempt_count=2, row_attempt_cap=6)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    generations: list[str] = []

    def run_generation(row: QueueRow, *_a: object, **_k: object) -> dict[str, object]:
        generations.append(row.workbook_id)
        return _attempt(tmp_path, len(generations), "published\nRECOVERY_USED: NO\n", workbook_id=row.workbook_id)

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(TypeError("'NoneType' object is not subscriptable")),
    )

    ledger = worker.run()

    assert [r["status"] for r in ledger["records"]] == ["worker_fix_needed", "worker_fix_needed"]
    assert generations == ["0001", "0002"]
    assert ledger["records"][0]["mechanical_controller_park"] is True
    assert _failure_record(config, "0001")["class"] == "controller"
    checkpoint = json.loads((config.state / "checkpoints" / "0001.json").read_text())
    assert checkpoint["status"] == "worker_fix_needed"
    assert checkpoint["worker_fix_overlay"] == worker.worker_overlay_id
    assert worker_module.exit_code_for(ledger, worker.run_state) == worker_module.EXIT_CONTROLLER_FIX


def test_verifier_attempt_that_did_not_complete_is_a_verifier_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    statuses = iter(["timed_out", "failed"])
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        # A PASS block was streamed, but the attempt itself did not complete.
        lambda *_a, **_k: _attempt(tmp_path, 90, PASS_REPORT(), status=next(statuses)),
    )
    monkeypatch.setattr(
        worker, "_run_generation", lambda *a, **k: _attempt(tmp_path, 1, "recovered\nRECOVERY_USED: YES\n")
    )
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")

    result = worker.process(row)

    assert result["status"] == "recovery_pending"
    assert result["verifier_error"] == "attempt_failed"
    checkpoint = json.loads((config.state / "checkpoints" / "0001.json").read_text())
    assert [item["reason"] for item in checkpoint["verifier_errors"]] == ["attempt_timed_out", "attempt_failed"]
    assert not (config.output / "batch-001" / "0001-outputs").exists()


def test_run_retries_immediately_and_delivers_after_an_inspect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, workbook_ids=("0001", "0002"), attempt_count=1)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    generations: list[str] = []
    inspections: dict[str, int] = {"0001": 0, "0002": 0}
    candidates = {
        row.workbook_id: replace(
            _candidate(tmp_path, row.workbook_id), source_sha256=row.run_source_sha256
        )
        for row in rows
    }

    def run_generation(row: QueueRow, *_a: object, **_k: object) -> dict[str, object]:
        generations.append(row.workbook_id)
        return _attempt(tmp_path, len(generations), "published\nRECOVERY_USED: NO\n", workbook_id=row.workbook_id)

    def inspect(row: QueueRow) -> ReleaseCandidate:
        inspections[row.workbook_id] += 1
        # 0001's first generation produces a release that does not bind;
        # its second one is fine.
        if row.workbook_id == "0001" and generations.count("0001") < 2:
            raise worker_module.RecoveryError("current immutable release does not bind")
        return candidates[row.workbook_id]

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", inspect)
    monkeypatch.setattr(
        worker, "_run_verifier", lambda *_a, **_k: _attempt(tmp_path, 90, PASS_REPORT())
    )
    monkeypatch.setattr(worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt")

    ledger = worker.run()

    # 0001 failed inspect once, was retried on the spot (no lane restart, no
    # other row in between), delivered on the second attempt; then 0002.
    assert [r["status"] for r in ledger["records"]] == ["generated", "generated"]
    assert generations == ["0001", "0001", "0002"]
    assert worker_module.exit_code_for(ledger, worker.run_state) == worker_module.EXIT_ALL_GENERATED
    assert (config.output / "batch-001" / "0001-outputs").is_dir()
    assert (config.output / "batch-001" / "0002-outputs").is_dir()
    record = _failure_record(config, "0001")
    assert record["status"] == "generated" and record["class"] == "inspect"
    assert "resolved_at" in record


def test_main_maps_exit_codes_and_handles_worker_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class FakeWorker:
        def __init__(self, config: object) -> None:
            self.run_state: dict[str, object] = {}

        def run(self) -> dict[str, object]:
            self.run_state["controller_fingerprints"] = ["sig"]
            return {"counts": {"recovery_pending": 1, "worker_fix_needed": 1}, "records": []}

    monkeypatch.setattr(worker_module.Config, "from_env", classmethod(lambda cls, env=None: object()))
    monkeypatch.setattr(worker_module, "RecoveryWorker", FakeWorker)
    assert worker_module.main([]) == worker_module.EXIT_CONTROLLER_FIX
    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1])["counts"]["worker_fix_needed"] == 1

    def broken(cls, env=None):  # noqa: ANN001
        raise worker_module.RecoveryError("ROW_ATTEMPT_CAP must be at least ATTEMPT_COUNT")

    monkeypatch.setattr(worker_module.Config, "from_env", classmethod(broken))
    assert worker_module.main([]) == worker_module.EXIT_HOST_FLOOR
    assert "ROW_ATTEMPT_CAP" in json.loads(capsys.readouterr().out.strip())["worker_error"]

    class RunErrors(FakeWorker):
        def run(self) -> dict[str, object]:
            raise worker_module.RecoveryError("another recovery worker owns this state directory")

    monkeypatch.setattr(worker_module.Config, "from_env", classmethod(lambda cls, env=None: object()))
    monkeypatch.setattr(worker_module, "RecoveryWorker", RunErrors)
    assert worker_module.main([]) == worker_module.EXIT_HOST_FLOOR


def test_parse_fairness_block_rules() -> None:
    parse = worker_module.parse_fairness_block
    accepted = worker_module.fairness_accepted
    assert parse("no block here\nFAIRNESS_VERDICT: PASS") is None
    assert accepted("no block here\nFAIRNESS_VERDICT: PASS") == (False, None, "missing_block")
    good = PASS_REPORT()
    ok, block, error = accepted(good)
    assert ok and error is None and block["verdict"] == "PASS"
    assert set(block["checks"]) == set(worker_module.FAIRNESS_CHECKS)
    # Missing check name -> malformed.
    broken = good.replace('"technical_evidence": "pass",', "")
    assert parse(broken) is None
    # Unknown check value -> malformed.
    assert parse(good.replace('"solvability": "pass"', '"solvability": "maybe"')) is None
    # FAIL with no failing check is not a usable verdict.
    fail_no_check = f"x\n{_fairness_block('FAIL')}\nFAIRNESS_VERDICT: FAIL\n"
    assert accepted(fail_no_check)[2] == "fail_without_failing_check"
    # PASS with a failing check is not a usable verdict.
    pass_with_fail = f"x\n{_fairness_block('PASS', {'solvability': ('u', 6)})}\nFAIRNESS_VERDICT: PASS\n"
    assert accepted(pass_with_fail)[2] == "pass_with_failing_check"
    # Block present, verdict line missing -> verifier error, not a FAIL.
    assert accepted(f"x\n{_fairness_block('PASS')}\n")[2] == "missing_verdict_line"
    # The last well-formed block wins; findings are normalised.
    two = f"{_fairness_block('FAIL', {'solvability': ('Units: Percent vs Ratio', 6)})}\n{_fairness_block('PASS')}\nFAIRNESS_VERDICT: PASS\n"
    assert accepted(two)[0] is True
    one = parse(f"{_fairness_block('FAIL', {'solvability': ('Units: Percent vs Ratio', 99)})}")
    assert one["findings"][0]["subclass"] == "units_percent_vs_ratio"
    assert one["findings"][0]["gate"] is None


def test_verdict_is_judged_on_the_last_message_that_carries_it() -> None:
    # A trailing acknowledgement message must not turn a clean PASS into a
    # failure, but qualifications inside the verdict's own message still do,
    # and a FAIL anywhere still wins in the merge.
    verdict_then_ack = [
        "Reviewed the bundle.\nFAIRNESS_VERDICT: PASS",
        "No follow-up is needed.",
    ]
    text = worker_module.verdict_transcript(
        verdict_then_ack, "FAIRNESS_VERDICT:", "fallback"
    )
    assert text == "Reviewed the bundle.\nFAIRNESS_VERDICT: PASS"
    assert worker_module.fairness_passed(text)

    qualified = ["Reviewed.\nFAIRNESS_VERDICT: PASS\nHowever the a.md is thin."]
    assert not worker_module.fairness_passed(
        worker_module.verdict_transcript(qualified, "FAIRNESS_VERDICT:", "")
    )

    failed_then_ack = ["FAIRNESS_VERDICT: FAIL\nMissing a.md paths.", "Done."]
    merged = _combine_fairness_reports(
        worker_module.verdict_transcript(failed_then_ack, "FAIRNESS_VERDICT:", ""),
        "FAIRNESS_VERDICT: PASS\n",
    )
    assert not worker_module.fairness_passed(merged)

    # No message carries the token: the flat transcript is used unchanged.
    assert worker_module.verdict_transcript(["hello"], "FAIRNESS_VERDICT:", "flat") == "flat"


def test_fairness_stdout_fail_wins_over_file_pass() -> None:
    merged = _combine_fairness_reports(
        "notes\nFAIRNESS_VERDICT: FAIL\n",
        "Independent review.\nFAIRNESS_VERDICT: PASS\n",
    )
    assert merged.endswith("FAIRNESS_VERDICT: FAIL\n")
    assert not worker_module.fairness_passed(merged)


def test_fairness_verifier_is_read_only_and_returns_report_in_final(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    prompt = worker._verifier_prompt(
        rows[0],
        _candidate(tmp_path),
        tmp_path / "vm-code-diff.txt",
    )

    assert "Do not modify, create, delete, rename, or write any file" in prompt
    assert "Return the complete verification report in your" in prompt
    assert "the recovery controller will persist it" in prompt
    assert "restricted_pass" in prompt


def test_fairness_verifier_uses_controller_technical_evidence(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    evidence = config.state / "verification-inputs" / "technical.json"
    prompt = worker._verifier_prompt(
        rows[0],
        _candidate(tmp_path),
        tmp_path / "vm-code-diff.txt",
        evidence,
        "a" * 64,
    )

    assert str(evidence) in prompt
    assert "authoritative result of deterministic" in prompt
    assert re.search(r"Do not rerun those technical\s+checks", prompt)
    assert "every source/segmentation/task/current-release binding" not in prompt
    assert "semantic check succeeds without uncertainty" in prompt
    assert '"schema_version": "recovery-fairness/v1"' in prompt
    for check in worker_module.FAIRNESS_CHECKS:
        assert f'"{check}"' in prompt


def test_technical_evidence_is_hash_bound_to_candidate(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    row = rows[0]
    candidate = replace(
        _candidate(tmp_path),
        source_sha256=row.run_source_sha256,
    )
    vm_diff = config.state / "vm-code-diff.txt"
    vm_diff.parent.mkdir(parents=True)
    vm_diff.write_text("no shared changes\n", encoding="utf-8")

    path, digest = worker._write_technical_evidence(
        row,
        candidate,
        vm_diff,
        {
            "agent_declaration": "YES",
            "has_amd": True,
            "baseline_diff": {},
            "changed_during_run": [],
        },
        {},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "recovery-technical-evidence/v1"
    assert payload["candidate"]["bundle_sha256"] == candidate.bundle_sha256
    assert payload["candidate"]["source_sha256"] == row.run_source_sha256
    assert payload["inspect_attestation"]["passed"] is True
    assert payload["evidence_sha256"] == digest
    assert worker_module._technical_evidence_digest(payload) == digest


def test_fairness_stdout_is_persisted_by_controller(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    final = PASS_REPORT()
    result = _attempt(tmp_path, 90, final)
    report = (
        config.state
        / "verification-inputs"
        / rows[0].workbook_id
        / "candidate"
        / "fairness.md"
    )
    report.parent.mkdir(parents=True)

    assert worker._read_fairness_report(result, report) == final
    assert report.read_text(encoding="utf-8") == final


def test_fairness_retry_preserves_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        worktree = worker.active_worktree
        assert worktree is not None
        release = worktree / "release_out" / row.workbook_id
        release.mkdir(parents=True, exist_ok=True)
        (release / "kept.txt").write_text("fairness-evidence\n", encoding="utf-8")
        return _attempt(tmp_path, 1, "recovered\nRECOVERY_USED: YES\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker,
        "_run_verifier",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 90, FAIL_REPORT("Hidden assumption.", {"hidden_assumptions": ("undisclosed_input", 9)})
        ),
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "fairness_retry"
    assert evidence["verification_attempt"]
    current = json.loads(
        (config.state / "failed-releases" / "0001" / "current.json").read_text()
    )
    assert (Path(current["snapshot_path"]) / "kept.txt").read_text(
        encoding="utf-8"
    ) == "fairness-evidence\n"


def test_copytree_skips_variant_generated_roots(tmp_path: Path) -> None:
    config, _, _ = _config(tmp_path)
    staging = config.baseline_repo / "tasks_outputs_mcp_assumption" / "x"
    staging.mkdir(parents=True)
    (staging / "answer_key.json").write_text("{}\n", encoding="utf-8")
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    try:
        assert not (worktree / "tasks_outputs_mcp_assumption").exists()
    finally:
        worker_module.recycle_isolated_worktree(worktree, config.baseline_repo)


def test_vm_diff_for_unversioned_copy_contains_verified_content(
    tmp_path: Path,
) -> None:
    config, _, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    try:
        changed = worktree / "xl_seg" / "emit.py"
        changed.write_text("CHANGED_EMIT = True\n", encoding="utf-8")
        state = worker_module._code_state(worktree, worker.baseline, "0001")
        evidence = {
            "baseline_diff": worker_module._baseline_diff(state, worker.baseline),
            "changed_during_run": ["xl_seg/emit.py"],
        }

        path = worker_module._write_vm_diff(
            worktree,
            config.baseline_repo,
            config.state,
            "0001",
            evidence,
        )
        text = path.read_text(encoding="utf-8")

        assert "--- a/xl_seg/emit.py" in text
        assert "+++ b/xl_seg/emit.py" in text
        assert "-BASELINE_EMIT = True" in text
        assert "+CHANGED_EMIT = True" in text
        assert "Not a git repository" not in text
    finally:
        worker_module.recycle_isolated_worktree(worktree, config.baseline_repo)


def test_pytest_cache_is_invisible_to_code_state_and_snapshots(tmp_path: Path) -> None:
    # Fleet step 10 runs pytest in the deployed baseline; `.pytest_cache/`
    # then appeared in every recovered row's code diff as an unexplained
    # change and failed fairness on paperwork.
    config, _, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    try:
        cache = worktree / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True)
        (worktree / ".pytest_cache" / "README.md").write_text("# cache\n")
        (cache / "nodeids").write_text("[]\n")
        pyc = worktree / "xl_seg" / "__pycache__"
        pyc.mkdir()
        (pyc / "emit.cpython-313.pyc").write_bytes(b"\x00")

        state = worker_module._code_state(worktree, worker.baseline, "0001")
        diff = worker_module._baseline_diff(state, worker.baseline)
        snapshot = worker_module.repo_snapshot(worktree, excluded=())

        assert not [p for p in diff if ".pytest_cache" in p or "__pycache__" in p]
        assert not [p for p in state if ".pytest_cache" in p]
        assert not [p for p in snapshot if ".pytest_cache" in p or "__pycache__" in p]
        worker.active_worktree = worktree
        env = worker._agent_environment()
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "-p no:cacheprovider" in env["PYTEST_ADDOPTS"]
    finally:
        worker.active_worktree = None
        worker_module.recycle_isolated_worktree(worktree, config.baseline_repo)


def test_vm_diff_rejects_symlinked_changed_file(tmp_path: Path) -> None:
    config, _, _ = _config(tmp_path)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    try:
        changed = worktree / "xl_seg" / "emit.py"
        changed.unlink()
        changed.symlink_to(config.baseline_repo / "xl_seg" / "emit.py")
        evidence = {
            "baseline_diff": {
                "xl_seg/emit.py": {
                    "baseline": worker_module.sha256_file(
                        config.baseline_repo / "xl_seg" / "emit.py"
                    ),
                    "current": "symlink",
                }
            }
        }

        with pytest.raises(PolicyViolation, match="contains a symlink"):
            worker_module._write_vm_diff(
                worktree,
                config.baseline_repo,
                config.state,
                "0001",
                evidence,
            )
    finally:
        worker_module.recycle_isolated_worktree(worktree, config.baseline_repo)


def test_code_state_includes_variant_staging_instruction(tmp_path: Path) -> None:
    config, _, _ = _config(tmp_path)
    dest = config.baseline_repo / "tasks_outputs_mcp_v3" / "0001-outputs"
    dest.mkdir(parents=True)
    (dest / "instruction.md").write_text("variant\n", encoding="utf-8")
    baseline = worker_module.read_code_baseline(
        config.code_baseline, config.baseline_repo
    )
    state = worker_module._code_state(config.baseline_repo, baseline, "0001")
    assert "tasks_outputs_mcp_v3/0001-outputs/instruction.md" in state


def test_preserve_failed_release_helper(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    worktree = tmp_path / "task-0001"
    release = worktree / "release_out" / "0001"
    release.mkdir(parents=True)
    (release / "current-release.json").write_text("{}\n", encoding="utf-8")
    preserved = _preserve_failed_release(config.state, rows[0], worktree)
    assert preserved is not None
    assert (preserved / "current-release.json").is_file()


def test_restart_reuses_worktree_and_cumulative_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=2)
    row = rows[0]
    first = RecoveryWorker(config)
    _stub_ready(first, records, monkeypatch)

    def first_generation(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path,
        _prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        assert first.active_worktree is not None
        (first.active_worktree / "xl_seg" / "emit.py").write_text(
            "RETAINED_PATCH = True\n", encoding="utf-8"
        )
        artifact = first.active_worktree / "seg_out" / row.workbook_id
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "partial.json").write_text("{}\n", encoding="utf-8")
        return _attempt(tmp_path, attempt_index + 1, "FULL_RERUN_BLOCKER: HARD\n")

    monkeypatch.setattr(first, "_run_generation", first_generation)
    monkeypatch.setattr(
        first,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("same failure")),
    )
    first.process(row)

    retained = config.work_root / "task-0001"
    assert retained.is_dir()
    checkpoint = json.loads(
        (config.state / "checkpoints" / "0001.json").read_text(encoding="utf-8")
    )
    assert checkpoint["cumulative_attempt_count"] == 2

    second = RecoveryWorker(config)
    _stub_ready(second, records, monkeypatch)
    seen: list[tuple[int, object]] = []

    def second_generation(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path,
        prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        assert second.active_worktree == retained
        assert "RETAINED_PATCH" in (
            retained / "xl_seg" / "emit.py"
        ).read_text(encoding="utf-8")
        assert (retained / "seg_out" / "0001" / "partial.json").is_file()
        seen.append((attempt_index, prior))
        return _attempt(tmp_path, attempt_index + 1, "FULL_RERUN_BLOCKER: HARD\n")

    monkeypatch.setattr(second, "_run_generation", second_generation)
    monkeypatch.setattr(
        second,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("same failure")),
    )
    second.process(row)

    assert [index for index, _prior in seen] == [2, 3]
    assert isinstance(seen[0][1], dict)
    assert second._ladder_step(2) in second._generation_prompt(
        row,
        row.run_source_path,
        config.inventory,
        seen[0][1],
        2,
    )


def test_worker_prompts_enforce_two_generation_lanes(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    row = rows[0]

    generation_prompt = worker._generation_prompt(
        row,
        row.run_source_path,
        config.inventory,
        None,
        0,
    )
    incident_prompt = worker._incident_prompt(
        row,
        "test",
        {"error": "example"},
        config.state / "incident.md",
    )

    assert "exactly 2 generation lanes" in " ".join(generation_prompt.split())
    assert "exactly 2 generation lanes" in " ".join(incident_prompt.split())
    assert "exactly 3 generation lanes" not in generation_prompt


def test_generation_prompt_budgets_only_the_ast_build(tmp_path: Path) -> None:
    """The inner budget must not turn into ``timeout N xl_segment.py``.

    Recurrence guard for the 0568 pattern: the agent read "segmentation
    commands may run for at most 7200 seconds", wrapped xl_segment in
    ``timeout 7200``, then reran from scratch with --recurate after the kill.
    """
    config, rows, _ = _config(tmp_path)
    worker = RecoveryWorker(config)
    row = rows[0]
    prompt = " ".join(
        worker._generation_prompt(row, row.run_source_path, config.inventory, None, 0).split()
    )
    inner = worker._inner_timeout(row)
    outer = int(worker._generation_timeout(row))
    assert f"may run for at most {inner} seconds" in prompt
    assert "AST build" in prompt
    assert "segmentation commands may run for at most" not in prompt
    assert "Do not wrap xl_segment.py in `timeout`" in prompt
    assert f"({outer} seconds)" in prompt
    assert "rerun xl_segment.py WITHOUT --recurate" in prompt
    assert outer >= inner + 3600


def test_mismatched_checkpoint_quarantines_tree_and_starts_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=1)
    row = rows[0]
    first = RecoveryWorker(config)
    _stub_ready(first, records, monkeypatch)

    def dirty(*_args: object, **_kwargs: object) -> dict[str, object]:
        assert first.active_worktree is not None
        (first.active_worktree / "xl_seg" / "emit.py").write_text(
            "UNSAFE_OLD_TREE = True\n", encoding="utf-8"
        )
        return _attempt(tmp_path, 1, "FULL_RERUN_BLOCKER: HARD\n")

    monkeypatch.setattr(first, "_run_generation", dirty)
    monkeypatch.setattr(
        first,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    first.process(row)
    checkpoint_path = config.state / "checkpoints" / "0001.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["run_source_sha256"] = "0" * 64
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    second = RecoveryWorker(config)
    _stub_ready(second, records, monkeypatch)

    def clean(*_args: object, **_kwargs: object) -> dict[str, object]:
        assert second.active_worktree is not None
        assert (
            second.active_worktree / "xl_seg" / "emit.py"
        ).read_text(encoding="utf-8") == BASELINE_EMIT
        return _attempt(tmp_path, 2, "FULL_RERUN_BLOCKER: HARD\n")

    monkeypatch.setattr(second, "_run_generation", clean)
    monkeypatch.setattr(
        second,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    second.process(row)
    quarantine = list(
        (config.state / "worktree-quarantine" / "0001").glob("*")
    )
    assert len(quarantine) == 1
    assert "UNSAFE_OLD_TREE" in (
        quarantine[0] / "xl_seg" / "emit.py"
    ).read_text(encoding="utf-8")


def test_candidate_snapshot_precedes_fairness_and_tree_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "recovered\nRECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(recovery, "_inspect_release", lambda _row: candidate)

    def verifier(*_args: object, **_kwargs: object) -> dict[str, object]:
        snapshots = list(
            (config.state / "candidates" / "0001").glob("*/task-bundle")
        )
        assert len(snapshots) == 1
        assert tree_sha256(snapshots[0]) == candidate.bundle_sha256
        return _attempt(tmp_path, 99, FAIL_REPORT(""))

    monkeypatch.setattr(recovery, "_run_verifier", verifier)
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )
    result = recovery.process(row)
    assert result["status"] == "fairness_retry"
    assert (config.work_root / "task-0001").is_dir()
    assert result["candidate_snapshot"]["bundle_sha256"] == candidate.bundle_sha256


def test_partial_delivery_is_quarantined_and_replaced(tmp_path: Path) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=row.run_source_sha256,
    )
    orphan = config.output / row.batch_id / "0001-outputs"
    orphan.mkdir(parents=True)
    (orphan / "partial.txt").write_text("orphan\n", encoding="utf-8")

    sidecar = deliver_bundle(
        config,
        row,
        candidate,
        modified=False,
        verification_report=None,
        verification_attempt=None,
    )
    assert sidecar["bundle_sha256"] == candidate.bundle_sha256
    quarantined = list(
        (config.state / "delivery-quarantine" / "0001").glob("*")
    )
    assert len(quarantined) == 1
    assert (quarantined[0] / "0001-outputs" / "partial.txt").is_file()


def test_complete_delivery_with_different_bytes_is_never_overwritten(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    first = replace(
        _candidate(tmp_path / "first", amd=False),
        source_sha256=row.run_source_sha256,
    )
    deliver_bundle(
        config,
        row,
        first,
        modified=False,
        verification_report=None,
        verification_attempt=None,
    )
    destination = config.output / row.batch_id / "0001-outputs"
    original_hash = tree_sha256(destination)
    second = replace(
        _candidate(tmp_path / "second", amd=False),
        source_sha256=row.run_source_sha256,
    )
    (second.task_dir / "instruction.md").write_text(
        "different\n", encoding="utf-8"
    )
    second = replace(second, bundle_sha256=tree_sha256(second.task_dir))
    with pytest.raises(FileExistsError, match="complete verified"):
        deliver_bundle(
            config,
            row,
            second,
            modified=False,
            verification_report=None,
            verification_attempt=None,
        )
    assert tree_sha256(destination) == original_hash


def test_canonical_grader_provenance_ignores_only_python_cache(
    tmp_path: Path,
) -> None:
    config, _, _ = _config(tmp_path)
    candidate = _candidate(tmp_path)
    cache = candidate.task_dir / "tests" / "finance_grader" / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"cache")
    worker_module.assert_canonical_grader_provenance(
        config.baseline_repo, candidate.task_dir
    )
    (candidate.task_dir / "tests" / "run_grader.py").write_text(
        "tampered\n", encoding="utf-8"
    )
    with pytest.raises(PolicyViolation, match="canonical"):
        worker_module.assert_canonical_grader_provenance(
            config.baseline_repo, candidate.task_dir
        )


def test_process_checks_candidate_grader_against_untouched_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=1)
    row = rows[0]
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=row.run_source_sha256,
    )
    checked_repositories: list[Path] = []

    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "published\nRECOVERY_USED: NO\n"
        ),
    )
    monkeypatch.setattr(recovery, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker_module,
        "assert_canonical_grader_provenance",
        lambda repo, _task: checked_repositories.append(repo),
    )
    monkeypatch.setattr(
        recovery,
        "_approve_and_deliver",
        lambda *_args, **_kwargs: {"status": "test"},
    )

    result = recovery.process(row)

    assert result["status"] == "generated"
    assert checked_repositories == [config.baseline_repo]


def test_amd_precheck_requires_changed_paths_and_exact_commands(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    good = {
        "baseline_diff": {"xl_seg/emit.py": {"baseline": "a", "current": "b"}},
        "changed_during_run": ["xl_seg/emit.py"],
    }
    worker_module.validate_recovery_amd(candidate.task_dir, good)

    unmentioned = {
        "baseline_diff": {"xl_source_recalc.py": {"baseline": "a", "current": "b"}},
        "changed_during_run": ["xl_seg/emit.py", "xl_source_recalc.py"],
    }
    with pytest.raises(worker_module.RecoveryError, match="xl_source_recalc.py"):
        worker_module.validate_recovery_amd(candidate.task_dir, unmentioned)

    amd = candidate.task_dir / "a.md"
    amd.write_text(
        amd.read_text(encoding="utf-8").replace(
            "Commands: python -m xl_seg.emit --workbook 0001",
            "Commands:\n```\npython xl_seg/... --workbook 0001\n```",
        ),
        encoding="utf-8",
    )
    with pytest.raises(worker_module.RecoveryError, match="abbreviated command"):
        worker_module.validate_recovery_amd(candidate.task_dir, good)


def _worktree_with_progress(worker: RecoveryWorker, workbook_id: str) -> None:
    assert worker.active_worktree is not None
    generation = (
        worker.active_worktree / "source_out" / workbook_id / "generations" / ("f" * 64)
    )
    generation.mkdir(parents=True, exist_ok=True)


def test_an_inspect_failure_with_artifacts_gets_one_targeted_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The agent built the pipeline and only the release bookkeeping failed.
    # That is ten minutes to redo, not thirty minutes of investigator plus a
    # parked row, and the ladder must not tell the agent to change a strategy
    # that produced the artifacts.
    config, rows, records = _config(tmp_path, attempt_count=3)
    row = rows[0]
    launches: list[str] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        launches.append(str(kwargs["command"]))
        Path(str(kwargs["report_path"])).write_text(
            "INCIDENT_VERDICT: TASK_FIX\n", encoding="utf-8"
        )
        return {"status": "completed", "return_code": 0}

    worker = RecoveryWorker(config, investigator_launcher=launcher)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path, amd=False), source_sha256=row.run_source_sha256)
    seen: list[dict[str, object]] = []

    def run_generation(
        _row: QueueRow, _record: ReadyRecord, _inventory: object, prior: object, index: int
    ) -> dict[str, object]:
        _worktree_with_progress(worker, row.workbook_id)
        seen.append(
            {
                "attempt": index,
                "ladder": worker._prompt_ladder_index(index),
                "retry": dict(worker._paperwork_retry or {}),
                "prompt": worker._generation_prompt(
                    row, tmp_path / "wb.xlsx", None, prior, index
                ),
            }
        )
        return _attempt(tmp_path, index + 1, "published\nRECOVERY_USED: NO\n")

    inspections = {"count": 0}

    def inspect(_row: QueueRow) -> ReleaseCandidate:
        inspections["count"] += 1
        if inspections["count"] == 1:
            raise worker_module.RecoveryError("current release pointer is missing")
        return candidate

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", inspect)
    monkeypatch.setattr(
        worker_module, "assert_canonical_grader_provenance", lambda *_a: None
    )
    monkeypatch.setattr(
        worker, "_approve_and_deliver", lambda *_args, **_kwargs: {"status": "test"}
    )

    result = worker.process(row)

    assert result["status"] == "generated"
    assert result["cumulative_attempt_count"] == 2
    # No investigator at all: not for the inspect failure, and not the
    # generated-recurrence one that a second attempt used to trigger.
    assert launches == []
    assert [item["attempt"] for item in seen] == [0, 1]
    assert [item["ladder"] for item in seen] == [0, 0]
    assert seen[1]["retry"]["gate"] == "inspect"
    assert "current release pointer is missing" in seen[1]["prompt"]
    assert "targeted retry" in seen[1]["prompt"]
    assert "f" * 64 in seen[1]["prompt"]
    assert "Do not repeat the same failed strategy" not in seen[1]["prompt"]
    checkpoint = json.loads(
        (config.state / "checkpoints" / f"{row.workbook_id}.json").read_text()
    )
    assert checkpoint["paperwork_retry_count"] == 1


def test_a_second_paperwork_failure_goes_to_the_investigator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=3)
    row = rows[0]
    launches: list[str] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        launches.append(str(kwargs["command"]))
        Path(str(kwargs["report_path"])).write_text("report\n", encoding="utf-8")
        return {"status": "completed", "return_code": 0}

    worker = RecoveryWorker(config, investigator_launcher=launcher)
    _stub_ready(worker, records, monkeypatch)
    ladders: list[int] = []

    def run_generation(*args: object, **_kwargs: object) -> dict[str, object]:
        _worktree_with_progress(worker, row.workbook_id)
        index = int(args[-1])
        ladders.append(worker._prompt_ladder_index(index))
        return _attempt(tmp_path, index + 1, "published\nRECOVERY_USED: NO\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    result = worker.process(row)

    assert result["status"] == "recovery_pending"
    # Attempt 1 fails -> free retry on the same step; attempt 2 fails -> the
    # investigator runs and the ladder advances from there.
    assert ladders == [0, 0, 1]
    assert len(launches) == 1


def test_an_incomplete_amd_is_retried_before_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=3)
    row = rows[0]
    launches: list[str] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        launches.append(str(kwargs["command"]))
        Path(str(kwargs["report_path"])).write_text("report\n", encoding="utf-8")
        return {"status": "completed", "return_code": 0}

    worker = RecoveryWorker(config, investigator_launcher=launcher)
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    amd = candidate.task_dir / "a.md"
    complete = amd.read_text(encoding="utf-8")
    amd.write_text("# a.md\nblocker only\n", encoding="utf-8")
    retries: list[dict[str, object]] = []
    verifier_calls = {"count": 0}

    def run_generation(*args: object, **_kwargs: object) -> dict[str, object]:
        retries.append(dict(worker._paperwork_retry or {}))
        if worker._paperwork_retry is not None:
            amd.write_text(complete, encoding="utf-8")
        return _attempt(tmp_path, int(args[-1]) + 1, "recovered\nRECOVERY_USED: YES\n")

    def approve(*_args: object, **_kwargs: object) -> dict[str, object]:
        verifier_calls["count"] += 1
        return {"status": "test"}

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(
        worker_module, "assert_canonical_grader_provenance", lambda *_a: None
    )
    monkeypatch.setattr(worker, "_approve_and_deliver", approve)

    result = worker.process(row)

    assert result["status"] in {"generated", "worker_fix_needed"}
    assert retries[0] == {}
    assert retries[1]["gate"] == "a.md recovery record"
    assert "not a complete recovery record" in str(retries[1]["error"])
    # The verifier only ever saw the complete a.md.
    assert verifier_calls["count"] == 1


def test_incident_investigator_deduplicates_failure_fingerprint(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    launches: list[list[str]] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        launches.append(list(kwargs["command"]))  # type: ignore[arg-type]
        Path(str(kwargs["report_path"])).write_text("report\n", encoding="utf-8")
        return {"status": "completed", "return_code": 0}

    recovery = RecoveryWorker(config, investigator_launcher=launcher)
    first = recovery._launch_incident(
        rows[0],
        "inspect",
        {"error": "missing release", "attempt": 1, "strategy": "normal"},
    )
    second = recovery._launch_incident(
        rows[0],
        "inspect",
        {"error": "missing release", "attempt": 2, "strategy": "recurate"},
    )
    assert len(launches) == 1
    assert "--mode" in launches[0] and "ask" in launches[0]
    assert first["fingerprint"] == second["fingerprint"]
    assert second["deduplicated"] is True
    assert Path(str(first["result_path"])).is_file()
    assert Path(str(first["report_path"])).is_file()


def test_default_incident_launcher_waits_and_persists_final_report(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    config.agent_binary.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({"result": "Root cause found.\\nRecurrence guard added."}))
""",
        encoding="utf-8",
    )
    recovery = RecoveryWorker(config)

    incident = recovery._launch_incident(
        rows[0],
        "delivery",
        {"error": "partial delivery"},
    )

    result = json.loads(Path(str(incident["result_path"])).read_text())
    report = Path(str(incident["report_path"])).read_text(encoding="utf-8")
    assert result["launch"]["status"] == "completed"
    assert result["launch"]["return_code"] == 0
    assert report == "Root cause found.\nRecurrence guard added.\n"
    assert Path(result["launch"]["stream_path"]).is_file()


def test_worker_defect_incident_stops_same_row_until_worker_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=2)
    row = rows[0]
    attempts: list[int] = []

    def launcher(**kwargs: object) -> dict[str, object]:
        Path(str(kwargs["report_path"])).write_text(
            "The inspect contract is wrong.\n"
            + CONTROLLER_VERDICT("inspect_contract", "scripts/batch/recovery_worker.py")
            + "INCIDENT_VERDICT: WORKER_FIX_NEEDED\n",
            encoding="utf-8",
        )
        return {"status": "completed", "return_code": 0}

    recovery = RecoveryWorker(config, investigator_launcher=launcher)
    _stub_ready(recovery, records, monkeypatch)

    def generate(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path | None,
        _prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        attempts.append(attempt_index)
        return _attempt(tmp_path, attempt_index + 1, "RECOVERY_USED: NO\n")

    monkeypatch.setattr(recovery, "_run_generation", generate)
    monkeypatch.setattr(
        recovery,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(
            worker_module.RecoveryError("worker rejected valid release")
        ),
    )

    result = recovery.process(row)

    assert result["status"] == "worker_fix_needed"
    assert attempts == [0]
    assert (config.work_root / "task-0001").is_dir()
    checkpoint = json.loads(
        (config.state / "checkpoints" / "0001.json").read_text()
    )
    assert checkpoint["status"] == "worker_fix_needed"
    assert checkpoint["worker_fix_baseline_hash"] == recovery.baseline_hash
    assert checkpoint["incident_reports"][-1]["worker_fix_needed"] is True


def test_unchanged_worker_defect_does_not_retry_or_skip_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(
        tmp_path,
        workbook_ids=("0001", "0002"),
        attempt_count=1,
    )
    recovery = RecoveryWorker(config)
    config.state.mkdir()
    (config.state / "summary.json").write_text(
        json.dumps(
            {
                "tasks": {
                    "0001": {
                        "batch_id": "batch-001",
                        "status": "worker_fix_needed",
                        "worker_fix_baseline_hash": recovery.baseline_hash,
                        "worker_fix_overlay": recovery.worker_overlay_id,
                    },
                    "0002": {
                        "batch_id": "batch-001",
                        "status": "recovery_pending",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    processed: list[str] = []
    monkeypatch.setattr(
        recovery,
        "process",
        lambda row: processed.append(row.workbook_id) or {
            "workbook_id": row.workbook_id,
            "status": "generated",
        },
    )

    ledger = recovery.run()

    # A row parked on the same worker build stays parked, but it no longer
    # blocks the rows behind it: the lane keeps working the queue and the
    # lane fix step owns the parked row.
    assert processed == ["0002"]
    assert ledger["records"][0]["status"] == "worker_fix_needed"
    assert ledger["records"][1]["status"] == "generated"


def test_capped_row_is_not_reselected_and_stays_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=1, row_attempt_cap=2
    )
    recovery = RecoveryWorker(config)
    config.state.mkdir()
    (config.state / "failures").mkdir()
    (config.state / "failures" / "0001.json").write_text(
        json.dumps({"lifetime_attempts": 2, "class": "inspect", "signature": "x"}),
        encoding="utf-8",
    )
    processed: list[str] = []
    monkeypatch.setattr(
        recovery,
        "process",
        lambda row: processed.append(row.workbook_id) or {
            "workbook_id": row.workbook_id,
            "status": "generated",
        },
    )

    ledger = recovery.run()

    assert processed == ["0002"]
    assert ledger["records"][0]["status"] == "recovery_pending"
    assert ledger["records"][1]["status"] == "generated"


def test_row_that_never_launches_an_attempt_is_not_spun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(tmp_path, workbook_ids=("0001", "0002"), attempt_count=1)
    recovery = RecoveryWorker(config)
    calls: list[str] = []

    def process(row: QueueRow) -> dict[str, object]:
        calls.append(row.workbook_id)
        return {
            "workbook_id": row.workbook_id,
            "status": "recovery_pending",
            "error": f"workbook {row.workbook_id} is absent from the pinned approved inventory",
            "queued": True,
        }

    monkeypatch.setattr(recovery, "process", process)

    ledger = recovery.run()

    # No generation attempt happened, so an immediate retry would only spin.
    # Each row is tried once and left active for the next lane cycle.
    assert calls == ["0001", "0002"]
    assert [row["status"] for row in ledger["records"]] == ["recovery_pending"] * 2
    assert _failure_record(config, "0001")["class"] == "orchestration"
    assert worker_module.exit_code_for(ledger, recovery.run_state) == worker_module.EXIT_ROWS_REMAIN


def test_exit_code_mapping() -> None:
    generated = {"counts": {"generated": 3}}
    pending = {"counts": {"generated": 1, "recovery_pending": 2}}
    assert worker_module.exit_code_for(generated, {}) == worker_module.EXIT_ALL_GENERATED
    assert worker_module.exit_code_for(pending, {}) == worker_module.EXIT_ROWS_REMAIN
    assert (
        worker_module.exit_code_for(pending, {"host_floor": True})
        == worker_module.EXIT_HOST_FLOOR
    )
    assert (
        worker_module.exit_code_for(pending, {"controller_fingerprints": ["abc"]})
        == worker_module.EXIT_CONTROLLER_FIX
    )
    # A host floor outranks a controller fingerprint: nothing can run anyway.
    assert (
        worker_module.exit_code_for(
            pending, {"host_floor": True, "controller_fingerprints": ["abc"]}
        )
        == worker_module.EXIT_HOST_FLOOR
    )


def test_error_signature_is_stable_across_hashes_paths_and_counters() -> None:
    first = worker_module.error_signature(
        "RecoveryError: task generation artifacts do not match manifest "
        "at /home/u/GDM_FCP/run/work-a/task-0539/release_out/0539/releases/"
        "69f5016ad836ca96be62a4bcec695d0a67579c01f026d6b918596419a6661c26 (417737 bytes)"
    )
    second = worker_module.error_signature(
        "RecoveryError: task generation artifacts do not match manifest "
        "at /home/u/GDM_FCP/run/work-b/task-0442/release_out/0442/releases/"
        "b43e136851a609c026febec43c9c6ae9ff612fd93dd9a3b77020722bea7f059a (12 bytes)"
    )
    assert first == second
    assert first[0] == "RecoveryError"
    assert "<path>" in first[1] and "<n>" in first[1]
    bare_hash = worker_module.error_signature(
        "bundle 69f5016ad836ca96be62a4bcec695d0a67579c01f026d6b918596419a6661c26 rejected"
    )
    assert bare_hash == ("", "bundle <hash> rejected")
    other = worker_module.error_signature(
        "RecoveryError: current immutable release does not bind"
    )
    assert other != first
    assert worker_module.failure_signature("inspect", 16, *first) != (
        worker_module.failure_signature("inspect", 16, *other)
    )


def test_classify_failure_precedence() -> None:
    classify = worker_module.classify_failure
    killed = classify(
        {
            "status": "recovery_pending",
            "inspect_error": "RecoveryError: no release",
            "last_attempt": {"status": "failed", "limit_reason": "cgroup_oom"},
        }
    )
    assert (killed["class"], killed["subclass"]) == ("generation_failed", "failed")
    oom = classify(
        {
            "status": "recovery_pending",
            "inspect_error": "RecoveryError: no release",
            "last_attempt": {"status": "completed", "limit_reason": "cgroup_oom"},
        }
    )
    assert (oom["class"], oom["subclass"]) == ("generation_failed", "cgroup_oom")
    hard = classify(
        {
            "status": "task_fix_needed",
            "saw_hard_blocker": True,
            "last_attempt": {"status": "completed"},
        }
    )
    assert hard["class"] == "agent_hard_blocker"
    inspect = classify(
        {
            "status": "recovery_pending",
            "inspect_error": "RecoveryError: current immutable release does not bind",
            "last_attempt": {"status": "completed"},
        }
    )
    assert (inspect["class"], inspect["gate"]) == ("inspect", 16)
    bug = classify(
        {
            "status": "recovery_pending",
            "inspect_error": "TypeError: 'NoneType' object is not subscriptable",
            "last_attempt": {"status": "completed"},
        }
    )
    assert bug["class"] == "controller"
    amd = classify(
        {
            "status": "recovery_pending",
            "error": "recovered task a.md is not a complete recovery record; changed files not named in a.md: x",
            "delivery_error": "recovered task a.md is not a complete recovery record; changed files not named in a.md: x",
        }
    )
    assert (amd["class"], amd["gate"]) == ("recovery_record", 16)
    delivered = classify(
        {"status": "recovery_pending", "delivery_error": "refusing to overwrite complete verified bundle"}
    )
    assert (delivered["class"], delivered["subclass"]) == ("delivery", "already_delivered")
    fairness = classify(
        {
            "status": "fairness_retry",
            "fairness_verdict": {
                "checks": {"solvability": "fail", "recovery_record": "pass"},
                "findings": [
                    {"check": "solvability", "subclass": "units_percent_vs_ratio", "gate": 6}
                ],
            },
        }
    )
    assert (fairness["class"], fairness["subclass"], fairness["gate"]) == (
        "fairness_semantic",
        "units_percent_vs_ratio",
        6,
    )
    paperwork = classify(
        {"status": "fairness_retry", "fairness_verdict": {"checks": {"recovery_record": "fail"}}}
    )
    assert paperwork["class"] == "recovery_record"
    controller = classify({"status": "worker_fix_needed", "error": "x"})
    assert controller["class"] == "controller"
    capped = classify({"status": "recovery_pending", "retry_budget_exhausted": True})
    assert capped["class"] == "retry_budget"
    disk = classify({"status": "disk_exhausted"})
    assert disk["class"] == "disk_exhausted"
    capacity = classify(
        {"status": "recovery_pending", "error": "evidence total would be 9 bytes, above configured limit 8"}
    )
    assert (capacity["class"], capacity["subclass"]) == ("disk_exhausted", "evidence_capacity")
    unknown = classify({"status": "recovery_pending", "error": "ValueError: odd"})
    assert (unknown["class"], unknown["subclass"]) == ("unknown", "valueerror")
    assert all(item["class"] in worker_module.FAILURE_CLASSES for item in (
        killed, oom, hard, inspect, bug, amd, delivered, fairness, paperwork, controller, capped, disk, capacity, unknown
    ))


def test_full_ledger_preserves_unprocessed_detailed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # task_limit=1 keeps 0002 unselected: with immediate retry the lane would
    # otherwise reach it, and the point here is that an unprocessed row's
    # prior detail survives the ledger rewrite untouched.
    config, rows, records = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=1, task_limit=1
    )
    config.state.mkdir()
    (config.state / "summary.json").write_text(
        json.dumps(
            {
                "tasks": {
                    "0002": {
                        "batch_id": "batch-001",
                        "status": "fairness_retry",
                        "error": "prior fairness detail",
                        "verification_attempt": {"attempt": "old"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "FULL_RERUN_BLOCKER: HARD\n"
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    ledger = recovery.run()
    assert ledger["expected_count"] == 2
    assert len(ledger["records"]) == 2
    second = ledger["records"][1]
    assert second["status"] == "fairness_retry"
    assert second["error"] == "prior fairness detail"
    assert second["verification_attempt"] == {"attempt": "old"}


def test_preserve_copy_error_merges_into_main_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=1)
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "FULL_RERUN_BLOCKER: HARD\n"
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    monkeypatch.setattr(
        worker_module,
        "_preserve_failed_release",
        lambda *_args: (_ for _ in ()).throw(OSError("copy failed")),
    )
    recovery.process(rows[0])
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )
    assert evidence["preserve_error"] == "copy failed"
    assert "no valid complete immutable release" in evidence["error"]
    assert not (config.state / "task-evidence" / "0001.preserve-error.json").exists()


def test_git_worktree_creation_cleans_generated_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, _ = _config(tmp_path)
    monkeypatch.setattr(worker_module, "_git_head", lambda _repo: "a" * 64)

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if "add" in command:
            destination = Path(command[-1])
            destination.mkdir(parents=True)
            (destination / "tasks_outputs_final" / "stale").mkdir(parents=True)
            (destination / "release_out" / "stale").mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worker_module.subprocess, "run", run)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, "0001"
    )
    assert not (worktree / "tasks_outputs_final").exists()
    assert not (worktree / "release_out").exists()
    assert (worktree / ".recovery-worktree").read_text() == "git\n"


def test_tasks_outputs_final_is_generated_not_protected() -> None:
    path = Path("tasks_outputs_final/0001-outputs/tests/run_grader.py")
    assert worker_module._ignored_snapshot_path(path)
    assert worker_module._is_generated_root("tasks_outputs_final")


def test_inspect_timeout_is_recovery_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery, row, worktree = _inspect_worktree(tmp_path)

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("inspect", worker_module.INSPECT_TIMEOUT_SECONDS)

    monkeypatch.setattr(worker_module.subprocess, "run", timeout)
    try:
        with pytest.raises(worker_module.RecoveryError, match="inspect timed out"):
            recovery._inspect_release(row)
    finally:
        recovery.active_worktree = None
        shutil_rmtree = __import__("shutil").rmtree
        shutil_rmtree(worktree, ignore_errors=True)


def test_unmodified_candidate_skips_verifier_and_delivery_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=row.run_source_sha256,
    )
    inspections = 0

    def inspect(_row: QueueRow) -> ReleaseCandidate:
        nonlocal inspections
        inspections += 1
        return candidate

    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "published\nRECOVERY_USED: NO\n"
        ),
    )
    monkeypatch.setattr(recovery, "_inspect_release", inspect)
    monkeypatch.setattr(
        recovery,
        "_run_verifier",
        lambda *_args, **_kwargs: pytest.fail("verifier should be skipped"),
    )
    result = recovery.process(row)
    assert result["status"] == "generated"
    assert result["modified"] is False
    assert inspections == 1

    restarted = RecoveryWorker(config)
    monkeypatch.setattr(
        restarted,
        "process",
        lambda _row: pytest.fail("complete delivery should be skipped"),
    )
    ledger = restarted.run()
    assert ledger["records"][0]["status"] == "generated"


def test_modified_candidate_is_reinspected_after_fairness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    inspections = 0

    def inspect(_row: QueueRow) -> ReleaseCandidate:
        nonlocal inspections
        inspections += 1
        return candidate

    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "RECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(recovery, "_inspect_release", inspect)
    monkeypatch.setattr(
        recovery,
        "_run_verifier",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 90, PASS_REPORT("")
        ),
    )
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )
    assert recovery.process(row)["status"] == "generated"
    assert inspections == 2
    incident_results = list(
        (config.state / "incidents" / "0001").glob(
            "*/attempts/*/result.json"
        )
    )
    categories = {
        json.loads(path.read_text(encoding="utf-8"))["category"]
        for path in incident_results
    }
    assert "generated-recurrence" in categories


def test_worker_lock_fails_closed(tmp_path: Path) -> None:
    config, _, _ = _config(tmp_path)
    config.state.mkdir()
    lock = config.state / "worker.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(worker_module.RecoveryError, match="another recovery worker"):
            RecoveryWorker(config).run()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@pytest.mark.parametrize("field", ["work_root", "state", "output"])
@pytest.mark.parametrize("direction", ["below_baseline", "contains_baseline"])
def test_root_layout_rejects_pairwise_nesting_before_creation(
    tmp_path: Path, field: str, direction: str
) -> None:
    config, _, _ = _config(tmp_path)
    if direction == "below_baseline":
        unsafe = config.baseline_repo / f"unsafe-{field}"
        assert not unsafe.exists()
    else:
        unsafe = tmp_path
    config = replace(config, **{field: unsafe})
    with pytest.raises(worker_module.RecoveryError, match="pairwise non-nested"):
        RecoveryWorker(config).run()
    if direction == "below_baseline":
        assert not unsafe.exists()


@pytest.mark.parametrize(
    "field", ["baseline_repo", "work_root", "state", "output"]
)
def test_root_layout_rejects_symlink_roots(
    tmp_path: Path, field: str
) -> None:
    config, _, _ = _config(tmp_path)
    original = getattr(config, field)
    target = original
    if field != "baseline_repo":
        target.mkdir(parents=True, exist_ok=True)
    link = tmp_path / f"{field}-link"
    link.symlink_to(target, target_is_directory=True)
    config = replace(config, **{field: link})
    recovery = RecoveryWorker(config)
    with pytest.raises(worker_module.RecoveryError, match="must not be a symlink"):
        recovery.run()


def test_unique_fairness_report_accepts_only_current_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    _stub_ready(recovery, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    seen_paths: list[Path] = []

    def verifier(*args: object) -> dict[str, object]:
        report_path = Path(args[-1])
        seen_paths.append(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            PASS_REPORT("fresh review"), encoding="utf-8"
        )
        return _attempt(tmp_path, 90, "wrote fresh report\n")

    monkeypatch.setattr(
        recovery,
        "_run_generation",
        lambda *_args, **_kwargs: _attempt(
            tmp_path, 1, "RECOVERY_USED: YES\n"
        ),
    )
    monkeypatch.setattr(recovery, "_inspect_release", lambda _row: candidate)
    monkeypatch.setattr(recovery, "_run_verifier", verifier)
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )
    assert recovery.process(row)["status"] == "generated"
    assert len(seen_paths) == 1
    assert candidate.bundle_sha256 in seen_paths[0].parts
    assert seen_paths[0].name.startswith("fairness-")


def test_failed_release_rejects_root_and_nested_symlinks(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    worktree = tmp_path / "failed-worktree"
    release_root = worktree / "release_out"
    release_root.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    root_link = release_root / row.workbook_id
    root_link.symlink_to(external, target_is_directory=True)
    with pytest.raises(worker_module.RecoveryError, match="safe directory"):
        _preserve_failed_release(config.state, row, worktree, config)
    root_link.unlink()
    root_link.mkdir()
    (root_link / "good.txt").write_text("good\n", encoding="utf-8")
    (root_link / "nested").symlink_to(external, target_is_directory=True)
    with pytest.raises(worker_module.RecoveryError, match="contains a symlink"):
        _preserve_failed_release(config.state, row, worktree, config)
    assert not (config.state / "failed-releases").exists()


def test_failed_release_copy_failure_preserves_prior_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    worktree = tmp_path / "failed-worktree"
    release = worktree / "release_out" / row.workbook_id
    release.mkdir(parents=True)
    (release / "note.txt").write_text("first\n", encoding="utf-8")
    first = _preserve_failed_release(config.state, row, worktree, config)
    assert first is not None
    current_path = config.state / "failed-releases" / row.workbook_id / "current.json"
    prior_pointer = current_path.read_bytes()
    (release / "note.txt").write_text("second\n", encoding="utf-8")
    monkeypatch.setattr(
        worker_module.shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with pytest.raises(OSError, match="copy failed"):
        _preserve_failed_release(config.state, row, worktree, config)
    assert current_path.read_bytes() == prior_pointer
    assert (first / "note.txt").read_text(encoding="utf-8") == "first\n"


def test_interrupted_attempt_advances_before_restart_and_retains_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, attempt_count=1)
    row = rows[0]
    first = RecoveryWorker(config)
    _stub_ready(first, records, monkeypatch)

    def interrupted(
        *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        assert first.active_worktree is not None
        (first.active_worktree / "xl_seg" / "emit.py").write_text(
            "INTERRUPTED_CHANGE = True\n", encoding="utf-8"
        )
        evidence = first.active_worktree / "seg_out" / row.workbook_id
        evidence.mkdir(parents=True)
        (evidence / "partial.json").write_text("{}\n", encoding="utf-8")
        raise RuntimeError("VM interrupted")

    monkeypatch.setattr(first, "_run_generation", interrupted)
    with pytest.raises(RuntimeError, match="VM interrupted"):
        first.process(row)
    checkpoint_path = config.state / "checkpoints" / "0001.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["cumulative_attempt_count"] == 1
    assert checkpoint["attempt_in_progress"]["attempt_index"] == 0

    second = RecoveryWorker(config)
    _stub_ready(second, records, monkeypatch)
    seen: list[int] = []

    def resumed(
        _row: QueueRow,
        _record: ReadyRecord,
        _inventory: Path,
        _prior: object,
        attempt_index: int,
    ) -> dict[str, object]:
        assert second.active_worktree is not None
        assert "INTERRUPTED_CHANGE" in (
            second.active_worktree / "xl_seg" / "emit.py"
        ).read_text()
        assert (
            second.active_worktree / "seg_out" / "0001" / "partial.json"
        ).is_file()
        seen.append(attempt_index)
        return _attempt(tmp_path, 2, "FULL_RERUN_BLOCKER: HARD\n")

    monkeypatch.setattr(second, "_run_generation", resumed)
    monkeypatch.setattr(
        second,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )
    second.process(row)
    assert seen == [1]
    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["interrupted_attempts"][0]["attempt_index"] == 0


def test_independent_release_rejects_pointer_and_manifest_tampering(
    tmp_path: Path,
) -> None:
    recovery, row, worktree = _inspect_worktree(tmp_path)
    release_id, generation_id, task = _write_independent_release(worktree, row)
    resolver = {
        "release_id": release_id,
        "release_dir": str(
            worktree / "release_out" / row.workbook_id / "releases" / release_id
        ),
        "task_dir": str(task),
        "task_generation_id": generation_id,
    }
    candidate = worker_module._independent_release_candidate(
        row, worktree, resolver
    )
    snapshot = recovery._snapshot_candidate(row, candidate)
    snapshot_root = Path(snapshot["snapshot_path"]).parent
    assert (snapshot_root / "current-release.json").is_file()
    assert (snapshot_root / "release-manifest.json").is_file()
    assert (snapshot_root / "task-generation-manifest.json").is_file()

    pointer_path = worktree / "release_out" / row.workbook_id / "current-release.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["release_path"] = f"../releases/{release_id}"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(worker_module.RecoveryError, match="path is not canonical"):
        worker_module._independent_release_candidate(row, worktree, resolver)


def test_independent_release_rejects_resolver_path_and_task_bytes(
    tmp_path: Path,
) -> None:
    _recovery, row, worktree = _inspect_worktree(tmp_path)
    release_id, generation_id, task = _write_independent_release(worktree, row)
    release_dir = (
        worktree / "release_out" / row.workbook_id / "releases" / release_id
    )
    resolver = {
        "release_id": release_id,
        "release_dir": str(release_dir),
        "task_dir": str(task),
        "task_generation_id": generation_id,
    }
    lying = {**resolver, "task_dir": str(tmp_path / "escaped")}
    with pytest.raises(worker_module.RecoveryError, match="resolver output"):
        worker_module._independent_release_candidate(row, worktree, lying)
    (task / "instruction.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(worker_module.RecoveryError, match="artifacts"):
        worker_module._independent_release_candidate(row, worktree, resolver)


def test_candidate_snapshots_are_keyed_by_release_identity(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    recovery = RecoveryWorker(config)
    first = replace(
        _candidate(tmp_path / "one"),
        source_sha256=rows[0].run_source_sha256,
        release_id="1" * 64,
        task_generation_id="2" * 64,
    )
    second = replace(
        first,
        release_id="3" * 64,
        task_generation_id="4" * 64,
    )
    one = recovery._snapshot_candidate(rows[0], first)
    two = recovery._snapshot_candidate(rows[0], second)
    assert one["bundle_sha256"] == two["bundle_sha256"]
    assert Path(one["snapshot_path"]).parent != Path(two["snapshot_path"]).parent


@pytest.mark.parametrize(
    ("launch", "report"),
    [
        ({"status": "unavailable"}, "report\n"),
        ({"status": "completed", "return_code": 2}, "report\n"),
        ({"status": "launch_error"}, "report\n"),
        ({"status": "timed_out", "return_code": -15}, "report\n"),
        ({"status": "completed", "return_code": 0}, ""),
    ],
)
def test_incomplete_incidents_remain_retryable(
    tmp_path: Path,
    launch: dict[str, object],
    report: str,
) -> None:
    config, rows, _ = _config(tmp_path)
    launches = 0

    def launcher(**kwargs: object) -> dict[str, object]:
        nonlocal launches
        launches += 1
        Path(str(kwargs["report_path"])).write_text(report, encoding="utf-8")
        return launch

    recovery = RecoveryWorker(config, investigator_launcher=launcher)
    first = recovery._launch_incident(rows[0], "inspect", {"error": "same"})
    second = recovery._launch_incident(rows[0], "inspect", {"error": "same"})
    assert launches == 2
    assert first["completed"] is False
    assert second["deduplicated"] is False
    fingerprint_root = (
        config.state / "incidents" / "0001" / str(first["fingerprint"])
    )
    assert not (fingerprint_root / "completed.json").exists()
    assert len(list(fingerprint_root.glob("attempts/*/result.json"))) == 2


def test_malformed_incident_jsonl_keeps_raw_and_is_retryable(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    config.agent_binary.write_text(
        "#!/bin/sh\nprintf 'not-json\\n'\n", encoding="utf-8"
    )
    recovery = RecoveryWorker(config)
    incident = recovery._launch_incident(
        rows[0], "unexpected", {"error": "malformed"}
    )
    result = json.loads(Path(incident["result_path"]).read_text())
    assert incident["completed"] is False
    assert result["launch"]["status"] == "empty_or_malformed"
    assert Path(result["launch"]["stream_path"]).read_text() == "not-json\n"
    assert not (
        config.state
        / "incidents"
        / "0001"
        / str(incident["fingerprint"])
        / "completed.json"
    ).exists()


def test_concurrent_valid_delivery_is_not_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=row.run_source_sha256,
    )
    original = worker_module._quarantine_partial_delivery
    installed = False

    def concurrent(config_arg: Config, row_arg: QueueRow) -> Path | None:
        nonlocal installed
        if not installed:
            installed = True
            batch = config.output / row.batch_id
            bundle = batch / "0001-outputs"
            batch.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidate.task_dir, bundle)
            sidecar = {
                "schema_version": worker_module.DELIVERY_SCHEMA,
                "batch_id": row.batch_id,
                "workbook_id": row.workbook_id,
                "release_id": candidate.release_id,
                "task_generation_id": candidate.task_generation_id,
                "source_sha256": row.run_source_sha256,
                "run_source_sha256": row.run_source_sha256,
                "bundle_path": str(bundle.resolve()),
                "bundle_sha256": tree_sha256(bundle),
                "modified": False,
                "fairness_verdict": "not_required",
                "fairness_report_path": None,
                "fairness_report_sha256": None,
                "verification_attempt": None,
                "guard_evidence": {},
            }
            (batch / "0001-outputs.verification.json").write_bytes(
                worker_module._canonical_bytes(sidecar)
            )
        return original(config_arg, row_arg)

    monkeypatch.setattr(
        worker_module, "_quarantine_partial_delivery", concurrent
    )
    sidecar = deliver_bundle(
        config,
        row,
        candidate,
        modified=False,
        verification_report=None,
        verification_attempt=None,
    )
    assert sidecar["bundle_sha256"] == candidate.bundle_sha256
    assert not (config.state / "delivery-quarantine").exists()


def test_restart_rejects_self_consistent_noncanonical_grader(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    candidate = replace(
        _candidate(tmp_path, amd=False),
        source_sha256=row.run_source_sha256,
    )
    deliver_bundle(
        config,
        row,
        candidate,
        modified=False,
        verification_report=None,
        verification_attempt=None,
    )
    batch = config.output / row.batch_id
    bundle = batch / "0001-outputs"
    (bundle / "tests" / "run_grader.py").write_text(
        "noncanonical\n", encoding="utf-8"
    )
    sidecar_path = batch / "0001-outputs.verification.json"
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["bundle_sha256"] = tree_sha256(bundle)
    sidecar_path.write_bytes(worker_module._canonical_bytes(sidecar))
    assert worker_module._read_valid_delivery(config, row) is None


def test_restart_rejects_external_fairness_report(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    candidate = replace(
        _candidate(tmp_path),
        source_sha256=row.run_source_sha256,
    )
    report = PASS_REPORT("review")
    deliver_bundle(
        config,
        row,
        candidate,
        modified=True,
        verification_report=report,
        verification_attempt={"attempt": "test"},
    )
    batch = config.output / row.batch_id
    expected = batch / "0001-outputs.fairness.md"
    external = tmp_path / "external-fairness.md"
    os.replace(expected, external)
    sidecar_path = batch / "0001-outputs.verification.json"
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["fairness_report_path"] = str(external)
    sidecar["fairness_report_sha256"] = worker_module.sha256_file(external)
    sidecar_path.write_bytes(worker_module._canonical_bytes(sidecar))
    assert worker_module._read_valid_delivery(config, row) is None


def test_evidence_limits_leave_latest_usable_artifacts(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    tiny_item = replace(
        config,
        evidence_item_limit_bytes=10,
        evidence_global_limit_bytes=1000,
    )
    recovery = RecoveryWorker(tiny_item)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, row.workbook_id
    )
    with pytest.raises(worker_module.RecoveryError, match="configured limit"):
        recovery._quarantine_worktree(row, worktree, "test")
    assert worktree.is_dir()
    candidate = replace(
        _candidate(tmp_path),
        source_sha256=row.run_source_sha256,
    )
    with pytest.raises(worker_module.RecoveryError, match="configured limit"):
        recovery._snapshot_candidate(row, candidate)

    bounded = replace(
        config,
        evidence_item_limit_bytes=700,
        evidence_global_limit_bytes=900,
    )
    batch = bounded.output / row.batch_id
    first = batch / "0001-outputs"
    first.mkdir(parents=True)
    (first / "partial.bin").write_bytes(b"x" * 400)
    first_snapshot = worker_module._quarantine_partial_delivery(bounded, row)
    assert first_snapshot is not None and first_snapshot.is_dir()
    second = batch / "0001-outputs"
    second.mkdir()
    (second / "partial.bin").write_bytes(b"y" * 400)
    with pytest.raises(worker_module.RecoveryError, match="evidence total"):
        worker_module._quarantine_partial_delivery(bounded, row)
    assert second.is_dir()
    assert first_snapshot.is_dir()


def test_worktree_quarantine_preserves_internal_publication_symlinks(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, row.workbook_id
    )
    generation = (
        worktree
        / "seg_out"
        / row.workbook_id
        / "generations"
        / "generation-1"
    )
    generation.mkdir(parents=True)
    (generation / "manifest.json").write_text("{}\n", encoding="utf-8")
    current = worktree / "seg_out" / row.workbook_id / "current"
    current.symlink_to(Path("generations") / generation.name)

    quarantine = recovery._quarantine_worktree(
        row, worktree, "baseline changed"
    )

    assert quarantine is not None
    preserved = quarantine / "seg_out" / row.workbook_id / "current"
    assert preserved.is_symlink()
    assert os.readlink(preserved) == f"generations/{generation.name}"
    assert not worktree.exists()


def test_worktree_refresh_preserves_recovery_lineage(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, row.workbook_id
    )
    recovery._write_worktree_marker(row, worktree)
    prior_attempt = {
        "attempt_number": 7,
        "status": "fairness_retry",
        "error": "external relationship required sanitation",
    }
    recovery._save_checkpoint(
        row,
        worktree,
        cumulative_attempt_count=7,
        last_attempt=prior_attempt,
        fairness_result={"passed": False, "report": "semantic defect"},
        incident_reports=[{"category": "fairness"}],
    )
    marker_path = recovery._marker_path(worktree)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["baseline_hash"] = "0" * 64
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    replacement = recovery._acquire_worktree(row)
    checkpoint = recovery._load_checkpoint(row)

    assert replacement.is_dir()
    assert checkpoint["cumulative_attempt_count"] == 7
    assert checkpoint["last_attempt"] == prior_attempt
    assert checkpoint["fairness_result"]["passed"] is False
    assert checkpoint["incident_reports"] == [{"category": "fairness"}]
    assert checkpoint["baseline_refresh_history"]
    assert "binding mismatch" in checkpoint["baseline_refresh_history"][-1]["reason"]


def test_worktree_quarantine_rejects_escaping_symlink(
    tmp_path: Path,
) -> None:
    config, rows, _ = _config(tmp_path)
    row = rows[0]
    recovery = RecoveryWorker(config)
    worktree = create_isolated_worktree(
        config.baseline_repo, config.work_root, row.workbook_id
    )
    external = tmp_path / "external"
    external.mkdir()
    link = worktree / "seg_out" / row.workbook_id / "current"
    link.parent.mkdir(parents=True)
    link.symlink_to(external, target_is_directory=True)

    with pytest.raises(worker_module.RecoveryError, match="escaping symlink"):
        recovery._quarantine_worktree(row, worktree, "baseline changed")

    assert worktree.is_dir()


def test_queue_mutation_writes_full_ledger_and_current_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, _ = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=1
    )
    recovery = RecoveryWorker(config)

    def mutate(row: QueueRow) -> dict[str, object]:
        config.queue.write_text("[]\n", encoding="utf-8")
        return {
            "workbook_id": row.workbook_id,
            "status": "recovery_pending",
            "queued": True,
            "error": "still pending",
        }

    monkeypatch.setattr(recovery, "process", mutate)
    with pytest.raises(worker_module.RecoveryError, match="must not rewrite"):
        recovery.run()
    ledger = json.loads(
        (config.state / "recovery-ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["expected_count"] == 2
    assert len(ledger["records"]) == 2
    assert ledger["records"][0]["status"] == "recovery_pending"
    integrity = json.loads(
        (
            config.state
            / "queue-integrity"
            / "original-queue-ledger.json"
        ).read_text()
    )
    assert integrity["expected_count"] == 2
    assert len(integrity["original_rows"]) == 2
    # Both rows ran (neither launched an attempt, so neither was retried);
    # the integrity evidence lands on the row that was current at exit.
    evidence = json.loads(
        (config.state / "task-evidence" / "0002.json").read_text()
    )
    assert evidence["queue_integrity_failure"] is True


def test_governor_holds_the_reservation_through_the_fairness_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, governor=True)
    row = rows[0]
    worker = RecoveryWorker(config)
    assert worker.budget is not None
    _stub_ready(worker, records, monkeypatch)
    candidate = replace(_candidate(tmp_path), source_sha256=row.run_source_sha256)
    held: dict[str, int] = {}

    def held_bytes() -> int:
        reservations = worker.budget.snapshot()["reservations"]
        return int(reservations.get(config.lane_id, {}).get("bytes", 0))

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        held["generation"] = held_bytes()
        return _attempt(tmp_path, 1, "recovered\nRECOVERY_USED: YES\n")

    def inspect(_row: QueueRow) -> ReleaseCandidate:
        held["inspect"] = held_bytes()
        return candidate

    def verify(*_args: object, **_kwargs: object) -> dict[str, object]:
        held["fairness"] = held_bytes()
        return _attempt(tmp_path, 90, PASS_REPORT("Independent."))

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(worker, "_inspect_release", inspect)
    monkeypatch.setattr(worker, "_run_verifier", verify)
    monkeypatch.setattr(
        worker_module, "_write_vm_diff", lambda *_args: config.state / "diff.txt"
    )

    result = worker.process(row)

    assert result["status"] == "generated"
    # The row must still be holding real budget while the verifier runs, or a
    # sibling lane would admit another heavy row on top of it.
    assert held["generation"] > 0
    assert held["inspect"] == config.memory_verifier_bytes
    assert held["fairness"] == config.memory_verifier_bytes
    assert worker.budget.snapshot()["used_bytes"] == 0


def test_governor_releases_the_reservation_when_a_row_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, governor=True)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)

    def run_generation(*_args: object, **_kwargs: object) -> dict[str, object]:
        assert worker.active_worktree is not None
        (worker.active_worktree / "run_grader.py").write_text(
            "tampered\n", encoding="utf-8"
        )
        return _attempt(tmp_path, 1, "RECOVERY_USED: YES\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    with pytest.raises(PolicyViolation):
        worker.process(rows[0])

    assert worker.budget.snapshot()["used_bytes"] == 0
    assert worker.budget.snapshot()["waiters"] == []


def test_a_sibling_lane_waits_while_a_heavy_row_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, governor=True)
    worker = RecoveryWorker(config)
    _stub_ready(worker, records, monkeypatch)
    budget = worker.budget
    assert budget is not None

    def run_generation(row: QueueRow, *_args: object, **_kwargs: object) -> dict[str, object]:
        # Stand in for the real stage, which resizes to the row's estimate
        # before launching the agent. The queue source here is not a real
        # workbook, so the estimate is the whole budget.
        worker._resize_lease(
            worker._generation_reservation(row), stage="generation"
        )
        assert budget.snapshot()["used_bytes"] == budget.total_bytes
        with pytest.raises(BudgetTimeout):
            budget.acquire("lane-b", 8 * 1024**3, timeout_seconds=0.1)
        return _attempt(tmp_path, 1, "RECOVERY_USED: NO\n")

    monkeypatch.setattr(worker, "_run_generation", run_generation)
    monkeypatch.setattr(
        worker,
        "_inspect_release",
        lambda _row: (_ for _ in ()).throw(worker_module.RecoveryError("no release")),
    )

    worker.process(rows[0])

    assert budget.snapshot()["used_bytes"] == 0


def test_an_unprobeable_source_reserves_the_whole_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The queue sources in these tests are not real workbooks, so the probe
    # fails and the row must reserve the whole budget rather than guess low.
    config, rows, records = _config(tmp_path, governor=True)
    worker = RecoveryWorker(config)

    assert worker._row_profile(rows[0]) is None
    assert worker._generation_reservation(rows[0]) == worker.budget.total_bytes
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text(encoding="utf-8")
    )
    assert evidence["memory_probe_failed"] is True


def test_a_probeable_source_reserves_less_than_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openpyxl

    config, rows, records = _config(tmp_path, governor=True)
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = 1
    workbook.active["B1"] = "=A1*2"
    workbook.save(rows[0].run_source_path)
    workbook.close()
    worker = RecoveryWorker(config)

    reservation = worker._generation_reservation(rows[0])

    assert 0 < reservation < worker.budget.total_bytes


def test_the_governor_degrades_to_ungoverned_when_disabled(
    tmp_path: Path,
) -> None:
    config, rows, _records = _config(tmp_path, governor=False)
    worker = RecoveryWorker(config)

    assert worker.budget is None
    assert worker.memory_scope_enabled is False
    assert worker._generation_reservation(rows[0]) == 0
    assert worker._generation_limit(0) is None


def test_the_generation_clock_covers_the_inner_budget_plus_a_margin(
    tmp_path: Path,
) -> None:
    config, rows, _records = _config(tmp_path)
    worker = RecoveryWorker(config)
    row = rows[0]

    # measured 7200 + 3600 margin beats the test config's 30 s clock.
    assert worker._generation_timeout(row) == 10_800
    large = dataclasses.replace(
        config,
        large_workbooks=frozenset({row.workbook_id}),
        timeout_seconds=worker_module.DEFAULT_TIMEOUT_SECONDS,
    )
    assert RecoveryWorker(large)._generation_timeout(row) == 18_000
    normal = dataclasses.replace(
        config, timeout_seconds=worker_module.DEFAULT_TIMEOUT_SECONDS
    )
    assert RecoveryWorker(normal)._generation_timeout(row) == 14_400
    assert worker_module.DEFAULT_FAIRNESS_TIMEOUT_SECONDS == 3600
    assert config.fairness_timeout_seconds == 3600


def test_the_generation_ceiling_is_a_host_share_not_a_tight_estimate(
    tmp_path: Path,
) -> None:
    # The first fleet run capped rows at 1.5x an estimate that was half of
    # reality, and every completed attempt spent hours throttled under
    # MemoryHigh. The ceiling is now the lane's share of the budget after the
    # unscoped allowance, so caps still fit the host together while a normal
    # row never touches its ceiling.
    config, _rows, _records = _config(tmp_path, governor=True, lane_count=3)
    worker = RecoveryWorker(config)
    total = worker.budget.total_bytes
    share = (total - config.memory_unscoped_allowance_bytes) // 3

    small = worker._generation_limit(8 * 1024**3)
    assert small == share
    assert 3 * small <= total - config.memory_unscoped_allowance_bytes

    # A reservation whose 1.5x headroom exceeds the share keeps the headroom,
    # capped at the budget.
    huge = worker._generation_limit(total)
    assert huge == total
    assert worker._generation_limit(int(share / 1.5) + 1024) > share


def test_a_quarantine_drops_to_the_io_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(tmp_path, governor=True)
    row = rows[0]
    worker = RecoveryWorker(config)
    worktree = create_isolated_worktree(config.baseline_repo, config.work_root, "0001")
    lease = worker.budget.acquire(config.lane_id, 80 * 1024**3, workbook_id="0001")
    worker._active_lease = lease

    worker._quarantine_worktree(row, worktree, "test reason")

    # Copying a worktree is gigabytes of dirty pages; it must not run while the
    # lane is still accounted for at its generation peak.
    assert lease.bytes == config.memory_io_bytes


def test_lane_count_reaches_the_agent_prompts(tmp_path: Path) -> None:
    config, rows, _records = _config(tmp_path, lane_count=1)
    worker = RecoveryWorker(config)
    worker.active_worktree = config.work_root / "task-0001"

    prompt = worker._generation_prompt(rows[0], rows[0].run_source_path, None, None, 0)

    assert "Preserve exactly 1 generation lanes" in prompt
    assert "exactly 2 generation lanes" not in prompt


def test_retry_prompt_tells_a_memory_killed_attempt_to_shrink_the_cone(
    tmp_path: Path,
) -> None:
    # With no throttle band the cap kills the child outright. The retry must
    # read that from the durable attempt record and say so, or the agent
    # reruns the same full-scope step into the same kill.
    config, rows, _records = _config(tmp_path, lane_count=1)
    worker = RecoveryWorker(config)
    worker.active_worktree = config.work_root / "task-0001"
    killed = {
        "attempt_path": str(tmp_path / "attempt-0001"),
        "status": "failed",
        "return_code": 137,
        "execution_limits": {
            "limit_reason": "cgroup_oom",
            "memory_max_bytes": 33 * 1024**3,
            "peak_rss_bytes": 32 * 1024**3,
            "memory_events": {"high": 0, "oom": 1, "oom_kill": 1},
        },
    }

    prompt = worker._generation_prompt(rows[0], rows[0].run_source_path, None, killed, 1)
    flat = " ".join(prompt.split())

    assert "killed by the memory cap (33.0 GiB" in flat
    assert "oom_kill=1" in flat
    assert "reached 32.0 GiB" in flat
    # Sheet restriction first; dropping outputs is the last resort and every
    # dropped line must carry the memory marker the gate checks.
    assert "--sheet" in flat
    assert "# excluded: memory" in flat
    assert "narrow the restriction cone" not in flat

    # oom_kill alone (no limit_reason) is enough; a clean failure says nothing.
    by_events = {"execution_limits": {"memory_events": {"oom_kill": 2}}}
    assert "oom_kill=2" in RecoveryWorker._memory_kill_text(by_events)
    clean = {"execution_limits": {"limit_reason": "", "memory_events": {"oom_kill": 0}}}
    assert RecoveryWorker._memory_kill_text(clean) == ""
    assert RecoveryWorker._memory_kill_text({"status": "failed"}) == ""


def test_lane_id_is_readable_and_stable() -> None:
    lane = worker_module._lane_id_from_path(Path("/srv/harbor/lane-b/state"))

    assert lane.startswith("state-")
    assert lane == worker_module._lane_id_from_path(Path("/srv/harbor/lane-b/state"))
    assert worker_module._lane_id_from_path(Path("/")).startswith("lane-")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("/home/hhu/harbor/b002/lane1/state", "/home/hhu/harbor/b003/lane1/state"),
        ("/home/hhu/recovery-b002/run/state", "/home/hhu/recovery-b003/run/state"),
        ("/srv/h/lane1/state/recovery", "/srv/h/lane2/state/recovery"),
        ("/srv/harbor/lane-a/state", "/srv/harbor/lane-b/state"),
    ],
)
def test_lane_ids_never_collide_for_distinct_state_roots(
    first: str, second: str
) -> None:
    # Lanes on one VM share a budget file. Two lanes deriving the same ID
    # would each exclude the other's reservation from its own accounting, see
    # a free budget, and admit together -- the exact event being prevented.
    # Real lane roots often differ only in a middle path segment.
    assert worker_module._lane_id_from_path(Path(first)) != (
        worker_module._lane_id_from_path(Path(second))
    )


def test_a_relative_budget_dir_is_absolutized(tmp_path: Path) -> None:
    # A relative dir would resolve against each lane's cwd, giving the two
    # lanes on a VM separate budget files that never see each other.
    config, _rows, _records = _config(tmp_path)
    env = {
        "RECOVERY_QUEUE": str(config.queue),
        "RECOVERY_BASELINE_REPO": str(config.baseline_repo),
        "RECOVERY_WORK_ROOT": str(config.work_root),
        "RECOVERY_STATE": str(config.state),
        "RECOVERY_OUTPUT": str(config.output),
        "RECOVERY_CODE_BASELINE": str(config.code_baseline),
        "RECOVERY_APPROVAL_BATCH_ID": "batch002",
        "RECOVERY_MEMORY_BUDGET_DIR": "harbor-memory",
    }

    resolved = Config.from_env(env).memory_budget_dir

    assert resolved.is_absolute()


def test_tree_hash_from_records_matches_a_fresh_walk(tmp_path: Path) -> None:
    # The release path validates a bundle's digests and then hashes the bundle.
    # Reusing the first result must give the identical hash, or the
    # optimization would change published identities.
    root = tmp_path / "bundle"
    (root / "nested").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    (root / "nested" / "b.json").write_text('{"b": 1}\n', encoding="utf-8")

    records = worker_module._tree_records(root)

    assert worker_module.tree_sha256_from_records(records) == (
        worker_module.tree_sha256(root)
    )


def test_manifest_validation_returns_records_that_hash_to_the_bundle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "task"
    root.mkdir()
    (root / "instruction.md").write_text("hello\n", encoding="utf-8")
    records = worker_module._tree_records(root)
    manifest = {
        "artifacts": {
            relative: {
                "algorithm": "sha256",
                "path": relative,
                "sha256": digest,
                "size_bytes": (root / relative).stat().st_size,
            }
            for relative, digest in records.items()
            if relative != "generation-manifest.json"
        }
    }

    returned = worker_module._validate_task_manifest_artifacts(root, manifest)

    assert returned == records
    assert worker_module.tree_sha256_from_records(returned) == (
        worker_module.tree_sha256(root)
    )


def test_manifest_validation_still_rejects_a_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "task"
    root.mkdir()
    (root / "instruction.md").write_text("hello\n", encoding="utf-8")

    with pytest.raises(worker_module.RecoveryError, match="do not match manifest"):
        worker_module._validate_task_manifest_artifacts(
            root, {"artifacts": {"instruction.md": {"sha256": "0" * 64}}}
        )


# -- disk floor and reaper ----------------------------------------------------

from scripts.batch import disk_reaper
from scripts.batch.disk_reaper import DiskReaper, ReapPolicy
from scripts.batch.memory_governor import MemoryBudget

GIB = 1024**3
HOUR = 3600


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _generation(worktree: Path, workbook: str, generation_id: str, *, age: float, size: int = 4096) -> Path:
    directory = worktree / "source_out" / workbook / "generations" / generation_id
    (directory / "ast").mkdir(parents=True)
    (directory / "generation-manifest.json").write_text(
        json.dumps({"generation_id": generation_id}), encoding="utf-8"
    )
    (directory / "ast" / "edges.csv").write_bytes(b"x" * size)
    receipts = worktree / "source_out" / workbook / "receipts" / generation_id
    receipts.mkdir(parents=True)
    (receipts / "receipt.json").write_text("{}", encoding="utf-8")
    _age(directory, age)
    return directory


def _staging(worktree: Path, root: str, workbook: str, name: str, *, age: float) -> Path:
    directory = worktree / root / workbook / name
    directory.mkdir(parents=True)
    (directory / "payload").write_bytes(b"y" * 2048)
    _age(directory, age)
    return directory


def _reaper(tmp_path: Path, work_root: Path, **overrides) -> DiskReaper:
    settings = {
        "work_roots": [work_root],
        "state_roots": [tmp_path / "state"],
        "policy": ReapPolicy(high_water_bytes=0, floor_bytes=0),
        "cwd_probe": lambda: [],
        "lock_dir": tmp_path / "locks",
    }
    settings.update(overrides)
    return DiskReaper(**settings)


def test_config_rejects_a_high_water_below_the_floor(tmp_path: Path) -> None:
    config, _rows, _records = _config(tmp_path)
    env = {
        "RECOVERY_QUEUE": str(config.queue),
        "RECOVERY_BASELINE_REPO": str(config.baseline_repo),
        "RECOVERY_WORK_ROOT": str(config.work_root),
        "RECOVERY_STATE": str(config.state),
        "RECOVERY_OUTPUT": str(config.output),
        "RECOVERY_CODE_BASELINE": str(config.code_baseline),
        "RECOVERY_APPROVAL_BATCH_ID": config.approval_batch_id,
        "RECOVERY_MIN_FREE_DISK_BYTES": str(100 * GIB),
        "RECOVERY_DISK_HIGH_WATER_BYTES": str(50 * GIB),
    }
    with pytest.raises(worker_module.RecoveryError, match="high-water"):
        Config.from_env(env)
    env["RECOVERY_MIN_FREE_DISK_BYTES"] = "-1"
    with pytest.raises(worker_module.RecoveryError, match="negative"):
        Config.from_env(env)
    env["RECOVERY_MIN_FREE_DISK_BYTES"] = "0"
    env["RECOVERY_DISK_HIGH_WATER_BYTES"] = "0"
    assert Config.from_env(env).min_free_disk_bytes == 0


def test_worker_refuses_to_start_below_the_disk_floor(tmp_path: Path) -> None:
    config, _rows, _records = _config(tmp_path, min_free_disk_bytes=2**60)

    with pytest.raises(worker_module.RecoveryError, match="below the"):
        RecoveryWorker(config)


def test_a_row_below_the_disk_floor_parks_as_disk_exhausted(tmp_path: Path) -> None:
    config, rows, _records = _config(tmp_path)
    worker = RecoveryWorker(config)
    worker.config = replace(config, min_free_disk_bytes=2**60)

    result = worker.process(rows[0])

    # Both keys are required: run() reads status and strips both by name.
    assert result["workbook_id"] == "0001"
    assert result["status"] == "disk_exhausted"
    assert result["queued"] is True
    assert not (config.work_root / "task-0001").exists()
    evidence = Path(str(result["evidence_path"]))
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8"))["disk_exhausted"] is True


def test_the_disk_floor_is_off_when_zero(tmp_path: Path) -> None:
    config, rows, _records = _config(tmp_path)
    worker = RecoveryWorker(config)

    assert worker._enforce_disk_floor(row=rows[0]) is None


def test_oom_self_test_reports_a_status_and_never_raises() -> None:
    result = worker_module.oom_score_adj_self_test()

    assert result["status"] in {"ok", "skipped"}
    if not Path("/proc/self/oom_score_adj").exists():
        assert result["status"] == "skipped"


def test_reaper_keeps_recent_and_newest_generations(tmp_path: Path) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    old = _generation(worktree, "0001", "a" * 64, age=30 * HOUR)
    older = _generation(worktree, "0001", "b" * 64, age=40 * HOUR)
    newest = _generation(worktree, "0001", "c" * 64, age=20 * HOUR)
    # 0002: the fresh one is not newest, so only the grace window keeps it.
    fresh_other = _generation(worktree, "0002", "d" * 64, age=10 * 60)
    newest_other = _generation(worktree, "0002", "f" * 64, age=10)
    stale_other = _generation(worktree, "0002", "e" * 64, age=50 * HOUR)

    report = _reaper(tmp_path, work).reap(apply=True, target_free_bytes=None)

    reaped = {item["path"] for item in report.reaped}
    assert reaped == {str(old), str(older), str(stale_other)}
    assert newest.is_dir() and fresh_other.is_dir() and newest_other.is_dir()
    assert not old.exists() and not older.exists()
    # Receipts go with their generation, and only theirs.
    receipts = worktree / "source_out" / "0001" / "receipts"
    assert not (receipts / ("a" * 64)).exists()
    assert (receipts / ("c" * 64)).is_dir()
    kept_by = {item["path"]: item["kept_by"] for item in report.kept}
    assert "newest" in kept_by[str(newest)]
    assert "grace" in kept_by[str(fresh_other)]
    assert not report.failed


def test_reaper_treats_a_source_named_downstream_as_live(tmp_path: Path) -> None:
    # Reaping cascades: a seg or release manifest that names a source
    # generation hard-requires it on validation, so it is live without any
    # current.json pointer.
    work = tmp_path / "work"
    worktree = work / "task-0001"
    named_by_seg = _generation(worktree, "0001", "1" * 64, age=30 * HOUR)
    named_by_release = _generation(worktree, "0001", "2" * 64, age=30 * HOUR)
    named_by_pointer = _generation(worktree, "0001", "3" * 64, age=30 * HOUR)
    named_by_audit = _generation(worktree, "0001", "4" * 64, age=30 * HOUR)
    orphan = _generation(worktree, "0001", "5" * 64, age=30 * HOUR)
    _generation(worktree, "0001", "6" * 64, age=1)  # newest, keeps itself
    seg = worktree / "seg_out" / "0001" / "generations" / ("f" * 64)
    seg.mkdir(parents=True)
    (seg / "generation-manifest.json").write_text(
        json.dumps({"bindings": {"source_generation": {"generation_id": "1" * 64}}}),
        encoding="utf-8",
    )
    release = worktree / "release_out" / "0001" / "releases" / ("e" * 64)
    release.mkdir(parents=True)
    (release / "release-manifest.json").write_text(
        json.dumps({"source_generation": {"generation_id": "2" * 64}}),
        encoding="utf-8",
    )
    (worktree / "source_out" / "0001" / "current.json").write_text(
        json.dumps({"generation_id": "3" * 64}), encoding="utf-8"
    )
    runs = worktree / "runs"
    runs.mkdir()
    (runs / "0001-prepared.json").write_text(
        json.dumps({"generation_id": "4" * 64}), encoding="utf-8"
    )
    (runs / "0001-variable-sources").mkdir()
    (runs / "0001-variable-sources" / "graph.json").write_text(
        json.dumps({"id": "5" * 64}), encoding="utf-8"
    )

    report = _reaper(tmp_path, work).reap(apply=True, target_free_bytes=None)

    assert [item["path"] for item in report.reaped] == [str(orphan)]
    for kept in (named_by_seg, named_by_release, named_by_pointer, named_by_audit):
        assert kept.is_dir()
    kept_by = {item["path"]: item["kept_by"] for item in report.kept}
    assert "segmentation" in kept_by[str(named_by_seg)]
    assert "release" in kept_by[str(named_by_release)]
    assert "current.json" in kept_by[str(named_by_pointer)]
    assert "audit" in kept_by[str(named_by_audit)]


def test_staging_sweep_spares_fresh_and_takes_old(tmp_path: Path) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    fresh = _staging(worktree, "source_out", "0001", ".source-build-abc123", age=60)
    old_build = _staging(worktree, "source_out", "0001", ".source-build-old", age=5 * HOUR)
    old_stage = _staging(worktree, "source_out", "0001", ".source-stage-old", age=5 * HOUR)
    old_seg = _staging(worktree, "seg_out", "0001", ".0001.seg-stage-old", age=5 * HOUR)
    old_task = _staging(worktree, "release_out", "0001", ".task-stage-old", age=5 * HOUR)
    old_legacy = _staging(worktree, "release_out", "0001", ".legacy-stage-old", age=5 * HOUR)
    old_snapshot = _staging(
        worktree, "source_out", "0001", ".original-source-snapshot-old", age=5 * HOUR
    )
    not_staging = _staging(worktree, "source_out", "0001", ".current-release.lock-dir", age=5 * HOUR)
    _generation(worktree, "0001", "a" * 64, age=5 * HOUR)

    report = _reaper(tmp_path, work).reap(
        apply=True, target_free_bytes=None, kinds=("staging",)
    )

    reaped = {item["path"] for item in report.reaped}
    assert reaped == {
        str(old_build), str(old_stage), str(old_seg),
        str(old_task), str(old_legacy), str(old_snapshot),
    }
    assert fresh.is_dir()
    assert not_staging.is_dir()
    assert (worktree / "source_out" / "0001" / "generations" / ("a" * 64)).is_dir()


def test_staging_sweep_with_zero_grace_still_respects_live_cwd(tmp_path: Path) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    live = _staging(worktree, "source_out", "0001", ".source-build-live", age=10)
    other = work / "task-0002"
    dead = _staging(other, "source_out", "0002", ".source-build-dead", age=10)

    report = _reaper(
        tmp_path, work, cwd_probe=lambda: [worktree / "source_out" / "0001"]
    ).reap(apply=True, target_free_bytes=None, kinds=("staging",), staging_grace_seconds=0)

    assert live.is_dir()
    assert not dead.exists()
    assert report.skipped_worktrees == [
        {"path": str(worktree), "reason": "ancestor of a live process cwd"}
    ]


def test_reaper_skips_a_worktree_held_by_a_live_reservation(tmp_path: Path) -> None:
    work = tmp_path / "work"
    held = _generation(work / "task-0001", "0001", "a" * 64, age=30 * HOUR)
    _generation(work / "task-0001", "0001", "b" * 64, age=1)
    free = _generation(work / "task-0002", "0002", "c" * 64, age=30 * HOUR)
    _generation(work / "task-0002", "0002", "d" * 64, age=1)
    budget = MemoryBudget(
        tmp_path / "budget",
        host_id="testvm",
        total_bytes=100 * GIB,
        reserve_bytes=10 * GIB,
        meminfo=tmp_path / "absent-meminfo",
        poll_seconds=0.01,
    )
    lease = budget.acquire("lane-b", GIB, workbook_id="0001")
    try:
        report = _reaper(tmp_path, work, budget=budget).reap(
            apply=True, target_free_bytes=None
        )
    finally:
        lease.release()

    assert held.is_dir()
    assert not free.exists()
    assert report.skipped_worktrees[0]["reason"].startswith("held by a live")


def test_reaper_never_leaves_its_work_root(tmp_path: Path) -> None:
    mine = tmp_path / "run" / "work-a"
    theirs = tmp_path / "run" / "work-b"
    mine_old = _staging(mine / "task-0001", "source_out", "0001", ".source-build-x", age=9 * HOUR)
    theirs_old = _staging(theirs / "task-0002", "source_out", "0002", ".source-build-y", age=9 * HOUR)
    theirs_gen = _generation(theirs / "task-0002", "0002", "a" * 64, age=90 * HOUR)
    _generation(theirs / "task-0002", "0002", "b" * 64, age=1)

    _reaper(tmp_path, mine).reap(apply=True, target_free_bytes=None)

    assert not mine_old.exists()
    assert theirs_old.is_dir()
    assert theirs_gen.is_dir()


def test_reaper_deletes_whole_generations_and_reports_failures(tmp_path: Path) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    doomed = _generation(worktree, "0001", "a" * 64, age=30 * HOUR)
    _generation(worktree, "0001", "b" * 64, age=1)
    reaper = _reaper(tmp_path, work)

    failures: list[dict[str, object]] = []
    original_rmtree = shutil.rmtree

    def failing_rmtree(path, onexc=None, **kwargs):
        onexc(os.rmdir, str(path), OSError("simulated EBUSY"))

    shutil.rmtree = failing_rmtree
    try:
        assert reaper._rmtree(doomed, failures) is False
    finally:
        shutil.rmtree = original_rmtree

    assert failures[0]["path"] == str(doomed)
    assert "EBUSY" in failures[0]["errors"][0]
    assert failures[0]["remaining"] is True
    # A real reap takes the directory whole, manifest included, never a bare ast/.
    report = reaper.reap(apply=True, target_free_bytes=None)
    assert [item["path"] for item in report.reaped] == [str(doomed)]
    assert not doomed.exists()


def test_evidence_units_are_pruned_whole_and_staging_copies_are_spared(tmp_path: Path) -> None:
    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    version = state / "failed-releases" / "0001" / "versions" / "100"
    version.mkdir(parents=True)
    (version / "snapshot.json").write_text("{}", encoding="utf-8")
    _age(version, 10 * HOUR)
    (state / "failed-releases" / "0001" / "current.json").write_text(
        json.dumps({"snapshot_path": str(version)}), encoding="utf-8"
    )
    in_progress = state / "failed-releases" / "0001" / "versions" / ".101.snapshot"
    in_progress.mkdir()
    (in_progress / "partial").write_bytes(b"z")
    _age(in_progress, 10 * HOUR)
    quarantine = state / "worktree-quarantine" / "0002" / "200"
    quarantine.mkdir(parents=True)
    (quarantine / "quarantine.json").write_text("{}", encoding="utf-8")
    _age(quarantine, 5 * HOUR)
    candidate = state / "candidates" / "0003" / "key"
    (candidate / "release").mkdir(parents=True)
    _age(candidate, 3 * HOUR)
    delivery_q = state / "delivery-quarantine" / "0004"
    delivery_q.mkdir(parents=True)
    (delivery_q / "bundle").write_bytes(b"q")
    _age(delivery_q, 1 * HOUR)

    report = _reaper(tmp_path, work).reap(apply=True, target_free_bytes=None)

    # Oldest first, whole units, never the dot-prefixed copy in progress.
    assert [item["path"] for item in report.reaped] == [
        str(version), str(quarantine), str(candidate), str(delivery_q)
    ]
    assert in_progress.is_dir()
    assert not (state / "failed-releases" / "0001" / "current.json").exists()
    assert not (state / "failed-releases" / "0001" / "versions" / "100").exists()
    assert (state / "candidates" / "0003").is_dir()


def test_tier_two_is_inert_above_the_high_water_mark(tmp_path: Path) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    old = _generation(worktree, "0001", "a" * 64, age=30 * HOUR)
    _generation(worktree, "0001", "b" * 64, age=1)

    # Free space is certainly above zero, so nothing should be touched.
    report = _reaper(tmp_path, work).reap(apply=True, target_free_bytes=1)

    assert report.reaped == []
    assert old.is_dir()


def test_cli_default_is_a_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work = tmp_path / "work"
    worktree = work / "task-0001"
    old = _generation(worktree, "0001", "a" * 64, age=30 * HOUR)
    _generation(worktree, "0001", "b" * 64, age=1)
    args = [
        "--work-root", str(work),
        "--state-root", str(tmp_path / "state"),
        "--no-governor",
        "--floor-bytes", "0",
        "--high-water-bytes", "0",
    ]

    assert disk_reaper.main([*args, "reap", "--all"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True
    assert [item["path"] for item in dry["reaped"]] == [str(old)]
    assert old.is_dir()

    assert disk_reaper.main([*args, "plan", "--all"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["kept"][0]["kept_by"].startswith("newest")

    assert disk_reaper.main([*args, "report"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["worktrees"][0]["source_generations"] == 2

    assert disk_reaper.main([*args, "reap", "--all", "--apply"]) == 0
    assert not old.exists()


def test_worker_sweeps_staging_when_a_row_parks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, rows, _records = _config(tmp_path)
    worker = RecoveryWorker(config)
    worktree = config.work_root / "task-0001"
    dead = _staging(worktree, "source_out", "0001", ".source-build-dead", age=10)
    (worktree / ".recovery-worktree").write_text("copy\n", encoding="utf-8")
    monkeypatch.setattr(
        worker, "_process_in_worktree",
        lambda _row, _worktree: {"workbook_id": "0001", "status": "recovery_pending"},
    )
    monkeypatch.setattr(worker_module, "_preserve_failed_release", lambda *a, **k: None)

    result = worker._process_row(rows[0], worktree)

    assert result["status"] == "recovery_pending"
    assert not dead.exists()
    log = (config.state / "disk-reap.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["stage"] == "row_closed"


def test_tree_size_charges_hardlinked_files_once_per_name(tmp_path):
    """Publish hardlinks build files into the generation; size is shared."""
    tree = tmp_path / "tree"
    tree.mkdir()
    original = tree / "a.bin"
    original.write_bytes(b"x" * 4096)
    assert disk_reaper.tree_size_bytes(tree) == 4096
    os.link(original, tree / "b.bin")
    # Two names, one inode: the tree still holds 4096 bytes on disk.
    assert disk_reaper.tree_size_bytes(tree) == 4096
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.link(original, other / "c.bin")
    # A third name outside the tree means removing the tree frees only 2/3.
    assert disk_reaper.tree_size_bytes(tree) == 2 * (4096 // 3)


def test_three_lanes_are_admitted_and_four_are_not(tmp_path: Path) -> None:
    config, _rows, _records = _config(tmp_path)
    env = {
        "RECOVERY_QUEUE": str(config.queue),
        "RECOVERY_BASELINE_REPO": str(config.baseline_repo),
        "RECOVERY_WORK_ROOT": str(config.work_root),
        "RECOVERY_STATE": str(config.state),
        "RECOVERY_OUTPUT": str(config.output),
        "RECOVERY_CODE_BASELINE": str(config.code_baseline),
        "RECOVERY_APPROVAL_BATCH_ID": config.approval_batch_id,
        "RECOVERY_LANE_COUNT": "4",
    }
    assert worker_module.GENERATION_LANE_COUNT == 4
    assert Config.from_env(env).lane_count == 4
    env["RECOVERY_LANE_COUNT"] = "5"
    with pytest.raises(worker_module.RecoveryError, match="between 1 and 4"):
        Config.from_env(env)
