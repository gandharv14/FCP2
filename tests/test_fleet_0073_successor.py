"""Closed frozen-code migration; no real endpoint or evidence mutation."""
import json,os,subprocess,sys,shutil
from pathlib import Path
import pytest
from scripts.fleet import build_0073_successor as B
from tests.test_fleet_0190_successor import child,test_all_future_public_model_entrypoints_require_fleet,test_actual_executor_uses_one_fleet_operation_and_no_transport_retry
@pytest.fixture(scope='module')
def built(tmp_path_factory):
 result=B.B.read(B.build(tmp_path_factory.mktemp('0073')/'build'));return result,Path(B.B.read(result['snapshot'])['root'])
def test_exact_legacy_read_and_no_mutation(built):
 child(built[1],'''
from scripts.fleet import legacy_0073 as L
from pathlib import Path
import copy
p=L.fleet_profile();assert len(p['legacy_calls'])==3
for path,row in p['legacy_calls'].items():
 old=L.read(row['plan']);before={p.name:p.read_bytes() for p in Path(path).iterdir() if p.is_file()}
 current=dict(old);current.update(fleet_transport_policy='single-dispatch-durable-global-gate/v1',max_transport_retries=0)
 assert L.fleet_legacy_plan(Path(path),current)==old
 assert 'fleet_transport_policy' not in old
 current['attempt_id']='other'
 try:L.fleet_legacy_plan(Path(path),current)
 except ValueError:pass
 else:raise AssertionError('changed identity allowed')
 assert before=={p.name:p.read_bytes() for p in Path(path).iterdir() if p.is_file()}
try:L.fleet_legacy_plan(Path('/tmp/unlisted'),{})
except ValueError:pass
else:raise AssertionError('unlisted call allowed')
''')
def test_reject_unreviewed_inventory(built,tmp_path):
 root=tmp_path/'changed';shutil.copytree(built[1],root);p=root/'plain_eligibility.py';p.write_bytes(p.read_bytes()+b'\n# changed\n')
 child(root,'''
from scripts.fleet import legacy_0073 as L
try:L.fleet_profile()
except ValueError as exc:assert 'unreviewed full code inventory' in str(exc)
else:raise AssertionError('unreviewed code allowed')
''')
def test_snapshot_includes_exact_pipeline_manifest(built):
 snapshot=B.B.read(built[0]['snapshot'])
 rows={r['relative']:r['sha256'] for r in snapshot['files']}
 assert rows['.pipeline-code.json']==B.B.record(built[1]/'.pipeline-code.json')['sha256']
 assert set(rows)=={str(p.relative_to(built[1])) for p in built[1].rglob('*') if p.is_file() and '__pycache__' not in p.parts}

def test_disk_guard_blocks_before_stage_or_child(tmp_path,monkeypatch):
 from scripts.fleet import normalization_0073 as N
 from types import SimpleNamespace
 home=tmp_path/'continuation';bound=N.retain(tmp_path/'plan.json',dict(home=str(home),code_root=str(tmp_path)))
 monkeypatch.setattr(N.shutil,'disk_usage',lambda p:SimpleNamespace(free=N.MEMORY-1))
 with pytest.raises(ValueError,match='8-GiB free-space reserve'):
  N.guarded(bound,'dispatch',{}, {},'partition-0000-round-1',None)
 assert not home.exists()

def test_campaign_limit_cannot_relax_before_configuration():
 from scripts.fleet import normalization_0073 as N
 from types import SimpleNamespace
 called=[];base=SimpleNamespace(_campaign=lambda c:called.append(c))
 with pytest.raises(ValueError,match='limit must remain one'):
  N.configure_campaign(base,SimpleNamespace(model_slots=2))
 assert called==[]
def test_all_three_actual_audit_calls_remain_readable_not_reexecutable(built):
 from tests.test_fleet_0190_successor import audit_call_check
 audit_call_check(built[1],B.HOME,3)
def test_takeover_uses_owning_campaign_lease_directory():
 from scripts.fleet import normalization_0073 as N
 plan={'original_config':B.B.record(B.HOME/'config.json')}
 directory=N.takeover_directory(plan)
 assert directory.parent==B.HOME/'lease-takeovers'
 assert not directory.exists()
