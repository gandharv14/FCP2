"""One guarded physical disclosure retry from an exact stopped Fresh author.

The original logical reservation, rejection and selected content stay unchanged.
The original payload/lease/instruction/dispatcher implementations own all writes.
"""
from pathlib import Path
from dataclasses import replace,asdict
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time

SCHEMA='fleet-disclosure-payload-continuation/v1'
FIELDS={'schema_version','helper','workbook','source','fresh_plan','original_job','outcome','result',
        'old_snapshot','snapshot','config','tracker','owner','fence','output','capacity_policy',
        'renewal','authorization','projection','preview'}
AUTHORIZATION_SHA='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'canonical physical continuation evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(type(row)is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'physical continuation evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def keep(path,value):
    path=Path(path);require(not any(x.is_symlink()for x in(path,*path.parents)),'unsafe physical continuation journal')
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)

def module(name,row):
    require(binding(row['path'])==row,'physical continuation helper changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def snapshot(row):
    s=read(row);root=Path(s['root']);expected={x['relative']:x['sha256']for x in s['files']}
    actual={str(p.relative_to(root)):binding(p)['sha256']for p in root.rglob('*')if p.is_file()and '__pycache__'not in p.parts}
    require(actual==expected and len(expected)==len(s['files']),'complete physical snapshot differs')
    return root

def failure(p,D,config):
    checkpoint,value=D.successful(p['outcome'])
    require(checkpoint==p['result'] and value['status']=='MODEL_RECONCILIATION_REQUIRED'
        and value['next_step']=='disclosure-review' and value['publication_performed']is False,
        'only exact stopped disclosure model boundary can continue')
    out=read(p['outcome']);guard=read(out['resource']);intent=read(guard['intent']);desc=read(value['descriptor'])
    old=read(p['old_snapshot']);old_I=binding(Path(old['root'])/'scripts/harbor_instruction_campaign.py')
    require(intent['code']==old_I and intent['action']=='step' and intent['models']is True
        and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592
        and intent['arguments']['descriptor']==value['descriptor']
        and intent['controls']==config['controls']==desc['controls']
        and value['evidence'][0]==intent['arguments']['checkpoint'] and desc['code']==old_I,
        'original disclosure guard or descriptor changed')
    blocked=read(value['checkpoint']);require(blocked['status']=='MODEL_RECONCILIATION_REQUIRED'
        and blocked['step']=='disclosure-review' and blocked['selection']==value['selection'],
        'actual blocked disclosure checkpoint differs')
    return value,desc

def context(row,*,child=False):
    p=read(row);require(set(p) in (FIELDS,FIELDS|{'takeover'}) and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed physical continuation plan required')
    require(p['authorization']['sha256']==AUTHORIZATION_SHA and binding(p['authorization']['path'])==p['authorization'],'explicit physical repair authority required')
    fresh=read(p['fresh_plan']);job=read(p['original_job']);old_config=read(job['config']);config=read(p['config'])
    require(fresh['schema_version']=='harbor-fleet-fresh-plan/v1' and fresh['workbook']==p['workbook']
        and job['driver']==fresh['dispatcher'] and old_config['workbook']==p['workbook']
        and old_config['code']==p['old_snapshot'] and config=={**old_config,'code':p['snapshot']}
        and all(job[k]is True for k in('models','commands','containers')) and job['publish']is False,
        'physical continuation changed original Fresh authoring policy')
    root=snapshot(p['snapshot']);snapshot(p['old_snapshot'])
    require('scripts'not in sys.modules,'physical continuation requires cold isolated imports')
    sys.path.insert(0,str(root));os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    legacy=fresh.get('legacy_research_history')
    if legacy is not None:
        history=read(legacy);require(history['workbook']==p['workbook'] and history['original']==p['source'],'physical repair detached original source history')
        require(not any(os.environ.get(k)for k in('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH')),'foreign source history')
        for key,value in [('HARBOR_FLEET_LEGACY_RESEARCH_PATH',legacy['path']),('HARBOR_FLEET_LEGACY_RESEARCH_SHA256',legacy['sha256'])]:
            require(os.environ.get(key)in(None,value),'foreign legacy source projection');os.environ[key]=value
    D=module('physical_original_dispatcher',job['driver']);env=D.setup({**job,'config':p['config']});C,I,P,W,R,_,observed_root,controls=env
    require(root==observed_root and C.SECONDS==3300 and C.MEMORY==8589934592,'original owning guards differ')
    prior,desc=failure(p,D,old_config);w=replace(I.worker_decode(desc['worker']),root=root);selection=I.codec.decode(prior['selection']);lease=C.decode(prior['lease'])
    require(w.run==Path(config['run_root'])/'instruction' and selection.content.workbook==p['workbook']
        and selection.content.bindings['original_raw_source']==p['source'] and binding(p['source']['path'])==p['source']
        and p['tracker']['path']==str(w.context.tracker_root/(p['workbook']+'.json')),'physical repair original source/worker changed')
    require(type(p['fence'])is int and p['fence']>0 and p['owner'] and Path(p['output']).is_absolute()
        and not Path(p['output']).is_relative_to(root),'isolated explicit fleet custody required')
    output=Path(p['output']);require((output.is_dir() and not(output/'permit').exists())if child else not output.exists(),'physical continuation already attempted; no replay')
    from scripts.batch import harbor_disclosure_review_resume as repair,harbor_disclosure_payload_repair as payload,harbor_instruction_repair as history
    require(p['projection']==payload.RECORD_PROJECTION,'explicit complete records physical projection required')
    repair.changes(p['old_snapshot'],root,projection=p['projection'])
    original=W.request_saved(w.run/'steps/disclosure-review/request.json');payload._rejected(original.prepared,w.campaign)
    require(payload.project(read(original.prepared.request),projection=p['projection'])==read(p['preview']),'selected complete physical projection changed')
    require(not(W.executor.location(original.prepared,w.campaign).parent/'physical-repair.json').exists()
        and not(w.run/'disclosure-repair.json').exists(),'physical repair already reserved')
    require(binding(p['tracker']['path'])==p['tracker'],'native tracker changed before physical authorization')
    if 'takeover' in p:
        from scripts.batch import harbor_lease_takeover as T
        before,after,current=T.validate_completion(p['takeover']);stop=read(read(p['takeover'])['stopped_owner_proof']);chosen=stop['selection']
        require(chosen['workbook']==p['workbook'] and chosen['owner']==p['owner']
            and chosen['config']==job['config'] and chosen['checkpoint']==p['result']
            and chosen['guard']==read(p['outcome'])['resource']
            and before['leases']['task_agent']['owner']==lease.owner and before['leases']['task_agent']['token']==lease.token
            and chosen['tracker']['path']==p['tracker']['path'] and read(p['tracker'])==after,
            'completed takeover detached from original physical disclosure boundary')
        lease=current
    W.active(w,selection,lease)
    L=module('physical_continuation_lease',p['renewal']);require(L.DURATION_NS==7200*10**9,'bounded continuation lease required')
    return p,job,D,env,w,selection,lease,prior,desc,repair,payload,history,L

def active(p,root):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'] and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
        and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==p['fence'],'owning physical continuation environment required')
    c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);s=c.call('status');capacity=module('physical_selected_capacity',p['capacity_policy']);capacity.validate_occupancy(s)
    rows=[w for w in s['workbooks']if w['workbook']==p['workbook']];require(len(rows)==1,'unique physical claim required');v=rows[0]
    require(abs(time.time()-s['at'])<=60 and v['state']=='active' and v['owner']==p['owner'] and v['fence']==p['fence']
        and v['source_sha256']==p['source']['sha256'] and v['version']==root.name and v['expires']>time.time(),'exact live physical claim required')
    require(not any(o['workbook']==p['workbook']and o['state']in('active','hold')for o in s['operations']),'target model operation unresolved')
    if hasattr(capacity,'model_admission_barriers'):require(not capacity.model_admission_barriers(s,p['workbook'],p['owner']),'model custody barrier')
    c.call('heartbeat',workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)

def prove(row):
    p,job,D,env,w,s,lease,prior,desc,R,P,H,L=context(row);C,I,_,W,_,_,root,_=env
    before=W.active(w,s,lease)[1];proof=H.prefix(before,s,w.context,stop='disclosure')
    require(binding(p['tracker']['path'])==p['tracker'],'native proof changed tracker')
    return dict(status='PHYSICAL_DISCLOSURE_CONTEXT_PROVEN',plan=row,tracker=p['tracker'],prefix=[x['kind']for x in proof],
        records=len(__import__('scripts.batch.harbor_disclosure_evidence',fromlist=['decode_body']).decode_body(json.loads(read(p['preview'])['messages'][1]['content']))['disclosure']['records']),
        native_lease_seconds=(before['leases']['task_agent']['expires_at']-time.time_ns())/1e9,models=0,claims=0,writes=0)

def permit_child(row):
    p,job,D,env,w,s,lease,prior,desc,R,P,H,L=context(row,child=True);C,I,_,W,_,_,root,controls=env;out=Path(p['output']);active(p,root)
    def validation(before,old):
        require(before==W.active(w,s,old)[1],'native validation tracker differs');P._rejected(W.request_saved(w.run/'steps/disclosure-review/request.json').prepared,w.campaign)
        proof=H.prefix(before,s,w.context,stop='disclosure');return [prior['checkpoint'],p['result'],p['old_snapshot'],p['snapshot'],p['preview'],*[r for part in proof for r in part['result']['evidence']]]
    if 'takeover' in p:
        renewed=p['takeover']
    else:
      with(w.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        lease,renewed=L.renew(life=C.lifecycle,read=I.read,record=binding,keep=keep,path=p['tracker']['path'],lease=lease,authority_record=row,
            validation=validation,custody=lambda:active(p,root),directory=out/'renewal')
    active(p,root);permit=R.authorize(w,s,lease,blocked=prior['checkpoint'],old_snapshot=p['old_snapshot'],directory=out/'permit',projection=p['projection'],takeover=p.get('takeover'))
    updated=replace(w,disclosure_repair=permit);R.validate(updated,W.active(updated,s,lease)[1]);handoff=out/'handoff';handoff.mkdir()
    descriptor=keep(handoff/'descriptor.json',{**desc,'code':binding(I.__file__),'worker':I.worker_encode(updated),
        'initial':dict(selection=I.codec.encode(s),lease=C.encode(lease)),'origin':[*desc['origin'],row,p['result'],p['snapshot'],renewed,permit]})
    result=keep(handoff/'result.json',I._result('RETRY_READY',descriptor,s,lease,checkpoint=permit,next_step='disclosure-review',evidence=[p['result'],row,renewed,permit]))
    I.selected(descriptor);I._prior(result,descriptor);return result

def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot execute physical continuation')
    p,job,D,env,w,s,lease,prior,desc,R,P,H,L=context(row);C,I,_,W,_,config,root,controls=env;active(p,root)
    require(W.active(w,s,lease)[1]['leases']['task_agent']['expires_at']>time.time_ns()+60*10**9,'expired native lease requires original takeover')
    out=Path(p['output']);require(__import__('shutil').disk_usage(root).free>=12*1024**3,'original native disk reserve required');out.parent.mkdir(parents=True,exist_ok=True);out.mkdir()
    intent=keep(out/'intent.json',dict(plan=row,helper=p['helper'],automatic_retry=False));command=[sys.executable,'-I','-B',p['helper']['path'],'permit-child','--plan',row['path'],'--sha256',row['sha256']]
    metrics={};rc=None;failure_type=None;started=time.monotonic()
    with D.fleet_slot(p['workbook'])as lock_fds:
        try:
            with(out/'permit.log').open('xb')as log:rc=C._run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=root,env=os.environ.copy(),guardian_lock_fds=lock_fds)
        except BaseException as exc:failure_type=type(exc).__name__
        guard=keep(out/'permit-resource.json',dict(intent=intent,command=command,returncode=rc,failure=failure_type,elapsed_seconds=time.monotonic()-started,metrics=metrics,seconds=3300,memory_bytes=8589934592,log=binding(out/'permit.log'),models=0,automatic_retry=False))
        require(rc==0 and failure_type is None,'physical permit stopped; reconcile, never replay')
        checkpoint=binding(out/'handoff/result.json');value=read(checkpoint);active(p,root)
        outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,out/'first-review',models=True,commands=True,containers=True,guardian_lock_fds=lock_fds)
        D.successful(outcome,env)
    operations=D.operation_namespace(config,str(Path(config['run_root'])/'dispatcher-v2'/out.name));current={**job,'config':p['config'],'outcome':outcome,'operations':str(operations)};bound=keep(out/'authoring-job.json',current)
    D.run(bound);held=read(binding(Path(operations)/'hold.json'));last,value=D.successful(held['outcome'],env)
    require(value['status']=='PUBLICATION_READY','remaining original authoring reached a hold')
    return keep(out/'result.json',dict(status='PUBLICATION_READY',workbook=p['workbook'],plan=row,job=bound,config=p['config'],outcome=held['outcome'],checkpoint=last,permit_guard=guard,publication_performed=False,automatic_retry=False))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','permit-child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();row=dict(path=v.plan,sha256=v.sha256)
    print(json.dumps({'probe':prove,'run':run,'permit-child':permit_child}[v.action](row)))
