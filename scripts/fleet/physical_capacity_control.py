"""One recorded append-only physical-capacity policy transaction.

The server owns the atomic eligibility and capacity checks. This controller
binds reviewed code, authorization, the exact running server, and a full ledger
copy proof before issuing the sole administrator request. A consumed journal
is inspected, never retried.
"""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,sqlite3,subprocess,time,urllib.request,urllib.error

AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
SCHEMA='fleet-physical-capacity-control/v1'
PRESERVED=('workbooks','operations','quarantines','quarantine_host_capacity','author_capacity_policy','author_reservations','model_policy','model_limit','publication_limit')


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical existing control required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'control bytes changed');return json.loads(Path(row['path']).read_bytes())


def keep(path,value):
    raw=value if isinstance(value,bytes)else(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(path)


def module(row,name):
    require(binding(row['path'])==row,'selected module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def load(row):
    p=read(row);require(set(p)=={'schema_version','helper','coordinator','reader','arguments','authorization','process_identity','declaration'}and p['schema_version']==SCHEMA and p['helper']==binding(str(Path(__file__).absolute())),'closed physical capacity control required')
    auth=p['authorization'];require(auth['sha256']==AUTH and binding(auth['path'])==auth,'retained authorization changed')
    args=read(p['arguments']);require(args['authorization']==auth,'argument authorization differs');read(args['evidence'])
    declaration=read(p['declaration']);require(declaration==dict(schema_version='fleet-physical-capacity-authorization/v1',authorization=auth,arguments=p['arguments'],unknown_operations_released=False,maximum_fresh_models=1,physical_model_limit=3,author_limit=2,maximum_seconds=14400,automatic_retry=False),'bounded declaration differs')
    old=read(p['coordinator']);require(old['url']=='http://127.0.0.1:8765'and old['command'][3]=='serve'and old['command'][old['command'].index('--database')+1]==old['database'],'original coordinator identity differs')
    code=dict(path=old['command'][2],sha256=old['code_sha256']);M=module(code,'physical_capacity_original_server');P=module(p['reader'],'physical_capacity_selected_reader')
    observed=subprocess.run(['/bin/ps','-p',str(old['pid']),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(observed.returncode==0 and observed.stdout.strip()==p['process_identity']and code['path']in observed.stdout,'running coordinator process changed')
    require(callable(getattr(M.Coordinator,'permit_physical_capacity_extension',None)),'original live server lacks selected policy primitive')
    return p,old,M,P,args


def copy_proof(data,home):
    p,old,M,P,args=data;home.mkdir(mode=0o700);copy=home/'ledger.sqlite3'
    with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as source,sqlite3.connect(copy)as destination:source.backup(destination)
    copy.chmod(0o600);clock=time.time();c=M.Coordinator(copy,clock=lambda:clock);before=c.status();r=c.permit_physical_capacity_extension(**args);after=c.status()
    require(all(after[k]==before[k]for k in PRESERVED),'copied policy changed original work or authority')
    require(after['physical_capacity_extensions']==before['physical_capacity_extensions']+[r]and r in P.validate_physical_extensions(after),'copy did not append exact valid policy')
    require(after['events']==before['events']+1,'copy added unrelated events')
    keep(home/'before.json',before);keep(home/'after.json',after)
    return keep(home/'proof.json',dict(status='FULL_DATABASE_COPY_PHYSICAL_EXTENSION_PASS',arguments=p['arguments'],record=r,original_state_preserved=True,provider_calls=0,live_mutations=0))


def run(row,directory,*,execute=False):
    data=load(row);p,old,M,P,args=data;home=Path(directory);require(home.is_absolute()and not home.exists()and not any(x.is_symlink()for x in home.parents),'fresh canonical policy journal required');home.mkdir(mode=0o700)
    proof=copy_proof(data,home/'copy-proof')
    if not execute:return keep(home/'result.json',dict(status='PHYSICAL_CAPACITY_VALIDATED_NOT_STARTED',plan=row,proof=proof))
    locks=[]
    try:
        for name in ('coordinator-recovery.lock','job-launch.lock'):
            fd=os.open(Path(old['database']).parent/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p,old,M,P,args=load(row);before=M.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status');keep(home/'before.json',before)
        keep(home/'intent.json',dict(plan=row,proof=proof,at=time.time(),automatic_retry=False,operation='permit_physical_capacity_extension'))
        token=(Path(old['private_directory'])/'admin.token').read_text().strip()
        body=json.dumps(dict(action='permit_physical_capacity_extension',arguments=args)).encode()
        request=urllib.request.Request(old['url'].rstrip('/')+'/v1',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=10)as response:raw=response.read();code=response.status
        except urllib.error.HTTPError as error:raw=error.read();code=error.code
        except BaseException as error:
            keep(home/'transport-error.json',dict(type=type(error).__name__,at=time.time(),automatic_retry=False));raise
        keep(home/'response.raw',raw);keep(home/'response-meta.json',dict(status=code,at=time.time()))
        require(code==200,'policy response failed; retain and reconcile without replay')
        record=json.loads(raw);require(record.get('schema_version')=='fleet-physical-capacity-extension/v1','policy response identity differs')
        require(all(record.get(k)==v for k,v in args.items()if k!='expected_revision'),'returned policy arguments differ')
        after=M.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status');keep(home/'after.json',after)
        require(record in P.validate_physical_extensions(after),'live policy confirmation differs')
        require(all(after[k]==before[k]for k in ('quarantines','quarantine_host_capacity','author_capacity_policy','model_policy','model_limit','publication_limit')),'live original policies changed')
        return keep(home/'result.json',dict(status='PHYSICAL_CAPACITY_EXTENSION_ACTIVE',plan=row,record=record,proof=proof,automatic_retry=False))
    finally:
        for fd in locks:os.close(fd)


def main():
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);a.add_argument('--execute',action='store_true');v=a.parse_args();print(json.dumps(run(dict(path=v.plan,sha256=v.sha256),v.journal,execute=v.execute)))

if __name__=='__main__':main()
