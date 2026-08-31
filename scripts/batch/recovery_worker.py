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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.batch.hardened_runner import (
    ReadyRecord,
    atomic_write_json,
    atomic_write_text,
    convert_and_publish,
    run_ready_attempt,
    sha256_file,
)


SUMMARY_SCHEMA = "recovery-summary/v1"
DELIVERY_SCHEMA = "recovery-delivery/v1"
LEDGER_SCHEMA = "recovery-ledger/v1"
CHECKPOINT_SCHEMA = "recovery-checkpoint/v1"
WORKTREE_MARKER_SCHEMA = "recovery-worktree/v1"
INCIDENT_SCHEMA = "recovery-incident/v1"
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
COPYTREE_EXCLUDE = frozenset({".venv", "__pycache__"}) | GENERATED_ROOTS
RECOVERY_STATUSES = frozenset(
    {
        "recovery_pending",
        "task_fix_needed",
        "fairness_retry",
        "worker_fix_needed",
        "generated",
    }
)
RECOVERY_LADDER = (
    "normal path: publish a complete immutable release without recovery",
    "smallest task-local pipeline, formula, or validator fix",
    "disclosed deterministic assumption for volatile or external cells",
    "recurate to a smaller self-contained output cone",
    "deterministic naturalization reconstruction",
    "a different strategy because the same failure repeated",
)
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
HARD_LINE = re.compile(r"(?m)^FULL_RERUN_BLOCKER: HARD$")
REPAIR_LINE = re.compile(r"(?m)^(?:FULL_RERUN_REPAIR|RECOVERY_USED): (YES|NO)$")
DEFAULT_ATTEMPT_COUNT = 6
INSPECT_TIMEOUT_SECONDS = 600
INCIDENT_TIMEOUT_SECONDS = 1800
GENERATION_LANE_COUNT = 2
DEFAULT_EVIDENCE_ITEM_LIMIT_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_EVIDENCE_GLOBAL_LIMIT_BYTES = 100 * 1024 * 1024 * 1024
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

    def __init__(self, report: str, attempt: Mapping[str, object] | None):
        super().__init__("independent fairness verifier did not PASS")
        self.report = report
        self.attempt = dict(attempt) if attempt is not None else None


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
                default="21600",
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
        if attempts < DEFAULT_ATTEMPT_COUNT:
            raise RecoveryError(
                "ATTEMPT_COUNT must be at least "
                f"{DEFAULT_ATTEMPT_COUNT}"
            )
        if task_limit < 0:
            raise RecoveryError("TASK_LIMIT cannot be negative")
        if evidence_item_limit <= 0 or evidence_global_limit < evidence_item_limit:
            raise RecoveryError(
                "evidence limits must be positive and global must cover one item"
            )
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
        return cls(
            queue=required_path("RECOVERY_QUEUE", "FULL_RERUN_QUEUE"),
            baseline_repo=baseline_repo,
            work_root=required_path("RECOVERY_WORK_ROOT"),
            state=required_path("RECOVERY_STATE", "FULL_RERUN_STATE"),
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
        )


def _safe_id(value: object, description: str) -> str:
    result = str(value).strip()
    if not SAFE_ID_RE.fullmatch(result):
        raise RecoveryError(f"{description} is unsafe: {value!r}")
    return result


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    if parts & {".git", ".venv", "__pycache__"}:
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
            records[relative] = sha256_file(path)
    return records


def tree_sha256(root: Path) -> str:
    return _sha256_bytes(_canonical_bytes(_tree_records(root)))


def _grader_records(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise PolicyViolation(f"canonical grader directory is missing: {root}")
    records: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix.casefold() == ".pyc":
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
        if any(part in {".git", ".venv", "__pycache__"} for part in relative_path.parts):
            continue
        if path.is_symlink():
            records[relative_path.as_posix()] = "symlink:" + os.readlink(path)
        elif path.is_file():
            records[relative_path.as_posix()] = sha256_file(path)
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
        if any(part in {".git", ".venv", "__pycache__"} for part in relative.parts):
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
                part in {".git", ".venv", "__pycache__"}
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


def validate_recovery_amd(task_dir: Path) -> None:
    path = task_dir / "a.md"
    if not path.is_file() or path.is_symlink():
        raise RecoveryError("recovered task has no final a.md")
    text = path.read_text(encoding="utf-8")
    missing = [term for term in AMD_REQUIRED if re.search(term, text, re.I) is None]
    if len(text.strip()) < 200 or missing:
        raise RecoveryError(
            "recovered task a.md is not a complete recovery record; missing: "
            + ", ".join(missing)
        )


def fairness_passed(report: str) -> bool:
    lines = [line.rstrip() for line in report.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == "FAIRNESS_VERDICT: PASS"


def incident_worker_fix_needed(report: str) -> bool:
    lines = [line.rstrip() for line in report.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == "INCIDENT_VERDICT: WORKER_FIX_NEEDED"


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
        atomic_write_json(
            temporary / "snapshot.json",
            {
                "workbook_id": row.workbook_id,
                "source_path": str(release_src),
                "size_bytes": size_bytes,
                "preserved_at": _timestamp(),
            },
        )
        staged_size = _safe_tree_size(temporary)
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
) -> None:
    expected = manifest.get("artifacts")
    if not isinstance(expected, dict):
        raise RecoveryError("task generation manifest has no artifact records")
    actual: dict[str, dict[str, object]] = {}
    for relative, digest in _tree_records(task_dir).items():
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
    _validate_task_manifest_artifacts(task_dir, task_manifest)
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
        bundle_sha256=tree_sha256(task_dir),
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
            validate_recovery_amd(bundle)
        elif (
            sidecar.get("fairness_verdict") != "not_required"
            or sidecar.get("fairness_report_path") is not None
            or sidecar.get("fairness_report_sha256") is not None
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
) -> dict[str, object]:
    assert_canonical_grader_provenance(
        config.baseline_repo, candidate.task_dir
    )
    if modified:
        validate_recovery_amd(candidate.task_dir)
        if verification_report is None or not fairness_passed(verification_report):
            raise RecoveryError("modified bundle lacks a passing fairness report")
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
            "verification_attempt": (
                dict(verification_attempt)
                if verification_attempt is not None
                else None
            ),
            "guard_evidence": dict(guard_evidence or {}),
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
        self.selected_approval = _select_approval(
            config.inventory_registry, config.approval_batch_id
        )
        self.investigator_launcher = investigator_launcher
        self.tasks: dict[str, dict[str, object]] = {}
        self.current: str | None = None
        self.active_worktree: Path | None = None

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
        excluded = frozenset({".git", ".venv", ".env", "__pycache__"})
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
            if _safe_tree_size(
                temporary,
                allow_internal_symlinks=True,
            ) != size_bytes:
                raise RecoveryError("worktree quarantine size changed during copy")
            atomic_write_json(
                temporary / "quarantine.json",
                {
                    "workbook_id": row.workbook_id,
                    "run_source_sha256": row.run_source_sha256,
                    "reason": reason,
                    "size_bytes": size_bytes,
                    "quarantined_at": _timestamp(),
                },
            )
            staged_size = _safe_tree_size(
                temporary,
                allow_internal_symlinks=True,
            )
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
{GENERATION_LANE_COUNT} generation lanes. Return the incident and recurrence
analysis in your final response; the worker will persist it at {report_path}.
Do not read or print .env. End with exactly one of:
INCIDENT_VERDICT: WORKER_FIX_NEEDED
INCIDENT_VERDICT: TASK_LOCAL_OR_EXTERNAL
Use WORKER_FIX_NEEDED only when the recovery worker or shared pipeline must be
changed before this lane can safely continue.
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
        process = subprocess.Popen(
            list(command),
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
        report = final_text.strip()
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
        stable_detail = {
            key: value
            for key, value in detail.items()
            if key
            not in {
                "attempt",
                "cumulative_attempt_count",
                "strategy",
                "launched_at",
            }
        }
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
                return {
                    "category": category,
                    "fingerprint": fingerprint,
                    "result_path": str(completed_path),
                    "report_path": str(report_path),
                    "worker_fix_needed": worker_fix_needed,
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
        worker_fix_needed = completed and incident_worker_fix_needed(report)
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

    def _set_status(self, row: QueueRow, status: str, **fields: object) -> None:
        task = self.tasks.setdefault(row.workbook_id, {})
        if status == "worker_fix_needed":
            fields = {
                **fields,
                "worker_fix_baseline_hash": self.baseline_hash,
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
        environment.pop("PYTHONHOME", None)
        return environment

    def _ladder_step(self, attempt_index: int) -> str:
        if attempt_index < len(RECOVERY_LADDER):
            return RECOVERY_LADDER[attempt_index]
        return RECOVERY_LADDER[-1]

    def _generation_prompt(
        self,
        row: QueueRow,
        workbook: Path,
        inventory: Path | None,
        prior_attempt: Mapping[str, object] | None,
        attempt_index: int,
    ) -> str:
        inner_timeout = (
            self.config.large_timeout_seconds
            if row.workbook_id in self.config.large_workbooks
            else self.config.measured_timeout_seconds
        )
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
        if prior_attempt is not None:
            retry_text = f"""
This is recovery attempt {attempt_index + 1}. Inspect the preserved prior
attempt at {prior_attempt.get('attempt_path')}. FULL_RERUN_BLOCKER: HARD from a
prior attempt is not a stop. Use this ladder step now:
{self._ladder_step(attempt_index)}.
The last durable attempt record is:
{json.dumps(dict(prior_attempt), sort_keys=True, default=str)}
{fairness_text}
Do not repeat the same failed strategy.
""".strip()
        else:
            retry_text = f"""
This is recovery attempt {attempt_index + 1}. Start with this ladder step:
{self._ladder_step(attempt_index)}.
""".strip()
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
(d) recurate to a smaller self-contained output cone
(e) deterministic naturalization reconstruction
(f) a different strategy if the same failure repeats

On `unsupported` or `insufficient_evidence`, do not stop. Implement the missing
semantics, treat the feature as a restriction and recurate away from it, or
bind a disclosed frozen value. On `recalc_candidate` without a macOS Excel
runner, do not stop: recurate to a cone that does not need empty caches, or
bind disclosed cached values already present in the source. On naturalization
failure, reconstruct protected sections byte-for-byte and retry editable spans
until the validator passes; two failures is not a limit.

You MAY edit xl_seg implementation, xl_source_*.py, xl_inventory_approval.py,
xl_segment.py, skill markdown, and naturalization scripts in this isolated
worktree. You MUST NOT change source workbooks, graders, rubrics, answer keys,
or acceptance thresholds. Do not weaken, bypass, skip, special-case, or
fabricate gate success.

Measured AST and segmentation commands may run for at most
{inner_timeout} seconds for this workbook.

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
{self.config.model}. Preserve exactly {GENERATION_LANE_COUNT} generation lanes.
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
        return run_ready_attempt(
            record,
            command,
            state_dir=self.config.state / "generation",
            timeout_seconds=self.config.timeout_seconds,
            cwd=self._task_repo(),
            env=self._agent_environment(),
        )

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

    def _read_fairness_report(
        self,
        verification_result: Mapping[str, object],
        report_path: Path,
    ) -> str:
        stdout_text = self._attempt_final(verification_result)
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

    def _verifier_prompt(
        self,
        row: QueueRow,
        candidate: ReleaseCandidate,
        vm_diff: Path,
    ) -> str:
        task = candidate.task_dir
        return f"""
You are the fresh, independent fairness verifier for workbook
{row.workbook_id}. This prompt is separate from generation. The worktree is
read-only. Do not modify, create, delete, rename, or write any file. Do not run
a command that writes files. Return the complete verification report in your
final response; the recovery controller will persist it. Do not read or print
.env.

Inspect the original workbook at {row.run_source_path} (SHA-256
{row.run_source_sha256}), the immutable task bundle at {task}, its instruction
at {task / 'instruction.md'}, a.md at {task / 'a.md'} if present, README at
{task / 'README.md'} if present, disclosure at {task / 'tests/disclosure.json'},
answer key at {task / 'tests/answer_key.json'}, grader at
{task / 'tests/run_grader.py'}, and the VM code diff at {vm_diff}.

Independently check solvability, answer leakage, hidden assumptions, output
representativeness, source fidelity, unchanged graders/rubrics/answer
keys/acceptance thresholds, and every source/segmentation/task/current-release
binding. Confirm a.md (when recovery was used) fully records the exact blocker,
evidence, failing stage, exact fix, changed files, before/after, commands, and
verifier outcome. Do not accept fabricated gate success.

When source health uses restricted routing, `route: restricted_pass` is a
source-stage routing decision. A nested source-observation diagnostic may
remain `insufficient_evidence` until strict segmentation and its hash-bound
restriction-cone proof pass. Do not treat those scoped fields as contradictory
by themselves; verify the downstream proof and bindings.

Give a detailed report. Approval is fail-closed. Your final non-empty line must
be exactly FAIRNESS_VERDICT: PASS or FAIRNESS_VERDICT: FAIL. Use PASS only when
every check succeeds without uncertainty.
""".strip()

    def _run_verifier(
        self,
        row: QueueRow,
        record: ReadyRecord,
        candidate: ReleaseCandidate,
        vm_diff: Path,
        report_path: Path,
    ) -> dict[str, object]:
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
            self._verifier_prompt(row, candidate, vm_diff),
        ]
        return run_ready_attempt(
            record,
            command,
            state_dir=self.config.state / "verification",
            timeout_seconds=self.config.timeout_seconds,
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
            validate_recovery_amd(candidate.task_dir)
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
            verification_result = dict(
                self._run_verifier(
                    row,
                    record,
                    candidate,
                    vm_diff,
                    report_path,
                )
            )
            verification_result["fairness_report_path"] = str(report_path)
            verification_result["candidate_bundle_sha256"] = (
                candidate.bundle_sha256
            )
            after_tree = repo_snapshot(
                repo,
                excluded=(self.config.state, self.config.output),
            )
            _assert_same_snapshot(
                before_tree, after_tree, "read-only verifier worktree"
            )
            _assert_source_hash(row, source_hash, "during read-only verifier")
            _assert_same_snapshot(
                verifier_protected,
                _protected_snapshot(repo, self.baseline),
                "protected files during read-only verifier",
            )
            verification_report = self._read_fairness_report(
                verification_result, report_path
            )
            if not fairness_passed(verification_report):
                raise FairnessRetry(verification_report, verification_result)
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
                },
            )
        else:
            self._save_checkpoint(
                row,
                repo,
                fairness_result={"passed": True, "not_required": True},
            )
        return deliver_bundle(
            self.config,
            row,
            candidate,
            modified=modified,
            verification_report=verification_report,
            verification_attempt=verification_result,
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
        worktree = self._acquire_worktree(row)
        self.active_worktree = worktree
        delivered = False
        unsafe = False
        result: dict[str, object] | None = None
        try:
            result = self._process_in_worktree(row, worktree)
            delivered = str(result.get("status")) == "generated"
            return result
        except PolicyViolation as error:
            unsafe = True
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
        final_text = ""
        candidate: ReleaseCandidate | None = None
        saw_hard = False
        inspect_error: str | None = None
        worker_fix_incident: dict[str, object] | None = None
        for attempt_index in range(
            cumulative_attempts,
            cumulative_attempts + self.config.attempt_count,
        ):
            cumulative_attempts = attempt_index + 1
            self._save_checkpoint(
                row,
                worktree,
                cumulative_attempt_count=cumulative_attempts,
                attempt_in_progress={
                    "attempt_index": attempt_index,
                    "attempt_number": cumulative_attempts,
                    "strategy": self._ladder_step(attempt_index),
                    "started_at": _timestamp(),
                },
                strategy=self._ladder_step(attempt_index),
            )
            last_result = self._run_generation(
                row, record, inventory, last_result, attempt_index
            )
            self._save_checkpoint(
                row,
                worktree,
                cumulative_attempt_count=cumulative_attempts,
                attempt_in_progress=None,
                last_attempt=last_result,
                strategy=self._ladder_step(attempt_index),
            )
            final_text = self._attempt_final(last_result)
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
                incident = self._launch_incident(
                    row,
                    "inspect",
                    {
                        "error": inspect_error,
                        "attempt": attempt_index + 1,
                        "strategy": self._ladder_step(attempt_index),
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
                        "strategy": self._ladder_step(attempt_index),
                    },
                )
                if incident.get("worker_fix_needed") is True:
                    worker_fix_incident = incident
                self._save_checkpoint(
                    row,
                    worktree,
                    cumulative_attempt_count=cumulative_attempts,
                    last_attempt=last_result,
                    strategy=self._ladder_step(attempt_index),
                    inspect_result={
                        "ok": False,
                        "error": inspect_error,
                        "incident": incident,
                    },
                )
                break
            self._save_checkpoint(
                row,
                worktree,
                cumulative_attempt_count=cumulative_attempts,
                last_attempt=last_result,
                strategy=self._ladder_step(attempt_index),
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
        try:
            sidecar = self._approve_and_deliver(
                row,
                record,
                candidate,
                modified=modified,
                modification_evidence=evidence,
                protected_before=protected_before,
            )
        except FairnessRetry as error:
            incident = self._launch_incident(
                row,
                "fairness",
                {
                    "report_sha256": _sha256_bytes(error.report.encode("utf-8")),
                    "bundle_sha256": candidate.bundle_sha256,
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
                },
            )
            report_path = _write_task_evidence(
                self.config.state,
                row,
                error="fairness verifier did not PASS",
                extra={
                    "fairness_report": error.report,
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
                "candidate_snapshot": candidate_snapshot,
                "incident_report": incident,
            }
        except PolicyViolation:
            raise
        except (RecoveryError, FileExistsError) as error:
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
                "queued": True,
                "evidence_path": str(evidence),
                "last_attempt": last_result,
                "candidate_snapshot": candidate_snapshot,
                "incident_report": incident,
            }
        checkpoint = self._load_checkpoint(row)
        prior_incidents = list(checkpoint.get("incident_reports", []))
        generated_incident: dict[str, object] | None = None
        if modified or cumulative_attempts > 1 or prior_incidents:
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
                },
            )
        generated_status = (
            "worker_fix_needed"
            if generated_incident is not None
            and generated_incident.get("worker_fix_needed") is True
            else "generated"
        )
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
                )
                if same_worker_defect:
                    task["status"] = "worker_fix_needed"
                elif _delivery_is_complete(config, row):
                    task["status"] = "generated"
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
                    break
                selected.append(row)
            if config.task_limit:
                selected = selected[: config.task_limit]
            results: list[dict[str, object]] = []
            for row in selected:
                try:
                    result = self.process(row)
                except RecoveryOrchestrationError as error:
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
                    }
                except (OSError, ValueError, RecoveryError) as error:
                    incident = self._launch_incident(
                        row,
                        "unexpected",
                        {"error": f"{type(error).__name__}: {error}"},
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
                results.append(result)
                self._set_status(
                    row,
                    str(result["status"]),
                    **{
                        key: value
                        for key, value in result.items()
                        if key not in {"workbook_id", "status"}
                    },
                )
                if str(result["status"]) != "generated":
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    worker = RecoveryWorker(Config.from_env())
    ledger = worker.run()
    print(json.dumps(ledger, sort_keys=True), flush=True)
    counts = ledger["counts"]
    unfinished = sum(
        int(count)
        for status, count in counts.items()
        if status != "generated"
    )
    return 1 if unfinished else 0


if __name__ == "__main__":
    raise SystemExit(main())
