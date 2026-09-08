#!/usr/bin/env python3
"""Two-attempt, whole-document prose rewrite with crash recovery.

Same shape as naturalize_recovery.py, but the writer submits one body
(`submit --body`), the validator is heading-free, and metadata lands in
`[metadata.prose_instruction]`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import validate_prose_rewrite as validator  # noqa: E402
from extract_critical_facts import (  # noqa: E402
    FactsError,
    extract_facts,
    writer_brief,
)
from recovery_lib import (  # noqa: E402
    RecoveryError,
    atomic_write_bytes,
    atomic_write_json,
    journaled_apply_bytes,
    load_json,
    recover_transaction,
    recovery_lock,
    sha256_bytes,
    target_pair_lock,
)

RECOVERY_VERSION = "prose-recovery-v1"
LOCK_DIR_NAME = ".prose-target-locks"
FAILPOINT_ENV = "PROSE_FAILPOINT"
MAX_ATTEMPTS = 2
SKILL_NAME = "gdpval-prose-instruction"
DIALOGUE_MARKER = Path("tests") / "dialogue-applied.json"
RETRYABLE_REASON_CODES = frozenset(
    {
        "answer_value_leak",
        "assumptions_pointer_lost",
        "banned_framing",
        "candidate_structure_invalid",
        "candidate_too_short",
        "cell_refs_changed",
        "deliverable_not_last",
        "disclosure_markers_invalid",
        "final_newline_changed",
        "heading_present",
        "invalid_utf8",
        "named_output_lost",
        "persona_title_invalid",
        "protected_block_duplicated",
        "protected_block_missing",
        "protected_block_order_changed",
        "protected_construct_order_changed",
        "replacement_contains_bom",
        "semantic_anchor_lost",
        "semantic_mismatch",
        "source_category_lost",
        "template_placeholder",
        "title_not_in_notes",
        "token_mismatch",
        "unsupported_inline_code",
        "validation_constraint_failed",
    }
)


def _state_path(root: Path) -> Path:
    return root / "state.json"


def _source_path(root: Path) -> Path:
    return root / "source.snapshot.md"


def _facts_path(root: Path) -> Path:
    return root / "critical_facts.json"


def _read_state(root: Path) -> dict[str, Any]:
    state = load_json(_state_path(root))
    if state.get("version") != RECOVERY_VERSION:
        raise RecoveryError("invalid_recovery_state", "unsupported recovery state version")
    return state


def _verified_snapshot(root: Path, state: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    source = _source_path(root).read_bytes()
    actual = sha256_bytes(source)
    if actual != state["source_sha256"]:
        raise RecoveryError(
            "snapshot_hash_mismatch",
            "code-owned source snapshot changed",
            expected_sha256=state["source_sha256"],
            actual_sha256=actual,
        )
    facts_bytes = _facts_path(root).read_bytes()
    if sha256_bytes(facts_bytes) != state["facts_sha256"]:
        raise RecoveryError("facts_hash_mismatch", "critical facts manifest changed")
    return source, json.loads(facts_bytes.decode("utf-8"))


def _verified_answer_key(root: Path, state: dict[str, Any]) -> dict | None:
    expected = state.get("answer_key_sha256")
    snapshot = root / "answer_key.snapshot.json"
    if expected is None:
        if snapshot.exists():
            raise RecoveryError(
                "unexpected_answer_key_snapshot", "unbound answer-key snapshot is present"
            )
        return None
    if not snapshot.is_file():
        raise RecoveryError("answer_key_snapshot_missing", "bound answer-key snapshot is missing")
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
            "answer_key_snapshot_invalid", "bound answer-key snapshot is invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise RecoveryError("answer_key_snapshot_invalid", "answer-key snapshot must be an object")
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
        or load_json(review_path) != accepted
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


def verify_prose_metadata(instruction_path: Path, task_toml_path: Path) -> dict[str, Any]:
    instruction = instruction_path.read_bytes()
    task_bytes = task_toml_path.read_bytes()
    try:
        parsed = tomllib.loads(task_bytes.decode("utf-8"))
        metadata = parsed["metadata"]["prose_instruction"]
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise RecoveryError(
            "invalid_prose_metadata",
            "task.toml has no valid metadata.prose_instruction table",
        ) from exc
    instruction_hash = sha256_bytes(instruction)
    required = {
        "model": validator.MODEL,
        "endpoint": "cursor-subagent",
        "prompt_version": validator.PROMPT_VERSION,
        "heading_free": True,
    }
    failures = [key for key, expected in required.items() if metadata.get(key) != expected]
    attempts = metadata.get("attempts")
    if not isinstance(attempts, int) or not 1 <= attempts <= MAX_ATTEMPTS:
        failures.append("attempts")
    if metadata.get("instruction_sha256") != instruction_hash:
        failures.append("instruction_sha256")
    source_hash = metadata.get("source_sha256")
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        failures.append("source_sha256")
    text = instruction.decode("utf-8-sig", errors="replace")
    # Fence-aware, like the validator: a `# comment` inside a code block is
    # not a heading.
    try:
        if any(line["kind"] == "heading" for line in validator.scan_lines(text)):
            failures.append("heading_free")
    except validator.FactsError:
        failures.append("heading_free")
    if failures:
        raise RecoveryError(
            "prose_metadata_mismatch",
            "prose metadata does not match live instruction",
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
    root: Path, state: dict[str, Any], recovered: dict[str, Any]
) -> dict[str, Any]:
    attempt_report = load_json(
        root / ("attempt-%02d" % state["attempts"][-1]["attempt"]) / "validation.json"
    )
    final_report = {
        **attempt_report,
        "valid": True,
        "applied": True,
        "idempotent": True,
        "state": "applied",
        **recovered,
    }
    atomic_write_json(root / "validation.json", final_report)
    return final_report


def _task_dir_for(instruction_path: Path) -> Path:
    return instruction_path.resolve().parent


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
    task_dir = _task_dir_for(instruction_path)
    with recovery_lock(recovery_root, label="prose rewrite"):
        if _state_path(recovery_root).exists():
            state = _read_state(recovery_root)
            _verified_snapshot(recovery_root, state)
            _verified_answer_key(recovery_root, state)
            configured_instruction = Path(state["instruction_path"]).resolve()
            configured_task = (
                Path(state["task_toml_path"]).resolve() if state.get("task_toml_path") else None
            )
            if configured_instruction != instruction_path or (
                task_toml_path is not None and configured_task != task_toml_path.resolve()
            ):
                raise RecoveryError(
                    "recovery_target_mismatch",
                    "existing recovery targets different staged files",
                )
            if state["status"] == "applied":
                journal = load_json(recovery_root / "apply-transaction" / "journal.json")
                if (
                    journal.get("version") != RECOVERY_VERSION
                    or journal.get("phase") != "committed"
                    or sha256_bytes(instruction_path.read_bytes())
                    != state["final_instruction_sha256"]
                    or configured_task is None
                    or sha256_bytes(configured_task.read_bytes()) != state.get("task_toml_sha256")
                ):
                    raise RecoveryError(
                        "applied_target_drift",
                        "applied recovery no longer matches its staged targets",
                    )
                metadata = verify_prose_metadata(instruction_path, configured_task)
                if metadata["source_sha256"] != state["source_sha256"]:
                    raise RecoveryError(
                        "applied_metadata_drift",
                        "applied metadata no longer matches recovery source",
                    )
            else:
                _check_live_source(state)
            return state

        source = source_path.read_bytes()
        try:
            facts = extract_facts(source, task_dir)
        except FactsError as exc:
            raise RecoveryError(exc.reason_code, str(exc), **exc.details) from exc
        facts_bytes = (json.dumps(facts, indent=2, sort_keys=True) + "\n").encode("utf-8")
        atomic_write_bytes(_source_path(recovery_root), source, mode=0o400)
        atomic_write_bytes(_facts_path(recovery_root), facts_bytes, mode=0o400)
        atomic_write_bytes(
            recovery_root / "writer_brief.md", writer_brief(facts).encode("utf-8"), mode=0o400
        )
        for number in range(1, MAX_ATTEMPTS + 1):
            attempt_dir = recovery_root / ("attempt-%02d" % number)
            attempt_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(attempt_dir / "source.snapshot.md", source, mode=0o400)

        answer_key_sha256 = None
        if answer_key_path is not None:
            answer_bytes = answer_key_path.resolve().read_bytes()
            json.loads(answer_bytes.decode("utf-8"))
            atomic_write_bytes(
                recovery_root / "answer_key.snapshot.json", answer_bytes, mode=0o400
            )
            answer_key_sha256 = sha256_bytes(answer_bytes)

        state: dict[str, Any] = {
            "version": RECOVERY_VERSION,
            "skill": SKILL_NAME,
            "status": "initialized",
            "mode": facts["mode"],
            "source_path": str(source_path),
            "instruction_path": str(instruction_path),
            "task_dir": str(task_dir),
            "task_toml_path": (
                str(task_toml_path.resolve()) if task_toml_path is not None else None
            ),
            "answer_key_sha256": answer_key_sha256,
            "source_sha256": facts["source_sha256"],
            "facts_sha256": sha256_bytes(facts_bytes),
            "attempt_limit": MAX_ATTEMPTS,
            "attempts": [],
            "next_attempt": 1,
            "final_instruction_sha256": None,
            "created_at_ns": time.time_ns(),
        }
        atomic_write_json(_state_path(recovery_root), state)
        return state


def submit_attempt(recovery_root: Path, body: bytes | str) -> dict[str, Any]:
    with recovery_lock(recovery_root, label="prose rewrite"):
        state = _read_state(recovery_root)
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        body_hash = sha256_bytes(body_bytes)
        if state["attempts"] and state["status"] != "applied" and (
            state["status"] != "retry_ready" or not state.get("semantic_reviews")
        ):
            previous_number = state["attempts"][-1]["attempt"]
            previous_report = load_json(
                recovery_root / ("attempt-%02d" % previous_number) / "validation.json"
            )
            if previous_report.get("body_submission_sha256") == body_hash:
                previous_report["state"] = state["status"]
                previous_report["idempotent"] = True
                return previous_report
        if state["status"] == "applied":
            raise RecoveryError("already_applied", "prose rewrite is already applied")
        if state["status"] in {"exhausted", "failed"}:
            raise RecoveryError(
                "terminal_state", "prose rewrite is in terminal state %s" % state["status"]
            )
        if state["status"] == "validated":
            raise RecoveryError("already_validated", "a valid candidate already exists")
        _check_live_source(state)
        source, facts = _verified_snapshot(recovery_root, state)
        attempt_number = len(state["attempts"]) + 1
        if attempt_number > MAX_ATTEMPTS:
            state["status"] = "exhausted"
            state["next_attempt"] = None
            atomic_write_json(_state_path(recovery_root), state)
            raise RecoveryError("attempts_exhausted", "both prose attempts are used")

        attempt_dir = recovery_root / ("attempt-%02d" % attempt_number)
        staged = state.get("staged_attempt")
        if staged is not None and (
            staged.get("attempt") != attempt_number
            or staged.get("body_submission_sha256") != body_hash
        ):
            raise RecoveryError(
                "staged_attempt_mismatch",
                "a different submission is already staged for this attempt",
            )
        atomic_write_bytes(attempt_dir / "body.md", body_bytes)
        state["status"] = "staged"
        state["staged_attempt"] = {
            "attempt": attempt_number,
            "source_sha256": state["source_sha256"],
            "body_submission_sha256": body_hash,
        }
        atomic_write_json(_state_path(recovery_root), state)

        candidate = body_bytes
        try:
            answer_key = _verified_answer_key(recovery_root, state)
            report = validator.validate(
                source,
                candidate,
                answer_key,
                task_dir=Path(state["task_dir"]),
                facts=facts,
            )
        except validator.ProseValidationError as exc:
            report = exc.as_report()

        atomic_write_bytes(attempt_dir / "candidate.md", candidate)
        report.update(
            {
                "attempt": attempt_number,
                "source_sha256": state["source_sha256"],
                "candidate_sha256": sha256_bytes(candidate),
                "body_submission_sha256": body_hash,
            }
        )
        atomic_write_json(attempt_dir / "validation.json", report)
        atomic_write_json(recovery_root / "validation.json", report)
        state["attempts"].append(
            {
                "attempt": attempt_number,
                "valid": bool(report.get("valid")),
                "reason_codes": report.get("reason_codes", []),
                "body_submission_sha256": body_hash,
                "candidate_sha256": sha256_bytes(candidate),
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
    recovery_root: Path, *, reason_code: str = "semantic_mismatch", message: str
) -> dict[str, Any]:
    if reason_code not in RETRYABLE_REASON_CODES:
        raise RecoveryError(
            "non_retryable_review_reason",
            "semantic review reason is not retryable",
            reason_code=reason_code,
        )
    with recovery_lock(recovery_root, label="prose rewrite"):
        state = _read_state(recovery_root)
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
        atomic_write_json(
            recovery_root / ("attempt-%02d" % attempt_number) / "semantic-review.json", review
        )
        state["semantic_reviews"] = [*(state.get("semantic_reviews") or []), review]
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
        terminal = {"valid": False, "applied": False, "state": state["status"], **review}
        atomic_write_json(recovery_root / "validation.json", terminal)
        return terminal


def accept_semantic_review(
    recovery_root: Path, *, message: str, reviewer: str = "independent-reviewer"
) -> dict[str, Any]:
    with recovery_lock(recovery_root, label="prose rewrite"):
        state = _read_state(recovery_root)
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
                and current.get("candidate_sha256") == state.get("final_instruction_sha256")
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
        atomic_write_json(
            recovery_root / ("attempt-%02d" % attempt_number) / "semantic-review.json", review
        )
        state["semantic_reviews"] = [*(state.get("semantic_reviews") or []), review]
        state["accepted_semantic_review"] = review
        state["updated_at_ns"] = time.time_ns()
        atomic_write_json(_state_path(recovery_root), state)
        return review


def _refresh_dialogue_marker(task_dir: Path, instruction_hash: str) -> bool:
    """Point tests/dialogue-applied.json at the live instruction.

    Idempotent, and called from every path that can end in `applied` (fresh
    apply, replay of a committed journal, verify-applied promotion, and the
    applied-state reconcile), so an interruption between the journal commit and
    this write cannot leave the marker stale forever.
    """
    marker = task_dir / DIALOGUE_MARKER
    if not marker.is_file():
        return False
    payload = load_json(marker)
    do_not = list(payload.get("do_not_rerun") or [])
    if SKILL_NAME not in do_not:
        do_not.append(SKILL_NAME)
    if payload.get("instruction_sha256") == instruction_hash and payload.get("do_not_rerun") == do_not:
        return True
    payload["instruction_sha256"] = instruction_hash
    payload["do_not_rerun"] = do_not
    atomic_write_json(marker, payload)
    return True


def _apply_without_target_lock(
    recovery_root: Path,
    *,
    instruction_path: Path | None = None,
    task_toml_path: Path | None = None,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    with recovery_lock(recovery_root, label="prose rewrite"):
        state = _read_state(recovery_root)
        transaction_root = recovery_root / "apply-transaction"
        source, facts = _verified_snapshot(recovery_root, state)
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
            journal_version=RECOVERY_VERSION,
            failpoint_env=FAILPOINT_ENV,
            expected_instruction_path=instruction,
            expected_task_toml_path=task_toml,
        )
        if "final_candidate" not in state:
            raise RecoveryError(
                "candidate_not_validated",
                "recovery state has no validated candidate",
                state=state["status"],
            )
        candidate = (recovery_root / state["final_candidate"]).read_bytes()
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
                state.get("task_toml_sha256") and task_hash != state["task_toml_sha256"]
            ):
                raise RecoveryError(
                    "applied_target_drift", "applied instruction or metadata changed after commit"
                )
            metadata = verify_prose_metadata(instruction, task_toml)
            if metadata["source_sha256"] != state["source_sha256"] or (
                recovered.get("committed") is not True
            ):
                raise RecoveryError(
                    "applied_state_mismatch",
                    "applied state lacks matching metadata or committed journal",
                )
            # Heals a marker left stale by an interruption after the commit.
            _refresh_dialogue_marker(Path(state["task_dir"]), candidate_hash)
            return _reconcile_applied_report(recovery_root, state, recovered)
        if state["status"] != "validated":
            raise RecoveryError(
                "candidate_not_validated",
                "apply requires a validated prose candidate",
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
            verify_prose_metadata(instruction, task_toml)
            _refresh_dialogue_marker(Path(state["task_dir"]), candidate_hash)
            return _reconcile_applied_report(recovery_root, state, recovered)
        _check_live_source(state)

        answer_key = _verified_answer_key(recovery_root, state)
        report = validator.validate(
            source, candidate, answer_key, task_dir=Path(state["task_dir"]), facts=facts
        )
        task_bytes = task_toml.read_bytes()
        new_task = validator.updated_task_toml(
            task_bytes.decode("utf-8"), report, len(state["attempts"])
        ).encode("utf-8")
        applied = journaled_apply_bytes(
            instruction,
            candidate,
            task_toml,
            new_task,
            expected_instruction_sha256=state["source_sha256"],
            expected_task_sha256=sha256_bytes(task_bytes),
            transaction_root=transaction_root,
            journal_version=RECOVERY_VERSION,
            failpoint_env=FAILPOINT_ENV,
            lock_dir_name=LOCK_DIR_NAME,
            failpoint_callback=failpoint,
            lock_held=True,
            target_lock_held=True,
        )
        marker_refreshed = _refresh_dialogue_marker(Path(state["task_dir"]), candidate_hash)
        state["status"] = "applied"
        state["applied_at_ns"] = time.time_ns()
        state["instruction_path"] = str(instruction)
        state["task_toml_path"] = str(task_toml)
        state["final_instruction_sha256"] = candidate_hash
        state["task_toml_sha256"] = sha256_bytes(task_toml.read_bytes())
        state["dialogue_marker_refreshed"] = marker_refreshed
        atomic_write_json(_state_path(recovery_root), state)
        final_report = {
            **report,
            **applied,
            "state": "applied",
            "instruction": str(instruction),
            "task_toml": str(task_toml),
            "dialogue_marker_refreshed": marker_refreshed,
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
    with target_pair_lock(
        instruction, task_toml, lock_dir_name=LOCK_DIR_NAME, label="prose rewrite"
    ):
        return _apply_without_target_lock(
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
    with target_pair_lock(
        instruction, task_toml, lock_dir_name=LOCK_DIR_NAME, label="prose rewrite"
    ):
        with recovery_lock(recovery_root, label="prose rewrite"):
            state = _read_state(recovery_root)
            _verified_snapshot(recovery_root, state)
            _verified_answer_key(recovery_root, state)
            recovered = recover_transaction(
                recovery_root / "apply-transaction",
                journal_version=RECOVERY_VERSION,
                failpoint_env=FAILPOINT_ENV,
                expected_instruction_path=instruction,
                expected_task_toml_path=task_toml,
            )
            if state["status"] == "validated" and recovered.get("committed"):
                state["status"] = "applied"
                state["applied_at_ns"] = time.time_ns()
                state["task_toml_sha256"] = sha256_bytes(task_toml.read_bytes())
                atomic_write_json(_state_path(recovery_root), state)
            if state["status"] == "applied" and recovered.get("committed"):
                # The marker write sits outside the journal; finish it here if
                # the apply was interrupted between commit and marker refresh.
                state["dialogue_marker_refreshed"] = _refresh_dialogue_marker(
                    Path(state["task_dir"]), state.get("final_instruction_sha256", "")
                )
            if state["status"] != "applied" or recovered.get("committed") is not True:
                raise RecoveryError(
                    "prose_not_applied",
                    "prose rewrite does not have committed applied state",
                    state=state["status"],
                )
            candidate = recovery_root / state.get("final_candidate", "")
            if (
                not candidate.is_file()
                or sha256_bytes(candidate.read_bytes()) != state.get("final_instruction_sha256")
                or sha256_bytes(instruction.read_bytes()) != state.get("final_instruction_sha256")
            ):
                raise RecoveryError(
                    "applied_target_drift", "live instruction does not match the selected candidate"
                )
            _verified_semantic_review(recovery_root, state)
            metadata = verify_prose_metadata(instruction, task_toml)
            if metadata["source_sha256"] != state["source_sha256"] or (
                metadata["task_toml_sha256"] != state.get("task_toml_sha256")
            ):
                raise RecoveryError(
                    "applied_metadata_drift", "recovery state and task metadata do not match"
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
    recovery_outcome: dict[str, Any] = {"recovered": False}
    if state["status"] == "applied" or transaction.is_file():
        try:
            return verify_applied_recovery(recovery_root)
        except RecoveryError as exc:
            if state["status"] == "applied" or exc.reason_code != "prose_not_applied":
                raise
            journal = load_json(transaction)
            if journal.get("phase") == "rolled_back":
                recovery_outcome = {"recovered": True, "rolled_back": True}
    with recovery_lock(recovery_root, label="prose rewrite"):
        state = _read_state(recovery_root)
        _verified_snapshot(recovery_root, state)
        _verified_answer_key(recovery_root, state)
        _check_live_source(state)
        return {**state, "transaction_recovery": recovery_outcome}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recoverable heading-free prose rewrite")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init")
    init_parser.add_argument("source", type=Path)
    init_parser.add_argument("--state-dir", type=Path, required=True)
    init_parser.add_argument("--instruction", type=Path)
    init_parser.add_argument("--task-toml", type=Path)
    init_parser.add_argument("--answer-key", type=Path)

    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("state_dir", type=Path)
    submit_parser.add_argument("--body", type=Path, required=True)

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
    brief_parser = commands.add_parser("brief")
    brief_parser.add_argument("state_dir", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "init":
            result = init_recovery(
                args.source,
                args.state_dir,
                instruction_path=args.instruction,
                task_toml_path=args.task_toml,
                answer_key_path=args.answer_key,
            )
        elif args.command == "submit":
            result = submit_attempt(args.state_dir, args.body.read_bytes())
        elif args.command == "apply":
            result = apply_recovery(
                args.state_dir,
                instruction_path=args.instruction,
                task_toml_path=args.task_toml,
            )
        elif args.command == "reject":
            result = reject_semantic_review(
                args.state_dir, reason_code=args.reason_code, message=args.message
            )
        elif args.command == "accept":
            result = accept_semantic_review(
                args.state_dir, message=args.message, reviewer=args.reviewer
            )
        elif args.command == "verify-applied":
            result = verify_applied_recovery(args.state_dir)
        elif args.command == "verify-metadata":
            result = verify_prose_metadata(args.instruction, args.task_toml)
        elif args.command == "brief":
            print((args.state_dir / "writer_brief.md").read_text(encoding="utf-8"), end="")
            return 0
        else:
            result = recovery_status(args.state_dir)
    except RecoveryError as exc:
        print(json.dumps(exc.as_dict(), indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "submit" and not result.get("valid"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
