from pathlib import Path
import json,subprocess,sys,time
import pytest
from scripts.fleet import remote_coordinator_migration as M,remote_coordinator_preparation as R
from tests.test_remote_coordinator_preparation import database

def keep(p,v):p.write_text(json.dumps(v));return R.binding(p)

def test_once_retirement_stops_only_selected_fixture_and_fences_legacy_starts(tmp_path,monkeypatch):
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db)
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
 try:
  until=time.monotonic()+3
  while M.identity(child.pid)is None and time.monotonic()<until:time.sleep(.01)
  identity=M.identity(child.pid);legacy=tmp_path/'legacy.lock';legacy.write_bytes(b'original lock bytes')
  p=dict(process_identity=identity,authorization=keep(tmp_path/'auth',{}),retirement_id='test-001')
  private=tmp_path/'private';private.mkdir();(private/'worker.token').write_text('w'*64);(private/'admin.token').write_text('a'*64)
  old=dict(database=str(db),pid=child.pid,legacy_locks=[str(legacy)],private_directory=str(private))
  monkeypatch.setattr(M,'load',lambda row:(p,old,None,None));monkeypatch.setattr(M,'current',lambda *args:state)
  # The real app's production8765 may be open; this fixture child has no listener.
  monkeypatch.setattr(M,'port_open',lambda:False)
  original_identity=M.identity
  monkeypatch.setattr(M,'identity',lambda pid:None if child.poll()is not None else original_identity(pid))
  result=M.retire({'path':'fixture','sha256':'f'*64},tmp_path/'journal',execute=True)
  assert child.wait(timeout=3)==-15 and db.is_dir()and legacy.is_dir()
  assert (tmp_path/'legacy.lock.retired-test-001').read_bytes()==b'original lock bytes'
  value=R.checked(result);assert value['status']=='LOCAL_AUTHORITY_RETIRED_FOR_REMOTE_TRANSFER'and not value['remote_started']
  with pytest.raises((ValueError,IsADirectoryError)):M.retire({},tmp_path/'journal',execute=True)
 finally:
  if child.poll()is None:child.terminate();child.wait(3)

def test_late_active_claim_refuses_before_signal(tmp_path,monkeypatch):
 import sqlite3
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db)
 with sqlite3.connect(db)as c:c.execute("UPDATE workbooks SET state='active'")
 p=dict(process_identity='unused',authorization=keep(tmp_path/'auth',{}),retirement_id='test-001')
 old=dict(database=str(db),pid=123456,legacy_locks=[])
 monkeypatch.setattr(M,'load',lambda row:(p,old,None,None));monkeypatch.setattr(M,'current',lambda *args:state)
 def signal_forbidden(*args):raise AssertionError('must refuse before signal')
 monkeypatch.setattr(M.os,'kill',signal_forbidden)
 with pytest.raises(ValueError,match='late admitted'):M.retire({},tmp_path/'journal',execute=True)
 assert db.is_file()and (tmp_path/'journal/intent.json').is_file()and not (tmp_path/'journal/complete.json').exists()
