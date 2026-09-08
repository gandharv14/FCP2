#!/usr/bin/env python3
"""Reclaim disk from parked recovery rows without touching live work.

Where the disk goes
-------------------
Delivery already reaps: a row that reaches ``generated`` has its whole
worktree recycled, AST included. Growth comes from rows the lane parked and
advanced past. Each keeps a worktree holding one or more immutable source
generations, and the AST inside a generation (``ast/nodes.csv``,
``ast/edges.csv``) has reached 47 GB for a single workbook. Crashed prepares
also leave dot-prefixed staging directories behind, one of which
(``.source-stage-*``) holds a second full copy of the AST.

Three tiers
-----------
* Tier 1, always on: sweep dead staging directories in one lane's own work
  root, at row close and at worktree acquisition.
* Tier 2, pressure-triggered: when free space is under the high-water mark,
  reap staging dirs, then source generations that fail every liveness signal
  (oldest first), then whole evidence units (oldest first), stopping as soon
  as free space clears the mark.
* Tier 3, the CLI at the bottom of this file: ``report``, ``plan``, and
  ``reap`` (dry-run unless ``--apply``).

Rules that keep this safe
-------------------------
* A generation directory is atomic. Its manifest hashes every artifact and the
  validator re-hashes them all, so deleting ``ast/`` alone leaves a generation
  that fails validation forever. Delete ``generations/<id>/`` whole or not at
  all, and take ``receipts/<id>/`` with it.
* Reaping cascades. A segmentation generation or a release names its source
  generation by id and hard-requires it on validation, so a source id named
  anywhere downstream is live even without a ``current.json`` pointer.
* Absence of a pointer is not death. Rows that never reached the publish step
  have generations and no pointer. Age and newest-per-workbook cover them.
* Staging directories are live while a prepare runs. They are only reaped
  when older than twice the longest command the prompt allows *and* no live
  process has the worktree as an ancestor of its cwd.
* Never leave the roots you were given. The worker passes its own work root
  and state root; a lane cannot see which of a sibling lane's rows is active,
  so it does not touch them.
* No ``ignore_errors``. A half-deleted generation keeps its manifest, looks
  like the newest generation, gets protected, and fails validation forever.
  Every failure is reported.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scripts.batch.memory_governor import (
    DEFAULT_BUDGET_DIR,
    MemoryBudget,
    MemoryGovernorError,
    _pid_alive,
)


REAP_SCHEMA = "harbor-disk-reap/v1"
# See recovery_worker.Config for how these are chosen. VM1's maximum free
# space is about 235 GiB, so the band between "start reaping" and "refuse to
# run" is kept to 20 GiB; a wider one would be less than one large AST.
DEFAULT_MIN_FREE_DISK_BYTES = 120 * 1024 * 1024 * 1024
DEFAULT_DISK_HIGH_WATER_BYTES = 140 * 1024 * 1024 * 1024
# The prompt gives measured AST and segmentation commands a 7200-second
# ceiling, so a generation younger than that may simply not have published.
DEFAULT_GENERATION_GRACE_SECONDS = 7200
# A staging directory's mtime is set when its top-level children appear, not
# as the AST is written into it, so the age gate is twice the ceiling.
STAGING_GRACE_FACTOR = 2

GENERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
HEX64_RE = re.compile(r"\b[0-9a-f]{64}\b")
# Every staging prefix in xl_source_recalc, xl_source_publication,
# xl_seg/publication and xl_release_publication. All are created directly
# under source_out/<wb>, seg_out/<wb>, or release_out/<wb>.
STAGING_PREFIXES = (
    ".source-build-",
    ".source-stage-",
    ".original-source-snapshot-",
    ".task-stage-",
    ".release-stage-",
    ".legacy-stage-",
)
SEG_STAGE_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9._-]*\.seg-stage-")
PIPELINE_ROOTS = ("source_out", "seg_out", "release_out")
# Evidence categories under a lane's state root, and how many path components
# sit between the category and one prunable unit. Units are pruned whole:
# failed-releases/<wb>/versions/<token>, worktree-quarantine/<wb>/<token>,
# candidates/<wb>/<identity_key>, delivery-quarantine/<wb>.
EVIDENCE_UNIT_DEPTH: Mapping[str, int] = {
    "failed-releases": 3,
    "worktree-quarantine": 2,
    "candidates": 2,
    "delivery-quarantine": 1,
}
# What must never be reaped, and why, so the deny list is auditable:
#   output-*/           delivery bundles; deleting one re-queues a finished row
#   verification_manifests/   read by the agent and _load_pinned_inventory
#   .venv, .env         symlinks into the baseline
# None of these are under source_out/seg_out/release_out generations or the
# evidence categories, so the scans below cannot reach them by construction.


class DiskReaperError(RuntimeError):
    """The reaper could not run safely."""


def free_disk_bytes(path: Path) -> int | None:
    """Free bytes available to this user on ``path``'s filesystem.

    ``shutil.disk_usage`` reports ``f_bavail``, the space a non-root process
    can actually use. The nearest existing ancestor is used so a not-yet
    created work root still resolves. None when nothing can be measured.
    """

    probe = Path(path)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


def tree_size_bytes(root: Path) -> int:
    """Apparent size of a tree without following symlinks.

    ``recovery_worker._safe_tree_size`` raises on any symlink and on absolute
    targets, and a worktree contains ``.venv`` and ``.env`` as absolute
    symlinks into the baseline, so it is the wrong tool for reporting here.
    """

    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                # Source publication hardlinks the build copy into the staged
                # generation, so during that window a file has two names on
                # one inode. Charge each name its share so ``reclaimed_bytes``
                # is not overstated when only one name is removed.
                total += info.st_size // max(1, info.st_nlink)
    return total


def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def _is_real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _read_json(path: Path) -> object | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def _hex_ids_in(value: object) -> set[str]:
    """Every 64-hex string anywhere in a JSON value, keys included.

    Over-inclusive on purpose: the manifests name their upstream generation
    under several different keys, and treating an extra hash as live only
    ever keeps more.
    """

    found: set[str] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            found.update(HEX64_RE.findall(item))
        elif isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return found


def live_cwds(proc: Path = Path("/proc")) -> list[Path]:
    """Resolved cwd of every process this user can read.

    Tolerates the races inherent in /proc: a pid that exits mid-scan, a
    process owned by another user, and the `` (deleted)`` suffix on a cwd
    whose directory is gone.
    """

    if not proc.is_dir():
        return []
    cwds: list[Path] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            target = os.readlink(entry / "cwd")
        except (OSError, PermissionError, ProcessLookupError):
            continue
        if target.endswith(" (deleted)"):
            target = target[: -len(" (deleted)")]
        cwds.append(Path(target))
    return cwds


def _is_ancestor_of_any(path: Path, cwds: Iterable[Path]) -> bool:
    resolved = path.resolve(strict=False)
    for cwd in cwds:
        try:
            if cwd == resolved or cwd.is_relative_to(resolved):
                return True
        except ValueError:
            continue
    return False


def live_reservation_workbooks(budget: MemoryBudget | None) -> set[str]:
    """Workbook ids held by a lane whose pid is still alive."""

    if budget is None:
        return set()
    try:
        state = budget.snapshot()
    except (MemoryGovernorError, OSError):
        return set()
    live: set[str] = set()
    for record in dict(state.get("reservations", {})).values():
        if not isinstance(record, Mapping):
            continue
        if _pid_alive(record.get("pid")) and record.get("workbook_id"):
            live.add(str(record["workbook_id"]))
    return live


def _is_staging_name(name: str) -> bool:
    return name.startswith(STAGING_PREFIXES) or bool(SEG_STAGE_RE.match(name))


@dataclass(frozen=True)
class ReapPolicy:
    high_water_bytes: int = DEFAULT_DISK_HIGH_WATER_BYTES
    floor_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES
    generation_grace_seconds: int = DEFAULT_GENERATION_GRACE_SECONDS
    staging_grace_seconds: int | None = None

    def staging_grace(self) -> int:
        if self.staging_grace_seconds is not None:
            return int(self.staging_grace_seconds)
        return int(self.generation_grace_seconds) * STAGING_GRACE_FACTOR


@dataclass
class ReapCandidate:
    kind: str
    path: Path
    bytes: int
    age_seconds: float
    workbook_id: str
    reason: str
    companions: tuple[Path, ...] = ()

    def to_record(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "bytes": int(self.bytes),
            "age_seconds": round(float(self.age_seconds), 1),
            "workbook_id": self.workbook_id,
            "reason": self.reason,
            "companions": [str(item) for item in self.companions],
        }


@dataclass
class ReapReport:
    dry_run: bool
    free_before: int | None
    free_after: int | None
    target_bytes: int | None
    reaped: list[dict[str, object]] = field(default_factory=list)
    failed: list[dict[str, object]] = field(default_factory=list)
    kept: list[dict[str, object]] = field(default_factory=list)
    skipped_worktrees: list[dict[str, object]] = field(default_factory=list)

    @property
    def reclaimed_bytes(self) -> int:
        return sum(int(item.get("bytes", 0)) for item in self.reaped)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": REAP_SCHEMA,
            "dry_run": self.dry_run,
            "free_before": self.free_before,
            "free_after": self.free_after,
            "target_bytes": self.target_bytes,
            "reclaimed_bytes": self.reclaimed_bytes,
            "reaped": self.reaped,
            "failed": self.failed,
            "kept": self.kept,
            "skipped_worktrees": self.skipped_worktrees,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


class DiskReaper:
    """Plan and apply reclamation over explicit work and state roots."""

    def __init__(
        self,
        *,
        work_roots: Sequence[Path],
        state_roots: Sequence[Path] = (),
        policy: ReapPolicy | None = None,
        budget: MemoryBudget | None = None,
        lock_dir: Path | None = None,
        now: float | None = None,
        cwd_probe=live_cwds,
    ) -> None:
        if not work_roots:
            raise DiskReaperError("at least one work root is required")
        self.work_roots = [Path(root) for root in work_roots]
        self.state_roots = [Path(root) for root in state_roots]
        self.policy = policy or ReapPolicy()
        self.budget = budget
        self.now = time.time() if now is None else float(now)
        self._cwd_probe = cwd_probe
        if lock_dir is not None:
            self.lock_dir = Path(lock_dir)
        elif budget is not None:
            self.lock_dir = budget.directory
        else:
            self.lock_dir = (
                self.state_roots[0] if self.state_roots else self.work_roots[0]
            )

    # -- locking -------------------------------------------------------------

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize reapers on the host.

        A separate lock from the memory budget's own, so a multi-minute
        ``rmtree`` does not stall every sibling's ``acquire``/``resize``.
        Confinement to the caller's own roots is what makes that safe.
        """

        self.lock_dir.mkdir(parents=True, exist_ok=True)
        host = self.budget.host_id if self.budget is not None else "local"
        descriptor = os.open(
            self.lock_dir / f"{host}.disk-reap.lock", os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    # -- measurement ---------------------------------------------------------

    def free_bytes(self) -> int | None:
        return free_disk_bytes(self.work_roots[0])

    def worktrees(self) -> list[Path]:
        found: list[Path] = []
        for root in self.work_roots:
            if not _is_real_dir(root):
                continue
            for entry in sorted(root.iterdir()):
                if entry.name.startswith("task-") and _is_real_dir(entry):
                    found.append(entry)
        return found

    def live_worktree_reason(
        self,
        worktree: Path,
        *,
        live_workbooks: set[str],
        cwds: Sequence[Path],
    ) -> str | None:
        workbook_id = worktree.name[len("task-"):]
        if workbook_id in live_workbooks:
            return "held by a live memory-budget reservation"
        if _is_ancestor_of_any(worktree, cwds):
            return "ancestor of a live process cwd"
        return None

    # -- liveness signals ----------------------------------------------------

    def keep_signals(self, worktree: Path) -> dict[str, str]:
        """Map every source generation id named downstream to the signal.

        Reads only structured pointers and manifests plus the flat
        ``runs/<wb>-*.json`` audit files. ``runs/`` is not walked recursively:
        it also holds multi-hundred-MB ``graph.json`` files, and a substring
        scan of those during a disk-pressure event is the wrong tool.
        """

        signals: dict[str, str] = {}

        def note(ids: Iterable[str], signal: str) -> None:
            for generation_id in ids:
                signals.setdefault(generation_id, signal)

        source_out = worktree / "source_out"
        if _is_real_dir(source_out):
            for wb_dir in source_out.iterdir():
                if wb_dir.name.startswith(".") or not _is_real_dir(wb_dir):
                    continue
                note(_hex_ids_in(_read_json(wb_dir / "current.json")), "source current.json")
        seg_out = worktree / "seg_out"
        if _is_real_dir(seg_out):
            for manifest in seg_out.glob("*/generations/*/generation-manifest.json"):
                note(_hex_ids_in(_read_json(manifest)), "segmentation generation manifest")
            for pointer in seg_out.glob("*/current.json"):
                note(_hex_ids_in(_read_json(pointer)), "segmentation current.json")
        release_out = worktree / "release_out"
        if _is_real_dir(release_out):
            for manifest in release_out.glob("*/releases/*/release-manifest.json"):
                note(_hex_ids_in(_read_json(manifest)), "release manifest")
            for pointer in release_out.glob("*/current-release.json"):
                note(_hex_ids_in(_read_json(pointer)), "current-release.json")
            for manifest in release_out.glob(
                "*/task-generations/*/generation-manifest.json"
            ):
                note(_hex_ids_in(_read_json(manifest)), "task generation manifest")
        for state in self.state_roots:
            candidates = state / "candidates"
            if _is_real_dir(candidates):
                for manifest in candidates.rglob("release-manifest.json"):
                    if any(part.startswith(".") for part in manifest.relative_to(candidates).parts):
                        continue
                    note(_hex_ids_in(_read_json(manifest)), "candidate release manifest")
        runs = worktree / "runs"
        if _is_real_dir(runs):
            for entry in runs.iterdir():
                if entry.suffix == ".json" and not entry.name.startswith("."):
                    note(_hex_ids_in(_read_json(entry)), "runs audit file")
        return signals

    # -- planning ------------------------------------------------------------

    def _staging_candidates(self, worktree: Path, grace: int) -> list[ReapCandidate]:
        found: list[ReapCandidate] = []
        for root_name in PIPELINE_ROOTS:
            root = worktree / root_name
            if not _is_real_dir(root):
                continue
            for wb_dir in root.iterdir():
                if wb_dir.name.startswith(".") or not _is_real_dir(wb_dir):
                    continue
                for entry in wb_dir.iterdir():
                    if not _is_staging_name(entry.name) or not _is_real_dir(entry):
                        continue
                    age = self.now - _mtime(entry)
                    if age < grace:
                        continue
                    found.append(
                        ReapCandidate(
                            kind="staging",
                            path=entry,
                            bytes=tree_size_bytes(entry),
                            age_seconds=age,
                            workbook_id=wb_dir.name,
                            reason=f"staging dir older than {grace}s with no live process",
                        )
                    )
        return found

    def _generation_candidates(
        self, worktree: Path, kept: list[dict[str, object]]
    ) -> list[ReapCandidate]:
        source_out = worktree / "source_out"
        if not _is_real_dir(source_out):
            return []
        signals = self.keep_signals(worktree)
        grace = int(self.policy.generation_grace_seconds)
        found: list[ReapCandidate] = []
        for wb_dir in sorted(source_out.iterdir()):
            if wb_dir.name.startswith(".") or not _is_real_dir(wb_dir):
                continue
            generations = wb_dir / "generations"
            if not _is_real_dir(generations):
                continue
            entries = [
                entry
                for entry in generations.iterdir()
                if GENERATION_ID_RE.match(entry.name) and _is_real_dir(entry)
            ]
            if not entries:
                continue
            newest = max(entries, key=_mtime)
            for entry in entries:
                age = self.now - _mtime(entry)
                keep_reason: str | None = None
                if entry.name in signals:
                    keep_reason = signals[entry.name]
                elif entry is newest:
                    keep_reason = "newest generation for this workbook"
                elif age < grace:
                    keep_reason = f"younger than the {grace}s grace window"
                if keep_reason is not None:
                    kept.append(
                        {
                            "kind": "generation",
                            "path": str(entry),
                            "workbook_id": wb_dir.name,
                            "kept_by": keep_reason,
                        }
                    )
                    continue
                receipts = wb_dir / "receipts" / entry.name
                found.append(
                    ReapCandidate(
                        kind="generation",
                        path=entry,
                        bytes=tree_size_bytes(entry),
                        age_seconds=age,
                        workbook_id=wb_dir.name,
                        reason=(
                            "no pointer, manifest, or audit names it; "
                            "not newest; past grace"
                        ),
                        companions=(receipts,) if _is_real_dir(receipts) else (),
                    )
                )
        return found

    def _evidence_candidates(self, live_workbooks: set[str]) -> list[ReapCandidate]:
        found: list[ReapCandidate] = []
        for state in self.state_roots:
            for category, depth in EVIDENCE_UNIT_DEPTH.items():
                root = state / category
                if not _is_real_dir(root):
                    continue
                for unit in self._units_at_depth(root, depth):
                    workbook_id = unit.relative_to(root).parts[0]
                    if workbook_id in live_workbooks:
                        continue
                    found.append(
                        ReapCandidate(
                            kind="evidence",
                            path=unit,
                            bytes=tree_size_bytes(unit),
                            age_seconds=self.now - _mtime(unit),
                            workbook_id=workbook_id,
                            reason=f"{category} unit, oldest first",
                        )
                    )
        return found

    @staticmethod
    def _units_at_depth(root: Path, depth: int) -> list[Path]:
        # Every category stages under a dot-prefixed sibling before an
        # os.replace, so a dot-prefixed entry at any level is a copy in
        # progress and is never a unit.
        level = [root]
        for _ in range(depth):
            next_level: list[Path] = []
            for directory in level:
                try:
                    entries = list(directory.iterdir())
                except OSError:
                    continue
                for entry in entries:
                    if entry.name.startswith(".") or not _is_real_dir(entry):
                        continue
                    next_level.append(entry)
            level = next_level
        return level

    def plan(
        self,
        *,
        kinds: Sequence[str] = ("staging", "generation", "evidence"),
        staging_grace_seconds: int | None = None,
        only_worktrees: Sequence[Path] | None = None,
    ) -> tuple[list[ReapCandidate], ReapReport]:
        """Order candidates: staging, then generations, then evidence.

        Within a tier, oldest first. ``only_worktrees`` restricts the
        worktree scans (Tier 1 sweeps one worktree at row close).
        """

        report = ReapReport(
            dry_run=True,
            free_before=self.free_bytes(),
            free_after=None,
            target_bytes=None,
        )
        live_workbooks = live_reservation_workbooks(self.budget)
        cwds = list(self._cwd_probe())
        grace = (
            self.policy.staging_grace()
            if staging_grace_seconds is None
            else int(staging_grace_seconds)
        )
        worktrees = self.worktrees()
        if only_worktrees is not None:
            wanted = {Path(item).resolve(strict=False) for item in only_worktrees}
            worktrees = [
                item for item in worktrees if item.resolve(strict=False) in wanted
            ]
        staging: list[ReapCandidate] = []
        generations: list[ReapCandidate] = []
        for worktree in worktrees:
            reason = self.live_worktree_reason(
                worktree, live_workbooks=live_workbooks, cwds=cwds
            )
            if reason is not None:
                report.skipped_worktrees.append(
                    {"path": str(worktree), "reason": reason}
                )
                continue
            if "staging" in kinds:
                staging.extend(self._staging_candidates(worktree, grace))
            if "generation" in kinds:
                generations.extend(self._generation_candidates(worktree, report.kept))
        evidence: list[ReapCandidate] = []
        if "evidence" in kinds:
            evidence = self._evidence_candidates(live_workbooks)
        oldest_first = lambda item: -item.age_seconds  # noqa: E731
        ordered = (
            sorted(staging, key=oldest_first)
            + sorted(generations, key=oldest_first)
            + sorted(evidence, key=oldest_first)
        )
        return ordered, report

    # -- applying ------------------------------------------------------------

    @staticmethod
    def _rmtree(path: Path, failures: list[dict[str, object]]) -> bool:
        problems: list[str] = []

        def onexc(_function, failed_path, error):
            problems.append(f"{failed_path}: {error!r}")

        shutil.rmtree(path, onexc=onexc)
        if problems or path.exists():
            failures.append(
                {"path": str(path), "errors": problems[:20], "remaining": path.exists()}
            )
            return False
        return True

    @staticmethod
    def _repair_failed_release_pointer(unit: Path) -> None:
        # failed-releases/<wb>/versions/<token> -> failed-releases/<wb>/current.json
        pointer = unit.parent.parent / "current.json"
        current = _read_json(pointer)
        if not isinstance(current, Mapping):
            return
        target = str(current.get("snapshot_path", ""))
        if target and Path(target).resolve(strict=False) == unit.resolve(strict=False):
            with contextlib.suppress(OSError):
                pointer.unlink()

    def reap(
        self,
        *,
        apply: bool,
        target_free_bytes: int | None = None,
        kinds: Sequence[str] = ("staging", "generation", "evidence"),
        staging_grace_seconds: int | None = None,
        only_worktrees: Sequence[Path] | None = None,
    ) -> ReapReport:
        """Delete candidates in order until free space clears the target.

        Dry-run unless ``apply``. ``target_free_bytes=None`` means take every
        candidate (Tier 1 sweeps); otherwise re-measure before each deletion
        and stop as soon as the target is met.
        """

        with self.locked():
            candidates, report = self.plan(
                kinds=kinds,
                staging_grace_seconds=staging_grace_seconds,
                only_worktrees=only_worktrees,
            )
            report.dry_run = not apply
            report.target_bytes = target_free_bytes
            for candidate in candidates:
                if target_free_bytes is not None:
                    free = self.free_bytes()
                    if free is not None and free >= target_free_bytes:
                        break
                record = candidate.to_record()
                if not apply:
                    report.reaped.append(record)
                    continue
                if not self._rmtree(candidate.path, report.failed):
                    continue
                for companion in candidate.companions:
                    if _is_real_dir(companion):
                        self._rmtree(companion, report.failed)
                if candidate.kind == "evidence" and "failed-releases" in candidate.path.parts:
                    self._repair_failed_release_pointer(candidate.path)
                report.reaped.append(record)
            report.free_after = self.free_bytes()
        return report

    # -- reporting -----------------------------------------------------------

    def report(self) -> dict[str, object]:
        live_workbooks = live_reservation_workbooks(self.budget)
        cwds = list(self._cwd_probe())
        worktrees: list[dict[str, object]] = []
        for worktree in self.worktrees():
            staging_bytes = 0
            staging_count = 0
            generation_bytes = 0
            generation_count = 0
            for root_name in PIPELINE_ROOTS:
                root = worktree / root_name
                if not _is_real_dir(root):
                    continue
                for wb_dir in root.iterdir():
                    if not _is_real_dir(wb_dir) or wb_dir.name.startswith("."):
                        continue
                    for entry in wb_dir.iterdir():
                        if _is_staging_name(entry.name) and _is_real_dir(entry):
                            staging_count += 1
                            staging_bytes += tree_size_bytes(entry)
                    generations = wb_dir / "generations"
                    if root_name == "source_out" and _is_real_dir(generations):
                        for entry in generations.iterdir():
                            if GENERATION_ID_RE.match(entry.name) and _is_real_dir(entry):
                                generation_count += 1
                                generation_bytes += tree_size_bytes(entry)
            worktrees.append(
                {
                    "path": str(worktree),
                    "bytes": tree_size_bytes(worktree),
                    "live": self.live_worktree_reason(
                        worktree, live_workbooks=live_workbooks, cwds=cwds
                    ),
                    "staging_dirs": staging_count,
                    "staging_bytes": staging_bytes,
                    "source_generations": generation_count,
                    "source_generation_bytes": generation_bytes,
                }
            )
        evidence: dict[str, int] = {}
        for state in self.state_roots:
            for category in EVIDENCE_UNIT_DEPTH:
                root = state / category
                if _is_real_dir(root):
                    evidence[str(root)] = tree_size_bytes(root)
        return {
            "schema_version": REAP_SCHEMA,
            "free_bytes": self.free_bytes(),
            "floor_bytes": self.policy.floor_bytes,
            "high_water_bytes": self.policy.high_water_bytes,
            "live_workbooks": sorted(live_workbooks),
            "worktrees": worktrees,
            "evidence_bytes": evidence,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _budget_from_args(args: argparse.Namespace) -> MemoryBudget | None:
    if args.no_governor:
        return None
    try:
        return MemoryBudget(args.budget_dir, host_id=args.host_id)
    except MemoryGovernorError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--work-root",
        action="append",
        required=True,
        help="a lane's work root (task-* worktrees); repeatable",
    )
    parser.add_argument(
        "--state-root",
        action="append",
        default=[],
        help="a lane's state root (evidence categories); repeatable",
    )
    parser.add_argument("--budget-dir", default=str(DEFAULT_BUDGET_DIR))
    parser.add_argument("--host-id", default=None)
    parser.add_argument(
        "--no-governor",
        action="store_true",
        help="do not consult the memory budget for live reservations",
    )
    parser.add_argument("--floor-bytes", type=int, default=DEFAULT_MIN_FREE_DISK_BYTES)
    parser.add_argument(
        "--high-water-bytes", type=int, default=DEFAULT_DISK_HIGH_WATER_BYTES
    )
    parser.add_argument(
        "--generation-grace-seconds",
        type=int,
        default=DEFAULT_GENERATION_GRACE_SECONDS,
    )
    parser.add_argument("--staging-grace-seconds", type=int, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("report", help="free space and per-worktree usage")
    plan = subparsers.add_parser(
        "plan", help="list what reap would delete and why each kept item is kept"
    )
    plan.add_argument("--all", action="store_true", help="ignore the high-water mark")
    reap = subparsers.add_parser("reap", help="delete candidates (dry-run by default)")
    reap.add_argument("--apply", action="store_true", help="actually delete")
    reap.add_argument(
        "--all", action="store_true", help="reap every candidate, not just to high-water"
    )
    args = parser.parse_args(argv)

    policy = ReapPolicy(
        high_water_bytes=args.high_water_bytes,
        floor_bytes=args.floor_bytes,
        generation_grace_seconds=args.generation_grace_seconds,
        staging_grace_seconds=args.staging_grace_seconds,
    )
    reaper = DiskReaper(
        work_roots=[Path(item) for item in args.work_root],
        state_roots=[Path(item) for item in args.state_root],
        policy=policy,
        budget=_budget_from_args(args),
    )
    if args.command == "report":
        print(json.dumps(reaper.report(), sort_keys=True, indent=2))
        return 0
    target = None if args.all else policy.high_water_bytes
    if args.command == "plan":
        report = reaper.reap(apply=False, target_free_bytes=target)
        print(json.dumps(report.to_record(), sort_keys=True, indent=2))
        return 0
    report = reaper.reap(apply=args.apply, target_free_bytes=target)
    print(json.dumps(report.to_record(), sort_keys=True, indent=2))
    if report.failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
