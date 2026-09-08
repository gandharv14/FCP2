#!/usr/bin/env python3
"""Generic locking and journaled two-file apply for instruction rewrites.

This is the span-agnostic part of naturalize_recovery.py. It knows nothing
about headings, spans, or validators. It only knows how to replace
`instruction.md` and `task.toml` as one recoverable transaction.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


class RecoveryError(RuntimeError):
    def __init__(self, reason_code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "valid": False,
            "reason_codes": [self.reason_code],
            "error": str(self),
        }
        if self.details:
            result["details"] = self.details
        return result


class RecoveryInterruption(RecoveryError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("%s.tmp.%d.%d" % (path.name, os.getpid(), time.time_ns()))
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RecoveryError(
            "temporary_file_exists",
            "refusing to overwrite stale temporary file: %s" % temp,
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, json_bytes(value))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(
            "invalid_recovery_state", "could not read recovery state: %s" % path
        ) from exc
    if not isinstance(value, dict):
        raise RecoveryError("invalid_recovery_state", "recovery state must be an object")
    return value


@contextmanager
def recovery_lock(root: Path, *, label: str = "rewrite") -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "recovery.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError(
                "recovery_locked",
                "another %s process holds the recovery lock" % label,
                lock=str(lock_path),
            ) from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def target_lock_root(
    instruction_path: Path, task_toml_path: Path, *, lock_dir_name: str
) -> Path:
    key = hashlib.sha256(
        (str(instruction_path.resolve()) + "\0" + str(task_toml_path.resolve())).encode(
            "utf-8"
        )
    ).hexdigest()
    common_parent = Path(
        os.path.commonpath(
            [instruction_path.resolve().parent, task_toml_path.resolve().parent]
        )
    )
    return common_parent.parent / lock_dir_name / key


@contextmanager
def target_pair_lock(
    instruction_path: Path,
    task_toml_path: Path,
    *,
    lock_dir_name: str,
    label: str = "rewrite",
) -> Iterator[None]:
    with recovery_lock(
        target_lock_root(instruction_path, task_toml_path, lock_dir_name=lock_dir_name),
        label=label,
    ):
        yield


def failpoint(
    name: str,
    env_var: str,
    callback: Callable[[str], None] | None = None,
) -> None:
    if callback is not None:
        callback(name)
    if os.environ.get(env_var) == name:
        raise RecoveryInterruption(
            "simulated_interruption",
            "simulated transaction interruption at %s" % name,
            failpoint=name,
        )


def transaction_paths(root: Path) -> dict[str, Path]:
    return {
        "journal": root / "journal.json",
        "instruction_backup": root / "instruction.backup",
        "task_backup": root / "task_toml.backup",
        "instruction_new": root / "instruction.new",
        "task_new": root / "task_toml.new",
    }


def recover_transaction(
    transaction_root: Path,
    *,
    journal_version: str,
    failpoint_env: str,
    expected_instruction_path: Path | None = None,
    expected_task_toml_path: Path | None = None,
    failpoint_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Roll back an incomplete two-file transaction; committed writes stay put."""
    paths = transaction_paths(transaction_root)
    if not paths["journal"].exists():
        return {"recovered": False}
    journal = load_json(paths["journal"])
    if journal.get("version") != journal_version:
        raise RecoveryError(
            "invalid_transaction_journal", "unsupported rewrite transaction journal"
        )
    instruction = Path(journal["instruction_path"])
    task_toml = Path(journal["task_toml_path"])
    if (
        expected_instruction_path is not None
        and instruction.resolve() != expected_instruction_path.resolve()
    ) or (
        expected_task_toml_path is not None
        and task_toml.resolve() != expected_task_toml_path.resolve()
    ):
        raise RecoveryError(
            "transaction_target_mismatch",
            "transaction journal targets do not match recovery state",
        )
    phase = journal.get("phase")

    if phase == "committed":
        if (
            instruction.exists()
            and task_toml.exists()
            and sha256_bytes(instruction.read_bytes()) == journal["instruction_new_sha256"]
            and sha256_bytes(task_toml.read_bytes()) == journal["task_new_sha256"]
        ):
            return {"recovered": False, "committed": True}
        raise RecoveryError(
            "committed_transaction_drift",
            "committed rewrite files no longer match the journal",
        )

    current_pair = (
        sha256_bytes(instruction.read_bytes()),
        sha256_bytes(task_toml.read_bytes()),
    )
    original_pair = (
        journal["instruction_original_sha256"],
        journal["task_original_sha256"],
    )
    instruction_only_pair = (
        journal["instruction_new_sha256"],
        journal["task_original_sha256"],
    )
    new_pair = (journal["instruction_new_sha256"], journal["task_new_sha256"])
    allowed_pairs = {
        "prepared": {original_pair, instruction_only_pair},
        "instruction_replaced": {instruction_only_pair, new_pair},
        "task_replaced": {new_pair},
        "rolled_back": {original_pair},
    }
    rollback_phases = {
        "rollback_started",
        "rollback_instruction_restored",
        "rollback_task_restored",
    }
    if phase in rollback_phases:
        if (
            current_pair[0] not in {original_pair[0], new_pair[0]}
            or current_pair[1] not in {original_pair[1], new_pair[1]}
        ):
            raise RecoveryError(
                "transaction_target_drift",
                "rollback encountered a file not produced by this transaction",
                phase=phase,
            )
    elif phase not in allowed_pairs or current_pair not in allowed_pairs[phase]:
        raise RecoveryError(
            "transaction_target_drift",
            "current files are not a state produced by this transaction",
            phase=phase,
            current_instruction_sha256=current_pair[0],
            current_task_sha256=current_pair[1],
        )
    if phase == "rolled_back":
        return {"recovered": False, "rolled_back": True}

    for backup_key, expected_key in (
        ("instruction_backup", "instruction_original_sha256"),
        ("task_backup", "task_original_sha256"),
    ):
        backup = paths[backup_key]
        if not backup.exists() or sha256_bytes(backup.read_bytes()) != journal[expected_key]:
            raise RecoveryError(
                "backup_hash_mismatch",
                "cannot safely recover transaction because a backup changed",
                backup=str(backup),
            )

    if phase not in rollback_phases:
        journal["phase"] = "rollback_started"
        journal["rollback_started_at_ns"] = time.time_ns()
        atomic_write_json(paths["journal"], journal)
        failpoint("after_rollback_intent", failpoint_env, failpoint_callback)
    atomic_write_bytes(instruction, paths["instruction_backup"].read_bytes())
    journal["phase"] = "rollback_instruction_restored"
    atomic_write_json(paths["journal"], journal)
    failpoint("after_rollback_instruction_restore", failpoint_env, failpoint_callback)
    atomic_write_bytes(task_toml, paths["task_backup"].read_bytes())
    journal["phase"] = "rollback_task_restored"
    atomic_write_json(paths["journal"], journal)
    failpoint("after_rollback_task_restore", failpoint_env, failpoint_callback)
    journal["phase"] = "rolled_back"
    journal["rolled_back_at_ns"] = time.time_ns()
    atomic_write_json(paths["journal"], journal)
    return {"recovered": True, "rolled_back": True}


def journaled_apply_bytes(
    instruction_path: Path,
    instruction_bytes: bytes,
    task_toml_path: Path,
    task_toml_bytes: bytes,
    *,
    expected_instruction_sha256: str,
    transaction_root: Path,
    journal_version: str,
    failpoint_env: str,
    lock_dir_name: str,
    expected_task_sha256: str | None = None,
    failpoint_callback: Callable[[str], None] | None = None,
    lock_held: bool = False,
    target_lock_held: bool = False,
) -> dict[str, Any]:
    """Apply instruction.md and task.toml as one recoverable transaction."""

    def run() -> dict[str, Any]:
        transaction_root.mkdir(parents=True, exist_ok=True)
        recovered = recover_transaction(
            transaction_root,
            journal_version=journal_version,
            failpoint_env=failpoint_env,
            expected_instruction_path=instruction_path,
            expected_task_toml_path=task_toml_path,
        )
        current_instruction = instruction_path.read_bytes()
        current_task = task_toml_path.read_bytes()
        new_instruction_hash = sha256_bytes(instruction_bytes)
        new_task_hash = sha256_bytes(task_toml_bytes)

        if (
            sha256_bytes(current_instruction) == new_instruction_hash
            and sha256_bytes(current_task) == new_task_hash
        ):
            return {"applied": True, "idempotent": True, **recovered}
        actual_hash = sha256_bytes(current_instruction)
        if actual_hash != expected_instruction_sha256:
            raise RecoveryError(
                "source_hash_mismatch",
                "apply target changed after the immutable source snapshot",
                expected_sha256=expected_instruction_sha256,
                actual_sha256=actual_hash,
            )
        actual_task_hash = sha256_bytes(current_task)
        if expected_task_sha256 is not None and actual_task_hash != expected_task_sha256:
            raise RecoveryError(
                "task_hash_mismatch",
                "task.toml changed before the rewrite transaction",
                expected_sha256=expected_task_sha256,
                actual_sha256=actual_task_hash,
            )

        paths = transaction_paths(transaction_root)
        for key in ("instruction_backup", "task_backup", "instruction_new", "task_new"):
            try:
                paths[key].unlink()
            except FileNotFoundError:
                pass
        atomic_write_bytes(paths["instruction_backup"], current_instruction, mode=0o400)
        atomic_write_bytes(paths["task_backup"], current_task, mode=0o400)
        atomic_write_bytes(paths["instruction_new"], instruction_bytes, mode=0o400)
        atomic_write_bytes(paths["task_new"], task_toml_bytes, mode=0o400)
        journal = {
            "version": journal_version,
            "phase": "prepared",
            "instruction_path": str(instruction_path.resolve()),
            "task_toml_path": str(task_toml_path.resolve()),
            "instruction_original_sha256": sha256_bytes(current_instruction),
            "task_original_sha256": sha256_bytes(current_task),
            "instruction_new_sha256": new_instruction_hash,
            "task_new_sha256": new_task_hash,
            "prepared_at_ns": time.time_ns(),
        }
        atomic_write_json(paths["journal"], journal)
        failpoint("after_prepare", failpoint_env, failpoint_callback)

        atomic_write_bytes(instruction_path, instruction_bytes)
        failpoint("after_instruction_replace", failpoint_env, failpoint_callback)
        journal["phase"] = "instruction_replaced"
        atomic_write_json(paths["journal"], journal)

        atomic_write_bytes(task_toml_path, task_toml_bytes)
        failpoint("after_task_replace", failpoint_env, failpoint_callback)
        journal["phase"] = "task_replaced"
        atomic_write_json(paths["journal"], journal)

        journal["phase"] = "committed"
        journal["committed_at_ns"] = time.time_ns()
        atomic_write_json(paths["journal"], journal)
        failpoint("after_commit", failpoint_env, failpoint_callback)
        return {"applied": True, "idempotent": False, **recovered}

    if not target_lock_held:
        with target_pair_lock(instruction_path, task_toml_path, lock_dir_name=lock_dir_name):
            return journaled_apply_bytes(
                instruction_path,
                instruction_bytes,
                task_toml_path,
                task_toml_bytes,
                expected_instruction_sha256=expected_instruction_sha256,
                transaction_root=transaction_root,
                journal_version=journal_version,
                failpoint_env=failpoint_env,
                lock_dir_name=lock_dir_name,
                expected_task_sha256=expected_task_sha256,
                failpoint_callback=failpoint_callback,
                lock_held=lock_held,
                target_lock_held=True,
            )
    if lock_held:
        return run()
    with recovery_lock(transaction_root):
        return run()
