"""Once-only local retirement stage of a private remote-authority migration.

The selected original no-admission census validates all eight native hosts.
This module never starts a remote authority. It seals the stopped local database
and legacy start paths; a subsequent qualified transfer/activation consumes its
receipt without replaying this stop or any workbook/model operation.
"""
from pathlib import Path
import contextlib,fcntl,importlib.util,json,os,signal,socket,sqlite3,subprocess,sys,time
from scripts.fleet.remote_coordinator_preparation import binding,checked,ledger,quiescent,require,snapshot_equal
from scripts.fleet.remote_authority_fence import retain,seal_database,exchange,fsync_dir

SCHEMA='fleet-remote-authority-retirement-plan/v1'

def module(name,row):
    require(binding(row['path'])==row,'selected migration code changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def identity(pid):
    r=subprocess.run(['/bin/ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=5)
    return r.stdout.strip()if r.returncode==0 else None

def port_open():
    with socket.socket()as s:s.settimeout(1);return s.connect_ex(('127.0.0.1',8765))==0

def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','helper','authorization','original_receipt','process_identity','maintenance','upgrade_reader','drain_proofs','expected_ledger','transport_proof','remote_preparation','retirement_id'}and p['schema_version']==SCHEMA and p['helper']==binding(Path(__file__).absolute()),'closed selected authority retirement required')
    require(binding(p['authorization']['path'])==p['authorization'],'migration authorization changed')
    old=checked(p['original_receipt']);require(old['url']=='http://127.0.0.1:8765'and old['command'][3]=='serve'and binding(old['command'][2])['sha256']==old['code_sha256'],'original authority command changed')
    require(old['command'][old['command'].index('--database')+1]==old['database'],'database command differs')
    transport=checked(p['transport_proof']);require(transport['status']=='ALL_PRIVATE_FORWARDERS_AUTHENTICATED_CANARY'and transport['preparation']==p['remote_preparation']and transport['production_changed']is False,'actual prepared private transport proof required')
    preparation=checked(p['remote_preparation']);require(preparation['original_receipt']==p['original_receipt'],'foreign remote preparation')
    O=module('original_migration_authority',dict(path=old['command'][2],sha256=old['code_sha256']));U=module('original_migration_admission_reader',p['upgrade_reader'])
    return p,old,O,U

def current(p,old,O,U):
    require(identity(old['pid'])==p['process_identity']and old['command'][2]in p['process_identity'],'actual old listener identity changed')
    status=O.Client(old['url'],Path(old['private_directory'])/'admin.token').call('status')
    require(not any(w['state']=='active'for w in status['workbooks'])and not any(o['state']=='active'for o in status['operations']),'active custody forbids migration')
    U.author_state(status,p['drain_proofs'],p['maintenance'],time.time(),existing_policy=status.get('author_capacity_policy'))
    actual=ledger(old['database']);quiescent(actual);snapshot_equal(checked(p['expected_ledger']),actual)
    return actual

@contextlib.contextmanager
def locks(paths):
    held=[]
    try:
        for path in paths:
            path=Path(path);require(path.is_absolute()and not any(x.is_symlink()for x in(path,*path.parents)),'canonical mutex required')
            fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);held.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(held):os.close(fd)

def retire(row,directory,*,execute=False):
    p,old,O,U=load(row);home=Path(directory);base=Path(old['database']).parent
    with locks([base/'coordinator-recovery.lock',base/'job-launch.lock']):
        actual=current(p,old,O,U)
        if not execute:return dict(status='REMOTE_MIGRATION_RETIREMENT_VALIDATED',plan=row,ledger=actual,mutations=0)
        require(home.is_absolute()and not home.exists()and not any(x.is_symlink()for x in home.parents),'fresh retirement journal required');home.mkdir(mode=0o700)
        intent=retain(home/'intent.json',dict(plan=row,ledger=actual,automatic_retry=False))
        # Reserve the SQLite writer before the last check and stop: delayed
        # native admissions cannot commit between the proof and listener stop.
        with sqlite3.connect('file:'+old['database']+'?mode=rw',uri=True,isolation_level=None,timeout=3)as barrier:
            barrier.execute('BEGIN IMMEDIATE')
            require(barrier.execute("SELECT count(*) FROM workbooks WHERE state='active'").fetchone()[0]==0 and barrier.execute("SELECT count(*) FROM operations WHERE state='active'").fetchone()[0]==0,'late admitted operation')
            require(identity(old['pid'])==p['process_identity'],'listener changed before stop')
            os.kill(old['pid'],signal.SIGTERM)
            deadline=time.monotonic()+10
            while identity(old['pid'])is not None and time.monotonic()<deadline:time.sleep(.1)
            require(identity(old['pid'])is None and not port_open(),'old listener not proven stopped; do not activate remote authority')
            require(barrier.total_changes==0,'migration barrier changed rows');barrier.rollback()
        with locks(old['legacy_locks']):
            with sqlite3.connect(old['database'])as db:
                answer=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone();require(answer[0]==0,'stopped SQLite checkpoint busy')
            snapshot_equal(actual,ledger(old['database']))
            stopped=retain(home/'stopped.json',dict(intent=intent,pid=old['pid'],process_absent=True,listener_absent=True,ledger=actual,at=time.time()))
            fence=seal_database(old['database'],actual,p['authorization'],stopped,suffix=p['retirement_id'])
            legacy=[]
            for name in old['legacy_locks']:
                path=Path(name);original=binding(path);sealed=path.with_name(path.name+'.retired-'+p['retirement_id']);require(not sealed.exists(),'legacy retirement namespace consumed');sealed.mkdir(mode=0o700)
                retain(sealed/'retirement.json',dict(database_fence=fence,original=original,automatic_restart=False));exchange(path,sealed);require(binding(sealed)['sha256']==original['sha256'],'legacy lock bytes changed');legacy.append(dict(path=str(path),original=original,sealed=binding(sealed)))
            tokens={name:binding(Path(old['private_directory'])/name)['sha256']for name in ('worker.token','admin.token')}
            return retain(home/'complete.json',dict(status='LOCAL_AUTHORITY_RETIRED_FOR_REMOTE_TRANSFER',plan=row,fence=fence,legacy=legacy,ledger=actual,token_sha256=tokens,remote_started=False,model_operations_replayed=0))
