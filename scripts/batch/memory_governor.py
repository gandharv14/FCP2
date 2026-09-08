#!/usr/bin/env python3
"""Predict, reserve, and cap per-task memory so a recovery VM keeps sshd alive.

The recovery fleet runs two generation lanes per VM and each lane materializes
a whole workbook in memory. The VMs have no swap, so two heavy rows at once
exhaust anonymous memory and sshd loses its allocation. This module predicts a
row's peak before the agent starts, reserves that prediction from a per-host
budget shared by every lane, and keeps a reserve that is never handed out.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import posixpath
import re
import socket
import stat
import subprocess
import time
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

PROFILE_SCHEMA = "harbor-workbook-profile/v1"
BUDGET_SCHEMA = "harbor-memory-budget/v1"
OBSERVATION_SCHEMA = "harbor-memory-observation/v1"
PLAN_SCHEMA = "harbor-memory-plan/v1"

GIB = 1024 * 1024 * 1024
# Mirrors the package caps in xl_source_health so a hostile archive cannot make
# the probe itself the memory problem. Kept local because the probe must stay
# importable by the worker without pulling in openpyxl or xl_seg.
MAX_ZIP_MEMBERS = 50_000
MAX_EXPANDED_BYTES = 2 * GIB

# Seed cost model. A row's stages run one after another inside one scope, so
# the peak is the heaviest stage, not their sum. The workbook load (openpyxl,
# now streaming) is small: 1-2 GB on the largest fleet workbook. The AST
# build holds one dict per node and per edge: 22 GB on 0568 (1.41M formula
# cells, 13.2M nodes, 22.7M edges), 0.34 GB on a 32.7k-formula workbook, so
# roughly 10-16 KB per formula cell. Segmentation (`xl_segment.py`) loads
# that graph as Python objects and the evaluator adds its own structures on
# top. On 0568 the old graph code passed 101 GB and was killed at a 106 GB
# cap; after slots + interning a direct run (no agent, published AST) loaded
# the graph in 11 GB, built the cell-level adj/radj sets to 58 GB, and was
# killed by the evaluator at the 118.9 GB whole-budget cap after 44 min. The
# segmentation cost is therefore at least 117.8 GB / 1.41M = 83.5 KB per
# formula cell (censored: the true peak is higher). Local runs of the same
# code: 18 KB (0573), 17 KB (0625), 212 KB (0479, a range-heavy sheet with
# 26 edges per node that the probe cannot see; the 0.9-quantile calibration
# exists for rows like it). Rows above ~700k formula cells therefore seed
# past the budget, are clamped to it, and run alone; the retry prompt tells
# a memory-killed attempt to shrink the cone, which is the only way such a
# row completes. The old AST_NODE_BYTES = 600 was the only per-formula term
# and left the seed 5.5x low on 0568; a scale ceiling of 4.0 could never
# have closed that.
AGENT_BASE_BYTES = 1536 * 1024 * 1024
OPENPYXL_PASSES = 2
FORMULA_CELL_BYTES = 1100
AST_BUILD_BYTES_PER_FORMULA = 16 * 1024
SEGMENTATION_BYTES_PER_FORMULA = 84 * 1024
# Kept for readers of old observation records; no longer used by the model.
AST_NODE_BYTES = 600
VALUE_CELL_BYTES = 400
EXPANDED_BYTES_FACTOR = 6
SAFETY_FACTOR = 1.4
MIN_RESERVE_BYTES = 4 * GIB
# Prior on the calibration scale until enough completed rows exist to fit it.
# The first fleet run observed real peaks at 2.0x the seed model on every
# completed row (12.1/6.0, 11.6/5.7, 13.8/6.6 GiB), and a scale of 1.0 for the
# first five rows meant every one of them ran throttled. The prior is applied
# to the estimate only, never folded into seed_bytes, so recorded ratios stay
# raw and calibration converges to the true ratio rather than to its square.
SEED_SCALE_ENV = "HARBOR_MEMORY_SEED_SCALE"
DEFAULT_SEED_SCALE = 2.0

DEFAULT_RESERVE_BYTES = 8 * GIB
RESERVE_FRACTION = 0.12
DEFAULT_BUDGET_DIR = Path("/var/tmp/harbor-memory")
DEFAULT_MEMINFO = Path("/proc/meminfo")
ACQUIRE_POLL_SECONDS = 1.0

# Three completed rows is enough to move off the prior: the prior is already
# measured, and with observations shared across a host's lanes three samples
# arrive within the first round of rows.
CALIBRATION_MIN_SAMPLES = 3
CALIBRATION_MIN_SCALE = 0.5
CALIBRATION_MAX_SCALE = 4.0
CALIBRATION_QUANTILE = 0.9
COMPLETED_STATUS = "completed"
# A truncated peak is evidence the true peak was at least this high, so it may
# raise the scale. An orchestrator error is a launcher crash, not evidence
# about memory at all, so it is excluded entirely.
CENSORED_STATUSES = frozenset({"failed", "timed_out"})
CENSORED_WINDOW = 20
COMPLETED_WINDOW = 50

# Ceiling on lanes per host. 128 GB hosts (16 vCPU, 125 GiB) launch with two
# (`--lane-cap 2`): the 2026-09-02 one-lane smoke showed a lane's real peak is
# `xl_segment` on the full AST (101+ GB on 0568), not the ~6-8 GB AST build
# the earlier fleet saw under a 13.6 GB cap that forced the agent to shrink
# the AST first. Three lanes at ~33 GiB each wait on observed anon peaks and
# a passing direct smoke at that cap (see "Lane count" in
# .cursor/skills/run-fcp-workbook-fleet/reference.md). The runtime admission
# gate still serialises rows whose reservations do not fit, so the cap is a
# ceiling, not a promise. Four is for 512 GB hosts (443 GiB budget: ~108 GiB
# per lane, the whole budget of a 128 GB host). On 128 GB hosts keep passing
# `--lane-cap 2` (or 3 with calibration evidence); build_queues.py does.
LANE_CAP = 4
# Disk decides how many lanes a host can carry: each lane holds a worktree,
# retained generations, and quarantine copies (~60 GiB at the high-water
# mark), and the reaper keeps a 120 GiB floor free for the outliers.
LANE_DISK_FLOOR_BYTES = 120 * GIB
LANE_DISK_BYTES = 60 * GIB
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class MemoryGovernorError(RuntimeError):
    """A probe, budget, or calibration operation cannot be trusted."""


class BudgetTimeout(TimeoutError):
    """A lane waited for budget past its deadline."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def safe_id(value: object, description: str) -> str:
    result = str(value).strip()
    if not SAFE_ID_RE.fullmatch(result):
        raise MemoryGovernorError(f"{description} is unsafe: {value!r}")
    return result


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkbookProfile:
    """Cheap size features of a workbook, measured without loading it."""

    file_bytes: int
    package_members: int
    package_expanded_bytes: int
    sheet_count: int
    shared_string_count: int
    formula_cells: int
    value_cells: int

    def to_record(self) -> dict[str, object]:
        return {"schema_version": PROFILE_SCHEMA, **asdict(self)}

    @classmethod
    def from_record(cls, value: Mapping[str, object]) -> "WorkbookProfile":
        try:
            return cls(
                file_bytes=int(value["file_bytes"]),
                package_members=int(value["package_members"]),
                package_expanded_bytes=int(value["package_expanded_bytes"]),
                sheet_count=int(value["sheet_count"]),
                shared_string_count=int(value["shared_string_count"]),
                formula_cells=int(value["formula_cells"]),
                value_cells=int(value["value_cells"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryGovernorError(
                f"workbook profile is malformed: {error}"
            ) from error


def _open_regular_nofollow(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    handle = os.fdopen(descriptor, "rb")
    metadata = os.fstat(handle.fileno())
    if not stat.S_ISREG(metadata.st_mode):
        handle.close()
        raise MemoryGovernorError(f"probe source is not a regular file: {path}")
    return handle


def _sheet_parts(archive: zipfile.ZipFile) -> list[str]:
    """Resolve worksheet parts, rejecting relationships that escape the zip."""

    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relations = ElementTree.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
    except (KeyError, ElementTree.ParseError) as error:
        raise MemoryGovernorError(f"unreadable workbook package: {error}") from error
    targets = {
        relation.attrib.get("Id"): relation.attrib.get("Target", "")
        for relation in relations.findall(f"{{{_REL_NS}}}Relationship")
    }
    names = set(archive.namelist())
    parts: list[str] = []
    for sheet in workbook.findall(".//{*}sheet"):
        target = targets.get(sheet.attrib.get(f"{{{_DOC_REL_NS}}}id"), "")
        if not target:
            continue
        raw = target.lstrip("/") if target.startswith("/") else posixpath.join(
            "xl", target
        )
        normalized = posixpath.normpath(raw)
        if normalized == ".." or normalized.startswith("../"):
            raise MemoryGovernorError("worksheet relationship escapes the package")
        if normalized in names:
            parts.append(normalized)
    return parts


def _count_sheet_cells(archive: zipfile.ZipFile, part: str) -> tuple[int, int]:
    """Stream one sheet part, counting formula and non-formula cells.

    Peak memory has to stay flat here regardless of sheet size, or the probe
    becomes the very problem it exists to predict. Two things are needed.

    Clearing a row drops its cell children. But the parser appends every
    element in a whole read buffer to its parent before the generator yields
    that buffer's first ``end`` event, so at row N's ``end`` the last child of
    ``sheetData`` is the last row of the current buffer, not row N. Deleting
    only the matching child therefore fires roughly once per buffer and leaves
    the rest to pile up. Dropping every accumulated child instead bounds
    retention to one buffer. Cell counts stay exact because the pending event
    queue holds its own references to the elements it has yet to yield.
    """

    formula_cells = 0
    value_cells = 0
    sheet_data: ElementTree.Element | None = None
    with archive.open(part) as handle:
        context = ElementTree.iterparse(handle, events=("start", "end"))
        for event, element in context:
            name = _local_name(element.tag)
            if event == "start":
                if name == "sheetData":
                    sheet_data = element
                continue
            if name == "c":
                if any(_local_name(child.tag) == "f" for child in element):
                    formula_cells += 1
                else:
                    value_cells += 1
            elif name == "row":
                element.clear()
                if sheet_data is not None and len(sheet_data):
                    del sheet_data[:]
    return formula_cells, value_cells


def _shared_string_count(archive: zipfile.ZipFile) -> int:
    """Read the shared-string count from the root tag without parsing the part."""

    if "xl/sharedStrings.xml" not in set(archive.namelist()):
        return 0
    with archive.open("xl/sharedStrings.xml") as handle:
        for _, element in ElementTree.iterparse(handle, events=("start",)):
            raw = element.attrib.get("uniqueCount") or element.attrib.get("count")
            try:
                return max(0, int(str(raw)))
            except (TypeError, ValueError):
                return 0
    return 0


def probe_workbook(path: str | Path) -> WorkbookProfile:
    """Measure a workbook's size features without materializing it."""

    source = Path(path)
    try:
        with _open_regular_nofollow(source) as handle:
            file_bytes = os.fstat(handle.fileno()).st_size
            with zipfile.ZipFile(handle) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ZIP_MEMBERS:
                    raise MemoryGovernorError(
                        f"package has {len(infos)} members, above {MAX_ZIP_MEMBERS}"
                    )
                expanded = sum(max(0, info.file_size) for info in infos)
                if expanded > MAX_EXPANDED_BYTES:
                    raise MemoryGovernorError(
                        f"package expands to {expanded} bytes, above "
                        f"{MAX_EXPANDED_BYTES}"
                    )
                parts = _sheet_parts(archive)
                formula_cells = 0
                value_cells = 0
                for part in parts:
                    sheet_formulas, sheet_values = _count_sheet_cells(archive, part)
                    formula_cells += sheet_formulas
                    value_cells += sheet_values
                return WorkbookProfile(
                    file_bytes=file_bytes,
                    package_members=len(infos),
                    package_expanded_bytes=expanded,
                    sheet_count=len(parts),
                    shared_string_count=_shared_string_count(archive),
                    formula_cells=formula_cells,
                    value_cells=value_cells,
                )
    except MemoryGovernorError:
        raise
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise MemoryGovernorError(f"cannot probe {source}: {error}") from error


# ---------------------------------------------------------------------------
# Estimator and calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibration:
    """Scalar correction applied to the seed model."""

    scale: float = 1.0
    sample_count: int = 0
    censored_count: int = 0

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def seed_peak_bytes(profile: WorkbookProfile) -> int:
    """Predict peak bytes from the seed model, before any calibration."""

    load = (
        OPENPYXL_PASSES * FORMULA_CELL_BYTES * profile.formula_cells
        + VALUE_CELL_BYTES * profile.value_cells
        + EXPANDED_BYTES_FACTOR * profile.package_expanded_bytes
    )
    ast_build = AST_BUILD_BYTES_PER_FORMULA * profile.formula_cells
    segmentation = SEGMENTATION_BYTES_PER_FORMULA * profile.formula_cells
    # Stages are sequential in one scope: the peak is the heaviest of them,
    # sitting on top of the agent process and the streamed workbook load.
    return int(AGENT_BASE_BYTES + load + max(ast_build, segmentation))


def seed_scale(env: Mapping[str, str] | None = None) -> float:
    """Prior scale on the seed model, used until calibration has samples."""

    values = os.environ if env is None else env
    raw = str(values.get(SEED_SCALE_ENV, "")).strip()
    if not raw:
        return DEFAULT_SEED_SCALE
    try:
        value = float(raw)
    except ValueError as error:
        raise MemoryGovernorError(f"{SEED_SCALE_ENV} is not a number: {raw!r}") from error
    if not (CALIBRATION_MIN_SCALE <= value <= CALIBRATION_MAX_SCALE):
        raise MemoryGovernorError(
            f"{SEED_SCALE_ENV} must be within "
            f"[{CALIBRATION_MIN_SCALE}, {CALIBRATION_MAX_SCALE}]: {raw!r}"
        )
    return value


def estimate_peak_bytes(
    profile: WorkbookProfile,
    calibration: Calibration | None = None,
    *,
    budget_total: int | None = None,
    safety: float = SAFETY_FACTOR,
) -> int:
    """Return the bytes a row should reserve for its generation agent."""

    scale = calibration.scale if calibration is not None else seed_scale()
    value = max(MIN_RESERVE_BYTES, int(seed_peak_bytes(profile) * scale * safety))
    if budget_total is not None and budget_total > 0:
        value = min(value, budget_total)
    return value


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 1.0
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _observation_ratio(record: Mapping[str, object]) -> float | None:
    try:
        observed = int(record["observed_peak_bytes"])
    except (KeyError, TypeError, ValueError):
        return None
    if observed <= 0:
        return None
    seed = record.get("seed_bytes")
    try:
        seed_bytes = int(seed) if seed is not None else 0
    except (TypeError, ValueError):
        seed_bytes = 0
    if seed_bytes <= 0:
        profile = record.get("profile")
        if not isinstance(profile, Mapping):
            return None
        try:
            seed_bytes = seed_peak_bytes(WorkbookProfile.from_record(profile))
        except MemoryGovernorError:
            return None
    if seed_bytes <= 0:
        return None
    return observed / seed_bytes


def read_observations(path: Path) -> list[dict[str, object]]:
    """Read the append-only observation log, skipping unparsable lines."""

    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise MemoryGovernorError(f"cannot read observations: {error}") from error
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def calibration_from_observations(
    records: Iterable[Mapping[str, object]],
) -> Calibration:
    """Fit the scalar correction.

    Only completed runs set the baseline: a killed or timed-out run reports a
    truncated peak, and letting those into the quantile would push the scale
    down and make the next estimate more optimistic. Truncated peaks are kept
    as censored lower bounds, which can raise the scale but never lower it.
    """

    completed: list[float] = []
    censored: list[float] = []
    for record in records:
        ratio = _observation_ratio(record)
        if ratio is None:
            continue
        status = str(record.get("status", ""))
        if status == COMPLETED_STATUS:
            completed.append(ratio)
        elif status in CENSORED_STATUSES:
            censored.append(ratio)
    # Windowed like the censored samples below. The log is append-only, so an
    # unwindowed quantile keeps weighting observations from before any change
    # to how much memory the pipeline actually uses -- disabling the segment
    # memory profiler, or overlapping two builds -- and the estimate never
    # catches up with reality.
    recent_completed = completed[-COMPLETED_WINDOW:]
    if len(recent_completed) >= CALIBRATION_MIN_SAMPLES:
        base = _percentile(recent_completed, CALIBRATION_QUANTILE)
    else:
        # Not enough completed rows to fit: fall back to the measured prior
        # rather than to 1.0, which every first row has shown to be half of
        # reality.
        base = seed_scale()
    # The log is append-only, so an unwindowed maximum would let a single bad
    # censored sample pin the scale at its ceiling for the life of the file,
    # stranding the host at one lane with no way back short of deleting it.
    # Only recent truncated peaks can raise the scale.
    recent_censored = censored[-CENSORED_WINDOW:]
    scale = max(base, max(recent_censored, default=0.0))
    scale = min(CALIBRATION_MAX_SCALE, max(CALIBRATION_MIN_SCALE, scale))
    return Calibration(
        scale=scale,
        sample_count=len(completed),
        censored_count=len(censored),
    )


def load_calibration(path: Path, *extra_paths: Path) -> Calibration:
    """Fit calibration from one or more observation logs.

    A lane's own log is authoritative for order, but a host's lanes see the
    same pipeline on the same kernel, so pooling the sibling logs lets three
    lanes reach the sample minimum three times sooner. Records are
    deduplicated by (lane, workbook, recorded_at) since the worker appends
    each observation to its own log and to the host-shared one.
    """

    records = read_observations(path)
    own = {_canonical_json(record) for record in records}
    for candidate in extra_paths:
        for record in read_observations(candidate):
            # The worker writes one identical record to both logs; skip the
            # copy of our own, keep everything a sibling lane wrote.
            if _canonical_json(record) in own:
                continue
            records.append(record)
    records.sort(key=lambda record: str(record.get("recorded_at", "")))
    return calibration_from_observations(records)


def record_observation(
    path: Path,
    profile: WorkbookProfile,
    *,
    status: str,
    observed_peak_bytes: int,
    reserved_bytes: int,
    workbook_id: str = "",
    lane_id: str = "",
    extra: Mapping[str, object] | None = None,
) -> None:
    """Append one observation under an exclusive lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "schema_version": OBSERVATION_SCHEMA,
        "workbook_id": str(workbook_id),
        "lane_id": str(lane_id),
        "status": str(status),
        "observed_peak_bytes": max(0, int(observed_peak_bytes)),
        "reserved_bytes": max(0, int(reserved_bytes)),
        "seed_bytes": seed_peak_bytes(profile),
        "profile": profile.to_record(),
        "recorded_at": _timestamp(),
    }
    if extra:
        record.update(dict(extra))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            os.write(descriptor, _canonical_json(record))
            os.fsync(descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


# ---------------------------------------------------------------------------
# Host memory facts
# ---------------------------------------------------------------------------


def _meminfo_bytes(field: str, meminfo: Path = DEFAULT_MEMINFO) -> int | None:
    try:
        text = Path(meminfo).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        name, _, remainder = line.partition(":")
        if name.strip() != field:
            continue
        parts = remainder.split()
        if not parts:
            return None
        try:
            value = int(parts[0])
        except ValueError:
            return None
        unit = parts[1].lower() if len(parts) > 1 else "kb"
        return value * 1024 if unit == "kb" else value
    return None


def host_total_bytes(meminfo: Path = DEFAULT_MEMINFO) -> int | None:
    return _meminfo_bytes("MemTotal", meminfo)


def host_available_bytes(meminfo: Path = DEFAULT_MEMINFO) -> int | None:
    return _meminfo_bytes("MemAvailable", meminfo)


def reserve_bytes_for(total_bytes: int) -> int:
    """Headroom that is never handed out, so sshd always has an allocation.

    Capped at half the host so a small machine still gets a working gate. A
    reserve that swallowed the whole host would make the budget unusable and
    silently disengage the governor, which is worst on exactly the hosts that
    have the least room to spare.
    """

    reserve = max(DEFAULT_RESERVE_BYTES, int(total_bytes * RESERVE_FRACTION))
    return min(reserve, max(1, total_bytes // 2))


def default_host_id() -> str:
    raw = socket.gethostname().split(".")[0] or "localhost"
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", raw)
    return cleaned if SAFE_ID_RE.fullmatch(cleaned) else "localhost"


# ---------------------------------------------------------------------------
# Budget gate
# ---------------------------------------------------------------------------


def _pid_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


@dataclass
class BudgetLease:
    """A live reservation held by one lane for the duration of one row."""

    budget: "MemoryBudget"
    lane_id: str
    bytes: int
    workbook_id: str = ""
    released: bool = False

    def resize(
        self,
        new_bytes: int,
        *,
        timeout_seconds: float | None = None,
        on_wait: object = None,
    ) -> int:
        self.bytes = self.budget.resize(
            self.lane_id,
            new_bytes,
            workbook_id=self.workbook_id,
            timeout_seconds=timeout_seconds,
            on_wait=on_wait,
        )
        return self.bytes

    def release(self) -> None:
        if self.released:
            return
        self.budget.release(self.lane_id)
        self.released = True


class MemoryBudget:
    """A per-host memory budget shared by every lane on that host.

    The lock is held only across a read-modify-write, never across a wait, so a
    lane that dies cannot block the host; the kernel drops its flock and the
    next acquire reaps its reservation by pid liveness.
    """

    def __init__(
        self,
        directory: str | Path = DEFAULT_BUDGET_DIR,
        *,
        host_id: str | None = None,
        total_bytes: int | None = None,
        reserve_bytes: int | None = None,
        meminfo: Path = DEFAULT_MEMINFO,
        poll_seconds: float = ACQUIRE_POLL_SECONDS,
    ) -> None:
        self.directory = Path(directory)
        self.host_id = safe_id(host_id or default_host_id(), "host ID")
        self.meminfo = Path(meminfo)
        self.poll_seconds = max(0.01, float(poll_seconds))
        detected = host_total_bytes(self.meminfo)
        if total_bytes is not None:
            capacity = int(total_bytes)
        elif detected is not None:
            capacity = int(detected)
        else:
            raise MemoryGovernorError(
                "cannot determine host memory; pass total_bytes explicitly"
            )
        if capacity <= 0:
            raise MemoryGovernorError("host memory must be positive")
        self.reserve_bytes = (
            int(reserve_bytes)
            if reserve_bytes is not None
            else reserve_bytes_for(capacity)
        )
        if self.reserve_bytes < 0 or self.reserve_bytes >= capacity:
            raise MemoryGovernorError(
                "reserve must be non-negative and smaller than host memory"
            )
        self.host_bytes = capacity
        self.total_bytes = capacity - self.reserve_bytes

    @property
    def path(self) -> Path:
        return self.directory / f"{self.host_id}.json"

    @property
    def lock_path(self) -> Path:
        return self.directory / f"{self.host_id}.lock"

    # -- state -------------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # The default budget root lives under /var/tmp, which is world
        # writable, so keep the directory itself private to the fleet user.
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _read(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reservations = payload.get("reservations")
        waiters = payload.get("waiters")
        try:
            next_ticket = int(payload.get("next_ticket", 1))
        except (TypeError, ValueError):
            next_ticket = 1
        return {
            "reservations": dict(reservations)
            if isinstance(reservations, dict)
            else {},
            "waiters": list(waiters) if isinstance(waiters, list) else [],
            "next_ticket": max(1, next_ticket),
        }

    def _write(self, state: Mapping[str, object]) -> None:
        payload = {
            "schema_version": BUDGET_SCHEMA,
            "host_id": self.host_id,
            "host_bytes": self.host_bytes,
            "reserve_bytes": self.reserve_bytes,
            "total_bytes": self.total_bytes,
            "reservations": state.get("reservations", {}),
            "waiters": state.get("waiters", []),
            "next_ticket": state.get("next_ticket", 1),
            "updated_at": _timestamp(),
        }
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        try:
            os.write(descriptor, _canonical_json(payload))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)

    def _reap(self, state: dict[str, object]) -> dict[str, object]:
        reservations = {
            lane: record
            for lane, record in dict(state["reservations"]).items()
            if isinstance(record, dict) and _pid_alive(record.get("pid"))
        }
        waiters = [
            waiter
            for waiter in list(state["waiters"])
            if isinstance(waiter, dict) and _pid_alive(waiter.get("pid"))
        ]
        state["reservations"] = reservations
        state["waiters"] = waiters
        return state

    def _clamp(self, request: int) -> int:
        # A row that alone exceeds the budget is clamped so it can run by
        # itself instead of waiting for capacity that will never exist.
        return max(0, min(int(request), self.total_bytes))

    def _held_bytes(self, state: Mapping[str, object], *, exclude: str) -> int:
        total = 0
        for lane, record in dict(state["reservations"]).items():
            if lane == exclude or not isinstance(record, dict):
                continue
            try:
                total += max(0, int(record.get("bytes", 0)))
            except (TypeError, ValueError):
                continue
        return total

    def _blocked_by_older(
        self, state: Mapping[str, object], *, lane_id: str, ticket: int
    ) -> bool:
        for waiter in list(state["waiters"]):
            if not isinstance(waiter, dict) or waiter.get("lane_id") == lane_id:
                continue
            try:
                other = int(waiter.get("ticket", 0))
            except (TypeError, ValueError):
                continue
            if 0 < other < ticket:
                return True
        return False

    def _available_ok(self) -> bool:
        available = host_available_bytes(self.meminfo)
        if available is None:
            return True
        return available >= self.reserve_bytes

    # -- operations --------------------------------------------------------

    def acquire(
        self,
        lane_id: str,
        request_bytes: int,
        *,
        workbook_id: str = "",
        timeout_seconds: float | None = None,
        on_wait: object = None,
    ) -> BudgetLease:
        """Reserve budget for a lane, waiting in ticket order when needed."""

        lane = safe_id(lane_id, "lane ID")
        request = self._clamp(request_bytes)
        deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        ticket: int | None = None
        waited = False
        try:
            while True:
                with self._locked():
                    state = self._reap(self._read())
                    if ticket is None:
                        ticket = int(state["next_ticket"])
                        state["next_ticket"] = ticket + 1
                        state["waiters"] = [
                            waiter
                            for waiter in state["waiters"]
                            if not (
                                isinstance(waiter, dict)
                                and waiter.get("lane_id") == lane
                            )
                        ]
                        state["waiters"].append(
                            {
                                "lane_id": lane,
                                "ticket": ticket,
                                "bytes": request,
                                "workbook_id": str(workbook_id),
                                "pid": os.getpid(),
                                "queued_at": _timestamp(),
                            }
                        )
                        self._write(state)
                    held = self._held_bytes(state, exclude=lane)
                    fits = held + request <= self.total_bytes
                    if (
                        fits
                        and self._available_ok()
                        and not self._blocked_by_older(
                            state, lane_id=lane, ticket=ticket
                        )
                    ):
                        state["waiters"] = [
                            waiter
                            for waiter in state["waiters"]
                            if not (
                                isinstance(waiter, dict)
                                and waiter.get("lane_id") == lane
                            )
                        ]
                        state["reservations"][lane] = {
                            "bytes": request,
                            "workbook_id": str(workbook_id),
                            "pid": os.getpid(),
                            "acquired_at": _timestamp(),
                        }
                        self._write(state)
                        return BudgetLease(
                            budget=self,
                            lane_id=lane,
                            bytes=request,
                            workbook_id=str(workbook_id),
                        )
                if not waited and callable(on_wait):
                    on_wait()
                    waited = True
                if deadline is not None and time.monotonic() >= deadline:
                    raise BudgetTimeout(
                        f"lane {lane} waited past its deadline for "
                        f"{request} budget bytes"
                    )
                time.sleep(self.poll_seconds)
        except BaseException:
            if ticket is not None:
                self._drop_waiter(lane)
            raise

    def _drop_waiter(self, lane_id: str) -> None:
        try:
            with self._locked():
                state = self._read()
                state["waiters"] = [
                    waiter
                    for waiter in state["waiters"]
                    if not (
                        isinstance(waiter, dict)
                        and waiter.get("lane_id") == lane_id
                    )
                ]
                self._write(state)
        except OSError:
            return

    def resize(
        self,
        lane_id: str,
        new_bytes: int,
        *,
        workbook_id: str = "",
        timeout_seconds: float | None = None,
        on_wait: object = None,
    ) -> int:
        """Change a live reservation. Shrinking never blocks."""

        lane = safe_id(lane_id, "lane ID")
        request = self._clamp(new_bytes)
        surrendered: dict[str, object] | None = None
        with self._locked():
            state = self._reap(self._read())
            current = state["reservations"].get(lane)
            if isinstance(current, dict):
                try:
                    current_bytes = max(0, int(current.get("bytes", 0)))
                except (TypeError, ValueError):
                    current_bytes = 0
                if request <= current_bytes:
                    state["reservations"][lane] = {
                        **current,
                        "bytes": request,
                        "resized_at": _timestamp(),
                    }
                    self._write(state)
                    return request
                # Growing: hand the held bytes back before waiting. Two lanes
                # that each hold a small opening reservation and each need most
                # of the budget would otherwise deadlock on the other's
                # headroom. A waiting lane is not using memory, and its FIFO
                # ticket keeps a younger request from jumping ahead.
                surrendered = dict(current)
                state["reservations"].pop(lane, None)
                self._write(state)
        try:
            lease = self.acquire(
                lane,
                request,
                workbook_id=workbook_id,
                timeout_seconds=timeout_seconds,
                on_wait=on_wait,
            )
        except BaseException:
            # Never leave the caller unreserved. Surrendering the bytes was an
            # internal step of this resize, so a failed grow has to put them
            # back or the lane would run its stage with nothing accounted for
            # and a sibling would admit on top of it.
            if surrendered is not None:
                self._restore(lane, surrendered)
            raise
        return lease.bytes

    def _restore(self, lane_id: str, record: Mapping[str, object]) -> None:
        with contextlib.suppress(OSError, MemoryGovernorError):
            with self._locked():
                state = self._reap(self._read())
                state["reservations"][lane_id] = {
                    **dict(record),
                    "restored_at": _timestamp(),
                }
                self._write(state)

    def release(self, lane_id: str) -> None:
        lane = safe_id(lane_id, "lane ID")
        with self._locked():
            state = self._read()
            state["reservations"].pop(lane, None)
            state["waiters"] = [
                waiter
                for waiter in state["waiters"]
                if not (isinstance(waiter, dict) and waiter.get("lane_id") == lane)
            ]
            self._write(state)

    @contextmanager
    def lease(
        self,
        lane_id: str,
        request_bytes: int,
        *,
        workbook_id: str = "",
        timeout_seconds: float | None = None,
    ) -> Iterator[BudgetLease]:
        held = self.acquire(
            lane_id,
            request_bytes,
            workbook_id=workbook_id,
            timeout_seconds=timeout_seconds,
        )
        try:
            yield held
        finally:
            held.release()

    def snapshot(self, *, reap: bool = False) -> dict[str, object]:
        """Report budget state.

        Read-only by default. Reporting must not write, because an operator
        running the report under sudo would re-own the budget file and lock the
        non-root lanes out of their own gate. Stale reservations are reaped by
        the next acquire anyway.
        """

        if reap:
            with self._locked():
                state = self._reap(self._read())
                self._write(state)
        else:
            state = self._reap(self._read())
        used = self._held_bytes(state, exclude="")
        return {
            "schema_version": BUDGET_SCHEMA,
            "host_id": self.host_id,
            "path": str(self.path),
            "host_bytes": self.host_bytes,
            "reserve_bytes": self.reserve_bytes,
            "total_bytes": self.total_bytes,
            "used_bytes": used,
            "free_bytes": max(0, self.total_bytes - used),
            "available_bytes": host_available_bytes(self.meminfo),
            "reservations": state["reservations"],
            "waiters": state["waiters"],
            "observed_at": _timestamp(),
        }


# ---------------------------------------------------------------------------
# Launch planner
# ---------------------------------------------------------------------------


def _queue_rows(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MemoryGovernorError(f"cannot read queue {path}: {error}") from error
    if isinstance(payload, dict):
        for key in ("rows", "records", "queue"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise MemoryGovernorError(f"queue {path} is not a list of rows")
    rows: list[dict[str, object]] = []
    for value in payload:
        if isinstance(value, dict):
            rows.append(dict(value))
    return rows


def score_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    calibration: Calibration | None = None,
    budget_total: int | None = None,
    queue_dir: Path | None = None,
) -> list[dict[str, object]]:
    """Estimate every row, degrading to a whole-budget reservation on failure."""

    scored: list[dict[str, object]] = []
    for row in rows:
        raw_path = str(row.get("run_source_path", ""))
        source = Path(raw_path)
        if queue_dir is not None and not source.is_absolute():
            source = Path(queue_dir) / source
        record: dict[str, object] = {
            "batch_id": str(row.get("batch_id", "")),
            "workbook_id": str(row.get("workbook_id", "")),
            "run_source_path": str(source),
        }
        try:
            profile = probe_workbook(source)
        except MemoryGovernorError as error:
            record["probe_error"] = str(error)
            record["estimate_bytes"] = (
                int(budget_total) if budget_total else MIN_RESERVE_BYTES
            )
            record["unprobed"] = True
        else:
            record["profile"] = profile.to_record()
            record["seed_bytes"] = seed_peak_bytes(profile)
            record["estimate_bytes"] = estimate_peak_bytes(
                profile, calibration, budget_total=budget_total
            )
            record["unprobed"] = False
        scored.append(record)
    return scored


def _median(values: Sequence[int]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def lanes_for_disk(
    free_bytes: int | None,
    *,
    lane_cap: int = LANE_CAP,
    floor_bytes: int = LANE_DISK_FLOOR_BYTES,
    per_lane_bytes: int = LANE_DISK_BYTES,
) -> int:
    """Lanes a host's free disk can carry: ``min(cap, (free - floor) // 60 GiB)``.

    ``None`` means the caller did not measure the disk, so only the cap
    applies. Never returns less than one lane: a host that cannot hold one is
    the reaper's problem at runtime, not the planner's.
    """

    if free_bytes is None:
        return max(1, lane_cap)
    usable = max(0, int(free_bytes) - floor_bytes)
    return max(1, min(lane_cap, usable // per_lane_bytes))


def heaviest_fit_lanes(
    estimates: Sequence[int], *, budget_total: int, lane_cap: int
) -> int:
    """Largest ``n <= lane_cap`` whose ``n`` heaviest estimates fit the budget.

    Tried for ``lane_cap`` down to 1. With ``n`` lanes the worst moment is the
    ``n`` heaviest rows running together; if that fits, every other mix does.
    Always at least 1 (a single row that exceeds the budget still needs a
    lane; the admission gate clamps it to the whole budget at runtime).
    """

    heaviest = sorted((int(value) for value in estimates), reverse=True)
    for lanes in range(min(lane_cap, len(heaviest)), 1, -1):
        if sum(heaviest[:lanes]) <= budget_total:
            return lanes
    return 1


def plan_fleet(
    scored_rows: Sequence[Mapping[str, object]],
    *,
    vm_count: int,
    budget_total: int,
    lane_cap: int = LANE_CAP,
    disk_free_bytes: Sequence[int | None] | None = None,
) -> dict[str, object]:
    """Spread heavy rows across VMs and pick each VM's starting lane count.

    Rows are assigned longest-processing-time first within each batch, so the
    batches stay in separate queues while the monsters land on different hosts.
    Each VM's lanes = min(heaviest-N memory fit, disk-derived lanes, row count).
    """

    if vm_count < 1:
        raise MemoryGovernorError("vm_count must be positive")
    if budget_total <= 0:
        raise MemoryGovernorError("budget_total must be positive")
    if lane_cap < 1:
        raise MemoryGovernorError("lane_cap must be positive")
    if disk_free_bytes is not None and len(disk_free_bytes) != vm_count:
        raise MemoryGovernorError(
            "disk_free_bytes needs one entry per VM (%d given, %d VMs)"
            % (len(disk_free_bytes), vm_count)
        )

    batches: dict[str, list[Mapping[str, object]]] = {}
    for row in scored_rows:
        batches.setdefault(str(row.get("batch_id", "")), []).append(row)

    assignments: list[dict[str, object]] = [
        {"vm_index": index, "load_bytes": 0, "queues": {}} for index in range(vm_count)
    ]
    for batch_id in sorted(batches):
        ordered = sorted(
            batches[batch_id],
            key=lambda row: int(row.get("estimate_bytes", 0)),
            reverse=True,
        )
        for row in ordered:
            target = min(assignments, key=lambda slot: int(slot["load_bytes"]))
            target["load_bytes"] = int(target["load_bytes"]) + int(
                row.get("estimate_bytes", 0)
            )
            target["queues"].setdefault(batch_id, []).append(row)

    vms: list[dict[str, object]] = []
    for slot in assignments:
        estimates = [
            int(row.get("estimate_bytes", 0))
            for rows in slot["queues"].values()
            for row in rows
        ]
        median = _median(estimates)
        heaviest = sorted(estimates, reverse=True)[:2]
        pair_fits = (
            len(heaviest) > 1 and sum(heaviest) <= budget_total
        )
        memory_lanes = heaviest_fit_lanes(
            estimates, budget_total=budget_total, lane_cap=lane_cap
        )
        free = (
            disk_free_bytes[slot["vm_index"]]
            if disk_free_bytes is not None
            else None
        )
        disk_lanes = lanes_for_disk(free, lane_cap=lane_cap)
        lanes = max(1, min(memory_lanes, disk_lanes, max(1, len(estimates))))
        vms.append(
            {
                "vm_index": slot["vm_index"],
                "lanes": lanes,
                "memory_lanes": memory_lanes,
                "disk_lanes": disk_lanes,
                "disk_free_bytes": None if free is None else int(free),
                "row_count": len(estimates),
                "load_bytes": int(slot["load_bytes"]),
                "median_estimate_bytes": median,
                "max_estimate_bytes": max(estimates, default=0),
                "heaviest_pair_fits": pair_fits,
                "queues": {
                    batch_id: [
                        {
                            "workbook_id": str(row.get("workbook_id", "")),
                            "estimate_bytes": int(row.get("estimate_bytes", 0)),
                            "unprobed": bool(row.get("unprobed", False)),
                        }
                        for row in rows
                    ]
                    for batch_id, rows in sorted(slot["queues"].items())
                },
            }
        )
    return {
        "schema_version": PLAN_SCHEMA,
        "budget_total_bytes": budget_total,
        "lane_cap": lane_cap,
        "vm_count": vm_count,
        "row_count": len(scored_rows),
        "vms": vms,
        "planned_at": _timestamp(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _budget_from_args(args: argparse.Namespace) -> MemoryBudget:
    return MemoryBudget(
        args.budget_dir,
        host_id=args.host_id,
        total_bytes=args.host_bytes,
        reserve_bytes=args.reserve_bytes,
    )


def _budget_total_from_args(args: argparse.Namespace) -> int:
    if args.budget_total:
        return int(args.budget_total)
    capacity = args.host_bytes or host_total_bytes()
    if capacity is None:
        raise MemoryGovernorError(
            "cannot determine host memory; pass --host-bytes or --budget-total"
        )
    reserve = (
        int(args.reserve_bytes)
        if args.reserve_bytes is not None
        else reserve_bytes_for(int(capacity))
    )
    return int(capacity) - reserve


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-dir", default=str(DEFAULT_BUDGET_DIR))
    parser.add_argument("--host-id", default=None)
    parser.add_argument("--host-bytes", type=int, default=None)
    parser.add_argument("--reserve-bytes", type=int, default=None)
    parser.add_argument("--budget-total", type=int, default=None)
    parser.add_argument("--observations", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="print a workbook size profile")
    probe.add_argument("workbook")

    estimate = subparsers.add_parser(
        "estimate", help="print the predicted peak for a workbook"
    )
    estimate.add_argument("workbook")

    plan = subparsers.add_parser(
        "plan", help="score queue rows and choose lane counts per VM"
    )
    plan.add_argument("--queues", nargs="+", required=True)
    plan.add_argument("--vms", type=int, default=3)
    plan.add_argument("--lane-cap", type=int, default=LANE_CAP)
    plan.add_argument(
        "--disk-free-bytes",
        type=int,
        nargs="+",
        default=None,
        help=(
            "free bytes on each VM's work disk, one per VM in order; lanes = "
            "min(cap, (free - 120 GiB floor) // 60 GiB per lane)"
        ),
    )

    subparsers.add_parser("report", help="print the current budget state")

    hold = subparsers.add_parser(
        "hold",
        help=(
            "reserve BYTES for LANE_ID, run COMMAND, release on exit; used by "
            "scripts/batch/lane_fix.sh so a fix agent counts against the host "
            "budget like any other agent"
        ),
    )
    hold.add_argument("--lane-id", required=True)
    hold.add_argument("--bytes", type=int, required=True)
    hold.add_argument("--timeout-seconds", type=float, default=1800.0)
    # Not named "command": that is the subparser dest.
    hold.add_argument("held_command", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    if args.command == "hold":
        command = list(args.held_command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("hold needs a command after --")
        budget = _budget_from_args(args)
        try:
            lease = budget.acquire(
                args.lane_id, args.bytes, timeout_seconds=args.timeout_seconds
            )
        except (MemoryGovernorError, BudgetTimeout, OSError) as error:
            print(json.dumps({"hold": "refused", "error": str(error)}), flush=True)
            return 75
        try:
            completed = subprocess.run(command, check=False)
        finally:
            lease.release()
        return completed.returncode
    observations = Path(args.observations) if args.observations else None
    calibration = load_calibration(observations) if observations else None

    if args.command == "probe":
        print(json.dumps(probe_workbook(args.workbook).to_record(), sort_keys=True))
        return 0

    if args.command == "estimate":
        profile = probe_workbook(args.workbook)
        budget_total = _budget_total_from_args(args)
        print(
            json.dumps(
                {
                    "profile": profile.to_record(),
                    "seed_bytes": seed_peak_bytes(profile),
                    "calibration": (calibration or Calibration()).to_record(),
                    "estimate_bytes": estimate_peak_bytes(
                        profile, calibration, budget_total=budget_total
                    ),
                    "budget_total_bytes": budget_total,
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "plan":
        budget_total = _budget_total_from_args(args)
        # The worker rejects a lane count above the fleet cap, so a plan that
        # asked for more would be unlaunchable.
        lane_cap = max(1, min(int(args.lane_cap), LANE_CAP))
        scored: list[dict[str, object]] = []
        for queue in args.queues:
            queue_path = Path(queue)
            scored.extend(
                score_rows(
                    _queue_rows(queue_path),
                    calibration=calibration,
                    budget_total=budget_total,
                    queue_dir=queue_path.parent,
                )
            )
        print(
            json.dumps(
                plan_fleet(
                    scored,
                    vm_count=args.vms,
                    budget_total=budget_total,
                    lane_cap=lane_cap,
                    disk_free_bytes=args.disk_free_bytes,
                ),
                sort_keys=True,
            )
        )
        return 0

    print(json.dumps(_budget_from_args(args).snapshot(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
