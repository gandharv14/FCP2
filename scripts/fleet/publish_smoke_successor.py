"""Publish on the owning VM, then collect independently verified bytes locally.

Neither operation reruns a model, extends a review budget, rewrites provenance,
or changes historical code. Both require a fresh centrally authorized claim and
the one durable global publication slot. Uncertainty leaves reconciliation hold.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import time
import uuid


def require(ok, message):
    if not ok: raise ValueError(message)


def safe(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            "absolute non-symlink path required")
    return path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for b in iter(lambda: stream.read(1024*1024), b""): h.update(b)
    return h.hexdigest()


def binding(path):
    return {"path": str(safe(path)), "sha256": sha(path)}


def checked(row):
    require(binding(row["path"]) == row, "retained evidence changed")
    return json.loads(Path(row["path"]).read_bytes())


def retain(path, value):
    path = safe(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
        fsync(path.parent)
    finally: os.unlink(temporary)
    return binding(path)


def fsync(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def files(root):
    root = safe(root); require(root.is_dir(), "actual task directory required")
    result = {}
    for path in sorted(root.rglob("*")):
        safe(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {"sha256": sha(path), "size_bytes": path.stat().st_size}
        else: require(path.is_dir(), "special task file unsupported")
    require(bool(result), "empty delivered task")
    return result


def manifest_tree(manifest, tree, workbook):
    """Bind every delivered byte to the independently selected task manifest."""
    identity = manifest["identity"]
    require(manifest.get("schema_version") == "task-generation/v2"
        and identity.get("schema_version") == "task-generation-identity/v2", "unsupported qualified task schema")
    require(identity["workbook_id"] == workbook and identity["artifacts"] == manifest["artifacts"]
        and identity["bindings"] == manifest["bindings"], "task identity bindings differ")
    canonical = (json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    require(hashlib.sha256(canonical).hexdigest() == manifest["generation_id"], "task generation identity hash differs")
    expected = {}
    for name, row in manifest["artifacts"].items():
        require(row["path"] == name and row["algorithm"] == "sha256", "task artifact binding differs")
        expected[name] = {k: row[k] for k in ("sha256", "size_bytes")}
    require({k: v for k, v in tree.items() if k != "generation-manifest.json"} == expected,
            "delivered files differ from qualified task manifest")


def load(name, row):
    require(binding(row["path"]) == row, "selected implementation changed")
    spec = importlib.util.spec_from_file_location(name, row["path"])
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module


SCHEMA='harbor-0270-successor-layout/v1'
OLD_RELEASE='5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057'
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')
LAYOUT_SHA256='' # Filled only after the exact native candidate and root plan are reviewed.

def validate(layout_binding,*,expected_sha256,remote=False):
    require(layout_binding['sha256']==expected_sha256,'unreviewed successor layout')
    v=checked(layout_binding)
    require(set(v)=={'schema_version','workbook','source','prior_outcome','root_plan','worker_job','authoring_snapshot',
        'authoring_version','profile','native_probe','original_config','runtime_release','runtime_manifest',
        'runtime_manifest_copy','runtime_python','runtime_worker','runtime_probe'},'closed successor layout required')
    require(v['schema_version']==SCHEMA and v['workbook']=='0270','only exact smoke successor supported')
    require(v['runtime_release']==OLD_RELEASE and v['authoring_version']!=OLD_RELEASE,'distinct successor authoring identity required')
    runtime=REMOTE/'releases'/OLD_RELEASE
    require(v['runtime_python']==str(runtime/'venv/bin/python') and v['runtime_worker']['path']==str(runtime/'code/scripts/fleet/worker.py'),'reviewed native runtime differs')
    require(v['runtime_manifest']['path']==str(runtime/'code/FLEET_CODE_MANIFEST.json')
        and v['runtime_manifest']['sha256']==v['runtime_manifest_copy']['sha256'],'native manifest projection changed')
    manifest=checked(v['runtime_manifest'] if remote else v['runtime_manifest_copy'])
    require(manifest['release']==OLD_RELEASE and manifest['files']['scripts/fleet/worker.py']['sha256']==v['runtime_worker']['sha256'],'native runtime worker differs')
    snapshot=checked(v['authoring_snapshot']);root=Path(snapshot['root'])
    require(root.name==v['authoring_version'] and root.is_absolute() and '..' not in root.parts,'exact successor snapshot identity required')
    seen=set()
    for row in snapshot['files']:
        name=Path(row['relative']);require(not name.is_absolute() and '..' not in name.parts and str(name) not in seen,'invalid snapshot inventory')
        seen.add(str(name));require(binding(root/name)['sha256']==row['sha256'],'successor code bytes changed')
    require('xl_mcp_oracle.py' in seen and 'scripts/batch/harbor_environment_worker.py' in seen,'incomplete owning snapshot')
    profile=checked(v['profile']);probe=checked(v['native_probe']);runtime_probe=checked(v['runtime_probe'])
    require(probe.get('status')=='PASS' and probe.get('models_dispatched')==0
        and probe.get('result',{}).get('plan')==v['root_plan'],'exact no-model native probe required')
    require(runtime_probe['schema_version']=='harbor-fleet-successor-runtime-probe/v1'
        and runtime_probe['runtime_release']==OLD_RELEASE and runtime_probe['executable']==v['runtime_python']
        and runtime_probe['runtime_manifest']==v['runtime_manifest'] and runtime_probe['runtime_worker']==v['runtime_worker']
        and runtime_probe['authoring_snapshot']==v['authoring_snapshot'] and runtime_probe['models_dispatched']==0
        and runtime_probe['python_version']=='3.12.3','native runtime proof differs')
    if remote:
        import sys
        require(sys.executable==v['runtime_python'] and binding(Path(sys.executable).resolve())==runtime_probe['native_executable'],'actual native interpreter changed')
    require(binding(v['source']['path'])==v['source'],'source bytes changed')
    old=checked(v['original_config']);prior=checked(v['prior_outcome']);plan=checked(v['root_plan']);job=checked(v['worker_job'])
    require(profile['schema_version']=='0270-oracle-gate-successor/v1' and profile['models_replayed'] is False
        and profile['semantic_budget_reset'] is False and profile['original_snapshot']==old['code'],'preserved original lineage required')
    require(plan['profile']==v['profile'] and plan['snapshot']==v['authoring_snapshot'],'root plan lineage differs')
    require(old['workbook']==plan['workbook']==job['workbook']=='0270' and job['source']==v['source'],'successor workbook/source differs')
    require(prior['status']=='RECONCILIATION_REQUIRED' and prior['automatic_retry'] is False,'prior hold must remain retained')
    require(job['version']==v['authoring_version'] and job['cwd']==str(runtime/'code')
        and job['command'][0]==v['runtime_python'] and job['inputs'][0]==v['root_plan'],'job must keep separate authoring/runtime bindings')
    require(v['profile'] in job['inputs'] and v['authoring_snapshot'] in job['inputs'],'worker must bind reviewed lineage and full snapshot')
    return v

def configuration(layout,new_binding):
    old=checked(layout['original_config']);new=checked(new_binding)
    require(new['code']==layout['authoring_snapshot'],'publication config does not select successor snapshot')
    require({k:v for k,v in new.items() if k!='code'}=={k:v for k,v in old.items() if k!='code'},'continuation changed original config semantics')
    return new


def pack(output, verification, publication, destination, successor_layout):
    """Package exactly the tree independently compared to immutable generation."""
    report = checked(verification)
    require(report.get("schema_version") == "independent-published-task-copy-check/v1"
        and all(report.get(k) is True for k in ("passed", "byte_identical_to_immutable_generation", "current_qualification_verified"))
        and report.get("model_calls") == 0 and report.get("runtime_operations") == 0
        and report["output"] == str(output), "independent publication qualification required")
    published = checked(publication); require(published.get("status") == "PROMOTED", "owning publication did not promote")
    tree = files(output)
    immutable_manifest = checked(report["task_manifest"])
    require(tree == files(Path(report["task_manifest"]["path"]).parent), "verified output differs from immutable generation")
    require(tree["generation-manifest.json"]["sha256"] == report["task_manifest"]["sha256"], "delivered manifest differs")
    manifest_tree(immutable_manifest, tree, report["workbook"])
    require(len(tree) == report["delivered_files"] and sum(x["size_bytes"] for x in tree.values()) == report["delivered_bytes"], "verified delivery inventory changed")
    layout=validate(successor_layout,expected_sha256=LAYOUT_SHA256,remote=True)
    require(report["workbook"]==layout["workbook"],"published workbook differs from successor")
    destination = safe(destination); require(not destination.exists(), "fresh export directory required")
    destination.mkdir(parents=True)
    archive = destination / "task.tar.gz"
    evidence = {"independent-verification.json": verification, "publication-outcome.json": publication,
                "tracker.json": report["tracker"], "release-manifest.json": report["release"],
                "task-generation-manifest.json": report["task_manifest"]}
    layout=validate(successor_layout,expected_sha256=LAYOUT_SHA256,remote=True)
    evidence.update({"successor-layout.json":successor_layout,"successor-snapshot.json":layout["authoring_snapshot"],"successor-profile.json":layout["profile"]})
    rows = {"task/"+k: v for k, v in tree.items()}
    for name, row in evidence.items():
        checked(row); rows["evidence/"+name] = {"sha256": row["sha256"], "size_bytes": Path(row["path"]).stat().st_size}
    with archive.open("xb") as out:
        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            for name, row in sorted(rows.items()):
                path = Path(output) / name[5:] if name.startswith("task/") else Path(evidence[name[9:]]["path"])
                require(sha(path) == row["sha256"], "verified source changed during export")
                info = tar.gettarinfo(str(path), arcname=name)
                require(info.isfile(), "regular export files required")
                info.uid = info.gid = info.mtime = 0; info.uname = info.gname = ""
                with path.open("rb") as stream: tar.addfile(info, stream)
        out.flush(); os.fsync(out.fileno())
    require(files(output) == tree, "published task changed during packaging")
    manifest = retain(destination / "manifest.json", {"schema_version": "harbor-fleet-qualified-return/v1",
        "workbook": report["workbook"], "remote_output": str(output), "verification": verification,
        "publication": publication, "evidence": evidence, "archive_sha256": sha(archive), "files": rows,
        "historical_bytes_rewritten": False, "model_calls": 0, "successor_layout": successor_layout})
    return {"manifest": manifest, "archive": binding(archive)}


def history_binding(plan):
    """Publication reuses the exact central survey selected for fresh authoring."""
    require('history' in plan, 'remote publication requires root-selected central history')
    value = checked(plan['history']); workbook = plan['workbook']
    require(value.get('schema_version') == 'fleet-central-history/v1'
        and workbook in value.get('fresh_ids', []) and workbook in value.get('known_ids', [])
        and set(value.get('roots', [])) <= set(value.get('scan_roots', []))
        and workbook not in value.get('excluded', {})
        and not value.get('histories', {}).get(workbook),
        'central history does not authorize this fresh workbook')
    return plan['history']


def publication_environment(plan, auth, *, action, url, token_file):
    """Never inherit a previous worker's fleet identity or model journal."""
    require(auth['workbook'] == plan['workbook'], 'publication custody workbook differs')
    env = {k: v for k, v in os.environ.items() if not k.startswith('HARBOR_FLEET_')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=plan['code_root'],
        HARBOR_FLEET_URL=url, HARBOR_FLEET_TOKEN_FILE=str(token_file),
        HARBOR_FLEET_WORKBOOK=auth['workbook'], HARBOR_FLEET_OWNER=auth['owner'],
        HARBOR_FLEET_CLAIM_TOKEN=auth['token'], HARBOR_FLEET_FENCE=str(auth['fence']),
        HARBOR_FLEET_PUBLICATION_ONLY='1')
    if action == 'remote':
        history = history_binding(plan)
        env.update(HARBOR_FLEET_HISTORY_PATH=history['path'], HARBOR_FLEET_HISTORY_SHA256=history['sha256'])
    # A publication process is not given a model-gate journal; even a nested
    # owning child cannot acquire a fleet model slot or dispatch a request.
    for key in ('OPENAI_API_KEY', 'LITELLM_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    return env


def publication_only():
    """Runtime guard for this isolated wrapper; owning publication also runs no models."""
    require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY') == '1', 'publication-only custody required')
    require(not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL'), 'publication must not receive a model journal')
    def forbidden(*args, **kwargs):
        raise RuntimeError('model execution is forbidden during publication')
    import xl_transport
    from scripts.batch import harbor_model_executor
    xl_transport.read_response = xl_transport._read_response = xl_transport.model_slot = forbidden
    harbor_model_executor.execute = forbidden


def remote_child(plan_binding):
    plan = checked(plan_binding)
    require(plan["schema_version"] == "harbor-fleet-remote-publication/v1", "remote publication plan required")
    layout=validate(plan["successor_layout"],expected_sha256=LAYOUT_SHA256,remote=True)
    require(plan["code_root"]==checked(layout["authoring_snapshot"])["root"],"owning successor root changed")
    root = safe(plan["code_root"]); sys.path.insert(0, str(root))
    history = history_binding(plan)
    require(os.environ.get('HARBOR_FLEET_WORKBOOK') == plan['workbook']
        and os.environ.get('HARBOR_FLEET_HISTORY_PATH') == history['path']
        and os.environ.get('HARBOR_FLEET_HISTORY_SHA256') == history['sha256'],
        'publication child central history/custody differs')
    from scripts.fleet.history import projected_survey
    certificate = checked(history)
    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))
    publication_only()
    ready = checked(plan["ready"])
    require(ready["status"] == "PUBLICATION_READY" and not ready["publication_performed"]
        and ready["workbook"] == plan["workbook"], "exact remote ready checkpoint required")
    configuration(layout,ready["config"])
    require(ready["plan"]==layout["root_plan"],"ready continuation plan changed")
    job = checked(ready["job"])
    sys.path.insert(0, str(Path(job["driver"]["path"]).parent))
    driver = load("fleet_publication_dispatcher", job["driver"])
    env = driver.setup(job)
    require(env[6] == root and job["config"] == ready["config"], "owning code/config changed")
    require(plan["verifier"] == driver.VERIFIER, "independent verifier differs from owning dispatcher")
    checkpoint, value = driver.successful(ready["outcome"], env)
    require(checkpoint == ready["checkpoint"] and value["status"] == "PUBLICATION_READY", "publication boundary changed")
    request = driver.publication_request(env, value)
    publication = env[2].run(request, Path(plan["journal"]) / "owning-publication")
    require(checked(publication)["status"] == "PROMOTED", "owning publication needs reconciliation")
    verification_dir = Path(plan["journal"]) / "independent"; verification_dir.mkdir()
    driver.verify(job, env, verification_dir)
    verification = binding(verification_dir / "independent-verification.json")
    package = pack(Path(env[5]["output"]), verification, publication, Path(plan["journal"]) / "export",plan["successor_layout"])
    return retain(Path(plan["journal"]) / "result.json", {"status": "REMOTE_VERIFIED", "workbook": plan["workbook"],
        "plan": plan_binding, "publication": publication, "verification": verification, "package": package,
        "model_calls": 0, "automatic_retry": False})


def collect(archive, manifest_binding, output_root, journal, successor_layout):
    """Atomic collection only after complete package and independent report checks."""
    layout=validate(successor_layout,expected_sha256=LAYOUT_SHA256,remote=False)
    manifest = checked(manifest_binding); require(manifest.get("successor_layout")==successor_layout,"returned authoring lineage differs")
    archive = safe(archive); output_root = safe(output_root); journal = safe(journal)
    require(manifest.get("schema_version") == "harbor-fleet-qualified-return/v1" and sha(archive) == manifest["archive_sha256"], "qualified archive integrity failure")
    wb = manifest["workbook"]; require(wb==layout["workbook"],"collected workbook differs from successor"); require(isinstance(wb, str) and len(wb) == 4 and wb.isascii() and wb.isdigit(), "exact workbook required")
    rows = manifest["files"]; require(isinstance(rows, dict) and rows, "closed package inventory required")
    task_files = {}
    for name, row in rows.items():
        require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
            and PurePosixPath(name).as_posix() == name and name.startswith(("task/", "evidence/")), "unsafe package path")
        if name.startswith("task/"): task_files[name[5:]] = row
    require(task_files and "generation-manifest.json" in task_files, "immutable task manifest missing")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        require(len(members) == len(rows) and {m.name for m in members} == set(rows), "package membership differs")
        for member in members:
            row = rows[member.name]
            require(member.isfile() and member.size == row["size_bytes"], "package file type or size differs")
            h = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b""): h.update(chunk)
            require(h.hexdigest() == row["sha256"], "package file hash differs")
        report = json.load(tar.extractfile("evidence/independent-verification.json"))
        require(report.get("schema_version") == "independent-published-task-copy-check/v1"
            and report["workbook"] == wb and report["output"] == manifest["remote_output"]
            and all(report.get(k) is True for k in ("passed", "byte_identical_to_immutable_generation", "current_qualification_verified"))
            and report.get("model_calls") == 0 and report.get("runtime_operations") == 0,
            "independent verification did not pass")
        require(manifest["verification"]["sha256"] == rows["evidence/independent-verification.json"]["sha256"]
            and len(task_files) == report["delivered_files"]
            and sum(r["size_bytes"] for r in task_files.values()) == report["delivered_bytes"], "report/package binding differs")
        for name, bound in manifest["evidence"].items():
            require(rows["evidence/"+name]["sha256"] == bound["sha256"], "remote evidence hash differs")
        task_manifest = json.load(tar.extractfile("task/generation-manifest.json"))
        require(task_manifest["identity"]["workbook_id"] == wb
            and rows["task/generation-manifest.json"]["sha256"] == report["task_manifest"]["sha256"], "immutable generation identity differs")
        manifest_tree(task_manifest, task_files, wb)
        tracker = json.load(tar.extractfile("evidence/tracker.json"))
        require(tracker["workbook_id"] == wb and tracker["execution_state"] == "PROMOTED"
            and tracker["promotion_failure"] is None
            and tracker["promotion_ids"]["task_generation_id"] == task_manifest["generation_id"]
            and rows["evidence/tracker.json"]["sha256"] == report["tracker"]["sha256"], "promoted tracker identity differs")
        output_root.mkdir(parents=True, exist_ok=True); journal.mkdir(parents=True, exist_ok=True)
        target = output_root / (wb + "-outputs")
        # Root holds the durable global slot too; this lock protects local UI and
        # existing collectors sharing the final directory on this machine.
        with (output_root / ".fleet-collection.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            idempotent = target.exists()
            if idempotent:
                require(files(target) == task_files, "existing published task differs; no overwrite")
            else:
                staged = Path(tempfile.mkdtemp(prefix="."+wb+"-verified-", dir=output_root))
                try:
                    for member in members:
                        if not member.name.startswith("task/"): continue
                        destination = staged / member.name[5:]; destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open("xb") as out, tar.extractfile(member) as source:
                            shutil.copyfileobj(source, out); out.flush(); os.fsync(out.fileno())
                        os.chmod(destination, member.mode & 0o777)
                    require(files(staged) == task_files, "staged qualified tree differs")
                    require(not target.exists(), "collection CAS changed")
                    os.rename(staged, target); fsync(output_root)
                finally:
                    # The only removable tree is this invocation's unpublished
                    # staging directory; sources and evidence remain untouched.
                    if staged.exists(): shutil.rmtree(staged)
            require(files(target) == task_files, "collected task changed")
        return retain(journal / "collection.json", {"schema_version": "harbor-fleet-collection/v1",
            "status": "COLLECTED", "workbook": wb, "manifest": manifest_binding,
            "archive_sha256": manifest["archive_sha256"], "output": str(target), "files": task_files,
            "independent_remote_verification": manifest["verification"], "local_byte_integrity_verified": True,
            "idempotent_existing_output": idempotent, "historical_bytes_rewritten": False, "model_calls": 0,"successor_layout":successor_layout})


def controlled(plan_binding, *, action, url, token_file, owner, version):
    """Fresh coordinator claim; root must reconcile the prior hold explicitly."""
    plan = checked(plan_binding); journal = safe(plan["journal"])
    require(action in {'remote', 'collect'}, 'explicit publication action required')
    require(plan.get('schema_version') == ('harbor-fleet-remote-publication/v1' if action == 'remote' else 'harbor-fleet-collection-plan/v1'), 'publication action/plan schema differs')
    layout=validate(plan["successor_layout"],expected_sha256=LAYOUT_SHA256,remote=action=="remote")
    require(plan["workbook"]==layout["workbook"] and plan["source_sha256"]==layout["source"]["sha256"],"publication source differs from successor")
    require(version==layout["authoring_version"] and plan["code_root"]==checked(layout["authoring_snapshot"])["root"],"publication/collection successor identity changed")
    if action=='collect':require(binding(Path(sys.executable).resolve())==plan['collector_runtime'],'collector interpreter changed')
    if action == 'remote': history_binding(plan)
    require(not journal.exists(), "publication attempt exists; reconcile without replay")
    require(shutil.disk_usage(safe(plan['code_root'])).free >= 12*1024**3, 'publication disk reserve unavailable before claim')
    journal.mkdir(parents=True, mode=0o700)
    sys.path.insert(0, str(safe(plan["code_root"])))
    from scripts.fleet.coordinator import Client
    from scripts.batch.hardened_runner import _guarded_process, _process_group_rss_bytes, _process_group_alive
    client = Client(url, token_file); claim_id = str(uuid.uuid4()); operation_id = str(uuid.uuid4())
    intent = retain(journal / "intent.json", {"plan": plan_binding, "action": action, "owner": owner,
        "claim_id": claim_id, "operation_id": operation_id, "version": version, "driver": binding(__file__), "automatic_retry": False})
    claim = client.call("claim", workbook=plan["workbook"], owner=owner, claim_id=claim_id, version=version, lease_seconds=180)
    auth = {k: claim[k] for k in ("workbook", "owner", "token", "fence")}
    start = time.monotonic(); next_heartbeat = 0; acquired = False; rc = None; failure = None; peak = 0
    try:
        require(claim['source_sha256'] == plan['source_sha256'], 'claimed original differs')
        while not acquired:
            client.call("heartbeat", **auth, lease_seconds=180, checkpoint={"intent": intent})
            result = client.call("begin", **auth, kind="publication", operation_id=operation_id, request_sha256=plan_binding["sha256"])
            acquired = result.get("execute") is True
            if not acquired:
                require(result.get("busy") and result.get("state") != "hold", "publication requires reconciliation")
                require(time.monotonic()-start < 3300, "publication wait limit exceeded")
                time.sleep(1)
        retain(journal / "acquired.json", {"wait_seconds": time.monotonic()-start, "operation_id": operation_id})
        env = publication_environment(plan, auth, action=action, url=url, token_file=token_file)
        with (journal / "controller.log").open("xb") as log:
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), action+"-child", "--plan", plan_binding["path"], "--plan-sha256", plan_binding["sha256"]]
            with _guarded_process(command, stdout=log, cwd=Path(plan["code_root"]), env=env, grace_seconds=5) as process:
                started_execution = time.monotonic()
                while True:
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        client.call("heartbeat", **auth, lease_seconds=180, checkpoint={"intent": intent, "pid": process.pid})
                        next_heartbeat = now+20
                    rss = _process_group_rss_bytes(process.pid)
                    require(rss is not None and rss <= 8*1024**3, "original memory guard exceeded or unavailable")
                    peak = max(peak, rss)
                    require(shutil.disk_usage(plan['code_root']).free >= 8*1024**3, 'publication disk reserve exhausted')
                    require(now-started_execution <= 3500, "publication outer timeout exceeded")
                    rc = process.poll()
                    if rc is not None and not _process_group_alive(process.pid): break
                    time.sleep(0.2)
        require(rc == 0, "publication child requires reconciliation")
        result = binding(journal / ("result.json" if action == "remote" else "collection.json")); value = checked(result)
        require(value["status"] == ("REMOTE_VERIFIED" if action == "remote" else "COLLECTED"), "verified publication result missing")
        client.call("end", **auth, kind="publication", operation_id=operation_id, evidence=json.dumps(result, sort_keys=True), outcome="confirmed")
        client.call("finish", **auth, evidence=json.dumps(result, sort_keys=True), disposition="hold" if action == "remote" else "complete")
        return result
    except Exception as exc:
        failure = retain(journal / "reconciliation.json", {"intent": intent, "returncode": rc,
            "failure_type": type(exc).__name__, "peak_rss_bytes": peak, "elapsed_seconds": time.monotonic()-start,
            "automatic_retry": False, "status": "RECONCILIATION_REQUIRED"})
        if acquired:
            try: client.call("end", **auth, kind="publication", operation_id=operation_id, evidence=json.dumps(failure), outcome="uncertain")
            except Exception: pass
        else:
            try: client.call("finish", **auth, evidence=json.dumps(failure), disposition="hold")
            except Exception: pass
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("remote", "collect", "remote-child", "collect-child"))
    p.add_argument("--plan", required=True); p.add_argument("--plan-sha256", required=True)
    for name in ("url", "token-file", "owner", "version"): p.add_argument("--"+name)
    a = p.parse_args(); selected = {"path": str(Path(a.plan).absolute()), "sha256": a.plan_sha256}
    if a.action == "remote-child": result = remote_child(selected)
    elif a.action == "collect-child":
        plan = checked(selected)
        require(plan["schema_version"] == "harbor-fleet-collection-plan/v1", "collection plan required")
        result = collect(Path(plan["archive"]), plan["manifest"], Path(plan["output_root"]), Path(plan["journal"]),plan["successor_layout"])
    else:
        require(all(getattr(a, k) for k in ("url", "token_file", "owner", "version")), "coordinator connection and identity required")
        result = controlled(selected, action=a.action, url=a.url, token_file=a.token_file, owner=a.owner, version=a.version)
    print(json.dumps(result))


if __name__ == "__main__": main()
