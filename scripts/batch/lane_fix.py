#!/usr/bin/env python3
"""Lane-local controller fix step.

The worker exits 3 when a row closed with a ``controller`` failure class: the
recovery worker or a shared batch module, not the task, is what broke. The
lane supervisor (scripts/batch/run_lane.sh) then runs this step before the
next cycle. It

1. gathers every controller failure in the lane's state (failure records,
   task evidence, investigator reports),
2. refuses if the same set of failure signatures has already been worked
   ``RECOVERY_LANE_FIX_MAX`` times (a fix that does not stick is a human's),
3. builds a scratch workspace: private copies of ``scripts/`` and ``tests/``
   plus symlinks to everything else in the baseline,
4. runs one Cursor agent in that workspace, under a memory-governor hold so
   it competes for host memory like any other agent,
5. accepts the result only if nothing outside ``scripts/batch/`` and
   ``tests/`` changed and the worker test suites pass in the workspace,
6. installs ``scripts/`` as the lane overlay at ``RECOVERY_STATE/overlay``.

The supervisor puts the overlay ahead of the baseline on PYTHONPATH. The
worker's build id hashes the modules it actually loaded, so rows parked as
``worker_fix_needed`` under the old build are released automatically.

The baseline repository is never written. Every fix is lane-local, logged
under ``RECOVERY_STATE/lane-fix/``, and reversible by deleting the overlay.
Fleet-wide adoption of a fix is a human decision made from the report.

Exit codes: 0 overlay installed; 1 attempt failed (agent, diff, or tests);
4 refused (nothing to fix, or the per-signature cap was reached).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

SCHEMA = "recovery-lane-fix/v1"
DEFAULT_TESTS = (
    "tests/test_recovery_worker.py",
    "tests/test_recovery_worker_amd_gate.py",
    "tests/test_recovery_worker_retention.py",
    "tests/test_memory_governor.py",
    "tests/test_batch_hardened_runner.py",
)
EDITABLE_PREFIXES = ("scripts/batch/", "tests/")
SKIP_TOP_LEVEL = {
    ".git",
    "runs",
    "__pycache__",
    ".pytest_cache",
    "deployment-artifacts",
    # Pipeline output trees a deployed baseline may carry; never code.
    "ast_out",
    "seg_out",
    "source_out",
    "inputs_out",
    "inputs_out_mcp",
    "release_out",
    "tasks_outputs",
    "tasks_outputs_mcp",
    "tasks_outputs_final",
    "staging",
}
# Large read-only data and the interpreter: linked, never copied, never edited.
SYMLINK_TOP_LEVEL = {"verification_manifests", ".venv"}
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 4
GIB = 1024**3


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _incident_report_text(state: Path, workbook_id: str, evidence: Mapping[str, object]) -> str:
    """The most relevant investigator report for a row, wherever the worker filed it.

    Prefers a report whose verdict routed to ``controller``; otherwise the
    newest completed one. Sources, in order: the evidence file's
    ``incident_report`` / ``worker_fix_incident`` / ``incident_reports``
    references, then ``state/incidents/<wb>/*/completed.json``.
    """

    candidates: list[tuple[int, str]] = []

    def consider(ref: object, weight: int) -> None:
        if not isinstance(ref, Mapping):
            return
        verdict = ref.get("verdict") if isinstance(ref.get("verdict"), Mapping) else {}
        bonus = 10 if verdict.get("route") == "controller" or ref.get("worker_fix_needed") is True else 0
        report_path = str(ref.get("report_path") or "")
        if report_path:
            candidates.append((weight + bonus, report_path))

    consider(evidence.get("incident_report"), 3)
    consider(evidence.get("worker_fix_incident"), 3)
    reports = evidence.get("incident_reports")
    if isinstance(reports, list):
        for index, ref in enumerate(reports):
            consider(ref, 1 + index)
    incidents_dir = state / "incidents" / workbook_id
    if incidents_dir.is_dir():
        for completed in sorted(incidents_dir.glob("*/completed.json"), key=lambda p: p.stat().st_mtime):
            consider(_read_json(completed), 0)
    for _weight, report_path in sorted(candidates, key=lambda item: -item[0]):
        path = Path(report_path)
        if path.is_file() and not path.is_symlink():
            try:
                return path.read_text(encoding="utf-8")[:6000]
            except OSError:
                continue
    return ""


def collect_controller_failures(state: Path) -> list[dict[str, object]]:
    """Every row whose latest failure record names the controller."""

    failures_dir = state / "failures"
    found: list[dict[str, object]] = []
    if not failures_dir.is_dir():
        return found
    for path in sorted(failures_dir.glob("*.json")):
        record = _read_json(path)
        if record.get("class") != "controller":
            continue
        if record.get("status") == "generated" or record.get("resolved_at"):
            # The row delivered since; nothing left to fix for it.
            continue
        workbook_id = str(record.get("workbook_id") or path.stem)
        evidence = _read_json(state / "task-evidence" / f"{workbook_id}.json")
        report_text = _incident_report_text(state, workbook_id, evidence)
        found.append(
            {
                "workbook_id": workbook_id,
                "signature": str(record.get("signature") or ""),
                "subclass": str(record.get("subclass") or ""),
                "error_type": str(record.get("error_type") or ""),
                "normalized_error": str(record.get("normalized_error") or ""),
                "error": str(record.get("error") or evidence.get("error") or "")[:1500],
                "occurrence_count": int(record.get("occurrence_count", 1) or 1),
                "failure_record": str(path),
                "task_evidence": str(state / "task-evidence" / f"{workbook_id}.json"),
                "incident_report": report_text,
                "inspect_error": str(evidence.get("inspect_error") or "")[:1500],
            }
        )
    return found


def fix_key(signatures: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(signatures))).encode("utf-8")).hexdigest()[:16]


def build_brief(failures: Sequence[Mapping[str, object]], *, work: Path, tests: Sequence[str]) -> str:
    lines = [
        "# Lane controller fix",
        "",
        "You are fixing the recovery worker, not a task. One or more rows in this",
        "lane closed with a controller-class failure: the worker, hardened runner,",
        "memory governor, or disk reaper misbehaved. The task worktrees are fine.",
        "",
        f"Workspace: {work}",
        "It is a private copy of the pipeline code (verification_manifests and",
        ".venv are read-only links). Edit ONLY files under scripts/batch/ and",
        "tests/. Any change outside those two trees is rejected and the whole fix",
        "is discarded.",
        "",
        "## Failures",
        "",
    ]
    for item in failures:
        lines += [
            f"### {item['workbook_id']}  subclass={item['subclass']}  x{item['occurrence_count']}",
            f"- error_type: {item['error_type'] or '(none)'}",
            f"- normalized: {item['normalized_error']}",
            f"- error: {item['error']}",
        ]
        if item.get("inspect_error"):
            lines.append(f"- inspect_error: {item['inspect_error']}")
        lines += [
            f"- failure record: {item['failure_record']}",
            f"- task evidence: {item['task_evidence']}",
        ]
        if item.get("incident_report"):
            lines += ["", "Investigator report:", "", "```", str(item["incident_report"]).rstrip(), "```"]
        lines.append("")
    lines += [
        "## Rules",
        "",
        "1. Read the failing code path first (scripts/batch/recovery_worker.py and",
        "   whichever module the traceback names). Reproduce with a unit test in",
        "   tests/ before changing behavior.",
        "2. Make the smallest change that removes the defect. Do not change the",
        "   worker's safety rules: no skipping rows, no marking anything generated",
        "   without a delivered bundle, no writes outside the lane's state and",
        "   work directories, no edits to protected pipeline files.",
        "3. Do not touch the queue file, the baseline repository, or any task",
        "   worktree. Do not run the worker against real state.",
        "4. Run the suites below from the workspace root with",
        f"   `PYTHONPATH={work} python -m pytest -q {' '.join(tests)}`",
        "   and finish only when they pass.",
        "5. End with one line: `LANE_FIX_SUMMARY: <what was wrong, what changed>`.",
        "",
        "There is no network and no human on the other end of this session.",
    ]
    return "\n".join(lines) + "\n"


def prepare_workspace(base: Path, work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for entry in sorted(base.iterdir()):
        if entry.name in SKIP_TOP_LEVEL:
            continue
        target = work / entry.name
        if entry.name in SYMLINK_TOP_LEVEL or entry.is_symlink():
            target.symlink_to(entry.resolve() if entry.exists() else os.readlink(entry))
        elif entry.is_dir():
            shutil.copytree(
                entry,
                target,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
        else:
            shutil.copy2(entry, target)
    # The baseline may be deployed read-only; the fix agent needs to write.
    for root, dirs, files in os.walk(work):
        os.chmod(root, 0o755)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                os.chmod(path, 0o755 if os.access(path, os.X_OK) else 0o644)


def tree_digests(root: Path, trees: Sequence[str] | None = None) -> dict[str, str]:
    """sha256 of every regular file under ``root`` (or the named subtrees)."""

    digests: dict[str, str] = {}
    tops = [root / tree for tree in trees] if trees else [root]
    for top in tops:
        if not top.is_dir() or top.is_symlink():
            continue
        # os.walk does not follow symlinked directories, so the linked
        # manifests and .venv stay out of the digest.
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [name for name in dirnames if name not in {"__pycache__", ".pytest_cache"}]
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink() or not path.is_file():
                    continue
                digests[str(path.relative_to(root))] = _sha256_file(path)
    return dict(sorted(digests.items()))


def changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def workspace_intact(base: Path, work: Path) -> list[str]:
    """Names of top-level entries that are no longer the symlinks we made."""

    broken: list[str] = []
    for entry in sorted(base.iterdir()):
        if entry.name not in SYMLINK_TOP_LEVEL:
            continue
        target = work / entry.name
        if not target.is_symlink() or target.resolve() != entry.resolve():
            broken.append(entry.name)
    return broken


def run_agent(
    *,
    agent: Path,
    model: str,
    work: Path,
    prompt: str,
    log_path: Path,
    timeout_seconds: float,
    hold: Sequence[str] | None,
    env: Mapping[str, str],
) -> dict[str, object]:
    command = [
        str(agent),
        "-p",
        "--force",
        "--trust",
        "--output-format",
        "text",
        "--model",
        model,
        "--workspace",
        str(work),
        prompt,
    ]
    if hold:
        command = [*hold, "--", *command]
    started = time.monotonic()
    with log_path.open("wb") as log:
        try:
            completed = subprocess.run(
                command,
                cwd=work,
                env=dict(env),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
            status = "completed"
            return_code: int | None = completed.returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
            return_code = None
        except OSError as error:
            status = "launch_error"
            return_code = None
            log.write(repr(error).encode("utf-8"))
    summary = ""
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("LANE_FIX_SUMMARY:"):
                summary = line[len("LANE_FIX_SUMMARY:"):].strip()
    except OSError:
        pass
    return {
        "status": status,
        "return_code": return_code,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "summary": summary,
        "log": str(log_path),
    }


def run_tests(work: Path, tests: Sequence[str], python: str, log_path: Path) -> bool:
    env = {**os.environ, "PYTHONPATH": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
    with log_path.open("wb") as log:
        compile_ok = subprocess.run(
            [python, "-m", "compileall", "-q", str(work / "scripts" / "batch")],
            cwd=work,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode == 0
        if not compile_ok:
            return False
        result = subprocess.run(
            [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            cwd=work,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return result.returncode == 0


def install_overlay(state: Path, work: Path, meta: Mapping[str, object]) -> Path:
    overlay = state / "overlay"
    staging = state / "overlay.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(
        work / "scripts",
        staging / "scripts",
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    _write_json(staging / "OVERLAY.json", dict(meta))
    if overlay.exists():
        previous = state / f"overlay.prev-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        os.replace(overlay, previous)
    os.replace(staging, overlay)
    return overlay


def guarded_digests(env: Mapping[str, str], state: Path, base: Path) -> dict[str, str]:
    """Digests of everything the fix agent must leave alone.

    The baseline tree (excluding the linked manifests, which are hashed by
    their own path list), ``code-baseline.json``, every task worktree under
    ``RECOVERY_WORK_ROOT``, and the lane's checkpoints. Large worktrees are
    hashed by (path, size, mtime_ns) rather than content so this stays
    seconds, not minutes; a write changes the mtime.
    """

    digests: dict[str, str] = {}
    for path, value in tree_digests(base).items():
        digests[f"base:{path}"] = value
    code_baseline = env.get("RECOVERY_CODE_BASELINE")
    if code_baseline and Path(code_baseline).is_file():
        digests["code_baseline"] = _sha256_file(Path(code_baseline))
    for label, root in (
        ("worktree", env.get("RECOVERY_WORK_ROOT")),
        ("checkpoints", str(state / "checkpoints")),
    ):
        if not root or not Path(root).is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in {"__pycache__", ".pytest_cache"}]
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                try:
                    stat_result = path.stat()
                except OSError:
                    continue
                digests[f"{label}:{path}"] = f"{stat_result.st_size}:{stat_result.st_mtime_ns}"
    return digests


def changed_guarded(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Guarded paths that changed, appeared or disappeared during the fix.

    New files under a worktree or checkpoints are ignored only when the
    worker itself could have written them; the worker is not running while
    the fix step runs, so any change here is the agent's.
    """

    return changed_paths(before, after)


def _host_lock(path: Path):
    """Blocking per-host lock so only one fix agent runs at a time."""

    import fcntl

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle
    except OSError:
        # A lock that cannot be taken (read-only /var/tmp on a dev box) must
        # not block the fix; the governor hold still bounds memory.
        return None


def _release_host_lock(handle) -> None:
    if handle is None:
        return
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _stop_lane_scopes(lane_id: str) -> None:
    """Best effort: stop any lingering harbor-* scope of this lane.

    The worker has exited when this runs, and hardened_runner kills its
    process groups, so normally there is nothing left. A stale scope would
    still hold memory the fix agent and the next cycle need.
    """

    if shutil.which("systemctl") is None:
        return
    pattern = "harbor-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in lane_id) + "-*"
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", pattern],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _append_history(state: Path, entry: Mapping[str, object]) -> None:
    path = state / "lane-fix" / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _alert(state: Path, payload: Mapping[str, object]) -> None:
    _write_json(state / "lane-fix" / "ALERT.json", {"at": _now(), **payload})


def main(argv: Sequence[str] | None = None) -> int:
    env = os.environ
    state = Path(env["RECOVERY_STATE"]).resolve()
    base = Path(env["RECOVERY_BASELINE_REPO"]).resolve()
    agent = Path(env.get("RECOVERY_AGENT") or Path.home() / ".local" / "bin" / "agent")
    model = env.get("RECOVERY_MODEL") or "gpt-5.6-sol-high"
    lane_id = env.get("RECOVERY_LANE_ID", "lane")
    python = env.get("RECOVERY_PYTHON") or sys.executable
    timeout_seconds = float(env.get("RECOVERY_LANE_FIX_TIMEOUT", "1800"))
    max_attempts = int(env.get("RECOVERY_LANE_FIX_MAX", "2"))
    hold_bytes = int(env.get("RECOVERY_LANE_FIX_BYTES", str(8 * GIB)))
    tests = tuple(
        item for item in env.get("RECOVERY_LANE_FIX_TESTS", " ".join(DEFAULT_TESTS)).split() if item
    )

    failures = collect_controller_failures(state)
    if not failures:
        print("LANE_FIX_REFUSED no controller failures recorded", flush=True)
        return EXIT_REFUSED
    signatures = [str(item["signature"]) for item in failures]
    key = fix_key(signatures)
    attempts_path = state / "lane-fix" / "attempts.json"
    attempts = _read_json(attempts_path)
    prior = int(attempts.get(key, 0) or 0)
    if prior >= max_attempts:
        _alert(
            state,
            {
                "kind": "lane_fix_cap",
                "lane_id": lane_id,
                "fix_key": key,
                "attempts": prior,
                "workbooks": [item["workbook_id"] for item in failures],
                "signatures": signatures,
                "message": (
                    f"{prior} lane fixes for this defect did not stick; rows stay "
                    "parked as worker_fix_needed until a human looks"
                ),
            },
        )
        print(f"LANE_FIX_REFUSED cap reached key={key} attempts={prior}", flush=True)
        return EXIT_REFUSED
    attempts[key] = prior + 1
    _write_json(attempts_path, attempts)

    fix_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{key}"
    root = state / "lane-fix" / fix_id
    root.mkdir(parents=True, exist_ok=True)
    work = root / "work"
    prepare_workspace(base, work)
    brief = build_brief(failures, work=work, tests=tests)
    (root / "brief.md").write_text(brief, encoding="utf-8")
    before = tree_digests(work)
    # Everything the fix agent must not touch, hashed before it runs. The
    # agent has --force --trust and could write to an absolute path; a
    # symlink-free workspace does not stop that, this does.
    guarded = guarded_digests(env, state, base)

    # One fix agent per host at a time: two lanes finding the same defect
    # would otherwise both spend a model call on it and race on the budget.
    lock_path = Path(env.get("RECOVERY_LANE_FIX_LOCK", "/var/tmp/harbor-lane-fix.lock"))
    lock_handle = _host_lock(lock_path)
    _stop_lane_scopes(lane_id)

    hold: list[str] | None = None
    if env.get("RECOVERY_MEMORY_GOVERNOR") == "1":
        hold = [
            python,
            "-m",
            "scripts.batch.memory_governor",
            "--budget-dir",
            env.get("RECOVERY_MEMORY_BUDGET_DIR", "/var/tmp/harbor-memory"),
        ]
        if env.get("RECOVERY_MEMORY_HOST_BYTES"):
            hold += ["--host-bytes", env["RECOVERY_MEMORY_HOST_BYTES"]]
        hold += [
            "hold",
            "--lane-id",
            f"{lane_id}-fix",
            "--bytes",
            str(hold_bytes),
            "--timeout-seconds",
            str(timeout_seconds),
        ]
    agent_env = {**env, "PYTHONPATH": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
    launch = run_agent(
        agent=agent,
        model=model,
        work=work,
        prompt=brief,
        log_path=root / "agent.log",
        timeout_seconds=timeout_seconds,
        hold=hold,
        env=agent_env,
    )

    _release_host_lock(lock_handle)
    after = tree_digests(work)
    changed = changed_paths(before, after)
    outside = [path for path in changed if not path.startswith(EDITABLE_PREFIXES)]
    broken = workspace_intact(base, work)
    guard_violations = changed_guarded(guarded, guarded_digests(env, state, base))
    result: dict[str, object] = {
        "schema_version": SCHEMA,
        "fix_id": fix_id,
        "fix_key": key,
        "lane_id": lane_id,
        "attempt": prior + 1,
        "workbooks": [item["workbook_id"] for item in failures],
        "signatures": signatures,
        "launch": launch,
        "changed_paths": changed,
        "rejected_paths": outside + broken,
        "guard_violations": guard_violations,
        "started_at": _now(),
    }

    def finish(status: str, reason: str, code: int) -> int:
        result.update(status=status, reason=reason, finished_at=_now())
        _write_json(root / "result.json", result)
        _append_history(state, {k: result[k] for k in ("fix_id", "fix_key", "status", "reason", "changed_paths", "workbooks")})
        if code != EXIT_OK:
            _alert(
                state,
                {
                    "kind": "lane_fix_failed",
                    "lane_id": lane_id,
                    "fix_id": fix_id,
                    "reason": reason,
                    "workbooks": result["workbooks"],
                    "attempt": prior + 1,
                    "max_attempts": max_attempts,
                },
            )
        print(f"LANE_FIX_{status.upper()} {reason} fix_id={fix_id}", flush=True)
        return code

    if guard_violations:
        # Checked first: a write into the baseline, a task worktree, a
        # checkpoint or code-baseline.json is a policy breach whatever else
        # the agent did. The alert names the paths so a human can restore
        # them from the deploy tarball / snapshot; the fix is discarded.
        code = finish("failed", f"guarded paths changed: {guard_violations[:10]}", EXIT_FAILED)
        _alert(
            state,
            {
                "kind": "lane_fix_guard_violation",
                "lane_id": lane_id,
                "fix_id": fix_id,
                "paths": guard_violations[:50],
                "message": "fix agent wrote outside its workspace; restore the listed paths before trusting this lane",
            },
        )
        return code
    if launch["status"] != "completed" or launch["return_code"] != 0:
        return finish("failed", f"agent {launch['status']} rc={launch['return_code']}", EXIT_FAILED)
    if outside or broken:
        return finish("failed", f"changes outside scripts/batch and tests: {outside + broken}", EXIT_FAILED)
    if not changed:
        return finish("failed", "agent changed nothing", EXIT_FAILED)
    if not run_tests(work, tests, python, root / "tests.log"):
        return finish("failed", "worker suites failed in the fix workspace", EXIT_FAILED)
    meta = {
        "schema_version": SCHEMA,
        "fix_id": fix_id,
        "fix_key": key,
        "lane_id": lane_id,
        "installed_at": _now(),
        "workbooks": result["workbooks"],
        "signatures": signatures,
        "changed_paths": changed,
        "files": {path: after[path] for path in changed if path in after},
        "summary": launch.get("summary", ""),
    }
    overlay = install_overlay(state, work, meta)
    result["overlay"] = str(overlay)
    alert = state / "lane-fix" / "ALERT.json"
    if alert.exists():
        alert.unlink()
    return finish("ok", f"overlay installed ({len(changed)} files)", EXIT_OK)


if __name__ == "__main__":
    raise SystemExit(main())
