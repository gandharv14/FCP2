"""Recurrence tests for the 2026-09-03 a.md paperwork-gate fix."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.batch import recovery_worker as worker_module


def _task_dir(tmp_path: Path, amd: str) -> Path:
    task = tmp_path / "task"
    task.mkdir()
    (task / "a.md").write_text(amd, encoding="utf-8")
    return task


FULL = (
    "# Recovery\n\nBlocker: x. Evidence: y. Stage: segmentation. Fix: z.\n"
    "Changed files: xl_seg/emit.py\nBefore: a. After: b.\n"
    "Commands: python -m xl_seg.emit --workbook 0001\nVerifier: PASS\n" + "x" * 200
)


def test_worker_build_id_is_a_hash_over_the_loaded_batch_modules(tmp_path: Path) -> None:
    import hashlib
    import re

    from scripts.batch import disk_reaper, hardened_runner, memory_governor

    assert re.fullmatch(r"[0-9a-f]{64}", worker_module.WORKER_OVERLAY_ID)
    expected = hashlib.sha256()
    for module in (worker_module, hardened_runner, memory_governor, disk_reaper):
        expected.update(Path(module.__file__).read_bytes())
    assert worker_module.WORKER_OVERLAY_ID == expected.hexdigest()
    # A fix in a sibling module changes the id: a lane overlay that patches
    # hardened_runner releases a row parked by the previous build.
    changed = hashlib.sha256()
    for module in (worker_module, hardened_runner, memory_governor, disk_reaper):
        data = Path(module.__file__).read_bytes()
        if module is hardened_runner:
            data += b"\n# overlay fix\n"
        changed.update(data)
    assert changed.hexdigest() != worker_module.WORKER_OVERLAY_ID


def test_amd_gate_reports_keyword_and_path_defects_together(tmp_path: Path) -> None:
    # Missing the word "stage" AND two changed paths not named: the stock
    # worker raised only "missing: stage" and the single retry never learned
    # about the paths. The overlay reports all of it in one error.
    amd = FULL.replace("Stage: segmentation.", "Step: segmentation.")
    task = _task_dir(tmp_path, amd)
    evidence = {
        "baseline_diff": {"seg_out/0001/curation.toml": {"baseline": "a", "current": "b"}},
        "changed_during_run": ["tasks_outputs_mcp/0001-outputs/instruction.md"],
    }
    with pytest.raises(worker_module.RecoveryError) as raised:
        worker_module.validate_recovery_amd(task, evidence)
    message = str(raised.value)
    assert "missing: stage" in message
    assert "seg_out/0001/curation.toml" in message
    assert "tasks_outputs_mcp/0001-outputs/instruction.md" in message


def test_amd_gate_still_passes_a_complete_record(tmp_path: Path) -> None:
    task = _task_dir(tmp_path, FULL)
    worker_module.validate_recovery_amd(
        task, {"baseline_diff": {"xl_seg/emit.py": {}}, "changed_during_run": ["xl_seg/emit.py"]}
    )


def test_evidence_changed_paths_merges_and_sorts() -> None:
    paths = worker_module._evidence_changed_paths(
        {"baseline_diff": {"b.py": {}}, "changed_during_run": ["a.py", "b.py", ""]}
    )
    assert paths == ["a.py", "b.py"]
    assert worker_module._evidence_changed_paths(None) == []


def test_same_worker_defect_requires_matching_overlay() -> None:
    # A row parked worker_fix_needed by the stock worker carries no overlay id,
    # so it must not be treated as the same defect under this overlay.
    src = Path(worker_module.__file__).read_text(encoding="utf-8")
    assert 'task.get("worker_fix_overlay") == self.worker_overlay_id' in src
    assert '"worker_fix_overlay": self.worker_overlay_id' in src


def test_seed_amd_retry_reads_delivery_error(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "task-evidence").mkdir(parents=True)
    (state / "checkpoints").mkdir()
    row_error = (
        "recovered task a.md is not a complete recovery record; changed files not "
        "named in a.md: seg_out/0569/curation.toml, tasks_outputs_mcp/0569-outputs/instruction.md"
    )
    (state / "task-evidence" / "0569.json").write_text(
        json.dumps({"workbook_id": "0569", "delivery_error": row_error}), encoding="utf-8"
    )

    class FakeWorker:
        config = type("C", (), {"state": state})()
        active_worktree = tmp_path / "wt"
        saved: dict[str, object] = {}

        def _save_checkpoint(self, row, worktree, **fields):
            self.saved.update(fields)

    row = type("R", (), {"workbook_id": "0569"})()
    seeded = worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, {}, 2)
    assert seeded is not None
    assert seeded["gate"] == "a.md recovery record"
    assert seeded["changed_paths"] == [
        "seg_out/0569/curation.toml",
        "tasks_outputs_mcp/0569-outputs/instruction.md",
    ]
    assert FakeWorker.saved.get("amd_retry_seeded") is True
    # Already seeded once: never again for this row.
    assert worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, {"amd_retry_seeded": True}, 3) is None
    # No a.md delivery error: nothing to seed.
    (state / "task-evidence" / "0569.json").write_text(json.dumps({"delivery_error": "fairness"}), encoding="utf-8")
    assert worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, {}, 2) is None


def test_bullet_and_inline_code_commands_are_held_to_no_abbreviation(tmp_path: Path) -> None:
    amd = FULL.replace(
        "Commands: python -m xl_seg.emit --workbook 0001",
        "Commands:\n- `python3 xl_seg/... --workbook 0001`\n2. `PATH=x python3 build_one.sh 0001`\n- prose that is not a command",
    )
    task = _task_dir(tmp_path, amd)
    with pytest.raises(worker_module.RecoveryError, match="abbreviated command"):
        worker_module.validate_recovery_amd(task, None)
    lines = worker_module._amd_command_lines(amd)
    assert "python3 xl_seg/... --workbook 0001" in lines
    assert "PATH=x python3 build_one.sh 0001" in lines
    assert not any("prose" in l for l in lines)


def test_seed_amd_retry_from_fairness_report(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "task-evidence").mkdir(parents=True)
    # On resume the evidence error is already the inventory note; the fairness
    # verdict must come from the durable checkpoint.
    (state / "task-evidence" / "0360.json").write_text(json.dumps({
        "error": "workbook 0360 is absent from the pinned approved inventory",
    }), encoding="utf-8")
    report = "... `a.md` is not an exact recovery record. Command lines contain `...`"
    checkpoint = {"fairness_result": {"passed": False, "report": report}}

    class FakeWorker:
        config = type("C", (), {"state": state})()
        active_worktree = tmp_path / "wt"
        def _save_checkpoint(self, row, worktree, **fields): pass

    row = type("R", (), {"workbook_id": "0360"})()
    seeded = worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, checkpoint, 2)
    assert seeded is not None and "fairness verifier" in seeded["gate"]
    assert "Command completeness" in seeded["error"]
    # Fairness failure whose report passes the recovery record: no seed.
    semantic = {"fairness_result": {"passed": False, "report": "Recovery record: PASS. `a.md` is fine.\nSolvability FAIL: units"}}
    assert worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, semantic, 2) is None
    # A passing fairness result never seeds.
    assert worker_module.RecoveryWorker._seed_amd_retry(FakeWorker(), row, {"fairness_result": {"passed": True, "report": "a.md FAIL"}}, 2) is None


def test_read_valid_delivery_does_not_rejudge_amd() -> None:
    import inspect
    src = inspect.getsource(worker_module._read_valid_delivery)
    assert "validate_recovery_amd(bundle)" not in src


def test_transcript_keeps_declaration_on_its_own_line(tmp_path: Path) -> None:
    """0500 attempt 8: 'RECOVERY_USED: YESAll checks completed' in final.txt."""
    att = tmp_path / "attempt"
    att.mkdir()
    (att / "final.txt").write_text(
        "done\nRECOVERY_USED: YESAll checks completed successfully.\n", encoding="utf-8"
    )
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done\nRECOVERY_USED: YES"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "All checks completed successfully."}]}},
        {"type": "result", "result": "done\nRECOVERY_USED: YESAll checks completed successfully."},
    ]
    (att / "stream.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )

    class W:
        _attempt_segments = worker_module.RecoveryWorker._attempt_segments
        _attempt_final = worker_module.RecoveryWorker._attempt_final

    text = worker_module.RecoveryWorker._attempt_transcript(W(), {"attempt_path": str(att)})
    assert worker_module.REPAIR_LINE.findall(text) == ["YES"]
    # v12: the glue-tolerant REPAIR_LINE also finds it in the flat final.txt
    assert worker_module.REPAIR_LINE.findall((att / "final.txt").read_text()) == ["YES"]
    (att / "stream.jsonl").unlink()
    assert "YESAll" in worker_module.RecoveryWorker._attempt_transcript(W(), {"attempt_path": str(att)})


def test_missing_declaration_is_a_paperwork_defect(tmp_path: Path) -> None:
    task = _task_dir(tmp_path, FULL)
    with pytest.raises(worker_module.RecoveryError, match="RECOVERY_USED: YES"):
        worker_module.validate_recovery_amd(
            task, {"agent_declaration": "MISSING", "baseline_diff": {}, "changed_during_run": []}
        )
    worker_module.validate_recovery_amd(
        task, {"agent_declaration": "YES", "baseline_diff": {}, "changed_during_run": []}
    )
    worker_module.validate_recovery_amd(task, {"baseline_diff": {}, "changed_during_run": []})


def test_resume_judges_last_attempt_transcript() -> None:
    """A resumed row must not be judged on an empty final response."""
    import inspect

    src = inspect.getsource(worker_module.RecoveryWorker._process_in_worktree)
    assert 'final_text = self._attempt_transcript(last_result) if last_result else ""' in src


def test_glued_declaration_in_one_text_block_is_found() -> None:
    """0673 attempt 4: the CLI glued the next message into the same text block."""
    glued = "- Source hash preserved: `09bf`\n\nRECOVERY_USED: YESBoth final background checks passed.\n"
    assert worker_module.REPAIR_LINE.findall(glued) == ["YES"]
    assert worker_module.REPAIR_LINE.findall("done.\nRECOVERY_USED: NO\n") == ["NO"]
    assert worker_module.REPAIR_LINE.findall("checks passed.RECOVERY_USED: YES\n") == ["YES"]
    assert worker_module.REPAIR_LINE.findall("RECOVERY_USED: YES, mostly\n") == []
    assert worker_module.REPAIR_LINE.findall("RECOVERY_USED: YES but see notes\n") == []
    assert worker_module.REPAIR_LINE.findall("see RECOVERY_USED: YES in a.md\n") == []
