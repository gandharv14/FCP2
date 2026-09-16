from pathlib import Path
import json,sqlite3
import pytest
from scripts.fleet import remote_authority_fence as F
from scripts.fleet import remote_coordinator_preparation as R
from tests.test_remote_coordinator_preparation import database

def evidence(tmp_path,name):
 p=tmp_path/name;p.write_text('{}');return R.binding(p)

def test_atomic_fence_preserves_every_byte_and_blocks_original_sqlite(tmp_path):
 db=tmp_path/'fleet.sqlite3';database(db);before=db.read_bytes();state=R.ledger(db)
 proof=F.seal_database(db,state,evidence(tmp_path,'auth'),evidence(tmp_path,'stopped'),suffix='001')
 assert db.is_dir()and (tmp_path/'fleet.sqlite3.retired-001').read_bytes()==before
 assert F.validate_fence(db)['ledger']==state
 with pytest.raises(sqlite3.OperationalError):sqlite3.connect(db)
 with pytest.raises(ValueError):F.seal_database(db,state,evidence(tmp_path,'auth2'),evidence(tmp_path,'stopped2'),suffix='002')
 assert R.binding(proof['path'])==proof

def test_exchange_interruption_already_fences_old_path(tmp_path,monkeypatch):
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db);original=F.exchange
 def interrupted(a,b):original(a,b);raise RuntimeError('power-loss boundary')
 monkeypatch.setattr(F,'exchange',interrupted)
 with pytest.raises(RuntimeError):F.seal_database(db,state,evidence(tmp_path,'auth'),evidence(tmp_path,'stopped'),suffix='001')
 with pytest.raises(sqlite3.OperationalError):sqlite3.connect(db)
 assert R.ledger(tmp_path/'fleet.sqlite3.retired-001')==state
 assert (db/'retirement.json').exists()and not (db/'complete.json').exists()

@pytest.mark.parametrize('change',["UPDATE workbooks SET state='active'","UPDATE operations SET state='active' WHERE operation_id='0'","UPDATE quarantine_renewals SET record='drift'"])
def test_live_or_drifted_ledger_never_fenced(tmp_path,change):
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db)
 with sqlite3.connect(db)as c:c.execute(change)
 with pytest.raises(ValueError):F.seal_database(db,state,evidence(tmp_path,'auth'),evidence(tmp_path,'stopped'),suffix='001')
 assert db.is_file()and not (tmp_path/'fleet.sqlite3.retired-001').exists()

def test_nonempty_wal_and_consumed_namespace_refused(tmp_path):
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db);wal=Path(str(db)+'-wal');wal.write_bytes(b'uncheckpointed')
 with pytest.raises(ValueError):F.seal_database(db,state,evidence(tmp_path,'auth'),evidence(tmp_path,'stopped'),suffix='001')
 assert db.is_file()

@pytest.mark.parametrize('field,value',[('status','invented'),('ledger',{}),('automatic_restart',True)])
def test_completed_fence_decision_tampering_rejected(tmp_path,field,value):
 db=tmp_path/'fleet.sqlite3';database(db);state=R.ledger(db)
 F.seal_database(db,state,evidence(tmp_path,'auth'),evidence(tmp_path,'stopped'),suffix='001')
 path=db/'complete.json';record=json.loads(path.read_bytes());record[field]=value;path.write_text(json.dumps(record))
 with pytest.raises(ValueError):F.validate_fence(db)
