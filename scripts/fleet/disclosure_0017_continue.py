"""Once-only 0017 native takeover and scope-repair permit; no model dispatch.

Stops at the owning RETRY_READY checkpoint. The ordinary instruction worker
must complete the deterministic disclosure build, then its original one review.
"""
import argparse,hashlib,json,os,shutil,sys
from dataclasses import replace
from pathlib import Path
SCHEMA='harbor-fleet-0017-scope-repair-continuation/v1'
MEMORY=8*1024**3
SECONDS=3300

def require(v,message):
    if not v:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not p.is_symlink(),'regular bound file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'bound evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def retain(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)
def load(plan_binding,*,guard=None):
    p=checked(plan_binding)
    require(set(p)=={'schema_version','workbook','snapshot','source','tracker','checkpoint','descriptor','blocked','guard','original_config','stopped_proof','home','takeover_directory','native_python','native_env_file','owner'},'closed 0017 continuation required')
    require(p['schema_version']==SCHEMA and p['workbook']=='0017','0017 scope required')
    for k in ('snapshot','tracker','checkpoint','descriptor','blocked','guard','original_config','stopped_proof'):checked(p[k])
    require(binding(p['source']['path'])==p['source'],'0017 source changed')
    root=Path(checked(p['snapshot'])['root']);sys.path.insert(0,str(root))
    from scripts.fleet import disclosure_0017_proof as P
    from scripts import harbor_instruction_campaign as I,harbor_campaign as C
    from scripts.batch import harbor_lease_takeover as T,harbor_transition_codec as codec
    P.verified_code(root);profile=P.profile();proof=checked(profile['source_proof'])
    require(p['source']==proof['bindings']['source'],'0017 source proof detached')
    old=checked(p['descriptor']);worker=I.worker_decode(old['worker']);home=Path(worker.run).parent
    require(p['descriptor']==binding(home/'dispatcher-v2/first/008/descriptor.json') and p['blocked']==binding(home/'instruction/steps/disclosure-build/exception.json'),'0017 original boundary differs')
    require(p['tracker']==binding(home/'trackers/workbooks/0017.json') and p['original_config']==binding(home/'config.json'),'0017 tracker/config changed')
    require(checked(checked(p['guard'])['intent'])['arguments']['checkpoint']==p['checkpoint'] and checked(p['checkpoint'])['descriptor']==p['descriptor'],'0017 retained guarded checkpoint differs')
    require(Path(p['home']).is_absolute() and not Path(p['home']).is_relative_to(root),'0017 continuation namespace invalid')
    if guard is None:require(not Path(p['home']).exists(),'0017 continuation namespace used')
    else:require(Path(guard)==Path(p['home'])/'guard' and Path(guard).is_dir() and not (Path(guard)/'result.json').exists() and not (Path(p['home'])/'scope-repair').exists(),'0017 guard continuation differs or consumed')
    require(Path(p['takeover_directory']).parent==home/'lease-takeovers' and not Path(p['takeover_directory']).exists(),'0017 dedicated unused takeover directory required')
    require(p['owner'].startswith('hhu-server-6:') and p['native_python'].startswith('/home/gmahajan_labelbox_com/synthetic-data-fleet/releases/') and p['native_python'].endswith('/venv/bin/python'),'0017 root selected host/native interpreter differs')
    require(p['native_env_file']=='/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env','0017 native credential locator differs')
    handoff=checked(p['stopped_proof']);require(handoff['schema_version']=='local-to-vm-custody-handoff/v1' and handoff['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION' and checked(handoff['pause'])['new_dispatch_allowed'] is False,'0017 local handoff absent')
    checked(handoff['process_check']);checked(handoff['lock_check']);rows=[r for r in handoff['workbooks'] if r['workbook_id']=='0017'];require(len(rows)==1 and rows[0]['original_source']['sha256']==p['source']['sha256'],'0017 local custody source changed')
    require(worker.campaign.model_slots==1 and C.MEMORY==MEMORY and C.SECONDS==SECONDS,'0017 model/resource guards differ')
    selection=codec.decode(checked(P.profile()['history']['initial'])['selection']);worker=replace(worker,root=root)
    controls=replace(C.decode(old['controls']),env_file=Path(p['native_env_file']));recipe=replace(C.decode(old['dependencies']),maximum_models=1)
    takeover=T.Selection(campaign_root=home,workbook='0017',tracker=p['tracker'],worker_kind='instruction',guard=p['guard'],checkpoint=p['checkpoint'],owner=p['owner'],config=p['original_config'])
    T._recipe(takeover)
    return p,P,I,C,T,worker,selection,controls,recipe,takeover

def probe(plan_binding,output=None):
    p,P,I,C,T,w,s,controls,recipe,takeover=load(plan_binding)
    from scripts.fleet import disclosure_0017_repair as Q
    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_repair as R,harbor_model_executor as E
    E.execute=lambda *a,**k:(_ for _ in ()).throw(AssertionError('0017 inspection cannot dispatch models'))
    E._open=E.execute
    inspection=Q.inspect_history(w.root);before=checked(p['tracker'])
    prefix=R.prefix(before,W.refreshed(s),w.context,stop='disclosure')
    native=None
    if output is not None:
        require(sys.executable==p['native_python'],'0017 native dependency observation requires selected interpreter')
        retain(Path(output)/'prefix.json',dict(plan=plan_binding,source_proof=P.profile()['source_proof'],prefix=prefix,model_calls=0,completed_arbitration=inspection['completed_arbitration']))
        C._campaign(w.campaign);C._controls(controls)
        native=native_dependency(I,recipe,Path(output)/'native-dependency')
    return dict(schema_version=SCHEMA,status='SCOPE_REPAIR_TAKEOVER_RECIPE_VERIFIED',plan=plan_binding,snapshot=p['snapshot'],tracker=p['tracker'],source_proof=P.profile()['source_proof'],prefix=prefix,native_dependency=native,native_python=sys.executable,completed_arbitration=inspection['completed_arbitration'],model_calls=0,publication_ready=False,review_ordinal=1,review_limit=1,legacy_completed_calls=3,execution_performed=False,native_task_containers_revalidated=False)

def native_dependency(I,recipe,directory):
    """The owning atomic writer requires its isolated parent to exist."""
    directory=Path(directory);require(not directory.exists(),'0017 native dependency receipt already exists')
    directory.mkdir(parents=True)
    return I.dependency(recipe,directory)

def child(intent_binding):
    intent=checked(intent_binding);require(set(intent)=={'plan','driver','seconds','memory'} and intent['driver']==binding(__file__) and intent['seconds']==SECONDS and intent['memory']==MEMORY,'0017 guard intent differs')
    p,P,I,C,T,w,s,controls,recipe,takeover=load(intent['plan'],guard=Path(intent_binding['path']).parent)
    require(sys.executable==p['native_python'],'0017 execute only selected native interpreter')
    require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'],'0017 claim owner differs')
    P.require_fleet_custody(w.root)
    # This preparation operation cannot consume even an accidental model call.
    from scripts.batch import harbor_model_executor as E
    from scripts.fleet import disclosure_0017_repair as Q
    E.execute=lambda *a,**k:(_ for _ in ()).throw(AssertionError('scope-repair permit cannot dispatch models'))
    E._open=E.execute
    C._campaign(w.campaign);C._controls(controls)
    directory=Path(intent_binding['path']).parent
    retain(directory/'child-started.json',dict(intent=intent_binding,automatic_retry=False))
    dep=I.dependency(recipe,directory)
    lease,completion=T.replace_expired(takeover,Path(p['takeover_directory']))
    # Keep the original failed selection: the supported permit owns the exact
    # source-equivalence projection and preserves every original author record.
    permit=Q.authorize(w,s,lease,old_snapshot=P.profile()['previous'],directory=Path(p['home'])/'scope-repair',takeover=completion)
    selected=I.codec.decode(checked(permit)['selection']);w=replace(w,code_repair=permit)
    descriptor=retain(directory/'descriptor.json',dict(schema_version=I.SCHEMA,worker=I.worker_encode(w),controls=C.encode(controls),dependencies=C.encode(recipe),origin=[p['descriptor'],p['checkpoint'],intent['plan'],dep,completion,permit],code=binding(I.__file__),initial=dict(selection=I.codec.encode(selected),lease=C.encode(lease))))
    result=retain(directory/'result.json',I._result('RETRY_READY',descriptor,selected,lease,checkpoint=permit,next_step='disclosure-build',evidence=[p['checkpoint'],dep,completion,permit]))
    I.selected(descriptor);I._prior(result,descriptor)
    return result

def run(plan_binding):
    p,P,I,C,T,w,s,controls,recipe,takeover=load(plan_binding)
    require(sys.executable==p['native_python'],'0017 execute only selected native interpreter')
    P.require_fleet_custody(w.root)
    require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'],'0017 claim owner differs')
    require(shutil.disk_usage(w.root).free>=MEMORY,'0017 8-GiB disk reserve required')
    directory=Path(p['home'])/'guard';directory.mkdir(parents=True)
    intent=retain(directory/'intent.json',dict(plan=plan_binding,driver=binding(__file__),seconds=SECONDS,memory=MEMORY))
    from scripts.batch.hardened_runner import _run_in_process_group
    metrics={};rc=None;failure=None
    try:
        with (directory/'step.log').open('xb') as log:
            rc=_run_in_process_group([sys.executable,'-I','-B',str(Path(__file__).absolute()),'child','--plan',intent['path'],'--sha256',intent['sha256']],stdout=log,timeout_seconds=SECONDS,grace_seconds=3,memory_limit_bytes=MEMORY,metrics=metrics,cwd=w.root,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    except Exception as exc:failure=type(exc).__name__
    resource=retain(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),automatic_retry=False))
    good=rc==0 and failure is None and (directory/'result.json').is_file()
    result=binding(directory/'result.json') if good else None
    return retain(Path(p['home'])/'result.json',dict(schema_version=SCHEMA,status='RETRY_READY' if good else 'RECONCILIATION_REQUIRED',plan=plan_binding,resource=resource,checkpoint=result,model_calls=0,publication_ready=False,publication_performed=False,automatic_retry=False,remaining='One ordinary deterministic disclosure-build completion, first independent disclosure review1/1, remaining native authoring/containers/publication and independent collection.'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['probe','run','child']);p.add_argument('--plan',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output');a=p.parse_args();b=dict(path=str(Path(a.plan).absolute()),sha256=a.sha256)
    value=probe(b,a.output) if a.action=='probe' else {'run':run,'child':child}[a.action](b)
    if a.output:value=retain(Path(a.output)/'result.json',checked(value) if a.action in ('run','child') else value)
    print(json.dumps(value))
