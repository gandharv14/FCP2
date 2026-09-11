#!/usr/bin/env python3
"""Two-attempt, span-only instruction naturalization with crash recovery."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
import tomllib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from instruction_spans import (  # noqa: E402
    InstructionSpanError,
    assemble_instruction,
    scan_instruction,
    sha256_bytes,
)


RECOVERY_VERSION = "naturalize-recovery-v1"
MAX_ATTEMPTS = 2
RETRYABLE_REASON_CODES = frozenset(
    {
        "frozen_span_invalid",
        "answer_value_leak",
        "candidate_structure_invalid",
        "final_newline_changed",
        "heading_structure_changed",
        "input_availability_lost",
        "input_exclusivity_lost",
        "input_modality_lost",
        "invalid_replacement_utf8",
        "named_outputs_changed",
        "protected_section_changed",
        "protected_construct_order_changed",
        "replacement_contains_bom",
        "removed_scope_changed",
        "semantic_anchor_lost",
        "semantic_mismatch",
        "source_category_lost",
        "template_placeholder",
        "token_mismatch",
        "validation_constraint_failed",
    }
)
TERMINAL_STATES = frozenset({"applied", "exhausted", "failed"})


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


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        "%s.tmp.%d.%d" % (path.name, os.getpid(), time.time_ns())
    )
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
    atomic_write_bytes(path, _json_bytes(value))


@contextmanager
def recovery_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "recovery.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError(
                "recovery_locked",
                "another naturalization process holds the recovery lock",
                lock=str(lock_path),
            ) from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _target_lock_root(instruction_path: Path, task_toml_path: Path) -> Path:
    key = hashlib.sha256(
        (
            str(instruction_path.resolve())
            + "\0"
            + str(task_toml_path.resolve())
        ).encode("utf-8")
    ).hexdigest()
    common_parent = Path(
        os.path.commonpath(
            [instruction_path.resolve().parent, task_toml_path.resolve().parent]
        )
    )
    return common_parent.parent / ".naturalize-target-locks" / key


@contextmanager
def target_pair_lock(
    instruction_path: Path,
    task_toml_path: Path,
) -> Iterator[None]:
    with recovery_lock(_target_lock_root(instruction_path, task_toml_path)):
        yield


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(
            "invalid_recovery_state",
            "could not read recovery state: %s" % path,
        ) from exc
    if not isinstance(value, dict):
        raise RecoveryError("invalid_recovery_state", "recovery state must be an object")
    return value


def _failpoint(name: str, callback: Callable[[str], None] | None = None) -> None:
    if callback is not None:
        callback(name)
    if os.environ.get("NATURALIZE_FAILPOINT") == name:
        raise RecoveryInterruption(
            "simulated_interruption",
            "simulated transaction interruption at %s" % name,
            failpoint=name,
        )


def _transaction_paths(root: Path) -> dict[str, Path]:
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
    expected_instruction_path: Path | None = None,
    expected_task_toml_path: Path | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Roll back an incomplete two-file transaction; committed writes stay put."""
    paths = _transaction_paths(transaction_root)
    if not paths["journal"].exists():
        return {"recovered": False}
    journal = _load_json(paths["journal"])
    if journal.get("version") != RECOVERY_VERSION:
        raise RecoveryError(
            "invalid_transaction_journal",
            "unsupported naturalization transaction journal",
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
            "committed naturalization files no longer match the journal",
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
    new_pair = (
        journal["instruction_new_sha256"],
        journal["task_new_sha256"],
    )
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
        _failpoint("after_rollback_intent", failpoint)
    atomic_write_bytes(instruction, paths["instruction_backup"].read_bytes())
    journal["phase"] = "rollback_instruction_restored"
    atomic_write_json(paths["journal"], journal)
    _failpoint("after_rollback_instruction_restore", failpoint)
    atomic_write_bytes(task_toml, paths["task_backup"].read_bytes())
    journal["phase"] = "rollback_task_restored"
    atomic_write_json(paths["journal"], journal)
    _failpoint("after_rollback_task_restore", failpoint)
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
    expected_task_sha256: str | None = None,
    failpoint: Callable[[str], None] | None = None,
    lock_held: bool = False,
    target_lock_held: bool = False,
) -> dict[str, Any]:
    """Apply instruction.md and task.toml as one recoverable transaction."""

    def run() -> dict[str, Any]:
        transaction_root.mkdir(parents=True, exist_ok=True)
        recovered = recover_transaction(
            transaction_root,
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
        if (
            expected_task_sha256 is not None
            and actual_task_hash != expected_task_sha256
        ):
            raise RecoveryError(
                "task_hash_mismatch",
                "task.toml changed before the naturalization transaction",
                expected_sha256=expected_task_sha256,
                actual_sha256=actual_task_hash,
            )

        paths = _transaction_paths(transaction_root)
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
            "version": RECOVERY_VERSION,
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
        _failpoint("after_prepare", failpoint)

        atomic_write_bytes(instruction_path, instruction_bytes)
        _failpoint("after_instruction_replace", failpoint)
        journal["phase"] = "instruction_replaced"
        atomic_write_json(paths["journal"], journal)

        atomic_write_bytes(task_toml_path, task_toml_bytes)
        _failpoint("after_task_replace", failpoint)
        journal["phase"] = "task_replaced"
        atomic_write_json(paths["journal"], journal)

        journal["phase"] = "committed"
        journal["committed_at_ns"] = time.time_ns()
        atomic_write_json(paths["journal"], journal)
        _failpoint("after_commit", failpoint)
        return {"applied": True, "idempotent": False, **recovered}

    if not target_lock_held:
        with target_pair_lock(instruction_path, task_toml_path):
            return journaled_apply_bytes(
                instruction_path,
                instruction_bytes,
                task_toml_path,
                task_toml_bytes,
                expected_instruction_sha256=expected_instruction_sha256,
                transaction_root=transaction_root,
                expected_task_sha256=expected_task_sha256,
                failpoint=failpoint,
                lock_held=lock_held,
                target_lock_held=True,
            )
    if lock_held:
        return run()
    with recovery_lock(transaction_root):
        return run()


def _state_path(root: Path) -> Path:
    return root / "state.json"


def _source_path(root: Path) -> Path:
    return root / "source.snapshot.md"


def _read_state(root: Path) -> dict[str, Any]:
    state = _load_json(_state_path(root))
    if state.get("version") != RECOVERY_VERSION:
        raise RecoveryError("invalid_recovery_state", "unsupported recovery state version")
    return state


def _verified_snapshot(root: Path, state: dict[str, Any]) -> bytes:
    _verified_budget(root, state)
    source = _source_path(root).read_bytes()
    actual = sha256_bytes(source)
    if actual != state["source_sha256"]:
        raise RecoveryError(
            "snapshot_hash_mismatch",
            "code-owned source snapshot changed",
            expected_sha256=state["source_sha256"],
            actual_sha256=actual,
        )
    spans = scan_instruction(source)
    stored_spans = _load_json(root / "spans.json")
    if spans.as_dict() != stored_spans:
        raise RecoveryError("span_hash_mismatch", "stored spans do not match the source snapshot")
    return source


def _attempts_used(state: dict[str, Any]) -> int:
    return state.get("inherited_attempts", 0) + len(state.get("attempts", []))


def _submission_engine(mode):
    if mode == "frozen-boundary-v1":
        import boundary_submission
        return boundary_submission
    import frozen_submission
    return frozen_submission


def _verified_budget(root: Path, state: dict[str, Any], depth: int = 0) -> None:
    import span_restoration
    span_restoration.validate(sys.modules[__name__], root, state)
    if state.get("boundary_recovery") or state.get("final_candidate", "").startswith("boundary-recovery-v1/"):
        repository = SCRIPT_DIR.parents[3]
        if str(repository) not in sys.path:
            sys.path.insert(0, str(repository))
        from scripts.batch import harbor_nat_boundary_recovery
        harbor_nat_boundary_recovery.validate(root, state, api=sys.modules[__name__])
    import frozen_submission
    for item in state.get("attempts", []):
        _submission_engine(item.get("submission_mode", "plain")).validate_attempt(sys.modules[__name__], root, state, item)
    inherited = state.get("inherited_attempts", 0)
    attempts = state.get("attempts", [])
    if (type(inherited) is not int or inherited < 0 or not isinstance(attempts, list)
            or inherited + len(attempts) > MAX_ATTEMPTS or depth > 32
            or [item.get("attempt") for item in attempts]
            != list(range(inherited + 1, inherited + len(attempts) + 1))):
        raise RecoveryError("invalid_attempt_budget", "naturalization attempt budget is inconsistent")
    recorded = {item["attempt"] for item in attempts}
    for number in range(inherited + 1, MAX_ATTEMPTS + 1):
        if ((root / ("attempt-%02d" % number) / "validation.json").is_file()
                and number not in recorded and (state.get("staged_attempt") or {}).get("attempt") != number):
            raise RecoveryError("invalid_attempt_budget", "completed attempt is missing from the budget record")
    binding = state.get("budget_inheritance")
    if not binding:
        if inherited:
            raise RecoveryError("invalid_attempt_budget", "inherited budget lacks bound evidence")
        return
    archive = root / "inherited-budget" / "previous"
    files = binding.get("files")
    if not isinstance(files, dict) or not files:
        raise RecoveryError("inherited_evidence_changed", "prior evidence manifest is missing")
    for relative, expected in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise RecoveryError("inherited_evidence_changed", "unsafe inherited evidence path")
        artifact = archive / path
        if artifact.is_symlink() or not artifact.is_file() or sha256_bytes(artifact.read_bytes()) != expected:
            raise RecoveryError("inherited_evidence_changed", "prior naturalization evidence changed", artifact=relative)
    state_path = archive / "state.json"
    if sha256_bytes(state_path.read_bytes()) != binding.get("previous_state_sha256"):
        raise RecoveryError("inherited_evidence_changed", "prior state digest changed")
    previous = _read_state(archive)
    _verified_budget(archive, previous, depth + 1)
    if inherited != _attempts_used(previous) or inherited != binding.get("attempts_used"):
        raise RecoveryError("invalid_attempt_budget", "inherited attempt count differs from prior evidence")


def _verify_attempt_evidence(root: Path, state: dict[str, Any]) -> None:
    import frozen_submission
    for item in state.get("attempts", []):
        _submission_engine(item.get("submission_mode", "plain")).validate_attempt(sys.modules[__name__], root, state, item)
        attempt = root / ("attempt-%02d" % item["attempt"])
        report = _load_json(attempt / "validation.json")
        for field in ("attempt", "valid", "reason_codes", "preamble_submission_sha256", "input_submission_sha256"):
            if report.get(field) != item.get(field):
                raise RecoveryError("prior_attempt_evidence_changed", "prior attempt record differs from its validation", field=field)
        for filename, field in (("preamble_body.md", "preamble_submission_sha256"),
                                ("input_body.md", "input_submission_sha256"),
                                ("candidate.md", "candidate_sha256")):
            if item.get(field) and sha256_bytes((attempt / filename).read_bytes()) != item[field]:
                raise RecoveryError("prior_attempt_evidence_changed", "prior submitted artifact changed", artifact=filename)
        if (report.get("source_sha256") != state.get("source_sha256")
                or sha256_bytes((attempt / "source.snapshot.md").read_bytes()) != report.get("source_sha256")):
            raise RecoveryError("prior_attempt_evidence_changed", "prior attempt source differs from its snapshot")


def inherit_attempt_budget(recovery_root: Path, previous_root: Path, *,
                           previous_state_sha256: str, reason: str) -> dict[str, Any]:
    """Carry consumed attempts across a corrected source without reusing prose.

    The caller supplies its already recorded prior-state digest. Only a fresh
    initialized run may inherit. Complete prior evidence is archived unchanged;
    chained inheritance preserves the cumulative two-attempt ceiling.
    """
    recovery_root, previous_root = recovery_root.resolve(), previous_root.resolve()
    if recovery_root == previous_root or not reason.strip():
        raise RecoveryError("invalid_budget_inheritance", "distinct runs and an explicit reason are required")
    if recovery_root in previous_root.parents or previous_root in recovery_root.parents:
        raise RecoveryError("invalid_budget_inheritance", "recovery runs must not contain one another")
    with ExitStack() as stack:
        for path in sorted((recovery_root, previous_root)):
            stack.enter_context(recovery_lock(path))
        state, previous = _read_state(recovery_root), _read_state(previous_root)
        if sha256_bytes(_state_path(previous_root).read_bytes()) != previous_state_sha256:
            raise RecoveryError("prior_state_changed", "prior state differs from the recorded digest")
        _verified_snapshot(previous_root, previous)
        _verified_answer_key(previous_root, previous)
        _verify_attempt_evidence(previous_root, previous)
        _verified_snapshot(recovery_root, state)
        _verified_answer_key(recovery_root, state)
        used = _attempts_used(previous)
        if used < 1 or previous.get("attempt_limit") != MAX_ATTEMPTS:
            raise RecoveryError("invalid_budget_inheritance", "prior run has no valid consumed attempt budget")
        existing = state.get("budget_inheritance")
        if existing:
            if existing.get("previous_state_sha256") == previous_state_sha256 and existing.get("reason") == reason:
                return state
            raise RecoveryError("budget_already_inherited", "this run already inherits another budget")
        if state.get("status") != "initialized" or state.get("attempts"):
            raise RecoveryError("budget_target_not_fresh", "budget inheritance requires a fresh initialized run")
        if previous.get("staged_attempt"):
            raise RecoveryError("prior_attempt_incomplete", "finish or resolve the interrupted prior submission first")
        archive_parent = recovery_root / "inherited-budget"
        archive = archive_parent / "previous"
        files = {}
        for path in previous_root.rglob("*"):
            if path.is_symlink():
                raise RecoveryError("invalid_budget_inheritance", "prior evidence contains a symlink")
            if path.is_file() and not path.name.endswith(".lock"):
                relative = str(path.relative_to(previous_root))
                files[relative] = sha256_bytes(path.read_bytes())
        archive_parent.mkdir(parents=True, exist_ok=True)
        temporary = archive_parent / "previous.pending"
        if temporary.exists():
            shutil.rmtree(temporary)
        if not archive.exists():
            temporary.mkdir()
            for relative in sorted(files):
                destination = temporary / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(destination, (previous_root / relative).read_bytes(), mode=0o400)
            os.replace(temporary, archive)
            _fsync_directory(archive_parent)
        binding = {"schema_version": "naturalization-budget-inheritance/v1",
                   "previous_root": str(previous_root), "previous_state_sha256": previous_state_sha256,
                   "attempts_used": used, "reason": reason, "files": files}
        next_state = {**state, "inherited_attempts": used, "budget_inheritance": binding,
                      "next_attempt": used + 1 if used < MAX_ATTEMPTS else None,
                      "status": "initialized" if used < MAX_ATTEMPTS else "exhausted",
                      "updated_at_ns": time.time_ns()}
        _verified_budget(recovery_root, next_state)
        atomic_write_json(_state_path(recovery_root), next_state)
        return next_state


def _verified_answer_key(root: Path, state: dict[str, Any]) -> dict | None:
    expected = state.get("answer_key_sha256")
    snapshot = root / "answer_key.snapshot.json"
    if expected is None:
        if snapshot.exists():
            raise RecoveryError(
                "unexpected_answer_key_snapshot",
                "unbound answer-key snapshot is present",
            )
        return None
    if not snapshot.is_file():
        raise RecoveryError(
            "answer_key_snapshot_missing",
            "bound answer-key snapshot is missing",
        )
    content = snapshot.read_bytes()
    actual = sha256_bytes(content)
    if actual != expected:
        raise RecoveryError(
            "answer_key_snapshot_drift",
            "bound answer-key snapshot changed",
            expected_sha256=expected,
            actual_sha256=actual,
        )
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(
            "answer_key_snapshot_invalid",
            "bound answer-key snapshot is invalid JSON",
        ) from exc
    if not isinstance(value, dict):
        raise RecoveryError(
            "answer_key_snapshot_invalid",
            "bound answer-key snapshot must be an object",
        )
    return value


def _verified_semantic_review(root: Path, state: dict[str, Any]) -> dict:
    accepted = state.get("accepted_semantic_review") or {}
    try:
        attempt = int(accepted.get("attempt", 0))
    except (TypeError, ValueError):
        attempt = 0
    review_path = root / ("attempt-%02d" % attempt) / "semantic-review.json"
    if (
        accepted.get("accepted") is not True
        or accepted.get("candidate_sha256") != state.get("final_instruction_sha256")
        or accepted.get("source_sha256") != state.get("source_sha256")
        or not review_path.is_file()
        or _load_json(review_path) != accepted
    ):
        raise RecoveryError(
            "semantic_review_mismatch",
            "selected candidate lacks matching persisted semantic approval",
        )
    return accepted


def _check_live_source(state: dict[str, Any]) -> None:
    source_path = Path(state["source_path"])
    try:
        actual = sha256_bytes(source_path.read_bytes())
    except OSError as exc:
        raise RecoveryError("source_unavailable", "live source is unavailable") from exc
    if actual != state["source_sha256"]:
        raise RecoveryError(
            "source_drift",
            "live instruction changed after recovery initialization",
            expected_sha256=state["source_sha256"],
            actual_sha256=actual,
        )


def verify_naturalizer_metadata(
    instruction_path: Path,
    task_toml_path: Path,
) -> dict[str, Any]:
    instruction = instruction_path.read_bytes()
    task_bytes = task_toml_path.read_bytes()
    try:
        parsed = tomllib.loads(task_bytes.decode("utf-8"))
        metadata = parsed["metadata"]["naturalizer"]
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise RecoveryError(
            "invalid_naturalizer_metadata",
            "task.toml has no valid metadata.naturalizer table",
        ) from exc
    instruction_hash = sha256_bytes(instruction)
    required = {
        "model": "gpt-5.6-sol-high",
        "prompt_version": "finance-instruction-naturalizer-v3",
        "naturalized": True,
    }
    failures = [
        key for key, expected in required.items() if metadata.get(key) != expected
    ]
    if metadata.get("endpoint") not in {"cursor-subagent", "https://litellm.labelbox.com"}:
        failures.append("endpoint")
    attempts = metadata.get("attempts")
    if not isinstance(attempts, int) or not 1 <= attempts <= MAX_ATTEMPTS:
        failures.append("attempts")
    if metadata.get("instruction_sha256") != instruction_hash:
        failures.append("instruction_sha256")
    source_hash = metadata.get("source_sha256")
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        failures.append("source_sha256")
    if failures:
        raise RecoveryError(
            "naturalizer_metadata_mismatch",
            "naturalizer metadata does not match live instruction",
            fields=sorted(set(failures)),
        )
    return {
        "valid": True,
        "instruction_sha256": instruction_hash,
        "task_toml_sha256": sha256_bytes(task_bytes),
        "source_sha256": source_hash,
        "attempts": attempts,
        "model": metadata["model"],
        "endpoint": metadata["endpoint"],
        "prompt_version": metadata["prompt_version"],
    }


def _reconcile_applied_report(
    recovery_root: Path,
    state: dict[str, Any],
    recovered: dict[str, Any],
) -> dict[str, Any]:
    attempt_report = _load_json(
        recovery_root
        / ("attempt-%02d" % state["attempts"][-1]["attempt"])
        / "validation.json"
    )
    final_report = {
        **attempt_report,
        "valid": True,
        "applied": True,
        "idempotent": True,
        "state": "applied",
        **recovered,
    }
    atomic_write_json(recovery_root / "validation.json", final_report)
    return final_report


def bind_endpoint(recovery_root: Path, endpoint: str) -> dict[str, Any]:
    """Bind actual invocation provenance without changing candidates or budgets."""
    if endpoint not in {"cursor-subagent", "https://litellm.labelbox.com"}:
        raise RecoveryError("unsupported_endpoint", "unsupported naturalizer endpoint")
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        if state["status"] == "applied":
            raise RecoveryError("already_applied", "cannot change applied provenance")
        if state.get("endpoint") not in {None, endpoint}:
            raise RecoveryError("endpoint_drift", "invocation endpoint is already bound")
        _verified_snapshot(recovery_root, state)
        _check_live_source(state)
        state["endpoint"] = endpoint
        state.setdefault("endpoint_bound_at_ns", time.time_ns())
        atomic_write_json(_state_path(recovery_root), state)
        return state


def init_recovery(
    source_path: Path,
    recovery_root: Path,
    *,
    instruction_path: Path | None = None,
    task_toml_path: Path | None = None,
    answer_key_path: Path | None = None,
) -> dict[str, Any]:
    source_path = source_path.resolve()
    instruction_path = (instruction_path or source_path).resolve()
    with recovery_lock(recovery_root):
        if _state_path(recovery_root).exists():
            state = _read_state(recovery_root)
            _verified_snapshot(recovery_root, state)
            _verified_answer_key(recovery_root, state)
            configured_instruction = Path(state["instruction_path"]).resolve()
            configured_task = (
                Path(state["task_toml_path"]).resolve()
                if state.get("task_toml_path")
                else None
            )
            if configured_instruction != instruction_path or (
                task_toml_path is not None
                and configured_task != task_toml_path.resolve()
            ):
                raise RecoveryError(
                    "recovery_target_mismatch",
                    "existing recovery targets different staged files",
                )
            if state["status"] == "applied":
                journal = _load_json(
                    recovery_root / "apply-transaction" / "journal.json"
                )
                if (
                    journal.get("version") != RECOVERY_VERSION
                    or journal.get("phase") != "committed"
                    or sha256_bytes(instruction_path.read_bytes())
                    != state["final_instruction_sha256"]
                    or configured_task is None
                    or sha256_bytes(configured_task.read_bytes())
                    != state.get("task_toml_sha256")
                ):
                    raise RecoveryError(
                        "applied_target_drift",
                        "applied recovery no longer matches its staged targets",
                    )
                metadata = verify_naturalizer_metadata(
                    instruction_path,
                    configured_task,
                )
                if metadata["source_sha256"] != state["source_sha256"]:
                    raise RecoveryError(
                        "applied_metadata_drift",
                        "applied metadata no longer matches recovery source",
                    )
            else:
                _check_live_source(state)
            return state

        source = source_path.read_bytes()
        spans = scan_instruction(source)
        atomic_write_bytes(_source_path(recovery_root), source, mode=0o400)
        atomic_write_json(recovery_root / "spans.json", spans.as_dict())
        for number in range(1, MAX_ATTEMPTS + 1):
            attempt_dir = recovery_root / ("attempt-%02d" % number)
            attempt_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(attempt_dir / "source.snapshot.md", source, mode=0o400)

        answer_key_sha256 = None
        if answer_key_path is not None:
            answer_bytes = answer_key_path.resolve().read_bytes()
            json.loads(answer_bytes.decode("utf-8"))
            atomic_write_bytes(recovery_root / "answer_key.snapshot.json", answer_bytes, mode=0o400)
            answer_key_sha256 = sha256_bytes(answer_bytes)

        state: dict[str, Any] = {
            "version": RECOVERY_VERSION,
            "status": "initialized",
            "source_path": str(source_path),
            "instruction_path": str(instruction_path),
            "task_toml_path": (
                str(task_toml_path.resolve()) if task_toml_path is not None else None
            ),
            "answer_key_sha256": answer_key_sha256,
            "source_sha256": spans.source_sha256,
            "attempt_limit": MAX_ATTEMPTS,
            "attempts": [],
            "next_attempt": 1,
            "final_instruction_sha256": None,
            "created_at_ns": time.time_ns(),
        }
        atomic_write_json(_state_path(recovery_root), state)
        return state


def _load_validator() -> Any:
    import validate_instruction_rewrite

    return validate_instruction_rewrite


def submit_attempt(
    recovery_root: Path,
    preamble_body: bytes | str,
    input_body: bytes | str,
    *,
    submission_mode: str = "plain",
    expected_attempt: int | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if submission_mode not in {"plain", "frozen", "frozen-boundary-v1"}:
        raise RecoveryError("invalid_submission_mode", "unsupported writer submission mode")
    frozen_submission = _submission_engine(submission_mode)
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        _verified_budget(recovery_root, state)
        # The journaled producer supplies its validated ordinal. This separates
        # replay of one invocation from a new invocation returning identical text.
        # Callers that omit it keep the existing body-based idempotence contract.
        used = _attempts_used(state)
        if expected_attempt is not None:
            if (
                type(expected_attempt) is not int
                or not 1 <= expected_attempt <= MAX_ATTEMPTS
                or expected_attempt not in (used, used + 1)
                or (
                    expected_attempt == used
                    and (not state["attempts"] or state["attempts"][-1]["attempt"] != used)
                )
            ):
                raise RecoveryError(
                    "attempt_identity_mismatch",
                    "explicit writer ordinal is not the next or exact retained attempt",
                )

        preamble_bytes = (
            preamble_body.encode("utf-8")
            if isinstance(preamble_body, str)
            else preamble_body
        )
        input_bytes = (
            input_body.encode("utf-8") if isinstance(input_body, str) else input_body
        )
        freeze_inputs = frozen_submission.read_inputs(recovery_root) if submission_mode in {"frozen", "frozen-boundary-v1"} else {}
        freeze_identity = frozen_submission.input_identity(freeze_inputs)
        preamble_hash = sha256_bytes(preamble_bytes)
        input_hash = sha256_bytes(input_bytes)
        if state["attempts"] and state["status"] != "applied" and (
            (expected_attempt is None and (
                state["status"] != "retry_ready" or not state.get("semantic_reviews")))
            or expected_attempt == used
        ):
            previous_number = state["attempts"][-1]["attempt"]
            previous_report_path = (
                recovery_root
                / ("attempt-%02d" % previous_number)
                / "validation.json"
            )
            previous_report = _load_json(previous_report_path)
            if (
                previous_report.get("preamble_submission_sha256") == preamble_hash
                and previous_report.get("input_submission_sha256") == input_hash
            ):
                if previous_report.get("submission_mode", "plain") != submission_mode:
                    raise RecoveryError("submission_mode_mismatch", "same bodies were submitted in a different mode")
                if submission_mode in {"frozen", "frozen-boundary-v1"}:
                    retained = frozen_submission.read_inputs(previous_report_path.parent)
                    if frozen_submission.input_identity(retained) != freeze_identity:
                        raise RecoveryError("staged_attempt_mismatch", "frozen authoring inputs changed")
                previous_report["state"] = state["status"]
                previous_report["idempotent"] = True
                return previous_report
            if expected_attempt == used:
                raise RecoveryError(
                    "attempt_identity_mismatch",
                    "retained writer ordinal has different submitted bodies",
                )
        if state["status"] == "applied":
            raise RecoveryError("already_applied", "naturalization is already applied")
        if state["status"] in {"exhausted", "failed"}:
            raise RecoveryError(
                "terminal_state",
                "naturalization is in terminal state %s" % state["status"],
            )
        if state["status"] == "validated":
            raise RecoveryError("already_validated", "a valid candidate already exists")
        _check_live_source(state)
        source = _verified_snapshot(recovery_root, state)
        attempt_number = _attempts_used(state) + 1
        if attempt_number > MAX_ATTEMPTS:
            state["status"] = "exhausted"
            state["next_attempt"] = None
            atomic_write_json(_state_path(recovery_root), state)
            raise RecoveryError("attempts_exhausted", "both naturalization attempts are used")

        attempt_dir = recovery_root / ("attempt-%02d" % attempt_number)
        staged = state.get("staged_attempt")
        if staged is not None and (
            staged.get("attempt") != attempt_number
            or staged.get("preamble_submission_sha256") != preamble_hash
            or staged.get("input_submission_sha256") != input_hash
            or staged.get("submission_mode", "plain") != submission_mode
            or staged.get("freeze_input_sha256", {}) != freeze_identity
        ):
            raise RecoveryError(
                "staged_attempt_mismatch",
                "a different submission is already staged for this attempt",
            )
        state["status"] = "staged"
        state["staged_attempt"] = {
            "attempt": attempt_number,
            "source_sha256": state["source_sha256"],
            "preamble_submission_sha256": preamble_hash,
            "input_submission_sha256": input_hash,
            "submission_mode": submission_mode,
            "freeze_input_sha256": freeze_identity,
        }
        atomic_write_json(_state_path(recovery_root), state)
        _failpoint("after_submission_staged", failpoint)
        def retain(name, raw):
            path = attempt_dir / name
            if path.exists() and path.read_bytes() != raw:
                raise RecoveryError("staged_attempt_mismatch", "retained submission artifact differs", artifact=name)
            if not path.exists():
                atomic_write_bytes(path, raw)
        retain("preamble_body.md", preamble_bytes)
        retain("input_body.md", input_bytes)
        frozen_binding = None
        try:
            spans = scan_instruction(source)
            if submission_mode in {"frozen", "frozen-boundary-v1"}:
                files, restoration, candidate = frozen_submission.derive(source, preamble_bytes, input_bytes, freeze_inputs)
                for name, raw in files.items():
                    retain(name, raw)
                restore_raw = _json_bytes(restoration)
                retain("restore.json", restore_raw)
                frozen_binding = {"schema_version": frozen_submission.SCHEMA, "restore_sha256": sha256_bytes(restore_raw)}
                _failpoint("after_frozen_restoration", failpoint)
                if not restoration["restored"]:
                    raise InstructionSpanError("frozen_span_invalid", "frozen span restoration failed", errors=restoration["errors"], drift=restoration["drift"])
            else:
                candidate = assemble_instruction(source, spans, preamble_body, input_body)
            validator = _load_validator()
            answer_key = _verified_answer_key(recovery_root, state)
            report = validator.validate(source, candidate, answer_key)
        except InstructionSpanError as exc:
            report = {
                "valid": False,
                "reason_codes": [exc.reason_code],
                "error": str(exc),
                "details": exc.details,
            }
            candidate = b""
        except Exception as exc:
            validator = _load_validator()
            if isinstance(exc, validator.RewriteValidationError):
                report = exc.as_report()
                candidate = locals().get("candidate", b"")
                report.update(validator.validation_byte_context(source, candidate))
            else:
                raise

        if candidate:
            atomic_write_bytes(attempt_dir / "candidate.md", candidate)
        report.update(
            {
                "attempt": attempt_number,
                "source_sha256": state["source_sha256"],
                "preamble_submission_sha256": preamble_hash,
                "input_submission_sha256": input_hash,
            }
        )
        report["submission_mode"] = submission_mode
        if frozen_binding is not None:
            report["frozen_submission"] = frozen_binding
        report.setdefault(
            "source_spans",
            _load_json(recovery_root / "spans.json").get("spans"),
        )
        atomic_write_json(attempt_dir / "validation.json", report)
        atomic_write_json(recovery_root / "validation.json", report)
        _failpoint("after_submission_validation", failpoint)
        state["attempts"].append(
            {
                "attempt": attempt_number,
                "valid": bool(report.get("valid")),
                "reason_codes": report.get("reason_codes", []),
                "preamble_submission_sha256": preamble_hash,
                "input_submission_sha256": input_hash,
                "candidate_sha256": (
                    sha256_bytes(candidate) if candidate else None
                ),
                "submission_mode": submission_mode,
                **({"frozen_submission": frozen_binding, "validation_sha256": sha256_bytes(_json_bytes(report))} if frozen_binding else {}),
            }
        )
        state.pop("staged_attempt", None)
        if report.get("valid"):
            state["status"] = "validated"
            state["next_attempt"] = None
            state["final_instruction_sha256"] = sha256_bytes(candidate)
            state["final_candidate"] = str(
                (attempt_dir / "candidate.md").relative_to(recovery_root)
            )
            state.pop("accepted_semantic_review", None)
        else:
            reason_codes = set(report.get("reason_codes") or [])
            retryable = bool(reason_codes) and reason_codes <= RETRYABLE_REASON_CODES
            if attempt_number < MAX_ATTEMPTS and retryable:
                state["status"] = "retry_ready"
                state["next_attempt"] = attempt_number + 1
            elif attempt_number >= MAX_ATTEMPTS:
                state["status"] = "exhausted"
                state["next_attempt"] = None
            else:
                state["status"] = "failed"
                state["next_attempt"] = None
        state["updated_at_ns"] = time.time_ns()
        atomic_write_json(_state_path(recovery_root), state)
        report["state"] = state["status"]
        return report


def reject_semantic_review(
    recovery_root: Path,
    *,
    reason_code: str = "semantic_mismatch",
    message: str,
) -> dict[str, Any]:
    """Record an independent semantic rejection and reopen only attempt two."""
    if reason_code not in RETRYABLE_REASON_CODES:
        raise RecoveryError(
            "non_retryable_review_reason",
            "semantic review reason is not retryable",
            reason_code=reason_code,
        )
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        _verified_budget(recovery_root, state)
        if state["status"] != "validated":
            raise RecoveryError(
                "candidate_not_validated",
                "semantic rejection requires a validated candidate",
                state=state["status"],
            )
        attempt_number = state["attempts"][-1]["attempt"]
        review = {
            "version": RECOVERY_VERSION,
            "accepted": False,
            "attempt": attempt_number,
            "reason_codes": [reason_code],
            "message": message,
            "source_sha256": state["source_sha256"],
            "candidate_sha256": state["final_instruction_sha256"],
        }
        attempt_dir = recovery_root / ("attempt-%02d" % attempt_number)
        atomic_write_json(attempt_dir / "semantic-review.json", review)
        state["semantic_reviews"] = [
            *(state.get("semantic_reviews") or []),
            review,
        ]
        state.pop("final_candidate", None)
        state.pop("accepted_semantic_review", None)
        state["final_instruction_sha256"] = None
        if attempt_number < MAX_ATTEMPTS:
            state["status"] = "retry_ready"
            state["next_attempt"] = attempt_number + 1
        else:
            state["status"] = "exhausted"
            state["next_attempt"] = None
        state["updated_at_ns"] = time.time_ns()
        atomic_write_json(_state_path(recovery_root), state)
        terminal = {
            "valid": False,
            "applied": False,
            "state": state["status"],
            **review,
        }
        atomic_write_json(recovery_root / "validation.json", terminal)
        return terminal


def accept_semantic_review(
    recovery_root: Path,
    *,
    message: str,
    reviewer: str = "independent-reviewer",
) -> dict[str, Any]:
    """Bind an explicit clause-by-clause approval to the selected candidate."""
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        _verified_budget(recovery_root, state)
        if state.get("span_restoration"):
            raise RecoveryError("restoration_semantic_applicability_required", "use explicit prior-review applicability for a restored candidate")
        if state["status"] != "validated":
            raise RecoveryError(
                "candidate_not_validated",
                "semantic approval requires a validated candidate",
                state=state["status"],
            )
        current = state.get("accepted_semantic_review")
        if current is not None:
            if (
                current.get("accepted") is True
                and current.get("candidate_sha256")
                == state.get("final_instruction_sha256")
                and current.get("source_sha256") == state["source_sha256"]
            ):
                return current
            raise RecoveryError(
                "semantic_review_mismatch",
                "existing semantic approval is not bound to the selected candidate",
            )
        attempt_number = state["attempts"][-1]["attempt"]
        review = {
            "version": RECOVERY_VERSION,
            "accepted": True,
            "attempt": attempt_number,
            "reviewer": reviewer,
            "message": message,
            "source_sha256": state["source_sha256"],
            "candidate_sha256": state["final_instruction_sha256"],
        }
        attempt_dir = recovery_root / ("attempt-%02d" % attempt_number)
        atomic_write_json(attempt_dir / "semantic-review.json", review)
        state["semantic_reviews"] = [
            *(state.get("semantic_reviews") or []),
            review,
        ]
        state["accepted_semantic_review"] = review
        state["updated_at_ns"] = time.time_ns()
        atomic_write_json(_state_path(recovery_root), state)
        return review


def _apply_recovery_without_target_lock(
    recovery_root: Path,
    *,
    instruction_path: Path | None = None,
    task_toml_path: Path | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        transaction_root = recovery_root / "apply-transaction"
        source = _verified_snapshot(recovery_root, state)
        instruction = (
            instruction_path.resolve()
            if instruction_path is not None
            else Path(state["instruction_path"])
        )
        configured_task = state.get("task_toml_path")
        task_toml = (
            task_toml_path.resolve()
            if task_toml_path is not None
            else (Path(configured_task) if configured_task else None)
        )
        if task_toml is None:
            raise RecoveryError("missing_task_toml", "no task.toml path was configured")
        recovered = recover_transaction(
            transaction_root,
            expected_instruction_path=instruction,
            expected_task_toml_path=task_toml,
        )
        if "final_candidate" not in state:
            raise RecoveryError(
                "candidate_not_validated",
                "recovery state has no validated candidate",
                state=state["status"],
            )
        candidate_path = recovery_root / state["final_candidate"]
        candidate = candidate_path.read_bytes()
        candidate_hash = sha256_bytes(candidate)
        if candidate_hash != state["final_instruction_sha256"]:
            raise RecoveryError("candidate_hash_mismatch", "validated candidate changed")
        accepted_review = state.get("accepted_semantic_review") or {}
        if (
            accepted_review.get("accepted") is not True
            or accepted_review.get("candidate_sha256") != candidate_hash
            or accepted_review.get("source_sha256") != state["source_sha256"]
        ):
            raise RecoveryError(
                "semantic_review_required",
                "apply requires semantic approval bound to the selected candidate",
            )
        _verified_semantic_review(recovery_root, state)
        if state["status"] == "applied":
            instruction_hash = sha256_bytes(instruction.read_bytes())
            task_hash = sha256_bytes(task_toml.read_bytes())
            if instruction_hash != candidate_hash or (
                state.get("task_toml_sha256")
                and task_hash != state["task_toml_sha256"]
            ):
                raise RecoveryError(
                    "applied_target_drift",
                    "applied instruction or metadata changed after commit",
                )
            metadata = verify_naturalizer_metadata(instruction, task_toml)
            if (
                metadata["source_sha256"] != state["source_sha256"]
                or recovered.get("committed") is not True
            ):
                raise RecoveryError(
                    "applied_state_mismatch",
                    "applied state lacks matching metadata or committed journal",
                )
            return _reconcile_applied_report(recovery_root, state, recovered)
        if state["status"] != "validated":
            raise RecoveryError(
                "candidate_not_validated",
                "apply requires a validated naturalization candidate",
                state=state["status"],
            )
        if recovered.get("committed"):
            if sha256_bytes(instruction.read_bytes()) != candidate_hash:
                raise RecoveryError(
                    "committed_transaction_drift",
                    "committed instruction does not match the validated candidate",
                )
            state["status"] = "applied"
            state["applied_at_ns"] = time.time_ns()
            state["task_toml_sha256"] = sha256_bytes(task_toml.read_bytes())
            atomic_write_json(_state_path(recovery_root), state)
            verify_naturalizer_metadata(instruction, task_toml)
            return _reconcile_applied_report(recovery_root, state, recovered)
        _check_live_source(state)

        validator = _load_validator()
        answer_key = _verified_answer_key(recovery_root, state)
        report = validator.validate(source, candidate, answer_key)
        task_bytes = task_toml.read_bytes()
        task_text = task_bytes.decode("utf-8")
        new_task = validator.updated_task_toml(
            task_text, report, _attempts_used(state),
            endpoint=state.get("endpoint", "cursor-subagent"),
        ).encode("utf-8")
        applied = journaled_apply_bytes(
            instruction,
            candidate,
            task_toml,
            new_task,
            expected_instruction_sha256=state["source_sha256"],
            expected_task_sha256=sha256_bytes(task_bytes),
            transaction_root=transaction_root,
            failpoint=failpoint,
            lock_held=True,
            target_lock_held=True,
        )
        state["status"] = "applied"
        state["applied_at_ns"] = time.time_ns()
        state["instruction_path"] = str(instruction)
        state["task_toml_path"] = str(task_toml)
        state["final_instruction_sha256"] = candidate_hash
        state["task_toml_sha256"] = sha256_bytes(task_toml.read_bytes())
        atomic_write_json(_state_path(recovery_root), state)
        final_report = {
            **report,
            **applied,
            "state": "applied",
            "instruction": str(instruction),
            "task_toml": str(task_toml),
        }
        atomic_write_json(recovery_root / "validation.json", final_report)
        return final_report


def apply_recovery(
    recovery_root: Path,
    *,
    instruction_path: Path | None = None,
    task_toml_path: Path | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    state = _read_state(recovery_root)
    instruction = (
        instruction_path.resolve()
        if instruction_path is not None
        else Path(state["instruction_path"])
    )
    configured_task = state.get("task_toml_path")
    task_toml = (
        task_toml_path.resolve()
        if task_toml_path is not None
        else (Path(configured_task) if configured_task else None)
    )
    if task_toml is None:
        raise RecoveryError("missing_task_toml", "no task.toml path was configured")
    with target_pair_lock(instruction, task_toml):
        return _apply_recovery_without_target_lock(
            recovery_root,
            instruction_path=instruction,
            task_toml_path=task_toml,
            failpoint=failpoint,
        )


def verify_applied_recovery(recovery_root: Path) -> dict[str, Any]:
    initial = _read_state(recovery_root)
    instruction = Path(initial["instruction_path"])
    configured_task = initial.get("task_toml_path")
    if not configured_task:
        raise RecoveryError("missing_task_toml", "no task.toml path was configured")
    task_toml = Path(configured_task)
    with target_pair_lock(instruction, task_toml):
        with recovery_lock(recovery_root):
            state = _read_state(recovery_root)
            _verified_snapshot(recovery_root, state)
            _verified_answer_key(recovery_root, state)
            recovered = recover_transaction(
                recovery_root / "apply-transaction",
                expected_instruction_path=instruction,
                expected_task_toml_path=task_toml,
            )
            if state["status"] == "validated" and recovered.get("committed"):
                state["status"] = "applied"
                state["applied_at_ns"] = time.time_ns()
                state["task_toml_sha256"] = sha256_bytes(task_toml.read_bytes())
                atomic_write_json(_state_path(recovery_root), state)
            if state["status"] != "applied" or recovered.get("committed") is not True:
                raise RecoveryError(
                    "naturalization_not_applied",
                    "naturalization does not have committed applied state",
                    state=state["status"],
                )
            candidate = recovery_root / state.get("final_candidate", "")
            if (
                not candidate.is_file()
                or sha256_bytes(candidate.read_bytes())
                != state.get("final_instruction_sha256")
                or sha256_bytes(instruction.read_bytes())
                != state.get("final_instruction_sha256")
            ):
                raise RecoveryError(
                    "applied_target_drift",
                    "live instruction does not match the selected candidate",
                )
            _verified_semantic_review(recovery_root, state)
            metadata = verify_naturalizer_metadata(instruction, task_toml)
            if (
                metadata["source_sha256"] != state["source_sha256"]
                or metadata["task_toml_sha256"] != state.get("task_toml_sha256")
                or metadata["endpoint"] != state.get("endpoint", "cursor-subagent")
            ):
                raise RecoveryError(
                    "applied_metadata_drift",
                    "recovery state and task metadata do not match",
                )
            report = _reconcile_applied_report(recovery_root, state, recovered)
            return {
                **state,
                "valid": True,
                "applied": True,
                "metadata": metadata,
                "validation_report_sha256": sha256_bytes(
                    (recovery_root / "validation.json").read_bytes()
                ),
                "transaction_recovery": recovered,
                "report": report,
            }


def recovery_status(recovery_root: Path) -> dict[str, Any]:
    state = _read_state(recovery_root)
    transaction = recovery_root / "apply-transaction" / "journal.json"
    recovery_outcome = {"recovered": False}
    if state["status"] == "applied" or transaction.is_file():
        try:
            return verify_applied_recovery(recovery_root)
        except RecoveryError as exc:
            if state["status"] == "applied":
                raise
            if exc.reason_code not in {"naturalization_not_applied"}:
                raise
            journal = _load_json(transaction)
            if journal.get("phase") == "rolled_back":
                recovery_outcome = {"recovered": True, "rolled_back": True}
    with recovery_lock(recovery_root):
        state = _read_state(recovery_root)
        _verified_snapshot(recovery_root, state)
        _verified_answer_key(recovery_root, state)
        _check_live_source(state)
        return {**state, "transaction_recovery": recovery_outcome}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recoverable instruction naturalization")
    commands = parser.add_subparsers(dest="command", required=True)
    endpoint_parser = commands.add_parser("bind-endpoint")
    endpoint_parser.add_argument("state_dir", type=Path)
    endpoint_parser.add_argument("--endpoint", required=True,
                                 choices=("cursor-subagent", "https://litellm.labelbox.com"))

    init_parser = commands.add_parser("init")
    init_parser.add_argument("source", type=Path)
    init_parser.add_argument("--state-dir", type=Path, required=True)
    init_parser.add_argument("--instruction", type=Path)
    init_parser.add_argument("--task-toml", type=Path)
    init_parser.add_argument("--answer-key", type=Path)

    for name in ("submit", "submit-frozen", "submit-frozen-boundary"):
        submit_parser = commands.add_parser(name)
        submit_parser.add_argument("state_dir", type=Path)
        submit_parser.add_argument("--preamble", type=Path, required=True)
        submit_parser.add_argument("--input", dest="input_body", type=Path, required=True)

    inherit_parser = commands.add_parser("inherit-budget")
    inherit_parser.add_argument("state_dir", type=Path)
    inherit_parser.add_argument("--previous-state-dir", type=Path, required=True)
    inherit_parser.add_argument("--previous-state-sha256", required=True)
    inherit_parser.add_argument("--reason", required=True)

    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("state_dir", type=Path)
    apply_parser.add_argument("--instruction", type=Path)
    apply_parser.add_argument("--task-toml", type=Path)

    reject_parser = commands.add_parser("reject")
    reject_parser.add_argument("state_dir", type=Path)
    reject_parser.add_argument("--reason-code", default="semantic_mismatch")
    reject_parser.add_argument("--message", required=True)

    accept_parser = commands.add_parser("accept")
    accept_parser.add_argument("state_dir", type=Path)
    accept_parser.add_argument("--message", required=True)
    accept_parser.add_argument("--reviewer", default="independent-reviewer")

    status_parser = commands.add_parser("status")
    status_parser.add_argument("state_dir", type=Path)
    verify_applied_parser = commands.add_parser("verify-applied")
    verify_applied_parser.add_argument("state_dir", type=Path)
    verify_metadata_parser = commands.add_parser("verify-metadata")
    verify_metadata_parser.add_argument("--instruction", type=Path, required=True)
    verify_metadata_parser.add_argument("--task-toml", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "bind-endpoint":
            result = bind_endpoint(args.state_dir, args.endpoint)
        elif args.command == "init":
            result = init_recovery(
                args.source,
                args.state_dir,
                instruction_path=args.instruction,
                task_toml_path=args.task_toml,
                answer_key_path=args.answer_key,
            )
        elif args.command in {"submit", "submit-frozen", "submit-frozen-boundary"}:
            result = submit_attempt(
                args.state_dir,
                args.preamble.read_bytes(),
                args.input_body.read_bytes(),
                submission_mode={"submit":"plain", "submit-frozen":"frozen", "submit-frozen-boundary":"frozen-boundary-v1"}[args.command],
            )
        elif args.command == "inherit-budget":
            result = inherit_attempt_budget(
                args.state_dir, args.previous_state_dir,
                previous_state_sha256=args.previous_state_sha256, reason=args.reason,
            )
        elif args.command == "apply":
            result = apply_recovery(
                args.state_dir,
                instruction_path=args.instruction,
                task_toml_path=args.task_toml,
            )
        elif args.command == "reject":
            result = reject_semantic_review(
                args.state_dir,
                reason_code=args.reason_code,
                message=args.message,
            )
        elif args.command == "accept":
            result = accept_semantic_review(
                args.state_dir,
                message=args.message,
                reviewer=args.reviewer,
            )
        elif args.command == "verify-applied":
            result = verify_applied_recovery(args.state_dir)
        elif args.command == "verify-metadata":
            result = verify_naturalizer_metadata(
                args.instruction,
                args.task_toml,
            )
        else:
            result = recovery_status(args.state_dir)
    except (RecoveryError, InstructionSpanError) as exc:
        if isinstance(exc, InstructionSpanError):
            result = exc.as_dict()
            result["valid"] = False
            result["reason_codes"] = [exc.reason_code]
        else:
            result = exc.as_dict()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command in {"submit", "submit-frozen", "submit-frozen-boundary"} and not result.get("valid"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
