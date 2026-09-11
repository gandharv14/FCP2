#!/usr/bin/python3
"""Maintain an existing protected deployment without opening or rotating its key."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import tempfile
import uuid

INSTALL=Path('/Library/Application Support/FCP2/recalc')
CONFIG=Path('/etc/harbor')
JOURNAL=Path('/var/lib/harbor/table-install/journal')
TABLE_JOURNAL=Path('/var/lib/harbor/table-quarantine/journal')
ARTIFACTS=Path('/var/lib/harbor/table-quarantine/artifacts')
WINDOW_LOCK=Path('/var/run/fcp2-recalc-window.lock')
INSTALL_ORDER=('observer','producer','window','config','attestation','table_config')
CONTROLS=('dedicated_session','network_disabled','macros_disabled','add_ins_disabled','link_updates_disabled','prompts_suppressed')
SOURCES={'observer':'scripts/recalc/excel_runner.py','producer':'scripts/recalc/table_quarantine_runner.py',
         'window':'scripts/recalc/table_quarantine_window.py','profile':'xl_table_recalc_quarantine.py',
         'recalc':'xl_source_recalc.py','health':'xl_source_health.py'}

class MaintenanceError(ValueError):pass

def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def same(a,b,label):
    if canonical(a)!=canonical(b):raise MaintenanceError(label+' changed')
def read_bytes(path):
    path=Path(path);m=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(m.st_mode) or m.st_nlink!=1:raise MaintenanceError('unsafe regular file')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as handle:data=handle.read()
    after=path.stat()
    if any(getattr(after,k)!=getattr(m,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):raise MaintenanceError('file changed while reading')
    return data

def hashed(path):return hashlib.sha256(read_bytes(path)).hexdigest()
def decode(data):
    def unique(items):
        out={}
        for key,value in items:
            if key in out:raise MaintenanceError('duplicate JSON field')
            out[key]=value
        return out
    return json.loads(data,object_pairs_hook=unique,parse_constant=lambda s:(_ for _ in ()).throw(MaintenanceError('nonfinite JSON')))

def load(path):return decode(read_bytes(path))

def load_bound(path,expected):
    data=read_bytes(path)
    same(hashlib.sha256(data).hexdigest(),expected,'reviewed file bytes')
    return decode(data)

def protected(path,directory=False):
    p=Path(path);m=p.lstat()
    if p.is_symlink() or m.st_uid!=0 or m.st_mode&0o022 or not (stat.S_ISDIR(m.st_mode) if directory else stat.S_ISREG(m.st_mode)):
        raise MaintenanceError('unprotected deployment path')
    for parent in p.resolve().parents:
        m=parent.stat()
        if m.st_uid!=0 or m.st_mode&0o022 or not stat.S_ISDIR(m.st_mode):raise MaintenanceError('unprotected deployment parent')

def targets():
    return {'observer':INSTALL/'excel_runner.py','producer':INSTALL/'table_quarantine_runner.py',
            'window':INSTALL/'table_quarantine_window.py','config':CONFIG/'excel-runner.json',
            'attestation':INSTALL/'excel-isolation-attestation.json','table_config':CONFIG/'excel-table-quarantine.json'}

def private_metadata(path):
    # Deliberately no open/read/hash: preservation follows from no write operation
    # targeting this inode and an exact metadata check before/after the transaction.
    path=Path(path);protected(path);m=path.lstat()
    if stat.S_IMODE(m.st_mode)!=0o600 or m.st_nlink!=1:raise MaintenanceError('unsafe private key metadata')
    return {'path':str(path),'dev':m.st_dev,'ino':m.st_ino,'uid':m.st_uid,'gid':m.st_gid,
            'mode':stat.S_IMODE(m.st_mode),'size':m.st_size,'mtime_ns':m.st_mtime_ns,'ctime_ns':m.st_ctime_ns}

def file_state(path):
    path=Path(path)
    if not path.exists() and not path.is_symlink():return None
    protected(path);m=path.stat()
    return {'sha256':hashed(path),'mode':stat.S_IMODE(m.st_mode),'size_bytes':m.st_size}

def deployment():
    t=targets();files={k:file_state(p) for k,p in t.items()}
    if any(files[k] is None for k in ('observer','config','attestation')):raise MaintenanceError('existing complete deployment required')
    base=load(t['config']);attestation=load(t['attestation'])
    same(base.get('schema_version'),'excel-runner-config/v1','base schema')
    same(attestation.get('schema_version'),'excel-isolation-attestation/v1','attestation schema')
    for obj in (base,attestation):
        same(obj.get('runner_implementation'),str(t['observer']),'fixed installed observer')
        same(obj.get('runner_implementation_sha256'),files['observer']['sha256'],'existing observer binding')
    if any(base.get('isolation_controls',{}).get(k) is not True or attestation.get(k) is not True for k in CONTROLS):
        raise MaintenanceError('existing controls incomplete')
    public=Path(base.get('receipt_public_key',''));private=Path(base.get('receipt_private_key',''))
    if not public.is_absolute() or not private.is_absolute():raise MaintenanceError('absolute existing key references required')
    if len({p.resolve() for p in (*t.values(),public,private)})!=len(t)+2:raise MaintenanceError('managed file/key alias')
    key=private_metadata(private)
    protected(public);public_state=file_state(public)
    for obj in (base,attestation):
        same(obj.get('receipt_public_key'),str(public),'existing public key reference')
        same(obj.get('receipt_public_key_sha256'),public_state['sha256'],'existing public key binding')
    return {'files':files,'public_key':{'path':str(public),**public_state},'private_key_metadata':key},base,attestation

def verify_keys(state):
    public=state['public_key'];private=state['private_key_metadata']
    if Path(public['path']).resolve()==Path(private['path']).resolve():raise MaintenanceError('public/private key alias')
    same(private_metadata(private['path']),private,'private key metadata')
    same(file_state(public['path']),{k:v for k,v in public.items() if k!='path'},'public key')

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def write_atomic(path,data,mode,*,expected):
    path=Path(path);same(file_state(path),expected,'CAS destination')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())
        same(file_state(path),expected,'CAS destination')
        os.replace(name,path);sync_dir(path.parent)
    finally:
        if os.path.exists(name):os.unlink(name)

def create_protected_directory(path,mode):
    path=Path(path)
    if path.exists():
        protected(path,directory=True)
        if stat.S_IMODE(path.stat().st_mode)!=mode:raise MaintenanceError('existing protected directory has wrong mode')
        return
    if not path.parent.exists():create_protected_directory(path.parent,0o755)
    protected(path.parent,directory=True);path.mkdir(mode=mode);path.chmod(mode);sync_dir(path.parent)

@contextlib.contextmanager
def shared_lock():
    protected(WINDOW_LOCK.parent,directory=True)
    fd=os.open(WINDOW_LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        protected(WINDOW_LOCK);fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)

def prepare_bundle(pipeline):
    pipeline=Path(pipeline).resolve();records={}
    for role,relative in SOURCES.items():
        path=pipeline/relative;records[role]={'path':str(path),'sha256':hashed(path)}
    result={'schema_version':'table-runner-reviewed-bundle/v1','sources':records}
    result['bundle_sha256']=digest(result);return result

def bundle_bytes(bundle):
    same(sorted(bundle),sorted(('schema_version','sources','bundle_sha256')),'bundle fields')
    same(bundle['schema_version'],'table-runner-reviewed-bundle/v1','bundle schema')
    same(bundle['bundle_sha256'],digest({k:v for k,v in bundle.items() if k!='bundle_sha256'}),'bundle digest')
    same(sorted(bundle['sources']),sorted(SOURCES),'reviewed source roles')
    data={}
    for role,record in bundle['sources'].items():
        same(sorted(record),['path','sha256'],'source record fields')
        if Path(record['path']).name!=Path(SOURCES[role]).name:raise MaintenanceError('unexpected reviewed source filename')
        data[role]=read_bytes(record['path']);same(hashlib.sha256(data[role]).hexdigest(),record['sha256'],'reviewed source')
    return data

def make_plan(bundle,*,allowed_invoker_uid,permitted_versions):
    data=bundle_bytes(bundle);state,base,attestation=deployment()
    if type(allowed_invoker_uid) is not int or allowed_invoker_uid<=0:raise MaintenanceError('dedicated UID required')
    if pwd.getpwnam(base['automation_user']).pw_uid!=allowed_invoker_uid:raise MaintenanceError('dedicated UID differs from existing automation account')
    if (not isinstance(permitted_versions,list) or not permitted_versions or
        any(not isinstance(v,str) or re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+){0,2}',v) is None for v in permitted_versions)
        or len(set(permitted_versions))!=len(permitted_versions)):raise MaintenanceError('unique concrete versions required')
    observer_hash=hashlib.sha256(data['observer']).hexdigest();producer_hash=hashlib.sha256(data['producer']).hexdigest()
    newbase=dict(base,runner_implementation_sha256=observer_hash)
    newattestation=dict(attestation,runner_implementation_sha256=observer_hash)
    table={'schema_version':'excel-table-quarantine-config/v1','base_config':str(targets()['config']),
           'base_module':str(targets()['observer']),'base_module_sha256':observer_hash,'dispatcher_sha256':producer_hash,
           'selected_policy_code':{'trusted_producer_sha256':producer_hash,'observation_sha256':observer_hash,
               'profile_sha256':hashlib.sha256(data['profile']).hexdigest(),
               'snapshot_receipt_sha256':hashlib.sha256(data['recalc']).hexdigest(),
               'health_sha256':hashlib.sha256(data['health']).hexdigest()},
           'journal_root':str(TABLE_JOURNAL),'artifact_root':str(ARTIFACTS),
           'allowed_invoker_uid':allowed_invoker_uid,'permitted_engine_versions':permitted_versions}
    payloads={k:data[k] for k in ('observer','producer','window')}
    payloads.update(config=canonical(newbase),attestation=canonical(newattestation),table_config=canonical(table))
    planned={role:{'path':str(targets()[role]),'data_base64':base64.b64encode(content).decode(),
             'new_state':{'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),
                          'mode':0o755 if role in {'observer','producer','window'} else state['files'][role]['mode'] if state['files'][role] else 0o644}}
             for role,content in payloads.items()}
    plan={'schema_version':'table-runner-maintenance-plan/v1','bundle':bundle,'expected_deployment':state,
          'allowed_invoker_uid':allowed_invoker_uid,'permitted_versions':permitted_versions,'files':planned}
    plan['plan_sha256']=digest(plan);return plan

def validate_plan(plan):
    same(plan,make_plan(plan['bundle'],allowed_invoker_uid=plan['allowed_invoker_uid'],
                        permitted_versions=plan['permitted_versions']),'reviewed CAS plan')

def write_journal(path,value):write_atomic(path,canonical(value),0o600,expected=file_state(path))

def apply_plan(plan,*,transaction_id=None):
    with shared_lock():
        validate_plan(plan)
        create_protected_directory(JOURNAL,0o700)
        tx=transaction_id or str(uuid.uuid4())
        if str(uuid.UUID(tx))!=tx:raise MaintenanceError('canonical transaction ID required')
        root=JOURNAL/tx;root.mkdir(mode=0o700);root.chmod(0o700);sync_dir(JOURNAL)
        # All backups and desired bytes are durable before any deployment write.
        backups={}
        for role,path in targets().items():
            old=plan['expected_deployment']['files'][role]
            if old is not None:
                data=read_bytes(path);same(hashlib.sha256(data).hexdigest(),old['sha256'],'backup source')
                backup=root/(role+'.before');write_atomic(backup,data,0o600,expected=None)
                backups[role]={'path':backup.name,'sha256':old['sha256']}
        record={'schema_version':'table-runner-maintenance-transaction/v1','state':'prepared','plan':plan,'backups':backups,'completed':[]}
        write_journal(root/'transaction.json',record)
        try:
            create_protected_directory(TABLE_JOURNAL,0o700);create_protected_directory(ARTIFACTS,0o755)
            for role in INSTALL_ORDER:
                item=plan['files'][role]
                verify_keys(plan['expected_deployment'])
                write_atomic(item['path'],base64.b64decode(item['data_base64'],validate=True),item['new_state']['mode'],
                             expected=plan['expected_deployment']['files'][role])
                record['completed'].append(role);write_journal(root/'transaction.json',record)
            verify_keys(plan['expected_deployment'])
            for role,item in plan['files'].items():same(file_state(item['path']),item['new_state'],'installed file')
            record['state']='complete';write_journal(root/'transaction.json',record)
            return {'status':'installed','transaction':str(root),'plan_sha256':plan['plan_sha256']}
        except BaseException as exc:
            record.update(state='interrupted',error_type=type(exc).__name__,error_text=str(exc)[:8192])
            write_journal(root/'transaction.json',record)
            raise

def recover(transaction_id):
    if str(uuid.UUID(transaction_id))!=transaction_id:raise MaintenanceError('canonical transaction ID required')
    with shared_lock():
        root=JOURNAL/transaction_id;protected(root,directory=True);path=root/'transaction.json';protected(path)
        record=load(path);plan=record['plan']
        same(plan['plan_sha256'],digest({k:v for k,v in plan.items() if k!='plan_sha256'}),'journal plan digest')
        same(sorted(plan['files']),sorted(targets()),'journal roles')
        verify_keys(plan['expected_deployment'])
        # Validate all destinations/backups before restoring any; do not overwrite
        # unknown post-install work or assume the completion list survived a crash.
        for role,item in plan['files'].items():
            same(item['path'],str(targets()[role]),'fixed recovery destination')
            actual=file_state(item['path']);old=plan['expected_deployment']['files'][role]
            if actual not in (old,item['new_state']):raise MaintenanceError('unknown current bytes; manual recovery required')
            if old is not None:
                backup=root/record['backups'][role]['path'];protected(backup)
                same(str(backup.parent),str(root),'backup directory')
                same(hashed(backup),old['sha256'],'retained backup')
        for role in reversed(INSTALL_ORDER):
            item=plan['files'][role]
            current=file_state(item['path']);old=plan['expected_deployment']['files'][role]
            if current==old:continue
            if old is None:
                Path(item['path']).unlink();sync_dir(Path(item['path']).parent)
            else:write_atomic(item['path'],read_bytes(root/record['backups'][role]['path']),old['mode'],expected=current)
        verify_keys(plan['expected_deployment']);record['state']='rolled_back';write_journal(path,record)
        return {'status':'rolled_back','transaction':str(root)}

def check_transaction(transaction_id):
    if str(uuid.UUID(transaction_id))!=transaction_id:raise MaintenanceError('canonical transaction ID required')
    root=JOURNAL/transaction_id;protected(root,directory=True);protected(root/'transaction.json')
    record=load(root/'transaction.json');plan=record['plan']
    same(record['state'],'complete','installation state')
    same(plan['plan_sha256'],digest({k:v for k,v in plan.items() if k!='plan_sha256'}),'journal plan digest')
    same(sorted(plan['files']),sorted(targets()),'installed roles')
    verify_keys(plan['expected_deployment'])
    for role,item in plan['files'].items():
        same(item['path'],str(targets()[role]),'fixed installed destination')
        same(file_state(item['path']),item['new_state'],'installed bytes')
    deployment()
    protected(TABLE_JOURNAL,directory=True);protected(ARTIFACTS,directory=True)
    return {'status':'installed_files_verified','transaction':str(root),'plan_sha256':plan['plan_sha256']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('--pipeline',required=True);prepare.add_argument('--out',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--bundle',required=True);plan.add_argument('--bundle-sha256',required=True);plan.add_argument('--uid',required=True,type=int);plan.add_argument('--version',required=True,action='append');plan.add_argument('--out',required=True)
    check=sub.add_parser('check');check.add_argument('--plan');check.add_argument('--plan-sha256');check.add_argument('--transaction-id')
    apply=sub.add_parser('apply');apply.add_argument('--plan',required=True);apply.add_argument('--plan-sha256',required=True)
    recovery=sub.add_parser('recover');recovery.add_argument('--transaction-id',required=True)
    args=parser.parse_args(argv)
    if args.command in {'apply','recover'} and (os.geteuid()!=0 or platform.system()!='Darwin'):raise MaintenanceError('installation requires macOS root')
    if args.command=='prepare':
        result=prepare_bundle(args.pipeline);path=Path(args.out)
        # User-owned preparation output; no protected file or request is written.
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as handle:handle.write(canonical(result));handle.flush();os.fsync(handle.fileno())
        sync_dir(path.parent);result={'status':'prepared_bundle','path':str(path),'sha256':hashed(path)}
    elif args.command=='plan':
        bundle=load_bound(args.bundle,args.bundle_sha256);result=make_plan(bundle,allowed_invoker_uid=args.uid,permitted_versions=args.version);path=Path(args.out)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as handle:handle.write(canonical(result));handle.flush();os.fsync(handle.fileno())
        sync_dir(path.parent);result={'status':'prepared_plan','path':str(path),'sha256':hashed(path)}
    elif args.command=='check' and args.transaction_id:
        if args.plan or args.plan_sha256:raise MaintenanceError('select installed transaction or pre-install plan')
        result=check_transaction(args.transaction_id)
    elif args.command in {'check','apply'}:
        if not args.plan or not args.plan_sha256:raise MaintenanceError('reviewed plan path and hash required')
        plan=load_bound(args.plan,args.plan_sha256)
        if args.command=='check':validate_plan(plan);result={'status':'current_plan_valid','plan_sha256':plan['plan_sha256']}
        else:result=apply_plan(plan)
    else:result=recover(args.transaction_id)
    print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
