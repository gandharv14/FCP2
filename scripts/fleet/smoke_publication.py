"""Root reviewable smoke-publication handoff; mutation requires --execute.

Preparation probes stopped process custody and exact ready evidence, then pins
and transfers a standalone publisher without changing the task's frozen code.
Launch rechecks the same fence and artifacts before explicit reconciliation.
Lost launch/claim replies never cause an automatic replay.
"""
from __future__ import annotations
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding, checked, retain, safe, export_files
from scripts.fleet.coordinator import Client

RELEASE = "5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057"
PUBLISHER = "e1bb392424a94f1acf0b478d2e4fe2b346466af7843314e108bf5a834775f1d4"
REMOTE = Path("/home/gmahajan_labelbox_com/synthetic-data-fleet")
COMMON = ["--project=saleseng", "--zone=us-central1-a", "--tunnel-through-iap", "--quiet"]


def require(ok, message):
    if not ok: raise ValueError(message)


def remote_probe(selection):
    """Pure remote read: no coordinator mutations, model calls or task execution."""
    from pathlib import Path
    import hashlib, json, os
    def bound(path):
        p = Path(path)
        if any(x.is_symlink() for x in (p, *p.parents)): raise ValueError("symlink evidence")
        return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    def read(row):
        if bound(row["path"]) != row: raise ValueError("selected evidence changed")
        return json.loads(Path(row["path"]).read_bytes())
    job = read(selection["worker_job"])
    launch = json.loads(Path(selection["worker_process"]).read_bytes())
    if selection["worker_job"]["path"] not in launch["command"]: raise ValueError("worker launch does not select job")
    needles = [selection["worker_job"]["path"], job["result"], job["inputs"][0]["path"], str(Path(job["result"]).parent)]
    active = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid(): continue
        try:
            state = (path / "stat").read_text().rsplit(")", 1)[1].split()[0]
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            # The remote probe command embeds the selection; exclude ancestor
            # SSH/shell probes, while retaining Python authoring descendants.
            if state != "Z" and "python" in command and "def remote_probe" not in command and (
                int(path.name) == launch["pid"] or any(n in command for n in needles)):
                active.append(int(path.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
    if active: return {"status": "WORKER_RUNNING", "active_pids": active, "worker_process": bound(selection["worker_process"])}
    outcome_path = Path(job["run"]) / "outcome.json"
    if not outcome_path.is_file(): return {"status": "OUTCOME_UNAVAILABLE", "active_pids": []}
    outcome_bound = bound(outcome_path); outcome = read(outcome_bound)
    if outcome.get("status") != "PUBLICATION_READY" or outcome.get("returncode") != 0 or outcome.get("failure_type") is not None:
        return {"status": "OWNING_HOLD", "active_pids": [], "worker_outcome": outcome_bound, "outcome": outcome}
    if read(outcome["intent"])["job"] != selection["worker_job"] or bound(outcome["log"]["path"]) != outcome["log"]:
        raise ValueError("worker outcome detached from its selected job/log")
    ready_bound = bound(job["result"])
    if outcome["result"] != ready_bound: raise ValueError("worker result differs")
    ready = read(ready_bound)
    if ready["status"] != "PUBLICATION_READY" or ready["publication_performed"] or ready["workbook"] != job["workbook"]:
        raise ValueError("owning result not publication ready")
    for name in ("job", "config", "outcome", "checkpoint", "plan"): read(ready[name])
    if ready["plan"] != job["inputs"][0]: raise ValueError("fresh result changed selected plan")
    owning_outcome = read(ready["outcome"])
    if owning_outcome["status"] != "COMPLETED_OPERATION" or owning_outcome["result"] != ready["checkpoint"]:
        raise ValueError("ready checkpoint not owned by completed operation")
    resource = read(owning_outcome["resource"])
    if resource["returncode"] != 0 or resource["failure"] is not None or bound(resource["log"]["path"]) != resource["log"]:
        raise ValueError("owning guarded outcome is incomplete")
    checkpoint = read(ready["checkpoint"])
    if checkpoint["status"] != "PUBLICATION_READY" or checkpoint["publication_performed"]:
        raise ValueError("instruction worker did not reach publication terminal")
    claim = read(bound(Path(job["run"]) / "claim.json"))
    if claim["fence"] != selection["expected_fence"] or claim["version"] != selection["release"]: raise ValueError("worker fence/code changed")
    return {"status": "STOPPED_AND_READY", "active_pids": [], "worker_process": bound(selection["worker_process"]),
        "worker_outcome": outcome_bound, "ready": ready_bound, "ready_value": ready, "claim": claim,
        "source": job["source"], "code_root": job["cwd"], "history": job["history"]}


def validate_custody(probe, status, selection):
    require(probe["status"] == "STOPPED_AND_READY" and probe["active_pids"] == [], "worker has not completed a safe publication boundary")
    require(status["model_limit"] == 1 and status["publication_limit"] == 1, "fleet resource gates changed")
    require(not any(x["state"] in {"active", "hold"} for x in status["operations"]), "global operation unresolved")
    rows = [r for r in status["workbooks"] if r["workbook"] == selection["workbook"]]
    require(len(rows) == 1, "coordinator workbook ambiguous")
    row = rows[0]; claim = probe["claim"]
    require(row["state"] == "hold" and row["fence"] == claim["fence"] == selection["expected_fence"]
        and row["owner"] == claim["owner"] and row["source_sha256"] == probe["source"]["sha256"], "coordinator custody differs")
    require(json.loads(row["reason"]) == probe["worker_outcome"], "worker finish was not durably reconciled to its exact outcome")
    require(probe["history"] == selection["history"] and probe["code_root"] == str(REMOTE / "releases" / selection["release"] / "code"), "selected history/code changed")
    return row


def ssh(selection, command, *, log=None, capture=False):
    return subprocess.run(["gcloud", "compute", "ssh", selection["host"], *COMMON, "--command", shlex.join(command)],
        stdout=subprocess.PIPE if capture else log, stderr=log, check=True, timeout=180)


def probe_remote(selection, *, log=None):
    script = inspect.getsource(remote_probe) + "\nprint(json.dumps(remote_probe("+repr(selection)+")))\n"
    # json is imported inside the probe; use a module import for the printer.
    script = "import json\n" + script
    response = ssh(selection, ["python3", "-c", script], log=log, capture=True)
    return json.loads(response.stdout)


def prepare(selection_binding, output, *, client):
    selected = checked(selection_binding); output = safe(output)
    require(selected["host"] == "hhu-server" and selected["workbook"] == "0270"
        and selected["release"] == RELEASE
        and type(selected["expected_fence"]) is int and selected["expected_fence"] >= 2,
        "closed smoke selection differs")
    require(selected["publisher"]["sha256"] == PUBLISHER and binding(selected["publisher"]["path"]) == selected["publisher"], "reviewed standalone publisher changed")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "probe.log").open("xb") as log: probe = probe_remote(selected, log=log)
    probe_binding = retain(output / "probe.json", probe)
    status = client.call("status"); status_binding = retain(output / "coordinator.json", status)
    validate_custody(probe, status, selected)
    plan = {"schema_version": "harbor-fleet-remote-publication/v1", "workbook": selected["workbook"],
        "source_sha256": probe["source"]["sha256"], "code_root": probe["code_root"], "journal": selected["remote_journal"],
        "ready": probe["ready"], "verifier": selected["verifier"], "history": selected["history"]}
    plan_binding = retain(output / "remote-plan.json", plan)
    helper = output / ("publish-"+PUBLISHER+".py")
    with helper.open("xb") as f: f.write(Path(selected["publisher"]["path"]).read_bytes())
    helper_binding = binding(helper)
    manifest = export_files([helper, Path(plan_binding["path"]), Path(selection_binding["path"])], output / "publication-inputs.tar.gz", output / "manifest.json")
    namespace = REMOTE / "publication-inputs" / manifest["sha256"]
    release = REMOTE / "releases" / RELEASE
    with (output / "transfer.log").open("xb") as log:
        ssh(selected, ["python3", "-c", "from pathlib import Path; Path("+repr(str(namespace))+").mkdir(parents=True,exist_ok=False)"], log=log)
        subprocess.run(["gcloud", "compute", "scp", str(output / "publication-inputs.tar.gz"), manifest["path"], selected["host"]+":"+str(namespace)+"/", *COMMON], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        result = ssh(selected, [str(release / "venv/bin/python"), "-I", "-B", str(release / "code/scripts/fleet/migration.py"), "import",
            "--archive", str(namespace / "publication-inputs.tar.gz"), "--manifest", str(namespace / "manifest.json"), "--manifest-sha256", manifest["sha256"],
            "--receipt", str(namespace / "receipt.json"), "--allowed-root", str(Path(__file__).resolve().parents[3])], log=log, capture=True)
    return retain(output / "prepared.json", {"schema_version": "harbor-fleet-smoke-publication-prepared/v1",
        "selection": selection_binding, "probe": probe_binding, "coordinator": status_binding, "plan": plan_binding,
        "helper": helper_binding, "import_receipt": json.loads(result.stdout), "claims": 0, "publication_started": False})


def launch(prepared_binding, *, client, execute=False):
    prepared = checked(prepared_binding); selected = checked(prepared["selection"]); plan = checked(prepared["plan"])
    release = REMOTE / "releases" / selected["release"]
    command = [str(release / "venv/bin/python"), "-I", "-B", prepared["helper"]["path"], "remote",
        "--plan", prepared["plan"]["path"], "--plan-sha256", prepared["plan"]["sha256"], "--url", "http://127.0.0.1:8765",
        "--token-file", "/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token", "--owner", "hhu-server:publication:0270:001", "--version", selected["release"]]
    if not execute: return {"execute": False, "command": command, "requires": "root --execute after fresh stopped-worker and coordinator recheck"}
    directory = Path(prepared_binding["path"]).parent
    require(not (directory / "execution-intent.json").exists(), "publication launch previously attempted; reconcile")
    with (directory / "execute-probe.log").open("xb") as log: probe = probe_remote(selected, log=log)
    status = client.call("status"); validate_custody(probe, status, selected)
    require(probe["ready"] == plan["ready"] and probe["worker_outcome"] == checked(prepared["probe"])["worker_outcome"], "prepared publication boundary changed")
    evidence = retain(directory / "execution-intent.json", {"prepared": prepared_binding, "probe": probe, "coordinator": status, "automatic_retry": False})
    client.call("reconcile", workbook="0270", disposition="ready", evidence=json.dumps(evidence, sort_keys=True), checkpoint={"ready": plan["ready"]})
    # Persist intent before launch. A lost SSH response requires process/claim
    # reconciliation, never another Popen or a new publication directory.
    script = "import json,os,subprocess,hashlib; from pathlib import Path; h="+repr(prepared["helper"])+"; assert hashlib.sha256(Path(h['path']).read_bytes()).hexdigest()==h['sha256']; "
    script += "r=Path("+repr(str(directory / "remote-launch.json"))+"); assert not r.exists(); log=r.with_suffix('.log').open('xb'); env={**os.environ,'PATH':"+repr(str(release / "venv/bin")+":")+"+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}; "
    script += "p=subprocess.Popen("+repr(command)+",stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env); value={'pid':p.pid,'command':"+repr(command)+"}; r.write_text(json.dumps(value)); print(json.dumps(value))"
    with (directory / "launch.log").open("xb") as log: result = ssh(selected, ["python3", "-c", script], log=log, capture=True)
    return retain(directory / "launch-receipt.json", {"intent": evidence, "remote": json.loads(result.stdout), "automatic_retry": False})


def stage_collection(prepared_binding, remote_result_binding, output):
    """Download and hash-verify return package; final output remains untouched."""
    prepared = checked(prepared_binding); selected = checked(prepared["selection"]); output = safe(output); output.mkdir(parents=True, exist_ok=False)
    script = "import json,hashlib; from pathlib import Path; b="+repr(remote_result_binding)+"; p=Path(b['path']); assert hashlib.sha256(p.read_bytes()).hexdigest()==b['sha256']; print(p.read_text())"
    with (output / "transfer.log").open("xb") as log:
        response = ssh(selected, ["python3", "-c", script], log=log, capture=True)
        result = json.loads(response.stdout)
        require(result["status"] == "REMOTE_VERIFIED" and result["plan"] == prepared["plan"], "wrong independently verified remote result")
        retain(output / "remote-result.json", {"binding": remote_result_binding, "value": result})
        package = result["package"]
        subprocess.run(["gcloud", "compute", "scp", selected["host"]+":"+package["archive"]["path"], selected["host"]+":"+package["manifest"]["path"], str(output)+"/", *COMMON], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    archive = output / Path(package["archive"]["path"]).name; manifest = output / Path(package["manifest"]["path"]).name
    require(binding(archive)["sha256"] == package["archive"]["sha256"] and binding(manifest)["sha256"] == package["manifest"]["sha256"], "returned package changed")
    value = checked(binding(manifest)); require(value["archive_sha256"] == binding(archive)["sha256"], "archive manifest differs")
    # Full member hashes are checked before producing a collection plan. Final
    # collector independently repeats this and binds the task generation/report.
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers(); require(len(members) == len(value["files"]) and {m.name for m in members} == set(value["files"]), "return package membership differs")
        for member in members:
            row = value["files"][member.name]; require(member.isfile() and member.size == row["size_bytes"], "return package type/size differs")
            h = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024*1024), b""): h.update(block)
            require(h.hexdigest() == row["sha256"], "return package file changed")
    plan = checked(prepared["plan"])
    return retain(output / "collection-plan.json", {"schema_version": "harbor-fleet-collection-plan/v1", "workbook": "0270",
        "source_sha256": plan["source_sha256"], "code_root": str(Path(__file__).resolve().parents[2]),
        "journal": str(output / "collection-operation"), "archive": str(archive), "manifest": binding(manifest),
        "output_root": str(Path(__file__).resolve().parents[3] / "tasks_outputs")})


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("action", choices=("prepare", "launch", "stage-collection"))
    p.add_argument("--input", required=True); p.add_argument("--input-sha256", required=True)
    p.add_argument("--output"); p.add_argument("--url", default="http://127.0.0.1:8765"); p.add_argument("--token-file")
    p.add_argument("--execute", action="store_true"); p.add_argument("--remote-result"); p.add_argument("--remote-result-sha256")
    a = p.parse_args(); selected = {"path": str(Path(a.input).absolute()), "sha256": a.input_sha256}
    if a.action == "prepare": result = prepare(selected, Path(a.output), client=Client(a.url, a.token_file))
    elif a.action == "launch": result = launch(selected, client=Client(a.url, a.token_file) if a.execute else None, execute=a.execute)
    else: result = stage_collection(selected, {"path": a.remote_result, "sha256": a.remote_result_sha256}, Path(a.output))
    print(json.dumps(result))


if __name__ == "__main__": main()
