#!/usr/bin/env python3
"""Fail-closed conversion and execution primitives for spreadsheet batches."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

import openpyxl


READY_SCHEMA = "batch-ready/v1"
LEDGER_SCHEMA = "batch-terminal-ledger/v1"
SUPPORTED_INPUTS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb"})
CGROUP_ROOT = Path("/sys/fs/cgroup")
# No throttle band: MemoryHigh equals MemoryMax. These hosts have no swap and
# the payload's footprint is anonymous memory (a Python object graph), so
# nothing in the band is reclaimable: at 0.85 the fleet's first rows spent
# hours in reclaim (hundreds of thousands of memory.high events), and at 0.95
# the 0568 smoke sat in the band for six minutes (9.5k events) on its way to
# the same OOM kill. A child that will not fit should die in minutes so the
# agent sees ``cgroup_oom`` and shrinks the cone, the path the old 13.6 GB cap
# forced but hours sooner. Kept as a knob so a swap-backed host could bring
# the band back.
MEMORY_HIGH_FRACTION = 1.0
# Sampling memory is a /proc scan or a cgroup file read. The old 20 Hz rate
# across a six-hour agent burned real CPU on a box already under pressure.
DEFAULT_MEMORY_POLL_SECONDS = 0.05
# The wait loop sleeps min(memory_poll, this). Production polls memory every
# five seconds, so this is the effective wake-up rate for a six-hour agent;
# 0.01 meant 100 wake-ups a second for nothing. Tests pass a smaller memory
# poll and keep their fast sampling through the min().
LIVENESS_POLL_SECONDS = 0.25
# A generation agent that has stopped writing its stream and whose scope is
# burning no CPU is not thinking and not waiting on a child; it is hung
# (a dropped connection, or the OOM loop below before the fix). Left alone it
# holds the lane until the wall-clock timeout, six hours on the fleet. Both
# signals are required: an agent blocked on a long xl_*.py child shows a flat
# stream but rising cgroup CPU, and must not be killed.
DEFAULT_STALL_SECONDS = 1800.0
STALL_CPU_BUDGET_USEC = 5_000_000
# memory.events ``oom`` climbing while ``oom_kill`` stays at zero is the
# immunity-loop signature: the kernel finds no killable task and retries the
# charge forever. _oom_killable_command prevents it; this catches a regression
# in a minute instead of a full timeout.
OOM_LOOP_SECONDS = 60.0
# oom_score_adj is inherited across fork and exec. The fleet protects sshd with
# OOMScoreAdjust=-1000, and every lane is launched from an SSH session, so
# without an explicit reset the agent, the worker, and every xl_*.py child
# would all be immune. Inside a MemoryMax scope that is the worst possible
# state: the kernel finds no killable task, prints a full OOM report, retries
# the charge, and loops -- the report flood filled three root disks in a day.
# Raising the score back to 0 is unprivileged; only lowering it needs
# CAP_SYS_RESOURCE.
CHILD_OOM_SCORE_ADJ = 0
OOM_SCORE_ADJ_PATH = "/proc/self/oom_score_adj"
# OOMPolicy= on scope units arrived in systemd 253; unknown properties are
# fatal to systemd-run, so it must be probed rather than assumed.
OOM_POLICY_MIN_SYSTEMD_VERSION = 253
TERMINAL_STATUSES = frozenset(
    {
        "already_completed",
        "blocked",
        "completed",
        "conversion_failed",
        "failed",
        "invalid_ready",
        "missing_ready",
        "orchestrator_error",
        "source_missing",
        "timed_out",
    }
)


class ReadyRecordError(ValueError):
    """A ready record is malformed or no longer matches its workbook."""


class ConversionTimeout(TimeoutError):
    """A converter and its process group exceeded their deadline."""


class LedgerError(ValueError):
    """Expected-workbook accounting is ambiguous or incomplete."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, contents: bytes) -> None:
    """Write a complete file and expose it with one same-directory rename."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, contents: str) -> None:
    atomic_write_bytes(path, contents.encode("utf-8"))


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write_bytes(path, _canonical_json(payload))


def validate_xlsx(path: Path) -> None:
    """Require an intact OOXML ZIP that openpyxl can parse."""

    if not path.is_file() or not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not an OOXML ZIP package")
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"{path} has a corrupt ZIP member: {bad_member}")
        names = set(archive.namelist())
        for required in ("[Content_Types].xml", "xl/workbook.xml"):
            if required not in names:
                raise ValueError(f"{path} is missing {required}")
    # A file handle lets callers validate an atomically staged ``.tmp`` file
    # without making it look like a queueable XLSX pathname.
    with path.open("rb") as handle:
        workbook = openpyxl.load_workbook(
            handle,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        workbook.close()


def _canonical_id(value: object) -> str:
    workbook_id = unicodedata.normalize("NFC", str(value).strip())
    if (
        not workbook_id
        or workbook_id in {".", ".."}
        or Path(workbook_id).name != workbook_id
        or "\x00" in workbook_id
    ):
        raise ValueError(f"unsafe workbook ID: {value!r}")
    return workbook_id


def _record_binding(payload: Mapping[str, object]) -> str:
    bound = {key: value for key, value in payload.items() if key != "record_sha256"}
    return _sha256_bytes(_canonical_json(bound))


@dataclass(frozen=True)
class ReadyRecord:
    schema_version: str
    workbook_id: str
    source_file: str
    source_sha256: str
    xlsx_file: str
    xlsx_sha256: str
    xlsx_size: int
    conversion_engine: str
    created_at: str
    record_sha256: str

    @property
    def ready_filename(self) -> str:
        return f"{self.workbook_id}.ready.json"

    @classmethod
    def create(
        cls,
        *,
        workbook_id: str,
        source_file: str,
        source_sha256: str,
        xlsx_file: str,
        xlsx_sha256: str,
        xlsx_size: int,
        conversion_engine: str,
    ) -> ReadyRecord:
        payload: dict[str, object] = {
            "schema_version": READY_SCHEMA,
            "workbook_id": _canonical_id(workbook_id),
            "source_file": source_file,
            "source_sha256": source_sha256,
            "xlsx_file": xlsx_file,
            "xlsx_sha256": xlsx_sha256,
            "xlsx_size": xlsx_size,
            "conversion_engine": conversion_engine,
            "created_at": _timestamp(),
        }
        payload["record_sha256"] = _record_binding(payload)
        return cls(**payload)  # type: ignore[arg-type]

    @classmethod
    def read(cls, path: Path) -> ReadyRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = cls(**payload)
        except (OSError, TypeError, json.JSONDecodeError) as error:
            raise ReadyRecordError(f"cannot read ready record {path}: {error}") from error
        record.validate_record(path)
        return record

    def validate_record(self, path: Path | None = None) -> None:
        if self.schema_version != READY_SCHEMA:
            raise ReadyRecordError(
                f"unsupported ready schema {self.schema_version!r}"
            )
        string_fields = {
            "workbook_id": self.workbook_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "xlsx_file": self.xlsx_file,
            "xlsx_sha256": self.xlsx_sha256,
            "conversion_engine": self.conversion_engine,
            "created_at": self.created_at,
            "record_sha256": self.record_sha256,
        }
        if any(
            not isinstance(value, str)
            for value in string_fields.values()
        ):
            raise ReadyRecordError("ready record fields have invalid types")
        if (
            not isinstance(self.xlsx_size, int)
            or isinstance(self.xlsx_size, bool)
            or self.xlsx_size < 0
        ):
            raise ReadyRecordError("ready record has an invalid XLSX size")
        for name in ("source_sha256", "xlsx_sha256", "record_sha256"):
            value = string_fields[name]
            if len(value) != 64 or any(
                character not in "0123456789abcdef"
                for character in value
            ):
                raise ReadyRecordError(
                    f"ready record has an invalid {name}"
                )
        try:
            canonical_id = _canonical_id(self.workbook_id)
        except ValueError as error:
            raise ReadyRecordError(str(error)) from error
        if canonical_id != self.workbook_id:
            raise ReadyRecordError("ready record uses a non-canonical workbook ID")
        if path is not None and path.name != self.ready_filename:
            raise ReadyRecordError(
                f"ready filename {path.name!r} does not bind ID {self.workbook_id!r}"
            )
        if Path(self.xlsx_file).name != self.xlsx_file:
            raise ReadyRecordError("ready record XLSX path must be a file name")
        if self.xlsx_file != f"{self.workbook_id}.xlsx":
            raise ReadyRecordError("ready record XLSX file does not bind workbook ID")
        if self.record_sha256 != _record_binding(asdict(self)):
            raise ReadyRecordError("ready record binding hash does not match")

    def validate_workbook(self, published_dir: Path) -> Path:
        self.validate_record()
        workbook = published_dir / self.xlsx_file
        try:
            size = workbook.stat().st_size
        except OSError as error:
            raise ReadyRecordError(
                f"ready workbook {workbook} is unavailable: {error}"
            ) from error
        if size != self.xlsx_size or sha256_file(workbook) != self.xlsx_sha256:
            raise ReadyRecordError(
                f"ready workbook {workbook} no longer matches its record"
            )
        try:
            validate_xlsx(workbook)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise ReadyRecordError(
                f"ready workbook {workbook} is not readable OOXML: {error}"
            ) from error
        return workbook


def _process_group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: float,
) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        process.poll()
        if not _process_group_alive(process_group):
            break
        time.sleep(0.01)
    if _process_group_alive(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=max(grace_seconds, 0.1))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _process_group_rss_bytes(process_group: int) -> int | None:
    """Return Linux process-group RSS, or None when the host cannot report it."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    page_size = os.sysconf("SC_PAGE_SIZE")
    total = 0
    observed = False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            suffix = stat_text[stat_text.rfind(")") + 2:].split()
            if len(suffix) < 3 or int(suffix[2]) != process_group:
                continue
            statm = (entry / "statm").read_text(encoding="utf-8").split()
            total += int(statm[1]) * page_size
            observed = True
        except (FileNotFoundError, PermissionError, ValueError, OSError):
            continue
    return total if observed else None


def _user_cgroup_base(cgroup_root: Path = CGROUP_ROOT) -> Path | None:
    """Return the delegated user-manager cgroup, or None when unavailable."""

    uid = os.getuid()
    base = (
        cgroup_root
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
    )
    return base if base.is_dir() else None


def memory_scope_supported(cgroup_root: Path = CGROUP_ROOT) -> bool:
    """True when a transient user scope can carry a real memory limit."""

    base = _user_cgroup_base(cgroup_root)
    if base is None or shutil.which("systemd-run") is None:
        return False
    try:
        controllers = (base / "cgroup.controllers").read_text(encoding="utf-8")
    except OSError:
        return False
    return "memory" in controllers.split()


def _oom_killable_command(
    command: Sequence[str], adjust: int = CHILD_OOM_SCORE_ADJ
) -> list[str]:
    """Wrap ``command`` so it runs with ``oom_score_adj`` reset to ``adjust``.

    A ``/bin/sh`` prologue writes the value into ``/proc/self/oom_score_adj``
    and then ``exec``s the real command in place, so the pid, the process
    group, and the session leader are unchanged. The write is guarded on
    writability so a host without ``/proc`` (macOS) runs the command
    untouched with nothing on stderr. ``shift`` follows the guard so a false
    ``[ -w ]`` cannot leak its status into ``$?``.

    This is the outermost wrapper by design: ``systemd-run --scope`` execs the
    payload in place, so one reset here covers the scope, the agent, and every
    descendant they fork. One visible side effect: a missing binary is now a
    shell exit 127 (126 under macOS ``sh``) with ``status="failed"`` instead
    of ``FileNotFoundError`` with ``status="orchestrator_error"``.

    ``preexec_fn`` is deliberately not used; ``run_ready_batch`` reaches the
    ``Popen`` through a ``ThreadPoolExecutor``, and running Python between
    fork and exec in a threaded parent can deadlock.
    """

    if not command:
        raise ValueError("command must not be empty")
    prologue = (
        f"[ -w {OOM_SCORE_ADJ_PATH} ] && "
        f'printf %s "$1" > {OOM_SCORE_ADJ_PATH} 2>/dev/null; '
        'shift; exec "$@"'
    )
    return [
        "/bin/sh",
        "-c",
        prologue,
        "harbor-oom-adjust",
        str(int(adjust)),
        *(str(item) for item in command),
    ]


def read_oom_score_adj(pid: int) -> int | None:
    """Return a live process's ``oom_score_adj``, or None off-Linux/after exit."""

    try:
        raw = Path(f"/proc/{int(pid)}/oom_score_adj").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


_OOM_POLICY_SUPPORTED: bool | None = None


def systemd_version() -> int | None:
    """Parse the major version from ``systemctl --version``, or None."""

    binary = shutil.which("systemctl")
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"\s*systemd\s+(\d+)", completed.stdout or "")
    if match is None:
        return None
    return int(match.group(1))


def oom_policy_supported() -> bool:
    """True when ``OOMPolicy=`` is a valid scope property on this host.

    Cached for the process lifetime; the answer cannot change under a
    running worker, and the probe forks ``systemctl``.
    """

    global _OOM_POLICY_SUPPORTED
    if _OOM_POLICY_SUPPORTED is None:
        version = systemd_version()
        _OOM_POLICY_SUPPORTED = (
            version is not None and version >= OOM_POLICY_MIN_SYSTEMD_VERSION
        )
    return _OOM_POLICY_SUPPORTED


def _scope_cgroup_dir(scope: str, cgroup_root: Path = CGROUP_ROOT) -> Path | None:
    """Locate a live scope's cgroup directory.

    systemd places a transient user scope under ``app.slice`` on current
    releases and directly under the user manager on older ones, so both are
    checked. The directory disappears when the scope exits, which is why the
    caller samples it while the command is still running.
    """

    base = _user_cgroup_base(cgroup_root)
    if base is None:
        return None
    unit = scope if scope.endswith(".scope") else f"{scope}.scope"
    for candidate in (base / "app.slice" / unit, base / unit):
        if candidate.is_dir():
            return candidate
    return None


def cgroup_peak_bytes(scope: str, cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """Return a scope's high-water memory from cgroup v2 ``memory.peak``.

    This counts every descendant regardless of session or process group and
    does not double-count shared pages, so it is strictly better than summing
    ``statm`` across a process group. ``memory.current`` is the fallback on
    kernels without ``memory.peak``.
    """

    directory = _scope_cgroup_dir(scope, cgroup_root)
    if directory is None:
        return None
    for name in ("memory.peak", "memory.current"):
        try:
            raw = (directory / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return None


def cgroup_anon_bytes(scope: str, cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """Return a scope's anonymous memory from cgroup v2 ``memory.stat``.

    ``memory.peak`` and ``memory.current`` charge page cache to the scope, so
    a row that streams a multi-gigabyte AST through the cache reads as if it
    used the whole ceiling whatever its real footprint. Calibration must not
    learn from that: it would push the scale to its maximum and serialize
    lanes that fit comfortably. ``anon`` is the number that decides whether a
    row fits next to a sibling on a swapless host.
    """

    directory = _scope_cgroup_dir(scope, cgroup_root)
    if directory is None:
        return None
    try:
        raw = (directory / "memory.stat").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name.strip() == "anon":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def cgroup_cpu_usec(scope: str, cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """Return a scope's cumulative CPU time from cgroup v2 ``cpu.stat``.

    ``usage_usec`` is exposed on every v2 cgroup whether or not the cpu
    controller is delegated, which makes it the cheapest liveness signal for
    a scope whose stream has gone quiet.
    """

    directory = _scope_cgroup_dir(scope, cgroup_root)
    if directory is None:
        return None
    try:
        raw = (directory / "cpu.stat").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name.strip() == "usage_usec":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def cgroup_events(scope: str, cgroup_root: Path = CGROUP_ROOT) -> dict[str, int]:
    """Read a scope's ``memory.events``.

    ``high`` counts reclaim throttling, which flags an estimate that is running
    low before it becomes an ``oom_kill``.
    """

    directory = _scope_cgroup_dir(scope, cgroup_root)
    if directory is None:
        return {}
    try:
        raw = (directory / "memory.events").read_text(encoding="utf-8")
    except OSError:
        return {}
    events: dict[str, int] = {}
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        try:
            events[name.strip()] = int(value.strip())
        except ValueError:
            continue
    return events


def scope_unit_name(*parts: object) -> str:
    """Build a systemd unit name that is safe to pass to systemd-run.

    Truncating the joined string would cut the trailing attempt index first,
    so two attempts of one row could collide on a single unit name -- which
    either fails to launch against a lingering scope or reads a stale peak
    from the wrong cgroup. The leading parts are shortened and a digest
    carries the rest, so the final part always survives verbatim.
    """

    cleaned = [
        re.sub(r"[^A-Za-z0-9_-]", "-", str(part)).strip("-")
        for part in parts
        if str(part).strip()
    ]
    cleaned = [piece for piece in cleaned if piece]
    if not cleaned:
        return "harbor-attempt"
    tail = cleaned[-1][:32]
    head = "-".join(cleaned[:-1])
    if not head:
        return f"harbor-{tail}"
    digest = hashlib.sha256(head.encode("utf-8")).hexdigest()[:10]
    return f"harbor-{head[:96]}-{digest}-{tail}"


def _scope_command(
    scope: str,
    command: Sequence[str],
    *,
    memory_max_bytes: int | None,
    memory_high_bytes: int | None = None,
    oom_policy: bool | None = None,
) -> list[str]:
    """Wrap a command in a transient user scope carrying a memory ceiling.

    ``oom_policy`` adds ``OOMPolicy=kill`` so that when the kernel OOM-kills
    one task in the scope, systemd takes the whole scope down rather than
    leaving orphaned siblings. It is defense in depth, not the OOM fix: the
    policy only acts *after* the kernel has picked a victim, and the incident
    it guards against was one where the kernel could pick nobody. That is
    handled by ``_oom_killable_command``. ``None`` means probe the host.
    """

    wrapped = [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        f"--unit={scope}",
        "--property=MemorySwapMax=0",
    ]
    if oom_policy is None:
        oom_policy = oom_policy_supported()
    if oom_policy:
        wrapped.append("--property=OOMPolicy=kill")
    if memory_max_bytes is not None and memory_max_bytes > 0:
        high = (
            memory_high_bytes
            if memory_high_bytes is not None and memory_high_bytes > 0
            else int(memory_max_bytes * MEMORY_HIGH_FRACTION)
        )
        wrapped.append(f"--property=MemoryMax={int(memory_max_bytes)}")
        wrapped.append(f"--property=MemoryHigh={min(int(high), int(memory_max_bytes))}")
    wrapped.extend(str(item) for item in command)
    return wrapped


def _run_in_process_group(
    command: Sequence[str],
    *,
    stdout: BinaryIO,
    timeout_seconds: float | None,
    grace_seconds: float,
    memory_limit_bytes: int | None = None,
    metrics: dict[str, object] | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    memory_probe: Callable[[int], int | None] | None = None,
    memory_events_probe: Callable[[], Mapping[str, int]] | None = None,
    memory_poll_seconds: float = DEFAULT_MEMORY_POLL_SECONDS,
    memory_observe_only: bool = False,
    record_oom_score_adj: bool = False,
    cpu_probe: Callable[[], int | None] | None = None,
    stall_seconds: float | None = None,
    oom_loop_seconds: float | None = None,
) -> int:
    if memory_limit_bytes is not None and memory_limit_bytes <= 0:
        raise ValueError("memory_limit_bytes must be positive")
    if memory_poll_seconds <= 0:
        raise ValueError("memory_poll_seconds must be positive")
    if stall_seconds is not None and stall_seconds <= 0:
        raise ValueError("stall_seconds must be positive")
    # Resolved per call so the module constant stays the seam for tests.
    if oom_loop_seconds is None:
        oom_loop_seconds = OOM_LOOP_SECONDS
    if not command:
        raise ValueError("command must not be empty")
    # Resolved per call, not bound as a default, so the module attribute stays
    # the single seam for tests and for hosts without cgroup delegation.
    probe = memory_probe or _process_group_rss_bytes
    # Observation is what populates peak_rss_bytes for calibration; enforcement
    # is separate so a kernel-capped scope can be measured without this loop
    # racing the kernel to kill the same process group.
    tracking = memory_limit_bytes is not None or memory_observe_only
    enforcing = memory_limit_bytes is not None and not memory_observe_only
    started_at = time.monotonic()
    # The wrapper is applied here and only here, and ``command`` itself is left
    # alone: it is interpolated into the error strings below, which must keep
    # naming the real payload rather than /bin/sh.
    process = subprocess.Popen(
        _oom_killable_command(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    peak_rss = 0
    last_observed = 0.0

    def stream_size() -> int | None:
        try:
            return os.fstat(stdout.fileno()).st_size
        except (OSError, ValueError, AttributeError):
            return None

    # Stall window: reset whenever the stream grows or the scope spends real
    # CPU. Without a CPU probe the loop cannot tell a hung agent from one
    # waiting on a child, so it never stall-kills.
    window_started = started_at
    window_stream = stream_size()
    window_cpu = cpu_probe() if cpu_probe is not None else None
    oom_loop_started: float | None = None
    last_oom_count = 0

    def _kill(reason: str, message: str) -> None:
        if metrics is not None:
            metrics["limit_reason"] = reason
        _terminate_process_group(process, grace_seconds=grace_seconds)
        raise ConversionTimeout(f"{message}: {command[0]}")

    def observe_stall(now: float) -> None:
        nonlocal window_started, window_stream, window_cpu
        if stall_seconds is None or cpu_probe is None:
            return
        size = stream_size()
        cpu = cpu_probe()
        progressed = False
        if size is not None and (window_stream is None or size > window_stream):
            progressed = True
        if cpu is None:
            # Blind on CPU: a flat stream alone cannot distinguish a hang from
            # an agent waiting on a child, so refuse to guess.
            progressed = True
        elif window_cpu is None or cpu - window_cpu > STALL_CPU_BUDGET_USEC:
            progressed = True
        if progressed:
            window_started = now
            window_stream = size
            window_cpu = cpu
            return
        if now - window_started >= stall_seconds:
            _kill(
                "stall",
                f"no stream output and no CPU for {int(stall_seconds)} seconds",
            )

    def observe_oom_loop(now: float, events: Mapping[str, int]) -> None:
        nonlocal oom_loop_started, last_oom_count
        oom = int(events.get("oom", 0) or 0)
        killed = int(events.get("oom_kill", 0) or 0)
        if killed == 0 and oom > last_oom_count:
            if oom_loop_started is None:
                oom_loop_started = now
            elif now - oom_loop_started >= oom_loop_seconds:
                last_oom_count = oom
                _kill(
                    "oom_loop",
                    "scope is OOM-looping with no killable task",
                )
        else:
            oom_loop_started = None
        last_oom_count = oom

    def observe_oom_score_adj() -> None:
        # The first sample can land before the shell prologue has written, so
        # keep the running maximum: the value only ever moves from the
        # inherited -1000 up to 0, and the maximum converges on the truth
        # instead of reporting a phantom failure from the race.
        if not record_oom_score_adj or metrics is None:
            return
        adjust = read_oom_score_adj(process.pid)
        if adjust is None:
            return
        previous = metrics.get("child_oom_score_adj")
        if isinstance(previous, int) and not isinstance(previous, bool):
            adjust = max(previous, adjust)
        metrics["child_oom_score_adj"] = adjust

    def observe_memory(*, force: bool = False) -> None:
        nonlocal peak_rss, last_observed
        if not tracking:
            return
        now = time.monotonic()
        # Self-throttling keeps both the main and linger loops honest without
        # tying the memory sample rate to the liveness poll rate.
        if not force and last_observed and now - last_observed < memory_poll_seconds:
            return
        last_observed = now
        observe_oom_score_adj()
        if memory_events_probe is not None:
            events = memory_events_probe()
            if events and metrics is not None:
                metrics["memory_events"] = dict(events)
            if events:
                observe_oom_loop(now, events)
        observe_stall(now)
        rss = probe(process.pid)
        if rss is None:
            return
        peak_rss = max(peak_rss, rss)
        if metrics is not None:
            metrics["peak_rss_bytes"] = peak_rss
        if enforcing and rss > memory_limit_bytes:
            if metrics is not None:
                metrics["limit_reason"] = "process_group_rss"
            _terminate_process_group(process, grace_seconds=grace_seconds)
            raise ConversionTimeout(
                f"process group exceeded {memory_limit_bytes} RSS bytes: "
                f"{command[0]}"
            )

    if not tracking:
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            if metrics is not None:
                metrics["limit_reason"] = "wall_time"
            _terminate_process_group(process, grace_seconds=grace_seconds)
            raise ConversionTimeout(
                f"command exceeded {timeout_seconds} seconds: {command[0]}"
            ) from error
    else:
        deadline = (
            started_at + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        liveness_poll = min(memory_poll_seconds, LIVENESS_POLL_SECONDS)
        while True:
            observe_memory()
            return_code = process.poll()
            if return_code is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                if metrics is not None:
                    metrics["limit_reason"] = "wall_time"
                _terminate_process_group(process, grace_seconds=grace_seconds)
                raise ConversionTimeout(
                    f"command exceeded {timeout_seconds} seconds: {command[0]}"
                )
            time.sleep(liveness_poll)
        # memory.peak is cumulative, so an interval sample already holds the
        # run's high-water mark. This last read only catches a scope that is
        # still alive because systemd-run has not reaped it yet.
        observe_memory(force=True)

    # A launcher can exit while a descendant keeps converting. Treat that as
    # part of the same deadline, and never expose output from a lingering group.
    if _process_group_alive(process.pid):
        linger_seconds = (
            max(0.0, timeout_seconds - (time.monotonic() - started_at))
            if timeout_seconds is not None
            else grace_seconds
        )
        linger_deadline = time.monotonic() + linger_seconds
        while (
            _process_group_alive(process.pid)
            and time.monotonic() < linger_deadline
        ):
            observe_memory()
            time.sleep(LIVENESS_POLL_SECONDS)
        if _process_group_alive(process.pid):
            _terminate_process_group(process, grace_seconds=grace_seconds)
            raise ConversionTimeout(f"descendants outlived command: {command[0]}")
    return return_code


def _atomic_publish_validated_xlsx(
    candidate: Path, destination: Path, *, candidate_sha256: str
) -> None:
    """Publish a validated candidate atomically.

    ``candidate_sha256`` is the digest taken when the candidate was validated,
    not re-read here. Besides saving a full pass over the file, it closes a
    hole: re-reading the candidate at copy time would agree with a copy made
    *after* a tampering write, whereas comparing against the validated digest
    catches a change at any point after validation.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(candidate, temporary)
        validate_xlsx(temporary)
        if sha256_file(temporary) != candidate_sha256:
            raise ValueError("XLSX changed while preparing atomic publication")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _convert_and_publish_locked(
    source: Path,
    *,
    workbook_id: str,
    private_root: Path,
    published_dir: Path,
    ready_dir: Path,
    libreoffice_binary: str = "libreoffice",
    timeout_seconds: float = 600,
    terminate_grace_seconds: float = 2,
) -> ReadyRecord:
    """Convert privately, validate twice, then publish XLSX and ready record."""

    source = source.resolve(strict=True)
    workbook_id = _canonical_id(workbook_id)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_INPUTS:
        raise ValueError(f"unsupported spreadsheet extension: {source.suffix}")

    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_root, 0o700)
    published_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)
    private_resolved = private_root.resolve()
    for public_root in (published_dir, ready_dir):
        public_resolved = public_root.resolve()
        if (
            private_resolved == public_resolved
            or private_resolved.is_relative_to(public_resolved)
            or public_resolved.is_relative_to(private_resolved)
        ):
            raise ValueError("private conversion root must not be publicly visible")

    source_hash = sha256_file(source)
    with tempfile.TemporaryDirectory(
        prefix=f"{workbook_id}.", dir=private_root
    ) as temporary_name:
        private_dir = Path(temporary_name)
        os.chmod(private_dir, 0o700)
        candidate = private_dir / f"{workbook_id}.xlsx"
        if suffix == ".xlsx":
            shutil.copy2(source, candidate)
            conversion_engine = "native_copy"
        else:
            profile = private_dir / "libreoffice-profile"
            profile.mkdir(mode=0o700)
            log_path = private_dir / "libreoffice.log"
            command = [
                libreoffice_binary,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(private_dir),
                str(source),
            ]
            with log_path.open("wb") as log:
                return_code = _run_in_process_group(
                    command,
                    stdout=log,
                    timeout_seconds=timeout_seconds,
                    grace_seconds=terminate_grace_seconds,
                )
            generated = private_dir / f"{source.stem}.xlsx"
            if return_code != 0 or not generated.is_file():
                output = log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(
                    f"LibreOffice conversion failed ({return_code}): {output[-1000:]}"
                )
            if generated != candidate:
                os.replace(generated, candidate)
            conversion_engine = Path(libreoffice_binary).name

        validate_xlsx(candidate)
        candidate_hash = sha256_file(candidate)
        if sha256_file(source) != source_hash:
            raise ValueError("source changed during private conversion")
        if suffix == ".xlsx" and candidate_hash != source_hash:
            raise ValueError("native XLSX copy does not match its source hash")
        destination = published_dir / f"{workbook_id}.xlsx"
        ready_path = ready_dir / f"{workbook_id}.ready.json"
        destination_exists = destination.exists() or destination.is_symlink()
        ready_exists = ready_path.exists() or ready_path.is_symlink()
        if destination_exists or ready_exists:
            if destination.is_file() and ready_path.is_file():
                existing = ReadyRecord.read(ready_path)
                existing.validate_workbook(published_dir)
                if (
                    existing.source_sha256 == source_hash
                    and existing.xlsx_sha256 == candidate_hash
                ):
                    return existing
            if (
                destination.is_file()
                and not destination.is_symlink()
                and not ready_exists
            ):
                validate_xlsx(destination)
                if sha256_file(destination) == candidate_hash:
                    record = ReadyRecord.create(
                        workbook_id=workbook_id,
                        source_file=source.name,
                        source_sha256=source_hash,
                        xlsx_file=destination.name,
                        xlsx_sha256=candidate_hash,
                        xlsx_size=destination.stat().st_size,
                        conversion_engine=conversion_engine,
                    )
                    atomic_write_json(ready_path, asdict(record))
                    return record
            raise FileExistsError(
                "refusing to overwrite an existing workbook or ready record"
            )
        _atomic_publish_validated_xlsx(
            candidate, destination, candidate_sha256=candidate_hash
        )
        if sha256_file(destination) != candidate_hash:
            raise ValueError("published XLSX does not match validated private output")

        record = ReadyRecord.create(
            workbook_id=workbook_id,
            source_file=source.name,
            source_sha256=source_hash,
            xlsx_file=destination.name,
            xlsx_sha256=candidate_hash,
            xlsx_size=destination.stat().st_size,
            conversion_engine=conversion_engine,
        )
        atomic_write_json(ready_path, asdict(record))
        return record


def convert_and_publish(
    source: Path,
    *,
    workbook_id: str,
    private_root: Path,
    published_dir: Path,
    ready_dir: Path,
    libreoffice_binary: str = "libreoffice",
    timeout_seconds: float = 600,
    terminate_grace_seconds: float = 2,
) -> ReadyRecord:
    """Serialize one ID so retries cannot replace already-ready bytes."""
    workbook_id = _canonical_id(workbook_id)
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_root, 0o700)
    lock_root = private_root / ".locks"
    lock_root.mkdir(exist_ok=True, mode=0o700)
    lock_path = lock_root / f"{workbook_id}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _convert_and_publish_locked(
            source,
            workbook_id=workbook_id,
            private_root=private_root,
            published_dir=published_dir,
            ready_dir=ready_dir,
            libreoffice_binary=libreoffice_binary,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
        )
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def discover_ready_records(
    ready_dir: Path,
    published_dir: Path,
) -> list[ReadyRecord]:
    """Return verified work items discovered only from atomic ready records."""

    records: list[ReadyRecord] = []
    seen_ids: set[str] = set()
    if not ready_dir.is_dir():
        return records
    for path in sorted(ready_dir.glob("*.ready.json")):
        record = ReadyRecord.read(path)
        record.validate_workbook(published_dir)
        if record.workbook_id in seen_ids:
            raise ReadyRecordError(
                f"duplicate ready record for {record.workbook_id}"
            )
        seen_ids.add(record.workbook_id)
        records.append(record)
    return records


def _same_resolved_path(first: Path, second: Path) -> bool:
    try:
        if first.exists() and second.exists() and os.path.samefile(first, second):
            return True
    except OSError:
        pass
    return first.resolve(strict=False) == second.resolve(strict=False)


def publish_diagnostic_snapshot(
    source: Path,
    destination: Path,
    *,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    copy_tree: Callable[..., object] = shutil.copytree,
) -> str:
    """Snapshot diagnostics before exposing a new immutable destination."""

    if _same_resolved_path(source, destination):
        return "same_path"
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.", suffix=".snapshot", dir=destination.parent
        )
    )
    # mkdtemp creates the root, while copytree requires its target not to exist.
    temporary.rmdir()
    try:
        if source.is_dir():
            copy_tree(source, temporary, symlinks=True)
        else:
            copy_file(source, temporary)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(
                f"diagnostic snapshots are immutable: {destination}"
            )
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        return "published"
    finally:
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            temporary.unlink(missing_ok=True)


def allocate_attempt_directory(state_dir: Path, workbook_id: str) -> Path:
    """Reserve the next immutable attempt directory without truncating retries."""

    workbook_id = _canonical_id(workbook_id)
    attempts = state_dir / "attempts" / workbook_id
    attempts.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        candidate = attempts / f"attempt-{number:04d}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"attempt limit reached for {workbook_id}")


def _iter_stream_events(stream: str) -> Iterable[dict[str, Any]]:
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


_TOOL_PATH_KEYS = ("path", "file_path", "relativeWorkspacePath", "target_file")


def extract_tool_commands(stream: str) -> list[dict[str, Any]]:
    """Shell commands and file writes the agent performed, from the stream.

    ``stream-json`` emits one ``tool_call`` event per start and one per
    completion, keyed by ``call_id``; the tool kind is the single key of
    ``tool_call`` (``shellToolCall``, ``writeToolCall``, ``editToolCall``,
    ``deleteToolCall``, ...). Shell calls carry ``args.command`` and, when
    completed, ``result.success.exitCode``. This is the controller's own
    record of what recovery ran, written into recovery-evidence.json, so
    a.md no longer has to be the only source of the command history and the
    verifier can check a.md against it. Unknown shapes are skipped, never
    raised on; field additions are expected.
    """

    by_call: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anonymous = 0
    for event in _iter_stream_events(stream):
        if event.get("type") != "tool_call":
            continue
        tool_call = event.get("tool_call")
        if not isinstance(tool_call, Mapping) or not tool_call:
            continue
        kind, body = next(iter(tool_call.items()))
        if not isinstance(body, Mapping):
            continue
        args = body.get("args") if isinstance(body.get("args"), Mapping) else {}
        call_id = str(event.get("call_id") or "")
        if not call_id:
            anonymous += 1
            call_id = f"anonymous-{anonymous}"
        record = by_call.get(call_id)
        if record is None:
            record = {"call_id": call_id, "kind": str(kind), "subtype": None}
            by_call[call_id] = record
            order.append(call_id)
        subtype = str(event.get("subtype") or "")
        if subtype:
            record["subtype"] = subtype
        if kind == "shellToolCall":
            command = args.get("command")
            if isinstance(command, str):
                record["command"] = command
            cwd = args.get("workingDirectory") or args.get("cwd")
            if isinstance(cwd, str):
                record["cwd"] = cwd
        else:
            for key in _TOOL_PATH_KEYS:
                if isinstance(args.get(key), str):
                    record["path"] = args[key]
                    break
        result = body.get("result")
        if isinstance(result, Mapping):
            success = result.get("success")
            if isinstance(success, Mapping):
                exit_code = success.get("exitCode", success.get("exit_code"))
                if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                    record["exit_code"] = exit_code
                record["ok"] = True
            elif "error" in result or "failure" in result or "rejected" in result:
                record["ok"] = False
    return [by_call[call_id] for call_id in order]


def extract_assistant_segments(stream: str) -> list[str]:
    """Return each assistant message's text, one entry per message.

    The CLI's terminal ``result`` field is the assistant messages joined with
    nothing between them, so a declaration line at the end of one message runs
    straight into the first word of the next (``RECOVERY_USED: YESNo
    follow-up is needed``) and no anchored regex can find it. The stream still
    carries each message separately; this is the seam that keeps them apart.
    """

    segments: list[str] = []
    for event in _iter_stream_events(stream):
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        text = "".join(parts)
        if text.strip():
            segments.append(text)
    return segments


def _extract_final_text(stream: str) -> str:
    """Final transcript text: assistant messages newline-joined, else ``result``.

    Messages are joined with a newline so a declaration or verdict that ends
    one message stays on its own line. The ``result`` fallback keeps older
    streams, and test doubles that only emit a result, readable.
    """

    segments = extract_assistant_segments(stream)
    if segments:
        return "\n".join(segment.strip("\n") for segment in segments)
    final_text = ""
    for event in _iter_stream_events(stream):
        if isinstance(event.get("result"), str):
            final_text = event["result"]
    return final_text


def run_ready_attempt(
    record: ReadyRecord,
    command: Sequence[str],
    *,
    state_dir: Path,
    diagnostics: Mapping[str, Path] | None = None,
    timeout_seconds: float | None = None,
    memory_limit_bytes: int | None = None,
    terminate_grace_seconds: float = 2,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    memory_scope: str | None = None,
    memory_high_bytes: int | None = None,
    memory_probe: Callable[[int], int | None] | None = None,
    memory_poll_seconds: float = DEFAULT_MEMORY_POLL_SECONDS,
    memory_observe_only: bool = False,
    stall_seconds: float | None = None,
) -> dict[str, object]:
    """Run one ready item, atomically finalize logs, then copy diagnostics.

    ``stall_seconds`` arms the hung-agent detector; it only acts inside a
    memory scope, where cgroup CPU time is readable.
    """

    record.validate_record()
    events_probe: Callable[[], Mapping[str, int]] | None = None
    cpu_probe: Callable[[], int | None] | None = None
    raw_peak: dict[str, int] = {}
    observe_only = memory_observe_only
    if memory_scope:
        # The kernel enforces MemoryMax on the scope, so the sampling loop
        # only measures. Two killers racing for the same process group would
        # make the failure fingerprint depend on which one won.
        command = _scope_command(
            memory_scope,
            command,
            memory_max_bytes=memory_limit_bytes,
            memory_high_bytes=memory_high_bytes,
        )
        scope_name = memory_scope
        fallback_probe = memory_probe or _process_group_rss_bytes

        def memory_probe(pid: int) -> int | None:
            # Prefer the cgroup, but never go blind. If the scope directory
            # cannot be found, silently reporting nothing would leave
            # calibration permanently empty with no sample and no alarm.
            # Anonymous memory is what calibration learns from; the raw
            # cache-inclusive peak is kept only for the report.
            peak = cgroup_peak_bytes(scope_name)
            if peak is not None:
                raw_peak["cgroup_peak_bytes"] = max(
                    raw_peak.get("cgroup_peak_bytes", 0), peak
                )
            anon = cgroup_anon_bytes(scope_name)
            if anon is not None:
                return anon
            return peak if peak is not None else fallback_probe(pid)

        def events_probe() -> Mapping[str, int]:
            return cgroup_events(scope_name)

        def cpu_probe() -> int | None:
            return cgroup_cpu_usec(scope_name)

        observe_only = True
    attempt = allocate_attempt_directory(state_dir, record.workbook_id)
    descriptor, stream_temporary_name = tempfile.mkstemp(
        prefix=".stream.", suffix=".tmp", dir=attempt
    )
    os.close(descriptor)
    stream_temporary = Path(stream_temporary_name)
    status = "failed"
    return_code: int | None = None
    error_text: str | None = None
    execution_metrics: dict[str, object] = {}
    try:
        with stream_temporary.open("wb") as stream_handle:
            try:
                return_code = _run_in_process_group(
                    command,
                    stdout=stream_handle,
                    timeout_seconds=timeout_seconds,
                    grace_seconds=terminate_grace_seconds,
                    memory_limit_bytes=memory_limit_bytes,
                    metrics=execution_metrics,
                    cwd=cwd,
                    env=env,
                    memory_probe=memory_probe,
                    memory_events_probe=events_probe,
                    memory_poll_seconds=memory_poll_seconds,
                    memory_observe_only=observe_only,
                    # Only scoped runs carry the metric: it is the evidence that
                    # a MemoryMax payload is killable, and unlimited runs must
                    # keep producing no execution_limits at all.
                    record_oom_score_adj=bool(memory_scope),
                    cpu_probe=cpu_probe,
                    stall_seconds=stall_seconds,
                )
                status = "completed" if return_code == 0 else "failed"
            except ConversionTimeout as error:
                status = "timed_out"
                error_text = str(error)
            except Exception as error:
                status = "orchestrator_error"
                error_text = repr(error)
            stream_handle.flush()
            os.fsync(stream_handle.fileno())

        stream_text = stream_temporary.read_text(
            encoding="utf-8", errors="replace"
        )
        os.replace(stream_temporary, attempt / "stream.jsonl")
        _fsync_directory(attempt)
        final_text = _extract_final_text(stream_text)
        atomic_write_text(
            attempt / "final.txt",
            final_text + ("\n" if final_text else ""),
        )

        # Final logs are durable before any diagnostic source is touched.
        for name, source in sorted((diagnostics or {}).items()):
            safe_name = _canonical_id(name)
            publish_diagnostic_snapshot(
                source, attempt / "diagnostics" / safe_name
            )
    except Exception as error:
        status = "orchestrator_error"
        error_text = repr(error)
    finally:
        stream_temporary.unlink(missing_ok=True)

    if memory_scope:
        execution_metrics["memory_scope"] = memory_scope
        if memory_limit_bytes is not None:
            execution_metrics["memory_max_bytes"] = int(memory_limit_bytes)
        if raw_peak:
            execution_metrics["cgroup_peak_bytes"] = raw_peak["cgroup_peak_bytes"]
        events = execution_metrics.get("memory_events")
        killed = isinstance(events, Mapping) and int(events.get("oom_kill", 0) or 0)
        # A cgroup kill lands as SIGKILL, which systemd-run reports as 137. The
        # scope is usually reaped before memory.events can be re-read, so the
        # exit code is the reliable signal.
        if killed or return_code in (137, -9):
            execution_metrics.setdefault("limit_reason", "cgroup_oom")

    result: dict[str, object] = {
        "workbook_id": record.workbook_id,
        "status": status,
        "attempt": attempt.name,
        "attempt_path": str(attempt),
        "return_code": return_code,
        "updated_at": _timestamp(),
    }
    if error_text is not None:
        result["error"] = error_text
    if execution_metrics:
        result["execution_limits"] = execution_metrics
    atomic_write_json(attempt / "result.json", result)
    return result


def run_ready_batch(
    expected_ids: Iterable[object],
    *,
    ready_dir: Path,
    published_dir: Path,
    state_dir: Path,
    command_builder: Callable[[ReadyRecord, Path], Sequence[str]],
    workers: int = 1,
    diagnostics_builder: (
        Callable[[ReadyRecord], Mapping[str, Path]] | None
    ) = None,
    timeout_seconds: float | None = None,
    memory_limit_bytes: int | None = None,
    producer_complete_marker: Path | None = None,
    poll_seconds: float = 1,
    initial_terminal_records: Iterable[Mapping[str, object]] = (),
    result_classifier: (
        Callable[[ReadyRecord, Mapping[str, object]], str] | None
    ) = None,
    terminal_reconciler: (
        Callable[[ReadyRecord, Mapping[str, object]], str] | None
    ) = None,
) -> dict[str, object]:
    """Run verified ready records and atomically publish exact final accounting."""

    if workers < 1:
        raise ValueError("workers must be positive")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    baseline = reconcile_expected_ledger(expected_ids, [])
    canonical_expected = [
        str(row["workbook_id"]) for row in baseline["records"]
    ]
    expected_set = set(canonical_expected)
    terminal_records: list[Mapping[str, object]] = list(
        initial_terminal_records
    )
    # Validate pre-terminal conversion/source outcomes before any work starts.
    reconcile_expected_ledger(canonical_expected, terminal_records)
    preterminal_ids = {
        _canonical_id(record["workbook_id"])
        for record in terminal_records
    }

    def execute(record: ReadyRecord) -> dict[str, object]:
        workbook = record.validate_workbook(published_dir)
        command = command_builder(record, workbook)
        diagnostics = (
            diagnostics_builder(record)
            if diagnostics_builder is not None
            else None
        )
        result = run_ready_attempt(
            record,
            command,
            state_dir=state_dir,
            diagnostics=diagnostics,
            timeout_seconds=timeout_seconds,
            memory_limit_bytes=memory_limit_bytes,
        )
        if result_classifier is not None and result["status"] == "completed":
            classified = result_classifier(record, result)
            if classified not in TERMINAL_STATUSES:
                raise LedgerError(
                    f"result classifier returned non-terminal status: "
                    f"{classified!r}"
                )
            result["status"] = classified
        if terminal_reconciler is not None:
            reconciled = terminal_reconciler(record, result)
            if reconciled not in TERMINAL_STATUSES:
                raise LedgerError(
                    f"terminal reconciler returned non-terminal status: "
                    f"{reconciled!r}"
                )
            result["status"] = reconciled
            atomic_write_json(
                Path(str(result["attempt_path"])) / "terminal-result.json",
                result,
            )
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Any, ReadyRecord] = {}
        submitted: set[str] = set(preterminal_ids)
        while True:
            unexpected = sorted(
                path.name
                for path in ready_dir.glob("*.ready.json")
                if path.name.removesuffix(".ready.json") not in expected_set
            ) if ready_dir.is_dir() else []
            if unexpected:
                raise LedgerError(f"unexpected ready workbook IDs: {unexpected}")
            for workbook_id in canonical_expected:
                if workbook_id in submitted:
                    continue
                path = ready_dir / f"{workbook_id}.ready.json"
                if not path.is_file():
                    continue
                try:
                    record = ReadyRecord.read(path)
                    record.validate_workbook(published_dir)
                except (OSError, ReadyRecordError, ValueError) as error:
                    terminal_records.append({
                        "workbook_id": workbook_id,
                        "status": "invalid_ready",
                        "error": str(error),
                    })
                    submitted.add(workbook_id)
                    continue
                futures[executor.submit(execute, record)] = record
                submitted.add(workbook_id)
            if submitted == expected_set:
                break
            if producer_complete_marker is None or producer_complete_marker.is_file():
                break
            time.sleep(poll_seconds)
        for future in as_completed(futures):
            record = futures[future]
            try:
                terminal_records.append(future.result())
            except Exception as error:
                terminal_records.append(
                    {
                        "workbook_id": record.workbook_id,
                        "status": "orchestrator_error",
                        "error": repr(error),
                    }
                )

    ledger = reconcile_expected_ledger(canonical_expected, terminal_records)
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_dir / "terminal-ledger.json", ledger)
    return ledger


def reconcile_expected_ledger(
    expected_ids: Iterable[object],
    terminal_records: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Create one and only one terminal accounting row per expected ID."""

    expected: list[str] = []
    expected_set: set[str] = set()
    for raw_id in expected_ids:
        try:
            workbook_id = _canonical_id(raw_id)
        except ValueError as error:
            raise LedgerError(str(error)) from error
        if workbook_id in expected_set:
            raise LedgerError(f"duplicate expected workbook ID: {workbook_id}")
        expected.append(workbook_id)
        expected_set.add(workbook_id)

    by_id: dict[str, dict[str, object]] = {}
    for record in terminal_records:
        try:
            workbook_id = _canonical_id(record["workbook_id"])
            status = str(record["status"])
        except (KeyError, ValueError) as error:
            raise LedgerError(f"invalid terminal record: {record!r}") from error
        if workbook_id not in expected_set:
            raise LedgerError(f"unexpected terminal workbook ID: {workbook_id}")
        if status not in TERMINAL_STATUSES:
            raise LedgerError(
                f"non-terminal status for {workbook_id}: {status!r}"
            )
        if workbook_id in by_id:
            raise LedgerError(f"multiple terminal statuses for {workbook_id}")
        by_id[workbook_id] = {
            **dict(record),
            "workbook_id": workbook_id,
            "status": status,
        }

    rows = [
        by_id.get(workbook_id, {
            "workbook_id": workbook_id,
            "status": "missing_ready",
        })
        for workbook_id in sorted(expected)
    ]
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in sorted({str(row["status"]) for row in rows})
    }
    if sum(counts.values()) != len(expected) or len(rows) != len(expected):
        raise LedgerError("terminal ledger failed exact accounting")
    return {
        "schema_version": LEDGER_SCHEMA,
        "created_at": _timestamp(),
        "expected_count": len(expected),
        "terminal_count": len(rows),
        "counts": counts,
        "records": rows,
    }


def _convert_command(arguments: argparse.Namespace) -> int:
    record = convert_and_publish(
        Path(arguments.source),
        workbook_id=arguments.workbook_id,
        private_root=Path(arguments.private_root),
        published_dir=Path(arguments.published_dir),
        ready_dir=Path(arguments.ready_dir),
        libreoffice_binary=arguments.libreoffice,
        timeout_seconds=arguments.timeout,
    )
    print(json.dumps(asdict(record), sort_keys=True), flush=True)
    return 0


def _read_expected_ids(path: Path) -> list[object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("expected_ids", "workbook_ids", "ids"):
            values = payload.get(key)
            if isinstance(values, list):
                return values
        records = payload.get("records")
        if isinstance(records, list):
            return [
                record["workbook_id"]
                for record in records
                if isinstance(record, dict) and "workbook_id" in record
            ]
    raise LedgerError(f"cannot read expected workbook IDs from {path}")


def _run_command(arguments: argparse.Namespace) -> int:
    template = list(arguments.command)
    if template and template[0] == "--":
        template.pop(0)
    if not template:
        raise SystemExit("run requires a command after --")

    def command_builder(record: ReadyRecord, workbook: Path) -> Sequence[str]:
        substitutions = {
            "workbook_id": record.workbook_id,
            "xlsx": str(workbook),
            "state_dir": str(Path(arguments.state_dir)),
        }
        return [part.format_map(substitutions) for part in template]

    ledger = run_ready_batch(
        _read_expected_ids(Path(arguments.expected_ledger)),
        ready_dir=Path(arguments.ready_dir),
        published_dir=Path(arguments.published_dir),
        state_dir=Path(arguments.state_dir),
        command_builder=command_builder,
        workers=arguments.workers,
        timeout_seconds=arguments.timeout,
        memory_limit_bytes=(
            int(arguments.memory_limit_gib * 1024 ** 3)
            if arguments.memory_limit_gib is not None
            else None
        ),
        producer_complete_marker=(
            Path(arguments.producer_complete_marker)
            if arguments.producer_complete_marker
            else None
        ),
        poll_seconds=arguments.poll_seconds,
    )
    print(json.dumps(ledger, sort_keys=True), flush=True)
    counts = ledger["counts"]
    failures = sum(
        int(count)
        for status, count in counts.items()
        if status not in {"already_completed", "completed"}
    )
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    convert = subparsers.add_parser("convert")
    convert.add_argument("source")
    convert.add_argument("--workbook-id", required=True)
    convert.add_argument("--private-root", required=True)
    convert.add_argument("--published-dir", required=True)
    convert.add_argument("--ready-dir", required=True)
    convert.add_argument("--libreoffice", default="libreoffice")
    convert.add_argument("--timeout", type=float, default=600)
    convert.set_defaults(handler=_convert_command)
    run = subparsers.add_parser("run")
    run.add_argument("--expected-ledger", required=True)
    run.add_argument("--ready-dir", required=True)
    run.add_argument("--published-dir", required=True)
    run.add_argument("--state-dir", required=True)
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--timeout", type=float)
    run.add_argument("--memory-limit-gib", type=float)
    run.add_argument("--producer-complete-marker")
    run.add_argument("--poll-seconds", type=float, default=1)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=_run_command)
    arguments = parser.parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
