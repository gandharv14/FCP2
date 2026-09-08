"""Lane supervisor (scripts/batch/run_lane.sh) and lane fix step (lane_fix.py).

Both are exercised with fakes: a scripted stand-in for the worker module that
exits with a chosen code per cycle, and a scripted stand-in for the Cursor
agent that edits the fix workspace. No model is called and no real workbook
is built; what is under test is the control flow around the worker.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_LANE = ROOT / "scripts" / "batch" / "run_lane.sh"

FAKE_WORKER = '''
import json, os, sys
from pathlib import Path
state = Path(os.environ["RECOVERY_STATE"])
plan = state / "plan.json"
codes = json.loads(plan.read_text()) if plan.is_file() else [0]
marker = Path(__file__).resolve().parent / "OVERLAY_MARKER"
(state / "cycles.log").open("a").write(
    json.dumps({"overlay": marker.exists(), "pythonpath": os.environ.get("PYTHONPATH", "")}) + "\\n"
)
code = codes.pop(0) if codes else 0
plan.write_text(json.dumps(codes))
ledger = {"records": [{"workbook_id": "0001", "status": "generated" if code == 0 else "recovery_pending", "error": os.environ.get("FAKE_LEDGER_ERROR", "x")}]}
if os.environ.get("FAKE_LEDGER_VARY") == "1":
    ledger["records"][0]["error"] = f"err-{len(codes)}"
(state / "recovery-ledger.json").write_text(json.dumps(ledger))
sys.exit(code)
'''


def _make_baseline(tmp_path: Path) -> Path:
    base = tmp_path / "baseline"
    (base / "scripts" / "batch").mkdir(parents=True)
    (base / "scripts" / "__init__.py").write_text("")
    (base / "scripts" / "batch" / "__init__.py").write_text("")
    (base / "scripts" / "batch" / "recovery_worker.py").write_text(FAKE_WORKER)
    shutil.copy2(RUN_LANE, base / "scripts" / "batch" / "run_lane.sh")
    return base


def _run_lane(base: Path, state: Path, env_extra: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RECOVERY_STATE": str(state),
        "RECOVERY_BASELINE_REPO": str(base),
        "RECOVERY_PYTHON": sys.executable,
        "RECOVERY_LANE_IDLE_SLEEP": "1",
        "RECOVERY_LANE_FLOOR_SLEEP": "1",
        "RECOVERY_LANE_CRASH_SLEEP": "1",
        "RECOVERY_LANE_MAX_CYCLES": "8",
        "PYTHONDONTWRITEBYTECODE": "1",
        **(env_extra or {}),
    }
    return subprocess.run(
        ["bash", str(base / "scripts" / "batch" / "run_lane.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _events(state: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (state / "lane-supervisor.jsonl").read_text().splitlines()
        if line.strip()
    ]


def test_supervisor_reruns_until_all_generated_and_backs_off_when_idle(tmp_path: Path) -> None:
    base = _make_baseline(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    # cycle 1: rows remain, ledger new -> rerun immediately
    # cycle 2: rows remain, ledger unchanged -> idle sleep
    # cycle 3: all generated -> done
    (state / "plan.json").write_text(json.dumps([1, 1, 0]))

    result = _run_lane(base, state)

    assert result.returncode == 0, result.stdout + result.stderr
    names = [event["event"] for event in _events(state)]
    assert names.count("worker_start") == 3
    assert names.count("idle") == 1
    assert names[-1] == "lane_done"
    idle = next(event for event in _events(state) if event["event"] == "idle")
    assert idle["streak"] == 1 and idle["sleep"] == 1


def test_supervisor_host_floor_and_crash_back_off_then_continue(tmp_path: Path) -> None:
    base = _make_baseline(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan.json").write_text(json.dumps([2, 9, 0]))

    result = _run_lane(base, state)

    assert result.returncode == 0, result.stdout + result.stderr
    names = [event["event"] for event in _events(state)]
    assert "host_floor" in names
    crash = next(event for event in _events(state) if event["event"] == "crash")
    assert crash["streak"] == 1
    assert names[-1] == "lane_done"


def test_supervisor_stop_file_ends_the_loop(tmp_path: Path) -> None:
    base = _make_baseline(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan.json").write_text(json.dumps([1, 1, 1, 1]))
    (state / "lane.stop").write_text("")

    result = _run_lane(base, state)

    assert result.returncode == 0
    assert [event["event"] for event in _events(state)] == ["stop"]


def test_supervisor_runs_lane_fix_on_exit_3_and_uses_the_overlay_next_cycle(tmp_path: Path) -> None:
    base = _make_baseline(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan.json").write_text(json.dumps([3, 0]))
    fix = tmp_path / "fake_fix.sh"
    fix.write_text(
        "#!/bin/bash\n"
        'set -e\n'
        'mkdir -p "$RECOVERY_STATE/overlay/scripts/batch"\n'
        'cp "$RECOVERY_BASELINE_REPO"/scripts/__init__.py "$RECOVERY_STATE/overlay/scripts/"\n'
        'cp "$RECOVERY_BASELINE_REPO"/scripts/batch/__init__.py "$RECOVERY_BASELINE_REPO"/scripts/batch/recovery_worker.py "$RECOVERY_STATE/overlay/scripts/batch/"\n'
        'touch "$RECOVERY_STATE/overlay/scripts/batch/OVERLAY_MARKER"\n'
        'echo FAKE_FIX_OK\n'
    )
    fix.chmod(fix.stat().st_mode | stat.S_IEXEC)

    result = _run_lane(base, state, {"RECOVERY_LANE_FIX_CMD": str(fix)})

    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(state)
    names = [event["event"] for event in events]
    # fix_start and fix_exit sit between the first cycle's exit and the
    # second cycle's start.
    assert names.index("fix_start") < names.index("fix_exit")
    second_start = len(names) - 1 - names[::-1].index("worker_start")
    assert names.index("fix_exit") < second_start
    assert next(e for e in events if e["event"] == "fix_exit")["rc"] == 0
    cycles = [json.loads(line) for line in (state / "cycles.log").read_text().splitlines()]
    assert [c["overlay"] for c in cycles] == [False, True]
    assert cycles[1]["pythonpath"].startswith(str(state / "overlay"))
    starts = [e for e in events if e["event"] == "worker_start"]
    assert [e["overlay"] for e in starts] == [False, True]


def test_supervisor_fix_failure_sleeps_and_continues(tmp_path: Path) -> None:
    base = _make_baseline(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan.json").write_text(json.dumps([3, 0]))

    result = _run_lane(base, state, {"RECOVERY_LANE_FIX_CMD": "false"})

    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(state)
    assert next(e for e in events if e["event"] == "fix_exit")["rc"] != 0
    assert [e["event"] for e in events][-1] == "lane_done"
    assert not (state / "overlay").exists()


# ---------------------------------------------------------------- lane_fix.py

FIX_BASE_WORKER = "VALUE = 1\n\n\ndef compute():\n    return VALUE\n"
FIX_TEST = (
    "from scripts.batch import recovery_worker\n\n\n"
    "def test_compute():\n    assert recovery_worker.compute() == 2\n"
)


def _make_fix_baseline(tmp_path: Path) -> Path:
    base = tmp_path / "fixbase"
    (base / "scripts" / "batch").mkdir(parents=True)
    (base / "tests").mkdir()
    (base / "verification_manifests").mkdir()
    (base / "verification_manifests" / "big.json").write_text("{}")
    (base / "scripts" / "__init__.py").write_text("")
    (base / "scripts" / "batch" / "__init__.py").write_text("")
    (base / "scripts" / "batch" / "recovery_worker.py").write_text(FIX_BASE_WORKER)
    (base / "tests" / "test_fix.py").write_text(FIX_TEST)
    (base / "README.md").write_text("baseline readme\n")
    return base


def _seed_controller_failure(state: Path) -> None:
    (state / "failures").mkdir(parents=True)
    (state / "task-evidence").mkdir()
    (state / "failures" / "0001.json").write_text(
        json.dumps(
            {
                "workbook_id": "0001",
                "class": "controller",
                "subclass": "unexpected_exception",
                "signature": "sig-a",
                "error_type": "TypeError",
                "normalized_error": "compute returned <n>",
                "error": "TypeError: compute returned 1",
                "occurrence_count": 2,
            }
        )
    )
    (state / "task-evidence" / "0001.json").write_text(json.dumps({"error": "TypeError: compute returned 1"}))


def _fake_agent(tmp_path: Path, body: str) -> Path:
    agent = tmp_path / "fake_agent.sh"
    agent.write_text("#!/bin/bash\n" + body)
    agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
    return agent


def _run_fix(base: Path, state: Path, agent: Path, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RECOVERY_STATE": str(state),
        "RECOVERY_BASELINE_REPO": str(base),
        "RECOVERY_AGENT": str(agent),
        "RECOVERY_MODEL": "test-model",
        "RECOVERY_LANE_ID": "lane-a",
        "RECOVERY_PYTHON": sys.executable,
        "RECOVERY_LANE_FIX_TESTS": "tests/test_fix.py",
        "RECOVERY_LANE_FIX_TIMEOUT": "60",
        "RECOVERY_LANE_FIX_LOCK": str(base.parent / "lane-fix.lock"),
        "RECOVERY_WORK_ROOT": str(base.parent / "worktrees"),
        "RECOVERY_CODE_BASELINE": str(base.parent / "code-baseline.json"),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        **(extra or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "scripts.batch.lane_fix"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_lane_fix_installs_overlay_when_agent_fixes_and_tests_pass(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    _seed_controller_failure(state)
    # The fake agent: workspace is the last-but-one arg's value (--workspace X).
    agent = _fake_agent(
        tmp_path,
        'ws=""; prev=""; for a in "$@"; do [ "$prev" = "--workspace" ] && ws=$a; prev=$a; done\n'
        'sed -i.bak "s/VALUE = 1/VALUE = 2/" "$ws/scripts/batch/recovery_worker.py" && rm "$ws/scripts/batch/recovery_worker.py.bak"\n'
        'echo "LANE_FIX_SUMMARY: VALUE was 1, tests expect 2"\n',
    )

    result = _run_fix(base, state, agent)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LANE_FIX_OK" in result.stdout
    overlay = state / "overlay"
    assert (overlay / "scripts" / "batch" / "recovery_worker.py").read_text() == FIX_BASE_WORKER.replace("VALUE = 1", "VALUE = 2")
    meta = json.loads((overlay / "OVERLAY.json").read_text())
    assert meta["changed_paths"] == ["scripts/batch/recovery_worker.py"]
    assert meta["summary"].startswith("VALUE was 1")
    assert meta["workbooks"] == ["0001"]
    # Baseline untouched; attempts recorded; no alert left behind.
    assert (base / "scripts" / "batch" / "recovery_worker.py").read_text() == FIX_BASE_WORKER
    attempts = json.loads((state / "lane-fix" / "attempts.json").read_text())
    assert list(attempts.values()) == [1]
    assert not (state / "lane-fix" / "ALERT.json").exists()
    history = [json.loads(l) for l in (state / "lane-fix" / "history.jsonl").read_text().splitlines()]
    assert history[-1]["status"] == "ok"


def test_lane_fix_rejects_edits_outside_scripts_batch_and_tests(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    _seed_controller_failure(state)
    agent = _fake_agent(
        tmp_path,
        'ws=""; prev=""; for a in "$@"; do [ "$prev" = "--workspace" ] && ws=$a; prev=$a; done\n'
        'sed -i.bak "s/VALUE = 1/VALUE = 2/" "$ws/scripts/batch/recovery_worker.py" && rm "$ws/scripts/batch/recovery_worker.py.bak"\n'
        'echo tampered >> "$ws/README.md"\n',
    )

    result = _run_fix(base, state, agent)

    assert result.returncode == 1
    assert "LANE_FIX_FAILED" in result.stdout and "README.md" in result.stdout
    assert not (state / "overlay").exists()
    assert (base / "README.md").read_text() == "baseline readme\n"
    alert = json.loads((state / "lane-fix" / "ALERT.json").read_text())
    assert alert["kind"] == "lane_fix_failed"


def test_lane_fix_rejects_writes_to_baseline_worktree_or_checkpoint(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    _seed_controller_failure(state)
    worktree = tmp_path / "worktrees" / "task-0001"
    worktree.mkdir(parents=True)
    (worktree / "curation.toml").write_text("keep\n")
    (state / "checkpoints").mkdir()
    (state / "checkpoints" / "0001.json").write_text("{}")
    (tmp_path / "code-baseline.json").write_text("{}")
    # A correct fix in the workspace, plus an absolute-path write into the
    # baseline and into a task worktree: the fix is discarded and an alert
    # names the paths.
    agent = _fake_agent(
        tmp_path,
        'ws=""; prev=""; for a in "$@"; do [ "$prev" = "--workspace" ] && ws=$a; prev=$a; done\n'
        'sed -i.bak "s/VALUE = 1/VALUE = 2/" "$ws/scripts/batch/recovery_worker.py" && rm "$ws/scripts/batch/recovery_worker.py.bak"\n'
        f'echo "# sneaky" >> "{base}/scripts/batch/recovery_worker.py"\n'
        f'sleep 0.05; echo changed > "{worktree}/curation.toml"\n',
    )

    result = _run_fix(base, state, agent)

    assert result.returncode == 1
    assert "guarded paths changed" in result.stdout
    assert not (state / "overlay").exists()
    alert = json.loads((state / "lane-fix" / "ALERT.json").read_text())
    assert alert["kind"] == "lane_fix_guard_violation"
    assert any(p.startswith("base:scripts/batch/recovery_worker.py") for p in alert["paths"])
    assert any(p.startswith("worktree:") and p.endswith("curation.toml") for p in alert["paths"])
    fix_result = json.loads(next((state / "lane-fix").glob("*/result.json")).read_text())
    assert fix_result["status"] == "failed"


def test_lane_fix_rejects_when_tests_fail_or_nothing_changed(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    _seed_controller_failure(state)
    idle = _fake_agent(tmp_path, "echo did nothing\n")
    result = _run_fix(base, state, idle)
    assert result.returncode == 1 and "changed nothing" in result.stdout

    wrong = _fake_agent(
        tmp_path,
        'ws=""; prev=""; for a in "$@"; do [ "$prev" = "--workspace" ] && ws=$a; prev=$a; done\n'
        'sed -i.bak "s/VALUE = 1/VALUE = 3/" "$ws/scripts/batch/recovery_worker.py" && rm "$ws/scripts/batch/recovery_worker.py.bak"\n',
    )
    result = _run_fix(base, state, wrong)
    assert result.returncode == 1 and "suites failed" in result.stdout
    assert not (state / "overlay").exists()


def test_lane_fix_refuses_after_the_per_signature_cap(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    _seed_controller_failure(state)
    idle = _fake_agent(tmp_path, "echo did nothing\n")
    assert _run_fix(base, state, idle, {"RECOVERY_LANE_FIX_MAX": "1"}).returncode == 1
    refused = _run_fix(base, state, idle, {"RECOVERY_LANE_FIX_MAX": "1"})
    assert refused.returncode == 4
    assert "cap reached" in refused.stdout
    alert = json.loads((state / "lane-fix" / "ALERT.json").read_text())
    assert alert["kind"] == "lane_fix_cap" and alert["workbooks"] == ["0001"]


def test_lane_fix_refuses_without_controller_failures(tmp_path: Path) -> None:
    base = _make_fix_baseline(tmp_path)
    state = tmp_path / "state"
    (state / "failures").mkdir(parents=True)
    (state / "failures" / "0001.json").write_text(json.dumps({"class": "inspect", "signature": "s"}))
    result = _run_fix(base, state, _fake_agent(tmp_path, "exit 0\n"))
    assert result.returncode == 4
    assert "no controller failures" in result.stdout


def test_governor_hold_runs_command_under_a_lease(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.batch.memory_governor",
            "--budget-dir",
            str(tmp_path / "budget"),
            "--host-bytes",
            str(64 * 1024**3),
            "hold",
            "--lane-id",
            "lane-a-fix",
            "--bytes",
            str(2 * 1024**3),
            "--",
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ok'); raise SystemExit(7)",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert marker.read_text() == "ok"
    snapshot = subprocess.run(
        [sys.executable, "-m", "scripts.batch.memory_governor", "--budget-dir", str(tmp_path / "budget"), "--host-bytes", str(64 * 1024**3), "report"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "lane-a-fix" not in json.loads(snapshot.stdout).get("reservations", {})
