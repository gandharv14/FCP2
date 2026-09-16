"""Root reviewable smoke-publication handoff; mutation requires --execute.

Preparation probes stopped process custody and exact ready evidence, then pins
and transfers a standalone publisher without changing the task's frozen code.
Launch rechecks the same fence and artifacts before explicit reconciliation.
Lost launch/claim replies never cause an automatic replay.
"""
from __future__ import annotations
import argparse
import hashlib
import ast
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
from scripts.fleet import smoke_successor_layout as V

RELEASE = "5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057"
PUBLISHER = "" # Frozen after exact successor layout and publisher review.
LAYOUT_SHA256 = ""
REMOTE = Path("/home/gmahajan_labelbox_com/synthetic-data-fleet")
COMMON = ["--project=saleseng", "--zone=us-central1-a", "--tunnel-through-iap", "--quiet"]


def require(ok, message):
    if not ok: raise ValueError(message)


def selected_layout(selected):
    require(set(selected)=={'schema_version','host','workbook','release','expected_fence','publisher','worker_job',
        'worker_process','verifier','history','remote_journal','successor_layout'},'closed successor publication selection required')
    require(selected['schema_version']=='harbor-0270-successor-publication-selection/v1'
        and selected['host']=='hhu-server' and selected['workbook']=='0270','exact smoke successor required')
    v=V.validate(selected['successor_layout'],expected_sha256=LAYOUT_SHA256)
    require(selected['release']==v['authoring_version'] and selected['worker_job']==v['worker_job']
        and type(selected['expected_fence']) is int and selected['expected_fence']>=5,'successor publication custody changed')
    require(selected['publisher']['sha256']==PUBLISHER and binding(selected['publisher']['path'])==selected['publisher'],'reviewed successor publisher changed')
    return v


def function_source(name):
    source=Path(__file__).read_text();node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ast.get_source_segment(source,node)


def owned_argv(arguments, namespaces):
    """Match whole argv paths, never text embedded inside a probe script."""
    for argument in arguments:
        value = argument.decode(errors="replace") if isinstance(argument, bytes) else argument
        if value.startswith("--") and "=" in value:
            value = value.split("=", 1)[1]
        for namespace in namespaces:
            if value == namespace or value.startswith(namespace.rstrip("/") + "/"):
                return True
    return False


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
    layout=validate(selection["successor_layout"],expected_sha256=LAYOUT_SHA256,remote=True)
    job = read(selection["worker_job"])
    launch = read(selection["worker_process"])
    if launch["job"]!=selection["worker_job"]:raise ValueError("worker process job changed")
    if selection["worker_job"]["path"] not in launch["command"]: raise ValueError("worker launch does not select job")
    original = read(layout["original_config"])
    root_plan = read(layout["root_plan"])
    operations = str(Path(original["run_root"]) / "dispatcher-v2" / Path(root_plan["home"]).name)
    needles = [selection["worker_job"]["path"], job["run"], job["result"], job["inputs"][0]["path"],
        str(Path(job["result"]).parent), original["run_root"], operations]
    # A terminal result binds the owning dispatcher job independently of the
    # outer worker; retain its exact operations namespace for orphan checks.
    if Path(job["result"]).is_file():
        candidate = read(bound(job["result"]))
        if candidate.get("job"):
            owning_job = read(candidate["job"])
            if owning_job.get("operations") != operations:
                raise ValueError("owning dispatcher operations changed")
            needles.append(owning_job["operations"])
    active = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid(): continue
        try:
            state = (path / "stat").read_text().rsplit(")", 1)[1].split()[0]
            arguments = (path / "cmdline").read_bytes().split(b"\0")
            if state != "Z" and (int(path.name) == launch["pid"] or owned_argv(arguments, needles)):
                active.append(int(path.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
    if active: return {"status": "WORKER_RUNNING", "active_pids": active, "worker_process": selection["worker_process"]}
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
    configuration(layout,ready["config"])
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
    expected_command=[layout["runtime_python"],"-I","-B",layout["runtime_worker"]["path"],"--job",selection["worker_job"]["path"],"--job-sha256",selection["worker_job"]["sha256"],"--url","http://127.0.0.1:8765","--token-file","/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token","--owner",claim["owner"]]
    if launch["command"]!=expected_command:raise ValueError("worker runtime/process command differs")
    return {"status": "STOPPED_AND_READY", "active_pids": [], "worker_process": selection["worker_process"],
        "worker_outcome": outcome_bound, "ready": ready_bound, "ready_value": ready, "claim": claim,
        "source": job["source"], "code_root": read(layout["authoring_snapshot"])["root"], "runtime_root":job["cwd"],"successor_layout":selection["successor_layout"], "history": job["history"]}


def validate_custody(probe, status, selection):
    require(probe["status"] == "STOPPED_AND_READY" and probe["active_pids"] == [], "worker has not completed a safe publication boundary")
    require(status["model_limit"] == 1 and status["publication_limit"] == 1, "fleet resource gates changed")
    require(not any(x["state"] in {"active", "hold"} for x in status["operations"]), "global operation unresolved")
    rows = [r for r in status["workbooks"] if r["workbook"] == selection["workbook"]]
    require(len(rows) == 1, "coordinator workbook ambiguous")
    layout=selected_layout(selection)
    row = rows[0]; claim = probe["claim"]
    require(row["state"] == "hold" and row["fence"] == claim["fence"] == selection["expected_fence"]
        and row["owner"] == claim["owner"] and row["version"]==layout["authoring_version"] and row["source_sha256"] == probe["source"]["sha256"], "coordinator custody differs")
    require(json.loads(row["reason"]) == probe["worker_outcome"], "worker finish was not durably reconciled to its exact outcome")
    require(probe["history"] == selection["history"] and probe["code_root"] == checked(layout["authoring_snapshot"])["root"]
        and probe["runtime_root"]==str(REMOTE/"releases"/layout["runtime_release"]/"code")
        and probe["successor_layout"]==selection["successor_layout"], "selected history/code changed")
    return row


def ssh(selection, command, *, log=None, capture=False):
    return subprocess.run(["gcloud", "compute", "ssh", selection["host"], *COMMON, "--command", shlex.join(command)],
        stdout=subprocess.PIPE if capture else log, stderr=log, check=True, timeout=180)


def probe_remote(selection, *, log=None):
    layout=selected_layout(selection)
    source=Path(V.__file__).read_text()
    script="import sys;sys.path.insert(0,"+repr(str(REMOTE/"releases"/layout["runtime_release"]/"code"))+")\n"+source+"\nLAYOUT_SHA256="+repr(LAYOUT_SHA256)+"\n"+function_source("owned_argv")+"\n"+function_source("remote_probe")+"\nprint(json.dumps(remote_probe("+repr(selection)+")))\n"
    # json is imported inside the probe; use a module import for the printer.
    script = "import json\n" + script
    response = ssh(selection, [layout["runtime_python"], "-I", "-B", "-c", script], log=log, capture=True)
    return json.loads(response.stdout)


def prepare(selection_binding, output, *, client):
    selected = checked(selection_binding); output = safe(output)
    layout=selected_layout(selected)
    require(selected["publisher"]["sha256"] == PUBLISHER and binding(selected["publisher"]["path"]) == selected["publisher"], "reviewed standalone publisher changed")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "probe.log").open("xb") as log: probe = probe_remote(selected, log=log)
    probe_binding = retain(output / "probe.json", probe)
    status = client.call("status"); status_binding = retain(output / "coordinator.json", status)
    validate_custody(probe, status, selected)
    plan = {"schema_version": "harbor-fleet-remote-publication/v1", "workbook": selected["workbook"],
        "source_sha256": probe["source"]["sha256"], "code_root": probe["code_root"], "journal": selected["remote_journal"],
        "ready": probe["ready"], "verifier": selected["verifier"], "history": selected["history"],"successor_layout":selected["successor_layout"]}
    plan_binding = retain(output / "remote-plan.json", plan)
    helper = output / ("publish-"+PUBLISHER+".py")
    with helper.open("xb") as f: f.write(Path(selected["publisher"]["path"]).read_bytes())
    helper_binding = binding(helper)
    manifest = export_files([helper, Path(plan_binding["path"]), Path(selection_binding["path"])], output / "publication-inputs.tar.gz", output / "manifest.json")
    namespace = REMOTE / "publication-inputs" / manifest["sha256"]
    release = REMOTE / "releases" / layout["runtime_release"]
    with (output / "transfer.log").open("xb") as log:
        ssh(selected, ["python3", "-c", "from pathlib import Path; Path("+repr(str(namespace))+").mkdir(parents=True,exist_ok=False)"], log=log)
        subprocess.run(["gcloud", "compute", "scp", str(output / "publication-inputs.tar.gz"), manifest["path"], selected["host"]+":"+str(namespace)+"/", *COMMON], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        result = ssh(selected, [str(release / "venv/bin/python"), "-I", "-B", str(release / "code/scripts/fleet/migration.py"), "import",
            "--archive", str(namespace / "publication-inputs.tar.gz"), "--manifest", str(namespace / "manifest.json"), "--manifest-sha256", manifest["sha256"],
            "--receipt", str(namespace / "receipt.json"), "--allowed-root", str(Path(checked(layout["original_config"])["output"]).parent)], log=log, capture=True)
    return retain(output / "prepared.json", {"schema_version": "harbor-fleet-smoke-publication-prepared/v1",
        "selection": selection_binding, "probe": probe_binding, "coordinator": status_binding, "plan": plan_binding,
        "helper": helper_binding, "import_receipt": json.loads(result.stdout), "claims": 0, "publication_started": False})


def remote_spawn(payload):
    """Self-contained subprocess launch; same function is exercised locally."""
    import hashlib,json,os,subprocess,time
    from pathlib import Path
    receipt=Path(payload['receipt']);command=payload['command']
    if not receipt.is_absolute() or any(p.is_symlink() for p in (receipt,*receipt.parents)):
        raise ValueError('canonical receipt required')
    def keep(path,value):
        with path.open('x') as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    paths=[receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]
    if any(p.exists() for p in paths):raise ValueError('launch already attempted; reconcile')
    receipt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    intent={'command':command,'job':payload['job'],'root_intent':payload['root_intent'],'automatic_retry':False,'created_at_ns':time.time_ns()}
    keep(paths[2],intent)
    with paths[1].open('xb') as log:
        environment={**os.environ,'PATH':str(Path(command[0]).parent)+':'+os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'}
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
            start_new_session=True,cwd=payload['cwd'],env=environment)
    # A failure after Popen must preserve the launch intent and live process.
    start=None;stat=Path('/proc')/str(process.pid)/'stat'
    if stat.exists():start=stat.read_text().rsplit(')',1)[1].split()[19]
    value={'pid':process.pid,'start_ticks':start,'command':command,'job':payload['job'],
        'root_intent':payload['root_intent'],'automatic_retry':False,'created_at_ns':time.time_ns()}
    keep(receipt,value)
    return value

def launch(prepared_binding, *, client, execute=False):
    prepared = checked(prepared_binding); selected = checked(prepared["selection"]); plan = checked(prepared["plan"])
    layout=selected_layout(selected)
    require(plan["successor_layout"]==selected["successor_layout"],"publication layout detached")
    release = REMOTE / "releases" / layout["runtime_release"]
    command = [str(release / "venv/bin/python"), "-I", "-B", prepared["helper"]["path"], "remote",
        "--plan", prepared["plan"]["path"], "--plan-sha256", prepared["plan"]["sha256"], "--url", "http://127.0.0.1:8765",
        "--token-file", "/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token", "--owner", "hhu-server:publication:0270:successor-001", "--version", selected["release"]]
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
    payload={"receipt":str(directory/"remote-launch.json"),"command":command,"job":prepared["plan"],
        "root_intent":evidence,"cwd":str(release/"code")}
    script="import hashlib,json;from pathlib import Path;h="+repr(prepared["helper"])+";assert hashlib.sha256(Path(h['path']).read_bytes()).hexdigest()==h['sha256']\n"
    script+=function_source("remote_spawn")+"\nprint(json.dumps(remote_spawn("+repr(payload)+")))\n"
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
    plan = checked(prepared["plan"]);layout=selected_layout(selected)
    require(plan["successor_layout"]==selected["successor_layout"] and value["successor_layout"]==selected["successor_layout"],"returned successor lineage changed")
    return retain(output / "collection-plan.json", {"schema_version": "harbor-fleet-collection-plan/v1", "workbook": "0270",
        "source_sha256": plan["source_sha256"], "code_root": checked(layout["authoring_snapshot"])["root"], "successor_layout":selected["successor_layout"], "collector_runtime":binding(Path(sys.executable).resolve()),
        "journal": str(output / "collection-operation"), "archive": str(archive), "manifest": binding(manifest),
        "output_root": str(Path(checked(layout["original_config"])["output"]).parent)})


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
