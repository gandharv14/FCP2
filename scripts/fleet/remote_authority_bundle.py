"""Receive a sealed coordinator bundle on its selected native hub.

The payload is supplied through private stdin transport and never returned or
logged. Only coordinator worker/admin tokens are accepted; account credentials
have no payload field. Receipt-only outputs contain hashes and ledger counts.
This stage does not open a listener or install/start a system service.
"""
from pathlib import Path
import base64,hashlib,json,os,socket
from scripts.fleet.remote_coordinator_preparation import binding,checked,ledger,quiescent,require,snapshot_equal
from scripts.fleet.remote_authority_fence import retain,fsync_dir

SCHEMA='fleet-remote-authority-bundle/v1'
SUPPORT={'scripts/__init__.py','scripts/fleet/__init__.py','scripts/fleet/coordinator.py','scripts/fleet/quarantine_renewal.py'}

def digest(data):return hashlib.sha256(data).hexdigest()

def decode(value):
    require(type(value)is str,'encoded selected payload required')
    return base64.b64decode(value,validate=True)

def receive(selection,payload):
    p=checked(selection)
    require(set(p)=={'schema_version','hub','remote_root','retirement','fence','ledger','database_sha256','server','support','token_sha256','authorization'}and p['schema_version']==SCHEMA,'closed remote authority bundle required')
    require(socket.gethostname().split('.')[0]==p['hub'],'bundle destination differs from selected VM')
    root=Path(p['remote_root']);require(root.is_absolute()and root.is_relative_to('/home/gmahajan_labelbox_com/synthetic-data-fleet')and not root.exists()and not any(x.is_symlink()for x in root.parents),'fresh native authority directory required')
    retirement=checked(p['retirement']);fence=checked(p['fence']);expected=checked(p['ledger']);quiescent(expected)
    require(retirement['status']=='LOCAL_AUTHORITY_RETIRED_FOR_REMOTE_TRANSFER'and retirement['fence']==p['fence']and retirement['ledger']==expected and retirement['remote_started']is False,'completed local retirement required')
    migration_plan=checked(retirement['plan']);original=checked(migration_plan['original_receipt'])
    require(migration_plan['authorization']==p['authorization']and p['server']==dict(path=original['command'][2],sha256=original['code_sha256'])and p['token_sha256']==retirement['token_sha256'],'original server, token custody or migration authorization differs')
    require(fence['status']=='LOCAL_AUTHORITY_DURABLY_FENCED'and fence['ledger']==expected and fence['sealed_database']['sha256']==p['database_sha256'],'sealed original database differs')
    require(binding(p['authorization']['path'])==p['authorization'],'migration authorization changed')
    require(type(payload)is dict and set(payload)=={'database','server','worker.token','admin.token','support'}and set(payload['support'])==set(p['support'])==SUPPORT,'no undeclared credentials or executable payloads')
    require(set(p['token_sha256'])=={'worker.token','admin.token'},'only original coordinator tokens may move')
    raw={name:decode(payload[name])for name in ('database','server','worker.token','admin.token')}
    require(digest(raw['database'])==p['database_sha256']and digest(raw['server'])==p['server']['sha256'],'sealed database or original authority code changed')
    require(all(digest(raw[k])==p['token_sha256'][k]and len(raw[k].strip())>=32 for k in ('worker.token','admin.token'))and raw['worker.token']!=raw['admin.token'],'unchanged distinct coordinator tokens required')
    support={name:decode(value)for name,value in payload['support'].items()}
    require(all(digest(value)==p['support'][name]for name,value in support.items())and support['scripts/fleet/coordinator.py']==raw['server'],'selected support closure changed')
    root.mkdir(mode=0o700);retain(root/'transfer-intent.json',dict(selection=selection,automatic_retry=False,service_started=False))
    def write(path,data):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb')as f:f.write(data);f.flush();os.fsync(f.fileno())
        fsync_dir(path.parent)
    for name,target in [('database','fleet.sqlite3'),('server','coordinator.py'),('worker.token','private/worker.token'),('admin.token','private/admin.token')]:write(root/target,raw[name])
    for name,value in support.items():write(root/name,value)
    snapshot_equal(expected,ledger(root/'fleet.sqlite3'))
    return retain(root/'transfer-complete.json',dict(status='SEALED_REMOTE_AUTHORITY_BUNDLE_VERIFIED',selection=selection,database=binding(root/'fleet.sqlite3'),server=binding(root/'coordinator.py'),ledger=expected,token_sha256=p['token_sha256'],service_started=False,account_credentials_transferred=False))
