"""report_lane_state.py (VM side) and report.py structured sections (local side)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".cursor" / "skills" / "run-fcp-workbook-fleet" / "scripts" / "report.py"
COLLECTOR = ROOT / "scripts" / "batch" / "report_lane_state.py"


def _load_report_module():
    spec = importlib.util.spec_from_file_location("fleet_report", REPORT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_run(tmp_path: Path) -> Path:
    run = tmp_path / "fcp-run-t1-vm1"
    for lane in ("a", "b"):
        (run / f"queue-{lane}.json").parent.mkdir(parents=True, exist_ok=True)
        (run / f"queue-{lane}.json").write_text("[]")
        state = run / f"state-{lane}"
        (state / "failures").mkdir(parents=True)
    (run / "state-a" / "failures" / "0001.json").write_text(
        json.dumps({"workbook_id": "0001", "class": "inspect", "subclass": "release_inspect", "gate": 16,
                    "signature": "aaaa" * 16, "status": "recovery_pending", "lifetime_attempts": 4,
                    "occurrence_count": 3, "instruction_mode": "gdpval_prose", "fixability": "fixable",
                    "error": "RecoveryError: current immutable release does not bind"})
    )
    (run / "state-a" / "failures" / "0002.json").write_text(
        json.dumps({"workbook_id": "0002", "class": "retry_budget", "subclass": "row_attempt_cap",
                    "signature": "bbbb" * 16, "status": "recovery_pending", "lifetime_attempts": 18,
                    "occurrence_count": 1, "instruction_mode": "naturalize", "fixability": "exhausted"})
    )
    (run / "state-b" / "failures" / "0003.json").write_text(
        json.dumps({"workbook_id": "0003", "class": "controller", "subclass": "unexpected_exception",
                    "signature": "cccc" * 16, "status": "worker_fix_needed", "lifetime_attempts": 2,
                    "occurrence_count": 1, "instruction_mode": "naturalize", "error": "TypeError: x"})
    )
    (run / "state-a" / "lane-supervisor.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [
            {"at": "t", "cycle": 1, "event": "worker_start", "overlay": False},
            {"at": "t", "cycle": 1, "event": "worker_exit", "rc": 3},
            {"at": "t", "cycle": 1, "event": "fix_start"},
            {"at": "t", "cycle": 1, "event": "fix_exit", "rc": 0},
            {"at": "t", "cycle": 2, "event": "worker_start", "overlay": True},
            {"at": "t", "cycle": 2, "event": "worker_exit", "rc": 1},
            {"at": "t", "cycle": 2, "event": "idle", "streak": 1, "sleep": 300},
        ]) + "\n"
    )
    (run / "state-a" / "overlay").mkdir()
    (run / "state-a" / "overlay" / "OVERLAY.json").write_text(
        json.dumps({"fix_id": "20260906T000000Z-abcd", "installed_at": "t", "changed_paths": ["scripts/batch/recovery_worker.py"],
                    "summary": "fixed None candidate", "workbooks": ["0003"]})
    )
    (run / "state-b" / "lane-fix").mkdir()
    (run / "state-b" / "lane-fix" / "ALERT.json").write_text(
        json.dumps({"kind": "lane_fix_cap", "message": "2 lane fixes did not stick", "workbooks": ["0003"]})
    )
    return run


def test_collector_emits_structured_lines(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    result = subprocess.run([sys.executable, str(COLLECTOR), str(run)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    kinds = [line.split(" ", 2)[0] for line in lines]
    assert kinds == ["FAILURES", "SUPERVISOR", "LANEFIX"] * 2
    failures_a = json.loads(lines[0].split(" ", 2)[2])
    assert [f["workbook_id"] for f in failures_a] == ["0001", "0002"]
    assert failures_a[0]["signature"] == "aaaa" * 4  # truncated to 16
    supervisor_a = json.loads(lines[1].split(" ", 2)[2])
    assert supervisor_a["cycles"] == 2 and supervisor_a["last_rc"] == 1
    assert supervisor_a["fixes"] == 1 and supervisor_a["fix_ok"] == 1
    assert supervisor_a["overlay_active"] is True and supervisor_a["idle_streak"] == 1
    lanefix_a = json.loads(lines[2].split(" ", 2)[2])
    assert lanefix_a["overlay"]["changed_paths"] == ["scripts/batch/recovery_worker.py"]
    lanefix_b = json.loads(lines[5].split(" ", 2)[2])
    assert lanefix_b["alert"]["kind"] == "lane_fix_cap" and lanefix_b["overlay"] is None
    # Empty run: no lines, exit 0.
    empty = subprocess.run([sys.executable, str(COLLECTOR), str(tmp_path / "nowhere")], capture_output=True, text=True, check=False)
    assert empty.returncode == 0 and empty.stdout == ""


def test_report_renders_classes_systemic_and_lane_fixes(tmp_path: Path) -> None:
    report = _load_report_module()
    run = _make_run(tmp_path)
    vm1 = subprocess.run([sys.executable, str(COLLECTOR), str(run)], capture_output=True, text=True, check=False).stdout
    # A second VM with the same inspect signature in prose mode and an
    # unrelated naturalize failure with the same subclass: only the first is
    # systemic, and the modes are never merged.
    vm2 = "\n".join([
        "FAILURES a " + json.dumps([
            {"workbook_id": "0101", "class": "inspect", "subclass": "release_inspect", "gate": 16, "signature": "aaaa" * 4,
             "instruction_mode": "gdpval_prose", "occurrence_count": 2, "error": "RecoveryError: current immutable release does not bind",
             "distinct_classes": ["fairness_semantic", "inspect", "recovery_record"]},
            {"workbook_id": "0102", "class": "inspect", "subclass": "release_inspect", "gate": 16, "signature": "aaaa" * 4,
             "instruction_mode": "naturalize", "occurrence_count": 1, "error": "same text, other mode"},
        ]),
        "SUPERVISOR a " + json.dumps({"cycles": 5, "last_event": "host_floor", "last_rc": 2, "fixes": 0, "fix_ok": 0,
                                      "idle_streak": 0, "host_floor_events": 1, "crashes": 0, "stopped": False, "overlay_active": False}),
        "LANEFIX a " + json.dumps({"overlay": None, "alert": None, "attempts": {}, "history_count": 0}),
        "garbage line that is not structured",
        "FAILURES b not-json",
    ])
    structured = report.collect_structured({"vm1": vm1, "vm2": vm2})
    assert len(structured["failures"]) == 5
    groups = report.systemic_groups(structured["failures"])
    systemic = [g for g in groups if g["systemic"]]
    assert len(systemic) == 1
    assert (systemic[0]["mode"], systemic[0]["class"], sorted(systemic[0]["vms"])) == ("gdpval_prose", "inspect", ["vm1", "vm2"])
    assert systemic[0]["occurrences"] == 5
    lines, notes = report.render_structured(structured)
    text = "\n".join(lines)
    assert "## Failure classes" in text and "| inspect | 3 | 1 | 2 |" in text
    assert "## Systemic" in text and "| gdpval_prose | inspect | release_inspect | 16 | 2 | 2 | 5 |" in text
    assert "## Lane supervisors" in text and "| vm1-a | 2 | idle | 1 | yes | 1/1 | 1 |" in text
    assert "## Lane fixes" in text and "20260906T000000Z-abcd" in text and "fixed None candidate" in text
    assert any(n.startswith("systemic gdpval_prose/inspect/release_inspect on 2 VM(s)") and "promotion needs your explicit request" in n for n in notes)
    assert any("vm2-a:0101 has cycled through 3 failure classes" in n for n in notes)
    assert any("retry cap reached" in n and "vm1-a:0002" in n for n in notes)
    assert any("controller-class failures" in n and "vm1-b:0003" in n for n in notes)
    assert any("lane fix lane_fix_cap" in n for n in notes)
    assert any("vm2-a: last worker exit was a host floor" in n for n in notes)
    # Nothing in the structured sections is phrased as a stop.
    assert "halt" not in text.lower() and "blocked" not in text.lower().replace("nothing is blocked", "")


def test_collector_skips_resolved_records_and_emits_distinct_classes(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    (run / "state-a" / "failures" / "0001.json").write_text(
        json.dumps({"workbook_id": "0001", "class": "inspect", "status": "generated", "resolved_at": "t", "signature": "x"})
    )
    (run / "state-a" / "failures" / "0002.json").write_text(
        json.dumps({"workbook_id": "0002", "class": "retry_budget", "signature": "y",
                    "history": [{"class": "inspect"}, {"class": "fairness_leak"}, {"class": "inspect"}]})
    )
    result = subprocess.run([sys.executable, str(COLLECTOR), str(run)], capture_output=True, text=True, check=False)
    failures_a = json.loads(result.stdout.splitlines()[0].split(" ", 2)[2])
    assert [f["workbook_id"] for f in failures_a] == ["0002"]
    assert failures_a[0]["distinct_classes"] == ["fairness_leak", "inspect", "retry_budget"]


def test_report_renders_empty_state(tmp_path: Path) -> None:
    report = _load_report_module()
    lines, notes = report.render_structured(report.collect_structured({"vm1": "HOST x\nLANE a done=0/1 counts={}\n"}))
    text = "\n".join(lines)
    assert notes == []
    assert "## Failure classes" in text and "\nnone" in text
    assert "no overlays installed" in text
