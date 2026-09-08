#!/usr/bin/env python3
"""Sequential VM recovery worker with isolated per-task worktrees.

HARD is not a terminal stop. Only a valid current-release plus fairness
(when recovery was used) can mark a workbook generated.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from xl_seg import retention as output_retention
from scripts.batch.disk_reaper import (
    DEFAULT_DISK_HIGH_WATER_BYTES,
    DEFAULT_GENERATION_GRACE_SECONDS as DEFAULT_DISK_GENERATION_GRACE_SECONDS,
    DEFAULT_MIN_FREE_DISK_BYTES,
    DiskReaper,
    DiskReaperError,
    ReapPolicy,
    free_disk_bytes,
)
from scripts.batch.hardened_runner import (
    CHILD_OOM_SCORE_ADJ,
    OOM_SCORE_ADJ_PATH,
    ReadyRecord,
    _oom_killable_command,
    atomic_write_json,
    atomic_write_text,
    convert_and_publish,
    extract_assistant_segments,
    extract_tool_commands,
    memory_scope_supported,
    run_ready_attempt,
    scope_unit_name,
    sha256_file,
)
from scripts.batch.memory_governor import (
    BudgetLease,
    BudgetTimeout,
    MemoryBudget,
    MemoryGovernorError,
    WorkbookProfile,
    estimate_peak_bytes,
    load_calibration,
    probe_workbook,
    record_observation,
)

try:
    # Repo-root module; present when the worker runs as ``-m scripts.batch.*``
    # from the deployed baseline. Memoises tree hashing of source_out /
    # seg_out / release_out, which the worker walks twice around the verifier.
    from xl_hash_cache import sha256_file as memo_sha256_file
except ImportError:  # pragma: no cover - imported outside the repo root
    memo_sha256_file = sha256_file


SUMMARY_SCHEMA = "recovery-summary/v1"
DELIVERY_SCHEMA = "recovery-delivery/v1"
LEDGER_SCHEMA = "recovery-ledger/v1"
CHECKPOINT_SCHEMA = "recovery-checkpoint/v1"
WORKTREE_MARKER_SCHEMA = "recovery-worktree/v1"
INCIDENT_SCHEMA = "recovery-incident/v1"
# Closed failure taxonomy (ported from the FCP2 orchestrator's
# promotion-failure contract). Every row close that is not ``generated``
# writes ``state/failures/<wb>.json`` with exactly one of these classes, a
# stable subclass, the SKILL.md gate number, and a signature that survives
# hashes, paths and counters changing between attempts. The fleet report keys
# on class/subclass, never on error text. ``retry_budget`` and ``controller``
# are classes, not statuses: the row status stays ``recovery_pending`` or
# ``worker_fix_needed`` so the resume rewrite in run() keeps the row active.
FAILURE_SCHEMA = "recovery-failure/v1"
FAILURE_CLASSES = frozenset(
    {
        "inspect",
        "retention",
        "recovery_record",
        "instruction_metadata",
        "fairness_semantic",
        "fairness_leak",
        "fairness_fidelity",
        "verifier_error",
        "delivery",
        "policy_violation",
        "retry_budget",
        "agent_hard_blocker",
        "generation_failed",
        "disk_exhausted",
        "orchestration",
        "shared_pipeline_defect",
        "controller",
        "unknown",
    }
)
# Fairness checks whose failure maps to a semantic class.
FAIRNESS_CHECK_CLASSES = {
    "technical_evidence": "fairness_fidelity",
    "solvability": "fairness_semantic",
    "answer_leakage": "fairness_leak",
    "hidden_assumptions": "fairness_semantic",
    "source_fidelity": "fairness_fidelity",
    "disclosure_fidelity": "fairness_fidelity",
    "output_representativeness": "fairness_semantic",
    "recovery_record": "recovery_record",
}
FAIRNESS_CHECKS = tuple(FAIRNESS_CHECK_CLASSES)
FAIRNESS_BLOCK_SCHEMA = "recovery-fairness/v1"
RECOVERY_EVIDENCE_SCHEMA = "recovery-evidence/v1"
INCIDENT_VERDICT_SCHEMA = "recovery-incident-verdict/v1"
INCIDENT_ROUTES = frozenset(
    {
        "worktree_fix",
        "paperwork",
        "cone_prune",
        "shared_pipeline_defect",
        "controller",
        "analysis_only",
    }
)
# Lifetime cap on generation attempts per row. process() grants
# ``attempt_count`` per pickup; with immediate retry a row would otherwise be
# retried forever. At the cap the row is recorded ``retry_budget`` and stays
# active (never ``generated``, never skipped); the lane moves to its next row.
DEFAULT_ROW_ATTEMPT_CAP = 18
# Worker exit codes read by scripts/batch/run_lane.sh.
EXIT_ALL_GENERATED = 0
EXIT_ROWS_REMAIN = 1
EXIT_HOST_FLOOR = 2
EXIT_CONTROLLER_FIX = 3
# Re-entry map (Port 6): gate number -> (name, artifacts it owns, recipe).
# Gate numbers follow .cursor/skills/create-harbor-task/SKILL.md.
_REENTRY_GATES: dict[int, tuple[str, tuple[str, ...], str]] = {
    1: (
        "preflight and AST",
        ("source health report", "ast_out/<wb>/"),
        "rerun source health and the AST build for the workbook; if source health "
        "routes restricted, follow that route before anything else",
    ),
    2: (
        "segmentation and curation",
        ("seg_out/<wb>/curation.toml", "seg_out/<wb>/ verification and pruning records"),
        "re-segment (xl_segment.py; --prune-failing-outputs when outputs were dropped), "
        "keep every verifier-passing output, mark any manual exclusion with an allowed code",
    ),
    3: ("baseline inputs", ("inputs/", "input mask"), "rebuild inputs from the current curation"),
    4: ("model audit", ("audit report",), "rerun the audit on the rebuilt inputs"),
    5: ("row import", ("imported rows",), "re-import every Markdown row"),
    6: (
        "normalize and account for every row",
        ("normalized rows", "row accounting"),
        "renormalize atomically; every row must be accounted for, units and ratios exact",
    ),
    7: ("public source profiles", ("source profiles",), "re-profile the public sources"),
    8: ("maskability review", ("mask decisions",), "re-review maskability, duplicates and extra cells"),
    9: ("deterministic MCP output", ("mcp output", "smoke result"), "rebuild, validate and smoke the MCP output"),
    10: ("MCP input mask", ("masked MCP inputs",), "re-mask the MCP inputs separately"),
    11: ("staging package", ("staging/<wb>/",), "repackage to staging from the rebuilt artifacts"),
    12: ("unified disclosure", ("tests/disclosure.json",), "rebuild the disclosure from the staged package"),
    13: (
        "instruction (naturalize or GDPval prose)",
        ("instruction.md", "instruction metadata"),
        "rerun gate 13 in the row's recorded instruction mode only; for prose mode run "
        "prose_recovery.py verify-metadata and fix every reason code it reports",
    ),
    14: ("HTTP oracle", ("oracle result",), "rerun the generalized oracle against the staged task"),
    15: ("exact-answer grader smoke", ("tests/run_grader.py smoke", "answer key check"), "re-smoke the grader with the answer key"),
    16: (
        "promotion and recovery record",
        ("release manifest", "a.md", "release_out/<wb>/"),
        "fix a.md against the controller's recovery-evidence command log and changed-file list, "
        "then re-publish the release; do not rebuild pipeline artifacts",
    ),
}
# Subclass -> gate, consulted before the class default. Names are the ones the
# fairness verifier and classify_failure produce.
_REENTRY_SUBCLASS_GATE: dict[str, int] = {
    "leak_inputs_workbook": 10,
    "leak_mcp_inputs": 10,
    "leak_readme": 11,
    "leak_disclosure": 12,
    "leak_instruction": 13,
    "key_in_instruction": 13,
    "units_audit_row": 4,
    "units_normalizer": 6,
    "units_percent_vs_ratio": 6,
    "row_unaccounted": 6,
    "undisclosed_input": 12,
    "hidden_assumption": 12,
    "disclosure_mismatch": 12,
    "source_profile_mismatch": 7,
    "mask_leak": 8,
    "grader_semantics": 15,
    "oracle_mismatch": 14,
    "amd_incomplete": 16,
    "amd_missing": 16,
    "amd_commands": 16,
    "verify_metadata": 13,
    "mode_mismatch": 13,
    "release_inspect": 16,
    "unexplained_exclusion": 2,
}
_REENTRY_DEFAULT_GATE: dict[str, int] = {
    "inspect": 16,
    "retention": 2,
    "recovery_record": 16,
    "instruction_metadata": 13,
    "fairness_semantic": 6,
    "fairness_leak": 12,
    "fairness_fidelity": 12,
    "delivery": 16,
    "agent_hard_blocker": 1,
    "generation_failed": 1,
    "shared_pipeline_defect": 2,
}
_SIGNATURE_HASH_RE = re.compile(r"\b[0-9a-f]{12,}\b")
_SIGNATURE_PATH_RE = re.compile(r"/\S+")
_SIGNATURE_NUMBER_RE = re.compile(r"\d+")
_SIGNATURE_TYPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Violation|Retry|Exception|Timeout)):\s*")
TECHNICAL_EVIDENCE_SCHEMA = "recovery-technical-evidence/v1"
QUEUE_FIELDS = frozenset(
    {
        "batch_id",
        "workbook_id",
        "run_source_path",
        "run_source_sha256",
        "source_format",
        "source_sha256",
        "source_size",
    }
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WORKBOOK_FORMATS = frozenset({"xlsx", "xlsm", "xls", "xlsb"})
REQUIRED_BUNDLE_FILES = (
    "instruction.md",
    "task.toml",
    "environment/Dockerfile",
    "tests/run_grader.py",
    "tests/answer_key.json",
    "tests/outputs.json",
    "tests/test.sh",
    "tests/segmentation_generation_manifest.json",
    "tests/inputs_generation.json",
    "tests/pipeline_bindings.json",
    "tests/disclosure.json",
)
REQUIRED_BUNDLE_DIRECTORIES = ("tests/finance_grader",)
GENERATED_ROOTS = frozenset(
    {
        "source_out",
        "seg_out",
        "inputs_out",
        "inputs_out_mcp",
        "runs",
        "tasks_outputs",
        "tasks_outputs_mcp",
        "tasks_outputs_final",
        "release_out",
    }
)
CODE_SUFFIXES = frozenset(
    {".py", ".sh", ".toml", ".yaml", ".yml", ".json", ".md"}
)
# Tool scratch that is never part of a task's code: bytecode caches and the
# pytest cache directory. The fleet's deploy step runs the test suite in the
# deployed baseline, and `.pytest_cache/README.md` then showed up in the code
# diff of every recovered row as an unexplained change, failing fairness on
# paperwork. Every snapshot, diff, and copy honours this one set.
TOOL_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache"})
COPYTREE_EXCLUDE = frozenset({".venv"}) | TOOL_CACHE_DIRS | GENERATED_ROOTS
# Documentation only: nothing in the worker reads this set. The run loop stops
# on any status other than "generated", and the resume rewrite in run() uses
# its own literal set, which is what turns disk_exhausted back into
# recovery_pending on the next start.
RECOVERY_STATUSES = frozenset(
    {
        "recovery_pending",
        "task_fix_needed",
        "fairness_retry",
        "worker_fix_needed",
        "disk_exhausted",
        "generated",
    }
)
RECOVERY_LADDER = (
    "normal path: publish a complete immutable release without recovery",
    "smallest task-local pipeline, formula, or validator fix",
    "disclosed deterministic assumption for volatile or external cells",
    "prune only verifier-implicated outputs (xl_segment.py "
    "--prune-failing-outputs); keep every output that verifies",
    "deterministic naturalization reconstruction",
    "a different strategy because the same failure repeated",
)
# One targeted retry per row when the delivered cone excludes auto-included
# outputs without an allowed reason marker (see xl_seg.retention). Separate
# from the paperwork retry: that one forbids rebuilding artifacts, and a cone
# fix has to re-segment and rebuild everything downstream. When this retry is
# spent the row still delivers, stamped ``override_after_retry`` in the
# sidecar, because no non-``generated`` status is terminal and a parked large
# workbook would burn a lane every run.
MAX_CONE_RETRIES = 1
RETENTION_GATE = "output retention"
# xl_segment's exit status for "cone pruned, curation on disk, rerun me".
PRUNE_EXIT_CODE = 3
AMD_REQUIRED = (
    r"blocker",
    r"evidence",
    r"stage",
    r"fix",
    r"changed files?",
    r"before",
    r"after",
    r"commands?",
    r"verifier",
)
# A line in a.md that reads as a command: a shell prompt, an interpreter, or
# an environment assignment followed by one.
AMD_COMMAND_LINE = re.compile(
    r"^(?:[$>]\s+|(?:[A-Z_][A-Z0-9_]*=\S*\s+)*"
    r"(?:python3?|\.venv/bin/python3?|uv|pytest|bash|sh|make)\b)"
)
# One targeted retry per row for a failure that is paperwork rather than
# pipeline: the release did not inspect clean, or a.md is incomplete. The
# artifacts in the worktree are kept and the agent is told to rerun only the
# failed gate on the same ladder step. A second failure of the same kind goes
# back to the investigator path.
_AMD_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_AMD_INLINE_CODE = re.compile(r"^`([^`]+)`[.:;,]?\s*(?:\(.*\))?$")
MAX_PAPERWORK_RETRIES = 1
PAPERWORK_FINAL_TAIL_CHARS = 1500
HARD_LINE = re.compile(r"(?m)^FULL_RERUN_BLOCKER: HARD$")
REPAIR_LINE = re.compile(
    r"(?m)(?:^|(?<=[.!?`)\]*_]))(?:FULL_RERUN_REPAIR|RECOVERY_USED): (YES|NO)(?=$|[A-Z][a-z])"
)
DEFAULT_ATTEMPT_COUNT = 6
INSPECT_TIMEOUT_SECONDS = 600
INCIDENT_TIMEOUT_SECONDS = 1800
# The generation wall clock. Six hours was the binding constraint on fleet
# throughput: a hung agent held its lane for the whole period. With the stall
# detector killing hung agents, four hours covers every completed attempt seen
# so far with room to spare. The per-row ceiling is never below the inner AST
# budget the prompt hands the agent plus an hour for the downstream gates, so
# a row on the large-workbook list keeps a longer outer clock.
DEFAULT_TIMEOUT_SECONDS = 14400
DOWNSTREAM_MARGIN_SECONDS = 3600
# The fairness verifier is a read-only agent that has never needed more than a
# few minutes. It used to share the generation clock, so a hung verifier could
# hold a lane for six hours on a row that was already published.
DEFAULT_FAIRNESS_TIMEOUT_SECONDS = 3600
# Fleet ceiling on generation lanes per VM. The governor may start a VM with
# fewer (VM1's 290 GB disk carries two), so prompts quote config.lane_count
# rather than this cap. It tracks memory_governor.LANE_CAP: three lanes fit
# the 110 GiB budget of a 128 GB host; the fourth exists for 512 GB hosts
# (hhu-server-14, 443 GiB budget), where each lane still gets ~110 GiB. The
# admission gate, not this number, decides how many lanes actually run.
GENERATION_LANE_COUNT = 4
# An evidence copy is a multi-gigabyte copytree of a live worktree. On a
# swapless host, letting one reach 20 GiB floods the page cache and stalls
# writeback hard enough to hang sshd on its own, so the ceiling is much lower
# and the copy also takes an I/O reservation from the memory budget.
DEFAULT_EVIDENCE_ITEM_LIMIT_BYTES = 6 * 1024 * 1024 * 1024
DEFAULT_EVIDENCE_GLOBAL_LIMIT_BYTES = 100 * 1024 * 1024 * 1024
DEFAULT_MEMORY_BUDGET_DIR = Path("/var/tmp/harbor-memory")
# The fairness verifier and the incident investigator are full agents on the
# same model and workspace, so a row keeps a real reservation after generation
# ends rather than releasing and letting a sibling admit another monster.
DEFAULT_MEMORY_VERIFIER_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MEMORY_IO_BYTES = 4 * 1024 * 1024 * 1024
MEMORY_LIMIT_HEADROOM = 1.5
# The generation scope's MemoryMax is a host share, not a tight estimate. The
# first fleet run capped every row at 1.5x an estimate that was itself half of
# reality, so every completed attempt spent hours in memory.high reclaim
# throttling. The cap now takes the budget, subtracts what runs outside any
# scope (the fairness verifier, the incident investigator, the worker itself,
# tmux, and quarantine copies), and splits the rest across the planned lanes.
# Caps therefore sum to less than the budget and a runaway is still stopped
# before it starves sshd, while an ordinary row never touches its ceiling.
DEFAULT_MEMORY_UNSCOPED_ALLOWANCE_BYTES = 12 * 1024 * 1024 * 1024
# A generation agent whose stream is silent and whose scope burns no CPU for
# this long is hung and is killed as timed_out rather than holding the lane
# for the full wall clock. 0 disables the detector.
DEFAULT_GENERATION_STALL_SECONDS = 1800
# A generation attempt runs for hours, so a five-second memory sample is ample
# and avoids burning CPU on a host already under pressure.
GENERATION_MEMORY_POLL_SECONDS = 5.0
# Admission waits are bounded. The live MemAvailable check can refuse because
# of memory the budget file cannot see, and an unbounded wait would stall every
# lane on the host with nothing in the logs to say why.
BUDGET_WAIT_TIMEOUT_SECONDS = 3600.0
# Disk floor and reaper high-water are defined in scripts.batch.disk_reaper and
# imported above. A generation attempt can write a 47 GB AST plus a second
# staged copy of it, and a full root disk takes journald and sshd down with it,
# so a row that cannot fit is parked before it starts rather than failing
# halfway. RECOVERY_MIN_FREE_DISK_BYTES=0 disables the gate.
# Runs in a fresh interpreter whose PYTHONPATH is the task worktree, so
# resolve_current_release uses the same modules the generation agent published
# with. The long-lived worker process stays on baseline code.
INSPECT_RELEASE_SCRIPT = r"""
import json
import sys
from pathlib import Path

payload = json.loads(sys.stdin.read())
repo = Path(payload["repo"])
workbook_id = payload["workbook_id"]
expected_sha = payload["run_source_sha256"]
required_files = payload["required_files"]
required_dirs = payload["required_dirs"]
sys.path.insert(0, str(repo))

from xl_release_publication import (
    resolve_current_release,
    resolve_task_generation_by_id,
)


def fail(message: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "error": message}) + "\n")
    raise SystemExit(2)


release_root = repo / "release_out" / workbook_id
pointer_path = release_root / "current-release.json"
if pointer_path.is_symlink():
    pointer_note = "current-release.json is a symlink"
elif pointer_path.is_file():
    pointer_note = "current-release.json is present"
else:
    pointer_note = "current-release.json is absent"
try:
    release_dir, release = resolve_current_release(
        release_root,
        source_root=repo / "source_out" / workbook_id,
        segmentation_root=repo / "seg_out" / workbook_id,
        task_root=release_root,
    )
except Exception as exc:
    fail("%s: %s [%s]" % (type(exc).__name__, exc, pointer_note))

if (
    release.get("workbook_id") != workbook_id
    or (release.get("bindings") or {}).get("source_sha256") != expected_sha
):
    fail("current immutable release does not bind queue source")
task_record = release.get("task_generation")
if not isinstance(task_record, dict):
    fail("release has no immutable task generation")
generation_id = task_record.get("generation_id")
if not isinstance(generation_id, str):
    fail("release task generation ID is missing")
try:
    task_dir, _manifest = resolve_task_generation_by_id(release_root, generation_id)
except Exception as exc:
    fail("%s: %s" % (type(exc).__name__, exc))
missing = [
    relative
    for relative in required_files
    if not (task_dir / relative).is_file() or (task_dir / relative).is_symlink()
]
missing.extend(
    relative
    for relative in required_dirs
    if not (task_dir / relative).is_dir() or (task_dir / relative).is_symlink()
)
input_workbook = task_dir / "environment" / ("%s-inputs.xlsx" % workbook_id)
if not input_workbook.is_file() or input_workbook.is_symlink():
    missing.append(input_workbook.relative_to(task_dir).as_posix())
if missing:
    fail("immutable release task bundle is incomplete: " + ", ".join(missing))
pointer = release_root / "current-release.json"
if pointer.is_symlink():
    fail("current-release.json is a symlink")
if not pointer.is_file():
    fail("current-release.json is absent")
try:
    pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
except Exception as exc:
    fail("current-release.json is unreadable: %s" % exc)
if not isinstance(pointer_data, dict):
    fail("current-release.json is not an object")
if str(pointer_data.get("release_id")) != str(release["release_id"]):
    fail("current-release.json does not bind current release")
sys.stdout.write(
    json.dumps(
        {
            "ok": True,
            "release_id": str(release["release_id"]),
            "release_dir": str(release_dir),
            "task_dir": str(task_dir),
            "task_generation_id": generation_id,
        }
    )
    + "\n"
)
"""


class RecoveryError(RuntimeError):
    """A queue, release, or recovery gate failed for one task."""


class PolicyViolation(RecoveryError):
    """Source bytes or a protected grader/rubric/key/threshold changed."""


class RecoveryOrchestrationError(RuntimeError):
    """Launcher-level failure. Not a workbook HARD blocker."""


class FairnessRetry(RecoveryError):
    """Independent fairness verifier did not PASS. Keep the row queued."""

    def __init__(
        self,
        report: str,
        attempt: Mapping[str, object] | None,
        verdict: Mapping[str, object] | None = None,
    ):
        super().__init__("independent fairness verifier did not PASS")
        self.report = report
        self.attempt = dict(attempt) if attempt is not None else None
        # Structured recovery-fairness/v1 block, when the report carried one.
        self.verdict = dict(verdict) if verdict is not None else None


@dataclass(frozen=True)
class QueueRow:
    batch_id: str
    workbook_id: str
    run_source_path: Path
    run_source_sha256: str
    source_format: str
    source_sha256: str
    source_size: int

    @classmethod
    def parse(cls, value: object, *, queue_dir: Path) -> "QueueRow":
        if not isinstance(value, dict):
            raise RecoveryError("every queue row must be a JSON object")
        missing = QUEUE_FIELDS - set(value)
        if missing:
            raise RecoveryError(
                "queue row is missing fields: " + ", ".join(sorted(missing))
            )
        batch_id = _safe_id(value["batch_id"], "batch ID")
        workbook_id = _safe_id(value["workbook_id"], "workbook ID")
        source_format = str(value["source_format"]).strip().lower().removeprefix(".")
        if source_format not in WORKBOOK_FORMATS:
            raise RecoveryError(
                f"unsupported source format for {workbook_id}: {source_format!r}"
            )
        for name in ("run_source_sha256", "source_sha256"):
            if not isinstance(value[name], str) or not HASH_RE.fullmatch(value[name]):
                raise RecoveryError(
                    f"{name} for {workbook_id} must be a lowercase SHA-256"
                )
        source_size = value["source_size"]
        if (
            not isinstance(source_size, int)
            or isinstance(source_size, bool)
            or source_size < 0
        ):
            raise RecoveryError(f"source_size for {workbook_id} is invalid")
        raw_path = Path(str(value["run_source_path"])).expanduser()
        source_path = (
            raw_path if raw_path.is_absolute() else queue_dir / raw_path
        ).resolve(strict=False)
        return cls(
            batch_id=batch_id,
            workbook_id=workbook_id,
            run_source_path=source_path,
            run_source_sha256=value["run_source_sha256"],
            source_format=source_format,
            source_sha256=value["source_sha256"],
            source_size=source_size,
        )


@dataclass(frozen=True)
class BaselineEntry:
    path: str
    sha256: str
    protected: bool


@dataclass(frozen=True)
class ReleaseCandidate:
    release_id: str
    release_dir: Path
    task_dir: Path
    task_generation_id: str
    source_sha256: str
    bundle_sha256: str
    pointer_path: Path | None = None
    pointer_sha256: str | None = None
    release_manifest_path: Path | None = None
    release_manifest_sha256: str | None = None
    task_manifest_path: Path | None = None
    task_manifest_sha256: str | None = None


@dataclass(frozen=True)
class Config:
    queue: Path
    baseline_repo: Path
    work_root: Path
    state: Path
    output: Path
    code_baseline: Path
    model: str
    timeout_seconds: float
    attempt_count: int
    task_limit: int
    agent_binary: Path
    inventory_registry: Path
    approval_batch_id: str
    inventory: Path | None
    large_workbooks: frozenset[str]
    measured_timeout_seconds: int
    large_timeout_seconds: int
    evidence_item_limit_bytes: int = DEFAULT_EVIDENCE_ITEM_LIMIT_BYTES
    evidence_global_limit_bytes: int = DEFAULT_EVIDENCE_GLOBAL_LIMIT_BYTES
    memory_governor_enabled: bool = True
    memory_budget_dir: Path = DEFAULT_MEMORY_BUDGET_DIR
    memory_host_bytes: int = 0
    memory_reserve_bytes: int = 0
    memory_verifier_bytes: int = DEFAULT_MEMORY_VERIFIER_BYTES
    memory_io_bytes: int = DEFAULT_MEMORY_IO_BYTES
    memory_unscoped_allowance_bytes: int = DEFAULT_MEMORY_UNSCOPED_ALLOWANCE_BYTES
    generation_stall_seconds: int = DEFAULT_GENERATION_STALL_SECONDS
    fairness_timeout_seconds: float = DEFAULT_FAIRNESS_TIMEOUT_SECONDS
    lane_id: str = "lane"
    lane_count: int = GENERATION_LANE_COUNT
    # Defaults are required here because the fields above have them; a bare
    # field would be a TypeError at class definition. 0 disables the gate,
    # which also means a directly constructed Config has no floor.
    min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES
    disk_high_water_bytes: int = DEFAULT_DISK_HIGH_WATER_BYTES
    disk_generation_grace_seconds: int = DEFAULT_DISK_GENERATION_GRACE_SECONDS
    row_attempt_cap: int = DEFAULT_ROW_ATTEMPT_CAP

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        values = os.environ if env is None else env

        def first_text(*names: str, default: str = "") -> str:
            for name in names:
                raw = str(values.get(name, "")).strip()
                if raw:
                    return raw
            return default

        def required_path(*names: str) -> Path:
            raw = first_text(*names)
            if not raw:
                raise RecoveryError(f"{names[0]} is required")
            return Path(os.path.abspath(Path(raw).expanduser()))

        baseline_repo = required_path(
            "RECOVERY_BASELINE_REPO", "FULL_RERUN_REPO"
        )
        timeout = float(
            first_text(
                "RECOVERY_TIMEOUT_SECONDS",
                "RECOVERY_TIMEOUT",
                "FULL_RERUN_TIMEOUT_SECONDS",
                "FULL_RERUN_TIMEOUT",
                default=str(DEFAULT_TIMEOUT_SECONDS),
            )
        )
        fairness_timeout = float(
            first_text(
                "RECOVERY_FAIRNESS_TIMEOUT_SECONDS",
                default=str(DEFAULT_FAIRNESS_TIMEOUT_SECONDS),
            )
        )
        attempts = int(
            first_text(
                "RECOVERY_ATTEMPT_COUNT",
                "FULL_RERUN_ATTEMPT_COUNT",
                default=str(DEFAULT_ATTEMPT_COUNT),
            )
        )
        task_limit = int(
            first_text("RECOVERY_TASK_LIMIT", "FULL_RERUN_TASK_LIMIT", default="0")
        )
        measured = int(
            first_text(
                "RECOVERY_MEASURED_TIMEOUT_SECONDS",
                "FULL_RERUN_MEASURED_TIMEOUT_SECONDS",
                default="7200",
            )
        )
        large = int(
            first_text(
                "RECOVERY_LARGE_TIMEOUT_SECONDS",
                "FULL_RERUN_LARGE_TIMEOUT_SECONDS",
                default="14400",
            )
        )
        evidence_item_limit = int(
            first_text(
                "RECOVERY_EVIDENCE_ITEM_LIMIT_BYTES",
                default=str(DEFAULT_EVIDENCE_ITEM_LIMIT_BYTES),
            )
        )
        evidence_global_limit = int(
            first_text(
                "RECOVERY_EVIDENCE_GLOBAL_LIMIT_BYTES",
                default=str(DEFAULT_EVIDENCE_GLOBAL_LIMIT_BYTES),
            )
        )
        if not (0 < timeout <= 86400):
            raise RecoveryError("TIMEOUT must be in (0, 86400]")
        if not (0 < fairness_timeout <= 86400):
            raise RecoveryError("FAIRNESS_TIMEOUT must be in (0, 86400]")
        if attempts < DEFAULT_ATTEMPT_COUNT:
            raise RecoveryError(
                "ATTEMPT_COUNT must be at least "
                f"{DEFAULT_ATTEMPT_COUNT}"
            )
        if task_limit < 0:
            raise RecoveryError("TASK_LIMIT cannot be negative")
        row_attempt_cap = int(
            first_text("RECOVERY_ROW_ATTEMPT_CAP", default=str(DEFAULT_ROW_ATTEMPT_CAP))
        )
        if row_attempt_cap < attempts:
            raise RecoveryError(
                "ROW_ATTEMPT_CAP must be at least ATTEMPT_COUNT "
                f"({row_attempt_cap} < {attempts})"
            )
        if evidence_item_limit <= 0 or evidence_global_limit < evidence_item_limit:
            raise RecoveryError(
                "evidence limits must be positive and global must cover one item"
            )
        memory_governor = first_text(
            "RECOVERY_MEMORY_GOVERNOR", default="1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        memory_verifier = int(
            first_text(
                "RECOVERY_MEMORY_VERIFIER_BYTES",
                default=str(DEFAULT_MEMORY_VERIFIER_BYTES),
            )
        )
        memory_io = int(
            first_text(
                "RECOVERY_MEMORY_IO_BYTES", default=str(DEFAULT_MEMORY_IO_BYTES)
            )
        )
        memory_host = int(first_text("RECOVERY_MEMORY_HOST_BYTES", default="0"))
        memory_reserve = int(first_text("RECOVERY_MEMORY_RESERVE_BYTES", default="0"))
        memory_unscoped = int(
            first_text(
                "RECOVERY_MEMORY_UNSCOPED_ALLOWANCE_BYTES",
                default=str(DEFAULT_MEMORY_UNSCOPED_ALLOWANCE_BYTES),
            )
        )
        stall_seconds = int(
            first_text(
                "RECOVERY_STALL_SECONDS",
                default=str(DEFAULT_GENERATION_STALL_SECONDS),
            )
        )
        if memory_verifier <= 0 or memory_io <= 0:
            raise RecoveryError("memory stage reservations must be positive")
        if memory_host < 0 or memory_reserve < 0 or memory_unscoped < 0:
            raise RecoveryError("memory host, reserve, and allowance cannot be negative")
        if stall_seconds < 0:
            raise RecoveryError("STALL_SECONDS cannot be negative")
        min_free_disk = int(
            first_text(
                "RECOVERY_MIN_FREE_DISK_BYTES",
                default=str(DEFAULT_MIN_FREE_DISK_BYTES),
            )
        )
        disk_high_water = int(
            first_text(
                "RECOVERY_DISK_HIGH_WATER_BYTES",
                default=str(DEFAULT_DISK_HIGH_WATER_BYTES),
            )
        )
        disk_grace = int(
            first_text(
                "RECOVERY_DISK_GENERATION_GRACE_SECONDS",
                default=str(DEFAULT_DISK_GENERATION_GRACE_SECONDS),
            )
        )
        if min_free_disk < 0 or disk_high_water < 0 or disk_grace < 0:
            raise RecoveryError("disk floor, high-water, and grace cannot be negative")
        if min_free_disk and disk_high_water and disk_high_water < min_free_disk:
            raise RecoveryError("disk high-water must not be below the disk floor")
        lane_count = int(
            first_text("RECOVERY_LANE_COUNT", default=str(GENERATION_LANE_COUNT))
        )
        if not (1 <= lane_count <= GENERATION_LANE_COUNT):
            raise RecoveryError(
                f"LANE_COUNT must be between 1 and {GENERATION_LANE_COUNT}"
            )
        lane_raw = first_text("RECOVERY_LANE_ID", default="")
        approval_batch_raw = first_text(
            "RECOVERY_APPROVAL_BATCH_ID", "FULL_RERUN_APPROVAL_BATCH_ID"
        )
        if not approval_batch_raw:
            raise RecoveryError("APPROVAL_BATCH_ID is required")
        inventory_raw = first_text(
            "RECOVERY_INVENTORY", "FULL_RERUN_INVENTORY"
        )
        agent_raw = first_text(
            "RECOVERY_AGENT",
            "FULL_RERUN_AGENT",
            default=str(Path.home() / ".local/bin/agent"),
        )
        large_ids = frozenset(
            item.strip()
            for item in first_text(
                "RECOVERY_LARGE_WORKBOOKS", "FULL_RERUN_LARGE_WORKBOOKS"
            ).split(",")
            if item.strip()
        )
        state_path = required_path("RECOVERY_STATE", "FULL_RERUN_STATE")
        # The budget file is shared by every lane on the host, so lane IDs must
        # be distinct. Each lane already has its own state root, so derive from
        # that rather than making the operator remember to pass one.
        lane_id = _safe_id(
            lane_raw or _lane_id_from_path(state_path), "lane ID"
        )
        return cls(
            queue=required_path("RECOVERY_QUEUE", "FULL_RERUN_QUEUE"),
            baseline_repo=baseline_repo,
            work_root=required_path("RECOVERY_WORK_ROOT"),
            state=state_path,
            output=required_path("RECOVERY_OUTPUT", "FULL_RERUN_OUTPUT"),
            code_baseline=required_path(
                "RECOVERY_CODE_BASELINE", "FULL_RERUN_CODE_BASELINE"
            ),
            model=first_text(
                "RECOVERY_MODEL",
                "FULL_RERUN_MODEL",
                default="gpt-5.6-sol-high",
            ),
            timeout_seconds=timeout,
            attempt_count=attempts,
            task_limit=task_limit,
            agent_binary=Path(agent_raw).expanduser().resolve(strict=False),
            inventory_registry=Path(
                first_text(
                    "RECOVERY_INVENTORY_APPROVAL",
                    "FULL_RERUN_INVENTORY_APPROVAL",
                    default=str(
                        baseline_repo
                        / "verification_manifests"
                        / "approved_source_inventories.v1.json"
                    ),
                )
            ).expanduser().resolve(strict=False),
            approval_batch_id=_safe_id(
                approval_batch_raw, "approval batch ID"
            ),
            inventory=(
                Path(inventory_raw).expanduser().resolve(strict=False)
                if inventory_raw
                else None
            ),
            large_workbooks=large_ids,
            measured_timeout_seconds=measured,
            large_timeout_seconds=large,
            evidence_item_limit_bytes=evidence_item_limit,
            evidence_global_limit_bytes=evidence_global_limit,
            memory_governor_enabled=memory_governor,
            # Absolute, like every other configured path. A relative value
            # would resolve against each lane's cwd, giving the two lanes on a
            # VM separate budget files that never see each other.
            memory_budget_dir=Path(
                os.path.abspath(
                    Path(
                        first_text(
                            "RECOVERY_MEMORY_BUDGET_DIR",
                            default=str(DEFAULT_MEMORY_BUDGET_DIR),
                        )
                    ).expanduser()
                )
            ),
            memory_host_bytes=memory_host,
            memory_reserve_bytes=memory_reserve,
            memory_verifier_bytes=memory_verifier,
            memory_io_bytes=memory_io,
            memory_unscoped_allowance_bytes=memory_unscoped,
            generation_stall_seconds=stall_seconds,
            fairness_timeout_seconds=fairness_timeout,
            lane_id=lane_id,
            lane_count=lane_count,
            min_free_disk_bytes=min_free_disk,
            disk_high_water_bytes=disk_high_water,
            disk_generation_grace_seconds=disk_grace,
            row_attempt_cap=row_attempt_cap,
        )


def _safe_id(value: object, description: str) -> str:
    result = str(value).strip()
    if not SAFE_ID_RE.fullmatch(result):
        raise RecoveryError(f"{description} is unsafe: {value!r}")
    return result


def _lane_id_from_path(path: Path) -> str:
    """Derive a stable, collision-free lane ID from a lane's state root.

    Lanes on one VM share a budget file, so two lanes deriving the same ID is
    not a cosmetic clash: each would exclude the other's reservation from its
    own accounting, see a free budget, and admit together. Trailing path
    components are not distinctive enough for that -- lane roots commonly
    differ only in a middle segment, as in .../b002/lane1/state versus
    .../b003/lane1/state -- so the ID carries a digest of the absolute path
    and keeps the last component only for readability.
    """

    resolved = os.path.abspath(str(Path(path).expanduser()))
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    parts = [part for part in Path(resolved).parts if part not in {"/", ""}]
    label = re.sub(r"[^A-Za-z0-9_-]", "-", parts[-1] if parts else "lane")
    return f"{label.strip('-')[:24] or 'lane'}-{digest}"


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def oom_score_adj_self_test(
    *, adjust: int = CHILD_OOM_SCORE_ADJ, timeout_seconds: float = 10.0
) -> dict[str, object]:
    """Prove the OOM wrapper pulls a child's ``oom_score_adj`` back to ``adjust``.

    A plain "child is not immune" check passes vacuously on any host whose
    parent already sits at 0, which is every dev box, so a silently broken
    prologue would ship. This tests the *delta*: an outer ``sh`` raises its
    own score to 500, which every unprivileged process may do, then the
    wrapper must bring the child back down. Exit 97 from the outer shell means
    the host forbids even that write; that is a skip, not a failure.

    Returns ``{"status": "ok" | "skipped" | "failed", ...}`` and never raises,
    so the caller decides how hard to fail based on whether memory scopes are
    in play.
    """

    if not Path(OOM_SCORE_ADJ_PATH).exists():
        return {"status": "skipped", "reason": "no /proc/self/oom_score_adj"}
    probe = [
        "/bin/sh",
        "-c",
        f"printf 500 > {OOM_SCORE_ADJ_PATH} 2>/dev/null || exit 97; " 'exec "$@"',
        "harbor-oom-selftest",
        *_oom_killable_command(["/bin/cat", OOM_SCORE_ADJ_PATH], adjust=adjust),
    ]
    try:
        completed = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "failed", "reason": f"probe did not run: {error!r}"}
    if completed.returncode == 97:
        return {"status": "skipped", "reason": "host forbids raising oom_score_adj"}
    observed = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return {
            "status": "failed",
            "reason": f"probe exited {completed.returncode}",
            "observed": observed,
        }
    if observed != str(int(adjust)):
        return {
            "status": "failed",
            "reason": f"child oom_score_adj is {observed or 'empty'}, want {adjust}",
            "observed": observed,
        }
    return {"status": "ok", "observed": observed}


def _validate_root_layout(config: Config) -> None:
    roots = {
        "baseline_repo": config.baseline_repo,
        "work_root": config.work_root,
        "state": config.state,
        "output": config.output,
    }
    resolved: dict[str, Path] = {}
    for name, path in roots.items():
        if path.is_symlink():
            raise RecoveryError(f"{name} root must not be a symlink")
        try:
            resolved[name] = path.resolve(strict=False)
        except OSError as error:
            raise RecoveryError(f"{name} root cannot be resolved: {error}") from error
    for left_name, left in resolved.items():
        for right_name, right in resolved.items():
            if left_name >= right_name:
                continue
            if (
                left == right
                or left.is_relative_to(right)
                or right.is_relative_to(left)
            ):
                raise RecoveryError(
                    f"{left_name} and {right_name} roots must be distinct "
                    "and pairwise non-nested"
                )


def _safe_tree_size(
    root: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
    allow_internal_symlinks: bool = False,
) -> int:
    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        raise RecoveryError(f"evidence root is unreadable: {root}") from error
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RecoveryError(f"evidence root is not a safe directory: {root}")
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise RecoveryError(f"evidence tree is unreadable: {directory}") from error
        for entry in entries:
            if entry.name in excluded_names or entry.name.endswith(".pyc"):
                continue
            mode = entry.stat(follow_symlinks=False).st_mode
            relative = Path(entry.path).relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                if not allow_internal_symlinks:
                    raise RecoveryError(
                        f"evidence tree contains a symlink: {relative}"
                    )
                target = Path(os.readlink(entry.path))
                resolved = (
                    Path(entry.path).parent / target
                ).resolve(strict=False)
                if target.is_absolute() or not resolved.is_relative_to(
                    root.resolve(strict=False)
                ):
                    raise RecoveryError(
                        f"evidence tree contains an escaping symlink: {relative}"
                    )
                total += entry.stat(follow_symlinks=False).st_size
                continue
            if stat.S_ISDIR(mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(mode):
                total += entry.stat(follow_symlinks=False).st_size
            else:
                raise RecoveryError(
                    f"evidence tree contains a special file: {relative}"
                )
    return total


def _evidence_usage(state: Path) -> int:
    total = 0
    for name in (
        "failed-releases",
        "worktree-quarantine",
        "candidates",
        "delivery-quarantine",
    ):
        root = state / name
        if not root.exists() or root.is_symlink() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                total += path.stat().st_size
    return total


def _check_evidence_capacity(
    config: Config,
    size_bytes: int,
    *,
    existing_bytes: int = 0,
) -> None:
    if size_bytes > config.evidence_item_limit_bytes:
        raise RecoveryError(
            f"evidence item is {size_bytes} bytes, above configured limit "
            f"{config.evidence_item_limit_bytes}"
        )
    projected = _evidence_usage(config.state) - existing_bytes + size_bytes
    if projected > config.evidence_global_limit_bytes:
        raise RecoveryError(
            f"evidence total would be {projected} bytes, above configured limit "
            f"{config.evidence_global_limit_bytes}"
        )


def read_queue(path: Path) -> list[QueueRow]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"cannot read recovery queue: {error}") from error
    if isinstance(payload, dict):
        for key in ("rows", "records", "queue"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise RecoveryError("recovery queue must be a list or contain rows")
    rows = [QueueRow.parse(row, queue_dir=path.parent) for row in payload]
    seen: set[str] = set()
    for row in rows:
        if row.workbook_id in seen:
            raise RecoveryError(
                f"duplicate workbook ID in recovery queue: {row.workbook_id}"
            )
        seen.add(row.workbook_id)
    return rows


def _baseline_records(payload: object) -> Iterable[tuple[str, object]]:
    if not isinstance(payload, dict):
        raise RecoveryError("code baseline must be a JSON object")
    for key in ("files", "artifacts", "records"):
        records = payload.get(key)
        if isinstance(records, dict):
            yield from records.items()
            return
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict) or "path" not in record:
                    raise RecoveryError("code baseline record lacks a path")
                yield str(record["path"]), record
            return
    raise RecoveryError("code baseline has no files, artifacts, or records")


def _editable_pipeline_path(relative: str) -> bool:
    """xl_seg implementation, source modules, skills, naturalization."""

    normalized = Path(relative).as_posix()
    name = Path(relative).name.casefold()
    if normalized.startswith("xl_seg/"):
        return not _protected_basename(name)
    if name.startswith("xl_source") and name.endswith(".py"):
        return not _protected_basename(name)
    if name in {"xl_inventory_approval.py", "xl_segment.py"}:
        return not _protected_basename(name)
    if "naturaliz" in normalized.casefold():
        return not _protected_basename(name)
    if normalized.startswith(".cursor/skills/"):
        return not _protected_basename(name)
    return False


def _protected_basename(name: str) -> bool:
    folded = name.casefold()
    return (
        "grader" in folded
        or "rubric" in folded
        or "answer_key" in folded
        or "answer-key" in folded
        or (
            "threshold" in folded
            and ("acceptance" in folded or folded in {"thresholds.json"})
        )
    )


def _protected_name(relative: str) -> bool:
    if _editable_pipeline_path(relative):
        return False
    return _protected_basename(Path(relative).name)


def _is_generated_root(name: str) -> bool:
    if name in GENERATED_ROOTS:
        return True
    return any(name.startswith(root + "_") for root in GENERATED_ROOTS)


def _ignored_snapshot_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & ({".git", ".venv"} | TOOL_CACHE_DIRS):
        return True
    if path.parts and _is_generated_root(path.parts[0]):
        return True
    return path.suffix.casefold() == ".pyc"


def read_code_baseline(path: Path, repo: Path) -> dict[str, BaselineEntry]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"cannot read code baseline: {error}") from error
    result: dict[str, BaselineEntry] = {}
    for raw_path, value in _baseline_records(payload):
        if isinstance(value, str):
            sha256 = value
            explicit_protected = False
        elif isinstance(value, dict):
            sha256 = value.get("sha256")
            explicit_protected = value.get("protected") is True
        else:
            raise RecoveryError(f"invalid baseline record for {raw_path}")
        relative = Path(str(raw_path))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in {"", "."}
        ):
            raise RecoveryError(f"unsafe code baseline path: {raw_path!r}")
        normalized = relative.as_posix()
        if not isinstance(sha256, str) or not HASH_RE.fullmatch(sha256):
            raise RecoveryError(f"invalid baseline SHA-256 for {normalized}")
        if normalized in result:
            raise RecoveryError(f"duplicate code baseline path: {normalized}")
        candidate = repo / normalized
        if candidate.is_symlink():
            raise RecoveryError(f"baseline path is a symlink: {normalized}")
        protected = _protected_name(normalized)
        if explicit_protected and not _editable_pipeline_path(normalized):
            protected = True
        result[normalized] = BaselineEntry(
            path=normalized,
            sha256=sha256,
            protected=protected,
        )
    if not result:
        raise RecoveryError("code baseline is empty")
    return result


def _protected_snapshot(
    repo: Path, baseline: Mapping[str, BaselineEntry]
) -> dict[str, str]:
    paths = {
        relative
        for relative, entry in baseline.items()
        if entry.protected and not _ignored_snapshot_path(Path(relative))
    }
    if repo.is_dir() and not repo.is_symlink():
        for path in repo.rglob("*"):
            relative = path.relative_to(repo)
            if _ignored_snapshot_path(relative):
                continue
            if not (path.is_file() or path.is_symlink()):
                continue
            posix = relative.as_posix()
            if _protected_name(posix):
                paths.add(posix)
    snapshot: dict[str, str] = {}
    for relative in sorted(paths):
        path = repo / relative
        if path.is_symlink():
            snapshot[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            snapshot[relative] = sha256_file(path)
        else:
            snapshot[relative] = "missing"
    return snapshot


def _assert_frozen_protected_baseline(
    snapshot: Mapping[str, str],
    baseline: Mapping[str, BaselineEntry],
) -> None:
    drift = {
        path: {"expected": entry.sha256, "actual": snapshot.get(path, "missing")}
        for path, entry in baseline.items()
        if (
            entry.protected
            and not _ignored_snapshot_path(Path(path))
            and snapshot.get(path) != entry.sha256
        )
    }
    if drift:
        raise PolicyViolation(
            "protected files do not match CODE_BASELINE: "
            + json.dumps(drift, sort_keys=True)
        )


def _assert_same_snapshot(
    expected: Mapping[str, str],
    actual: Mapping[str, str],
    description: str,
) -> None:
    if dict(expected) != dict(actual):
        changed = sorted(set(expected) | set(actual))
        changed = [
            path for path in changed if expected.get(path) != actual.get(path)
        ]
        raise PolicyViolation(f"{description} drift: {changed}")


def _assert_source_hash(row: QueueRow, expected: str, phase: str) -> None:
    try:
        actual = sha256_file(row.run_source_path)
    except OSError as error:
        raise PolicyViolation(f"source drift {phase}: {error}") from error
    if actual != expected:
        raise PolicyViolation(f"source drift {phase}")


def _validate_source(row: QueueRow) -> None:
    source = row.run_source_path
    if source.is_symlink() or not source.is_file():
        raise RecoveryError(
            f"run source for {row.workbook_id} is missing or is a symlink"
        )
    if source.suffix.casefold() != ".xlsx":
        raise RecoveryError(
            f"run source for {row.workbook_id} must be the approved XLSX"
        )
    actual_hash = sha256_file(source)
    if actual_hash != row.run_source_sha256:
        raise PolicyViolation(f"run source hash drift for {row.workbook_id}")
    if row.source_format == "xlsx" and (
        row.source_sha256 != row.run_source_sha256
        or row.source_size != source.stat().st_size
    ):
        raise RecoveryError(
            f"native source metadata does not bind {row.workbook_id}"
        )


def _walk_dicts(value: object) -> Iterable[dict[str, Any]]:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            yield current
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _select_approval(registry: Path, approval_batch_id: str) -> dict[str, Any]:
    if not registry.is_file():
        raise RecoveryOrchestrationError(
            f"inventory approval registry is unavailable: {registry}"
        )
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryOrchestrationError(
            f"cannot read inventory approval registry: {error}"
        ) from error
    approvals = payload.get("approvals") if isinstance(payload, dict) else None
    if not isinstance(approvals, list):
        raise RecoveryOrchestrationError(
            "inventory approval registry has no approvals"
        )
    matching = [
        value
        for value in approvals
        if isinstance(value, dict)
        and value.get("batch_id") == approval_batch_id
    ]
    if not matching:
        raise RecoveryOrchestrationError(
            f"selected approved inventory batch is absent: {approval_batch_id}"
        )
    if len(matching) != 1:
        raise RecoveryOrchestrationError(
            f"ambiguous approved inventory batch {approval_batch_id}"
        )
    approval = matching[0]
    inventory_hash = approval.get("inventory_sha256")
    if not isinstance(inventory_hash, str) or not HASH_RE.fullmatch(inventory_hash):
        raise RecoveryOrchestrationError("approved inventory hash is invalid")
    inventory_artifact_hash = approval.get("inventory_artifact_sha256")
    if (
        not isinstance(inventory_artifact_hash, str)
        or not HASH_RE.fullmatch(inventory_artifact_hash)
    ):
        raise RecoveryOrchestrationError(
            "approved inventory artifact hash is invalid"
        )
    if not isinstance(approval.get("batch_source_ledger"), list):
        raise RecoveryOrchestrationError(
            "selected approval has no batch source ledger"
        )
    return approval


def _approval_for_row(
    approval: Mapping[str, Any], row: QueueRow
) -> tuple[str, Mapping[str, Any]] | None:
    inventory_hash = approval["inventory_sha256"]
    ledger = approval.get("batch_source_ledger")
    records = [
        value
        for value in ledger
        if isinstance(value, dict)
        and value.get("workbook_id") == row.workbook_id
    ]
    if not records:
        return None
    if len(records) != 1:
        raise PolicyViolation(
            f"selected approval ambiguously lists workbook {row.workbook_id}"
        )
    record = records[0]
    original = record.get("original_source")
    original = original if isinstance(original, dict) else {}
    original_path = str(original.get("path", ""))
    if (
        record.get("source_sha256") != row.run_source_sha256
        or original.get("sha256") != row.source_sha256
        or original.get("size_bytes") != row.source_size
        or Path(original_path).suffix.casefold().removeprefix(".")
        != row.source_format
    ):
        raise PolicyViolation(
            f"approved inventory metadata does not exactly bind {row.workbook_id}"
        )
    return inventory_hash, approval


def _load_pinned_inventory(
    config: Config, inventory_artifact_hash: str
) -> tuple[Path, object]:
    if config.inventory is not None:
        candidates = [config.inventory]
    else:
        manifest_root = config.baseline_repo / "verification_manifests"
        candidates = (
            sorted(manifest_root.glob("*.json"))
            if manifest_root.is_dir()
            else []
        )
    for candidate in dict.fromkeys(candidates):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            content = candidate.read_bytes()
        except OSError:
            continue
        if _sha256_bytes(content) != inventory_artifact_hash:
            continue
        try:
            return candidate, json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RecoveryOrchestrationError(
                f"pinned approved inventory artifact is invalid: {candidate}"
            ) from error
    raise RecoveryOrchestrationError(
        "pinned approved inventory artifact is missing or hash-invalid"
    )


def _inventory_contains_row(payload: object, row: QueueRow) -> bool | None:
    records = [
        record
        for record in _walk_dicts(payload)
        if record.get("workbook_id") == row.workbook_id
    ]
    if not records:
        return None
    if len(records) != 1:
        raise PolicyViolation(
            f"pinned approved inventory ambiguously lists {row.workbook_id}"
        )
    if records[0].get("sha256") != row.run_source_sha256:
        raise PolicyViolation(
            "pinned approved inventory run-source hash does not match "
            f"{row.workbook_id}"
        )
    return True


def resolve_approved_inventory(
    config: Config,
    row: QueueRow,
    *,
    selected_approval: Mapping[str, Any] | None = None,
) -> Path | None:
    selected = (
        selected_approval
        if selected_approval is not None
        else _select_approval(
            config.inventory_registry, config.approval_batch_id
        )
    )
    inventory_artifact_hash = selected["inventory_artifact_sha256"]
    inventory, payload = _load_pinned_inventory(
        config, inventory_artifact_hash
    )
    if _inventory_contains_row(payload, row) is None:
        return None
    if _approval_for_row(selected, row) is None:
        raise PolicyViolation(
            f"selected approval does not bind inventory workbook {row.workbook_id}"
        )
    return inventory


def ensure_inventory(
    row: QueueRow,
    config: Config,
    *,
    selected_approval: Mapping[str, Any] | None = None,
) -> Path:
    """Resolve a pinned inventory that contains ``row``.

    Absence is an orchestration error. The launcher repairs inventories.
    The claiming agent must not treat this as FULL_RERUN_BLOCKER: HARD.
    """

    inventory = resolve_approved_inventory(
        config, row, selected_approval=selected_approval
    )
    if inventory is None:
        raise RecoveryOrchestrationError(
            f"workbook {row.workbook_id} is absent from the pinned approved "
            "inventory; this is an orchestration error, not a workbook HARD "
            "blocker. Repair inventories before retrying."
        )
    return inventory


def _evidence_changed_paths(evidence: Mapping[str, object] | None) -> list[str]:
    """Every path the a.md gate will require to be named."""

    if evidence is None:
        return []
    changed: set[str] = set()
    diff = evidence.get("baseline_diff")
    if isinstance(diff, Mapping):
        changed.update(str(key) for key in diff)
    during = evidence.get("changed_during_run")
    if isinstance(during, (list, tuple)):
        changed.update(str(item) for item in during)
    return sorted(item for item in changed if item)


def _worker_build_id() -> str:
    """Identity of the worker build actually loaded, for worker_fix_needed parking.

    A row parked on a controller defect stays parked only while the same
    build is running. The id hashes every batch module the worker imports,
    not just this file, so a lane-local overlay that fixes hardened_runner
    or the governor releases the row as well. A constant here would need a
    hand bump for every fix; the lane fix step (scripts/batch/lane_fix.sh)
    cannot rely on that.
    """

    digest = hashlib.sha256()
    for module_name in (
        "scripts.batch.recovery_worker",
        "scripts.batch.hardened_runner",
        "scripts.batch.memory_governor",
        "scripts.batch.disk_reaper",
    ):
        module = sys.modules.get(module_name)
        path = getattr(module, "__file__", None) if module is not None else None
        if path is None and module_name == "scripts.batch.recovery_worker":
            path = __file__
        if not path:
            digest.update(module_name.encode("utf-8"))
            continue
        try:
            digest.update(Path(path).read_bytes())
        except OSError:
            digest.update(module_name.encode("utf-8"))
    return digest.hexdigest()


WORKER_OVERLAY_ID = _worker_build_id()


def _copytree_ignore(directory: str, names: list[str]) -> list[str]:
    ignored: list[str] = []
    for name in names:
        if (
            name in COPYTREE_EXCLUDE
            or _is_generated_root(name)
            or name.endswith(".pyc")
        ):
            ignored.append(name)
    return ignored


def _git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    head = result.stdout.strip()
    return head if result.returncode == 0 and HASH_RE.fullmatch(head) else "unversioned"


def _baseline_hash(baseline: Mapping[str, BaselineEntry]) -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {
                path: {"sha256": entry.sha256, "protected": entry.protected}
                for path, entry in sorted(baseline.items())
            }
        )
    )


def _cleanup_generated_roots(repo: Path) -> None:
    if not repo.is_dir() or repo.is_symlink():
        return
    for child in repo.iterdir():
        if not _is_generated_root(child.name):
            continue
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child)


def create_isolated_worktree(
    baseline_repo: Path,
    work_root: Path,
    workbook_id: str,
) -> Path:
    """Create a fresh checkout from the shared baseline."""

    workbook_id = _safe_id(workbook_id, "workbook ID")
    work_root.mkdir(parents=True, exist_ok=True)
    destination = work_root / f"task-{workbook_id}"
    if destination.exists() or destination.is_symlink():
        recycle_isolated_worktree(destination, baseline_repo)
    if _git_head(baseline_repo) != "unversioned":
        result = subprocess.run(
            [
                "git",
                "-C",
                str(baseline_repo),
                "worktree",
                "add",
                "--detach",
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and destination.is_dir():
            _cleanup_generated_roots(destination)
            _link_baseline_runtime(baseline_repo, destination)
            atomic_write_text(destination / ".recovery-worktree", "git\n")
            return destination
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(
        baseline_repo,
        destination,
        symlinks=False,
        ignore=_copytree_ignore,
    )
    _cleanup_generated_roots(destination)
    _link_baseline_runtime(baseline_repo, destination)
    atomic_write_text(destination / ".recovery-worktree", "copy\n")
    return destination


def _link_baseline_runtime(baseline_repo: Path, destination: Path) -> None:
    """Share the baseline venv and env file without copying them."""

    for name in (".venv", ".env"):
        source = baseline_repo / name
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        if source.exists() or source.is_symlink():
            target.symlink_to(source)


def recycle_isolated_worktree(path: Path, baseline_repo: Path) -> None:
    """Delete a per-task worktree so the next task sees unchanged baseline code."""

    if not path.exists() and not path.is_symlink():
        return
    kind = ""
    marker = path / ".recovery-worktree"
    if marker.is_file():
        try:
            kind = marker.read_text(encoding="utf-8").strip()
        except OSError:
            kind = ""
    if kind == "git" or _git_head(baseline_repo) != "unversioned":
        subprocess.run(
            [
                "git",
                "-C",
                str(baseline_repo),
                "worktree",
                "remove",
                "--force",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _tree_records(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError(f"tree is missing or unsafe: {root}")
    records: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RecoveryError(f"unsafe bundle artifact: {relative}")
        if stat.S_ISREG(mode):
            records[relative] = memo_sha256_file(path)
    return records


def tree_sha256_from_records(records: Mapping[str, str]) -> str:
    """Hash a tree from digests a caller already computed.

    Lets one operation validate a bundle and hash it without walking it twice,
    which also makes the bundle hash provably describe the same bytes the
    validation just checked.
    """

    return _sha256_bytes(_canonical_bytes(dict(records)))


def tree_sha256(root: Path) -> str:
    return tree_sha256_from_records(_tree_records(root))


def _grader_records(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise PolicyViolation(f"canonical grader directory is missing: {root}")
    records: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            TOOL_CACHE_DIRS & set(relative.parts)
            or path.suffix.casefold() == ".pyc"
        ):
            continue
        if path.is_symlink():
            raise PolicyViolation(
                f"grader provenance contains a symlink: {relative.as_posix()}"
            )
        if path.is_file():
            records[relative.as_posix()] = sha256_file(path)
    return records


def assert_canonical_grader_provenance(repo: Path, task_dir: Path) -> None:
    canonical_runner = repo / "grader" / "run_grader.py"
    candidate_runner = task_dir / "tests" / "run_grader.py"
    if (
        canonical_runner.is_symlink()
        or candidate_runner.is_symlink()
        or not canonical_runner.is_file()
        or not candidate_runner.is_file()
        or canonical_runner.read_bytes() != candidate_runner.read_bytes()
    ):
        raise PolicyViolation(
            "candidate tests/run_grader.py does not byte-match canonical "
            "grader/run_grader.py"
        )
    canonical_finance = repo / "grader" / "finance_grader"
    candidate_finance = task_dir / "tests" / "finance_grader"
    if _grader_records(canonical_finance) != _grader_records(candidate_finance):
        raise PolicyViolation(
            "candidate tests/finance_grader does not byte-match canonical "
            "grader/finance_grader"
        )


def _parse_inspect_output(
    stdout: str, stderr: str, returncode: int
) -> dict[str, object]:
    payloads: list[dict[str, object]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "ok" in data:
            payloads.append(data)
    if payloads:
        data = payloads[-1]
        if data.get("ok") is True:
            return data
        raise RecoveryError(str(data.get("error") or "inspect failed"))
    detail = (stderr or stdout or "").strip() or f"inspect exited {returncode}"
    raise RecoveryError(detail)


def _same_or_below(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except OSError:
        return False


def repo_snapshot(repo: Path, *, excluded: Sequence[Path]) -> dict[str, str]:
    excluded_resolved = [path.resolve(strict=False) for path in excluded]
    records: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if any(_same_or_below(path, root) for root in excluded_resolved):
            continue
        relative_path = path.relative_to(repo)
        if any(
            part in {".git", ".venv"} or part in TOOL_CACHE_DIRS
            for part in relative_path.parts
        ):
            continue
        if path.is_symlink():
            records[relative_path.as_posix()] = "symlink:" + os.readlink(path)
        elif path.is_file():
            records[relative_path.as_posix()] = memo_sha256_file(path)
    return records


def _code_state(
    repo: Path, baseline: Mapping[str, BaselineEntry], workbook_id: str
) -> dict[str, str]:
    paths = set(baseline)
    for path in repo.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(repo)
        if relative.as_posix() in {
            ".recovery-worktree",
            ".recovery-worktree.json",
        }:
            continue
        if relative.parts and _is_generated_root(relative.parts[0]):
            continue
        if any(
            part in {".git", ".venv"} or part in TOOL_CACHE_DIRS
            for part in relative.parts
        ):
            continue
        if path.suffix.casefold() in CODE_SUFFIXES:
            paths.add(relative.as_posix())
    generated_roots = [
        child
        for child in repo.iterdir()
        if child.is_dir() and not child.is_symlink() and _is_generated_root(child.name)
    ]
    for generated_root in generated_roots:
        for child_name in (workbook_id, f"{workbook_id}-outputs"):
            root = generated_root / child_name
            for relative in ("curation.toml", "instruction.md"):
                candidate = root / relative
                if candidate.is_file() or candidate.is_symlink():
                    paths.add(candidate.relative_to(repo).as_posix())
    result: dict[str, str] = {}
    for relative in sorted(paths):
        path = repo / relative
        if path.is_symlink():
            result[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[relative] = sha256_file(path)
        else:
            result[relative] = "missing"
    return result


def _baseline_diff(
    state: Mapping[str, str], baseline: Mapping[str, BaselineEntry]
) -> dict[str, dict[str, str]]:
    changed: dict[str, dict[str, str]] = {}
    for path, entry in baseline.items():
        actual = state.get(path, "missing")
        if actual != entry.sha256:
            changed[path] = {"baseline": entry.sha256, "current": actual}
    for path, actual in state.items():
        if path not in baseline and actual != "missing":
            changed[path] = {"baseline": "absent", "current": actual}
    return changed


def _write_vm_diff(
    repo: Path,
    baseline_repo: Path,
    state_dir: Path,
    workbook_id: str,
    modification_evidence: Mapping[str, object],
) -> Path:
    raw_changes = modification_evidence.get("baseline_diff", {})
    if not isinstance(raw_changes, Mapping):
        raise RecoveryError("modification evidence has no baseline diff")

    def read_diff_file(root: Path, relative_text: str) -> bytes | None:
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in {"", "."}
            or any(
                part in {".git", ".venv"}
                or part in TOOL_CACHE_DIRS
                or part == ".env"
                or part.startswith(".env.")
                for part in relative.parts
            )
        ):
            raise PolicyViolation(f"unsafe VM diff path: {relative_text!r}")
        candidate = root
        for part in relative.parts:
            candidate = candidate / part
            if not candidate.exists() and not candidate.is_symlink():
                return None
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise PolicyViolation(
                    f"VM diff path contains a symlink: {relative.as_posix()}"
                )
        mode = candidate.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise PolicyViolation(
                f"VM diff path is not a regular file: {relative.as_posix()}"
            )
        return candidate.read_bytes()

    sections: list[str] = []
    for raw_relative in sorted(str(path) for path in raw_changes):
        details = raw_changes[raw_relative]
        if not isinstance(details, Mapping):
            raise RecoveryError(f"invalid baseline diff record: {raw_relative}")
        before = read_diff_file(baseline_repo, raw_relative)
        after = read_diff_file(repo, raw_relative)
        expected_before = details.get("baseline")
        expected_after = details.get("current")
        if (
            before is not None
            and isinstance(expected_before, str)
            and HASH_RE.fullmatch(expected_before)
            and _sha256_bytes(before) != expected_before
        ):
            raise PolicyViolation(
                f"VM diff baseline content changed: {raw_relative}"
            )
        if (
            after is not None
            and isinstance(expected_after, str)
            and HASH_RE.fullmatch(expected_after)
            and _sha256_bytes(after) != expected_after
        ):
            raise PolicyViolation(
                f"VM diff worktree content changed: {raw_relative}"
            )
        try:
            before_text = "" if before is None else before.decode("utf-8")
            after_text = "" if after is None else after.decode("utf-8")
        except UnicodeDecodeError:
            sections.append(
                f"Binary change {raw_relative}: "
                f"before_sha256={_sha256_bytes(before) if before is not None else 'absent'} "
                f"after_sha256={_sha256_bytes(after) if after is not None else 'absent'}\n"
            )
            continue
        sections.extend(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{raw_relative}",
                tofile=f"b/{raw_relative}",
            )
        )

    destination = state_dir / "verification-inputs" / workbook_id / "vm-code-diff.txt"
    content = (
        "CODE_BASELINE differences:\n"
        + json.dumps(modification_evidence, indent=2, sort_keys=True)
        + "\n\nVerified baseline-to-worktree content diff:\n"
        + "".join(sections)
    )
    atomic_write_text(destination, content)
    return destination


def _amd_command_lines(text: str) -> list[str]:
    """Lines of a.md that present themselves as commands.

    Fenced code blocks and lines that open with a shell prompt or an
    interpreter are the places the fairness verifier reads as "the commands
    that were run", so those are the lines held to the no-abbreviation rule.
    """

    commands: list[str] = []
    fenced = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            if line:
                commands.append(line)
            continue
        # Agents emit commands as "- `python3 ...`" bullets and
        # as inline code; the verifier reads those as the commands run.
        candidate = _AMD_LIST_MARKER.sub("", line, count=1).strip()
        inline = _AMD_INLINE_CODE.fullmatch(candidate)
        if inline:
            candidate = inline.group(1).strip()
        if AMD_COMMAND_LINE.match(candidate):
            commands.append(candidate)
    return commands


def validate_recovery_amd(
    task_dir: Path, evidence: Mapping[str, object] | None = None
) -> None:
    """Fail closed on an a.md the fairness verifier would reject anyway.

    The keyword check alone let through records that named the right sections
    and still failed fairness on paperwork: a changed file the diff showed
    but the record never mentioned, or a command written as
    ``python xl_seg/... --workbook 0568`` that nobody could rerun. Catching
    those here costs seconds; the verifier costs a fresh agent, and its FAIL
    used to cost an investigator and a parked row on top.
    """

    path = task_dir / "a.md"
    if not path.is_file() or path.is_symlink():
        raise RecoveryError("recovered task has no final a.md")
    text = path.read_text(encoding="utf-8")
    missing = [term for term in AMD_REQUIRED if re.search(term, text, re.I) is None]
    # Only a cone that lost outputs has to account for them; a clean cone has
    # nothing to say and must not fail on a term that does not apply.
    retention_record = _bundle_output_retention(task_dir)
    if retention_record is not None:
        bands = retention_record.get("bands") or {}
        dropped = [
            str(item.get("band"))
            for key in ("pruned", "manual_excluded")
            for item in (bands.get(key) or ())
            if isinstance(item, Mapping) and item.get("band")
        ]
        if dropped:
            if re.search(r"pruned|excluded", text, re.I) is None:
                missing.append("pruned/excluded outputs")
            unnamed = [band for band in dropped if band not in text]
            if unnamed:
                missing.append(
                    "dropped output bands not named: " + ", ".join(unnamed[:10])
                )
    problems: list[str] = []
    if len(text.strip()) < 200 or missing:
        # Report keyword defects together with the path and command
        # defects below, so one retry can fix all of them.
        problems.append(
            "missing: " + ", ".join(missing or ["substantive content"])
        )
    if evidence is not None and str(evidence.get("agent_declaration", "")) == "MISSING":
        problems.append(
            "final response did not end with a standalone declaration line "
            "(write exactly RECOVERY_USED: YES as the last line of your final message)"
        )
    if evidence is not None:
        changed: set[str] = set()
        diff = evidence.get("baseline_diff")
        if isinstance(diff, Mapping):
            changed.update(str(key) for key in diff)
        during = evidence.get("changed_during_run")
        if isinstance(during, (list, tuple)):
            changed.update(str(item) for item in during)
        unmentioned = sorted(item for item in changed if item and item not in text)
        if unmentioned:
            problems.append(
                "changed files not named in a.md: " + ", ".join(unmentioned)
            )
    abbreviated = [
        line
        for line in _amd_command_lines(text)
        if "..." in line or "\u2026" in line
    ]
    if abbreviated:
        problems.append(
            "abbreviated command lines (write the exact command): "
            + " | ".join(abbreviated[:5])
        )
    if problems:
        raise RecoveryError(
            "recovered task a.md is not a complete recovery record; "
            + "; ".join(problems)
        )


def _bundle_output_retention(task_dir: Path) -> dict[str, object] | None:
    """The generation's ``output_retention`` record as copied into the bundle.

    ``xl_output_task`` copies the segmentation generation manifest into
    ``tests/``, and the manifest carries the record derived from the staged
    ``curation.toml``. ``None`` when the bundle predates the record.
    """

    path = Path(task_dir) / "tests" / "segmentation_generation_manifest.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    record = manifest.get("output_retention") if isinstance(manifest, dict) else None
    return dict(record) if isinstance(record, Mapping) else None


def _ooxml_evidence(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PolicyViolation(f"workbook evidence path is missing or unsafe: {path}")
    evidence: dict[str, object] = {
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    try:
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            names = [member.filename for member in members]
    except (OSError, zipfile.BadZipFile):
        evidence["valid_ooxml"] = False
        return evidence
    if len(names) != len(set(names)):
        raise PolicyViolation(f"workbook evidence has duplicate OOXML members: {path}")
    evidence.update(
        {
            "valid_ooxml": True,
            "member_count": len(members),
            "expanded_bytes": sum(member.file_size for member in members),
        }
    )
    return evidence


def _technical_evidence_digest(payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body.pop("evidence_sha256", None)
    return _sha256_bytes(_canonical_bytes(body))


def fairness_passed(report: str) -> bool:
    lines = [line.rstrip() for line in report.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == "FAIRNESS_VERDICT: PASS"


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", re.DOTALL)
_CHECK_VALUES = frozenset({"pass", "fail", "not_applicable"})


def parse_fairness_block(report: str) -> dict[str, object] | None:
    """The structured ``recovery-fairness/v1`` block from a verifier report.

    The verifier is told to end its report with a fenced JSON object naming
    a verdict, one value per check, and its findings. Prose stays for the
    human; this block is what the worker and the fleet report act on. The
    last block with the right schema wins. Returns ``None`` when no
    well-formed block exists; callers treat that as a verifier error.
    """

    candidates: list[str] = [match.group(1) for match in _JSON_FENCE_RE.finditer(report or "")]
    if not candidates:
        # Tolerate an unfenced object as the report's last paragraph.
        tail = (report or "").strip().rsplit("\n\n", 1)[-1].strip()
        if tail.startswith("{") and tail.endswith("}"):
            candidates.append(tail)
    for text in reversed(candidates):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict):
            continue
        if loaded.get("schema_version") != FAIRNESS_BLOCK_SCHEMA:
            continue
        verdict = str(loaded.get("verdict", "")).upper()
        checks = loaded.get("checks")
        if verdict not in {"PASS", "FAIL"} or not isinstance(checks, Mapping):
            return None
        normalized_checks: dict[str, str] = {}
        for name in FAIRNESS_CHECKS:
            value = str(checks.get(name, "")).lower()
            if value not in _CHECK_VALUES:
                return None
            normalized_checks[name] = value
        findings_raw = loaded.get("findings", [])
        if not isinstance(findings_raw, list):
            return None
        findings: list[dict[str, object]] = []
        for item in findings_raw:
            if not isinstance(item, Mapping):
                return None
            check = str(item.get("check", ""))
            if check not in FAIRNESS_CHECKS:
                return None
            gate = item.get("gate")
            findings.append(
                {
                    "check": check,
                    "subclass": re.sub(r"[^a-z0-9_]+", "_", str(item.get("subclass", "")).lower()).strip("_")
                    or "unspecified",
                    "gate": int(gate) if isinstance(gate, int) and not isinstance(gate, bool) and 1 <= gate <= 16 else None,
                    "artifact": str(item.get("artifact", ""))[:300],
                    "detail": str(item.get("detail", ""))[:1000],
                }
            )
        return {
            "schema_version": FAIRNESS_BLOCK_SCHEMA,
            "verdict": verdict,
            "checks": normalized_checks,
            "findings": findings,
        }
    return None


def fairness_accepted(report: str) -> tuple[bool, dict[str, object] | None, str | None]:
    """``(accepted, block, verifier_error)`` for one fairness report.

    Accepted means the last-line verdict is PASS, the JSON block says PASS,
    and no check says fail. Anything the two forms disagree on, and any
    missing or malformed block, is a verifier error, not a task verdict: the
    caller reruns the verifier once and otherwise records ``verifier_error``
    rather than sending the generation agent after a phantom defect.
    """

    block = parse_fairness_block(report)
    line_pass = fairness_passed(report)
    # Last-line rule, same as PASS: the token quoted in prose is not a verdict.
    lines = [line.rstrip() for line in (report or "").splitlines() if line.strip()]
    line_fail = bool(lines) and lines[-1] == "FAIRNESS_VERDICT: FAIL"
    if block is None:
        return False, None, "missing_block"
    block_fail = block["verdict"] == "FAIL" or any(
        value == "fail" for value in block["checks"].values()
    )
    if block["verdict"] == "FAIL" and not any(value == "fail" for value in block["checks"].values()):
        return False, block, "fail_without_failing_check"
    if block["verdict"] == "PASS" and block_fail:
        return False, block, "pass_with_failing_check"
    if line_pass and block_fail:
        return False, block, "line_block_disagreement"
    if line_fail and not block_fail:
        return False, block, "line_block_disagreement"
    if not line_pass and not line_fail:
        return False, block, "missing_verdict_line"
    return (line_pass and not block_fail), block, None


class VerifierError(RecoveryError):
    """The fairness verifier's output could not be trusted (not a task verdict)."""

    def __init__(self, reason: str, report: str, attempt: Mapping[str, object] | None):
        super().__init__(f"fairness verifier output unusable: {reason}")
        self.reason = reason
        self.report = report
        self.attempt = dict(attempt) if attempt is not None else None


def incident_worker_fix_needed(report: str) -> bool:
    lines = [line.rstrip() for line in report.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == "INCIDENT_VERDICT: WORKER_FIX_NEEDED"


def parse_incident_verdict(report: str) -> dict[str, object] | None:
    """The structured ``recovery-incident-verdict/v1`` block from an investigator report.

    ``route`` is what the worker acts on: ``controller`` parks the row for
    the lane fix step; every other route keeps it task-side. The verdict
    line and the route must agree (``controller`` <=> WORKER_FIX_NEEDED);
    a disagreeing or malformed block is unusable and the row stays
    task-side, which is the direction that keeps the queue moving.
    """

    candidates = [match.group(1) for match in _JSON_FENCE_RE.finditer(report or "")]
    for text in reversed(candidates):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict) or loaded.get("schema_version") != INCIDENT_VERDICT_SCHEMA:
            continue
        verdict = str(loaded.get("verdict", "")).upper()
        route = str(loaded.get("route", "")).lower()
        if verdict not in {"WORKER_FIX_NEEDED", "TASK_LOCAL_OR_EXTERNAL"}:
            return None
        if route not in INCIDENT_ROUTES:
            return None
        if (route == "controller") != (verdict == "WORKER_FIX_NEEDED"):
            return None
        gate = loaded.get("gate")
        evidence = loaded.get("evidence", [])
        return {
            "schema_version": INCIDENT_VERDICT_SCHEMA,
            "verdict": verdict,
            "route": route,
            "subclass": re.sub(r"[^a-z0-9_]+", "_", str(loaded.get("subclass", "")).lower()).strip("_")
            or "unspecified",
            "gate": int(gate) if isinstance(gate, int) and not isinstance(gate, bool) and 1 <= gate <= 16 else None,
            "cause": str(loaded.get("cause", ""))[:600],
            "component": str(loaded.get("component", ""))[:200],
            "evidence": [str(item)[:300] for item in evidence][:20] if isinstance(evidence, list) else [],
            "fix_hint": str(loaded.get("fix_hint", ""))[:600],
        }
    return None


def incident_verdict(report: str) -> tuple[dict[str, object] | None, bool, str | None]:
    """``(block, worker_fix_needed, verdict_error)`` for one investigator report.

    worker_fix_needed is True only when the last line says WORKER_FIX_NEEDED
    and a usable block routes to ``controller``. A last line that says
    WORKER_FIX_NEEDED with no usable block is recorded as a verdict error
    and treated task-side: parking a row on an unexplained verdict would
    stall it for a fix step that has nothing to fix.
    """

    block = parse_incident_verdict(report)
    line_fix = incident_worker_fix_needed(report)
    lines = [line.rstrip() for line in (report or "").splitlines() if line.strip()]
    line_present = bool(lines) and lines[-1].startswith("INCIDENT_VERDICT:")
    if block is None:
        return None, False, "missing_block" if line_present else "missing_verdict"
    if line_fix != (block["route"] == "controller"):
        return block, False, "line_block_disagreement"
    if not line_present:
        return block, False, "missing_verdict_line"
    return block, line_fix, None


def verdict_transcript(segments: Sequence[str], token: str, fallback: str) -> str:
    """Transcript text on which a last-line verdict rule should be judged.

    An agent often follows its verdict message with a short acknowledgement
    ("No follow-up is needed"). Judged on the flat transcript that trailing
    message would fail an otherwise clean PASS. The rule the verifiers rely on
    is: take the last assistant message that mentions the token and require
    the token to be that message's last line. So the transcript is cut after
    that message; qualifications inside the same message still follow the
    token and still fail, and a FAIL anywhere still counts because callers
    search the whole text for it.
    """

    for index in range(len(segments) - 1, -1, -1):
        if token in segments[index]:
            return "\n".join(
                segment.strip("\n") for segment in segments[: index + 1]
            )
    return fallback


def _combine_fairness_reports(stdout_text: str, file_text: str) -> str:
    """Fail-closed merge of agent stdout and the written fairness file."""

    stdout_text = stdout_text or ""
    file_text = file_text or ""
    if "FAIRNESS_VERDICT: FAIL" in stdout_text:
        return stdout_text
    if "FAIRNESS_VERDICT: FAIL" in file_text:
        return file_text
    if fairness_passed(stdout_text):
        return stdout_text
    if fairness_passed(file_text):
        return file_text
    return stdout_text or file_text


def _preserve_failed_release(
    state: Path,
    row: QueueRow,
    worktree: Path,
    config: Config | None = None,
) -> Path | None:
    """Atomically publish a safe, versioned failed-release snapshot."""

    release_src = worktree / "release_out" / row.workbook_id
    root = state / "failed-releases" / row.workbook_id
    current_path = root / "current.json"
    if not release_src.exists() and not release_src.is_symlink():
        if current_path.is_file() and not current_path.is_symlink():
            try:
                current = json.loads(current_path.read_text(encoding="utf-8"))
                path = Path(str(current.get("snapshot_path", "")))
                return path if path.is_dir() and not path.is_symlink() else None
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None
        return None
    size_bytes = _safe_tree_size(release_src)
    if config is not None:
        _check_evidence_capacity(config, size_bytes)
    versions = root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    token = str(time.time_ns())
    destination = versions / token
    temporary = versions / f".{token}.snapshot"
    try:
        shutil.copytree(release_src, temporary, symlinks=False)
        copied_size = _safe_tree_size(temporary)
        if copied_size != size_bytes:
            raise RecoveryError("failed-release snapshot size changed during copy")
        snapshot_path = temporary / "snapshot.json"
        # The derivation below adds this record's size to the measured copy, so
        # it would double-count if the copy already contained the same name.
        if snapshot_path.exists() or snapshot_path.is_symlink():
            raise RecoveryError(
                "failed-release snapshot already contains snapshot.json"
            )
        atomic_write_json(
            snapshot_path,
            {
                "workbook_id": row.workbook_id,
                "source_path": str(release_src),
                "size_bytes": size_bytes,
                "preserved_at": _timestamp(),
            },
        )
        # The staged tree is the copy just measured plus this one record, so
        # one stat gives the same total as re-walking the whole tree.
        staged_size = copied_size + snapshot_path.stat().st_size
        if config is not None:
            _check_evidence_capacity(
                config, staged_size, existing_bytes=staged_size
            )
        os.replace(temporary, destination)
        atomic_write_json(
            current_path,
            {
                "workbook_id": row.workbook_id,
                "snapshot_path": str(destination),
                "size_bytes": size_bytes,
                "updated_at": _timestamp(),
            },
        )
        return destination
    except BaseException:
        if temporary.exists() and temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _assert_inspect_candidate(
    row: QueueRow, repo: Path, data: Mapping[str, object]
) -> None:
    """Worker-side pointer and path checks. Patched resolve cannot skip these."""

    release_root = repo / "release_out" / row.workbook_id
    pointer_path = release_root / "current-release.json"
    if pointer_path.is_symlink():
        raise RecoveryError("current-release.json is a symlink")
    if not pointer_path.is_file():
        raise RecoveryError("current-release.json is absent")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(
            f"current-release.json is unreadable: {error}"
        ) from error
    if not isinstance(pointer, dict):
        raise RecoveryError("current-release.json is not an object")
    release_id = str(data.get("release_id", ""))
    if not release_id or str(pointer.get("release_id")) != release_id:
        raise RecoveryError("current-release.json does not bind inspect release_id")
    try:
        root = release_root.resolve(strict=False)
    except OSError as error:
        raise RecoveryError(f"release_out is unreadable: {error}") from error
    for key, label in (("release_dir", "release_dir"), ("task_dir", "task_dir")):
        raw = Path(str(data.get(key, "")))
        try:
            resolved = raw.resolve(strict=False)
        except OSError as error:
            raise RecoveryError(f"{label} is unreadable: {error}") from error
        if not resolved.is_relative_to(root):
            raise RecoveryError(f"inspect {label} escapes release_out")


def _read_bound_json(path: Path, description: str) -> tuple[dict[str, object], bytes]:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise RecoveryError(f"{description} is missing: {error}") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RecoveryError(f"{description} is not a safe regular file")
    try:
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"{description} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise RecoveryError(f"{description} must be a JSON object")
    return value, content


def _assert_canonical_directory(path: Path, parent: Path, description: str) -> None:
    if parent.is_symlink() or path.is_symlink():
        raise RecoveryError(f"{description} path contains a symlink")
    if not parent.is_dir() or not path.is_dir():
        raise RecoveryError(f"{description} directory is missing")
    if path.resolve(strict=False).parent != parent.resolve(strict=False):
        raise RecoveryError(f"{description} path is not canonical")


def _validate_task_manifest_artifacts(
    task_dir: Path, manifest: Mapping[str, object]
) -> dict[str, str]:
    """Check a bundle against its manifest and return the digests computed."""

    expected = manifest.get("artifacts")
    if not isinstance(expected, dict):
        raise RecoveryError("task generation manifest has no artifact records")
    records = _tree_records(task_dir)
    actual: dict[str, dict[str, object]] = {}
    for relative, digest in records.items():
        if relative == "generation-manifest.json":
            continue
        path = task_dir / relative
        actual[relative] = {
            "algorithm": "sha256",
            "path": relative,
            "sha256": digest,
            "size_bytes": path.stat().st_size,
        }
    if actual != expected:
        raise RecoveryError("task generation artifacts do not match manifest")
    return records


def _independent_release_candidate(
    row: QueueRow,
    repo: Path,
    resolver_data: Mapping[str, object],
) -> ReleaseCandidate:
    release_root = repo / "release_out" / row.workbook_id
    if release_root.is_symlink() or not release_root.is_dir():
        raise RecoveryError("release root is missing or unsafe")
    pointer_path = release_root / "current-release.json"
    pointer, pointer_bytes = _read_bound_json(
        pointer_path, "current release pointer"
    )
    release_id = pointer.get("release_id")
    if not isinstance(release_id, str) or not HASH_RE.fullmatch(release_id):
        raise RecoveryError("current release pointer has an invalid release ID")
    if pointer.get("schema_version") != "workbook-current-release/v2":
        raise RecoveryError("current release pointer schema is unsupported")
    if pointer.get("release_path") != f"releases/{release_id}":
        raise RecoveryError("current release pointer path is not canonical")
    pointer_manifest_hash = pointer.get("manifest_sha256")
    if (
        not isinstance(pointer_manifest_hash, str)
        or not HASH_RE.fullmatch(pointer_manifest_hash)
    ):
        raise RecoveryError("current release pointer manifest hash is invalid")

    releases = release_root / "releases"
    release_dir = releases / release_id
    _assert_canonical_directory(release_dir, releases, "release")
    if set(path.name for path in release_dir.iterdir()) != {
        "release-manifest.json"
    }:
        raise RecoveryError("release directory contains unbound artifacts")
    release_manifest_path = release_dir / "release-manifest.json"
    release_manifest, release_bytes = _read_bound_json(
        release_manifest_path, "release manifest"
    )
    release_hash = _sha256_bytes(release_bytes)
    if release_hash != pointer_manifest_hash:
        raise RecoveryError("release manifest does not match pointer hash")
    identity = release_manifest.get("identity")
    if (
        release_manifest.get("schema_version") != "workbook-release/v2"
        or not isinstance(identity, dict)
        or identity.get("schema_version") != "workbook-release-identity/v2"
        or _sha256_bytes(_canonical_bytes(identity)) != release_id
        or release_manifest.get("release_id") != release_id
        or release_manifest.get("workbook_id") != row.workbook_id
        or identity.get("workbook_id") != row.workbook_id
    ):
        raise RecoveryError("release manifest identity is invalid")
    for key in (
        "workbook_id",
        "source_generation",
        "segmentation_generation",
        "task_generation",
        "bindings",
        "versions",
        "prior_release_id",
        "legacy_snapshot_hash",
    ):
        if release_manifest.get(key) != identity.get(key):
            raise RecoveryError(f"release manifest identity field changed: {key}")
    bindings = release_manifest.get("bindings")
    if (
        not isinstance(bindings, dict)
        or bindings.get("source_sha256") != row.run_source_sha256
    ):
        raise RecoveryError("release manifest does not bind queue source")
    task_record = release_manifest.get("task_generation")
    if not isinstance(task_record, dict):
        raise RecoveryError("release manifest has no task generation")
    generation_id = task_record.get("generation_id")
    if not isinstance(generation_id, str) or not HASH_RE.fullmatch(generation_id):
        raise RecoveryError("release task generation ID is invalid")
    task_generations = release_root / "task-generations"
    task_dir = task_generations / generation_id
    _assert_canonical_directory(task_dir, task_generations, "task generation")
    task_manifest_path = task_dir / "generation-manifest.json"
    task_manifest, task_bytes = _read_bound_json(
        task_manifest_path, "task generation manifest"
    )
    task_hash = _sha256_bytes(task_bytes)
    if (
        task_record.get("generation_path")
        != str(task_dir.resolve(strict=False))
        or task_record.get("manifest_path")
        != str(task_manifest_path.resolve(strict=False))
        or task_record.get("manifest_sha256") != task_hash
    ):
        raise RecoveryError("release task manifest hash changed")
    task_identity = task_manifest.get("identity")
    if (
        task_manifest.get("schema_version") != "task-generation/v2"
        or not isinstance(task_identity, dict)
        or task_identity.get("schema_version") != "task-generation-identity/v2"
        or _sha256_bytes(_canonical_bytes(task_identity)) != generation_id
        or task_manifest.get("generation_id") != generation_id
        or task_identity.get("workbook_id") != row.workbook_id
        or task_manifest.get("bindings") != task_identity.get("bindings")
        or task_manifest.get("artifacts") != task_identity.get("artifacts")
        or task_record.get("task_generation_sha256")
        != _sha256_bytes(_canonical_bytes(task_identity))
    ):
        raise RecoveryError("task generation manifest identity is invalid")
    bundle_records = _validate_task_manifest_artifacts(task_dir, task_manifest)
    expected_release_dir = str(release_dir.resolve(strict=False))
    expected_task_dir = str(task_dir.resolve(strict=False))
    if (
        str(Path(str(resolver_data.get("release_dir", ""))).resolve(strict=False))
        != expected_release_dir
        or str(Path(str(resolver_data.get("task_dir", ""))).resolve(strict=False))
        != expected_task_dir
        or str(resolver_data.get("release_id", "")) != release_id
        or str(resolver_data.get("task_generation_id", "")) != generation_id
    ):
        raise RecoveryError("mutable resolver output disagrees with canonical release")
    return ReleaseCandidate(
        release_id=release_id,
        release_dir=release_dir,
        task_dir=task_dir,
        task_generation_id=generation_id,
        source_sha256=row.run_source_sha256,
        # Reuses the digests the manifest check above just computed instead of
        # re-walking and re-hashing the same tree.
        bundle_sha256=tree_sha256_from_records(bundle_records),
        pointer_path=pointer_path,
        pointer_sha256=_sha256_bytes(pointer_bytes),
        release_manifest_path=release_manifest_path,
        release_manifest_sha256=release_hash,
        task_manifest_path=task_manifest_path,
        task_manifest_sha256=task_hash,
    )


def _exclusive_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_file() and path.read_bytes() == content:
            return
        raise FileExistsError(f"refusing to overwrite immutable sidecar: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != content:
                raise
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _read_valid_delivery(config: Config, row: QueueRow) -> dict[str, object] | None:
    batch_root = config.output / row.batch_id
    bundle = batch_root / f"{row.workbook_id}-outputs"
    sidecar_path = batch_root / f"{row.workbook_id}-outputs.verification.json"
    if (
        batch_root.is_symlink()
        or batch_root.resolve(strict=False).parent
        != config.output.resolve(strict=False)
        or bundle.is_symlink()
        or sidecar_path.is_symlink()
    ):
        return None
    if not bundle.is_dir() or not sidecar_path.is_file():
        return None
    try:
        missing = [
            relative
            for relative in REQUIRED_BUNDLE_FILES
            if not (bundle / relative).is_file()
            or (bundle / relative).is_symlink()
        ]
        missing.extend(
            relative
            for relative in REQUIRED_BUNDLE_DIRECTORIES
            if not (bundle / relative).is_dir()
            or (bundle / relative).is_symlink()
        )
        input_workbook = bundle / "environment" / f"{row.workbook_id}-inputs.xlsx"
        if (
            missing
            or not input_workbook.is_file()
            or input_workbook.is_symlink()
        ):
            return None
        _safe_tree_size(bundle)
        baseline = read_code_baseline(
            config.code_baseline, config.baseline_repo
        )
        _assert_frozen_protected_baseline(
            _protected_snapshot(config.baseline_repo, baseline),
            baseline,
        )
        assert_canonical_grader_provenance(config.baseline_repo, bundle)
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        expected_bundle = str(bundle.resolve(strict=False))
        if not isinstance(sidecar, dict) or (
            sidecar.get("schema_version") != DELIVERY_SCHEMA
            or sidecar.get("batch_id") != row.batch_id
            or sidecar.get("workbook_id") != row.workbook_id
            or sidecar.get("run_source_sha256") != row.run_source_sha256
            or sidecar.get("source_sha256") != row.run_source_sha256
            or sidecar.get("bundle_path") != expected_bundle
            or sidecar.get("bundle_sha256") != tree_sha256(bundle)
        ):
            return None
        if sidecar.get("modified"):
            report = batch_root / f"{row.workbook_id}-outputs.fairness.md"
            amd = bundle / "a.md"
            if (
                sidecar.get("fairness_verdict") != "PASS"
                or sidecar.get("fairness_report_path")
                != str(report.resolve(strict=False))
                or not report.is_file()
                or report.is_symlink()
                or sha256_file(report) != sidecar.get("fairness_report_sha256")
                or not fairness_passed(report.read_text(encoding="utf-8"))
                or not amd.is_file()
                or amd.is_symlink()
            ):
                return None
            # The independent fairness verifier already passed this record
            # and the sidecar binds its report by hash. A later, stricter
            # a.md gate must not un-deliver a finished task.
        elif (
            sidecar.get("fairness_verdict") != "not_required"
            or sidecar.get("fairness_report_path") is not None
            or sidecar.get("fairness_report_sha256") is not None
        ):
            return None
        # Recovery evidence is bound only when the sidecar carries it, so
        # deliveries from before the field existed stay valid.
        bound = sidecar.get("recovery_evidence")
        if isinstance(bound, dict) and bound.get("sha256"):
            evidence = batch_root / f"{row.workbook_id}-outputs.recovery-evidence.json"
            if (
                bound.get("path") != str(evidence.resolve(strict=False))
                or not evidence.is_file()
                or evidence.is_symlink()
                or sha256_file(evidence) != bound.get("sha256")
            ):
                return None
        return sidecar
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        RecoveryError,
        PolicyViolation,
    ):
        return None


def _quarantine_partial_delivery(config: Config, row: QueueRow) -> Path | None:
    if _read_valid_delivery(config, row) is not None:
        return None
    batch_root = config.output / row.batch_id
    names = (
        f"{row.workbook_id}-outputs",
        f"{row.workbook_id}-outputs.verification.json",
        f"{row.workbook_id}-outputs.fairness.md",
        # Written before the sidecar; a crash between the two would otherwise
        # leave a file _exclusive_write refuses to overwrite on every retry.
        f"{row.workbook_id}-outputs.recovery-evidence.json",
    )
    existing = [batch_root / name for name in names if (batch_root / name).exists() or (batch_root / name).is_symlink()]
    if not existing:
        return None
    size_bytes = 0
    for source in existing:
        mode = source.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise RecoveryError("partial delivery contains a symlink")
        if stat.S_ISDIR(mode):
            size_bytes += _safe_tree_size(source)
        elif stat.S_ISREG(mode):
            size_bytes += source.stat().st_size
        else:
            raise RecoveryError("partial delivery contains a special file")
    _check_evidence_capacity(config, size_bytes)
    parent = config.state / "delivery-quarantine" / row.workbook_id
    parent.mkdir(parents=True, exist_ok=True)
    token = str(time.time_ns())
    quarantine = parent / token
    temporary = parent / f".{token}.quarantine"
    temporary.mkdir()
    moved: list[tuple[Path, Path]] = []
    try:
        for source in existing:
            target = temporary / source.name
            os.replace(source, target)
            moved.append((source, target))
        atomic_write_json(
            temporary / "quarantine.json",
            {
                "workbook_id": row.workbook_id,
                "batch_id": row.batch_id,
                "reason": "incomplete or invalid delivery sidecar",
                "size_bytes": size_bytes,
                "quarantined_at": _timestamp(),
            },
        )
        staged_size = _safe_tree_size(temporary)
        _check_evidence_capacity(
            config, staged_size, existing_bytes=staged_size
        )
        os.replace(temporary, quarantine)
        return quarantine
    except BaseException:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                os.replace(target, source)
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


@contextlib.contextmanager
def _output_delivery_lock(config: Config):
    config.output.mkdir(parents=True, exist_ok=True)
    lock_path = config.output / ".recovery-delivery.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def deliver_bundle(
    config: Config,
    row: QueueRow,
    candidate: ReleaseCandidate,
    *,
    modified: bool,
    verification_report: str | None,
    verification_attempt: Mapping[str, object] | None,
    guard_evidence: Mapping[str, object] | None = None,
    output_retention: Mapping[str, object] | None = None,
    fairness_verdict: Mapping[str, object] | None = None,
    recovery_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    assert_canonical_grader_provenance(
        config.baseline_repo, candidate.task_dir
    )
    if modified:
        validate_recovery_amd(candidate.task_dir)
        if verification_report is None or not fairness_passed(verification_report):
            raise RecoveryError("modified bundle lacks a passing fairness report")
        accepted, _block, verifier_error = fairness_accepted(verification_report)
        if not accepted:
            raise RecoveryError(
                f"modified bundle fairness report not accepted: {verifier_error}"
            )
    batch_root = config.output / row.batch_id
    destination = batch_root / f"{row.workbook_id}-outputs"
    expected_hash = candidate.bundle_sha256
    existing_delivery = _read_valid_delivery(config, row)
    if existing_delivery is not None:
        if existing_delivery.get("bundle_sha256") != expected_hash:
            raise FileExistsError(
                f"refusing to overwrite complete verified bundle {destination}"
            )
        return existing_delivery
    with _output_delivery_lock(config):
        existing_delivery = _read_valid_delivery(config, row)
        if existing_delivery is not None:
            if existing_delivery.get("bundle_sha256") != expected_hash:
                raise FileExistsError(
                    f"refusing to overwrite complete verified bundle {destination}"
                )
            return existing_delivery
        _quarantine_partial_delivery(config, row)
        concurrent_delivery = _read_valid_delivery(config, row)
        if concurrent_delivery is not None:
            if concurrent_delivery.get("bundle_sha256") != expected_hash:
                raise FileExistsError(
                    f"refusing to overwrite complete verified bundle {destination}"
                )
            return concurrent_delivery
        if not destination.exists() and not destination.is_symlink():
            batch_root.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f".{destination.name}.",
                    suffix=".delivery",
                    dir=batch_root,
                )
            )
            try:
                temporary.rmdir()
                shutil.copytree(candidate.task_dir, temporary, symlinks=False)
                if tree_sha256(temporary) != expected_hash:
                    raise RecoveryError("atomic delivery staging hash mismatch")
                os.rename(temporary, destination)
                descriptor = os.open(batch_root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
        elif _read_valid_delivery(config, row) is None:
            raise RecoveryError("delivery destination appeared without valid sidecar")
        report_path: Path | None = None
        report_hash: str | None = None
        if modified:
            report_path = batch_root / f"{row.workbook_id}-outputs.fairness.md"
            report_bytes = verification_report.encode("utf-8")  # type: ignore[union-attr]
            _exclusive_write(report_path, report_bytes)
            report_hash = _sha256_bytes(report_bytes)
        delivered_evidence: dict[str, object] | None = None
        if recovery_evidence is not None and recovery_evidence.get("path"):
            source = Path(str(recovery_evidence["path"]))
            if source.is_file() and not source.is_symlink():
                evidence_bytes = source.read_bytes()
                evidence_hash = _sha256_bytes(evidence_bytes)
                if recovery_evidence.get("sha256") not in (None, evidence_hash):
                    raise RecoveryError("recovery evidence changed before delivery")
                evidence_copy = batch_root / f"{row.workbook_id}-outputs.recovery-evidence.json"
                _exclusive_write(evidence_copy, evidence_bytes)
                delivered_evidence = {
                    "path": str(evidence_copy.resolve(strict=False)),
                    "state_path": str(source),
                    "sha256": evidence_hash,
                }
        sidecar = {
            "schema_version": DELIVERY_SCHEMA,
            "batch_id": row.batch_id,
            "workbook_id": row.workbook_id,
            "release_id": candidate.release_id,
            "task_generation_id": candidate.task_generation_id,
            "source_sha256": candidate.source_sha256,
            "run_source_sha256": row.run_source_sha256,
            "bundle_path": str(destination.resolve(strict=False)),
            "bundle_sha256": expected_hash,
            "modified": modified,
            "fairness_verdict": "PASS" if modified else "not_required",
            "fairness_report_path": (
                str(report_path.resolve(strict=False)) if report_path else None
            ),
            "fairness_report_sha256": report_hash,
            # The structured verdict the worker acted on (checks + findings),
            # so a batch audit never has to re-parse prose.
            "fairness_checks": (
                dict(fairness_verdict) if fairness_verdict is not None else None
            ),
            "verification_attempt": (
                dict(verification_attempt)
                if verification_attempt is not None
                else None
            ),
            # Controller-written record of what recovery did (commands, changed
            # files, attempt history), hash-bound; lives outside the bundle.
            "recovery_evidence": delivered_evidence,
            "guard_evidence": dict(guard_evidence or {}),
            # Candidates vs. retained outputs, the retention gate result, and
            # whether the cone retry was spent; what the fleet report and any
            # batch audit read to measure trimming.
            "output_retention": (
                dict(output_retention) if output_retention is not None else None
            ),
        }
        sidecar_path = batch_root / f"{row.workbook_id}-outputs.verification.json"
        _exclusive_write(sidecar_path, _canonical_bytes(sidecar))
        return sidecar


def _delivery_is_complete(config: Config, row: QueueRow) -> bool:
    return _read_valid_delivery(config, row) is not None


def _write_task_evidence(
    state: Path, row: QueueRow, *, error: str, extra: Mapping[str, object] | None = None
) -> Path:
    destination = state / "task-evidence" / f"{row.workbook_id}.json"
    prior: dict[str, object] = {}
    if destination.is_file() and not destination.is_symlink():
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prior = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            prior = {}
    payload = {
        **prior,
        "workbook_id": row.workbook_id,
        "batch_id": row.batch_id,
        "error": error,
        "updated_at": _timestamp(),
        **dict(extra or {}),
    }
    atomic_write_json(destination, payload)
    return destination


def error_signature(text: object) -> tuple[str, str]:
    """``(error_type, normalized_first_line)`` stable across attempts.

    Hashes, absolute paths and counters change between attempts of the same
    defect; the fingerprint must not. The exception type is split off so it
    can be a field of its own.
    """

    first = str(text or "").strip().splitlines()
    line = first[0].strip() if first else ""
    error_type = ""
    match = _SIGNATURE_TYPE_RE.match(line)
    if match:
        error_type = match.group(1)
        line = line[match.end():]
    line = line.lower()
    line = _SIGNATURE_HASH_RE.sub("<hash>", line)
    line = _SIGNATURE_PATH_RE.sub("<path>", line)
    line = _SIGNATURE_NUMBER_RE.sub("<n>", line)
    line = " ".join(line.split())
    return error_type, line[:200]


def failure_signature(
    failure_class: str,
    gate: int | None,
    error_type: str,
    normalized: str,
    failing_checks: Sequence[str] = (),
) -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {
                "class": failure_class,
                "gate": gate,
                "error_type": error_type,
                "normalized": normalized,
                "failing_checks": sorted(str(item) for item in failing_checks),
            }
        )
    )


def classify_failure(result: Mapping[str, object]) -> dict[str, object]:
    """Mechanical class/subclass/gate for a non-``generated`` row result.

    Precedence, in order: a killed or failed generation attempt beats the
    inspect error it inevitably produces; a controller verdict from the
    investigator beats everything task-side; structured fairness findings
    name their own class; then the deterministic gates by error prefix.
    Anything the vocabulary does not name is ``unknown`` and never raises.
    """

    status = str(result.get("status", "recovery_pending"))
    error = str(result.get("error") or "")
    inspect_error = str(result.get("inspect_error") or "")
    delivery_error = str(result.get("delivery_error") or "")
    last_attempt = result.get("last_attempt")
    last_attempt = last_attempt if isinstance(last_attempt, Mapping) else {}
    limits = last_attempt.get("execution_limits")
    limits = limits if isinstance(limits, Mapping) else {}
    limit_reason = str(
        last_attempt.get("limit_reason") or limits.get("limit_reason") or ""
    )
    attempt_status = str(last_attempt.get("status") or "")
    incident = result.get("incident_report")
    incident = incident if isinstance(incident, Mapping) else {}
    verdict = incident.get("verdict")
    verdict = verdict if isinstance(verdict, Mapping) else {}
    fairness = result.get("fairness_verdict")
    fairness = fairness if isinstance(fairness, Mapping) else {}
    failing_checks = [
        str(name)
        for name, value in (fairness.get("checks") or {}).items()
        if isinstance(fairness.get("checks"), Mapping) and str(value).lower() == "fail"
    ] if isinstance(fairness.get("checks"), Mapping) else []
    failure_class = "unknown"
    subclass = ""
    gate: int | None = None
    text = error

    if status == "disk_exhausted":
        failure_class, subclass = "disk_exhausted", "disk_floor"
    elif "evidence total would be" in error or "evidence item is" in error:
        failure_class, subclass = "disk_exhausted", "evidence_capacity"
    elif result.get("retry_budget_exhausted"):
        failure_class, subclass = "retry_budget", "row_attempt_cap"
    elif result.get("policy_violation"):
        failure_class, subclass, gate = "policy_violation", "protected_drift", 16
    elif result.get("verifier_error"):
        failure_class = "verifier_error"
        subclass = str(result.get("verifier_error"))
    elif status == "worker_fix_needed" or verdict.get("route") == "controller":
        failure_class = "controller"
        subclass = str(verdict.get("subclass") or "investigator_verdict")
        gate = verdict.get("gate") if isinstance(verdict.get("gate"), int) else None
    elif attempt_status and attempt_status != "completed":
        failure_class, subclass = "generation_failed", attempt_status
        text = str(last_attempt.get("error") or error)
    elif limit_reason:
        failure_class, subclass = "generation_failed", limit_reason
    elif result.get("saw_hard_blocker"):
        failure_class, subclass = "agent_hard_blocker", "hard_line"
    elif verdict.get("route") == "shared_pipeline_defect":
        failure_class = "shared_pipeline_defect"
        subclass = str(verdict.get("subclass") or "")
        gate = verdict.get("gate") if isinstance(verdict.get("gate"), int) else None
    elif failing_checks:
        primary = next(
            (check for check in FAIRNESS_CHECKS if check in failing_checks), failing_checks[0]
        )
        if failing_checks == ["recovery_record"]:
            primary = "recovery_record"
        failure_class = FAIRNESS_CHECK_CLASSES.get(primary, "fairness_semantic")
        finding = next(
            (
                item
                for item in (fairness.get("findings") or [])
                if isinstance(item, Mapping) and str(item.get("check")) == primary
            ),
            {},
        )
        subclass = str(finding.get("subclass") or primary)
        gate = finding.get("gate") if isinstance(finding.get("gate"), int) else None
    elif status == "fairness_retry" or "fairness verifier did not PASS" in error:
        failure_class, subclass = "fairness_semantic", "verdict_fail"
    elif "a.md is not a complete recovery record" in (delivery_error or error):
        failure_class, subclass, gate = "recovery_record", "amd_incomplete", 16
        text = delivery_error or error
    elif "recovered task has no final a.md" in (delivery_error or error):
        failure_class, subclass, gate = "recovery_record", "amd_missing", 16
    elif "instruction metadata" in (delivery_error or error):
        failure_class, gate = "instruction_metadata", 13
        detail = (delivery_error or error).split("instruction metadata:", 1)[-1].strip()
        reason = re.split(r"[\s(]", detail, maxsplit=1)[0].strip(":") if detail else ""
        if "wrong mode" in detail:
            subclass = "mode_mismatch"
        elif reason and re.fullmatch(r"[a-z_]+", reason):
            # prose_recovery.py reason codes (prose_metadata_mismatch,
            # invalid_prose_metadata, ...) keep their name under a prose_ prefix.
            subclass = reason if reason.startswith("prose_") else f"prose_{reason}"
        else:
            subclass = "verify_metadata"
    elif "refusing to overwrite complete verified bundle" in (delivery_error or error):
        failure_class, subclass, gate = "delivery", "already_delivered", 16
    elif delivery_error:
        failure_class, subclass, gate = "delivery", "delivery_error", 16
        text = delivery_error
    elif result.get("retention_findings"):
        failure_class, subclass, gate = "retention", "unexplained_exclusion", 2
    elif result.get("unexpected") or (
        inspect_error and not inspect_error.startswith(("RecoveryError", "RecoveryOrchestrationError"))
        and ":" in inspect_error.split(" ", 1)[0]
    ):
        failure_class, subclass = "controller", "unexpected_exception"
        text = inspect_error or error
    elif inspect_error:
        failure_class, subclass, gate = "inspect", "release_inspect", 16
        text = inspect_error
    elif "absent from the pinned approved inventory" in error or "approval" in error:
        failure_class, subclass = "orchestration", "inventory"
    error_type, normalized = error_signature(text)
    if failure_class == "unknown" and error_type:
        subclass = error_type.lower()
    return {
        "class": failure_class,
        "subclass": subclass or "unspecified",
        "gate": gate,
        "error_type": error_type,
        "normalized": normalized,
        "failing_checks": failing_checks,
        "signature": failure_signature(
            failure_class, gate, error_type, normalized, failing_checks
        ),
    }


def _failures_path(state: Path, workbook_id: str) -> Path:
    return state / "failures" / f"{workbook_id}.json"


def read_failure_record(state: Path, workbook_id: str) -> dict[str, object]:
    path = _failures_path(state, workbook_id)
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_failure_record(
    state: Path,
    row: QueueRow,
    result: Mapping[str, object],
    *,
    lifetime_attempts: int | None = None,
    instruction_mode: str | None = None,
    stage: str | None = None,
) -> dict[str, object]:
    """Merge one non-``generated`` row result into ``state/failures/<wb>.json``.

    The file outlives the checkpoint (which is deleted on unsafe lineage), so
    it is the durable home of ``lifetime_attempts`` and of the recurrence
    counters the fleet report aggregates.
    """

    prior = read_failure_record(state, row.workbook_id)
    classified = classify_failure(result)
    now = _timestamp()
    history = list(prior.get("history", [])) if isinstance(prior.get("history"), list) else []
    same = prior.get("signature") == classified["signature"]
    occurrence = int(prior.get("occurrence_count", 0)) + 1 if same else 1
    if prior.get("signature") and not same:
        history.append(
            {
                "class": prior.get("class"),
                "subclass": prior.get("subclass"),
                "signature": prior.get("signature"),
                "occurrence_count": prior.get("occurrence_count"),
                "last_seen": prior.get("last_seen"),
            }
        )
        history = history[-20:]
    attempts = max(
        int(prior.get("lifetime_attempts", 0) or 0),
        int(lifetime_attempts if lifetime_attempts is not None else 0),
        int(result.get("cumulative_attempt_count") or 0),
    )
    evidence: list[dict[str, object]] = []
    for key in ("evidence_path",):
        value = result.get(key)
        if value:
            path = Path(str(value))
            evidence.append(
                {
                    "path": str(path),
                    "sha256": sha256_file(path) if path.is_file() else None,
                }
            )
    last_attempt = result.get("last_attempt")
    last_attempt = last_attempt if isinstance(last_attempt, Mapping) else {}
    payload = {
        "schema_version": FAILURE_SCHEMA,
        "workbook_id": row.workbook_id,
        "batch_id": row.batch_id,
        "status": str(result.get("status", "recovery_pending")),
        "class": classified["class"],
        "subclass": classified["subclass"],
        "gate": classified["gate"],
        "stage": stage or _stage_for_class(str(classified["class"])),
        "error_type": classified["error_type"],
        "normalized_error": classified["normalized"],
        "failing_checks": classified["failing_checks"],
        "signature": classified["signature"],
        "fixability": (
            "exhausted"
            if classified["class"] == "retry_budget"
            else "terminal"
            if classified["class"] in {"disk_exhausted", "policy_violation"}
            else "fixable"
        ),
        "lifetime_attempts": attempts,
        "occurrence_count": occurrence,
        "first_seen": prior.get("first_seen", now) if same else now,
        "last_seen": now,
        "instruction_mode": instruction_mode or prior.get("instruction_mode"),
        "attempt_status": last_attempt.get("status"),
        "limit_reason": last_attempt.get("limit_reason"),
        "error": str(result.get("error") or "")[:500],
        "evidence": evidence,
        "history": history,
    }
    atomic_write_json(_failures_path(state, row.workbook_id), payload)
    return payload


def _stage_for_class(failure_class: str) -> str:
    return {
        "inspect": "inspect",
        "retention": "retention",
        "recovery_record": "paperwork",
        "instruction_metadata": "paperwork",
        "fairness_semantic": "fairness",
        "fairness_leak": "fairness",
        "fairness_fidelity": "fairness",
        "verifier_error": "fairness",
        "delivery": "delivery",
        "policy_violation": "generation",
        "retry_budget": "row",
        "agent_hard_blocker": "generation",
        "generation_failed": "generation",
        "disk_exhausted": "host",
        "orchestration": "row_open",
        "shared_pipeline_defect": "generation",
        "controller": "controller",
    }.get(failure_class, "unknown")


def bump_failure_occurrence(state: Path, workbook_id: str) -> None:
    """A deduplicated incident is a recurrence; count it without a new verdict."""

    record = read_failure_record(state, workbook_id)
    if not record:
        return
    record["occurrence_count"] = int(record.get("occurrence_count", 0)) + 1
    record["last_seen"] = _timestamp()
    atomic_write_json(_failures_path(state, workbook_id), record)


def resolve_failure_record(state: Path, workbook_id: str) -> None:
    """Mark the row's failure record resolved once the row delivers.

    The file is kept (its history is what the report and the retry prompt
    learn from) but ``status`` becomes ``generated`` and ``resolved_at`` is
    set, so the lane fix step and the fleet report stop counting a row that
    is already delivered.
    """

    record = read_failure_record(state, workbook_id)
    if not record or record.get("status") == "generated":
        return
    record["status"] = "generated"
    record["resolved_at"] = _timestamp()
    record["fixability"] = "resolved"
    atomic_write_json(_failures_path(state, workbook_id), record)


def exit_code_for(ledger: Mapping[str, object], run_state: Mapping[str, object]) -> int:
    """Worker exit code for scripts/batch/run_lane.sh.

    0: every row generated. 2: a host or human floor ended the run (disk,
    evidence capacity, a worker that could not start). 3: at least one new
    controller fingerprint this run, so the lane fix step should run before
    the next cycle. 1: rows remain for the next cycle.
    """

    if run_state.get("host_floor"):
        return EXIT_HOST_FLOOR
    if run_state.get("controller_fingerprints"):
        return EXIT_CONTROLLER_FIX
    counts = ledger.get("counts") if isinstance(ledger.get("counts"), Mapping) else {}
    unfinished = sum(
        int(count) for status, count in counts.items() if status != "generated"
    )
    return EXIT_ROWS_REMAIN if unfinished else EXIT_ALL_GENERATED


def _recovery_ledger(
    expected_ids: Sequence[str], records: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    by_id = {str(record["workbook_id"]): dict(record) for record in records}
    rows = []
    for workbook_id in expected_ids:
        row = by_id.get(
            workbook_id,
            {"workbook_id": workbook_id, "status": "recovery_pending"},
        )
        rows.append(row)
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "recovery_pending"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": LEDGER_SCHEMA,
        "created_at": _timestamp(),
        "expected_count": len(expected_ids),
        "counts": dict(sorted(counts.items())),
        "records": rows,
    }


class RecoveryWorker:
    def __init__(
        self,
        config: Config,
        *,
        investigator_launcher: Callable[..., Mapping[str, object]] | None = None,
    ):
        self.config = config
        self.baseline = read_code_baseline(
            config.code_baseline, config.baseline_repo
        )
        self.baseline_head = _git_head(config.baseline_repo)
        self.baseline_hash = _baseline_hash(self.baseline)
        self.worker_overlay_id = WORKER_OVERLAY_ID
        # Per-run signals for the exit code (scripts/batch/run_lane.sh):
        # host_floor -> 2, controller_fingerprints -> 3.
        self.run_state: dict[str, object] = {}
        self._violation_attempts = 0
        self.selected_approval = _select_approval(
            config.inventory_registry, config.approval_batch_id
        )
        self.investigator_launcher = investigator_launcher
        self.tasks: dict[str, dict[str, object]] = {}
        self.current: str | None = None
        self.active_worktree: Path | None = None
        self.budget = self._build_budget()
        self.memory_scope_enabled = (
            self.budget is not None and memory_scope_supported()
        )
        self._active_lease: BudgetLease | None = None
        self._profiles: dict[str, WorkbookProfile] = {}
        # Set for the duration of one generation attempt. The ladder index is
        # the step the prompt quotes; it lags the attempt index by the number
        # of paperwork retries so a retry does not tell the agent to change a
        # strategy that worked. The retry record, when present, is what the
        # prompt describes as the one failed gate to rerun.
        self._pinned_ladder_index: int | None = None
        self._paperwork_retry: dict[str, object] | None = None
        # The cone retry record (why the output retention gate failed) while
        # its retry attempt runs, and the retention verdict carried into the
        # fairness prompt and the delivery sidecar.
        self._cone_retry: dict[str, object] | None = None
        self._active_output_retention: dict[str, object] | None = None
        # Attempt start, so xl_segment can be handed an absolute deadline.
        self._attempt_started_at: float | None = None
        # A MemoryMax scope whose payload inherited sshd's OOM immunity is the
        # failure that filled the fleet's disks, so a lane that will use
        # scopes must prove the reset works before it runs anything.
        self.oom_self_test = oom_score_adj_self_test()
        if self.oom_self_test.get("status") == "failed":
            message = (
                "OOM score self-test failed: "
                f"{self.oom_self_test.get('reason', 'unknown')}"
            )
            if self.memory_scope_enabled:
                raise RecoveryError(message)
            print(f"WARNING {message}", file=sys.stderr)
        self._enforce_disk_floor(row=None)

    # -- disk ----------------------------------------------------------------

    def _disk_reaper(self) -> DiskReaper:
        # Confined to this lane's own work root and state root. A lane cannot
        # tell which of a sibling's rows is active, so it never touches them;
        # each lane reaps its own parked rows when the shared disk fills.
        return DiskReaper(
            work_roots=[self.config.work_root],
            state_roots=[self.config.state],
            policy=ReapPolicy(
                high_water_bytes=self.config.disk_high_water_bytes,
                floor_bytes=self.config.min_free_disk_bytes,
                generation_grace_seconds=self.config.disk_generation_grace_seconds,
            ),
            budget=self.budget,
        )

    def _log_disk_reap(self, report: Mapping[str, object], *, stage: str) -> None:
        record = {
            "lane_id": self.config.lane_id,
            "workbook_id": self.current or "",
            "stage": stage,
            **dict(report),
        }
        with contextlib.suppress(OSError):
            path = self.config.state / "disk-reap.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(_canonical_bytes(record))
        reaped = report.get("reaped")
        count = len(reaped) if isinstance(reaped, list) else 0
        if count:
            # The fleet report (run-fcp-workbook-fleet) already reads stages.jsonl.
            self._record_memory_stage(
                "disk_reap",
                self._active_lease.bytes if self._active_lease else 0,
                note=(
                    f"{stage}: reclaimed {int(report.get('reclaimed_bytes', 0) or 0)} "
                    f"bytes over {count} items; free_after="
                    f"{report.get('free_after')}"
                ),
            )
        for item in reaped if isinstance(reaped, list) else []:
            if isinstance(item, Mapping) and item.get("kind") == "generation":
                self._note_disk_reap(str(item.get("workbook_id", "")), item)

    def _note_disk_reap(self, workbook_id: str, item: Mapping[str, object]) -> None:
        """Record a reaped generation on the row's checkpoint.

        The resume path rebuilds what was reaped, and this is how the next
        attempt knows it is a rebuild rather than a fresh row.
        """

        if not workbook_id or not SAFE_ID_RE.match(workbook_id):
            return
        path = self.config.state / "checkpoints" / f"{workbook_id}.json"
        if not path.is_file() or path.is_symlink():
            return
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(checkpoint, dict):
            return
        reaps = checkpoint.get("disk_reaps")
        reaps = list(reaps) if isinstance(reaps, list) else []
        reaps.append({"path": item.get("path"), "bytes": item.get("bytes"), "at": _timestamp()})
        checkpoint["disk_reaps"] = reaps
        with contextlib.suppress(OSError):
            atomic_write_json(path, checkpoint)

    def _sweep_staging(self, worktree: Path, *, stage: str) -> None:
        """Tier 1: drop dead staging dirs in one closed worktree.

        Grace is zero because the row is closed: its agent and every
        descendant have exited or been killed, so nothing is still building
        into these directories. The live-cwd check stays on as the backstop.
        """

        if not worktree.is_dir():
            return
        try:
            report = self._disk_reaper().reap(
                apply=True,
                target_free_bytes=None,
                kinds=("staging",),
                staging_grace_seconds=0,
                only_worktrees=[worktree],
            )
        except (DiskReaperError, OSError) as error:
            self._record_memory_stage("disk_sweep_failed", 0, note=str(error))
            return
        if report.reaped or report.failed:
            self._log_disk_reap(report.to_record(), stage=stage)

    def _enforce_disk_floor(self, *, row: QueueRow | None) -> dict[str, object] | None:
        """Tier 2 then the hard floor, before any row opens.

        Order matters and is pinned here: the caller has already taken the
        row's I/O-sized lease, so the tree walk and rmtree run accounted for;
        reap to the high-water mark; re-measure; then apply the floor. At
        startup (``row=None``) a floor miss is fatal. Per row it parks the
        row as ``disk_exhausted``, which the resume path turns back into
        ``recovery_pending`` on the next start.
        """

        floor = int(self.config.min_free_disk_bytes)
        high_water = int(self.config.disk_high_water_bytes)
        if floor <= 0 and high_water <= 0:
            return None
        free = free_disk_bytes(self.config.work_root)
        if free is None:
            return None
        if high_water > 0 and free < high_water:
            try:
                report = self._disk_reaper().reap(
                    apply=True, target_free_bytes=high_water
                )
            except (DiskReaperError, OSError) as error:
                self._record_memory_stage("disk_reap_failed", 0, note=str(error))
            else:
                self._log_disk_reap(
                    report.to_record(),
                    stage="startup" if row is None else "row_open",
                )
                if report.free_after is not None:
                    free = report.free_after
        if floor <= 0 or free >= floor:
            return None
        message = (
            f"free disk {free} bytes on {self.config.work_root} is below the "
            f"{floor}-byte floor (RECOVERY_MIN_FREE_DISK_BYTES)"
        )
        if row is None:
            raise RecoveryError(message)
        evidence = _write_task_evidence(
            self.config.state,
            row,
            error=message,
            extra={
                "disk_exhausted": True,
                "free_disk_bytes": free,
                "min_free_disk_bytes": floor,
            },
        )
        return {
            "workbook_id": row.workbook_id,
            "status": "disk_exhausted",
            "error": message,
            "free_disk_bytes": free,
            "queued": True,
            "evidence_path": str(evidence),
        }

    # -- memory governor ---------------------------------------------------

    def _build_budget(self) -> MemoryBudget | None:
        """Attach to the host budget, or degrade to ungoverned operation.

        A host that cannot report its own memory gets no gate rather than a
        guessed one, which keeps the worker usable off-fleet.
        """

        if not self.config.memory_governor_enabled:
            return None
        try:
            return MemoryBudget(
                self.config.memory_budget_dir,
                total_bytes=self.config.memory_host_bytes or None,
                reserve_bytes=self.config.memory_reserve_bytes or None,
            )
        except MemoryGovernorError:
            return None

    def _observations_path(self) -> Path:
        return self.config.state / "memory" / "observations.jsonl"

    def _shared_observations_path(self) -> Path | None:
        """Host-shared observation log, next to the budget file.

        Every lane on the host appends here as well as to its own log, so a
        new lane does not start from the prior while its siblings have
        already measured the same pipeline on the same kernel.
        """

        if not self.config.memory_governor_enabled:
            return None
        return self.config.memory_budget_dir / "observations.jsonl"

    def _calibration(self):
        shared = self._shared_observations_path()
        if shared is None:
            return load_calibration(self._observations_path())
        return load_calibration(self._observations_path(), shared)

    def _row_profile(self, row: QueueRow) -> WorkbookProfile | None:
        """Measure the workbook once per row, before any agent starts."""

        if row.workbook_id in self._profiles:
            return self._profiles[row.workbook_id]
        try:
            profile = probe_workbook(row.run_source_path)
        except MemoryGovernorError as error:
            _write_task_evidence(
                self.config.state,
                row,
                error=str(error),
                extra={"memory_probe_failed": True},
            )
            return None
        self._profiles[row.workbook_id] = profile
        return profile

    def _generation_reservation(self, row: QueueRow) -> int:
        """Bytes a row should hold while its generation agent runs.

        An unprobeable workbook reserves the whole budget so it runs alone.
        Guessing low here is what takes the VM down.
        """

        if self.budget is None:
            return 0
        profile = self._row_profile(row)
        if profile is None:
            return self.budget.total_bytes
        return estimate_peak_bytes(
            profile,
            self._calibration(),
            budget_total=self.budget.total_bytes,
        )

    def _resize_lease(self, request_bytes: int, *, stage: str) -> None:
        """Move the row's reservation to what this stage needs.

        Shrinking never blocks, so a stage handing memory back cannot stall.
        Growing waits, but only for a bounded time: the live MemAvailable check
        can refuse admission because of memory the budget file knows nothing
        about, and an unbounded wait there would stall every lane silently. On
        timeout the stage proceeds on whatever it still holds, and the cgroup
        ceiling remains the backstop.
        """

        lease = self._active_lease
        if lease is None or self.budget is None:
            return
        try:
            lease.resize(
                request_bytes,
                timeout_seconds=BUDGET_WAIT_TIMEOUT_SECONDS,
                on_wait=lambda: self._record_memory_stage(
                    f"{stage}_waiting", lease.bytes
                ),
            )
        except (MemoryGovernorError, BudgetTimeout, OSError) as error:
            self._record_memory_stage(f"{stage}_unresized", lease.bytes, note=str(error))
            return
        self._record_memory_stage(stage, lease.bytes)

    def _record_memory_stage(
        self, stage: str, held_bytes: int, *, note: str = ""
    ) -> None:
        path = self.config.state / "memory" / "stages.jsonl"
        record: dict[str, object] = {
            "lane_id": self.config.lane_id,
            "workbook_id": self.current or "",
            "stage": stage,
            "held_bytes": int(held_bytes),
            "at": _timestamp(),
        }
        if note:
            record["note"] = note
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(_canonical_bytes(record))


    def _checkpoint_path(self, row: QueueRow) -> Path:
        return self.config.state / "checkpoints" / f"{row.workbook_id}.json"

    def _load_checkpoint(self, row: QueueRow) -> dict[str, object]:
        path = self._checkpoint_path(row)
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _checkpoint_bindings(
        self, row: QueueRow, worktree: Path, kind: str
    ) -> dict[str, object]:
        return {
            "schema_version": CHECKPOINT_SCHEMA,
            "workbook_id": row.workbook_id,
            "run_source_sha256": row.run_source_sha256,
            "baseline_head": self.baseline_head,
            "baseline_hash": self.baseline_hash,
            "worktree_path": str(worktree.resolve(strict=False)),
            "worktree_type": kind,
        }

    def _save_checkpoint(
        self, row: QueueRow, worktree: Path, **fields: object
    ) -> dict[str, object]:
        checkpoint = self._load_checkpoint(row)
        kind = self._worktree_kind(worktree)
        checkpoint.update(self._checkpoint_bindings(row, worktree, kind))
        checkpoint.update(fields)
        checkpoint["updated_at"] = _timestamp()
        checkpoint.setdefault("cumulative_attempt_count", 0)
        checkpoint.setdefault("last_attempt", None)
        checkpoint.setdefault("strategy", self._ladder_step(0))
        checkpoint.setdefault("inspect_result", None)
        checkpoint.setdefault("fairness_result", None)
        checkpoint.setdefault("incident_reports", [])
        atomic_write_json(self._checkpoint_path(row), checkpoint)
        return checkpoint

    @staticmethod
    def _worktree_kind(worktree: Path) -> str:
        marker = worktree / ".recovery-worktree"
        if marker.is_file() and not marker.is_symlink():
            try:
                kind = marker.read_text(encoding="utf-8").strip()
                if kind in {"git", "copy"}:
                    return kind
            except OSError:
                pass
        return "unknown"

    def _marker_path(self, worktree: Path) -> Path:
        return worktree / ".recovery-worktree.json"

    def _write_worktree_marker(self, row: QueueRow, worktree: Path) -> None:
        marker = {
            "schema_version": WORKTREE_MARKER_SCHEMA,
            **self._checkpoint_bindings(
                row, worktree, self._worktree_kind(worktree)
            ),
        }
        marker["schema_version"] = WORKTREE_MARKER_SCHEMA
        atomic_write_json(self._marker_path(worktree), marker)

    def _binding_matches(
        self, value: Mapping[str, object], row: QueueRow, worktree: Path
    ) -> bool:
        expected = self._checkpoint_bindings(
            row, worktree, self._worktree_kind(worktree)
        )
        return all(value.get(key) == expected[key] for key in expected if key != "schema_version")

    def _read_worktree_marker(self, worktree: Path) -> dict[str, object]:
        path = self._marker_path(worktree)
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _quarantine_worktree(
        self, row: QueueRow, worktree: Path, reason: str
    ) -> Path | None:
        if not worktree.exists() and not worktree.is_symlink():
            return None
        # Copying a live worktree is gigabytes of dirty pages. Drop to the I/O
        # reservation first so the copy cannot run while this lane is still
        # accounted for at its generation peak.
        self._resize_lease(self.config.memory_io_bytes, stage="quarantine")
        excluded = frozenset({".git", ".venv", ".env"}) | TOOL_CACHE_DIRS
        if worktree.is_symlink() or not worktree.is_dir():
            raise RecoveryError("unsafe worktree cannot be copied to quarantine")
        size_bytes = _safe_tree_size(
            worktree,
            excluded_names=excluded,
            allow_internal_symlinks=True,
        )
        _check_evidence_capacity(self.config, size_bytes)
        parent = (
            self.config.state / "worktree-quarantine" / row.workbook_id
        )
        parent.mkdir(parents=True, exist_ok=True)
        token = str(time.time_ns())
        quarantine = parent / token
        temporary = parent / f".{token}.quarantine"

        def ignore(_directory: str, names: list[str]) -> list[str]:
            return [
                name
                for name in names
                if name in excluded or name.endswith(".pyc")
            ]

        try:
            shutil.copytree(
                worktree,
                temporary,
                symlinks=True,
                ignore=ignore,
            )
            copied_size = _safe_tree_size(
                temporary,
                allow_internal_symlinks=True,
            )
            if copied_size != size_bytes:
                raise RecoveryError("worktree quarantine size changed during copy")
            quarantine_record = temporary / "quarantine.json"
            # Same reason as the failed-release snapshot: the staged size is
            # derived by addition, so a pre-existing record would be counted
            # twice.
            if quarantine_record.exists() or quarantine_record.is_symlink():
                raise RecoveryError(
                    "worktree quarantine already contains quarantine.json"
                )
            atomic_write_json(
                quarantine_record,
                {
                    "workbook_id": row.workbook_id,
                    "run_source_sha256": row.run_source_sha256,
                    "reason": reason,
                    "size_bytes": size_bytes,
                    "quarantined_at": _timestamp(),
                },
            )
            # Same tree, plus the one record just written, so one stat replaces
            # a third full walk of a multi-gigabyte worktree.
            staged_size = copied_size + quarantine_record.stat().st_size
            _check_evidence_capacity(
                self.config,
                staged_size,
                existing_bytes=staged_size,
            )
            os.replace(temporary, quarantine)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            raise
        recycle_isolated_worktree(worktree, self.config.baseline_repo)
        return quarantine

    def _acquire_worktree(self, row: QueueRow) -> Path:
        destination = self.config.work_root / f"task-{row.workbook_id}"
        checkpoint = self._load_checkpoint(row)
        source_error: PolicyViolation | None = None
        try:
            _assert_source_hash(row, row.run_source_sha256, "before worktree resume")
        except PolicyViolation as error:
            source_error = error
        if destination.exists() or destination.is_symlink():
            marker = self._read_worktree_marker(destination)
            mismatch = not (
                checkpoint
                and marker
                and self._binding_matches(checkpoint, row, destination)
                and self._binding_matches(marker, row, destination)
            )
            reason = "checkpoint or worktree marker binding mismatch"
            if source_error is not None:
                mismatch = True
                reason = str(source_error)
            if not mismatch:
                try:
                    protected = _protected_snapshot(destination, self.baseline)
                    _assert_frozen_protected_baseline(protected, self.baseline)
                except (PolicyViolation, RecoveryError) as error:
                    mismatch = True
                    reason = str(error)
            if mismatch:
                quarantined = self._quarantine_worktree(
                    row, destination, reason
                )
                lineage_safe = bool(
                    checkpoint
                    and checkpoint.get("workbook_id") == row.workbook_id
                    and checkpoint.get("run_source_sha256")
                    == row.run_source_sha256
                )
                if lineage_safe:
                    refreshes = list(
                        checkpoint.get("baseline_refresh_history") or []
                    )
                    refreshes.append(
                        {
                            "reason": reason,
                            "quarantined_worktree": str(quarantined),
                            "prior_baseline_hash": checkpoint.get(
                                "baseline_hash"
                            ),
                            "replacement_baseline_hash": self.baseline_hash,
                            "refreshed_at": _timestamp(),
                        }
                    )
                    checkpoint["baseline_refresh_history"] = refreshes
                    atomic_write_json(self._checkpoint_path(row), checkpoint)
                else:
                    checkpoint = {}
                    self._checkpoint_path(row).unlink(missing_ok=True)
                _write_task_evidence(
                    self.config.state,
                    row,
                    error=reason,
                    extra={
                        "worktree_quarantined": str(quarantined),
                        "unsafe_worktree": True,
                    },
                )
            else:
                _link_baseline_runtime(self.config.baseline_repo, destination)
                return destination
        if source_error is not None:
            raise source_error
        prior_attempts = (
            int(checkpoint.get("cumulative_attempt_count", 0))
            if checkpoint
            else 0
        )
        prior_last_attempt = checkpoint.get("last_attempt") if checkpoint else None
        worktree = create_isolated_worktree(
            self.config.baseline_repo,
            self.config.work_root,
            row.workbook_id,
        )
        self._write_worktree_marker(row, worktree)
        self._save_checkpoint(
            row,
            worktree,
            status="recovery_pending",
            cumulative_attempt_count=prior_attempts,
            last_attempt=prior_last_attempt,
        )
        return worktree

    def _snapshot_candidate(
        self, row: QueueRow, candidate: ReleaseCandidate
    ) -> dict[str, object]:
        identity_key = (
            f"{candidate.release_id}--{candidate.task_generation_id}"
        )
        root = (
            self.config.state
            / "candidates"
            / row.workbook_id
            / identity_key
        )
        bundle = root / "task-bundle"
        if bundle.exists() or bundle.is_symlink():
            if bundle.is_symlink() or tree_sha256(bundle) != candidate.bundle_sha256:
                raise PolicyViolation("candidate snapshot hash conflict")
        else:
            size_bytes = _safe_tree_size(candidate.task_dir)
            _check_evidence_capacity(self.config, size_bytes)
            root.parent.mkdir(parents=True, exist_ok=True)
            temporary_root = root.parent / f".{identity_key}.{time.time_ns()}.snapshot"
            try:
                temporary_root.mkdir()
                temporary = temporary_root / "task-bundle"
                shutil.copytree(candidate.task_dir, temporary, symlinks=False)
                if tree_sha256(temporary) != candidate.bundle_sha256:
                    raise RecoveryError("candidate snapshot hash mismatch")
                manifest_sources = (
                    ("current-release.json", candidate.pointer_path, candidate.pointer_sha256),
                    (
                        "release-manifest.json",
                        candidate.release_manifest_path,
                        candidate.release_manifest_sha256,
                    ),
                    (
                        "task-generation-manifest.json",
                        candidate.task_manifest_path,
                        candidate.task_manifest_sha256,
                    ),
                )
                manifest_hashes: dict[str, str] = {}
                for name, source, expected_hash in manifest_sources:
                    if source is None:
                        continue
                    _value, content = _read_bound_json(source, name)
                    actual_hash = _sha256_bytes(content)
                    if actual_hash != expected_hash:
                        raise PolicyViolation(
                            f"candidate {name} changed before snapshot"
                        )
                    (temporary_root / name).write_bytes(content)
                    manifest_hashes[name] = actual_hash
                atomic_write_json(
                    temporary_root / "snapshot-record.json",
                    {
                        "workbook_id": row.workbook_id,
                        "release_id": candidate.release_id,
                        "task_generation_id": candidate.task_generation_id,
                        "bundle_sha256": candidate.bundle_sha256,
                        "size_bytes": size_bytes,
                        "manifest_sha256": manifest_hashes,
                        "snapshotted_at": _timestamp(),
                    },
                )
                staged_size = _safe_tree_size(temporary_root)
                _check_evidence_capacity(
                    self.config,
                    staged_size,
                    existing_bytes=staged_size,
                )
                os.replace(temporary_root, root)
            except BaseException:
                if temporary_root.exists():
                    shutil.rmtree(temporary_root, ignore_errors=True)
                raise
        size_bytes = _safe_tree_size(bundle)
        metadata = {
            "workbook_id": row.workbook_id,
            "run_source_sha256": row.run_source_sha256,
            "release_id": candidate.release_id,
            "release_dir": str(candidate.release_dir),
            "task_generation_id": candidate.task_generation_id,
            "source_sha256": candidate.source_sha256,
            "bundle_sha256": candidate.bundle_sha256,
            "snapshot_path": str(bundle),
            "size_bytes": size_bytes,
            "pointer_sha256": candidate.pointer_sha256,
            "release_manifest_sha256": candidate.release_manifest_sha256,
            "task_manifest_sha256": candidate.task_manifest_sha256,
            "snapshotted_at": _timestamp(),
        }
        metadata_path = root / "candidate.json"
        if metadata_path.is_file():
            prior = json.loads(metadata_path.read_text(encoding="utf-8"))
            prior = dict(prior) if isinstance(prior, dict) else {}
            for key in (
                "workbook_id",
                "run_source_sha256",
                "release_id",
                "task_generation_id",
                "source_sha256",
                "bundle_sha256",
                "snapshot_path",
            ):
                if prior.get(key) != metadata[key]:
                    raise PolicyViolation("candidate metadata snapshot conflict")
            metadata = prior
        else:
            _exclusive_write(metadata_path, _canonical_bytes(metadata))
        return metadata

    def _incident_prompt(
        self,
        row: QueueRow,
        category: str,
        detail: Mapping[str, object],
        report_path: Path,
    ) -> str:
        return f"""
You are a fresh read-only incident investigator. Analyze recovery recurrence
for workbook {row.workbook_id}. Category: {category}. Evidence:
{json.dumps(detail, sort_keys=True, default=str)}

Inspect only files already present in {self._task_repo()} and lane state under
{self.config.state}. Do not modify files, advance the queue, generate a task,
or make a fairness decision. This investigator is separate from the exactly
{self.config.lane_count} generation lanes. Return the incident and recurrence
analysis in your final response; the worker will persist it at {report_path}.
Do not read or print .env.

Name the cause and the route. Routes (pick exactly one):
- worktree_fix: a task-side defect the generation agent fixes in its own
  worktree (source, segmentation, instruction, answer key, grader, disclosure).
- paperwork: only a.md or release bookkeeping is wrong; artifacts are valid.
- cone_prune: an output-cone exclusion lacks an allowed reason.
- shared_pipeline_defect: a bug in a protected pipeline file (xl_seg, gates,
  build_one.sh) that the agent must patch in its worktree under RECOVERY.md
  and that the fleet report should count across lanes.
- controller: the recovery worker, hardened runner, memory governor, or disk
  reaper itself misbehaved (exception in worker code, wrong status transition,
  lease or scope bug). Only this route parks the row for the lane fix step.
- analysis_only: post-delivery recurrence review; nothing to route.

End with two things, in this order. First, one fenced ```json block:

```json
{{
  "schema_version": "recovery-incident-verdict/v1",
  "verdict": "WORKER_FIX_NEEDED" | "TASK_LOCAL_OR_EXTERNAL",
  "route": "<one route from the list>",
  "subclass": "<short_snake_case_label for the defect>",
  "gate": <SKILL.md gate 1-16 that should have caught it, or null>,
  "component": "<file or module at fault>",
  "cause": "<one sentence>",
  "evidence": ["<path>", "..."],
  "fix_hint": "<one sentence: the smallest fix>"
}}
```

Second, the final non-empty line must be exactly one of:
INCIDENT_VERDICT: WORKER_FIX_NEEDED
INCIDENT_VERDICT: TASK_LOCAL_OR_EXTERNAL
WORKER_FIX_NEEDED if and only if route is controller.
""".strip()

    def _default_investigator_launcher(
        self,
        *,
        command: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        report_path: Path,
    ) -> Mapping[str, object]:
        if not self.config.agent_binary.is_file():
            return {"status": "unavailable", "reason": "agent binary is unavailable"}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # This is the one full agent that bypasses hardened_runner, so it needs
        # the same oom_score_adj reset. The four remaining subprocess.run calls
        # in this module (git, python -c) are short, allocate little, and never
        # run inside a MemoryMax scope, so they are left unwrapped on purpose.
        process = subprocess.Popen(
            _oom_killable_command(command),
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            stream, _ = process.communicate(
                timeout=min(
                    self.config.timeout_seconds,
                    INCIDENT_TIMEOUT_SECONDS,
                )
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stream, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stream, _ = process.communicate()
        stream = stream or ""
        stream_path = report_path.with_suffix(".stream.jsonl")
        atomic_write_text(stream_path, stream)
        final_text = ""
        for line in stream.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("result"), str):
                final_text = event["result"]
        # Judge the verdict on the last assistant message that carries it,
        # not on the CLI's concatenated result: a trailing acknowledgement
        # message would otherwise defeat the last-line rule (see
        # verdict_transcript).
        try:
            segments = extract_assistant_segments(stream)
        except Exception:
            segments = []
        report = verdict_transcript(segments, "INCIDENT_VERDICT:", final_text).strip()
        if report and not report.endswith("\n"):
            report += "\n"
        atomic_write_text(report_path, report)
        if timed_out:
            status = "timed_out"
        elif process.returncode != 0:
            status = "nonzero_exit"
        elif not report.strip():
            status = "empty_or_malformed"
        else:
            status = "completed"
        return {
            "status": status,
            "pid": process.pid,
            "return_code": process.returncode,
            "report_sha256": (
                sha256_file(report_path) if report_path.is_file() else None
            ),
            "stream_path": str(stream_path),
        }

    def _launch_incident(
        self,
        row: QueueRow,
        category: str,
        detail: Mapping[str, object],
    ) -> dict[str, object]:
        # Mechanical fingerprint: the same defect on a later attempt must map
        # to the same incident, so per-attempt values (bundle and report
        # hashes, counters) are dropped and error text is normalised the way
        # failure signatures are (hashes, paths, numbers masked).
        stable_detail: dict[str, object] = {}
        for key, value in detail.items():
            if key in {
                "attempt",
                "cumulative_attempt_count",
                "strategy",
                "launched_at",
                "bundle_sha256",
                "report_sha256",
                "prior_failure_fingerprints",
                "output_retention",
            }:
                continue
            if key == "error":
                error_type, normalized = error_signature(value)
                stable_detail["error"] = f"{error_type}: {normalized}" if error_type else normalized
            else:
                stable_detail[key] = value
        fingerprint = _sha256_bytes(
            _canonical_bytes({"category": category, "detail": stable_detail})
        )
        root = self.config.state / "incidents" / row.workbook_id / fingerprint
        completed_path = root / "completed.json"
        if completed_path.is_file() and not completed_path.is_symlink():
            worker_fix_needed = False
            report_path = Path()
            try:
                prior = json.loads(completed_path.read_text(encoding="utf-8"))
                report_path = Path(str(prior.get("report_path", "")))
                launch = prior.get("launch")
                worker_fix_needed = bool(
                    isinstance(prior, dict)
                    and prior.get("worker_fix_needed") is True
                )
                valid_completed = bool(
                    isinstance(launch, dict)
                    and launch.get("status") == "completed"
                    and type(launch.get("return_code")) is int
                    and launch.get("return_code") == 0
                    and report_path.is_file()
                    and not report_path.is_symlink()
                    and report_path.read_text(encoding="utf-8").strip()
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                AttributeError,
                TypeError,
            ):
                valid_completed = False
            if valid_completed:
                # Same defect again: count the recurrence, skip the agent.
                bump_failure_occurrence(self.config.state, row.workbook_id)
                return {
                    "category": category,
                    "fingerprint": fingerprint,
                    "result_path": str(completed_path),
                    "report_path": str(report_path),
                    "worker_fix_needed": worker_fix_needed,
                    "verdict": prior.get("verdict") if isinstance(prior, dict) else None,
                    "verdict_error": prior.get("verdict_error") if isinstance(prior, dict) else None,
                    "completed": True,
                    "deduplicated": True,
                }
        root.mkdir(parents=True, exist_ok=True)
        attempt_root = root / "attempts" / str(time.time_ns())
        attempt_root.mkdir(parents=True)
        result_path = attempt_root / "result.json"
        report_path = attempt_root / "report.md"
        prompt = self._incident_prompt(row, category, detail, report_path)
        command = [
            str(self.config.agent_binary),
            "-p",
            "--mode",
            "ask",
            "--trust",
            "--output-format",
            "stream-json",
            "--model",
            self.config.model,
            "--workspace",
            str(self._task_repo()),
            prompt,
        ]
        launcher = self.investigator_launcher or self._default_investigator_launcher
        try:
            launch = dict(
                launcher(
                    command=command,
                    cwd=self._task_repo(),
                    env=self._agent_environment(),
                    report_path=report_path,
                )
            )
        except Exception as error:
            launch = {"status": "launch_error", "error": repr(error)}
        report = ""
        if report_path.is_file() and not report_path.is_symlink():
            try:
                report = report_path.read_text(encoding="utf-8")
            except OSError:
                report = ""
        completed = bool(
            launch.get("status") == "completed"
            and type(launch.get("return_code")) is int
            and launch.get("return_code") == 0
            and report.strip()
        )
        verdict_block, worker_fix_needed, verdict_error = (
            incident_verdict(report) if completed else (None, False, "not_completed")
        )
        result = {
            "schema_version": INCIDENT_SCHEMA,
            "workbook_id": row.workbook_id,
            "category": category,
            "fingerprint": fingerprint,
            "detail": dict(detail),
            "report_path": str(report_path),
            "launched_at": _timestamp(),
            "launch": launch,
            "worker_fix_needed": worker_fix_needed,
            "verdict": verdict_block,
            "verdict_error": verdict_error,
            "completed": completed,
        }
        atomic_write_json(result_path, result)
        if completed:
            atomic_write_json(completed_path, result)
        reference = {
            "category": category,
            "fingerprint": fingerprint,
            "result_path": str(completed_path if completed else result_path),
            "report_path": str(report_path),
            "worker_fix_needed": worker_fix_needed,
            "verdict": verdict_block,
            "verdict_error": verdict_error,
            "completed": completed,
            "retryable": not completed,
            "deduplicated": False,
        }
        checkpoint = self._load_checkpoint(row)
        reports = list(checkpoint.get("incident_reports", []))
        if completed and not any(
            isinstance(item, dict) and item.get("fingerprint") == fingerprint
            for item in reports
        ):
            reports.append(reference)
        worktree = self.active_worktree
        if worktree is not None:
            self._save_checkpoint(row, worktree, incident_reports=reports)
        elif checkpoint:
            checkpoint["incident_reports"] = reports
            checkpoint["updated_at"] = _timestamp()
            atomic_write_json(self._checkpoint_path(row), checkpoint)
        return reference

    def ensure_inventory(self, row: QueueRow) -> Path:
        return ensure_inventory(
            row, self.config, selected_approval=self.selected_approval
        )

    def _task_repo(self) -> Path:
        if self.active_worktree is not None:
            return self.active_worktree
        return self.config.baseline_repo

    def _write_summary(self) -> None:
        counts: dict[str, int] = {}
        for task in self.tasks.values():
            status = str(task.get("status", "recovery_pending"))
            counts[status] = counts.get(status, 0) + 1
        atomic_write_json(
            self.config.state / "summary.json",
            {
                "schema_version": SUMMARY_SCHEMA,
                "updated_at": _timestamp(),
                "current": self.current,
                "active": self.current,
                "counts": dict(sorted(counts.items())),
                "tasks": self.tasks,
            },
        )

    # Result keys that describe one row close. They are replaced, not merged,
    # so a retry's record never carries the previous attempt's fairness
    # verdict or candidate next to a new inspect error.
    _ROW_RESULT_KEYS = frozenset(
        {
            "error",
            "inspect_error",
            "verification_attempt",
            "candidate_snapshot",
            "incident_report",
            "delivery",
            "worker_fix_incident",
            "saw_hard_blocker",
            "release_id",
            "task_generation_id",
            "modified",
            "evidence_path",
            "policy_violation",
            "unexpected",
            "queued",
            "retry_budget_exhausted",
            "host_floor",
            "incident_reports",
            "fairness_verdict",
            "verifier_error",
            "retention_findings",
            "delivery_error",
        }
    )

    def _set_status(self, row: QueueRow, status: str, **fields: object) -> None:
        task = self.tasks.setdefault(row.workbook_id, {})
        if fields or status == "generated":
            # A row close replaces the previous close's detail; a generated
            # row carries no failure detail at all.
            for key in self._ROW_RESULT_KEYS:
                task.pop(key, None)
        if status == "worker_fix_needed":
            fields = {
                **fields,
                "worker_fix_baseline_hash": self.baseline_hash,
                "worker_fix_overlay": self.worker_overlay_id,
            }
            checkpoint = self._load_checkpoint(row)
            if checkpoint:
                checkpoint.update(
                    status=status,
                    worker_fix_baseline_hash=self.baseline_hash,
                    updated_at=_timestamp(),
                )
                atomic_write_json(self._checkpoint_path(row), checkpoint)
        task.update(
            batch_id=row.batch_id,
            status=status,
            updated_at=_timestamp(),
            **fields,
        )
        self.current = (
            row.workbook_id if status == "recovery_pending" and fields.get("running")
            else None
        )
        if status == "recovery_pending" and not fields:
            self.current = row.workbook_id
        self._write_summary()

    def _prepare_ready(self, row: QueueRow) -> ReadyRecord:
        _validate_source(row)
        published = self.config.state / "published"
        ready = self.config.state / "ready"
        record = convert_and_publish(
            row.run_source_path,
            workbook_id=row.workbook_id,
            private_root=self.config.state / "conversion-private",
            published_dir=published,
            ready_dir=ready,
        )
        if record.xlsx_sha256 != row.run_source_sha256:
            raise PolicyViolation("validated ready record does not bind queue source")
        record.validate_workbook(published)
        atomic_write_json(
            self.config.state / "queue-bindings" / f"{row.workbook_id}.json",
            {
                "queue": {
                    **asdict(row),
                    "run_source_path": str(row.run_source_path),
                },
                "ready_record": asdict(record),
            },
        )
        _validate_source(row)
        return record

    def _agent_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        repo = self._task_repo()
        environment["PATH"] = (
            f"{self.config.baseline_repo / '.venv/bin'}:"
            f"{self.config.agent_binary.parent}:"
            f"{environment.get('PATH', '')}"
        )
        environment["PYTHONPATH"] = str(repo)
        environment["PYTHONNOUSERSITE"] = "1"
        # Anything pytest or the interpreter would leave in the worktree shows
        # up as a code change in the fairness diff. Keep both quiet at the
        # source as well as excluding them from every snapshot.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        addopts = environment.get("PYTEST_ADDOPTS", "").strip()
        if "no:cacheprovider" not in addopts:
            environment["PYTEST_ADDOPTS"] = (
                f"{addopts} -p no:cacheprovider".strip()
            )
        environment.pop("PYTHONHOME", None)
        return environment

    def _ladder_step(self, attempt_index: int) -> str:
        if attempt_index < len(RECOVERY_LADDER):
            return RECOVERY_LADDER[attempt_index]
        return RECOVERY_LADDER[-1]

    def _prompt_ladder_index(self, attempt_index: int) -> int:
        if self._pinned_ladder_index is not None:
            return self._pinned_ladder_index
        return attempt_index

    def _worktree_has_progress(self, row: QueueRow) -> bool:
        """True when the last attempt left real pipeline artifacts behind.

        A paperwork retry only makes sense when there is something to keep. An
        attempt that never published a source generation gets the ordinary
        ladder advance instead.
        """

        repo = self._task_repo()
        generations = repo / "source_out" / row.workbook_id / "generations"
        if generations.is_dir() and any(
            entry.is_dir() and not entry.name.startswith(".")
            for entry in generations.iterdir()
        ):
            return True
        return (repo / "release_out" / row.workbook_id / "current-release.json").is_file()

    def _generation_inventory(self, row: QueueRow) -> dict[str, object]:
        """IDs already present under the pipeline roots for this workbook."""

        repo = self._task_repo()
        wb = row.workbook_id

        def ids(directory: Path) -> list[str]:
            if not directory.is_dir():
                return []
            names = sorted(
                entry.name
                for entry in directory.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            )
            return names[-5:]

        return {
            "source_out": ids(repo / "source_out" / wb / "generations"),
            "seg_out": ids(repo / "seg_out" / wb / "generations"),
            "release_out/releases": ids(repo / "release_out" / wb / "releases"),
            "release_out/task-generations": ids(
                repo / "release_out" / wb / "task-generations"
            ),
            "current-release.json": (
                repo / "release_out" / wb / "current-release.json"
            ).is_file(),
        }

    def _seed_amd_retry(
        self,
        row: QueueRow,
        checkpoint: Mapping[str, object],
        cumulative_attempts: int,
    ) -> dict[str, object] | None:
        """One targeted a.md retry for a row whose last delivery failed on a.md.

        The 2026-09-03 fleet parked rows after the single paperwork retry
        was spent on the first-reported defect. The artifacts in the
        retained worktree are valid; only a.md is not.
        """

        if cumulative_attempts < 1 or checkpoint.get("amd_retry_seeded"):
            return None
        evidence_path = self.config.state / "task-evidence" / f"{row.workbook_id}.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        error = str(evidence.get("delivery_error") or "")
        gate = "a.md recovery record"
        if "a.md is not a complete recovery record" not in error:
            # The evidence file's top-level error is rewritten on resume (the
            # inventory note lands first), so read the durable fairness result.
            fairness = checkpoint.get("fairness_result")
            fairness = fairness if isinstance(fairness, Mapping) else {}
            if fairness.get("passed") is not False:
                return None
            if fairness.get("verifier_error"):
                # Unusable verifier output is not an a.md defect.
                return None
            report = str(fairness.get("report") or evidence.get("fairness_report") or "")
            verdict = fairness.get("verdict")
            checks = (
                dict(verdict.get("checks") or {})
                if isinstance(verdict, Mapping) and isinstance(verdict.get("checks"), Mapping)
                else None
            )
            if checks is not None:
                # Structured verdict: the paperwork path applies only when
                # a.md is the sole failing check. Any other failing check is
                # a task defect the ladder must handle.
                failing = {name for name, value in checks.items() if value == "fail"}
                if failing != {"recovery_record"}:
                    return None
            elif re.search(
                r"(?is)(?:(?:a\.md|recovery record)[^\n]{0,120}\bFAIL\b"
                r"|a\.md[^\n]{0,80}not (?:an exact|a complete) recovery record"
                r"|command lines? contain)",
                report,
            ) is None:
                return None
            gate = "a.md recovery record (as judged by the fairness verifier)"
            error = (
                "The fairness verifier FAILED this bundle and its report names "
                "a.md defects (abbreviated or summarized command lines, an "
                "incomplete changed-files inventory). Rewrite a.md with every "
                "exact command as run and every changed path named. If the "
                "report ALSO names task-content defects (units, solvability, "
                "disclosure), fix those in this worktree with the smallest "
                "change, rebuild only the affected artifacts, and republish. "
                "The full report is in the durable fairness result above. "
                "Command completeness means: every command you executed for "
                "this workbook appears verbatim, including failed invocations "
                "and their corrections, the uv smoke, grader and extractor "
                "imports, naturalization submit/reopen/accept/apply/verify, "
                "disclosure review, oracle, extraction, recovery-gate, freeze, "
                "and publish commands, in order, one per line in a fenced block; "
                "no prose summaries and no ellipses. The controller's own "
                "command log for this row is under "
                f"{self.config.state / 'recovery-evidence' / row.workbook_id}/ "
                "(recovery-evidence/v1: shell_commands per attempt); the "
                "verifier checks a.md against it, so copy commands from there "
                "rather than from memory. Do not write the task "
                "generation id of the bundle a.md ships in; republishing changes "
                "it. Refer to it as the generation bound in the release manifest."
            )
        changed = sorted(
            item.strip()
            for chunk in re.findall(r"changed files not named in a\.md: ([^;]+)", error)
            for item in chunk.split(",")
            if item.strip()
        )
        self._save_checkpoint(row, self.active_worktree, amd_retry_seeded=True)
        return {
            "gate": gate,
            "error": error,
            "attempt": cumulative_attempts,
            "retried_from": None,
            "changed_paths": changed,
            "seeded_on_resume": True,
        }

    def _retry_context_text(
        self, row: QueueRow, prior_attempt: Mapping[str, object] | None
    ) -> str:
        """Extra retry context: failed gate, prior final tail, existing IDs."""

        if prior_attempt is None:
            return ""
        parts: list[str] = []
        retry = self._paperwork_retry
        if retry is not None:
            parts.append(
                f"""
The previous attempt failed ONLY the {retry.get('gate')} gate. The pipeline
artifacts in this worktree are valid; do not rebuild them and do not change
strategy. Fix the gate below and rerun only that gate, then re-publish the
release and finish with the declaration line.
Failed gate error:
{str(retry.get('error', ''))[:2000]}
""".strip()
            )
            changed_paths = retry.get("changed_paths") or []
            if changed_paths:
                parts.append(
                    "The delivery gate will require a.md to name every one of "
                    "these changed paths verbatim, under the changed-files "
                    "section: " + ", ".join(str(p) for p in changed_paths) + ". "
                    "A self-check that calls validate_recovery_amd without "
                    "modification evidence is not the delivery gate."
                )
        cone_retry = self._cone_retry
        if cone_retry is not None:
            findings = cone_retry.get("findings") or []
            parts.append(
                f"""
The previous attempt published a release but failed the {RETENTION_GATE} gate:
auto-included outputs were excluded from seg_out/{row.workbook_id}/curation.toml
without an allowed reason marker. Findings:
{chr(10).join('- ' + str(item) for item in findings[:20])}
Re-segment with `xl_segment.py --prune-failing-outputs` on the existing
curation.toml (do not pass --recurate). Keep every output the verifier does
not implicate. If you must exclude an output by hand, mark the include line
`include = false  # excluded: <code> <evidence>` using only the codes listed
in this prompt. Then rebuild inputs, disclosure, naturalization, grader and
the release, and finish with the declaration line.
""".strip()
            )
        memory_kill = self._memory_kill_text(prior_attempt)
        if memory_kill:
            parts.append(memory_kill)
        reentry = self._reentry_text(row)
        if reentry:
            parts.append(reentry)
        evidence_dir = self.config.state / "recovery-evidence" / row.workbook_id
        if evidence_dir.is_dir():
            prior_evidence = sorted(evidence_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if prior_evidence:
                parts.append(
                    "Controller evidence from the previous candidate (attempt "
                    "outcomes, the shell commands actually run, changed files, "
                    f"incident verdicts): {prior_evidence[-1]}. Read it before "
                    "repeating a command that already failed; a.md is checked "
                    "against the next version of this file."
                )
        pruning_paths = self._pruning_artifacts(row)
        if pruning_paths:
            parts.append(
                "Segmentation pruning records already in this worktree (read them "
                "before touching curation.toml): " + ", ".join(pruning_paths)
            )
        inventory = self._generation_inventory(row)
        parts.append(
            "Generations already present in this worktree (reuse them): "
            + json.dumps(inventory, sort_keys=True)
        )
        tail = self._attempt_final(prior_attempt)[-PAPERWORK_FINAL_TAIL_CHARS:]
        if tail.strip():
            parts.append(
                "Tail of the previous attempt's final output:\n" + tail.strip()
            )
        return "\n\n".join(parts)

    def _reentry_text(self, row: QueueRow) -> str:
        """Where to re-enter the 16 gates for the last recorded failure.

        Ported from the FCP2 orchestrator's re-entry map. The failure record
        (class, subclass, gate) is mechanical or came from a structured
        verdict; this turns it into the earliest gate whose artifacts must be
        rebuilt and the artifacts downstream of it, so the retry does not
        start over at gate 1 for a gate-13 defect, and does not patch gate
        16 paperwork over a gate-6 segmentation error.
        """

        record = read_failure_record(self.config.state, row.workbook_id)
        if not record or record.get("class") in {None, "retry_budget"}:
            return ""
        failure_class = str(record.get("class"))
        subclass = str(record.get("subclass") or "")
        gate = record.get("gate")
        gate = gate if isinstance(gate, int) else None
        if gate is None:
            gate = _REENTRY_SUBCLASS_GATE.get(subclass)
        if gate is None and subclass.startswith("prose_"):
            gate = 13
        if gate is None:
            gate = _REENTRY_DEFAULT_GATE.get(failure_class)
        if gate is None:
            return ""
        entry = _REENTRY_GATES.get(gate)
        if entry is None:
            return ""
        name, artifacts, recipe = entry
        downstream = [
            _REENTRY_GATES[number][0]
            for number in sorted(_REENTRY_GATES)
            if number > gate
        ]
        mode = self._instruction_mode(row)
        prose_flag = " --gdpval-prose" if mode == "gdpval_prose" else ""
        if gate <= 3:
            how = (
                "Fix the cause with at most three hand commands (source health, "
                "AST, segmentation), then rerun the FULL build_one.sh"
                f"{prose_flag} so every downstream artifact is regenerated from "
                "the corrected one; do not hand-patch later gates."
            )
        elif gate <= 12:
            how = (
                "Fix the failing input or artifact in place, then rerun the FULL "
                f"build_one.sh{prose_flag}; gates 4-12 are cheap and their outputs "
                "must agree with each other."
            )
        elif gate == 13:
            how = (
                "Gate 13 is a model gate. Rerun it with build_one.sh "
                f"--resume-after-naturalization{prose_flag} in the row's recorded "
                f"mode ({mode}); never switch modes mid-row."
            )
        else:
            how = (
                "Gates 14, 15, 15.5 and 16 are rerun by hand in order: HTTP "
                "oracle, grader smoke, additional-assumptions Q&A, then re-publish "
                "the release; do not rerun build_one.sh for a paperwork or "
                "promotion defect."
            )
        lines = [
            f"Re-entry point. The last recorded failure was class {failure_class}"
            + (f", subclass {subclass}" if subclass and subclass != "unspecified" else "")
            + f", attributed to gate {gate} ({name}).",
            f"Re-enter at gate {gate}. Artifacts owned by that gate that must be "
            f"rebuilt or re-checked: {', '.join(artifacts)}.",
            f"Recipe: {recipe}",
            f"How: {how}",
        ]
        if downstream:
            lines.append(
                "Then rerun every later gate in order ("
                + ", ".join(downstream)
                + "); do not hand-edit their artifacts."
            )
        if gate > 1:
            lines.append(
                "Do not rebuild artifacts from gates before the re-entry point "
                "unless the failing gate proves they are wrong; they are valid "
                "in this worktree."
            )
        if failure_class == "controller":
            lines.append(
                "The failure was in the recovery worker itself, not the task. "
                "Rebuild only what the error names; the lane fix step handles "
                "the worker."
            )
        return "\n".join(lines)

    @staticmethod
    def _memory_kill_text(prior_attempt: Mapping[str, object]) -> str:
        """Tell the retry the prior attempt died at the memory cap, and why.

        With no throttle band the kernel kills the child (``xl_segment`` or
        the AST builder) the moment the scope hits ``MemoryMax``; the agent
        sees a dead process and, without this, tends to rerun the same
        full-scope step and die the same way. The kill is recorded by the
        runner as ``limit_reason == "cgroup_oom"`` or as a non-zero
        ``memory.events oom_kill``; either one turns into an instruction to
        shrink the work (restrict AST sheets, narrow the cone) before rerunning.
        """

        limits = prior_attempt.get("execution_limits")
        if not isinstance(limits, Mapping):
            return ""
        events = limits.get("memory_events")
        oom_kills = 0
        if isinstance(events, Mapping):
            try:
                oom_kills = int(events.get("oom_kill", 0) or 0)
            except (TypeError, ValueError):
                oom_kills = 0
        reason = str(limits.get("limit_reason", "") or "")
        if reason != "cgroup_oom" and oom_kills <= 0:
            return ""
        cap = limits.get("memory_max_bytes")
        cap_text = (
            f"{int(cap) / 1024**3:.1f} GiB"
            if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0
            else "the lane cap"
        )
        peak = limits.get("peak_rss_bytes")
        peak_text = (
            f" The scope reached {int(peak) / 1024**3:.1f} GiB before the kill."
            if isinstance(peak, int) and not isinstance(peak, bool) and peak > 0
            else ""
        )
        return (
            f"The previous attempt was killed by the memory cap ({cap_text}; "
            f"limit_reason={reason or 'cgroup_oom'}, oom_kill={oom_kills})."
            f"{peak_text} The cap does not change on retry: do not rerun the "
            "same full-scope AST build or segmentation. Shrink the work "
            "first: restrict the AST to the sheets the cone needs "
            "(xl_ast_graph.py --sheet, one per sheet) or otherwise cut the "
            "graph the segmentation loads. Only if that is not enough, drop "
            "outputs from the existing seg_out/<wb>/curation.toml (do not pass "
            "--recurate), largest cone first, and mark every dropped line "
            "`include = false  # excluded: memory <peak and cap>`; the worker "
            "checks that marker against this kill record. Then rerun the "
            "failed step."
        )

    def _pruning_artifacts(self, row: QueueRow) -> list[str]:
        """Pruning log and last verification left by xl_segment, if any."""
        seg_dir = self._task_repo() / "seg_out" / row.workbook_id
        found = []
        for name in ("pruning-log.json", "last-verification.json"):
            path = seg_dir / name
            if path.is_file() and not path.is_symlink():
                found.append(str(path))
        return found

    def _inner_timeout(self, row: QueueRow) -> int:
        """AST/segmentation budget the prompt hands the agent for this row."""

        return (
            self.config.large_timeout_seconds
            if row.workbook_id in self.config.large_workbooks
            else self.config.measured_timeout_seconds
        )

    def _generation_timeout(self, row: QueueRow) -> float:
        """Outer wall clock for one generation attempt.

        Never below the inner budget plus the downstream margin: the agent is
        told it may spend ``inner_timeout`` on the AST alone, so an outer clock
        shorter than that plus the remaining gates would kill rows the prompt
        promised were in budget.
        """

        return float(
            max(
                self.config.timeout_seconds,
                self._inner_timeout(row) + DOWNSTREAM_MARGIN_SECONDS,
            )
        )

    def _generation_prompt(
        self,
        row: QueueRow,
        workbook: Path,
        inventory: Path | None,
        prior_attempt: Mapping[str, object] | None,
        attempt_index: int,
    ) -> str:
        inner_timeout = self._inner_timeout(row)
        outer_timeout = int(self._generation_timeout(row))
        repo = self._task_repo()
        skill = repo / ".cursor/skills/create-harbor-task/SKILL.md"
        overlay = repo / ".cursor/skills/create-harbor-task/RECOVERY.md"
        durable_fairness = self._load_checkpoint(row).get("fairness_result")
        fairness_text = (
            "\nThe last durable fairness result is:\n"
            + json.dumps(durable_fairness, sort_keys=True, default=str)
            if durable_fairness is not None
            else ""
        )
        retry_text = ""
        manual_codes = "; ".join(
            f"{code} ({hint})"
            for code, hint in output_retention.MANUAL_EXCLUSION_CODES.items()
        )
        ladder_index = self._prompt_ladder_index(attempt_index)
        if prior_attempt is not None and self._paperwork_retry is not None:
            retry_text = f"""
This is recovery attempt {attempt_index + 1}, a targeted retry. Inspect the
preserved prior attempt at {prior_attempt.get('attempt_path')}. Stay on this
ladder step: {self._ladder_step(ladder_index)}.
{self._retry_context_text(row, prior_attempt)}
{fairness_text}
""".strip()
        elif prior_attempt is not None:
            retry_text = f"""
This is recovery attempt {attempt_index + 1}. Inspect the preserved prior
attempt at {prior_attempt.get('attempt_path')}. FULL_RERUN_BLOCKER: HARD from a
prior attempt is not a stop. Use this ladder step now:
{self._ladder_step(ladder_index)}.
The last durable attempt record is:
{json.dumps(dict(prior_attempt), sort_keys=True, default=str)}
{fairness_text}
{self._retry_context_text(row, prior_attempt)}
Do not repeat the same failed strategy.
""".strip()
        else:
            retry_text = f"""
This is recovery attempt {attempt_index + 1}. Start with this ladder step:
{self._ladder_step(ladder_index)}.
""".strip()
        # Opt-in: HARBOR_GDPVAL_PROSE=1 (set fleet-wide via deploy_fleet.sh
        # --lane-env) swaps gate 13 to the heading-free GDPval prose skill.
        # The row's recorded mode wins over the lane environment: a resume
        # with the other flag fails closed in build_one.sh.
        instruction_mode = self._instruction_mode(row)
        env_mode = (
            "gdpval_prose" if os.environ.get("HARBOR_GDPVAL_PROSE") == "1" else "naturalize"
        )
        if env_mode != instruction_mode:
            retry_text += f"""

Gate 13 mode for this row is {instruction_mode} (recorded on its first
attempt); the lane environment says {env_mode}. Follow the recorded mode.
""".rstrip()
            if self.active_worktree is not None:
                # Recorded so the report can see lanes relaunched with the
                # other flag; the row itself is unaffected.
                self._save_checkpoint(
                    row,
                    self.active_worktree,
                    mode_mismatch={
                        "recorded": instruction_mode,
                        "lane_environment": env_mode,
                        "at": _timestamp(),
                    },
                )
        if instruction_mode == "gdpval_prose":
            retry_text += """

Gate 13 mode: GDPval prose. Pass --gdpval-prose to every build_one.sh call
(initial and --resume-after-naturalization). Run /gdpval-prose-instruction
(.cursor/skills/gdpval-prose-instruction/SKILL.md) instead of
/naturalize-finance-task-instruction, with state in
runs/<WB>-instruction-prose and prose_recovery.py for init, submit --body,
accept/reject, apply, verify-applied, verify-metadata. a.md must cite those
exact commands. Ladder step (e) in this mode means: keep every protected block
(fences, table rows, list items, workbook-notes markers on their own lines)
byte-for-byte, except the four research tool-list items, which may be omitted
as a whole group but never trimmed or edited, and rewrite only the surrounding
prose until validate_prose_rewrite passes. Follow writer_brief.md: it names the
droppable MCP-mechanics sentences and the token budget; never write "removed",
"cells are blank", "no formulas", "remaining inputs", or "mock". The
two-attempt cap is per state dir; when it
reads `exhausted`, instruction.md is still the untouched source, so remove
runs/<WB>-instruction-prose and init again.
""".rstrip()
        if inventory is not None:
            inventory_text = f"""
The queue row is present in the commit-pinned approved inventory
{inventory}, approved by {self.config.inventory_registry} under approval ID
{self.config.approval_batch_id}. Use that inventory only for this exact
workbook. If a restricted route still fails inventory binding, apply a
task-local prepare/publication fix in this worktree.
""".strip()
        else:
            inventory_text = f"""
No pinned approved inventory currently contains workbook {row.workbook_id}.
That is an orchestration gap, not a reason to stop. If source health is
`pass`, continue without restricted evidence. If the route is restricted or
would have been `unsupported`, apply a task-local pipeline fix in this
worktree so source/AST publication can proceed, then recurate away from
uncomputable cells or bind a disclosed deterministic assumption. Do not
invent an approval registry.
""".strip()
        return f"""
Read {skill}. Then read {overlay} if it exists. Fail-closed "stop / impossible /
HARD" rules in the skill are SUPERSEDED for this recovery worker. You MUST
publish a complete immutable release and current-release.json pointer for
workbook {row.workbook_id}. Never finish as if this workbook is impossible.
Never treat a skill stop as success.

Process only workbook {row.workbook_id}, using the validated, private run copy
{workbook}. Work only in the isolated worktree {repo}. The shared baseline
{self.config.baseline_repo} is read-only. Do not read or print .env. Do not
commit or push. The original run source {row.run_source_path} is immutable and
hash-bound to {row.run_source_sha256}; never modify, replace, rename, or
rewrite it.

{inventory_text}

{retry_text}

Recovery ladder (use the assigned step; escalate only with evidence):
(a) normal path
(b) smallest task-local pipeline, formula, or validator fix
(c) disclosed deterministic assumption for volatile/external cells
(d) prune only verifier-implicated outputs: rerun xl_segment.py with
    --prune-failing-outputs on the existing curation.toml; keep every output
    that verifies
(e) deterministic naturalization reconstruction
(f) a different strategy if the same failure repeats

On `unsupported` or `insufficient_evidence`, do not stop. Implement the missing
semantics, prune the outputs whose cone needs the feature (step d), or bind a
disclosed frozen value. On `recalc_candidate` without a macOS Excel runner, do
not stop: prune to the outputs that do not need empty caches, or bind disclosed
cached values already present in the source. On naturalization failure,
reconstruct protected sections byte-for-byte and retry editable spans until the
validator passes; two failures is not a limit.

Output cone rules (checked by the worker's {RETENTION_GATE} gate before delivery):
- Always run xl_segment.py with --prune-failing-outputs. When strict
  verification fails it flips `include = false  # pruned: <codes>` only for
  the outputs the verifier implicates, then re-verifies the rest. If it exits
  with code {PRUNE_EXIT_CODE}, the pruned curation is already on disk: rerun the
  same command. seg_out/<wb>/last-verification.json lists the blame per output
  and seg_out/<wb>/pruning-log.json the rounds.
- Never flip `include = false` by hand for an output the verifier did not
  implicate, and never keep one output when several verify. The task is meant
  to be as hard as the workbook allows.
- A hand exclusion of an auto-included output must carry a marker on its
  include line, `include = false  # excluded: <code> <evidence>`, with one of:
  {manual_codes}.
  More than max(3, 25% of auto-included) hand exclusions need memory, budget,
  ast_scope, or cone_wide as the cause.
- Cone-wide failures (last-verification.json `cone_wide` non-empty) are the
  case for a fix, a disclosed assumption, or `excluded: cone_wide:<code>`.
- a.md must list every pruned or excluded output with its reason code.

You MAY edit xl_seg implementation, xl_source_*.py, xl_inventory_approval.py,
xl_segment.py, skill markdown, and naturalization scripts in this isolated
worktree. You MUST NOT change source workbooks, graders, rubrics, answer keys,
or acceptance thresholds. Do not weaken, bypass, skip, special-case, or
fabricate gate success.

The AST build (xl_source_recalc.py prepare / xl_ast_graph.py) may run for at
most {inner_timeout} seconds for this workbook. Do not wrap xl_segment.py in
`timeout` or any shorter clock: the worker's own attempt clock
({outer_timeout} seconds) is the only limit, and a segmentation killed midway
wastes the whole graph build. xl_segment.py writes seg_out/<wb>/curation.toml
before verification starts; to change the output cone, edit the `include`
flags in that file and rerun xl_segment.py WITHOUT --recurate (--recurate
discards your edits and recomputes from scratch). If a previous run was
killed, its curation.toml is already on disk: reuse it.

Success is only a complete immutable release for {row.workbook_id}. Ending the
agent, building source/AST only, or printing FULL_RERUN_BLOCKER: HARD is not
success. If you print that HARD line, the worker will ignore it as a completion
signal and retry with the next ladder step.

If any recovery step other than (a) is used, write literal a.md at the root of
the final {row.workbook_id}-outputs bundle. a.md must record: exact blocker,
evidence and failing stage, exact fix, changed files, before/after, commands,
and verifier outcome. README.md is optional and useful; a.md is required for
recovered tasks. Finish with exactly RECOVERY_USED: YES. If the normal path
published a complete release with no recovery, finish with RECOVERY_USED: NO.

Any nested generation or review agent must be fresh and use
{self.config.model}. Preserve exactly {self.config.lane_count} generation lanes.
The separate incident investigator is read-only and is not a generation lane.
""".strip()

    def _run_generation(
        self,
        row: QueueRow,
        record: ReadyRecord,
        inventory: Path | None,
        prior_attempt: Mapping[str, object] | None,
        attempt_index: int,
    ) -> dict[str, object]:
        workbook = record.validate_workbook(self.config.state / "published")
        command = [
            str(self.config.agent_binary),
            "-p",
            "--force",
            "--trust",
            "--output-format",
            "stream-json",
            "--model",
            self.config.model,
            "--workspace",
            str(self._task_repo()),
            self._generation_prompt(
                row, workbook, inventory, prior_attempt, attempt_index
            ),
        ]
        reservation = self._generation_reservation(row)
        if reservation:
            self._resize_lease(reservation, stage="generation")
        scope = (
            scope_unit_name(self.config.lane_id, row.workbook_id, attempt_index)
            if self.memory_scope_enabled
            else None
        )
        limit = self._generation_limit(reservation)
        environment = self._agent_environment()
        # xl_segment --prune-failing-outputs only starts a re-proof that fits
        # the attempt clock. Absolute, because the AST build runs first and
        # the segmentation process cannot know when the attempt started.
        outer_timeout = self._generation_timeout(row)
        started_at = time.time()
        self._attempt_started_at = started_at
        environment["XL_SEGMENT_DEADLINE_EPOCH"] = str(
            int(started_at + max(0.0, outer_timeout - DOWNSTREAM_MARGIN_SECONDS))
        )
        result = run_ready_attempt(
            record,
            command,
            state_dir=self.config.state / "generation",
            timeout_seconds=outer_timeout,
            cwd=self._task_repo(),
            env=environment,
            memory_scope=scope,
            memory_limit_bytes=limit,
            memory_poll_seconds=GENERATION_MEMORY_POLL_SECONDS,
            stall_seconds=(
                float(self.config.generation_stall_seconds)
                if self.config.generation_stall_seconds > 0
                else None
            ),
        )
        if reservation:
            result = {
                **result,
                "memory_reserved_bytes": reservation,
                "memory_limit_bytes": limit,
                "memory_scope": scope or "",
            }
            self._record_generation_observation(row, result, reservation)
        return result

    def _generation_limit(self, reservation: int) -> int | None:
        """Hard ceiling for one attempt: the lane's share of the host.

        The reservation is an admission estimate and stays the thing lanes
        compete over. The ceiling is deliberately looser: the larger of the
        old 1.5x headroom and an equal split of the budget after the unscoped
        allowance, capped at the budget. Every lane's ceiling then fits in the
        host at once, so a true runaway is still stopped before it can starve
        sshd on a swapless host, while an ordinary underestimate no longer
        pins the row against MemoryHigh reclaim for hours.
        """

        if not reservation or self.budget is None:
            return None
        total = int(self.budget.total_bytes)
        lanes = max(1, int(self.config.lane_count))
        share = max(0, total - int(self.config.memory_unscoped_allowance_bytes))
        share //= lanes
        return int(min(total, max(reservation * MEMORY_LIMIT_HEADROOM, share)))

    def _record_generation_observation(
        self,
        row: QueueRow,
        result: Mapping[str, object],
        reservation: int,
    ) -> None:
        """Log what the attempt really used, for recalibration.

        Only a completed attempt sets the baseline. A killed or timed-out run
        reports a truncated peak, and letting that into the quantile would pull
        future estimates down, which is the dangerous direction.
        """

        profile = self._profiles.get(row.workbook_id)
        if profile is None:
            return
        limits = result.get("execution_limits")
        if not isinstance(limits, Mapping):
            return
        try:
            peak = int(limits.get("peak_rss_bytes", 0) or 0)
        except (TypeError, ValueError):
            return
        if peak <= 0:
            return
        extra: dict[str, object] = {
            "limit_reason": str(limits.get("limit_reason", "")),
            "memory_events": limits.get("memory_events") or {},
            # Pinned here so both copies of the record carry one timestamp and
            # dedupe cleanly when the logs are pooled.
            "recorded_at": _timestamp(),
        }
        raw_peak = limits.get("cgroup_peak_bytes")
        if isinstance(raw_peak, int) and not isinstance(raw_peak, bool):
            extra["cgroup_peak_bytes"] = raw_peak
        # Both logs carry the same record with the same recorded_at, so the
        # reader can pool them without double counting.
        targets = [self._observations_path()]
        shared = self._shared_observations_path()
        if shared is not None:
            targets.append(shared)
        for target in targets:
            with contextlib.suppress(MemoryGovernorError, OSError):
                record_observation(
                    target,
                    profile,
                    status=str(result.get("status", "")),
                    observed_peak_bytes=peak,
                    reserved_bytes=reservation,
                    workbook_id=row.workbook_id,
                    lane_id=self.config.lane_id,
                    extra=extra,
                )

    def _attempt_resource_evidence(self, row: QueueRow) -> dict[str, bool]:
        """Whether any attempt of this row was memory-killed or ran out of time.

        Read from the durable attempt records, never from the agent's own
        claims: a ``memory``/``budget`` exclusion marker is only accepted when
        the worker itself saw the kill.
        """

        evidence = {"memory": False, "budget": False}
        attempts_root = (
            self.config.state / "generation" / "attempts" / row.workbook_id
        )
        if attempts_root.is_dir():
            for result_path in sorted(attempts_root.glob("attempt-*/result.json")):
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(result, dict):
                    continue
                limits = result.get("execution_limits")
                limits = limits if isinstance(limits, Mapping) else {}
                reason = str(limits.get("limit_reason", "") or "")
                events = limits.get("memory_events")
                oom_kills = 0
                if isinstance(events, Mapping):
                    try:
                        oom_kills = int(events.get("oom_kill", 0) or 0)
                    except (TypeError, ValueError):
                        oom_kills = 0
                if reason == "cgroup_oom" or oom_kills > 0:
                    evidence["memory"] = True
                if result.get("status") == "timed_out" or reason in {
                    "wall_time",
                    "stall",
                }:
                    evidence["budget"] = True
        # A proof that took more than half the inner budget is budget evidence
        # too: the agent could not afford another full-cone round.
        last_verification = (
            self._task_repo() / "seg_out" / row.workbook_id / "last-verification.json"
        )
        if last_verification.is_file() and not last_verification.is_symlink():
            try:
                payload = json.loads(last_verification.read_text(encoding="utf-8"))
                runtime = float(payload.get("verification_runtime_s") or 0.0)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                runtime = 0.0
            if runtime > self._inner_timeout(row) / 2:
                evidence["budget"] = True
        return evidence

    def _retention_summary(self) -> dict[str, object]:
        """The retention verdict as the fairness prompt and the sidecar see it."""

        active = self._active_output_retention or {}
        record = active.get("record")
        summary: dict[str, object] = {
            "gate": active.get("gate", "not_evaluated"),
            "cone_retry_used": int(active.get("cone_retry_used") or 0),
            "findings": list(active.get("findings") or []),
        }
        if isinstance(record, Mapping):
            bands = record.get("bands") or {}
            summary.update({
                "schema_version": record.get("schema_version"),
                "threshold": record.get("threshold"),
                "candidates_listed": record.get("candidates_listed"),
                "candidates_auto_included": record.get("candidates_auto_included"),
                "retained": record.get("retained"),
                "pruned": record.get("pruned"),
                "manual_excluded": record.get("manual_excluded"),
                "adjudicated_excluded": record.get("adjudicated_excluded"),
                "unexplained_excluded": record.get("unexplained_excluded"),
                "reason_codes": list(record.get("reason_codes") or []),
                "pruned_bands": list(bands.get("pruned") or []),
                "manual_excluded_bands": list(bands.get("manual_excluded") or []),
                "unexplained_excluded_bands": list(
                    bands.get("unexplained_excluded") or []
                ),
            })
        else:
            summary["record"] = None
        return summary

    def _output_retention_gate(
        self, row: QueueRow, candidate: ReleaseCandidate
    ) -> dict[str, object]:
        """Every excluded auto-included output must carry an allowed reason.

        Reads the retention record the generation manifest carries into the
        bundle; a bundle without one was segmented by code that predates the
        record, which is itself a finding. Returns the record, the findings,
        and the worker evidence used to judge resource markers.
        """

        record = _bundle_output_retention(candidate.task_dir)
        evidence = self._attempt_resource_evidence(row)
        if record is None:
            # A bundle segmented by code that predates the record. Nothing to
            # judge, so no retry; the sidecar says ``no_record`` and the fleet
            # report flags it, which is how a stale baseline gets noticed.
            return {"record": None, "findings": [], "resource_evidence": evidence,
                    "no_record": True}
        findings = output_retention.gate_findings(
            record, resource_evidence=evidence
        )
        return {
            "record": record,
            "findings": findings,
            "resource_evidence": evidence,
            "no_record": False,
        }

    def _inspect_python(self) -> Path:
        repo = self._task_repo()
        for candidate in (
            repo / ".venv" / "bin" / "python",
            self.config.baseline_repo / ".venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return candidate
        return Path(sys.executable)

    def _inspect_release(self, row: QueueRow) -> ReleaseCandidate:
        repo = self._task_repo()
        payload = {
            "repo": str(repo),
            "workbook_id": row.workbook_id,
            "run_source_sha256": row.run_source_sha256,
            "required_files": list(REQUIRED_BUNDLE_FILES),
            "required_dirs": list(REQUIRED_BUNDLE_DIRECTORIES),
        }
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repo)
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("PYTHONHOME", None)
        try:
            result = subprocess.run(
                [str(self._inspect_python()), "-c", INSPECT_RELEASE_SCRIPT],
                input=json.dumps(payload),
                cwd=str(repo),
                env=environment,
                capture_output=True,
                text=True,
                timeout=INSPECT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            detail = str(error.stderr or error.stdout or "").strip()
            raise RecoveryError(
                f"inspect timed out after {INSPECT_TIMEOUT_SECONDS}s"
                + (f": {detail[:2000]}" if detail else "")
            ) from error
        data = _parse_inspect_output(result.stdout, result.stderr, result.returncode)
        _assert_inspect_candidate(row, repo, data)
        return _independent_release_candidate(row, repo, data)

    def _attempt_final(self, result: Mapping[str, object]) -> str:
        path = Path(str(result.get("attempt_path", ""))) / "final.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _attempt_segments(self, result: Mapping[str, object]) -> list[str]:
        path = Path(str(result.get("attempt_path", ""))) / "stream.jsonl"
        if not path.is_file():
            return []
        try:
            return extract_assistant_segments(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            return []

    def _attempt_transcript(self, result: Mapping[str, object]) -> str:
        """Assistant messages joined by newlines; final.txt when no stream.

        The CLI result field concatenates messages with nothing between
        them, so a trailing "RECOVERY_USED: YES" followed by a short
        acknowledgement message became "RECOVERY_USED: YESAll checks..."
        and the anchored REPAIR_LINE never matched (0500, attempt 8).
        """

        segments = self._attempt_segments(result)
        if segments:
            return "\n".join(segment.strip("\n") for segment in segments) + "\n"
        return self._attempt_final(result)

    def _record_verifier_error(self, row: QueueRow, reason: str, report_path: Path) -> None:
        """Count a verifier output problem on the checkpoint (fleet report reads it)."""

        checkpoint = self._load_checkpoint(row)
        errors = list(checkpoint.get("verifier_errors", []))
        errors.append({"reason": reason, "report_path": str(report_path), "at": _timestamp()})
        if self.active_worktree is not None:
            self._save_checkpoint(row, self.active_worktree, verifier_errors=errors[-10:])

    def _recovery_evidence_path(self, row: QueueRow, candidate: ReleaseCandidate) -> Path:
        return (
            self.config.state
            / "recovery-evidence"
            / row.workbook_id
            / f"{candidate.bundle_sha256}.json"
        )

    def _recovery_evidence_summary(
        self, row: QueueRow, candidate: ReleaseCandidate
    ) -> dict[str, object] | None:
        """Path + hash of the controller-written recovery evidence, if any."""

        path = self._recovery_evidence_path(row, candidate)
        if not path.is_file() or path.is_symlink():
            return None
        return {"path": str(path), "sha256": sha256_file(path)}

    def _write_recovery_evidence(
        self,
        row: QueueRow,
        candidate: ReleaseCandidate,
        modification_evidence: Mapping[str, object],
        modified: bool,
    ) -> Path:
        """Write ``state/recovery-evidence/<wb>/<bundle>.json`` for this candidate.

        Everything in it is observed by the controller, not declared by the
        agent: attempt outcomes from the attempt records, shell commands and
        file writes parsed from each attempt's stream, changed files from the
        code-state diff, incident and verifier verdicts from the checkpoint.
        """

        checkpoint = self._load_checkpoint(row)
        attempts_dir = self.config.state / "generation" / "attempts" / row.workbook_id
        attempts: list[dict[str, object]] = []
        if attempts_dir.is_dir():
            for attempt_dir in sorted(attempts_dir.iterdir()):
                if not attempt_dir.name.startswith("attempt-") or not attempt_dir.is_dir():
                    continue
                result_path = attempt_dir / "result.json"
                result: dict[str, object] = {}
                if result_path.is_file() and not result_path.is_symlink():
                    try:
                        loaded = json.loads(result_path.read_text(encoding="utf-8"))
                        result = loaded if isinstance(loaded, dict) else {}
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        result = {}
                commands: list[dict[str, object]] = []
                stream_path = attempt_dir / "stream.jsonl"
                commands_source = "unavailable"
                if stream_path.is_file() and not stream_path.is_symlink():
                    try:
                        commands = extract_tool_commands(
                            stream_path.read_text(encoding="utf-8", errors="replace")
                        )
                        # A killed or timed-out attempt has a truncated stream:
                        # the log is real but not the whole story.
                        commands_source = (
                            "stream" if result.get("status") == "completed" else "partial"
                        )
                    except OSError:
                        commands = []
                limits = result.get("execution_limits")
                limits = limits if isinstance(limits, Mapping) else {}
                attempts.append(
                    {
                        "attempt": attempt_dir.name,
                        "commands_source": commands_source,
                        "status": result.get("status"),
                        "return_code": result.get("return_code"),
                        "limit_reason": result.get("limit_reason") or limits.get("limit_reason"),
                        "started_at": result.get("started_at"),
                        "finished_at": result.get("finished_at"),
                        "shell_commands": [
                            {
                                "command": item.get("command"),
                                "exit_code": item.get("exit_code"),
                                "completed": item.get("subtype") == "completed",
                            }
                            for item in commands
                            if item.get("kind") == "shellToolCall" and item.get("command")
                        ],
                        "file_writes": sorted(
                            {
                                str(item.get("path"))
                                for item in commands
                                if item.get("kind") in {"writeToolCall", "editToolCall", "deleteToolCall"}
                                and item.get("path")
                            }
                        ),
                    }
                )
        changed: set[str] = set()
        diff = modification_evidence.get("baseline_diff")
        if isinstance(diff, Mapping):
            changed.update(str(key) for key in diff)
        during = modification_evidence.get("changed_during_run")
        if isinstance(during, (list, tuple)):
            changed.update(str(item) for item in during)
        incidents = [
            {
                "category": item.get("category"),
                "fingerprint": item.get("fingerprint"),
                "route": (item.get("verdict") or {}).get("route") if isinstance(item.get("verdict"), Mapping) else None,
                "subclass": (item.get("verdict") or {}).get("subclass") if isinstance(item.get("verdict"), Mapping) else None,
                "worker_fix_needed": item.get("worker_fix_needed"),
                "report_path": item.get("report_path"),
            }
            for item in checkpoint.get("incident_reports", [])
            if isinstance(item, Mapping)
        ]
        failure_record = read_failure_record(self.config.state, row.workbook_id)
        payload = {
            "schema_version": RECOVERY_EVIDENCE_SCHEMA,
            "workbook_id": row.workbook_id,
            "batch_id": row.batch_id,
            "bundle_sha256": candidate.bundle_sha256,
            "release_id": candidate.release_id,
            "task_generation_id": candidate.task_generation_id,
            "written_at": _timestamp(),
            "instruction_mode": checkpoint.get("instruction_mode") or self._instruction_mode(row),
            "modified": modified,
            "agent_declaration": modification_evidence.get("agent_declaration"),
            "changed_files": sorted(item for item in changed if item),
            "cumulative_attempt_count": checkpoint.get("cumulative_attempt_count"),
            "paperwork_retry_count": checkpoint.get("paperwork_retry_count", 0),
            "cone_retry_count": checkpoint.get("cone_retry_count", 0),
            "attempts": attempts,
            "incidents": incidents,
            "verifier_errors": list(checkpoint.get("verifier_errors", [])),
            "prior_failure": {
                "class": failure_record.get("class"),
                "subclass": failure_record.get("subclass"),
                "signature": failure_record.get("signature"),
                "occurrence_count": failure_record.get("occurrence_count"),
                "history": failure_record.get("history", []),
            }
            if failure_record
            else None,
            "worker_build_id": self.worker_overlay_id,
            "baseline_hash": self.baseline_hash,
        }
        path = self._recovery_evidence_path(row, candidate)
        atomic_write_json(path, payload)
        return path

    def _read_fairness_report(
        self,
        verification_result: Mapping[str, object],
        report_path: Path,
    ) -> str:
        stdout_text = verdict_transcript(
            self._attempt_segments(verification_result),
            "FAIRNESS_VERDICT:",
            self._attempt_final(verification_result),
        )
        file_text = ""
        if report_path.is_file() and not report_path.is_symlink():
            try:
                file_text = report_path.read_text(encoding="utf-8")
            except OSError:
                file_text = ""
        report = _combine_fairness_reports(stdout_text, file_text)
        if report_path.is_symlink():
            raise PolicyViolation("fairness report path is a symlink")
        atomic_write_text(report_path, report)
        return report

    def _modified(
        self,
        before: Mapping[str, str],
        after: Mapping[str, str],
        final_text: str,
        candidate: ReleaseCandidate | None,
    ) -> tuple[bool, dict[str, object]]:
        baseline_diff = _baseline_diff(after, self.baseline)
        changed_during_run = sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        declarations = REPAIR_LINE.findall(final_text)
        declaration = declarations[-1] if declarations else "MISSING"
        has_amd = bool(
            candidate is not None
            and (candidate.task_dir / "a.md").is_file()
        )
        modified = bool(
            baseline_diff or changed_during_run or declaration == "YES" or has_amd
        )
        return modified, {
            "baseline_diff": baseline_diff,
            "changed_during_run": changed_during_run,
            "agent_declaration": declaration,
            "has_amd": has_amd,
        }

    def _write_technical_evidence(
        self,
        row: QueueRow,
        candidate: ReleaseCandidate,
        vm_diff: Path,
        modification_evidence: Mapping[str, object],
        protected_snapshot: Mapping[str, str],
    ) -> tuple[Path, str]:
        task = candidate.task_dir
        input_workbook = task / "environment" / f"{row.workbook_id}-inputs.xlsx"
        if tree_sha256(task) != candidate.bundle_sha256:
            raise PolicyViolation("candidate bundle changed before technical evidence")
        assert_canonical_grader_provenance(self.config.baseline_repo, task)
        payload: dict[str, object] = {
            "schema_version": TECHNICAL_EVIDENCE_SCHEMA,
            "workbook_id": row.workbook_id,
            "queue_source": _ooxml_evidence(row.run_source_path),
            "delivered_workbook": _ooxml_evidence(input_workbook),
            "candidate": {
                "release_id": candidate.release_id,
                "task_generation_id": candidate.task_generation_id,
                "source_sha256": candidate.source_sha256,
                "bundle_sha256": candidate.bundle_sha256,
                "pointer_sha256": candidate.pointer_sha256,
                "release_manifest_sha256": candidate.release_manifest_sha256,
                "task_manifest_sha256": candidate.task_manifest_sha256,
                "required_bundle_complete": True,
            },
            "grader_provenance": {
                "matches_canonical_baseline": True,
                "run_grader_sha256": sha256_file(task / "tests" / "run_grader.py"),
                "finance_grader_sha256": _sha256_bytes(
                    _canonical_bytes(_grader_records(task / "tests" / "finance_grader"))
                ),
            },
            "inspect_attestation": {
                "passed": True,
                "checks": [
                    "current_release_pointer",
                    "release_manifest_identity",
                    "task_manifest_artifacts",
                    "source_sha256_binding",
                    "required_bundle_files",
                    "canonical_grader_provenance",
                ],
            },
            "protected_snapshot_sha256": _sha256_bytes(
                _canonical_bytes(protected_snapshot)
            ),
            "modification": {
                "agent_declaration": modification_evidence.get(
                    "agent_declaration"
                ),
                "has_amd": modification_evidence.get("has_amd") is True,
                "baseline_diff_paths": sorted(
                    str(path)
                    for path in (
                        modification_evidence.get("baseline_diff") or {}
                    )
                ),
                "changed_during_run": sorted(
                    str(path)
                    for path in (
                        modification_evidence.get("changed_during_run") or []
                    )
                ),
                "vm_diff_present": vm_diff.is_file() and not vm_diff.is_symlink(),
                "vm_diff_sha256": (
                    sha256_file(vm_diff)
                    if vm_diff.is_file() and not vm_diff.is_symlink()
                    else None
                ),
            },
        }
        digest = _technical_evidence_digest(payload)
        payload["evidence_sha256"] = digest
        root = (
            self.config.state
            / "verification-inputs"
            / row.workbook_id
            / candidate.bundle_sha256
        )
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"technical-evidence-{time.time_ns()}.json"
        _exclusive_write(path, _canonical_bytes(payload))
        return path, digest

    def _verifier_prompt(
        self,
        row: QueueRow,
        candidate: ReleaseCandidate,
        vm_diff: Path,
        technical_evidence: Path | None = None,
        evidence_digest: str = "",
    ) -> str:
        task = candidate.task_dir
        evidence_text = (
            f"{technical_evidence} (evidence SHA-256 {evidence_digest})"
            if technical_evidence is not None
            else "the controller technical-evidence JSON supplied for this attempt"
        )
        retention_text = json.dumps(
            self._retention_summary(), sort_keys=True, default=str
        )
        recovery_evidence = self._recovery_evidence_summary(row, candidate)
        recovery_evidence_text = (
            f"""
The controller also wrote recovery evidence at {recovery_evidence['path']}
(SHA-256 {recovery_evidence['sha256']}): every generation attempt's outcome,
the shell commands and file writes parsed from the agent streams, the changed
files from the code diff, and incident verdicts. It is observed, not declared.
Judge a.md against it: recovery_record FAILS if a.md omits a changed file the
evidence lists, names a command the evidence does not contain, or contradicts
an attempt outcome. a.md may refer to the evidence file for the full command
log instead of repeating every line; that is not a defect."""
            if recovery_evidence is not None
            else ""
        )
        instruction_mode = self._instruction_mode(row)
        mode_text = (
            "GDPval prose (heading-free instruction written by the prose skill; "
            "task.toml carries metadata.prose_instruction bound to the live instruction)"
            if instruction_mode == "gdpval_prose"
            else "naturalize (the naturalization skill; no prose metadata table)"
        )
        return f"""
You are the fresh, independent fairness verifier for workbook
{row.workbook_id}. This prompt is separate from generation. The worktree is
read-only. Gate 13 mode for this row: {mode_text}. Judge the instruction
against that mode's conventions, not the other's. Do not modify, create, delete, rename, or write any file. Do not run
a command that writes files. Return the complete verification report in your
final response; the recovery controller will persist it. Do not read or print
.env.

Read the controller-generated technical evidence at {evidence_text}. It is the
authoritative result of deterministic source/workbook hashing, OOXML package
inspection, immutable release and task-manifest binding checks, required-file
checks, and canonical grader byte comparison. Do not rerun those technical
checks and do not fail merely because Ask mode cannot execute hash or binary
inspection commands. Fail if the evidence is missing, malformed, or
contradicted by a readable artifact.

Semantically inspect the immutable task bundle at {task}, its instruction
at {task / 'instruction.md'}, a.md at {task / 'a.md'} if present, README at
{task / 'README.md'} if present, disclosure at {task / 'tests/disclosure.json'},
answer key at {task / 'tests/answer_key.json'}, grader at
{task / 'tests/run_grader.py'}, and the VM code diff at {vm_diff}.

Independently check solvability, answer leakage, hidden assumptions, output
representativeness, source/disclosure fidelity, and whether the instruction,
answer key, and grader semantics are fair. Trust the technical evidence for
byte hashes and release bindings. Confirm a.md (when recovery was used) fully records the exact blocker,
evidence, failing stage, exact fix, changed files, before/after, commands, and
verifier outcome. Do not accept fabricated gate success.{recovery_evidence_text}

Output retention. The controller's record of how many auto-included output
candidates survived curation, and why the rest were dropped, is:
{retention_text}
Representativeness FAILS when a deeper or higher-scored candidate was excluded
with no allowed reason (an ``unexplained_excluded`` band, or a gate result of
``override_after_retry``), or when the delivered outputs are a shallow subset
while verifier-passing deeper candidates were dropped by hand. Verifier-pruned
bands (``pruned`` with reason codes) and evidenced manual exclusions are
acceptable; a.md must name each dropped band with its code.

When source health uses restricted routing, `route: restricted_pass` is a
source-stage routing decision. A nested source-observation diagnostic may
remain `insufficient_evidence` until strict segmentation and its hash-bound
restriction-cone proof pass. Do not treat those scoped fields as contradictory
by themselves; use the technical evidence attestation for the downstream
proof and bindings.

Give a detailed report. Approval is fail-closed. Use PASS only when every
semantic check succeeds without uncertainty.

End the report with two things, in this order. First, one fenced ```json block
with exactly this shape (the controller parses it; the fleet report keys on
it, so use these check names and lower-case values only):

```json
{{
  "schema_version": "recovery-fairness/v1",
  "verdict": "PASS" | "FAIL",
  "checks": {{
    "technical_evidence": "pass" | "fail" | "not_applicable",
    "solvability": "pass" | "fail" | "not_applicable",
    "answer_leakage": "pass" | "fail" | "not_applicable",
    "hidden_assumptions": "pass" | "fail" | "not_applicable",
    "source_fidelity": "pass" | "fail" | "not_applicable",
    "disclosure_fidelity": "pass" | "fail" | "not_applicable",
    "output_representativeness": "pass" | "fail" | "not_applicable",
    "recovery_record": "pass" | "fail" | "not_applicable"
  }},
  "findings": [
    {{"check": "<one of the names above>", "subclass": "<short_snake_case_label>",
      "gate": <SKILL.md gate number 1-16 that should have caught it, or null>,
      "artifact": "<file the defect lives in>", "detail": "<one sentence>"}}
  ]
}}
```

A FAIL verdict must have at least one check marked fail and one finding per
failing check; a PASS must have no check marked fail. ``recovery_record`` is
the a.md check and is not_applicable when recovery was not used.

Second, after the block, your final non-empty line must be exactly
FAIRNESS_VERDICT: PASS or FAIRNESS_VERDICT: FAIL, agreeing with the block.
""".strip()

    def _run_verifier(
        self,
        row: QueueRow,
        record: ReadyRecord,
        candidate: ReleaseCandidate,
        vm_diff: Path,
        report_path: Path,
    ) -> dict[str, object]:
        technical_evidence = getattr(
            self, "_active_technical_evidence", None
        )
        evidence_digest = str(
            getattr(self, "_active_technical_evidence_digest", "")
        )
        command = [
            str(self.config.agent_binary),
            "-p",
            "--mode",
            "ask",
            "--trust",
            "--output-format",
            "stream-json",
            "--model",
            self.config.model,
            "--workspace",
            str(self._task_repo()),
            self._verifier_prompt(
                row,
                candidate,
                vm_diff,
                technical_evidence,
                evidence_digest,
            ),
        ]
        return run_ready_attempt(
            record,
            command,
            state_dir=self.config.state / "verification",
            timeout_seconds=self.config.fairness_timeout_seconds,
            cwd=self._task_repo(),
            env=self._agent_environment(),
        )

    def _approve_and_deliver(
        self,
        row: QueueRow,
        record: ReadyRecord,
        candidate: ReleaseCandidate,
        *,
        modified: bool,
        modification_evidence: Mapping[str, object],
        protected_before: Mapping[str, str],
    ) -> dict[str, object]:
        repo = self._task_repo()
        verification_result: dict[str, object] | None = None
        verification_report: str | None = None
        if modified:
            validate_recovery_amd(candidate.task_dir, modification_evidence)
            vm_diff = _write_vm_diff(
                repo,
                self.config.baseline_repo,
                self.config.state,
                row.workbook_id,
                modification_evidence,
            )
            before_tree = repo_snapshot(
                repo,
                excluded=(self.config.state, self.config.output),
            )
            source_hash = sha256_file(row.run_source_path)
            verifier_protected = _protected_snapshot(repo, self.baseline)
            _assert_same_snapshot(
                protected_before,
                verifier_protected,
                "protected files before verifier",
            )
            technical_evidence, evidence_digest = self._write_technical_evidence(
                row,
                candidate,
                vm_diff,
                modification_evidence,
                verifier_protected,
            )
            self._active_technical_evidence = technical_evidence
            self._active_technical_evidence_digest = evidence_digest
            report_path = (
                self.config.state
                / "verification-inputs"
                / row.workbook_id
                / candidate.bundle_sha256
                / f"fairness-{time.time_ns()}.md"
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            if report_path.exists() or report_path.is_symlink():
                raise RecoveryError("fresh fairness report path already exists")
            def verify_once(path: Path, rerun_reason: str | None) -> tuple[dict[str, object], str, bool, dict[str, object] | None, str | None]:
                """One verifier call plus the three read-only assertions and the acceptance rule."""

                result = dict(self._run_verifier(row, record, candidate, vm_diff, path))
                result["fairness_report_path"] = str(path)
                result["candidate_bundle_sha256"] = candidate.bundle_sha256
                if rerun_reason is not None:
                    result["verifier_rerun_reason"] = rerun_reason
                _assert_same_snapshot(
                    before_tree,
                    repo_snapshot(repo, excluded=(self.config.state, self.config.output)),
                    "read-only verifier worktree",
                )
                _assert_source_hash(row, source_hash, "during read-only verifier")
                _assert_same_snapshot(
                    verifier_protected,
                    _protected_snapshot(repo, self.baseline),
                    "protected files during read-only verifier",
                )
                report = self._read_fairness_report(result, path)
                status = str(result.get("status") or "")
                if status != "completed":
                    # A killed, timed-out or crashed verifier is not a verdict
                    # even if a verdict block had already been streamed.
                    return result, report, False, None, f"attempt_{status or 'unknown'}"
                ok, block, error = fairness_accepted(report)
                return result, report, ok, block, error

            verification_result, verification_report, accepted, fairness_block, verifier_error = (
                verify_once(report_path, None)
            )
            if verifier_error is not None:
                # The verifier's output is unusable (no structured block, its
                # block and verdict line disagree, or the attempt did not
                # complete). That is not a task verdict. Rerun the verifier
                # once, synchronously, on the same untouched bundle; only then
                # give up on this verifier call.
                self._record_verifier_error(row, verifier_error, report_path)
                first_reason = verifier_error
                report_path = report_path.with_name(f"fairness-{time.time_ns()}-rerun.md")
                verification_result, verification_report, accepted, fairness_block, verifier_error = (
                    verify_once(report_path, first_reason)
                )
                if verifier_error is not None:
                    self._record_verifier_error(row, verifier_error, report_path)
                    raise VerifierError(
                        verifier_error, verification_report, verification_result
                    )
            if not accepted:
                raise FairnessRetry(
                    verification_report, verification_result, fairness_block
                )
            observed = self._inspect_release(row)
            if observed != candidate:
                raise PolicyViolation("immutable release changed during verification")
            self._save_checkpoint(
                row,
                repo,
                fairness_result={
                    "passed": True,
                    "report_sha256": _sha256_bytes(
                        verification_report.encode("utf-8")
                    ),
                    "attempt": verification_result,
                    "report_path": str(report_path),
                    "verdict": fairness_block,
                },
            )
        else:
            fairness_block = None
            self._save_checkpoint(
                row,
                repo,
                fairness_result={"passed": True, "not_required": True},
            )
        recovery_evidence = self._recovery_evidence_summary(row, candidate)
        return deliver_bundle(
            self.config,
            row,
            candidate,
            modified=modified,
            verification_report=verification_report,
            verification_attempt=verification_result,
            fairness_verdict=fairness_block,
            recovery_evidence=recovery_evidence,
            output_retention=self._retention_summary(),
            guard_evidence={
                "source_before_generation": row.run_source_sha256,
                "source_after_generation": sha256_file(row.run_source_path),
                "protected_before_generation": _sha256_bytes(
                    _canonical_bytes(protected_before)
                ),
                "protected_after_generation": _sha256_bytes(
                    _canonical_bytes(_protected_snapshot(repo, self.baseline))
                ),
                "code_baseline_sha256": sha256_file(self.config.code_baseline),
            },
        )

    def process(self, row: QueueRow) -> dict[str, object]:
        self._set_status(row, "recovery_pending")
        # The reservation spans the whole row, not just generation. Inspect,
        # the incident investigator, and the fairness verifier are full agents
        # on the same model, and worktree acquisition can trigger a quarantine
        # copytree, so releasing early would let a sibling lane admit another
        # heavy row on top of this one. Stages resize instead of releasing.
        lease = self._acquire_row_budget(row)
        self._active_lease = lease
        try:
            # Disk gate runs under the I/O lease and before the worktree
            # exists, so a reap never races this row's own writes.
            exhausted = self._enforce_disk_floor(row=row)
            if exhausted is not None:
                return exhausted
            worktree = self._acquire_worktree(row)
            self.active_worktree = worktree
            self._sweep_staging(worktree, stage="worktree_acquired")
            return self._process_row(row, worktree)
        finally:
            self._active_lease = None
            if lease is not None:
                lease.release()

    def _acquire_row_budget(self, row: QueueRow) -> BudgetLease | None:
        """Take the row's opening reservation, sized for worktree setup.

        A budget-file failure degrades to ungoverned operation rather than
        failing the row, matching how the governor is attached in the first
        place. A transient ENOSPC on the budget volume must not end a
        multi-hour recovery.
        """

        if self.budget is None:
            return None
        started = time.monotonic()
        try:
            lease = self.budget.acquire(
                self.config.lane_id,
                self.config.memory_io_bytes,
                workbook_id=row.workbook_id,
                timeout_seconds=BUDGET_WAIT_TIMEOUT_SECONDS,
                on_wait=lambda: self._record_memory_stage(
                    "row_open_waiting", self.config.memory_io_bytes
                ),
            )
        except (MemoryGovernorError, BudgetTimeout, OSError) as error:
            _write_task_evidence(
                self.config.state,
                row,
                error=str(error),
                extra={"memory_budget_unavailable": True},
            )
            return None
        waited = time.monotonic() - started
        self._record_memory_stage("row_open", lease.bytes)
        if waited >= 1:
            self.tasks.setdefault(row.workbook_id, {})["budget_wait_seconds"] = round(
                waited, 3
            )
        return lease

    def _process_row(self, row: QueueRow, worktree: Path) -> dict[str, object]:
        delivered = False
        unsafe = False
        result: dict[str, object] | None = None
        try:
            result = self._process_in_worktree(row, worktree)
            delivered = str(result.get("status")) == "generated"
            return result
        except PolicyViolation as error:
            unsafe = True
            # Quarantine deletes the checkpoint; keep the attempt count so
            # the failure record's lifetime_attempts survives the row close.
            self._violation_attempts = int(
                self._load_checkpoint(row).get("cumulative_attempt_count", 0) or 0
            )
            quarantined = self._quarantine_worktree(row, worktree, str(error))
            self.active_worktree = None
            incident = self._launch_incident(
                row,
                "policy",
                {"error": str(error)},
            )
            _write_task_evidence(
                self.config.state,
                row,
                error=str(error),
                extra={
                    "policy_violation": True,
                    "worktree_quarantined": str(quarantined),
                    "incident_report": incident,
                },
            )
            raise
        finally:
            if not delivered and not unsafe:
                try:
                    _preserve_failed_release(
                        self.config.state, row, worktree, self.config
                    )
                except (OSError, RecoveryError) as error:
                    _write_task_evidence(
                        self.config.state,
                        row,
                        error=str(
                            (result or {}).get("error")
                            or "failed release preservation"
                        ),
                        extra={"preserve_error": str(error)},
                    )
                checkpoint_fields: dict[str, object] = {
                    "status": str(
                        (result or {}).get("status", "recovery_pending")
                    )
                }
                if checkpoint_fields["status"] == "worker_fix_needed":
                    checkpoint_fields["worker_fix_baseline_hash"] = (
                        self.baseline_hash
                    )
                    checkpoint_fields["worker_fix_overlay"] = self.worker_overlay_id
                if result is not None and result.get("last_attempt") is not None:
                    checkpoint_fields["last_attempt"] = result["last_attempt"]
                if (
                    result is not None
                    and result.get("cumulative_attempt_count") is not None
                ):
                    checkpoint_fields["cumulative_attempt_count"] = result[
                        "cumulative_attempt_count"
                    ]
                self._save_checkpoint(row, worktree, **checkpoint_fields)
                # The row is parked, not delivered, so its worktree stays. Its
                # processes are gone, so any staging dir left behind is dead.
                self._sweep_staging(worktree, stage="row_closed")
            else:
                if delivered:
                    self._save_checkpoint(row, worktree, status="generated")
                    recycle_isolated_worktree(worktree, self.config.baseline_repo)
            self.active_worktree = None

    def _process_in_worktree(
        self, row: QueueRow, worktree: Path
    ) -> dict[str, object]:
        protected_before = _protected_snapshot(worktree, self.baseline)
        _assert_frozen_protected_baseline(protected_before, self.baseline)
        source_before = row.run_source_sha256
        record = self._prepare_ready(row)
        try:
            inventory = self.ensure_inventory(row)
        except RecoveryOrchestrationError as error:
            inventory = None
            _write_task_evidence(
                self.config.state,
                row,
                error=str(error),
                extra={"inventory_missing": True},
            )
        code_before = _code_state(worktree, self.baseline, row.workbook_id)
        checkpoint = self._load_checkpoint(row)
        cumulative_attempts = int(checkpoint.get("cumulative_attempt_count", 0))
        interrupted = checkpoint.get("attempt_in_progress")
        if isinstance(interrupted, dict):
            interrupted_attempts = list(
                checkpoint.get("interrupted_attempts", [])
            )
            interrupted_attempts.append(
                {
                    **interrupted,
                    "reconciled_at": _timestamp(),
                    "retained_worktree": str(worktree),
                }
            )
            checkpoint = self._save_checkpoint(
                row,
                worktree,
                attempt_in_progress=None,
                interrupted_attempts=interrupted_attempts,
            )
        durable_last = checkpoint.get("last_attempt")
        last_result = (
            dict(durable_last) if isinstance(durable_last, dict) else None
        )
        final_text = self._attempt_transcript(last_result) if last_result else ""
        candidate: ReleaseCandidate | None = None
        saw_hard = False
        inspect_error: str | None = None
        worker_fix_incident: dict[str, object] | None = None
        # Paperwork retries do not advance the ladder and do not count as
        # substantive attempts for the recurrence investigator. The count is
        # durable so a restart neither forgets a spent retry nor advances the
        # ladder past the step that produced the artifacts.
        paperwork_retries = int(checkpoint.get("paperwork_retry_count", 0))
        # The cone retry has its own durable counter and, like a paperwork
        # retry, holds the ladder still: the strategy was fine, the cone was
        # trimmed without evidence.
        cone_retries = int(checkpoint.get("cone_retry_count", 0))
        ladder_offset = paperwork_retries + cone_retries
        self._paperwork_retry = None
        self._cone_retry = None
        self._active_output_retention = None
        seeded = self._seed_amd_retry(row, checkpoint, cumulative_attempts)
        if seeded is not None:
            self._paperwork_retry = seeded
            paperwork_retries += 1
            ladder_offset += 1
        self._pinned_ladder_index = None
        modified = False
        modification_evidence: dict[str, object] = {}

        def paperwork_retry_available() -> bool:
            return paperwork_retries < MAX_PAPERWORK_RETRIES

        def cone_retry_available() -> bool:
            return cone_retries < MAX_CONE_RETRIES

        # Bound this pickup by the lifetime cap so immediate retries cannot
        # overshoot it; the cap itself is enforced in run() before the call.
        # _row_attempts also counts attempt directories, so a quarantine that
        # wiped the checkpoint cannot make this bound too generous.
        attempts_this_pickup = max(
            1,
            min(
                self.config.attempt_count,
                self.config.row_attempt_cap
                - max(cumulative_attempts, self._row_attempts(row)),
            ),
        )
        try:
            for attempt_index in range(
                cumulative_attempts,
                cumulative_attempts + attempts_this_pickup,
            ):
                cumulative_attempts = attempt_index + 1
                ladder_index = max(0, attempt_index - ladder_offset)
                self._pinned_ladder_index = ladder_index
                strategy = self._ladder_step(ladder_index)
                self._save_checkpoint(
                    row,
                    worktree,
                    instruction_mode=self._instruction_mode(row),
                    cumulative_attempt_count=cumulative_attempts,
                    paperwork_retry_count=paperwork_retries,
                    cone_retry_count=cone_retries,
                    attempt_in_progress={
                        "attempt_index": attempt_index,
                        "attempt_number": cumulative_attempts,
                        "strategy": strategy,
                        "paperwork_retry": self._paperwork_retry,
                        "cone_retry": self._cone_retry,
                        "started_at": _timestamp(),
                    },
                    strategy=strategy,
                )
                last_result = self._run_generation(
                    row, record, inventory, last_result, attempt_index
                )
                retry_used = self._paperwork_retry
                self._paperwork_retry = None
                cone_retry_used = self._cone_retry
                self._cone_retry = None
                # Hand back the generation peak but keep a real reservation:
                # the inspect script, the incident investigator, and the
                # fairness verifier all still have to run under this row.
                self._resize_lease(
                    self.config.memory_verifier_bytes, stage="post_generation"
                )
                self._save_checkpoint(
                    row,
                    worktree,
                    cumulative_attempt_count=cumulative_attempts,
                    attempt_in_progress=None,
                    last_attempt=last_result,
                    strategy=strategy,
                )
                final_text = self._attempt_transcript(last_result)
                if HARD_LINE.search(final_text):
                    saw_hard = True
                _assert_source_hash(row, source_before, "during generation agent")
                _assert_same_snapshot(
                    protected_before,
                    _protected_snapshot(worktree, self.baseline),
                    "protected files during generation agent",
                )
                try:
                    candidate = self._inspect_release(row)
                    assert_canonical_grader_provenance(
                        self.config.baseline_repo, candidate.task_dir
                    )
                    inspect_error = None
                except PolicyViolation:
                    raise
                except RecoveryError as error:
                    inspect_error = f"{type(error).__name__}: {error}"
                    candidate = None
                    if (
                        paperwork_retry_available()
                        and not HARD_LINE.search(final_text)
                        and self._worktree_has_progress(row)
                    ):
                        # The agent built real artifacts and only the release
                        # bookkeeping is off. A targeted rerun of the failed
                        # gate is ten minutes; an investigator plus a parked
                        # row is thirty and a lost lane.
                        paperwork_retries += 1
                        ladder_offset += 1
                        self._paperwork_retry = {
                            "gate": "inspect",
                            "error": inspect_error,
                            "attempt": attempt_index + 1,
                            "retried_from": retry_used,
                        }
                        self._save_checkpoint(
                            row,
                            worktree,
                            cumulative_attempt_count=cumulative_attempts,
                            paperwork_retry_count=paperwork_retries,
                            last_attempt=last_result,
                            strategy=strategy,
                            inspect_result={
                                "ok": False,
                                "error": inspect_error,
                                "paperwork_retry": self._paperwork_retry,
                            },
                        )
                        continue
                    incident = self._launch_incident(
                        row,
                        "inspect",
                        {
                            "error": inspect_error,
                            "attempt": attempt_index + 1,
                            "strategy": strategy,
                        },
                    )
                    if incident.get("worker_fix_needed") is True:
                        worker_fix_incident = incident
                except Exception as error:
                    inspect_error = f"{type(error).__name__}: {error}"
                    candidate = None
                    incident = self._launch_incident(
                        row,
                        "inspect",
                        {
                            "error": inspect_error,
                            "attempt": attempt_index + 1,
                            "strategy": strategy,
                        },
                    )
                    if incident.get("worker_fix_needed") is True:
                        worker_fix_incident = incident
                    self._save_checkpoint(
                        row,
                        worktree,
                        cumulative_attempt_count=cumulative_attempts,
                        last_attempt=last_result,
                        strategy=strategy,
                        inspect_result={
                            "ok": False,
                            "error": inspect_error,
                            "incident": incident,
                        },
                    )
                    break
                if candidate is not None:
                    # Output retention gate, unconditional: an auto-included
                    # output dropped from the cone without an allowed marker
                    # gets one targeted retry on the same ladder step. When
                    # that is spent the row delivers anyway, flagged, so a
                    # large workbook never parks a lane over this.
                    gate = self._output_retention_gate(row, candidate)
                    findings = list(gate.get("findings") or [])
                    if findings and cone_retry_available():
                        cone_retries += 1
                        ladder_offset += 1
                        self._cone_retry = {
                            "gate": RETENTION_GATE,
                            "findings": findings,
                            "attempt": attempt_index + 1,
                            "retried_from": cone_retry_used,
                            "record": gate.get("record"),
                        }
                        self._save_checkpoint(
                            row,
                            worktree,
                            cumulative_attempt_count=cumulative_attempts,
                            cone_retry_count=cone_retries,
                            last_attempt=last_result,
                            strategy=strategy,
                            inspect_result={
                                "ok": True,
                                "release_id": candidate.release_id,
                                "bundle_sha256": candidate.bundle_sha256,
                                "cone_retry": self._cone_retry,
                            },
                        )
                        candidate = None
                        continue
                    self._active_output_retention = {
                        "record": gate.get("record"),
                        "gate": (
                            "override_after_retry" if findings
                            else "no_record" if gate.get("no_record")
                            else "pass"
                        ),
                        "findings": findings,
                        "cone_retry_used": cone_retries,
                        "resource_evidence": gate.get("resource_evidence"),
                    }
                    # Paperwork precheck: the same a.md rules the verifier
                    # applies, run here where a failure can still be a retry
                    # rather than an investigator.
                    code_after = _code_state(worktree, self.baseline, row.workbook_id)
                    modified, modification_evidence = self._modified(
                        code_before, code_after, final_text, candidate
                    )
                    if modified:
                        try:
                            validate_recovery_amd(candidate.task_dir, modification_evidence)
                        except RecoveryError as error:
                            if paperwork_retry_available():
                                paperwork_retries += 1
                                ladder_offset += 1
                                self._paperwork_retry = {
                                    "gate": "a.md recovery record",
                                    "error": str(error),
                                    "attempt": attempt_index + 1,
                                    "retried_from": retry_used,
                                    "changed_paths": _evidence_changed_paths(
                                        modification_evidence
                                    ),
                                }
                                self._save_checkpoint(
                                    row,
                                    worktree,
                                    cumulative_attempt_count=cumulative_attempts,
                                    paperwork_retry_count=paperwork_retries,
                                    last_attempt=last_result,
                                    strategy=strategy,
                                    inspect_result={
                                        "ok": True,
                                        "release_id": candidate.release_id,
                                        "bundle_sha256": candidate.bundle_sha256,
                                        "paperwork_retry": self._paperwork_retry,
                                    },
                                )
                                candidate = None
                                continue
                            # Retry spent: fall through and let the delivery
                            # path raise the same error into its handler.
                self._save_checkpoint(
                    row,
                    worktree,
                    cumulative_attempt_count=cumulative_attempts,
                    last_attempt=last_result,
                    strategy=strategy,
                    inspect_result=(
                        {
                            "ok": True,
                            "release_id": candidate.release_id,
                            "bundle_sha256": candidate.bundle_sha256,
                        }
                        if candidate is not None
                        else {
                            "ok": False,
                            "error": inspect_error,
                            "incident": incident,
                        }
                    ),
                )
                if worker_fix_incident is not None:
                    break
                if candidate is not None:
                    break
        finally:
            self._paperwork_retry = None
            self._pinned_ladder_index = None
        if candidate is None:
            status = (
                "worker_fix_needed"
                if worker_fix_incident is not None
                else "task_fix_needed"
                if saw_hard
                else "recovery_pending"
            )
            preserved = self.config.state / "failed-releases" / row.workbook_id
            incident_reports = list(
                self._load_checkpoint(row).get("incident_reports", [])
            )
            evidence = _write_task_evidence(
                self.config.state,
                row,
                error="no valid complete immutable release",
                extra={
                    "saw_hard_blocker": saw_hard,
                    "last_attempt": last_result,
                    "inspect_error": inspect_error,
                    "preserved_release": str(preserved),
                    "cumulative_attempt_count": cumulative_attempts,
                    "incident_reports": incident_reports,
                    "worker_fix_incident": worker_fix_incident,
                },
            )
            return {
                "workbook_id": row.workbook_id,
                "status": status,
                "error": "no valid complete immutable release",
                "inspect_error": inspect_error,
                "saw_hard_blocker": saw_hard,
                "queued": True,
                "evidence_path": str(evidence),
                "last_attempt": last_result,
                "cumulative_attempt_count": cumulative_attempts,
                "incident_reports": incident_reports,
                "worker_fix_incident": worker_fix_incident,
            }
        candidate_snapshot = self._snapshot_candidate(row, candidate)
        self._save_checkpoint(
            row,
            worktree,
            candidate_snapshot=candidate_snapshot,
            inspect_result={
                "ok": True,
                "release_id": candidate.release_id,
                "bundle_sha256": candidate.bundle_sha256,
            },
        )
        code_after = _code_state(worktree, self.baseline, row.workbook_id)
        modified, evidence = self._modified(
            code_before, code_after, final_text, candidate
        )
        # Gate 13 evidence is deterministic: a prose-mode bundle must carry a
        # metadata.prose_instruction table that binds the live instruction,
        # and a naturalize-mode bundle must not. Checked here, before any
        # agent is paid for, so a mode mismatch or stale metadata is class
        # instruction_metadata (gate 13) rather than a fairness opinion.
        metadata_error = self._verify_instruction_metadata(row, candidate)
        if metadata_error is not None:
            return self._close_row_on_delivery_error(
                row,
                worktree,
                candidate,
                candidate_snapshot,
                last_result,
                cumulative_attempts,
                RecoveryError(metadata_error),
            )
        # Controller-written record of what recovery did for this bundle
        # (attempts, commands from the agent streams, changed files,
        # incident verdicts). Outside the bundle, hash-bound in the sidecar,
        # handed to the verifier so a.md is checked against facts.
        try:
            self._write_recovery_evidence(row, candidate, evidence, modified)
        except (OSError, RecoveryError) as error:
            _write_task_evidence(
                self.config.state,
                row,
                error="generated",
                extra={"recovery_evidence_error": str(error)},
            )
        try:
            sidecar = self._approve_and_deliver(
                row,
                record,
                candidate,
                modified=modified,
                modification_evidence=evidence,
                protected_before=protected_before,
            )
        except VerifierError as error:
            # Two verifier calls produced unusable output. Mechanical class
            # verifier_error; no investigator (there is no task defect to
            # explain) and no fairness_retry (nothing task-side was named).
            # The row stays active; the next pickup re-verifies the same
            # untouched bundle via the paperwork path.
            self._save_checkpoint(
                row,
                worktree,
                status="recovery_pending",
                fairness_result={
                    "passed": False,
                    "verifier_error": error.reason,
                    "report": error.report,
                    "attempt": error.attempt,
                },
            )
            report_path = _write_task_evidence(
                self.config.state,
                row,
                error=str(error),
                extra={
                    "verifier_error": error.reason,
                    "fairness_report": error.report,
                    "verification_attempt": error.attempt,
                    "candidate_snapshot": candidate_snapshot,
                },
            )
            return {
                "workbook_id": row.workbook_id,
                "status": "recovery_pending",
                "error": str(error),
                "verifier_error": error.reason,
                "queued": True,
                "evidence_path": str(report_path),
                "last_attempt": last_result,
                "verification_attempt": error.attempt,
                "candidate_snapshot": candidate_snapshot,
                "cumulative_attempt_count": cumulative_attempts,
            }
        except FairnessRetry as error:
            failing = sorted(
                name
                for name, value in ((error.verdict or {}).get("checks") or {}).items()
                if value == "fail"
            )
            incident = self._launch_incident(
                row,
                "fairness",
                {
                    "report_sha256": _sha256_bytes(error.report.encode("utf-8")),
                    "bundle_sha256": candidate.bundle_sha256,
                    "failing_checks": failing,
                },
            )
            retry_status = (
                "worker_fix_needed"
                if incident.get("worker_fix_needed") is True
                else "fairness_retry"
            )
            self._save_checkpoint(
                row,
                worktree,
                status=retry_status,
                fairness_result={
                    "passed": False,
                    "report": error.report,
                    "attempt": error.attempt,
                    "incident": incident,
                    "verdict": error.verdict,
                },
            )
            report_path = _write_task_evidence(
                self.config.state,
                row,
                error="fairness verifier did not PASS",
                extra={
                    "fairness_report": error.report,
                    "fairness_verdict": error.verdict,
                    "verification_attempt": error.attempt,
                    "preserved_release": str(
                        self.config.state / "failed-releases" / row.workbook_id
                    ),
                    "candidate_snapshot": candidate_snapshot,
                    "incident_report": incident,
                },
            )
            return {
                "workbook_id": row.workbook_id,
                "status": retry_status,
                "error": str(error),
                "queued": True,
                "evidence_path": str(report_path),
                "last_attempt": last_result,
                "verification_attempt": error.attempt,
                "fairness_verdict": error.verdict,
                "candidate_snapshot": candidate_snapshot,
                "incident_report": incident,
                "cumulative_attempt_count": cumulative_attempts,
            }
        except PolicyViolation:
            raise
        except (RecoveryError, FileExistsError) as error:
            return self._close_row_on_delivery_error(
                row,
                worktree,
                candidate,
                candidate_snapshot,
                last_result,
                cumulative_attempts,
                error,
            )
        checkpoint = self._load_checkpoint(row)
        prior_incidents = list(checkpoint.get("incident_reports", []))
        generated_incident: dict[str, object] | None = None
        # A paperwork retry is not a recurrence: the same strategy produced
        # the same artifacts and only the bookkeeping was redone.
        substantive_attempts = cumulative_attempts - paperwork_retries - cone_retries
        if modified or substantive_attempts > 1 or prior_incidents:
            generated_incident = self._launch_incident(
                row,
                "generated-recurrence",
                {
                    "bundle_sha256": candidate.bundle_sha256,
                    "modified": modified,
                    "cumulative_attempt_count": cumulative_attempts,
                    "prior_failure_fingerprints": sorted(
                        str(item.get("fingerprint"))
                        for item in prior_incidents
                        if isinstance(item, dict)
                    ),
                    "paperwork_retry_count": paperwork_retries,
                    "cone_retry_count": cone_retries,
                    "output_retention": self._retention_summary(),
                },
            )
        # A delivered bundle is delivered. The post-delivery investigator's
        # verdict is analysis only: it stays on the record and in run_state
        # for the report, but it never un-delivers the row (an immediate
        # retry would rebuild and then refuse to overwrite the complete
        # bundle) and it does not trigger the lane fix step: there is no
        # failure record for a delivered row, so the step would have nothing
        # to work on.
        generated_status = "generated"
        if (
            generated_incident is not None
            and generated_incident.get("worker_fix_needed") is True
        ):
            notes = self.run_state.setdefault("post_delivery_controller_verdicts", [])
            signature = f"{row.workbook_id}:{generated_incident.get('fingerprint')}"
            if signature not in notes:
                notes.append(signature)
        _write_task_evidence(
            self.config.state,
            row,
            error="generated",
            extra={
                "candidate_snapshot": candidate_snapshot,
                "incident_report": generated_incident,
            },
        )
        return {
            "workbook_id": row.workbook_id,
            "status": generated_status,
            "release_id": candidate.release_id,
            "task_generation_id": candidate.task_generation_id,
            "modified": modified,
            "delivery": sidecar,
            "last_attempt": last_result,
            "candidate_snapshot": candidate_snapshot,
            "incident_report": generated_incident,
            "cumulative_attempt_count": cumulative_attempts,
        }

    def _close_row_on_delivery_error(
        self,
        row: QueueRow,
        worktree: Path,
        candidate: ReleaseCandidate,
        candidate_snapshot: Mapping[str, object],
        last_result: Mapping[str, object] | None,
        cumulative_attempts: int,
        error: Exception,
    ) -> dict[str, object]:
        """Row result for a deterministic post-inspect failure (delivery, metadata)."""

        incident = self._launch_incident(
            row,
            "delivery",
            {
                "error": str(error),
                "bundle_sha256": candidate.bundle_sha256,
            },
        )
        retry_status = (
            "worker_fix_needed"
            if incident.get("worker_fix_needed") is True
            else "recovery_pending"
        )
        evidence = _write_task_evidence(
            self.config.state,
            row,
            error=str(error),
            extra={
                "delivery_error": str(error),
                "preserved_release": str(
                    self.config.state / "failed-releases" / row.workbook_id
                ),
                "candidate_snapshot": candidate_snapshot,
                "incident_report": incident,
            },
        )
        return {
            "workbook_id": row.workbook_id,
            "status": retry_status,
            "error": str(error),
            "delivery_error": str(error),
            "queued": True,
            "evidence_path": str(evidence),
            "last_attempt": last_result,
            "candidate_snapshot": candidate_snapshot,
            "incident_report": incident,
            "cumulative_attempt_count": cumulative_attempts,
        }

    def _verify_instruction_metadata(
        self, row: QueueRow, candidate: ReleaseCandidate
    ) -> str | None:
        """Deterministic gate-13 evidence; an error string when the bundle fails it.

        Prose mode: ``task.toml`` must carry ``metadata.prose_instruction``
        and ``prose_recovery.py verify-metadata`` must accept it against the
        live ``instruction.md`` (hash, model, prompt version, heading-free).
        Naturalize mode: that table must be absent, otherwise gate 13 ran in
        the wrong mode. The error text starts with "instruction metadata" so
        classify_failure files it under class instruction_metadata, gate 13.
        """

        mode = self._instruction_mode(row)
        task_toml = candidate.task_dir / "task.toml"
        instruction = candidate.task_dir / "instruction.md"
        has_table = False
        if task_toml.is_file() and not task_toml.is_symlink():
            try:
                parsed = tomllib.loads(task_toml.read_text(encoding="utf-8"))
                metadata = parsed.get("metadata")
                has_table = isinstance(metadata, Mapping) and "prose_instruction" in metadata
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                if mode == "gdpval_prose":
                    return "instruction metadata: task.toml is unreadable"
                # Naturalize mode has no metadata to check; the bundle hash
                # and release inspect already bind task.toml.
                return None
        if mode != "gdpval_prose":
            if has_table:
                return (
                    "instruction metadata: metadata.prose_instruction present in a "
                    f"{mode}-mode row (gate 13 ran in the wrong mode)"
                )
            return None
        if not has_table:
            return "instruction metadata: prose_metadata_missing (no metadata.prose_instruction table)"
        if not instruction.is_file():
            return "instruction metadata: instruction.md missing"
        script = self._task_repo() / ".cursor/skills/gdpval-prose-instruction/scripts/prose_recovery.py"
        if not script.is_file():
            script = self.config.baseline_repo / ".cursor/skills/gdpval-prose-instruction/scripts/prose_recovery.py"
        if not script.is_file():
            return "instruction metadata: prose_recovery.py unavailable"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "verify-metadata",
                    "--instruction",
                    str(instruction),
                    "--task-toml",
                    str(task_toml),
                ],
                cwd=self._task_repo(),
                env=self._agent_environment(),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"instruction metadata: verify-metadata could not run ({error})"
        if completed.returncode == 0:
            return None
        reason = "prose_metadata_mismatch"
        fields: list[str] = []
        try:
            payload = json.loads(completed.stdout or "{}")
            reason = str(payload.get("reason_code") or payload.get("code") or reason)
            details = payload.get("details") if isinstance(payload.get("details"), Mapping) else payload
            raw_fields = details.get("fields") if isinstance(details, Mapping) else None
            if isinstance(raw_fields, list):
                fields = [str(item) for item in raw_fields]
        except (json.JSONDecodeError, AttributeError):
            pass
        return f"instruction metadata: {reason}" + (f" fields={','.join(fields)}" if fields else "")

    def run(self) -> dict[str, object]:
        config = self.config
        _validate_root_layout(config)
        if not config.baseline_repo.is_dir() or not config.agent_binary.is_file():
            raise RecoveryError(
                "RECOVERY_BASELINE_REPO or agent binary is unavailable"
            )
        if not config.model:
            raise RecoveryError("MODEL cannot be empty")
        config.state.mkdir(parents=True, exist_ok=True)
        config.work_root.mkdir(parents=True, exist_ok=True)
        lock_path = config.state / "worker.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecoveryError(
                    "another recovery worker owns this state directory"
                ) from error
            rows = read_queue(config.queue)
            original_queue = config.queue.read_bytes()
            prior_tasks: dict[str, dict[str, object]] = {}
            ledger_path = config.state / "recovery-ledger.json"
            if ledger_path.is_file() and not ledger_path.is_symlink():
                try:
                    prior_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                    for record in prior_ledger.get("records", []):
                        if isinstance(record, dict) and "workbook_id" in record:
                            prior_tasks[str(record["workbook_id"])] = dict(record)
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                    pass
            summary_path = config.state / "summary.json"
            if summary_path.is_file() and not summary_path.is_symlink():
                try:
                    prior_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    for workbook_id, record in prior_summary.get("tasks", {}).items():
                        if isinstance(record, dict):
                            prior_tasks[str(workbook_id)] = {
                                **prior_tasks.get(str(workbook_id), {}),
                                **record,
                            }
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                    pass
            self.tasks = {}
            for row in rows:
                task = dict(prior_tasks.get(row.workbook_id, {}))
                task.pop("workbook_id", None)
                task["batch_id"] = row.batch_id
                same_worker_defect = (
                    task.get("status") == "worker_fix_needed"
                    and task.get("worker_fix_baseline_hash")
                    == self.baseline_hash
                    and task.get("worker_fix_overlay") == self.worker_overlay_id
                )
                if same_worker_defect:
                    task["status"] = "worker_fix_needed"
                elif _delivery_is_complete(config, row):
                    task["status"] = "generated"
                    resolve_failure_record(config.state, row.workbook_id)
                elif task.get("status") == "worker_fix_needed":
                    task["status"] = "recovery_pending"
                elif task.get("status") not in {
                    "recovery_pending",
                    "task_fix_needed",
                    "fairness_retry",
                }:
                    task["status"] = "recovery_pending"
                self.tasks[row.workbook_id] = task
            self._write_summary()
            selected: list[QueueRow] = []
            for row in rows:
                status = self.tasks[row.workbook_id]["status"]
                if status == "generated":
                    continue
                if status == "worker_fix_needed":
                    # Parked on a controller defect: the lane fix step (Port
                    # 1b) owns it. The rest of the queue keeps moving.
                    continue
                if self._row_attempts(row) >= config.row_attempt_cap:
                    # Capped on an earlier cycle. Costs nothing to skip here;
                    # the row stays active and the report lists it.
                    continue
                selected.append(row)
            if config.task_limit:
                selected = selected[: config.task_limit]
            results: list[dict[str, object]] = []
            run_ended = False
            for row in selected:
                # Immediate retry: a row that does not deliver is retried on
                # the spot until it delivers, parks on a controller defect, or
                # reaches the lifetime attempt cap. Only a host floor ends the
                # run. Nothing here skips a row.
                while True:
                    self.active_worktree = None
                    self._active_lease = None
                    if _delivery_is_complete(config, row):
                        # A post-delivery incident or an earlier cycle already
                        # delivered this row; never rebuild a delivered bundle.
                        result = {"workbook_id": row.workbook_id, "status": "generated"}
                        self._set_status(row, "generated")
                        resolve_failure_record(config.state, row.workbook_id)
                        results.append(result)
                        break
                    attempts = self._row_attempts(row)
                    if attempts >= config.row_attempt_cap:
                        result = {
                            "workbook_id": row.workbook_id,
                            "status": "recovery_pending",
                            "error": (
                                f"row attempt cap reached ({attempts} >= "
                                f"{config.row_attempt_cap}); row stays active"
                            ),
                            "retry_budget_exhausted": True,
                            "cumulative_attempt_count": attempts,
                            "queued": True,
                        }
                        self._record_row_close(row, result)
                        results.append(result)
                        break
                    result = self._run_row(row)
                    results.append(result)
                    status = str(result["status"])
                    if status == "generated":
                        break
                    if status == "worker_fix_needed":
                        break
                    if status == "disk_exhausted" or result.get("host_floor"):
                        self.run_state["host_floor"] = True
                        run_ended = True
                        break
                    if self._row_attempts(row) <= attempts:
                        # No generation attempt happened (orchestration error,
                        # inventory gap, budget refusal). Retrying without new
                        # information would spin; leave the row active for the
                        # next lane cycle and move on.
                        break
                if run_ended:
                    break
            ledger = _recovery_ledger(
                [row.workbook_id for row in rows],
                [
                    {"workbook_id": row.workbook_id, **self.tasks[row.workbook_id]}
                    for row in rows
                ],
            )
            atomic_write_json(config.state / "recovery-ledger.json", ledger)
            self.current = None
            self._write_summary()
            queue_read_error: str | None = None
            try:
                current_queue = config.queue.read_bytes()
            except OSError as error:
                current_queue = None
                queue_read_error = repr(error)
            if current_queue != original_queue:
                integrity = {
                    "schema_version": "recovery-queue-integrity/v1",
                    "queue_path": str(config.queue),
                    "original_sha256": _sha256_bytes(original_queue),
                    "current_sha256": (
                        _sha256_bytes(current_queue)
                        if current_queue is not None
                        else None
                    ),
                    "current_read_error": queue_read_error,
                    "expected_count": len(rows),
                    "original_rows": [
                        {
                            **asdict(row),
                            "run_source_path": str(row.run_source_path),
                        }
                        for row in rows
                    ],
                    "ledger_path": str(
                        config.state / "recovery-ledger.json"
                    ),
                    "detected_at": _timestamp(),
                }
                integrity_path = (
                    config.state / "queue-integrity" / "original-queue-ledger.json"
                )
                atomic_write_json(integrity_path, integrity)
                current_id = (
                    str(results[-1]["workbook_id"]) if results else None
                )
                evidence_row = next(
                    (
                        row
                        for row in rows
                        if row.workbook_id == current_id
                    ),
                    rows[0] if rows else None,
                )
                if evidence_row is not None:
                    _write_task_evidence(
                        config.state,
                        evidence_row,
                        error="recovery queue changed during worker run",
                        extra={
                            "queue_integrity_failure": True,
                            "queue_integrity_path": str(integrity_path),
                            "expected_count": len(rows),
                            "recovery_ledger_path": str(
                                config.state / "recovery-ledger.json"
                            ),
                        },
                    )
                raise RecoveryError(
                    "recovery worker must not rewrite the queue file"
                )
            return ledger
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _row_attempts(self, row: QueueRow) -> int:
        """Lifetime generation attempts for the row, from the durable sources.

        The checkpoint carries ``cumulative_attempt_count`` but is deleted
        when lineage is unsafe; the failures file is never deleted by the
        worker. Take the larger of the two.
        """

        checkpoint = self._load_checkpoint(row)
        failures = read_failure_record(self.config.state, row.workbook_id)
        # Attempt directories are allocated once per generation run and never
        # removed by quarantine, so they count even when a PolicyViolation
        # left no cumulative count behind.
        attempts_dir = self.config.state / "generation" / "attempts" / row.workbook_id
        attempt_dirs = (
            sum(1 for child in attempts_dir.iterdir() if child.name.startswith("attempt-"))
            if attempts_dir.is_dir()
            else 0
        )
        return max(
            int(checkpoint.get("cumulative_attempt_count", 0) or 0),
            int(failures.get("lifetime_attempts", 0) or 0),
            attempt_dirs,
        )

    def _record_row_close(self, row: QueueRow, result: Mapping[str, object]) -> dict[str, object]:
        """Set the task status and, for a non-generated close, write the failure record.

        Returns the result as recorded: the mechanical controller rule may
        rewrite the status, and run()'s retry loop must see that rewrite.
        """

        result = dict(result)
        status = str(result.get("status", "recovery_pending"))
        if status != "generated":
            record = write_failure_record(
                self.config.state,
                row,
                result,
                instruction_mode=self._instruction_mode(row),
            )
            if record.get("class") == "controller":
                fingerprints = self.run_state.setdefault("controller_fingerprints", [])
                signature = str(record.get("signature"))
                if signature not in fingerprints:
                    fingerprints.append(signature)
                if status != "worker_fix_needed":
                    # The mechanical class says the worker itself failed
                    # (unexpected exception, non-RecoveryError inspect). Retrying
                    # generation against a worker bug only burns attempts; park
                    # the row for the lane fix step regardless of what the
                    # investigator's route said.
                    status = "worker_fix_needed"
                    result = {**result, "status": status, "mechanical_controller_park": True}
                    self._mark_checkpoint_worker_fix(row)
            if record.get("class") == "disk_exhausted":
                self.run_state["host_floor"] = True
        else:
            resolve_failure_record(self.config.state, row.workbook_id)
        self._set_status(
            row,
            status,
            **{
                key: value
                for key, value in result.items()
                if key not in {"workbook_id", "status"}
            },
        )
        return result

    def _mark_checkpoint_worker_fix(self, row: QueueRow) -> None:
        checkpoint = self._load_checkpoint(row)
        if checkpoint:
            checkpoint.update(
                status="worker_fix_needed",
                worker_fix_baseline_hash=self.baseline_hash,
                worker_fix_overlay=self.worker_overlay_id,
                updated_at=_timestamp(),
            )
            atomic_write_json(self._checkpoint_path(row), checkpoint)

    def _instruction_mode(self, row: QueueRow) -> str:
        """Gate-13 implementation this row is bound to.

        Persisted on the checkpoint at the row's first attempt so a lane
        relaunched without ``HARBOR_GDPVAL_PROSE`` cannot resume a prose
        worktree in naturalize mode (build_one.sh fails closed on the flag
        mismatch). The lane environment only decides for a row that has no
        recorded mode yet.
        """

        checkpoint = self._load_checkpoint(row)
        recorded = checkpoint.get("instruction_mode")
        if isinstance(recorded, str) and recorded in {"naturalize", "gdpval_prose"}:
            return recorded
        return (
            "gdpval_prose"
            if os.environ.get("HARBOR_GDPVAL_PROSE") == "1"
            else "naturalize"
        )

    def _run_row(self, row: QueueRow) -> dict[str, object]:
        """One ``process()`` call with every failure mode turned into a row result."""

        config = self.config
        try:
            result = self.process(row)
        except RecoveryOrchestrationError as error:
            self.active_worktree = None
            self._active_lease = None
            evidence = _write_task_evidence(
                config.state, row, error=str(error)
            )
            result = {
                "workbook_id": row.workbook_id,
                "status": "recovery_pending",
                "error": str(error),
                "queued": True,
                "evidence_path": str(evidence),
            }
        except PolicyViolation as error:
            if self.active_worktree is not None:
                self._quarantine_worktree(
                    row, self.active_worktree, str(error)
                )
            self.active_worktree = None
            self._active_lease = None
            incident = self._launch_incident(
                row,
                "policy",
                {"error": str(error)},
            )
            failure_status = (
                "worker_fix_needed"
                if incident.get("worker_fix_needed") is True
                else "task_fix_needed"
            )
            evidence = _write_task_evidence(
                config.state,
                row,
                error=str(error),
                extra={
                    "policy_violation": True,
                    "incident_report": incident,
                },
            )
            result = {
                "workbook_id": row.workbook_id,
                "status": failure_status,
                "error": str(error),
                "policy_violation": True,
                "queued": True,
                "evidence_path": str(evidence),
                "incident_report": incident,
                "cumulative_attempt_count": getattr(self, "_violation_attempts", 0),
            }
            self._violation_attempts = 0
        except (OSError, ValueError, RecoveryError) as error:
            self.active_worktree = None
            self._active_lease = None
            message = f"{type(error).__name__}: {error}"
            if "evidence total would be" in str(error) or "evidence item is" in str(error):
                # Evidence capacity is a host floor, not a row defect:
                # retrying only burns attempts until space returns.
                evidence = _write_task_evidence(
                    config.state,
                    row,
                    error=str(error),
                    extra={"host_floor": "evidence_capacity"},
                )
                result = {
                    "workbook_id": row.workbook_id,
                    "status": "recovery_pending",
                    "error": str(error),
                    "queued": True,
                    "host_floor": "evidence_capacity",
                    "evidence_path": str(evidence),
                }
            else:
                incident = self._launch_incident(
                    row,
                    "unexpected",
                    {"error": message},
                )
                failure_status = (
                    "worker_fix_needed"
                    if incident.get("worker_fix_needed") is True
                    else "recovery_pending"
                )
                evidence = _write_task_evidence(
                    config.state,
                    row,
                    error=str(error),
                    extra={"incident_report": incident},
                )
                result = {
                    "workbook_id": row.workbook_id,
                    "status": failure_status,
                    "error": str(error),
                    "queued": True,
                    "evidence_path": str(evidence),
                    "incident_report": incident,
                }
        except Exception as error:
            if self.active_worktree is not None:
                try:
                    _preserve_failed_release(
                        config.state,
                        row,
                        self.active_worktree,
                        config,
                    )
                except (OSError, RecoveryError):
                    pass
            self.active_worktree = None
            self._active_lease = None
            incident = self._launch_incident(
                row,
                "unexpected",
                {"error": repr(error)},
            )
            failure_status = (
                "worker_fix_needed"
                if incident.get("worker_fix_needed") is True
                else "recovery_pending"
            )
            evidence = _write_task_evidence(
                config.state,
                row,
                error=repr(error),
                extra={
                    "unexpected": True,
                    "incident_report": incident,
                },
            )
            result = {
                "workbook_id": row.workbook_id,
                "status": failure_status,
                "error": repr(error),
                "queued": True,
                "evidence_path": str(evidence),
                "unexpected": True,
                "incident_report": incident,
            }
        self.active_worktree = None
        self._active_lease = None
        return self._record_row_close(row, result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        worker = RecoveryWorker(Config.from_env())
    except RecoveryError as error:
        # A worker that cannot start needs a human, not another cycle.
        print(json.dumps({"worker_error": str(error), "exit": EXIT_HOST_FLOOR}), flush=True)
        return EXIT_HOST_FLOOR
    try:
        ledger = worker.run()
    except RecoveryError as error:
        print(json.dumps({"worker_error": str(error), "exit": EXIT_HOST_FLOOR}), flush=True)
        return EXIT_HOST_FLOOR
    print(json.dumps(ledger, sort_keys=True), flush=True)
    return exit_code_for(ledger, worker.run_state)


if __name__ == "__main__":
    raise SystemExit(main())
