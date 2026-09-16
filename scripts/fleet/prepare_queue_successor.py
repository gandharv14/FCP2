"""Prepare explicit nine-workbook fleet plans and lossless input custody only.

This script does not claim work, start workers, dispatch models, or publish.
Root still gates supervisor start on the independently validated smoke task.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding, checked, retain, safe, export_files
from scripts.fleet.python_entry import command as python_command

HOSTS = ["hhu-server"] + ["hhu-server-"+str(i) for i in range(2, 9)]
WORKBOOKS = ["0085", "0139", "0308", "0491", "0137", "0063", "0144", "0145", "0281"]
REMOTE = Path("/home/gmahajan_labelbox_com/synthetic-data-fleet")


def transfer(archive, manifest, release, output):
    archive, output = safe(archive), safe(output)
    value = checked(manifest)
    from scripts.fleet.migration import digest
    if digest(archive) != value["archive_sha256"]:
        raise ValueError("input archive changed")
    output.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[3]
    namespace = REMOTE / "inputs" / ("fresh9-"+manifest["sha256"][:16])
    code = REMOTE / "releases" / release

    def host(machine):
        started = time.monotonic(); log = output / (machine+".log")
        prefix = ["gcloud", "compute", "ssh", machine, "--project=saleseng", "--zone=us-central1-a", "--tunnel-through-iap", "--quiet"]
        rc = None; failure = None
        with log.open("xb") as stream:
            def ssh(script):
                return subprocess.run(prefix+["--command", shlex.join(["python3", "-c", script])], stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
            try:
                bootstrap = "from pathlib import Path; import os,subprocess; p=Path("+repr(str(source_root))+ "); n=Path("+repr(str(namespace))+"); "
                bootstrap += "\nif not p.exists():\n subprocess.run(['sudo','-n','mkdir','-p',str(p)],check=True)\n subprocess.run(['sudo','-n','chown',str(os.getuid())+':'+str(os.getgid()),str(p)],check=True)\n"
                bootstrap += "n.mkdir(parents=True,exist_ok=False)\n"
                ssh(bootstrap)
                subprocess.run(["gcloud", "compute", "scp", str(archive), manifest["path"], machine+":"+str(namespace)+"/",
                    "--project=saleseng", "--zone=us-central1-a", "--tunnel-through-iap", "--quiet"],
                    stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=300)
                command = [str(code / "venv/bin/python"), "-I", "-B", str(code / "code/scripts/fleet/migration.py"), "import",
                    "--archive", str(namespace / archive.name), "--manifest", str(namespace / Path(manifest["path"]).name),
                    "--manifest-sha256", manifest["sha256"], "--receipt", str(namespace / "import-receipt.json"), "--allowed-root", str(source_root)]
                subprocess.run(prefix+["--command", shlex.join(command)], stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=300)
                subprocess.run(["gcloud", "compute", "scp", machine+":"+str(namespace / "import-receipt.json"), str(output / (machine+"-import-receipt.json")),
                    "--project=saleseng", "--zone=us-central1-a", "--tunnel-through-iap", "--quiet"],
                    stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
                receipt = json.loads((output / (machine+"-import-receipt.json")).read_bytes())
                if not receipt["absolute_paths_preserved"] or receipt["historical_bytes_rewritten"] or receipt["archive_sha256"] != value["archive_sha256"]:
                    raise ValueError("remote custody receipt differs")
                if {r["original_path"]: r["sha256"] for r in receipt["files"]} != {r["path"]: r["sha256"] for r in value["files"]}:
                    raise ValueError("remote installed file inventory differs")
                rc = 0
            except Exception as exc:
                failure = type(exc).__name__
        return {"host": machine, "status": "INPUTS_VERIFIED" if rc == 0 else "RECONCILIATION_REQUIRED",
            "failure_type": failure, "elapsed_seconds": time.monotonic()-started, "log": binding(log),
            "remote_namespace": str(namespace), "claims": 0, "workers_started": 0}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(host, HOSTS))
    return retain(output / "report.json", {"schema_version": "harbor-fleet-input-custody/v1", "manifest": manifest,
        "release": release, "hosts": rows, "claims": 0, "workers_started": 0})


def prepare(root, release, bundle_manifest, handoff, history, output, *, dispatcher):
    root, output = safe(root), safe(output)
    original_root = root.parent; q = root / "diagnostics/authoring-queue-extension-20260911T185027Z"
    runtime = REMOTE / "releases" / release
    history_value = checked(history); handoff_value = checked(handoff)
    bundle = checked(bundle_manifest)
    if bundle.get("release") != release:
        raise ValueError("selected bundle version differs")
    handoffs = {r["workbook_id"]: r for r in handoff_value["workbooks"]}
    # fleet_deploy manifest keys are the file closure plus pinned modes.
    rows = bundle["files"]
    if isinstance(rows, list): files = {r["path"]: r["sha256"] for r in rows}
    else: files = {k: (v["sha256"] if isinstance(v, dict) else v) for k, v in rows.items()}
    in_release = Path(dispatcher['path']).is_relative_to(runtime/'code')
    if in_release:
        relative = str(Path(dispatcher['path']).relative_to(runtime/'code'))
        if '..' in Path(relative).parts or files.get(relative) != dispatcher['sha256']:
            raise ValueError('selected continuation dispatcher differs from release')
    elif binding(dispatcher['path']) != dispatcher:
        raise ValueError('selected continuation dispatcher changed')
    output.mkdir(parents=True, exist_ok=False)
    queue = binding(q / "queue.json"); inventory = binding(root / "diagnostics/authoring-resume-20260910/root/inventory.json")
    originals = {r["workbook_id"]: r for r in checked(inventory)["sources"]}
    queue_rows = {r["workbook_id"]: r for r in checked(queue)["items"]}
    assignments = {host: [] for host in HOSTS}
    record_paths = [] if in_release else [Path(dispatcher["path"])]
    for index, wb in enumerate(WORKBOOKS):
        host = HOSTS[index % len(HOSTS)]
        if wb not in history_value["fresh_ids"] or queue_rows[wb]["history_class"] != "no_history":
            raise ValueError("workbook absent from reconciled fresh history")
        if (q / ("reconstruction-"+wb+"-attempt-001")).exists():
            raise ValueError("prior reconstruction consumed; do not create another attempt")
        preparation = binding(q / "fresh28-current-preparations-v2" / (wb+".json"))
        previous = checked(preparation)
        if previous["queue"] != queue or previous["inventory"] != inventory:
            raise ValueError("previous fresh preparation scope differs")
        original = originals[wb]
        if queue_rows[wb]["original_source"] != original:
            raise ValueError("immutable source identity differs")
        selected_handoff = handoffs[wb]
        if selected_handoff["status"] != "unstarted_reconstruction" or selected_handoff["original_source"] != original or selected_handoff["preparation"] != preparation:
            raise ValueError("root handoff does not authorize an unused fresh preparation")
        run = root / "diagnostics/vm-fleet-20260912/workbooks" / wb / "attempt-001"
        if run.exists(): raise ValueError("fleet workbook attempt already exists")
        runner = root / "diagnostics/authoring-resume-20260910/runner"
        plan = {"schema_version": "harbor-fleet-fresh-plan/v1", "workbook": wb, "queue": queue, "inventory": inventory,
            "history_roots": history_value["roots"], "code_root": str(runtime / "code"), "home": str(run),
            "preparer": binding(runner / "author_workbook_v3.py"), "dispatcher": dispatcher,
            "taxonomy": binding(root / "taxonomy_out/workbooks.json"), "env_file": "/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env",
            "batch": "vm-fleet-20260912", "owner": "harbor-orchestrator-"+wb+"-"+host,
            "publication_root": str(root / "diagnostics/vm-fleet-20260912/releases" / wb), "output_root": str(original_root / "tasks_outputs"),
            "reconstruction": {"mode": "fresh", "prior_preparation": preparation,
                "restriction_inventory": previous["restriction_inventory"], "inventory_batch_id": previous["inventory_batch_id"]}}
        plan_binding = retain(output / (wb+"-plan.json"), plan); record_paths.append(Path(plan_binding["path"]))
        driver = {"path": str(runtime / "code/scripts/fleet/fresh.py"), "sha256": files["scripts/fleet/fresh.py"]}
        job = {"schema_version": "harbor-fleet-worker-job/v1", "workbook": wb,
            "source": {k: original[k] for k in ("path", "sha256")}, "version": release, "driver": driver,
            "inputs": [plan_binding, handoff, history, dispatcher], "history": history,
            "command": python_command(runtime / "venv/bin/python", driver["path"], "run", "--plan", plan_binding["path"], "--plan-sha256", plan_binding["sha256"]),
            "cwd": str(runtime / "code"), "run": str(root / "diagnostics/vm-fleet-20260912/workers" / host / (wb+"-attempt-001")),
            "result": str(run / "result.json"), "memory_limit_bytes": 8*1024**3, "timeout_seconds": 86400, "minimum_disk_bytes": 12*1024**3}
        job_binding = retain(output / (wb+"-job.json"), job); record_paths.append(Path(job_binding["path"]))
        assignments[host].append(job_binding)
    for host in HOSTS:
        config = {"schema_version": "harbor-fleet-pool/v1", "host": host, "code_root": str(runtime / "code"),
            "python": str(runtime / "venv/bin/python"), "worker": {"path": str(runtime / "code/scripts/fleet/worker.py"), "sha256": files["scripts/fleet/worker.py"]},
            "url": "http://127.0.0.1:8765", "token_file": "/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token", "jobs": assignments[host]}
        config_binding = retain(output / (host+"-pool.json"), config); record_paths.append(Path(config_binding["path"]))
    manifest = export_files([*record_paths, Path(handoff["path"])], output / "plans.tar.gz", output / "manifest.json")
    return retain(output / "preparation.json", {"schema_version": "harbor-fleet-queue-preparation/v1", "release": release,
        "handoff": handoff, "history": history, "dispatcher": dispatcher, "assignments": assignments, "plan_manifest": manifest,
        "claims": 0, "workers_started": 0, "start_gate": "independently verified end-to-end smoke required"})


def validate_plans(preparation, output):
    value = checked(preparation); output = safe(output); output.mkdir(parents=True, exist_ok=False)
    folder = Path(preparation["path"]).parent
    release = REMOTE / "releases" / value["release"]
    def host(machine):
        selected = binding(folder / (machine+"-pool.json"))
        probe = "import json,sys; sys.path.insert(0,"+repr(str(release / "code"))+"); from scripts.fleet.pool import config; from scripts.fleet.worker import validate; c,j=config("+repr(selected)+"); [validate(v) for b,v in j]; print(json.dumps({'host':c['host'],'workbooks':[v['workbook'] for b,v in j],'status':'PLAN_AND_INPUT_HASHES_VERIFIED','claims':0,'workers_started':0}))"
        result = subprocess.run(["gcloud", "compute", "ssh", machine, "--project=saleseng", "--zone=us-central1-a",
            "--tunnel-through-iap", "--quiet", "--command", shlex.join([str(release / "venv/bin/python"), "-I", "-B", "-c", probe])],
            capture_output=True, text=True, timeout=180)
        (output / (machine+".stdout")).write_text(result.stdout); (output / (machine+".stderr")).write_text(result.stderr)
        if result.returncode: return {"host": machine, "status": "VALIDATION_FAILED", "returncode": result.returncode}
        row = json.loads(result.stdout.strip().splitlines()[-1])
        if row["host"] != machine: raise ValueError("remote pool host differs")
        row["pool_config"] = selected
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(host, HOSTS))
    return retain(output / "report.json", {"preparation": preparation, "hosts": rows, "claims": 0, "workers_started": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="action", required=True)
    x = sub.add_parser("transfer"); x.add_argument("--archive", required=True); x.add_argument("--manifest", required=True); x.add_argument("--manifest-sha256", required=True)
    y = sub.add_parser("prepare"); y.add_argument("--root", required=True)
    z = sub.add_parser("validate"); z.add_argument("--preparation", required=True); z.add_argument("--preparation-sha256", required=True); z.add_argument("--output", required=True)
    for name in ("bundle-manifest", "handoff", "history", "dispatcher"):
        y.add_argument("--"+name, required=True); y.add_argument("--"+name+"-sha256", required=True)
    for p in (x, y): p.add_argument("--release", required=True); p.add_argument("--output", required=True)
    a = parser.parse_args()
    if a.action == "transfer": result = transfer(Path(a.archive), {"path": a.manifest, "sha256": a.manifest_sha256}, a.release, Path(a.output))
    elif a.action == "validate": result = validate_plans({"path": a.preparation, "sha256": a.preparation_sha256}, Path(a.output))
    else:
        result = prepare(Path(a.root), a.release, {"path": a.bundle_manifest, "sha256": a.bundle_manifest_sha256},
            {"path": a.handoff, "sha256": a.handoff_sha256}, {"path": a.history, "sha256": a.history_sha256}, Path(a.output),
            dispatcher={"path": a.dispatcher, "sha256": a.dispatcher_sha256})
    print(json.dumps(result))


if __name__ == "__main__": main()
