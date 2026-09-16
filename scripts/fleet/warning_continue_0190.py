"""Declared, zero-model0190 warning continuation through original guarded steps.

Preparation issues only the source-backed Q permit. Every later task transition
uses the selected original I.run/I.child; publication is a separate operation.
"""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,shutil,sys,time

SCHEMA='0190-guarded-warning-continuation/v1'
FIELDS={'schema_version','workbook','helper','prepare_helper','prepare_plan','snapshot',
        'author_helper','dispatcher','config','output','maximum_steps'}
PHASES=('dialogue-review','final-runtime','dialogue-commit','enqueue')
MEMORY=8589934592

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular warning artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(type(row) is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'warning artifact differs')
    return json.loads(Path(row['path']).read_bytes())

def keep(path,value):
    path=Path(path);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    require(not any(p.is_symlink() for p in (path,*path.parents)),'unsafe warning receipt')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)

def module(name,row):
    require(binding(row['path'])==row,'warning module differs')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def context(row):
    p=read(row)
    require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0190'
            and p['helper']==binding(__file__) and p['maximum_steps']==4,'closed warning continuation required')
    M=module('selected0190warning_prepare',p['prepare_helper'])
    prep,C,I,W,Q,w,selection,controls,recipe,old,T=M.context(p['prepare_plan'])
    require(p['snapshot']==prep['snapshot'] and C.SECONDS==3300 and C.MEMORY==MEMORY,'original guard/code contract differs')
    config=read(p['config']);retained=read(prep['retained_plan']);old_config=read(retained['config'])
    require(config=={**old_config,'code':p['snapshot']} and config['workbook']=='0190','warning config changed beyond declared source-backed code')
    require(C.decode(config['controls'])==controls and w.run==Path(config['run_root'])/'instruction','original worker/controls differ')
    module('author_workbook',p['author_helper']);D=module('original0190warning_dispatcher',p['dispatcher'])
    # Use original validation/publication request helpers without replaying setup
    # or invoking its model-enabled authoring loop.
    from scripts import harbor_publish as P
    from scripts.batch import harbor_instruction_lease_renewal as R
    env=(C,I,P,W,R,config,Path(prep['code_root']),controls)
    require(all(Path(m.__file__).resolve().is_relative_to(env[6]) for m in (C,I,P,W,R)),'foreign warning imports')
    require(Path(p['output'])==Path(prep['home']).parent/'warning-remaining-001','closed remaining namespace required')
    return p,M,prep,D,env

def validate_bootstrap(sidecar,row,intent,original):
    v=read(sidecar);p=read(row)
    require(v==dict(schema_version='0190-warning-child-bootstrap/v1',plan=row,helper=p['helper'],
                    intent=intent,original_command=original,models=False,automatic_retry=False),
            'warning child detached from original guarded intent')
    require(Path(sidecar['path'])==Path(intent['path']).parent/'warning-bootstrap.json','foreign warning bootstrap directory')

def install_child_bootstrap(row,C,I):
    original_run=C._run_in_process_group;p=read(row)
    def guarded(command,*args,**kwargs):
        require(len(command)==6 and command[1:4]==['-B',str(Path(I.__file__).resolve()),'_child'],
                'warning bootstrap accepts only original instruction child')
        intent=dict(path=command[4],sha256=command[5]);v=read(intent)
        require(v['schema_version']==I.SCHEMA and v['action']=='step' and v['models'] is False
                and v['code']==binding(I.__file__) and v['base_code']==binding(C.__file__),
                'warning child cannot dispatch models or change owning code')
        sidecar=keep(Path(intent['path']).parent/'warning-bootstrap.json',dict(
            schema_version='0190-warning-child-bootstrap/v1',plan=row,helper=p['helper'],
            intent=intent,original_command=command,models=False,automatic_retry=False))
        replacement=[command[0],'-I','-B',p['helper']['path'],'instruction-child','--plan',row['path'],
                     '--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],
                     '--bootstrap',sidecar['path'],'--bootstrap-sha256',sidecar['sha256']]
        return original_run(replacement,*args,**kwargs)
    C._run_in_process_group=guarded

def instruction_child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated warning child required')
    p,M,prep,D,env=context(row);C,I=env[:2]
    original=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']]
    validate_bootstrap(sidecar,row,intent,original)
    value=read(intent)
    require(value['models'] is False and value['action']=='step','warning models forbidden')
    # context() blocks every physical executor entry before the real child runs.
    return I.child(intent)

def prepare_guard(row,p,M,C,controls,directory,lock_fds):
    require(not directory.exists(),'warning preparation guard already consumed');directory.mkdir()
    command=[sys.executable,'-I','-B',p['prepare_helper']['path'],'run','--plan',p['prepare_plan']['path'],'--sha256',p['prepare_plan']['sha256']]
    intent=keep(directory/'intent.json',dict(plan=row,command=command,memory_limit_bytes=MEMORY,
                timeout_seconds=3300,models=False,automatic_retry=False))
    fd=os.open(C._controls(controls),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    rc=None;failure=None;metrics={};started=time.monotonic()
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (directory/'step.log').open('xb') as log:
            try:rc=C._run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,
                    memory_limit_bytes=MEMORY,metrics=metrics,cwd=C.ROOT,
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*lock_fds))
            except Exception as exc:failure=type(exc).__name__
    finally:os.close(fd)
    resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,
                 elapsed_seconds=time.monotonic()-started,log=binding(directory/'step.log')))
    require(rc==0 and failure is None,'warning preparation requires reconciliation')
    result=binding(Path(read(p['prepare_plan'])['home'])/'result.json')
    return result,resource

def run(row):
    p,M,prep,D,env=context(row);C,I,P,W,R,config,root,controls=env
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot continue warning')
    M.load(p['prepare_plan']);M.active(prep)
    out=Path(p['output']);require(not out.exists() and shutil.disk_usage(root).free>=12*1024**3,'fresh warning namespace and original disk reserve required')
    out.mkdir();intent=keep(out/'intent.json',dict(plan=row,models=False,automatic_retry=False))
    with D.fleet_slot('0190') as lock_fds:
        checkpoint,preparation_resource=prepare_guard(row,p,M,C,controls,out/'prepare',lock_fds)
        value=read(checkpoint);require(value['status']=='RETRY_READY' and value['next_step']=='dialogue-review','owning warning handoff differs')
        I._prior(checkpoint,value['descriptor']);install_child_bootstrap(row,C,I)
        outcomes=[]
        for index,phase in enumerate(PHASES):
            require(value['next_step']==phase,'warning remaining phase skipped or repeated')
            M.active(prep)
            outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,
                out/('step-%02d'%index),models=False,commands=True,containers=True,guardian_lock_fds=lock_fds)
            checkpoint,value=D.successful(outcome,env);outcomes.append(outcome)
            require(value['status']==('PUBLICATION_READY' if phase=='enqueue' else 'STEP_COMPLETE'),
                    'owning warning stage requires reconciliation')
        D.publication_request(env,value)
    return keep(out/'result.json',dict(status='PUBLICATION_READY',workbook='0190',plan=row,intent=intent,
        config=p['config'],preparation_resource=preparation_resource,outcomes=outcomes,outcome=outcomes[-1],
        checkpoint=checkpoint,models_dispatched=0,publication_performed=False,automatic_retry=False))

def probe(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','read-only warning probe required')
    p,M,prep,D,env=context(row);result=M.probe(p['prepare_plan'])
    return dict(status='WARNING_REMAINING_CONTEXT_VALIDATED',plan=row,preparation=result,
                owning_operations=0,models_dispatched=0,publication_performed=False)

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','instruction-child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True)
    for n in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):a.add_argument('--'+n)
    v=a.parse_args();row=dict(path=str(Path(v.plan).absolute()),sha256=v.sha256)
    if v.action=='instruction-child':result=instruction_child(row,dict(path=v.intent,sha256=v.intent_sha256),dict(path=v.bootstrap,sha256=v.bootstrap_sha256))
    else:result=(run if v.action=='run' else probe)(row)
    print(json.dumps(result))
if __name__=='__main__':main()
