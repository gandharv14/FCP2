"""Resume a terminal failed baseline in a new namespace, retaining both source lanes.

The migration is a guarded zero-model operation. Its actual imported checkpoint
feeds one original environment step, then the unchanged ordinary dispatcher.
No source rebuild, admission, lease mutation, or model retry is implicit.
"""
from pathlib import Path
from dataclasses import replace
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time

SCHEMA='fleet-baseline-continuation/v1'
def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'canonical selected file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'baseline continuation evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    return binding(p)
def module(name,row):
    require(binding(row['path'])==row,'selected module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def retained_history(p,w,*,probe):
    """Inspect the original admitted survey; never invent live custody."""
    old=checked(p['original_worker_job']);config=checked(w['config']);first=checked(old['inputs'][0])
    require(old['workbook']==p['workbook'] and old['source']['sha256']==p['source']['sha256']
            and old['version']==Path(checked(w['old_code'])['root']).name
            and old['result']==str(Path(config['run_root']).parent/'result.json')
            and first['home']==str(Path(config['run_root']).parent),'retained history belongs to another worker')
    history=old['history'];checked(history)
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_PATH'),'mixed admission histories')
    from scripts.fleet import history as H
    if probe:
        os.environ.update(HARBOR_FLEET_WORKBOOK=p['workbook'],HARBOR_FLEET_HISTORY_PATH=history['path'],HARBOR_FLEET_HISTORY_SHA256=history['sha256'])
        # Only this read-only process replaces the heartbeat-backed projection;
        # the owning process continues to require the original real heartbeat.
        def inspect(roots,known_ids):return H.inspect_projection(roots,known_ids,p['workbook'],history)
        H.projected_survey=inspect
    else:
        require(os.environ.get('HARBOR_FLEET_HISTORY_PATH')==history['path'] and
                os.environ.get('HARBOR_FLEET_HISTORY_SHA256')==history['sha256'],
                'live baseline worker must retain its original admission history')

def load(bound, *, require_live=True):
    p=checked(bound)
    require(set(p)=={'schema_version','entry','stdlib','workbook','source','snapshot','boundary','original_job','original_worker_job','home','destination','operations','owner','takeover'} and p['schema_version']==SCHEMA and p['entry']==binding(__file__),'closed selected baseline plan required')
    module('baseline_stdlib',p['stdlib']).preload_inspect()
    snapshot=checked(p['snapshot']);root=Path(snapshot['root']);sys.path.insert(0,str(root))
    # External executables must use the same interpreter environment as the
    # already validated native baseline CLI, including uv and LibreOffice.
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_source_only_continuation as M,harbor_baseline_continuation as B
    B.selected_code(p['snapshot']);w=checked(p['boundary']);old=checked(p['original_job'])
    require(w['new_code']==p['snapshot'] and w['workbook']==p['workbook'] and w['source']==p['source'] and old['config']==w['config'],'baseline job/source/snapshot changed')
    require(old['publish'] is False and all(old[k] is True for k in ('models','commands','containers')),'original authoring capabilities differ')
    retained_history(p,w,probe=not require_live)
    config,worker,prepared,validated=M.inspect(w['config'],w['descriptor'],w['checkpoint'],w['tracker'],baseline=p['boundary'],takeover=p['takeover'])
    require(Path(p['destination']).parent==Path(config['run_root'])/'continuations' and not Path(p['destination']).exists(),'baseline destination consumed or outside retained home')
    require(Path(p['home']).parent==Path(config['run_root'])/'dispatcher-v2' and Path(p['operations']).parent==Path(p['destination'])/'dispatcher-v2','separate baseline execution namespace required')
    lease=C.decode(checked(w['checkpoint'])['lease'])
    if p['takeover'] is not None:
        from scripts.batch import harbor_lease_takeover as T
        _,_,lease=T.validate_completion(p['takeover'])
    if require_live:
        tracker=C.bootstrap._tracker(worker.bootstrap,lease)
        require(tracker['leases']['task_agent']['expires_at']-time.time_ns()>(900+3330+60)*10**9,'insufficient retained lease for migration and first owning guard')
    return p,w,old,config,worker,lease,C,M

def active(p):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'] and os.environ.get('HARBOR_FLEET_OWNER')==p['owner'],'baseline central owner differs')
    state=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=p['owner'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(state['state']=='active' and state['source_sha256']==p['source']['sha256'] and state['version']==Path(checked(p['snapshot'])['root']).name,'baseline central source/version differs')
    return state

def migration_intent(p,bound):
    return dict(schema_version='fleet-baseline-migration-guard/v1',plan=bound,entry=p['entry'],boundary=p['boundary'],seconds=900,memory_bytes=8*1024**3,models=False,lease_mutations=False,automatic_retry=False)

def continuation_intake(intake,current,lease):
    return replace(intake,bootstrap=current.bootstrap,environment_run=current.run,owner=lease.owner)

def migrate_child(bound,intent):
    p,w,old,config,worker,lease,C,M=load(bound);require(checked(intent)==migration_intent(p,bound),'migration guard intent differs')
    require(Path(intent['path'])==Path(p['home'])/'migration/intent.json','foreign migration child namespace')
    active(p)
    def forbidden(*a,**kw):raise ValueError('model execution forbidden in baseline migration')
    C.executor.execute=forbidden
    controls=C.decode(config['controls']);destination=Path(p['destination'])
    current,receipt,descriptor,checkpoint=M.commit(w['config'],w['descriptor'],w['checkpoint'],w['tracker'],lease,p['takeover'],destination,controls,p['snapshot'],baseline=p['boundary'])
    intake=C.decode(checked(config['intake']))
    new_intake=keep(destination/'intake.json',C.encode(continuation_intake(intake,current,lease)))
    updated={**config,'code':p['snapshot'],'run_root':str(destination),'intake':new_intake,'source_continuation':receipt}
    new_config=keep(destination/'config.json',updated);M.layout(updated,destination,'environment')
    C.selected(descriptor);C._prior(checkpoint,descriptor)
    return keep(Path(intent['path']).parent/'result.json',dict(status='IMPORTED',plan=bound,config=new_config,receipt=receipt,descriptor=descriptor,checkpoint=checkpoint,models=0,lease_mutations=0,semantic_budget_reset=False))

def validate_migration(p,bound,outcome,C):
    directory=Path(p['home'])/'migration';v=checked(outcome)
    require(outcome==binding(directory/'outcome.json') and v['status']=='COMPLETED_OPERATION' and v['automatic_retry'] is False,'migration did not complete; reconcile')
    resource=checked(v['resource']);require(v['resource']==binding(directory/'resource.json') and resource['returncode']==0 and resource['failure'] is None and resource['seconds']==900 and resource['memory_bytes']==8*1024**3 and resource['log']==binding(directory/'step.log'),'migration guard differs')
    require(checked(resource['intent'])==migration_intent(p,bound),'migration intent changed')
    require(resource['command']==[sys.executable,'-I','-B',p['entry']['path'],'migrate-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],'--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],'migration command differs')
    r=checked(v['result']);require(v['result']==binding(directory/'result.json') and r['plan']==bound and r['status']=='IMPORTED' and r['models']==r['lease_mutations']==0 and r['semantic_budget_reset'] is False,'migration result differs')
    require(r['config']==binding(Path(p['destination'])/'config.json') and r['receipt']==binding(Path(p['destination'])/'source-continuation.json') and r['descriptor']==binding(Path(p['destination'])/'descriptor.json') and r['checkpoint']==binding(Path(p['destination'])/'result.json'),'migration result namespace changed')
    C.selected(r['descriptor']);prior,_=C._prior(r['checkpoint'],r['descriptor']);require(prior['status']=='IMPORTED' and prior['step']=='baseline','original owning imported boundary rejected')
    return r

def probe(bound):
    p,w,old,config,worker,lease,C,M=load(bound,require_live=False)
    return dict(status='BASELINE_BOUNDARY_VALIDATED',plan=bound,tracker=w['tracker'],source=w['source'],generation_ids=w['generation_ids'],models=0,lease_mutations=0,source_rebuilds=0)

def context(bound):
    """Read an actual completed baseline migration for later instruction work.

    Fresh migration eligibility remains in load(). This separate certificate
    retains the original guarded migration and original dispatcher setup.
    """
    p=checked(bound)
    require(set(p)-{'normalization_profile'}=={'schema_version','entry','original_runtime','original_plan','migration','job','workbook','source'}
            and p['schema_version']=='fleet-completed-baseline-context/v1'
            and p['entry']==binding(__file__),'closed completed baseline context required')
    original=module('retained_completed_baseline',p['original_runtime']);old=checked(p['original_plan'])
    require(old['schema_version']==SCHEMA and old['entry']==p['original_runtime']
            and old['workbook']==p['workbook'] and old['source']==p['source'],
            'completed baseline source or implementation differs')
    original.module('completed_baseline_stdlib',old['stdlib']).preload_inspect()
    snapshot=checked(old['snapshot']);root=Path(snapshot['root']);sys.path.insert(0,str(root))
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_baseline_continuation as B
    B.selected_code(old['snapshot']);boundary=checked(old['boundary']);job=checked(old['original_job'])
    require(boundary['new_code']==old['snapshot'] and boundary['source']==p['source']
            and boundary['workbook']==p['workbook'] and job['config']==boundary['config'],
            'completed baseline original selection differs')
    original.retained_history(old,boundary,probe=True)
    migrated=original.validate_migration(old,p['original_plan'],p['migration'],C)
    actual=checked(p['job']);home=Path(old['home'])
    require(p['job']==binding(home/'authoring-job.json')
            and {k:v for k,v in actual.items()if k!='outcome'}=={**{k:v for k,v in job.items()if k!='outcome'},'config':migrated['config'],'operations':old['operations']}
            and actual['outcome']==binding(home/'baseline/outcome.json'),
            'completed baseline authoring job differs')
    D=original.module('completed_baseline_dispatcher',job['driver']);env=D.setup(actual)
    _,value=D.successful(actual['outcome'],env)
    require((value['status']=='STEP_COMPLETE' and value['step']=='baseline')
            or(value['status']=='PENDING_MODEL' and value['step']=='research'),
            'original baseline step did not complete')
    if 'normalization_profile'in p:
        selected=checked(p['normalization_profile'])
        require(set(selected)=={'schema_version','prepared','snapshot','authorization'}
                and selected['schema_version']=='harbor-normalization-snapshot-selection/v1'
                and selected['snapshot']==old['snapshot']
                and selected['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
                and binding(selected['authorization']['path'])==selected['authorization'],
                'normalization selection changed retained code or authorization')
        checked(selected['prepared'])
        os.environ['HARBOR_FLEET_NORMALIZATION_PROFILE']=json.dumps(p['normalization_profile'],sort_keys=True)
        from scripts.batch import harbor_normalization_validator_bridge as normalization
        normalization._approved(selected['prepared'])
    return p,D,env

def probe_live(bound):
    # Use only the original read-only history projection, then authenticate the
    # actual completed takeover and current native lease without a fake claim.
    p,w,old,config,worker,lease,C,M=load(bound,require_live=False)
    tracker=C.bootstrap._tracker(worker.bootstrap,lease)
    remaining=tracker['leases']['task_agent']['expires_at']-time.time_ns()
    require(remaining>(900+3330+60)*10**9,'insufficient retained lease for migration and first owning guard')
    return dict(status='BASELINE_LIVE_BOUNDARY_VALIDATED',plan=bound,tracker=dict(path=str(worker.bootstrap.policy.tracker_root/(worker.bootstrap.workbook+'.json')),sha256=lease.sha256),remaining_seconds=remaining/10**9,models=0,lease_mutations=0,source_rebuilds=0)

def run(bound):
    p,w,old,config,worker,lease,C,M=load(bound);active(p)
    home=Path(p['home']);require(not home.exists(),'baseline execution attempted; reconcile without replay')
    D=module('baseline_original_dispatcher',old['driver']);controls=C.decode(config['controls']);home.mkdir();directory=home/'migration'
    with D.fleet_slot(p['workbook'])as fleet_fds:
        fd=os.open(C._controls(controls),os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);active(p);directory.mkdir()
            intent=keep(directory/'intent.json',migration_intent(p,bound))
            command=[sys.executable,'-I','-B',p['entry']['path'],'migrate-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
            rc=None;failure=None;metrics={}
            try:
                with(directory/'step.log').open('xb')as log:rc=C._run_in_process_group(command,stdout=log,timeout_seconds=900,memory_limit_bytes=8*1024**3,grace_seconds=3,metrics=metrics,cwd=C.ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fds))
            except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
            resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),command=command,seconds=900,memory_bytes=8*1024**3))
            result=binding(directory/'result.json')if rc==0 and failure is None and(directory/'result.json').exists()else None
            outcome=keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION'if result else'RECONCILIATION_REQUIRED',resource=resource,result=result,automatic_retry=False))
        finally:os.close(fd)
    migrated=validate_migration(p,bound,outcome,C);job={**old,'config':migrated['config'],'operations':p['operations']};env=D.setup(job)
    # This is the real unchanged baseline command under the original guard;
    # the typed migration itself is never presented as an ordinary step.
    active(p);first=C.run('step',dict(descriptor=migrated['descriptor'],checkpoint=migrated['checkpoint'],captures=None),env[7],home/'baseline',dispatch=False)
    _,value=D.successful(first,env);require(value['status']=='STEP_COMPLETE' and value['step']=='baseline' or value['status']=='PENDING_MODEL' and value['step']=='research','corrected baseline held; preserve actual outcome')
    actual=keep(home/'authoring-job.json',{**job,'outcome':first});D.run(actual)
    held=checked(binding(Path(p['operations'])/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
    result=keep(home/'result.json',dict(status=value['status'],plan=bound,job=actual,config=migrated['config'],outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))
    require(value['status']=='PUBLICATION_READY','remaining authoring held; no automatic replay')
    return result

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','probe_live','run','migrate-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();bound=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
    result=migrate_child(bound,dict(path=v.intent,sha256=v.intent_sha256))if v.action=='migrate-child'else globals()[v.action](bound)
    print(json.dumps(result))
if __name__=='__main__':main()
