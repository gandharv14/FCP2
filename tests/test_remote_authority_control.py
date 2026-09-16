from pathlib import Path
import json
import pytest
from scripts.fleet import remote_authority_control as C,remote_authority_fence as F,remote_coordinator_preparation as R
from tests.test_remote_coordinator_preparation import database

def keep(p,v):p.write_text(json.dumps(v));return R.binding(p)

def selection(tmp_path):
 db=tmp_path/'fleet.sqlite3';database(db)
 old=dict(database=str(db),url='http://127.0.0.1:8765',private_directory=str(tmp_path/'private'),code_sha256='a'*64)
 o=keep(tmp_path/'old.json',old);auth=keep(tmp_path/'auth',{});stop=keep(tmp_path/'stop',{})
 f=F.seal_database(db,R.ledger(db),auth,stop,suffix='001');v=F.validate_fence(db)
 native=keep(tmp_path/'native.json',dict(old,database='/home/fleet/authority/fleet.sqlite3'))
 migration=keep(tmp_path/'migration.json',dict(status='REMOTE_AUTHORITY_ACTIVE_VERIFIED',original_receipt=o,native_receipt=native,hub='hhu-server-7',local_fence=f,ledger=v['ledger']))
 runner=tmp_path/'runner.py';runner.write_text('# selected remote runner')
 return dict(old,remote_authority=dict(schema_version=C.SCHEMA,original_receipt=o,migration=migration,native_receipt=native,hub='hhu-server-7',remote_runner=R.binding(runner)))

def test_root_api_identity_preserved_and_mutex_shared_with_original(tmp_path):
 p=selection(tmp_path);assert C.path(p)==tmp_path/'job-launch.lock'
 assert p['url']=='http://127.0.0.1:8765'and Path(p['database']).is_dir()

@pytest.mark.parametrize('field,value',[('url','http://10.0.0.1:8765'),('database','/tmp/mirror.sqlite3'),('private_directory','/tmp/foreign')])
def test_changed_root_authority_identity_rejected(tmp_path,field,value):
 p=selection(tmp_path);p[field]=value
 with pytest.raises(ValueError):C.path(p)

def test_missing_or_changed_migration_fence_blocks_root(tmp_path):
 p=selection(tmp_path);Path(p['database'],'complete.json').write_text('{}')
 with pytest.raises((ValueError,KeyError)):C.path(p)

def test_generic_arbitrary_remote_controller_rejected_before_import(tmp_path):
 p=dict(schema_version='fleet-remote-admin-invocation/v1',role='arbitrary',controller={},plan={},native_receipt={},journal=str(tmp_path/'out'),execute=True,dependencies=[])
 with pytest.raises(ValueError):C.native_run(keep(tmp_path/'p.json',p))

@pytest.mark.parametrize('flags',[['-B'],['-I','-B']])
def test_native_service_actual_python_or_singleton_argv(flags):
 db='/home/fleet/authority/fleet.sqlite3';server='/home/fleet/authority/coordinator.py'
 argv=['/usr/bin/python3',*flags,server,'serve','--database',db,'--port','8765','--worker-token-file','/home/fleet/authority/private/worker.token','--admin-token-file','/home/fleet/authority/private/admin.token']
 assert C.authority_command(argv,server,db)==argv
 assert C.authority_command(['/usr/bin/flock','--exclusive','--nonblock','--no-fork','/home/fleet/authority/authority.lock',*argv],server,db)==argv
 with pytest.raises(ValueError):C.authority_command([*argv,'--port','9876'],server,db)
 with pytest.raises(ValueError):C.authority_command(['/usr/bin/python3','-c',server,*argv[len(flags)+2:]],server,db)


def test_root_remote_dispatch_uses_iap_and_stdin_without_credentials(tmp_path):
 p=selection(tmp_path);selected,old,native,m=C.connection(p);native['command']=['/usr/bin/python3','-B','/home/fleet/authority/coordinator.py','serve']
 runtime=dict(package_root=str(tmp_path),dependencies=[selected['remote_runner']])
 argv,stdin=C.remote_argv(native,selected,{'path':'/native/selected.json','sha256':'c'*64},runtime)
 assert argv[:4]==['gcloud','compute','ssh','hhu-server-7']and '--tunnel-through-iap'in argv
 assert b'admin.token'not in stdin and b'worker.token'not in stdin
 assert json.loads(stdin)['invocation']['path']=='/native/selected.json'
 with pytest.raises(ValueError):C.remote_argv(native,selected,{},dict(package_root=str(tmp_path),dependencies=[]))
