#!/usr/bin/env python3
"""Run one sequential restricted-workbook queue with one Cursor agent at a time."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


HOME = Path.home()
ROOT = HOME / "GDM_FCP"
REPO = Path(
    os.environ.get("RESTRICTED_RECOVERY_REPO", ROOT / "FCP2-optimized")
).resolve()
SOURCE = Path(
    os.environ.get(
        "RESTRICTED_RECOVERY_SOURCE",
        ROOT / "restricted-cohort-v2-sources",
    )
).resolve()
OUTPUT = Path(
    os.environ.get(
        "RESTRICTED_RECOVERY_OUTPUT",
        ROOT / "08_26_samples_tasks_outputs_unified",
    )
).resolve()
WORKER_NAME = os.environ.get("RESTRICTED_RECOVERY_WORKER", "restricted-v2")
MODEL = os.environ.get("RESTRICTED_RECOVERY_MODEL", "gpt-5.6-sol-high")
EXTRA_INSTRUCTIONS = os.environ.get(
    "RESTRICTED_RECOVERY_EXTRA_INSTRUCTIONS", ""
).strip()
WORKBOOKS = [
    value.strip()
    for value in os.environ.get("RESTRICTED_RECOVERY_WORKBOOKS", "").split(",")
    if value.strip()
]
STATE = OUTPUT / ".batch_state" / WORKER_NAME


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_status(workbook: str, status: str, **fields: object) -> None:
    path = STATE / "summary.json"
    summary = json.loads(path.read_text()) if path.is_file() else {"tasks": {}}
    task = summary["tasks"].setdefault(workbook, {})
    task.update(status=status, updated_at=timestamp(), **fields)
    summary["current"] = workbook if status == "running" else None
    summary["updated_at"] = timestamp()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def promoted(workbook: str) -> bool:
    pointer = REPO / "release_out" / workbook / "current-release.json"
    if not pointer.is_file():
        return False
    task_roots = (
        OUTPUT / f"{workbook}-outputs",
        REPO / "tasks_outputs" / f"{workbook}-outputs",
        REPO / "tasks_outputs_mcp" / f"{workbook}-outputs",
    )
    required = (
        Path("instruction.md"),
        Path("task.toml"),
        Path("environment/Dockerfile"),
        Path("tests/run_grader.py"),
        Path("tests/answer_key.json"),
    )
    return any(all((root / item).is_file() for item in required) for root in task_roots)


def prompt(workbook: str) -> str:
    inventory = (
        REPO
        / "verification_manifests"
        / "restricted_source_cohort_123.v2.json"
    )
    source = SOURCE / f"{workbook}.xlsx"
    return f"""
Read and follow {REPO / ".cursor/skills/create-harbor-task/SKILL.md"}.
Process only {source} as workbook {workbook}. Work in {REPO}.

This is a policy-v2 recovery of a previously source-health-blocked workbook.
Use the exact frozen inventory at {inventory}. Re-observe the source bytes. If
the route is not restricted_pass, stop this workbook and preserve the exact
reason codes. Never reinterpret unsupported, insufficient_evidence, mixed
restricted-plus-recalc, true external, data-table, macro, OLE, connection, or
unsafe conversion cases.

For restricted_pass, build an inactive immutable source/AST generation, then an
inactive strict segmentation generation and complete restriction-cone
certificate. Continue only if every source event maps one-to-one and strict
verification fully passes. Carry the pinned source and segmentation generation
IDs through every downstream gate. Promote only through the single
current-release.json compare-and-swap after the immutable task generation
passes. A source or AST build alone is not success.

The user authorizes unattended continuation past curation only when curation is
unchanged or freshly machine-generated in this run, strict segmentation fully
passes, and there is no ambiguity. Otherwise stop fail-closed. Keep real failure
evidence. Do not weaken gates, edit pipeline code, overwrite prior generations,
or process another workbook. Bound light AST/segmentation commands to 30
minutes; workbook 0263 may use 90 minutes and must remain last in its queue.

When fully promoted, materialize the compatibility task at
{OUTPUT / f"{workbook}-outputs"} only if that destination does not already
exist. Nested agents, if required, must be fresh and use model: inherit.

{EXTRA_INSTRUCTIONS}
""".strip()


def main() -> int:
    if not WORKBOOKS:
        raise SystemExit("RESTRICTED_RECOVERY_WORKBOOKS is required")
    if not REPO.is_dir() or not SOURCE.is_dir():
        raise SystemExit("recovery repository or source root is missing")
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "logs").mkdir(exist_ok=True)
    agent = HOME / ".local" / "bin" / "agent"
    environment = os.environ.copy()
    environment["PATH"] = (
        f"{REPO / '.venv' / 'bin'}:{HOME / '.local' / 'bin'}:"
        f"{environment.get('PATH', '')}"
    )
    for workbook in WORKBOOKS:
        if promoted(workbook):
            write_status(workbook, "already_promoted")
            continue
        source = SOURCE / f"{workbook}.xlsx"
        if not source.is_file():
            write_status(workbook, "source_missing", source=str(source))
            continue
        log_path = STATE / "logs" / f"{workbook}.stream.jsonl"
        write_status(workbook, "running", log=str(log_path), source=str(source))
        with log_path.open("w") as log:
            result = subprocess.run(
                [
                    str(agent),
                    "-p",
                    "--force",
                    "--trust",
                    "--output-format",
                    "stream-json",
                    "--model",
                    MODEL,
                    "--workspace",
                    str(REPO),
                    prompt(workbook),
                ],
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        write_status(
            workbook,
            "promoted" if promoted(workbook) else "blocked",
            exit_code=result.returncode,
            log=str(log_path),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
