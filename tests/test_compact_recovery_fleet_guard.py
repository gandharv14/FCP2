from pathlib import Path
import importlib.util,os,json
import pytest
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/four-resume-diagnostics/guard_compact_recovery.py'
def module():
 s=importlib.util.spec_from_file_location('guard_recovery_test',PATH);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_real_pinned_fleet_capacity_and_duplicate_job(tmp_path,monkeypatch):
 m=module();_,_,_,_,f,_=m.setup('0298');monkeypatch.setattr(f,'FLEET',tmp_path/'fleet')
 with f.fleet_slot('0298') as one:
  assert len(one)==2
  with pytest.raises(BlockingIOError):
   with f.fleet_slot('0298'):pytest.fail('duplicate job')
  with f.fleet_slot('0051'):
   with pytest.raises(ValueError,match='capacity'):
    with f.fleet_slot('0062'):pytest.fail('third job')
 with f.fleet_slot('0298'):pass

@pytest.mark.parametrize('rc',[0,1])
def test_outer_guard_retains_both_fds_and_seals_failure_without_replay(tmp_path,monkeypatch,rc):
 m=module();driver,fleet,snap,root,f,_=m.setup('0298');monkeypatch.setattr(f,'FLEET',tmp_path/'fleet');monkeypatch.setattr(m,'HERE',tmp_path);seen=[]
 def guard(command,**kw):
  assert kw['timeout_seconds']==1050 and kw['memory_limit_bytes']==8*1024**3
  assert len(kw['guardian_lock_fds'])==2
  for fd in kw['guardian_lock_fds']:os.fstat(fd)
  with pytest.raises(BlockingIOError):
   with f.fleet_slot('0298'):pytest.fail('job lock not retained')
  seen.append(command)
  if rc==0:(tmp_path/'0298-compact-recovery-result.json').write_text('{}')
  return rc
 monkeypatch.setattr(m,'setup',lambda wb:(driver,fleet,snap,root,f,guard))
 if rc:
  with pytest.raises(SystemExit):m.run('0298')
 else:m.run('0298')
 outcome=json.loads((tmp_path/'guarded-compact-recoveries/0298/outcome.json').read_text());assert outcome['automatic_retry'] is False
 assert outcome['status']==('COMPLETED_OPERATION' if rc==0 else 'RECONCILIATION_REQUIRED')
 with pytest.raises(FileExistsError):m.run('0298')
 assert len(seen)==1

def test_changed_driver_and_unknown_workbook_fail_before_guard(monkeypatch):
 m=module();monkeypatch.setitem(m.DRIVERS,'0298','0'*64)
 with pytest.raises(AssertionError):m.setup('0298')
 with pytest.raises(AssertionError):m.setup('9999')
