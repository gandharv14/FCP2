#!/usr/bin/env python3
"""Fail-closed conversion and execution primitives for spreadsheet batches."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
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
) -> int:
    if memory_limit_bytes is not None and memory_limit_bytes <= 0:
        raise ValueError("memory_limit_bytes must be positive")
    started_at = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    peak_rss = 0

    def observe_memory() -> None:
        nonlocal peak_rss
        if memory_limit_bytes is None:
            return
        rss = _process_group_rss_bytes(process.pid)
        if rss is None:
            return
        peak_rss = max(peak_rss, rss)
        if metrics is not None:
            metrics["peak_rss_bytes"] = peak_rss
        if rss > memory_limit_bytes:
            if metrics is not None:
                metrics["limit_reason"] = "process_group_rss"
            _terminate_process_group(process, grace_seconds=grace_seconds)
            raise ConversionTimeout(
                f"process group exceeded {memory_limit_bytes} RSS bytes: "
                f"{command[0]}"
            )

    if memory_limit_bytes is None:
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
            time.sleep(0.05)

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
            time.sleep(0.01)
        if _process_group_alive(process.pid):
            _terminate_process_group(process, grace_seconds=grace_seconds)
            raise ConversionTimeout(f"descendants outlived command: {command[0]}")
    return return_code


def _atomic_publish_validated_xlsx(candidate: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(candidate, temporary)
        validate_xlsx(temporary)
        if sha256_file(temporary) != sha256_file(candidate):
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
        _atomic_publish_validated_xlsx(candidate, destination)
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


def _extract_final_text(stream: str) -> str:
    final_text = ""
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("result"), str):
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
) -> dict[str, object]:
    """Run one ready item, atomically finalize logs, then copy diagnostics."""

    record.validate_record()
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
