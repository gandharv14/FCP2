from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.batch import recovery_worker as worker_module
from scripts.batch.hardened_runner import ReadyRecord
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
        task_limit=0,
        agent_binary=agent,
        inventory_registry=approval_registry,
        approval_batch_id="batch-002-restricted-native-19-v3",
        inventory=inventory,
        large_workbooks=frozenset(),
        measured_timeout_seconds=7_200,
        large_timeout_seconds=14_400,
    )
    return config, rows, records


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

    assert statuses["0001"] in {"recovery_pending", "task_fix_needed"}
    assert statuses["0001"] != "generated"
    assert statuses["0002"] == "recovery_pending"
    assert attempts["0001"] == 2
    assert attempts["0002"] == 0
    assert json.loads(config.queue.read_text())[1]["workbook_id"] == "0002"
    summary = json.loads((config.state / "summary.json").read_text())
    assert summary["tasks"]["0001"]["status"] != "generated"
    assert not (config.output / "batch-001" / "0001-outputs").exists()


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
            tmp_path, 90, "Independent review.\nFAIRNESS_VERDICT: PASS\n"
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

    assert processed[0] == "0001"
    assert "0002" not in processed
    assert processed.count("0001") == 1
    assert by_id["0001"]["status"] == "task_fix_needed"
    assert by_id["0001"]["policy_violation"] is True
    assert "source drift" in str(by_id["0001"]["error"])
    assert by_id["0002"]["status"] == "recovery_pending"
    assert Path(str(by_id["0001"]["evidence_path"])).is_file()
    assert json.loads(config.queue.read_text())[1]["workbook_id"] == "0002"


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
            tmp_path, 90, "Hidden assumption.\nFAIRNESS_VERDICT: FAIL\n"
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
            verification_report="ok\nFAIRNESS_VERDICT: PASS\n",
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
            tmp_path, 90, "Independent review.\nFAIRNESS_VERDICT: PASS\n"
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
    summary = json.loads((config.state / "summary.json").read_text(encoding="utf-8"))

    assert by_id["0002"]["status"] == "recovery_pending"
    assert by_id["0001"]["status"] != "generated"
    assert "does not bind" in str(by_id["0001"]["inspect_error"])
    assert "does not bind" in str(evidence["inspect_error"])
    assert "does not bind" in str(summary["tasks"]["0001"]["inspect_error"])
    current = json.loads(
        (config.state / "failed-releases" / "0001" / "current.json").read_text()
    )
    assert (Path(current["snapshot_path"]) / "current-release.json").is_file()
    leftover = list((config.work_root).glob("task-*"))
    assert leftover == [config.work_root / "task-0001"]


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
    assert by_id["0002"]["status"] == "recovery_pending"
    assert by_id["0001"]["queued"] is True
    assert by_id["0001"]["unexpected"] is True
    assert "unexpected worker bug" in evidence["error"]
    assert evidence["unexpected"] is True


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

    def run_verifier(*_args: object, **_kwargs: object) -> dict[str, object]:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "Independent review.\nFAIRNESS_VERDICT: PASS\n", encoding="utf-8"
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
    assert result["status"] == "fairness_retry"


def test_fairness_stdout_fail_wins_over_file_pass() -> None:
    merged = _combine_fairness_reports(
        "notes\nFAIRNESS_VERDICT: FAIL\n",
        "Independent review.\nFAIRNESS_VERDICT: PASS\n",
    )
    assert merged.endswith("FAIRNESS_VERDICT: FAIL\n")
    assert not worker_module.fairness_passed(merged)


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
            tmp_path, 90, "Hidden assumption.\nFAIRNESS_VERDICT: FAIL\n"
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
        return _attempt(tmp_path, 99, "FAIRNESS_VERDICT: FAIL\n")

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
            "INCIDENT_VERDICT: WORKER_FIX_NEEDED\n",
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

    assert processed == []
    assert ledger["records"][0]["status"] == "worker_fix_needed"
    assert ledger["records"][1]["status"] == "recovery_pending"


def test_full_ledger_preserves_unprocessed_detailed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, rows, records = _config(
        tmp_path, workbook_ids=("0001", "0002"), attempt_count=1
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
            tmp_path, 90, "FAIRNESS_VERDICT: PASS\n"
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
            "fresh review\nFAIRNESS_VERDICT: PASS\n", encoding="utf-8"
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
    report = "review\nFAIRNESS_VERDICT: PASS\n"
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
    evidence = json.loads(
        (config.state / "task-evidence" / "0001.json").read_text()
    )
    assert evidence["queue_integrity_failure"] is True
