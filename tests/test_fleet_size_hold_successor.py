"""Actual retained audit Calls on each explicitly selected frozen successor."""
from pathlib import Path
import pytest
from scripts.fleet import build_size_hold_successor as B
from tests.test_fleet_0190_successor import child,audit_call_check
@pytest.fixture(scope='module',params=sorted(B.CATALOG))
def built(request,tmp_path_factory):
 wb=request.param;value=B.B.read(B.build(wb,tmp_path_factory.mktemp('size-hold-'+wb)/'build'));root=Path(B.B.read(value['snapshot'])['root']);return wb,value,root

def test_exact_original_calls_readable_never_reexecutable(built):
 wb,_,root=built;suffix,_,calls,_=B.CATALOG[wb]
 audit_call_check(root,B.B.Q/'authoring'/(wb+'-'+suffix),calls)

def test_all_public_future_entries_require_global_fleet_gate(built):
 child(built[2],'''
from scripts.batch import harbor_model_executor as E
import xl_transport as T
for f in (lambda:E.execute(None,None,credential='fixture'),lambda:T.read_response(None,timeout=1),lambda:T.model_slot().__enter__()):
 try:f()
 except ValueError as exc:assert 'requires live fleet' in str(exc)
 else:raise AssertionError('ungated successor model path')
''')

def test_full_snapshot_and_budget_identity(built):
 wb,value,root=built;s=B.B.read(value['snapshot']);rows={r['relative']:r['sha256'] for r in s['files']};assert rows['.pipeline-code.json']==B.B.record(root/'.pipeline-code.json')['sha256']
 plan=B.B.read(value['inspection_plan']);assert plan['budget']['ordinal']==1 and plan['budget']['limit']==2
 assert B.B.read(plan['original_plan'])['budget']==plan['budget']
 assert value['preserved_calls']==B.CATALOG[wb][2]

def test_arbitrary_workbook_not_authorized(tmp_path):
 with pytest.raises(ValueError,match='unreviewed workbook'):B.build('9999',tmp_path/'build')
 assert not (tmp_path/'build').exists()
def test_prepared_future_jobs_bind_exact_budget_source_and_latest_guard():
 import importlib.util
 root=B.ROOT/'diagnostics/vm-fleet-20260912'
 for wb in sorted(B.CATALOG):
  p=root/f'holds-{wb}/gated-successor-001/jobs-001';job=B.B.read(B.B.record(p/'worker-job-draft.json'));plan=B.B.read(job['inputs'][0]);old=B.B.read(plan['normalization_plan'])
  assert job['memory_limit_bytes']==8*1024**3 and job['minimum_disk_bytes']==12*1024**3 and job['timeout_seconds']==86400
  assert job['source']==old['bindings']['original_raw_source'] and old['budget']['ordinal']==1 and old['budget']['limit']==2
  assert B.B.read(plan['original_job'])['publish'] is False
  spec=importlib.util.spec_from_file_location('selected_'+wb,job['driver']['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  assert m.SCHEMA=='harbor-fleet-'+wb+'-normalization-continuation/v1'
  assert m.takeover_directory(plan).parent==Path(B.B.read(plan['original_config'])['run_root'])/'lease-takeovers'
  if wb=='0173':assert '/after-disk-003-v5/000/result.json' in plan['unresolved']['path']
  assert not Path(plan['home']).exists()
