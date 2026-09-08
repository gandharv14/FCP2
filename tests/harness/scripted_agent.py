#!/usr/bin/env python3
"""Fake Cursor ``agent`` binary for local end-to-end tests.

Speaks the real ``--output-format stream-json`` protocol so the worker's
launch paths (hardened_runner.run_ready_attempt for generation and the
fairness verifier, _default_investigator_launcher for the incident
investigator) run for real, with no model and no network.

The role is inferred from the prompt (the last argument), the way the real
agent would see it:

* ``--mode ask`` and the prompt opens as the fairness verifier -> verifier.
  Emits a report ending in a recovery-fairness/v1 block and a verdict line.
  Controlled by SCRIPTED_FAIRNESS = PASS | FAIL | NOBLOCK | DISAGREE
  (default PASS) and SCRIPTED_FAIRNESS_CHECK (failing check, default
  solvability).
* ``--mode ask`` and the prompt opens as the incident investigator ->
  investigator. Emits a report ending in a recovery-incident-verdict/v1
  block. Controlled by
  SCRIPTED_INCIDENT_ROUTE (default worktree_fix; "controller" parks the row)
  and SCRIPTED_INCIDENT_BARE=1 (last line only, no block).
* anything else -> generation agent. Emits a few shell/edit tool calls, then
  the final message. SCRIPTED_GENERATION_FINAL overrides the final text
  (default "Published.\\nRECOVERY_USED: YES"). SCRIPTED_GENERATION_TOUCH
  names a workspace-relative file to write (to make the run "modified").
  SCRIPTED_GENERATION_EXIT sets the exit code.

SCRIPTED_AGENT_LOG, when set, receives one JSON line per invocation with the
role and argv, so a test can assert which agents ran and in what order.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def assistant(text: str) -> None:
    emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": "scripted"})


def tool(call_id: str, kind: str, args: dict, result: dict | None = None) -> None:
    emit({"type": "tool_call", "subtype": "started", "call_id": call_id, "tool_call": {kind: {"args": args}}, "session_id": "scripted"})
    emit({"type": "tool_call", "subtype": "completed", "call_id": call_id,
          "tool_call": {kind: {"args": args, "result": {"success": result or {}}}}, "session_id": "scripted"})


def fairness_block(verdict: str, failing: str | None) -> str:
    checks = {name: "pass" for name in (
        "technical_evidence", "solvability", "answer_leakage", "hidden_assumptions",
        "source_fidelity", "disclosure_fidelity", "output_representativeness", "recovery_record")}
    findings = []
    if failing:
        checks[failing] = "fail"
        findings.append({"check": failing, "subclass": "scripted_defect", "gate": 6, "artifact": "instruction.md", "detail": "scripted"})
    body = {"schema_version": "recovery-fairness/v1", "verdict": verdict, "checks": checks, "findings": findings}
    return "```json\n" + json.dumps(body, indent=2) + "\n```"


def incident_block(route: str) -> str:
    verdict = "WORKER_FIX_NEEDED" if route == "controller" else "TASK_LOCAL_OR_EXTERNAL"
    body = {"schema_version": "recovery-incident-verdict/v1", "verdict": verdict, "route": route,
            "subclass": "scripted_cause", "gate": 6, "component": "scripted", "cause": "scripted cause",
            "evidence": ["state/task-evidence/x.json"], "fix_hint": "scripted hint"}
    return "```json\n" + json.dumps(body, indent=2) + "\n```", verdict


def main() -> int:
    argv = sys.argv[1:]
    prompt = argv[-1] if argv else ""
    workspace = None
    for index, arg in enumerate(argv):
        if arg == "--workspace" and index + 1 < len(argv):
            workspace = Path(argv[index + 1])
    # Role by how the worker addresses the agent in the prompt's first line
    # (the generation prompt *mentions* the other two roles, so a substring
    # search anywhere would misfire). Both read-only roles run in --mode ask.
    head = prompt.lstrip()[:120]
    ask_mode = "--mode" in argv and argv[argv.index("--mode") + 1] == "ask"
    if ask_mode and "fairness verifier" in head:
        role = "verifier"
    elif ask_mode and "incident investigator" in head:
        role = "investigator"
    else:
        role = "generation"
    log = os.environ.get("SCRIPTED_AGENT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"role": role, "at": time.time(), "workspace": str(workspace)}) + "\n")
    emit({"type": "system", "subtype": "init", "model": "scripted", "session_id": "scripted"})

    if role == "verifier":
        mode = os.environ.get("SCRIPTED_FAIRNESS", "PASS").upper()
        failing = os.environ.get("SCRIPTED_FAIRNESS_CHECK", "solvability")
        assistant("Independent review of the bundle.")
        if mode == "PASS":
            assistant(fairness_block("PASS", None) + "\nFAIRNESS_VERDICT: PASS")
        elif mode == "FAIL":
            assistant(fairness_block("FAIL", failing) + "\nFAIRNESS_VERDICT: FAIL")
        elif mode == "NOBLOCK":
            assistant("FAIRNESS_VERDICT: PASS")
        elif mode == "DISAGREE":
            assistant(fairness_block("FAIL", failing) + "\nFAIRNESS_VERDICT: PASS")
        # A trailing acknowledgement, which the last-line rule must tolerate.
        assistant("No follow-up is needed.")
        emit({"type": "result", "subtype": "success", "result": "verifier done", "session_id": "scripted"})
        return 0

    if role == "investigator":
        route = os.environ.get("SCRIPTED_INCIDENT_ROUTE", "worktree_fix")
        block, verdict = incident_block(route)
        assistant("Root cause analysis.")
        if os.environ.get("SCRIPTED_INCIDENT_BARE") == "1":
            assistant(f"INCIDENT_VERDICT: {verdict}")
        else:
            assistant(block + f"\nINCIDENT_VERDICT: {verdict}")
        assistant("Done.")
        emit({"type": "result", "subtype": "success", "result": "investigator done", "session_id": "scripted"})
        return 0

    # generation
    assistant("Starting the build.")
    tool("g1", "shellToolCall", {"command": "python xl_source_health.py --workbook 0001"}, {"exitCode": 0})
    tool("g2", "shellToolCall", {"command": "bash build_one.sh 0001"}, {"exitCode": 0})
    touch = os.environ.get("SCRIPTED_GENERATION_TOUCH")
    if touch and workspace is not None:
        target = workspace / touch
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SCRIPTED_EDIT = True\n", encoding="utf-8")
        tool("g3", "editToolCall", {"path": touch}, {"linesAdded": 1})
    final = os.environ.get("SCRIPTED_GENERATION_FINAL", "Published.\nRECOVERY_USED: YES")
    assistant(final)
    emit({"type": "result", "subtype": "success", "result": final, "session_id": "scripted"})
    return int(os.environ.get("SCRIPTED_GENERATION_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
