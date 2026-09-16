from pathlib import Path
import base64,json
import pytest
from scripts.fleet import remote_authority_bundle as B,remote_coordinator_preparation as R
from tests.test_remote_coordinator_preparation import database

def keep(path,v):path.write_text(json.dumps(v));return R.binding(path)

def selected(tmp_path,monkeypatch):
 db=tmp_path/'source.sqlite3';database(db);state=R.ledger(db);server=b'# original coordinator';worker=b'w'*64;admin=b'a'*64
 support={x:b''for x in B.SUPPORT};support['scripts/fleet/coordinator.py']=server
 fence=keep(tmp_path/'fence.json',dict(status='LOCAL_AUTHORITY_DURABLY_FENCED',ledger=state,sealed_database=dict(sha256=B.digest(db.read_bytes()))))
 auth=keep(tmp_path/'authorization',{});original=keep(tmp_path/'original.json',dict(command=['python','-B','/original/coordinator.py','serve'],code_sha256=B.digest(server)));plan=keep(tmp_path/'migration-plan.json',dict(original_receipt=original,authorization=auth))
 retired=keep(tmp_path/'retirement.json',dict(status='LOCAL_AUTHORITY_RETIRED_FOR_REMOTE_TRANSFER',plan=plan,fence=fence,ledger=state,token_sha256={'worker.token':B.digest(worker),'admin.token':B.digest(admin)},remote_started=False))
 root=tmp_path/'native';original=Path.is_relative_to
 monkeypatch.setattr(Path,'is_relative_to',lambda p,*a:True if a==('/home/gmahajan_labelbox_com/synthetic-data-fleet',)and p==root else original(p,*a))
 monkeypatch.setattr(B.socket,'gethostname',lambda:'hhu-server-7.internal')
 p=dict(schema_version=B.SCHEMA,hub='hhu-server-7',remote_root=str(root),retirement=retired,fence=fence,ledger=keep(tmp_path/'ledger.json',state),database_sha256=B.digest(db.read_bytes()),server=dict(path='/original/coordinator.py',sha256=B.digest(server)),support={k:B.digest(v)for k,v in support.items()},token_sha256={'worker.token':B.digest(worker),'admin.token':B.digest(admin)},authorization=auth)
 enc=lambda v:base64.b64encode(v).decode()
 payload=dict(database=enc(db.read_bytes()),server=enc(server),support={k:enc(v)for k,v in support.items()},**{'worker.token':enc(worker),'admin.token':enc(admin)})
 return p,payload,root

def test_native_receiving_preserves_all_rows_and_private_token_modes(tmp_path,monkeypatch):
 p,payload,root=selected(tmp_path,monkeypatch);row=keep(tmp_path/'selection.json',p);result=B.receive(row,payload)
 v=R.checked(result);assert v['ledger']==R.checked(p['ledger'])and not v['service_started']
 assert (root/'private/worker.token').read_bytes()==b'w'*64 and (root/'private/worker.token').stat().st_mode&0o777==0o600
 assert 'wwww'not in json.dumps(v)and 'aaaa'not in json.dumps(v)
 with pytest.raises(ValueError):B.receive(row,payload)

@pytest.mark.parametrize('change',[lambda p,v:v.update(oauth='forbidden'),lambda p,v:v.update(database=base64.b64encode(b'changed').decode()),lambda p,v:v.update(**{'worker.token':v['admin.token']}),lambda p,v:p.update(hub='hhu-server-8')])
def test_foreign_credential_byte_or_host_rejected_before_directory(tmp_path,monkeypatch,change):
 p,payload,root=selected(tmp_path,monkeypatch);change(p,payload)
 with pytest.raises(ValueError):B.receive(keep(tmp_path/'selection.json',p),payload)
 assert not root.exists()
