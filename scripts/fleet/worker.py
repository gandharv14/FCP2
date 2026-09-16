"""One centrally claimed operation, with durable intent and fail-closed custody.

The child is a root-selected hash-bound driver. Its native per-task resource and
review gates remain authoritative. A failed heartbeat terminates the complete
child process group and leaves a reconciliation hold; no command is replayed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet.coordinator import Client
from scripts.fleet.migration import binding, checked, retain, safe
from scripts.batch.hardened_runner import (_guarded_process,
    _process_group_rss_bytes, _process_group_alive, _terminate_process_group)

SCHEMA = "harbor-fleet-worker-job/v1"


def failure_diagnostics(exc, stage):
    """Retain safe protocol/guard details without arbitrary exception text."""
    message = str(exc)
    result = dict(schema_version="fleet-worker-failure-diagnostics/v1",
                  stage=stage, exception_type=type(exc).__name__,
                  message_sha256=hashlib.sha256(message.encode()).hexdigest(),
                  message_bytes=len(message.encode()))
    http = re.fullmatch(r"coordinator HTTP ([1-5][0-9]{2}): ([\s\S]*)", message)
    if isinstance(exc, RuntimeError) and http:
        body = http.group(2)
        result.update(category="coordinator_http", http_status=int(http.group(1)),
                      response_error_sha256=hashlib.sha256(body.encode()).hexdigest(),
                      response_error_bytes=len(body.encode()))
        if body in {"forbidden", "coordinator error; reconcile uncertain mutations before retry"}:
            result["response"] = {"error": body}
        else:
            result["response_redacted"] = True
    elif isinstance(exc, RuntimeError) and message in {
            "memory observation unavailable", "process group exceeded original memory guard",
            "worker wall time guard exceeded", "runtime disk reserve exhausted"}:
        result.update(category="resource_guard", reason=message)
    else:
        result["category"] = "other"
    return result


def retain_failure(run, name, exc, stage, **anchors):
    # Diagnostics must never prevent the original outcome/finish path.
    try:
        retain(run / name, dict(failure_diagnostics(exc, stage), **anchors))
    except OSError:
        pass


def selected_plan(job):
    """Bind a plan-bearing command to the same immutable input being checked."""
    command = job["command"]
    fresh = Path(job["driver"]["path"]).name == "fresh.py"
    if any(value.startswith(("--plan=", "--plan-sha256=", "--sha256=")) for value in command):
        raise ValueError("selected plan requires canonical separate arguments")
    if "--plan" not in command:
        if fresh:
            raise ValueError("exact selected plan argument required")
        return None
    hashes = [flag for flag in command if flag in {"--sha256", "--plan-sha256"}]
    if command.count("--plan") != 1 or len(hashes) != 1 or not job["inputs"]:
        raise ValueError("one selected plan and hash required")
    flag = hashes[0]
    if fresh and flag != "--plan-sha256":
        raise ValueError("fresh selected plan hash flag differs")
    try:
        actual = {"path": command[command.index("--plan")+1],
                  "sha256": command[command.index(flag)+1]}
    except IndexError as exc:
        raise ValueError("selected plan argument is incomplete") from exc
    if actual != job["inputs"][0] or sum(row == actual for row in job["inputs"]) != 1:
        raise ValueError("command selected plan differs from sole current plan input")
    return actual


def validate(job):
    fields = {"schema_version", "workbook", "source", "version", "driver", "inputs",
              "command", "cwd", "run", "result", "memory_limit_bytes", "timeout_seconds",
              "minimum_disk_bytes"}
    if set(job) - {"history", "continuation_history", "physical_model_policy", "operational_allowance", "heartbeat_reconciliation"} != fields or job["schema_version"] != SCHEMA:
        raise ValueError("closed fleet job required")
    if len(job["workbook"]) != 4 or not job["workbook"].isascii() or not job["workbook"].isdigit():
        raise ValueError("four-digit workbook required")
    if job["minimum_disk_bytes"] < 12 * 1024**3:
        raise ValueError("existing resource guards must remain")
    if "heartbeat_reconciliation" in job:
        from scripts.fleet.heartbeat_reconciliation import validate as validate_heartbeat
        validate_heartbeat(job["heartbeat_reconciliation"])
    if "operational_allowance" in job:
        from scripts.batch import harbor_continuation_allowance as allowances
        policy = allowances.validate(job["operational_allowance"],
            expected_scope=allowances.scope(job["workbook"], job["source"]["sha256"]), at=time.time_ns())
        if (type(job["memory_limit_bytes"]) is not int or not 0 < job["memory_limit_bytes"] <= policy['limits']['memory_bytes']):
            raise ValueError('worker exceeds explicit scoped memory allowance')
    elif job["memory_limit_bytes"] != 8 * 1024**3:
        raise ValueError("existing resource guards must remain without explicit allowance")
    if type(job["timeout_seconds"]) is not int or not 1 <= job["timeout_seconds"] <= 86400:
        raise ValueError("bounded outer timeout required")
    if not isinstance(job["command"], list) or not all(isinstance(s, str) and s for s in job["command"]):
        raise ValueError("argument vector required")
    if str(job["driver"]["path"]) not in job["command"]:
        raise ValueError("command must select the bound driver")
    from scripts.fleet.python_entry import validate as validate_python_entry
    validate_python_entry(job["command"], job["driver"]["path"])
    selected_plan(job)
    if "history" in job and "continuation_history" in job:
        raise ValueError("fresh and continued history contexts cannot be combined")
    for row in (job["source"], job["driver"], *job["inputs"], *([job["history"]] if "history" in job else []), *([job["continuation_history"]] if "continuation_history" in job else []), *([job["physical_model_policy"]] if "physical_model_policy" in job else [])):
        if binding(row["path"]) != row:
            raise ValueError("job input hash differs")
    for key in ("cwd", "run", "result"):
        safe(job[key])
    if "physical_model_policy" in job:
        from scripts.fleet.physical_model_capacity import validate as validate_physical
        validate_physical(job["physical_model_policy"])


def execute(job_binding, *, client, owner, url, token_file):
    job = checked(job_binding); validate(job)
    run = safe(job["run"])
    if run.exists():
        raise ValueError("worker attempt already exists; reconcile, never replay")
    if shutil.disk_usage(job["cwd"]).free < job["minimum_disk_bytes"]:
        raise ValueError("disk reserve unavailable before claim")
    run.mkdir(parents=True, mode=0o700)
    claim_id = str(uuid.uuid4())
    intent = retain(run / "intent.json", {"job": job_binding, "owner": owner,
        "claim_id": claim_id, "automatic_retry": False, "created_at_ns": time.time_ns()})
    # Any lost response leaves this intent for root reconciliation. Never issue a
    # new UUID to work around an uncertain claim or operation.
    claim = client.call("claim", workbook=job["workbook"], owner=owner,
                        claim_id=claim_id, version=job["version"], lease_seconds=180)
    auth = {key: claim[key] for key in ("workbook", "owner", "token", "fence")}
    if claim["source_sha256"] != job["source"]["sha256"]:
        client.call("finish", **auth, evidence=json.dumps(intent), disposition="hold")
        raise ValueError("claimed original hash differs")
    retain(run / "claim.json", {key: value for key, value in claim.items() if key != "token"})
    from scripts.fleet.python_entry import environment as python_environment
    env = {**python_environment(sys.executable, os.environ), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": job["cwd"],
        "HARBOR_FLEET_URL": url, "HARBOR_FLEET_TOKEN_FILE": str(token_file),
        "HARBOR_FLEET_WORKBOOK": claim["workbook"], "HARBOR_FLEET_OWNER": claim["owner"],
        "HARBOR_FLEET_CLAIM_TOKEN": claim["token"], "HARBOR_FLEET_FENCE": str(claim["fence"]),
        "HARBOR_FLEET_MODEL_JOURNAL": str(run / "model-operations")}
    if "history" in job:
        env.update(HARBOR_FLEET_HISTORY_PATH=job["history"]["path"],
                   HARBOR_FLEET_HISTORY_SHA256=job["history"]["sha256"])
    if "continuation_history" in job:
        env.update(HARBOR_FLEET_CONTINUATION_HISTORY_PATH=job["continuation_history"]["path"],
                   HARBOR_FLEET_CONTINUATION_HISTORY_SHA256=job["continuation_history"]["sha256"])
    if "physical_model_policy" in job:
        env.update(HARBOR_PHYSICAL_MODEL_POLICY=job["physical_model_policy"]["path"],
                   HARBOR_PHYSICAL_MODEL_POLICY_SHA256=job["physical_model_policy"]["sha256"])
    else:
        env.pop('HARBOR_PHYSICAL_MODEL_POLICY', None)
        env.pop('HARBOR_PHYSICAL_MODEL_POLICY_SHA256', None)
    heartbeat = None
    if "heartbeat_reconciliation" in job:
        from scripts.fleet.heartbeat_reconciliation import Heartbeats
        heartbeat = Heartbeats(job["heartbeat_reconciliation"], claim)
    metrics = {"peak_rss_bytes": 0, "heartbeat_count": 0}
    start = time.monotonic(); rc = None; failure = None; stage = "start_child"
    try:
        with (run / "worker.log").open("xb") as log:
            with _guarded_process(job["command"], stdout=log, cwd=Path(job["cwd"]),
                                  env=env, grace_seconds=5) as process:
                def reconciliation_guard():
                    rss = _process_group_rss_bytes(process.pid)
                    if rss is None:
                        raise RuntimeError("memory observation unavailable")
                    metrics["peak_rss_bytes"] = max(metrics["peak_rss_bytes"], rss)
                    if rss > job["memory_limit_bytes"]:
                        raise RuntimeError("process group exceeded original memory guard")
                    if time.monotonic() - start > job["timeout_seconds"]:
                        raise RuntimeError("worker wall time guard exceeded")
                    if shutil.disk_usage(job["cwd"]).free < 8 * 1024**3:
                        raise RuntimeError("runtime disk reserve exhausted")
                next_heartbeat = 0
                while True:
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        stage = "heartbeat"
                        if heartbeat is None:
                            client.call("heartbeat", **auth, lease_seconds=180,
                                        checkpoint={"intent": intent, "pid": process.pid})
                        else:
                            heartbeat.beat(client, auth, {"intent": intent, "pid": process.pid},
                                           run, reconciliation_guard)
                        metrics["heartbeat_count"] += 1; next_heartbeat = now + 20
                    stage = "memory_observation"
                    rss = _process_group_rss_bytes(process.pid)
                    if rss is None:
                        raise RuntimeError("memory observation unavailable")
                    metrics["peak_rss_bytes"] = max(metrics["peak_rss_bytes"], rss)
                    stage = "memory_guard"
                    if rss > job["memory_limit_bytes"]:
                        raise RuntimeError("process group exceeded original memory guard")
                    stage = "time_guard"
                    if now - start > job["timeout_seconds"]:
                        raise RuntimeError("worker wall time guard exceeded")
                    stage = "disk_guard"
                    if shutil.disk_usage(job["cwd"]).free < 8 * 1024**3:
                        raise RuntimeError("runtime disk reserve exhausted")
                    stage = "process_status"
                    rc = process.poll()
                    if rc is not None and not _process_group_alive(process.pid):
                        break
                    time.sleep(0.2)
    except Exception as exc:
        failure = type(exc).__name__
        retain_failure(run, "failure-diagnostics.json", exc, stage, intent=intent)
    metrics["elapsed_seconds"] = time.monotonic() - start
    result = binding(job["result"]) if Path(job["result"]).is_file() else None
    try:
        result_value = checked(result) if result else None
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result_value = None
        failure = type(exc).__name__
    ready = bool(rc == 0 and failure is None and result_value
                 and result_value.get("status") == "PUBLICATION_READY")
    outcome = retain(run / "outcome.json", {"schema_version": "harbor-fleet-worker-outcome/v1",
        "intent": intent, "status": "PUBLICATION_READY" if ready else "RECONCILIATION_REQUIRED",
        "returncode": rc, "failure_type": failure, "metrics": metrics,
        "result": result, "log": binding(run / "worker.log"),
        "automatic_retry": False, "publication_performed": False})
    # Even ready authoring awaits artifact transfer and central publication.
    try:
        client.call("finish", **auth, evidence=json.dumps(outcome, sort_keys=True), disposition="hold")
    except Exception as exc:
        retain_failure(run, "finish-diagnostics.json", exc, "finish", outcome=outcome)
        retain(run / "finish-uncertain.json", {"outcome": outcome, "failure_type": type(exc).__name__,
            "automatic_retry": False, "reconciliation_required": True})
    return outcome


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("job", "job-sha256", "url", "token-file", "owner"):
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    result = execute({"path": str(Path(a.job).absolute()), "sha256": a.job_sha256},
        client=Client(a.url, a.token_file), owner=a.owner, url=a.url, token_file=a.token_file)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
