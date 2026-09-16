"""Append-only root-selected jobs for an already running VM supervisor.

An append changes neither a prior job nor a dispatch. The supervisor still uses
its original validation, resource gates and atomic central claim for every job.
"""
from pathlib import Path
import argparse,fcntl,hashlib,json,math,os,tempfile,time

SCHEMA='fleet-durable-backlog/v1'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok:raise ValueError(message)


def safe(path):
    p=Path(path)
    require(p.is_absolute()and '..'not in p.parts and not any(q.is_symlink()for q in(p,*p.parents)),
            'canonical backlog path required')
    return p


def binding(path):
    p=safe(path);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'backlog bound bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def persist(path,value,*,immutable):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.append-',dir=p.parent)
    try:
        with os.fdopen(fd,'w')as f:json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        if immutable:os.link(tmp,p)
        else:os.replace(tmp,p)
        fd=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return binding(p)


def policy(row):
    p=read(row)
    require(set(p)=={'schema_version','host','code_root','worker','directory','authorization','maximum_jobs','expires_at','helper'}
            and p['schema_version']==SCHEMA and p['helper']==binding(Path(__file__).absolute()),'closed backlog selection required')
    require(p['host']in['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)],'backlog host differs')
    require(type(p['maximum_jobs'])is int and 1<=p['maximum_jobs']<=10000,'bounded backlog required')
    require(type(p['expires_at'])in(int,float)and math.isfinite(p['expires_at'])and time.time()<p['expires_at'],'backlog authorization expired or nonfinite')
    safe(p['directory']);safe(p['code_root'])
    require(p['authorization']['sha256']==AUTHORIZATION_SHA256 and binding(p['authorization']['path'])==p['authorization'],
            'backlog explicit authorization bytes differ')
    require(binding(p['worker']['path'])==p['worker']and Path(p['worker']['path']).is_relative_to(p['code_root']),
            'backlog worker differs')
    return p


def load(row,*,previous=None):
    p=policy(row);root=Path(p['directory']);head=root/'head.json';entries=root/'entries'
    if not head.exists():
        require(previous is None and (not entries.exists()or not list(entries.iterdir())),
                'uncommitted or rolled-back backlog requires reconciliation')
        return [],None
    tail=read(binding(head));require(set(tail)=={'sequence','entry'},'backlog head differs')
    require(type(tail['sequence'])is int and 1<=tail['sequence']<=p['maximum_jobs'],'backlog sequence invalid')
    cursor=tail['entry'];parts=[];seen=set()
    for sequence in range(tail['sequence'],0,-1):
        require(cursor['path']==str(entries/f'{sequence:08d}.json'),'backlog chain path differs')
        v=read(cursor)
        require(set(v)=={'schema_version','policy','sequence','previous','jobs'}and v['schema_version']==SCHEMA
                and v['policy']==row and v['sequence']==sequence,'backlog entry identity differs')
        batch=read(v['jobs']);require(type(batch)is list and batch,'nonempty selected job batch required')
        parts.append((sequence,cursor,batch));cursor=v['previous']
    require(cursor is None,'backlog chain has an extra predecessor')
    if previous is not None:
        require(any(dict(sequence=i,entry=b)==previous for i,b,_ in parts),'backlog cursor rolled back or changed')
    jobs=[]
    for _,_,batch in reversed(parts):
        for selected in batch:
            job=read(selected)
            require(job['workbook']not in seen,'backlog repeats a workbook');seen.add(job['workbook'])
            require(job['cwd']==p['code_root']and Path(job['driver']['path']).is_relative_to(p['code_root']),
                    'backlog job runtime differs')
            jobs.append(selected)
    require(len(jobs)<=p['maximum_jobs'],'backlog exceeds its finite allowance')
    return jobs,tail


def append(row,batch,*,expected):
    p=policy(row);root=safe(p['directory']);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root/'append.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        jobs,tail=load(row);require(tail==expected,'backlog changed since selection')
        extra=read(batch);require(type(extra)is list and extra,'nonempty selected job batch required')
        all_jobs=jobs+extra;require(len(all_jobs)<=p['maximum_jobs'],'backlog exceeds its finite allowance')
        seen=set()
        # Use the same selected original worker validator as the supervisor.
        import importlib.util,sys
        sys.path.insert(0,p['code_root']);spec=importlib.util.spec_from_file_location('backlog_selected_worker',p['worker']['path'])
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        for selected in all_jobs:
            job=read(selected);require(job['workbook']not in seen,'backlog repeats a workbook');seen.add(job['workbook'])
            require(job['cwd']==p['code_root']and Path(job['driver']['path']).is_relative_to(p['code_root']),
                    'backlog job runtime differs');worker.validate(job)
        sequence=1 if tail is None else tail['sequence']+1
        entry=persist(root/'entries'/f'{sequence:08d}.json',dict(schema_version=SCHEMA,policy=row,sequence=sequence,
                      previous=None if tail is None else tail['entry'],jobs=batch),immutable=True)
        return persist(root/'head.json',dict(sequence=sequence,entry=entry),immutable=False)
    finally:os.close(fd)


def observe(row,*,previous=None):
    """Persist the last authenticated cursor across slot/supervisor generations."""
    p=policy(row);root=Path(p['directory']);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root/'observe.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        path=root/'observed.json';saved=read(binding(path))if path.exists()else None
        if saved is not None:
            require(set(saved)=={'policy','cursor'}and saved['policy']==row,'durable backlog observation differs')
        jobs,tail=load(row,previous=saved['cursor']if saved else previous)
        if previous is not None:load(row,previous=previous)
        if tail is not None and (saved is None or saved['cursor']!=tail):
            persist(path,dict(policy=row,cursor=tail),immutable=False)
        return jobs,tail
    finally:os.close(fd)


def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--policy',required=True);a.add_argument('--sha256',required=True)
    a.add_argument('--batch',required=True);a.add_argument('--batch-sha256',required=True);a.add_argument('--expected-head')
    args=a.parse_args();expected=json.loads(Path(args.expected_head).read_bytes())if args.expected_head else None
    print(json.dumps(append(dict(path=args.policy,sha256=args.sha256),dict(path=args.batch,sha256=args.batch_sha256),expected=expected)))


if __name__=='__main__':main()
