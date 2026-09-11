"""Root-selected single-workbook admission/environment steps; no fleet fanout.

Each CLI operation has a fresh journal, one shared workbook lock and a hard
900-second/8-GiB process-group guard. Checkpoints are selected by exact hashes.
No instruction execution, publication or terminal-failure inference is hidden.
"""
from dataclasses import dataclass,asdict,fields,is_dataclass,replace
from pathlib import Path
import argparse
import fcntl
import json
import os
import shutil
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.batch import harbor_workbook_bootstrap as bootstrap
from scripts.batch import harbor_environment_worker as worker
from scripts.batch import harbor_admission as admission
from scripts.batch import harbor_model_executor as executor
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_transition_codec as content_codec
from scripts.batch.hardened_runner import _run_in_process_group,ConversionTimeout
from scripts.batch.harbor_admission import read,record,require
ROOT=bootstrap.ROOT
MEMORY=8*1024**3
SECONDS=3300
DURATION=3600*10**9

@dataclass(frozen=True)
class Controls:
    slot_lock: Path  # Same root-selected lock used by other actual workbook drivers.
    env_file: Path | None = None

@dataclass(frozen=True)
class Intake:
    bootstrap: bootstrap.Selection
    campaign: executor.Campaign
    taxonomy: dict
    environment_run: Path
    owner: str
    inherited_audit_budget: dict | None = None


def registry():
    return {'Controls':Controls,'Intake':Intake,'Bootstrap':bootstrap.Selection,
        'LedgerRefresh':bootstrap.LedgerRefresh,'Lease':bootstrap.Lease,
        'Environment':worker.Selection,'Admission':admission.AdmissionPolicy,
        'Dependencies':admission.DependencyRecipe,'Campaign':executor.Campaign,
        'Budget':worker.normalization.Budget,'Policy':bootstrap.Policy}


def encode(value):
    if is_dataclass(value):
        names={cls:name for name,cls in registry().items()};require(type(value) in names,'unsupported coordinator class')
        return {'type':names[type(value)],'value':{f.name:encode(getattr(value,f.name)) for f in fields(value)}}
    if isinstance(value,Path):return {'type':'path','value':str(value)}
    if isinstance(value,dict):return {'type':'dict','value':[[encode(k),encode(v)] for k,v in value.items()]}
    if isinstance(value,(list,tuple)):return {'type':'tuple' if isinstance(value,tuple) else 'list','value':[encode(v) for v in value]}
    require(value is None or type(value) in (str,int,bool,float),'unsupported coordinator value');return {'type':'scalar','value':value}


def decode(value):
    require(isinstance(value,dict) and set(value)=={'type','value'},'closed coordinator encoding required')
    kind,v=value['type'],value['value']
    if kind in registry():
        cls=registry()[kind];require(isinstance(v,dict) and set(v)=={f.name for f in fields(cls)},'coordinator dataclass fields changed')
        return cls(**{k:decode(x) for k,x in v.items()})
    require(kind in {'scalar','path','dict','list','tuple'},'unrecognized coordinator type')
    return content_codec.decode(value) if kind in {'scalar','path'} else _collection(kind,v)


def _collection(kind,v):
    require(isinstance(v,list),'invalid coordinator sequence')
    if kind in {'list','tuple'}:
        result=[decode(x) for x in v];return tuple(result) if kind=='tuple' else result
    result={}
    for pair in v:
        require(isinstance(pair,list) and len(pair)==2,'invalid coordinator dictionary')
        key=decode(pair[0]);require(isinstance(key,str) and key not in result,'duplicate/nonstring key');result[key]=decode(pair[1])
    return result


def keep(path,value):
    executor.retain(path,executor.canonical(value));return record(path)


def _controls(controls):
    require(isinstance(controls,Controls),'typed root controls required')
    lock=executor.safe(controls.slot_lock)
    require(lock.parent.is_dir() and (not lock.exists() or lock.is_file()),'existing coordinator-controlled slot directory required')
    if controls.env_file is not None:require(executor.safe(controls.env_file).is_file(),'selected environment file is unavailable')
    return lock


def _campaign(campaign):
    require(isinstance(campaign,executor.Campaign) and type(campaign.model_slots) is int and campaign.model_slots in (1,2),'configured model capacity must be one or two')
    executor.safe(campaign.root);executor.safe(campaign.slot_root)
    os.environ['HARBOR_MODEL_SLOTS']=str(campaign.model_slots)
    os.environ['HARBOR_MODEL_SLOT_ROOT']=str(campaign.slot_root)


def _descriptor(selection,lease,controls,origin):
    return dict(schema_version='harbor-environment-coordinator/v1',worker=encode(selection),initial_lease=encode(lease),controls=encode(controls),origin=origin,code=record(__file__))


def selected(binding):
    value=read(binding)
    expected={'schema_version','worker','initial_lease','controls','origin','code'}
    refreshed=value.get('schema_version')=='harbor-environment-coordinator-refresh/v1'
    require(set(value)==expected|({'refresh'} if refreshed else set()) and (refreshed or value['schema_version']=='harbor-environment-coordinator/v1'),'unsupported root descriptor')
    if refreshed:executor.checked_bytes(value['refresh'])
    require(value['code']==record(__file__),'coordinator code changed; explicit reviewed import required')
    s,lease,controls=(decode(value[k]) for k in ('worker','initial_lease','controls'))
    require(isinstance(s,worker.Selection) and isinstance(lease,bootstrap.Lease),'typed worker/lease required')
    _controls(controls)
    for evidence in value['origin']:executor.checked_bytes(evidence)
    return s,lease,controls


def _dependency(s,directory):
    recipe=s.bootstrap.policy.dependencies
    proof=keep(directory/'dependency.json',admission.dependency_report(recipe,ROOT))
    admission.validate_dependency(proof,recipe,ROOT)
    require(shutil.disk_usage(ROOT).free>=MEMORY,'8-GiB free-space reserve required')
    return proof


def _authorize(path,owner,revision,token=None):
    def exact(operation,target,before,request):
        return (operation==('renew' if token else 'acquire') and target==str(path)
            and before['revision']==revision and request['owner']==owner
            and request['scope']=='workbook:'+before['workbook_id']
            and (token is None or request.get('token')==token))
    return lifecycle.RootAuthority('harbor-orchestrator',DURATION,exact)


def _lease(s,lease,directory):
    _,before=worker._active(s,lease)
    if before['leases']['task_agent']['expires_at']-time.time_ns()>(SECONDS+30)*10**9:return lease
    path=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')
    result=lifecycle.lease_operation(path,authority=_authorize(path,lease.owner,lease.revision,lease.token),operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+s.bootstrap.workbook,token=lease.token,duration_ns=DURATION,expected_revision=lease.revision,expected_sha256=lease.sha256)
    renewed=replace(lease,revision=result['revision'],sha256=result['sha256'])
    keep(directory/'lease-renewal.json',{'previous':encode(lease),'lease':encode(renewed),'tracker':record(path)})
    return renewed


def _admit(intake,controls,directory):
    require(isinstance(intake,Intake),'typed root intake required');s=intake.bootstrap
    require(isinstance(s,bootstrap.Selection) and isinstance(intake.owner,str) and bool(intake.owner),'bootstrap/owner required')
    _campaign(intake.campaign)
    require(intake.campaign.model_slots<=s.policy.dependencies.maximum_models,'campaign exceeds declared model dependency capacity')
    require(not s.run.exists() and not intake.environment_run.exists(),'admission was already attempted; preserve and reconcile')
    environment=executor.safe(intake.environment_run)
    protected=(s.run,s.policy.tracker_root,Path(s.source_manifest['path']).parent,Path(s.segmentation_manifest['path']).parent,intake.campaign.root,intake.campaign.slot_root)
    require(all(environment!=p and environment not in p.parents and p not in environment.parents for p in protected),'environment namespace overlaps protected source/campaign/bootstrapping evidence')
    executor.checked_bytes(intake.taxonomy)
    if intake.inherited_audit_budget is not None:executor.checked_bytes(intake.inherited_audit_budget)
    prepared=bootstrap.prepare(s);admitted=bootstrap.admit(s,prepared)
    path=s.policy.tracker_root/(s.workbook+'.json')
    result=lifecycle.lease_operation(path,authority=_authorize(path,intake.owner,admitted['revision']),operation='acquire',lease_kind='task_agent',owner=intake.owner,scope='workbook:'+s.workbook,duration_ns=DURATION,expected_revision=admitted['revision'],expected_sha256=admitted['sha256'])
    lease=bootstrap.Lease(intake.owner,result['tracker']['leases']['task_agent']['token'],result['revision'],result['sha256'])
    budget=worker.normalization.Budget(1,2,str(uuid.uuid4()),(prepared,))
    selected_worker=worker.Selection(s,prepared,intake.environment_run,intake.campaign,intake.taxonomy,budget,intake.inherited_audit_budget)
    descriptor=keep(directory/'descriptor.json',_descriptor(selected_worker,lease,controls,(prepared,)))
    return dict(status='ADMITTED',descriptor=descriptor,lease=encode(lease),selection=None,evidence=[prepared],publication_ready=False)


def _prior(binding,descriptor):
    value=read(binding)
    require(value.get('descriptor')==descriptor,'checkpoint belongs to another descriptor')
    require(value.get('status') in {'ADMITTED','STEP_COMPLETE','PENDING_MODEL','PENDING_CAPTURE','IMPORTED','RESOURCE_WAIT'},'prior result requires root reconciliation, not replay')
    lease=decode(value['lease']);require(isinstance(lease,bootstrap.Lease),'checkpoint lease is not typed')
    for evidence in value['evidence']:executor.checked_bytes(evidence)
    if value['status']=='RESOURCE_WAIT':
        wait=read(value['resource_wait'])
        require(set(wait)=={'schema_version','checkpoint','descriptor','code','free_bytes','required_bytes','observation','checked_at_ns'},'resource wait schema differs')
        require(wait['schema_version']=='harbor-disk-resource-wait/v1' and wait['descriptor']==descriptor and wait['code']==record(__file__),'resource wait authority differs')
        require(type(wait['free_bytes']) is int and type(wait['required_bytes']) is int and 0<=wait['free_bytes']<wait['required_bytes'],'resource wait is not a disk deficit')
        old,_=_prior(wait['checkpoint'],descriptor)
        require(old.get('status')!='RESOURCE_WAIT','resource wait cannot wrap another wait')
        require(value=={**old,'status':'RESOURCE_WAIT','resource_wait':value['resource_wait'],'evidence':[*old['evidence'],value['resource_wait']]},'resource wait changed completed checkpoint')
    return value,lease


def _step(descriptor,checkpoint,directory,dispatch,captures):
    s,_,controls=selected(descriptor);prior,lease=_prior(checkpoint,descriptor)
    refresh=read(descriptor).get('refresh')
    if refresh is None:worker._setup(s,lease)
    else:worker._active(s,lease)
    _campaign(s.campaign)
    try:
        dependency=_dependency(s,directory)
    except admission.DiskReserveUnavailable as exc:
        require(exc.required_bytes==s.bootstrap.policy.dependencies.minimum_disk_bytes and 0<=exc.free_bytes<exc.required_bytes,'invalid disk-only observation')
        original=read(prior['resource_wait'])['checkpoint'] if prior['status']=='RESOURCE_WAIT' else checkpoint
        original_value=read(original)
        wait=keep(directory/'resource-wait.json',dict(schema_version='harbor-disk-resource-wait/v1',checkpoint=original,descriptor=descriptor,code=record(__file__),free_bytes=exc.free_bytes,required_bytes=exc.required_bytes,observation=exc.observation,checked_at_ns=time.time_ns()))
        return {**original_value,'status':'RESOURCE_WAIT','resource_wait':wait,'evidence':[*original_value['evidence'],wait]}
    lease=_lease(s,lease,directory)
    credential=None
    if dispatch:
        require(controls.env_file is not None,'explicit root-selected credential file required for dispatch')
        from xl_task_build import read_env_key
        credential=read_env_key(controls.env_file);require(bool(credential),'configured model credential unavailable')
    # Captures are root-selected immutable records, never fetched by coordinator.
    if captures is not None:read(captures)  # Validate JSON and hash, retain the binding.
    if refresh is None:result=worker.advance(s,lease,dispatch=dispatch,credential=credential,captures=captures)
    else:
        from scripts.batch.harbor_environment_refresh import advance
        result=advance(s,lease,refresh,directory,dispatch=dispatch,credential=credential,captures=captures)
    return {**result,'descriptor':descriptor,'lease':encode(result['lease']),'selection':content_codec.encode(result['selection']),'evidence':[*result['evidence'],dependency], 'publication_ready':False}


def _legacy_worker(value):
    """Exact asdict schema emitted by the original root-selected0035 driver."""
    require(set(value)==set(worker.Selection.__dataclass_fields__),'legacy worker fields changed')
    b=dict(value['bootstrap']);p=dict(b['policy']);d=dict(p['dependencies'])
    require(set(b)==set(bootstrap.Selection.__dataclass_fields__) and set(p)==set(admission.AdmissionPolicy.__dataclass_fields__) and set(d)==set(admission.DependencyRecipe.__dataclass_fields__),'legacy bootstrap/policy fields changed')
    d['requirements']=tuple(d['requirements']);d['imports']=tuple(d['imports']);p['dependencies']=admission.DependencyRecipe(**d)
    p['requested_ids']=tuple(p['requested_ids']);p['tracker_root']=Path(p['tracker_root']);p['history_roots']=tuple(Path(x) for x in p['history_roots']);p['campaign_evidence']=tuple(p['campaign_evidence'])
    b['policy']=admission.AdmissionPolicy(**p);b['run']=Path(b['run']);b['model_policy']=bootstrap.Policy(**b['model_policy']);b['refresh']=None if b['refresh'] is None else bootstrap.LedgerRefresh(**b['refresh'])
    c=value['campaign'];budget=value['normalization_budget']
    return worker.Selection(bootstrap.Selection(**b),value['prepared'],Path(value['run']),executor.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots']),value['taxonomy'],worker.normalization.Budget(budget['ordinal'],budget['limit'],budget['attempt_id'],tuple(budget['evidence'])),value['inherited_audit_budget'])


def _import(legacy,checkpoint,controls,directory):
    saved=read(legacy);require(set(saved)=={'worker','lease'},'only original root descriptor schema supported')
    s=_legacy_worker(saved['worker']);value=read(checkpoint)
    require(value.get('status') in {'STEP_COMPLETE','PENDING_MODEL','PENDING_CAPTURE','ENVIRONMENT_READY'},'legacy result is unresolved; root recovery must complete first')
    lease=bootstrap.Lease(**value['lease']);worker._setup(s,lease)
    current=content_codec.decode(value['selection'])
    if current is not None:require(current.content.workbook==s.bootstrap.workbook,'legacy result belongs to another workbook')
    if value['status'] in {'STEP_COMPLETE','ENVIRONMENT_READY'}:
        kind=value.get('step');require(kind in {'source','segmentation','research','normalization_phase1','source_profile','normalization_phase2','environment'},'legacy completed result has no fixed owning phase')
        if kind in {'source','segmentation'}:
            _,tracker=worker._active(s,lease);lane=dict(worker.lanes.STEPS)[kind]
            require(tracker['lane_state'][lane]['state']=='passed' and current is None,'legacy source lane differs from current owning tracker')
            bootstrap.validate(s.bootstrap)
        else:
            actual=worker._read_checkpoint(s,kind)
            require(content_codec.encode(actual)==value['selection'],'legacy result differs from retained owning checkpoint')
    for evidence in value['evidence']:executor.checked_bytes(evidence)
    # Revalidate every retained internal checkpoint the worker may resume from.
    for kind in ('research','normalization_phase1','source_profile','normalization_phase2','environment'):
        if (s.run/(kind+'.json')).exists():worker._read_checkpoint(s,kind)
    if (s.run/'research.json').exists():
        worker.audit_bridge.validate_completed(s.run/'audit-bridge',s.bootstrap.run/'audit'/(s.bootstrap.workbook+'-variable-sources'),record(s.run/'audit-plan.json'),s.bootstrap.model_policy,s.campaign)
    descriptor=keep(directory/'descriptor.json',_descriptor(s,lease,controls,(legacy,checkpoint)))
    return dict(status='ENVIRONMENT_READY' if value['status']=='ENVIRONMENT_READY' else 'IMPORTED',descriptor=descriptor,lease=encode(lease),selection=value['selection'],evidence=[legacy,checkpoint],publication_ready=False)



def _refresh(arguments,controls,directory):
    from scripts.batch import harbor_environment_refresh as refresh
    saved=read(arguments['legacy']);require(set(saved)=={'worker','lease'},'original root selection required')
    s=_legacy_worker(saved['worker']);lease=decode(read(arguments['lease']))
    require(isinstance(lease,bootstrap.Lease),'explicit renewed root lease required')
    current=content_codec.decode(read(arguments['current']))
    require(current.content.workbook==s.bootstrap.workbook,'foreign current selection')
    old,kind,_=refresh.predecessor(s,arguments['checkpoint'],current=current)
    last='normalization_phase1' if refresh.unchanged(old,current) else kind
    proof=refresh.prepare(s,lease,arguments['checkpoint'],current,last,directory/'prefix')
    lease,current,committed=refresh.commit(s,lease,proof)
    value=_descriptor(s,lease,controls,tuple(arguments.values())+(proof,committed))
    value.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=proof)
    descriptor=keep(directory/'descriptor.json',value)
    return dict(status='IMPORTED',step=last,descriptor=descriptor,lease=encode(lease),selection=content_codec.encode(current),evidence=[proof,committed,*arguments.values()],publication_ready=False)


def _recover_refresh(arguments,controls,directory):
    from scripts.batch import harbor_environment_refresh as refresh
    saved=read(arguments['legacy']);require(set(saved)=={'worker','lease'},'original root selection required')
    s=_legacy_worker(saved['worker']);lease=decode(read(arguments['lease']))
    require(isinstance(lease,bootstrap.Lease),'explicit current root lease required')
    worker._active(s,lease)
    committed=refresh.reconcile(s,arguments['refresh'])
    proof,current=refresh.validate(s,arguments['refresh'])
    receipt=read(committed)
    require((lease.revision,lease.sha256)==(receipt['revision'],receipt['tracker_sha256']),'select the exact committed CAS before subsequent renewals')
    value=_descriptor(s,lease,controls,tuple(arguments.values())+(committed,))
    value.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=arguments['refresh'])
    descriptor=keep(directory/'descriptor.json',value)
    return dict(status='IMPORTED',step=proof['last'],descriptor=descriptor,lease=encode(lease),selection=content_codec.encode(current),evidence=[committed,*arguments.values()],publication_ready=False)

def child(intent):
    value=read(intent)
    require(set(value)=={'schema_version','action','arguments','controls','dispatch','code','executable','memory_limit_bytes','timeout_seconds'} and value['schema_version']=='harbor-coordinator-step/v1','invalid fixed launch intent')
    require(value['code']==record(__file__) and value['executable']==record(Path(sys.executable).resolve()),'coordinator runtime changed after launch intent')
    require(value['memory_limit_bytes']==MEMORY and value['timeout_seconds']==SECONDS and type(value['dispatch']) is bool,'fixed launch limits changed')
    directory=Path(intent['path']).parent;controls=decode(value['controls']);_controls(controls)
    require(record(directory/'intent.json')==intent,'foreign child intent')
    action=value['action'];arguments=value['arguments']
    if action=='admit':result=_admit(decode(read(arguments['intake'])),controls,directory)
    elif action=='import':result=_import(arguments['legacy'],arguments['checkpoint'],controls,directory)
    elif action=='refresh':result=_refresh(arguments,controls,directory)
    elif action=='recover-refresh':result=_recover_refresh(arguments,controls,directory)
    elif action=='step':result=_step(arguments['descriptor'],arguments['checkpoint'],directory,value['dispatch'],arguments.get('captures'))
    else:raise ValueError('unsupported coordinator action')
    return keep(directory/'result.json',result)


def _guardian_fds(value):
    """Explicit parent-owned lock custody, retained by each process guardian."""
    import stat
    require(isinstance(value,tuple) and len(value)<=8 and all(type(fd) is int and fd>=0 for fd in value)
            and len(set(value))==len(value),'invalid inherited lock descriptors')
    for fd in value:require(stat.S_ISREG(os.fstat(fd).st_mode),'inherited custody descriptor must be a regular lock file')
    return value


def run(action,arguments,controls,directory,*,dispatch=False,guardian_lock_fds=()):
    """One guarded operation. A fresh directory is mandatory, even after failure."""
    guardian_lock_fds=_guardian_fds(guardian_lock_fds)
    require(action in {'admit','import','refresh','recover-refresh','step'} and type(dispatch) is bool,'explicit action/dispatch required')
    require(action=='step' or not dispatch,'admission/import cannot dispatch models')
    allowed={'admit':{'intake'},'import':{'legacy','checkpoint'},'step':{'descriptor','checkpoint','captures'},'refresh':{'legacy','checkpoint','current','lease'},'recover-refresh':{'legacy','refresh','lease'}}[action]
    require(set(arguments)==allowed,'exact root-selected arguments required')
    for binding in arguments.values():
        if binding is not None:executor.checked_bytes(binding)
    if action=='step':require(selected(arguments['descriptor'])[2]==controls,'selected controls changed')
    lockpath=_controls(controls);directory=executor.safe(directory)
    if dispatch:require(controls.env_file is not None,'explicit root-selected environment file required before model dispatch')
    require(not directory.exists(),'coordinator attempt exists; retain it and select an explicit completed checkpoint')
    require(directory!=lockpath.parent and directory not in lockpath.parents,'journal overlaps shared slot')
    require(shutil.disk_usage(ROOT).free>=MEMORY,'8-GiB free-space reserve required')
    fd=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directory.mkdir(parents=True,mode=0o700)
        intent=keep(directory/'intent.json',dict(schema_version='harbor-coordinator-step/v1',action=action,arguments=arguments,controls=encode(controls),dispatch=dispatch,code=record(__file__),executable=record(Path(sys.executable).resolve()),memory_limit_bytes=MEMORY,timeout_seconds=SECONDS))
        metrics={};rc=None;failure=None;logpath=directory/'step.log'
        try:
            with logpath.open('xb') as log:
                rc=_run_in_process_group([sys.executable,'-B',str(Path(__file__).resolve()),'_child',intent['path'],intent['sha256']],stdout=log,timeout_seconds=SECONDS,grace_seconds=3,memory_limit_bytes=MEMORY,metrics=metrics,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT)},guardian_lock_fds=(fd,*guardian_lock_fds))
                log.flush();os.fsync(log.fileno())
        except Exception as exc:
            failure=dict(type=type(exc).__name__,message_sha256=executor.sha(str(exc).encode()))
        resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,metrics=metrics,log=record(logpath),failure=failure))
        if rc!=0 or failure or not (directory/'result.json').is_file():
            return keep(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
        result=record(directory/'result.json');read(result)
        return keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=resource,publication_performed=False))
    finally:os.close(fd)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv and argv[0]=='_child':
        require(len(argv)==3,'exact child intent record required');child(dict(path=argv[1],sha256=argv[2]));return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('admit','import','refresh','recover-refresh','step'))
    for field in ('intake','legacy','descriptor','checkpoint','captures','current','lease','refresh'):
        parser.add_argument('--'+field);parser.add_argument('--'+field+'-sha256')
    parser.add_argument('--journal',required=True);parser.add_argument('--slot-lock',required=True);parser.add_argument('--env-file');parser.add_argument('--dispatch-models',action='store_true')
    args=parser.parse_args(argv);arguments={}
    for field in ('intake','legacy','descriptor','checkpoint','captures','current','lease','refresh'):
        path,sha=getattr(args,field),getattr(args,field+'_sha256')
        require((path is None)==(sha is None),'path and explicit sha256 must be paired')
        if path is not None:arguments[field]=dict(path=path,sha256=sha)
    if args.action=='step':arguments.setdefault('captures',None)
    result=run(args.action,arguments,Controls(Path(args.slot_lock),None if args.env_file is None else Path(args.env_file)),Path(args.journal),dispatch=args.dispatch_models)
    print(json.dumps(result),flush=True);return 0

if __name__=='__main__':raise SystemExit(main())
