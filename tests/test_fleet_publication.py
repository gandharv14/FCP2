import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import pytest
from scripts.fleet import publish as p


def canonical_hash(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)+"\n").encode()).hexdigest()


def qualified_fixture(tmp_path):
    root = tmp_path.resolve(); immutable = root / "immutable"; immutable.mkdir()
    text = immutable / "instruction.md"; text.write_text("Source-backed test task.\n")
    artifacts = {"instruction.md": {"path": "instruction.md", "algorithm": "sha256", "sha256": p.sha(text), "size_bytes": text.stat().st_size}}
    identity = {"workbook_id": "0270", "schema_version": "task-generation-identity/v2", "artifacts": artifacts, "bindings": {}}
    generation_id = canonical_hash(identity)
    task = p.retain(immutable / "generation-manifest.json", {"schema_version": "task-generation/v2",
        "identity": identity, "generation_id": generation_id, "artifacts": artifacts, "bindings": {}})
    output = root / "remote-output"; shutil.copytree(immutable, output)
    tracker = p.retain(root / "tracker.json", {"workbook_id": "0270", "execution_state": "PROMOTED",
        "promotion_failure": None, "promotion_ids": {"task_generation_id": generation_id}})
    release = p.retain(root / "release.json", {"task_generation_id": generation_id})
    publication = p.retain(root / "publication.json", {"status": "PROMOTED"})
    report = p.retain(root / "independent.json", {"schema_version": "independent-published-task-copy-check/v1",
        "workbook": "0270", "passed": True, "byte_identical_to_immutable_generation": True,
        "current_qualification_verified": True, "model_calls": 0, "runtime_operations": 0,
        "output": str(output), "delivered_files": len(p.files(output)),
        "delivered_bytes": sum(x["size_bytes"] for x in p.files(output).values()),
        "tracker": tracker, "release": release, "task_manifest": task})
    return root, output, report, publication


def test_verified_collection_atomic_and_idempotent(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    package = p.pack(output, report, publication, root / "export")
    first = p.collect(Path(package["archive"]["path"]), package["manifest"], root / "final", root / "collect1")
    receipt = p.checked(first)
    assert receipt["status"] == "COLLECTED" and receipt["local_byte_integrity_verified"]
    assert not receipt["idempotent_existing_output"] and receipt["model_calls"] == 0
    assert p.files(root / "final" / "0270-outputs") == p.files(output)
    second = p.collect(Path(package["archive"]["path"]), package["manifest"], root / "final", root / "collect2")
    assert p.checked(second)["idempotent_existing_output"]


def test_existing_publication_never_overwritten(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    package = p.pack(output, report, publication, root / "export")
    target = root / "final" / "0270-outputs"; target.mkdir(parents=True)
    (target / "retained.txt").write_text("existing qualification")
    with pytest.raises(ValueError, match="existing published task differs"):
        p.collect(Path(package["archive"]["path"]), package["manifest"], root / "final", root / "collect")
    assert (target / "retained.txt").read_text() == "existing qualification"


def test_modified_delivered_content_cannot_use_old_pass(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    path = output / "instruction.md"; path.write_bytes(b"X"*path.stat().st_size)
    with pytest.raises(ValueError, match="immutable generation"):
        p.pack(output, report, publication, root / "export")


def test_even_rehashed_archive_cannot_change_qualified_task(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    package = p.pack(output, report, publication, root / "export")
    manifest = p.checked(package["manifest"])
    archive = Path(package["archive"]["path"]); modified = root / "changed.tar.gz"
    with tarfile.open(archive, "r:gz") as old, tarfile.open(modified, "w:gz") as new:
        import io
        for item in old.getmembers():
            raw = old.extractfile(item).read()
            if item.name == "task/instruction.md":
                raw = b"X"*len(raw)
                manifest["files"][item.name]["sha256"] = hashlib.sha256(raw).hexdigest()
            new.addfile(item, io.BytesIO(raw))
    manifest["archive_sha256"] = p.sha(modified)
    selected = p.retain(root / "changed-manifest.json", manifest)
    with pytest.raises(ValueError, match="qualified task manifest"):
        p.collect(modified, selected, root / "final", root / "collect")
    assert not (root / "final").exists()


def test_failed_independent_qualification_cannot_be_packaged(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    rejected = p.checked(report); rejected["current_qualification_verified"] = False
    bad = p.retain(root / "failed-report.json", rejected)
    with pytest.raises(ValueError, match="independent publication qualification"):
        p.pack(output, bad, publication, root / "export")


def test_truncated_transfer_rejected_before_publication(tmp_path):
    root, output, report, publication = qualified_fixture(tmp_path)
    package = p.pack(output, report, publication, root / "export")
    archive = Path(package["archive"]["path"]); archive.write_bytes(archive.read_bytes()[:-20])
    with pytest.raises(ValueError, match="archive integrity"):
        p.collect(archive, package["manifest"], root / "final", root / "collect")
    assert not (root / "final").exists()


def control_fixture(tmp_path):
    root = tmp_path.resolve()
    history = p.retain(root / 'history.json', {'schema_version': 'fleet-central-history/v1',
        'roots': ['/retained/central/history'], 'scan_roots': ['/retained/central/history'], 'known_ids': ['0270'], 'fresh_ids': ['0270'],
        'excluded': {}, 'histories': {}})
    plan = {'schema_version': 'harbor-fleet-remote-publication/v1', 'workbook': '0270',
        'source_sha256': 'a'*64, 'code_root': str(Path(p.__file__).resolve().parents[2]),
        'journal': str(root / 'publication'), 'history': history}
    return plan


def test_controlled_publication_passes_new_live_claim_and_exact_history(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import patch
    import os
    from scripts.fleet import coordinator, history
    from scripts.batch import hardened_runner
    plan = control_fixture(tmp_path)
    from types import SimpleNamespace
    monkeypatch.setattr(p.shutil, 'disk_usage', lambda path: SimpleNamespace(free=32*1024**3))
    bound = p.retain(tmp_path / 'plan.json', plan)
    central = coordinator.Coordinator(tmp_path / 'coordinator.sqlite')
    central.enroll('0270', 'a'*64, 'synthetic inventory')
    central.reconcile('0270', 'synthetic prior owner stopped')
    class Direct:
        def __init__(self,*args,**kwargs): pass
        def call(self, action, **args): return getattr(central, action)(**args)
    monkeypatch.setattr(coordinator, 'Client', Direct)
    monkeypatch.setenv('HARBOR_FLEET_CLAIM_TOKEN', 'obsolete-claim')
    monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL', '/obsolete/model-journal')
    monkeypatch.setenv('LITELLM_API_KEY', 'synthetic-secret')
    observed = {}
    @contextmanager
    def guarded(command, *, env, stdout, **kwargs):
        observed.update(env)
        assert 'remote-child' in command
        assert env['HARBOR_FLEET_OWNER'] == 'publication-owner'
        assert env['HARBOR_FLEET_CLAIM_TOKEN'] != 'obsolete-claim'
        assert env['HARBOR_FLEET_FENCE'] == '1'
        assert 'HARBOR_FLEET_MODEL_JOURNAL' not in env and 'LITELLM_API_KEY' not in env
        with patch.dict(os.environ, env, clear=True):
            # Exercise the actual projection hook with the actual fenced lease.
            excluded, histories = history.projected_survey(['/retained/central/history'], {'0270'})
            assert excluded == {} and histories == {}
        p.retain(Path(plan['journal']) / 'result.json', {'status': 'REMOTE_VERIFIED'})
        class Process:
            pid = 12345
            def poll(self): return 0
        yield Process()
    monkeypatch.setattr(hardened_runner, '_guarded_process', guarded)
    monkeypatch.setattr(hardened_runner, '_process_group_rss_bytes', lambda pid: 1024)
    monkeypatch.setattr(hardened_runner, '_process_group_alive', lambda pid: False)
    result = p.controlled(bound, action='remote', url='http://127.0.0.1:8765',
                         token_file=tmp_path/'worker.token', owner='publication-owner', version='fixture')
    assert p.checked(result)['status'] == 'REMOTE_VERIFIED'
    assert central.status()['operations'][0]['state'] == 'confirmed'
    assert central.status()['workbooks'][0]['state'] == 'hold'
    for path in Path(plan['journal']).rglob('*'):
        if path.is_file():
            assert observed['HARBOR_FLEET_CLAIM_TOKEN'].encode() not in path.read_bytes()
            assert b'synthetic-secret' not in path.read_bytes()


def test_missing_or_changed_history_rejected_before_claim(tmp_path):
    plan = control_fixture(tmp_path)
    del plan['history']
    bound = p.retain(tmp_path/'missing-plan.json', plan)
    with pytest.raises(ValueError, match='root-selected central history'):
        p.controlled(bound, action='remote', url='http://127.0.0.1:1', token_file=tmp_path/'missing', owner='owner', version='fixture')
    assert not Path(plan['journal']).exists()


def test_publication_guard_blocks_model_executor_before_budget_mutation(tmp_path):
    import os
    import subprocess
    import sys
    command = '''
from scripts.fleet.publish import publication_only
import xl_transport
from scripts.batch import harbor_model_executor
publication_only()
for call in (lambda:xl_transport.read_response(None,timeout=1),lambda:harbor_model_executor.execute(None,None,credential='fixture')):
    try:call()
    except RuntimeError as exc:assert 'forbidden during publication' in str(exc)
    else:raise AssertionError('model dispatch was not blocked')
print('publication-only guard passed')
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith('HARBOR_FLEET_')}
    env['HARBOR_FLEET_PUBLICATION_ONLY'] = '1'
    result = subprocess.run([sys.executable, '-c', command], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'publication-only guard passed'


def test_original_publication_memory_guard_enters_reconciliation_hold(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from scripts.fleet import coordinator
    from scripts.batch import hardened_runner
    from types import SimpleNamespace
    monkeypatch.setattr(p.shutil,'disk_usage',lambda path:SimpleNamespace(free=32*1024**3))
    plan=control_fixture(tmp_path);bound=p.retain(tmp_path/'plan.json',plan)
    central=coordinator.Coordinator(tmp_path/'coordinator.sqlite')
    central.enroll('0270','a'*64,'fixture');central.reconcile('0270','fixture')
    class Direct:
        def __init__(self,*args,**kwargs):pass
        def call(self,action,**args):return getattr(central,action)(**args)
    monkeypatch.setattr(coordinator,'Client',Direct)
    @contextmanager
    def guarded(*args,**kwargs):
        class Process:
            pid=123
            def poll(self):return None
        yield Process()
    monkeypatch.setattr(hardened_runner,'_guarded_process',guarded)
    monkeypatch.setattr(hardened_runner,'_process_group_rss_bytes',lambda pid:8*1024**3+1)
    with pytest.raises(ValueError,match='original memory guard'):
        p.controlled(bound,action='remote',url='http://127.0.0.1:1',token_file=tmp_path/'token',owner='owner',version='fixture')
    status=central.status()
    assert status['workbooks'][0]['state']=='hold' and status['operations'][0]['state']=='hold'
    assert (Path(plan['journal'])/'reconciliation.json').is_file()


def test_publication_disk_guard_prevents_claim_and_journal_creation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    plan=control_fixture(tmp_path);bound=p.retain(tmp_path/'plan.json',plan)
    monkeypatch.setattr(p.shutil,'disk_usage',lambda path:SimpleNamespace(free=12*1024**3-1))
    with pytest.raises(ValueError,match='disk reserve unavailable before claim'):
        p.controlled(bound,action='remote',url='http://127.0.0.1:1',token_file=tmp_path/'missing',owner='fixture',version='fixture')
    assert not Path(plan['journal']).exists()
