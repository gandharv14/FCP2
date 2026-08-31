#!/usr/bin/env python3
"""Run one sequential restricted-workbook queue with one Cursor agent at a time."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scripts.batch.hardened_runner import (
    atomic_write_json,
    convert_and_publish,
    run_ready_batch,
)

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
PARALLELISM = int(os.environ.get("RESTRICTED_RECOVERY_PARALLELISM", "1"))
AGENT_TIMEOUT_SECONDS = float(
    os.environ.get("RESTRICTED_RECOVERY_AGENT_TIMEOUT_SECONDS", "7200")
)
_MEMORY_GIB = os.environ.get("RESTRICTED_RECOVERY_AGENT_MEMORY_GIB")
AGENT_MEMORY_LIMIT_BYTES = int(
    (
        float(_MEMORY_GIB)
        if _MEMORY_GIB is not None
        else 112.0 / PARALLELISM
    )
    * 1024 ** 3
)
INVENTORY = Path(
    os.environ.get("RESTRICTED_RECOVERY_INVENTORY", "")
).resolve()
INVENTORY_APPROVAL = Path(
    os.environ.get(
        "RESTRICTED_RECOVERY_INVENTORY_APPROVAL",
        REPO / "verification_manifests" / "approved_source_inventories.v1.json",
    )
).resolve()
BATCH_ID = os.environ.get("RESTRICTED_RECOVERY_BATCH_ID", "").strip()
ALLOWED_ROUTES = frozenset(
    value.strip()
    for value in os.environ.get(
        "RESTRICTED_RECOVERY_ALLOWED_ROUTES",
        "restricted_pass,restricted_recalc_pass",
    ).split(",")
    if value.strip()
)
KNOWN_ROUTES = frozenset({
    "pass",
    "restricted_pass",
    "restricted_recalc_pass",
})
RESTRICTED_ROUTES = frozenset({
    "restricted_pass",
    "restricted_recalc_pass",
})
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
    atomic_write_json(path, summary)


def promoted(workbook: str) -> bool:
    from xl_release_publication import (
        ReleasePublicationError,
        resolve_current_release,
    )

    release_root = REPO / "release_out" / workbook
    try:
        resolve_current_release(
            release_root,
            source_root=REPO / "source_out" / workbook,
            segmentation_root=REPO / "seg_out" / workbook,
        )
    except (OSError, ValueError, ReleasePublicationError):
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


def prompt(workbook: str, source: Path) -> str:
    allowed = ", ".join(sorted(ALLOWED_ROUTES))
    if ALLOWED_ROUTES & RESTRICTED_ROUTES:
        authorization = f"""
This is an approved restricted-source recovery for batch {BATCH_ID}.
Use the exact approved inventory at {INVENTORY} and the commit-pinned approval
registry at {INVENTORY_APPROVAL}. Re-observe the source bytes.
""".strip()
    else:
        authorization = """
This is an unresolved ordinary-source recovery. Re-observe the source bytes and
use ordinary identity evidence. No restricted evidence or inventory may be
introduced.
""".strip()
    return f"""
Read and follow {REPO / ".cursor/skills/create-harbor-task/SKILL.md"}.
Process only {source} as workbook {workbook}. Work in {REPO}.

{authorization}
Proceed only when the fresh route is one of: {allowed}. Otherwise stop and
preserve exact reason codes. Never reinterpret unsupported,
insufficient_evidence, true external,
data-table, macro, OLE, connection, structured-reference, disallowed-volatile,
or unsafe conversion cases.

For a restricted route, build an inactive immutable source/AST generation, then
an inactive strict segmentation generation and complete restriction-cone
certificate. A restricted_recalc_pass must also preserve the exact bound
recalculation-signal ledger. Continue only if every source event maps one-to-one
and strict verification fully passes. Carry the pinned source and segmentation
generation
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
    if not ALLOWED_ROUTES or not ALLOWED_ROUTES <= KNOWN_ROUTES:
        raise SystemExit("recovery allowed routes are invalid")
    if ALLOWED_ROUTES & RESTRICTED_ROUTES and (
        not BATCH_ID
        or not INVENTORY.is_file()
        or not INVENTORY_APPROVAL.is_file()
    ):
        raise SystemExit(
            "approved recovery batch ID, inventory, and registry are required"
        )
    if not REPO.is_dir() or not SOURCE.is_dir():
        raise SystemExit("recovery repository or source root is missing")
    if PARALLELISM < 1:
        raise SystemExit("RESTRICTED_RECOVERY_PARALLELISM must be positive")
    STATE.mkdir(parents=True, exist_ok=True)
    agent = HOME / ".local" / "bin" / "agent"
    environment = os.environ.copy()
    environment["PATH"] = (
        f"{REPO / '.venv' / 'bin'}:{HOME / '.local' / 'bin'}:"
        f"{environment.get('PATH', '')}"
    )
    published = STATE / "published"
    ready = STATE / "ready"
    private = STATE / "conversion-private"
    preterminal: list[dict[str, object]] = []
    for workbook in WORKBOOKS:
        if promoted(workbook):
            preterminal.append({
                "workbook_id": workbook,
                "status": "already_completed",
            })
            continue
        source = SOURCE / f"{workbook}.xlsx"
        if not source.is_file():
            preterminal.append({
                "workbook_id": workbook,
                "status": "source_missing",
                "source": str(source),
            })
            continue
        try:
            convert_and_publish(
                source,
                workbook_id=workbook,
                private_root=private,
                published_dir=published,
                ready_dir=ready,
            )
        except Exception as error:
            preterminal.append({
                "workbook_id": workbook,
                "status": "conversion_failed",
                "error": repr(error),
                "source": str(source),
            })

    producer_complete = STATE / "producer.complete"
    producer_complete.touch(exist_ok=True)

    def command_builder(record, workbook_path):
        return [
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
            prompt(record.workbook_id, workbook_path),
        ]

    def classify_result(record, _result):
        return "completed" if promoted(record.workbook_id) else "blocked"

    def reconcile_terminal(record, result):
        # A timeout can race the agent's final immutable publication and
        # current-pointer switch. Re-read the full promoted state before the
        # terminal ledger records a timeout; all partial states remain timed out.
        if result.get("status") == "timed_out" and promoted(record.workbook_id):
            return "completed"
        return str(result["status"])

    ledger = run_ready_batch(
        WORKBOOKS,
        ready_dir=ready,
        published_dir=published,
        state_dir=STATE,
        command_builder=command_builder,
        workers=PARALLELISM,
        timeout_seconds=AGENT_TIMEOUT_SECONDS,
        memory_limit_bytes=AGENT_MEMORY_LIMIT_BYTES,
        producer_complete_marker=producer_complete,
        initial_terminal_records=preterminal,
        result_classifier=classify_result,
        terminal_reconciler=reconcile_terminal,
    )
    summary = {
        "current": None,
        "counts": ledger["counts"],
        "tasks": {
            row["workbook_id"]: row
            for row in ledger["records"]
        },
        "updated_at": timestamp(),
    }
    atomic_write_json(STATE / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
