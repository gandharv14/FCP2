#!/usr/bin/env python3
"""Structured per-lane state for the fleet report. Read-only. Runs on a VM.

usage: report_lane_state.py RUN_DIR

Prints one line per lane and kind, ``KIND <lane> <json>``, so
report_remote.sh can pass them through and report.py can aggregate across
VMs without parsing prose:

  FAILURES  <lane> [{workbook_id, class, subclass, gate, signature, status,
                     lifetime_attempts, occurrence_count, instruction_mode,
                     fixability, last_seen}]
  SUPERVISOR <lane> {cycles, last_event, last_rc, fixes, idle_streak,
                     host_floor_events, crashes, stopped}
  LANEFIX   <lane> {overlay: {fix_id, installed_at, changed_paths, summary,
                     workbooks} | null, alert: {...} | null, attempts: {...},
                     history_count}

Everything here comes from files the worker, supervisor and fix step write
(``state-<lane>/failures/*.json``, ``lane-supervisor.jsonl``, ``lane-fix/``,
``overlay/OVERLAY.json``). Missing files are reported as empty, never as an
error: a lane that has not failed has nothing to say.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def lane_failures(state: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    failures = state / "failures"
    if not failures.is_dir():
        return out
    for path in sorted(failures.glob("*.json")):
        record = _load(path, {})
        if not isinstance(record, dict):
            continue
        if record.get("status") == "generated" or record.get("resolved_at"):
            # Delivered since; the record stays for history but is not a
            # current failure.
            continue
        history = record.get("history") if isinstance(record.get("history"), list) else []
        out.append(
            {
                "workbook_id": str(record.get("workbook_id") or path.stem),
                "class": record.get("class"),
                "subclass": record.get("subclass"),
                "gate": record.get("gate"),
                "signature": str(record.get("signature") or "")[:16],
                "status": record.get("status"),
                "lifetime_attempts": record.get("lifetime_attempts"),
                "occurrence_count": record.get("occurrence_count"),
                "instruction_mode": record.get("instruction_mode"),
                "fixability": record.get("fixability"),
                "last_seen": record.get("last_seen"),
                "error": str(record.get("error") or "")[:160],
                # Distinct classes this row has cycled through (current + history).
                "distinct_classes": sorted(
                    {str(record.get("class"))}
                    | {str(item.get("class")) for item in history if isinstance(item, dict) and item.get("class")}
                ),
            }
        )
    return out


def lane_supervisor(state: Path) -> dict[str, object]:
    path = state / "lane-supervisor.jsonl"
    summary: dict[str, object] = {
        "cycles": 0,
        "last_event": None,
        "last_rc": None,
        "last_at": None,
        "fixes": 0,
        "fix_ok": 0,
        "idle_streak": 0,
        "host_floor_events": 0,
        "crashes": 0,
        "stopped": False,
        "overlay_active": False,
    }
    if not path.is_file():
        return summary
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return summary
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("event")
        summary["cycles"] = max(int(summary["cycles"]), int(event.get("cycle", 0) or 0))
        summary["last_event"] = name
        summary["last_at"] = event.get("at")
        if name == "worker_exit":
            summary["last_rc"] = event.get("rc")
        elif name == "worker_start":
            summary["overlay_active"] = bool(event.get("overlay"))
        elif name == "fix_exit":
            summary["fixes"] = int(summary["fixes"]) + 1
            if event.get("rc") == 0:
                summary["fix_ok"] = int(summary["fix_ok"]) + 1
        elif name == "idle":
            summary["idle_streak"] = int(event.get("streak", 0) or 0)
        elif name == "host_floor":
            summary["host_floor_events"] = int(summary["host_floor_events"]) + 1
        elif name == "crash":
            summary["crashes"] = int(summary["crashes"]) + 1
        elif name == "stop":
            summary["stopped"] = True
    return summary


def lane_fix(state: Path) -> dict[str, object]:
    overlay = _load(state / "overlay" / "OVERLAY.json", None)
    alert = _load(state / "lane-fix" / "ALERT.json", None)
    attempts = _load(state / "lane-fix" / "attempts.json", {})
    history = state / "lane-fix" / "history.jsonl"
    try:
        history_count = len(history.read_text(encoding="utf-8").splitlines()) if history.is_file() else 0
    except OSError:
        history_count = 0
    return {
        "overlay": (
            {
                "fix_id": overlay.get("fix_id"),
                "installed_at": overlay.get("installed_at"),
                "changed_paths": overlay.get("changed_paths", []),
                "summary": str(overlay.get("summary") or "")[:300],
                "workbooks": overlay.get("workbooks", []),
            }
            if isinstance(overlay, dict)
            else None
        ),
        "alert": alert if isinstance(alert, dict) else None,
        "attempts": attempts if isinstance(attempts, dict) else {},
        "history_count": history_count,
    }


def emit(run: Path) -> list[str]:
    lines: list[str] = []
    for queue in sorted(run.glob("queue-*.json")):
        lane = queue.name[len("queue-") : -len(".json")]
        state = run / f"state-{lane}"
        lines.append(f"FAILURES {lane} {json.dumps(lane_failures(state), sort_keys=True)}")
        lines.append(f"SUPERVISOR {lane} {json.dumps(lane_supervisor(state), sort_keys=True)}")
        lines.append(f"LANEFIX {lane} {json.dumps(lane_fix(state), sort_keys=True)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__)
        return 2
    for line in emit(Path(args[0])):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
