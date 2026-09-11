"""Explicit root activation of four fixed workbook lanes; no scheduler or work.

The root must stop admitting old singleton descriptors after activation. This
module does not modify frozen historical drivers, trackers or model identities.
"""
from contextlib import contextmanager
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

WORKBOOKS=('0035','0042','0076','0184')
SCHEMA='harbor-fixed-parallel-policy/v1'
ACTIVATION='harbor-fixed-parallel-activation/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def safe(value):
    p=Path(value)
    require(p.is_absolute() and '..' not in p.parts,'absolute canonical path required')
    require(not any(x.is_symlink() for x in (p,*p.parents)),'symlink path forbidden')
    require(str(p)==str(value),'noncanonical path')
    return p


def record(path):
    p=safe(path);require(p.is_file(),'regular evidence file required')
    return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}


def checked(binding):
    require(isinstance(binding,dict) and set(binding)=={'path','sha256'},'exact evidence binding required')
    p=safe(binding['path']);raw=p.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==binding['sha256'],'evidence hash changed')
    return raw


def _pairs(items):
    d={}
    for k,v in items:
        require(k not in d,'duplicate JSON key');d[k]=v
    return d


def read(binding):return json.loads(checked(binding),object_pairs_hook=_pairs)


def canonical(value):return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def put(path,value):
    p=safe(path);require(p.parent.is_dir(),'existing root-owned parent required')
    temp=p.with_name('.'+p.name+'.'+str(uuid.uuid4())+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as out:out.write(canonical(value));out.flush();os.fsync(out.fileno())
        os.link(temp,p);df=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(df)
        finally:os.close(df)
    finally:temp.unlink(missing_ok=True)
    return record(p)


def proposal(campaign_root):
    root=safe(campaign_root)
    return dict(schema_version=SCHEMA,root_actor='harbor-orchestrator',campaign_root=str(root),
        implementation=record(Path(__file__).resolve()),model_journal_root=str(root/'model-journal'),
        model_slot_root=str(root/'model-slots'),model_capacity=1,workbook_capacity=4,
        singleton_lock=str(root/'actual-workbook-slot.lock'),publication_queue_lock=str(root/'publication-queue.lock'),
        activation_marker=str(root/'parallel-policy/activation.json'),
        lanes=[dict(lane=i+1,workbook=wb,lock=str(root/'parallel-workbook-locks'/(wb+'.lock'))) for i,wb in enumerate(WORKBOOKS)],
        old_singleton_admission_after_activation=False,timeout_seconds=900,memory_limit_bytes=8*1024**3)


def contract(binding):
    p=read(binding);require(isinstance(p,dict),'closed policy required')
    require(canonical(p)==canonical(proposal(p.get('campaign_root'))),'policy roster, roots, capacity or implementation differs')
    require(type(p['model_capacity']) is int and type(p['workbook_capacity']) is int and all(type(x['lane']) is int for x in p['lanes']),'integer capacities/lanes required')
    root=safe(p['campaign_root']);require(root.is_dir(),'existing campaign root required')
    safe(binding['path'])
    require(Path(binding['path']).parent==root/'parallel-policy','contract must be retained in selected campaign policy directory')
    for path in [p['singleton_lock'],p['publication_queue_lock'],p['activation_marker'],p['model_journal_root'],p['model_slot_root'],*[x['lock'] for x in p['lanes']]]:safe(path)
    return p


def lock_identity(fd,path):
    info=os.fstat(fd);live=os.stat(safe(path),follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==(live.st_dev,live.st_ino),'lock identity changed')
    return dict(path=str(path),device=info.st_dev,inode=info.st_ino)


def _open(path,create=False):
    p=safe(path);require(p.parent.is_dir(),'existing lock parent required')
    return os.open(p,os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT if create else 0),0o600)


def _model(p):
    root=safe(p['model_slot_root']);require(root.is_dir() and safe(p['model_journal_root']).is_dir(),'existing shared model roots required')
    capacity=record(root/'capacity');require(checked(capacity)==b'1\n','existing model capacity must remain one')
    return capacity


def activate(binding):
    p=contract(binding);marker=safe(p['activation_marker']);require(not marker.exists(),'policy already activated; select existing evidence, never replace')
    # Root pre-creates the scoped policy and lane directories. No permissions,
    # existing lock files or global/model capacity records are changed here.
    held=[]
    try:
        for path,create in [(p['singleton_lock'],False),*[(x['lock'],True) for x in p['lanes']],(p['publication_queue_lock'],True)]:
            fd=_open(path,create);held.append((fd,path));fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        model=_model(p)
        require(contract(binding)==p,'contract changed during activation')
        identities=[lock_identity(fd,path) for fd,path in held]
        require(len({(x['device'],x['inode']) for x in identities})==len(identities),'different policy paths alias the same lock inode')
        return put(marker,dict(schema_version=ACTIVATION,contract=binding,implementation=p['implementation'],
            observed_at_ns=time.time_ns(),singleton_quiescent=True,lanes_quiescent=True,publication_quiescent=True,
            model_capacity=model,locks=identities,old_singleton_admission_after_activation=False))
    finally:
        for fd,_ in reversed(held):os.close(fd)


def validate(binding,activation,workbook=None):
    p=contract(binding);a=read(activation)
    require(activation==record(p['activation_marker']),'canonical activation marker required')
    require(set(a)=={'schema_version','contract','implementation','observed_at_ns','singleton_quiescent','lanes_quiescent','publication_quiescent','model_capacity','locks','old_singleton_admission_after_activation'},'closed activation required')
    require(a['schema_version']==ACTIVATION and a['contract']==binding and a['implementation']==p['implementation'],'activation is not bound to selected policy')
    require(type(a['observed_at_ns']) is int and 0<a['observed_at_ns']<=time.time_ns() and all(a[x] is True for x in ('singleton_quiescent','lanes_quiescent','publication_quiescent')) and a['old_singleton_admission_after_activation'] is False,'invalid activation observations')
    require(a['model_capacity']==_model(p),'shared model capacity evidence changed')
    paths=[p['singleton_lock'],*[x['lock'] for x in p['lanes']],p['publication_queue_lock']]
    require(len(a['locks'])==len(paths) and [x['path'] for x in a['locks']]==paths,'lock inventory differs')
    for expected in a['locks']:
        fd=_open(expected['path'])
        try:require(lock_identity(fd,expected['path'])==expected,'persistent lock inode changed')
        finally:os.close(fd)
    require(len({(x['device'],x['inode']) for x in a['locks']})==len(paths),'aliased lock inventory')
    if workbook is not None:
        require(workbook in WORKBOOKS,'workbook outside fixed roster')
        return Path(next(x['lock'] for x in p['lanes'] if x['workbook']==workbook))
    return p


def selected_lock(binding,activation,workbook):
    return validate(binding,activation,workbook)


@contextmanager
def lane_lock(binding,activation,workbook):
    path=selected_lock(binding,activation,workbook);fd=_open(path)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        validate(binding,activation);lock_identity(fd,path)
        yield fd  # Root direct guards must include this descriptor in guardian_lock_fds.
    finally:os.close(fd)


def controls(binding,activation,workbook,*,env_file=None):
    """For an explicitly NEW C descriptor only; never rewrites old controls."""
    from scripts import harbor_campaign as C
    p=validate(binding,activation)
    require(C.MEMORY==p['memory_limit_bytes'] and C.SECONDS==p['timeout_seconds'],'coordinator guard limits differ')
    return C.Controls(selected_lock(binding,activation,workbook),Path(env_file) if env_file is not None else None)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['proposal','activate','validate']);a.add_argument('--campaign-root');a.add_argument('--contract');a.add_argument('--contract-sha256');a.add_argument('--activation');a.add_argument('--activation-sha256');args=a.parse_args()
    b={'path':args.contract,'sha256':args.contract_sha256}
    if args.action=='proposal':result=proposal(args.campaign_root)
    elif args.action=='activate':result=activate(b)
    else:p=validate(b,{'path':args.activation,'sha256':args.activation_sha256});result={'valid':True,'workbooks':list(WORKBOOKS),'model_capacity':p['model_capacity']}
    print(json.dumps(result),flush=True)
