"""Declared 0077 initial-instruction continuation with original takeover custody.

The original environment, normalization Calls, code and initial checkpoint stay
immutable. An expired takeover is a separate once-only operation. Its remaining
runner binds a new typed initial handoff and invokes the original step dispatcher.
"""
from pathlib import Path
from dataclasses import asdict
from types import FunctionType
import argparse, hashlib, importlib.util, json, os, sys, time

SCHEMA = 'fleet-0077-initial-continuation/v2'
VERSION = '94fa49411fd92b8ca1bf596d473c4fdfed894326d125fde498f94963c7b71779'
SOURCE = '0df2f7af48453b5bbdce96f447189f269f68ed1f0a2bb0c1b25a0a4d3b07b8eb'
OWNER = 'hhu-server-6:slot-2:0077-initial-continuation-004'

def require(ok, message):
    if not ok: raise ValueError(message)

def binding(path):
    p = Path(path)
    require(p.is_absolute() and not any(x.is_symlink() for x in (p, *p.parents)), 'canonical evidence required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(type(row) is dict and set(row)=={'path','sha256'} and binding(row['path'])==row, 'bound evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name, row):
    require(binding(row['path'])==row, 'selected implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']); value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value; spec.loader.exec_module(value); return value

def load(row):
    p=read(row)
    require(set(p)=={'schema_version','helper','old_driver','old_plan','recipe','takeover','capacity','model_gate',
                    'home','operations','takeover_home','takeover_directory','old_job','source','version','stdlib','stopped_central','stopped_process'}, 'closed initial plan required')
    require(p['schema_version']==SCHEMA and p['helper']==binding(__file__) and p['source']==SOURCE and p['version']==VERSION,
            'selected initial identity differs')
    require(p['old_driver']['sha256']=='1798e3e22bb34e5e95cba5f4c88fdc27c5cf37e0da2c412f5a18ea54ea9e024c'
            and p['old_plan']['sha256']=='882c9badb1a8084c60bba850fd372d436c9077a68275ae817f45a9fd03b6ec06'
            and p['takeover']['sha256']=='593cc980f450c8b6f81c2ee4d9a301bbf6ff1dc178a8326501958b17ef447a62'
            and p['recipe']['sha256']=='4ee21dccf091525e728d2dbde82771a4cd43cefa041c220f654080749820c3a7', 'unreviewed initial lineage')
    require(p['stdlib']['sha256']=='5bb59bfa3010bc1ba156b20ba12ae602881b590877c39e63fd98b6a6dda588a8','stdlib bootstrap differs')
    module('0077_verified_stdlib',p['stdlib']).preload_inspect()
    os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','')
    central=read(p['stopped_central']);stopped=read(p['stopped_process'])
    require(p['stopped_central']['sha256']=='72d626614fbbed81bece8bc683a51fa7536d75e42d086bc40a06a06e7d829782' and p['stopped_process']['sha256']=='6692059165154a203c568992bada918dc005e4d3523c2aecb486534bab6228c6','stopped evidence selection differs')
    require(stopped['process']['state']=='ABSENT' and central['workbook']['state']=='hold' and central['workbook']['fence']==3,'stopped initial custody required')
    M=module('original0077_completed_normalization',p['old_driver'])
    prior,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=M.load(p['old_plan'])
    base,C,_,W,R,config,root,controls=env
    require(root.name==VERSION and base.SECONDS==3300 and base.MEMORY==8589934592,'original snapshot/guards differ')
    proof=read(p['recipe']); native=json.loads(proof['log']); resource=proof['resource']
    require(resource['returncode']==0 and resource['failure'] is None and resource['guard_seconds']==3300
            and resource['memory_bytes']==8589934592 and native['status']=='ACTUAL_0077_INITIAL_RECIPE_VALIDATED'
            and native['replacement_count']==1 and native['models_dispatched']==native['owning_operations']==0,
            'actual original recipe proof required')
    T=module('scripts.batch.harbor_lease_takeover',p['takeover'])
    import scripts.batch, scripts.fleet
    scripts.batch.harbor_lease_takeover=T
    policy=module('scripts.fleet.capacity_policy',p['capacity']); scripts.fleet.capacity_policy=policy
    gate=module('scripts.fleet.model_gate',p['model_gate']); scripts.fleet.model_gate=gate
    require(p['capacity']['sha256']=='17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4'
            and p['model_gate']['sha256']=='8e284b66b43b8acd215700a812d242ae3eb8d6cb964122addfdb27c8441baf92', 'model policy differs')
    s=T._selection(native['selection']); old=read(p['old_job'])
    require(s.workbook=='0077' and s.worker_kind=='environment' and s.owner==OWNER and s.config==old['config']
            and s.duration_ns==T.MAX_DURATION_NS==7200*10**9 and T.MAX_REPLACEMENTS==3, 'original takeover selection differs')
    initial=read(native['initial_checkpoint']); held=read(native['renewal_hold'])
    require(initial['status']=='INSTRUCTION_READY' and initial['checkpoint'] is None and initial['next_step']=='disclosure-build'
            and held['status']=='LEASE_RENEWAL_REQUIRED' and held['checkpoint'] is None
            and held['evidence']==[native['initial_checkpoint']], 'unstarted original instruction boundary required')
    require(Path(p['home'])==Path(prior['home']).parent/'initial-authoring-004'
            and Path(p['operations'])==Path(config['run_root'])/'dispatcher-v2/initial-authoring-004'
            and Path(p['takeover_home'])==Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/0077-initial-takeover-002')
            and Path(p['takeover_directory'])==Path(config['run_root'])/'lease-takeovers/fleet-initial-0077-002', 'new namespaces differ')
    dispatcher.operation_namespace(config,p['operations'])
    return p,M,env,A,dispatcher,old,T,s,native,policy

def custody(p,policy,*,active=False):
    from scripts.fleet.coordinator import Client
    c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']); status=c.call('status')
    policy.validate_occupancy(status); rows=[w for w in status['workbooks'] if w['workbook']=='0077']
    require(len(rows)==1, 'unique workbook custody required'); w=rows[0]
    require(w['source_sha256']==SOURCE and w['version']==VERSION, 'source/version custody differs')
    require(not any(o['state']=='hold' or o['state']=='active' and (o['kind']=='publication' or o['workbook']=='0077')
                    for o in status['operations']), 'uncertain or target operation requires reconciliation')
    for op in status['operations']:
        if op['state']=='active':
            owners=[x for x in status['workbooks'] if x['workbook']==op['workbook']];require(len(owners)==1,'active other model owner missing');other=owners[0]
            require(op['kind']=='model' and other['state']=='active' and other['owner']==op['owner'] and other['fence']==op['fence'] and other['expires']==op['expires'] and other['expires']>status['at'] and op['ended'] is None,'uncertain other operation owner')
    if active:
        require(w['state']=='active' and w['owner']==OWNER and w['fence']==4
                and os.environ.get('HARBOR_FLEET_OWNER')==OWNER and os.environ.get('HARBOR_FLEET_FENCE')=='4', 'new exclusive claim required')
        c.call('heartbeat',workbook='0077',owner=OWNER,fence=4,token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
    else:
        require(w==read(p['stopped_central'])['workbook'] and w['state']=='hold' and w['fence']==3 and w['owner']=='hhu-server-6:slot-2:0077-normalization-003', 'stopped original claim required')
    return w

def replacement_receipt(p,T,s,row):
    result=read(row)
    require(set(result)=={'status','plan','intent','takeover','tracker','replacement_count','maximum_replacements',
                         'models_dispatched','semantic_budget_reset','automatic_retry'}
            and result['status']=='INITIAL_CUSTODY_REPLACED' and read(result['plan'])==p
            and result['replacement_count']==2 and result['maximum_replacements']==3
            and result['models_dispatched']==0 and result['semantic_budget_reset'] is False
            and result['automatic_retry'] is False, 'exact completed original custody receipt required')
    require(read(result['intent'])==dict(plan=result['plan'],tracker=s.tracker,automatic_retry=False), 'takeover intent detached')
    before,after,lease=T.validate_completion(result['takeover'])
    stop=read(read(result['takeover'])['stopped_owner_proof'])
    require(T._selection(stop['selection'])==s and T.life.validate_lifecycle(before)==1 and T.life.validate_lifecycle(after)==2
            and result['tracker']['sha256']==lease.sha256 and after['revision']==lease.revision, 'actual original takeover2 required')
    return result,after,lease

def stopped(p):
    proof=read(p['stopped_process']);require(proof=={'process':{'state':'ABSENT','pid':473317},'interrupt_intent_exists':False,'automatic_resume':False},'selected stopped original process differs')
    import subprocess
    code="from pathlib import Path\nimport json\nowner='hhu-server-6:slot-2:0077-normalization-003'\nhits=[]\nfor proc in Path('/proc').iterdir():\n if not proc.name.isdigit():continue\n try:\n  if (proc/'stat').read_text().rsplit(')',1)[1].split()[0] in ('Z','X'):continue\n  fields=[x.partition(b'=')[2].decode(errors='replace') for x in (proc/'environ').read_bytes().split(bytes([0])) if x.startswith(b'HARBOR_FLEET_OWNER=')]\n  if owner in fields:hits.append(int(proc.name))\n except (FileNotFoundError,ProcessLookupError):pass\nprint(json.dumps(hits))\n"
    observed=subprocess.run(['/usr/bin/sudo','-n','/usr/bin/python3','-I','-B','-c',code],capture_output=True,timeout=15)
    require(observed.returncode==0 and json.loads(observed.stdout)==[],'live or unreadable prior owner process')


def probe(row):
    p,M,env,A,dispatcher,old,T,s,native,policy=load(row);custody(p,policy);stopped(p)
    before,lease,run,evidence=T._recipe(s);T._prior_chain(s,before)
    require(T.life.validate_lifecycle(before)==1 and lease['expires_at']<=time.time_ns(),'actual expired T1 custody required')
    with T._stopped_locks(s):require(binding(s.tracker['path'])==s.tracker,'native stopped tracker differs')
    require(not Path(p['takeover_home']).exists() and not Path(p['takeover_directory']).exists() and not Path(p['home']).exists() and not Path(p['operations']).exists() and not (Path(env[5]['run_root'])/'instruction').exists(),'initial takeover or authoring already attempted')
    return dict(status='ACTUAL_EXPIRED_T1_INITIAL_CONTINUATION_READY_NOT_STARTED',plan=row,tracker=s.tracker,revision=before['revision'],replacement_count=1,max_replacements=3,duration_seconds=7200,models=0,claims=0,lease_mutations=0)


def replace_once(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot replace')
    require(os.environ.get('HARBOR_INITIAL_0077_ROOT')=='0077','explicit root takeover execution required')
    p,M,env,A,dispatcher,old,T,s,native,policy=load(row); custody(p,policy)
    stopped(p)
    before,lease,run,evidence=T._recipe(s);T._prior_chain(s,before)
    require(lease['expires_at']<=time.time_ns() and T.life.validate_lifecycle(before)==1,'must actually expire before original takeover2')
    home=Path(p['takeover_home']); require(not home.exists() and not Path(p['takeover_directory']).exists(),'takeover already attempted; reconcile')
    require(not (Path(env[5]['run_root'])/'instruction').exists(),'instruction already entered')
    with T._stopped_locks(s): require(binding(s.tracker['path'])==s.tracker,'tracker changed')
    home.mkdir(); intent=A.retain(home/'intent.json',dict(plan=row,tracker=s.tracker,automatic_retry=False))
    lease,complete=T.replace_expired(s,Path(p['takeover_directory']))
    before,after,current=T.validate_completion(complete);require(current==lease and T.life.validate_lifecycle(after)==2,'original takeover return differs')
    return A.retain(home/'complete.json',dict(status='INITIAL_CUSTODY_REPLACED',plan=row,intent=intent,takeover=complete,
        tracker=dict(path=s.tracker['path'],sha256=lease.sha256),replacement_count=2,maximum_replacements=3,
        models_dispatched=0,semantic_budget_reset=False,automatic_retry=False))

def evidence(p): return [p[k] for k in ('old_driver','old_plan','recipe','takeover','capacity','model_gate','stdlib','helper','stopped_central','stopped_process')]

def selected_dispatcher(M,dispatcher,env,A,row,p):
    f=M.selected_dispatcher; g={**f.__globals__,'__file__':__file__,'evidence':evidence}
    selected,se=FunctionType(f.__code__,g,f.__name__,f.__defaults__,f.__closure__)(dispatcher,env,A,row,p)
    original=selected.successful
    def successful(outcome,current=None):
        value=read(outcome)
        if value.get('schema_version')==HANDOFF_SCHEMA:return validate_handoff(row,outcome,current or se)
        return original(outcome,current)
    selected.successful=successful
    return selected,se


def owning_child(row,kind,intent):
    p,M,env,A,dispatcher,old,T,s,native,policy=load(row);custody(p,policy,active=True)
    producer={'environment':env[0],'instruction':env[1],'renewal':dispatcher}[kind]
    A.validate_child(producer,base=env[0],kind=kind,reader_plan=row,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p))
    return producer.renewal_child(intent) if kind=='renewal' else producer.child(intent)

HANDOFF_SCHEMA='fleet-0077-initial-custody-handoff/v1'

def initial_values(row,env,T,s,native,p):
    base,C,_,W,_,config,root,controls=env
    receipt=binding(Path(p['takeover_home'])/'complete.json')
    result,after,lease=replacement_receipt(p,T,s,receipt)
    require(result['plan']==row and binding(s.tracker['path'])==result['tracker'],'actual original T2 tracker differs')
    old=read(native['initial_checkpoint']);prior,selection,oldlease=C._prior(native['initial_checkpoint'],old['descriptor'])
    worker,expected,recipe=C.selected(old['descriptor'])
    require(expected==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction','retained initial worker differs')
    require(prior['status']=='INSTRUCTION_READY' and prior['checkpoint'] is None and prior['next_step']==W.STEPS[0]=='disclosure-build','first instruction boundary changed')
    require(all(getattr(worker,k,None) is None for k in ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair','lease_renewal','disclosure_revision','dialogue_pre_review_repair')),'initial worker acquired unselected repair history')
    require(T.life.validate_lifecycle(after)==2 and after['leases']['task_agent']['expires_at']-time.time_ns()>3330*10**9,'original step runway unavailable')
    W.active(worker,selection,lease)
    return receipt,result,after,lease,old,worker,recipe,selection

def initial_intent(row,p):
    return dict(schema_version=HANDOFF_SCHEMA,plan=row,helper=p['helper'],takeover=binding(Path(p['takeover_home'])/'complete.json'),
                seconds=900,memory_bytes=8589934592,automatic_retry=False,models=0)

def handoff_child(row,intent):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot write initial handoff')
    p,M,env,A,dispatcher,old_job,T,s,native,policy=load(row);custody(p,policy,active=True)
    value=read(intent);require(value==initial_intent(row,p),'initial handoff guard intent differs')
    directory=Path(p['home'])/'initial-handoff';require(Path(intent['path'])==directory/'intent.json','handoff guard namespace differs')
    receipt,result,after,lease,old,worker,recipe,selection=initial_values(row,env,T,s,native,p)
    require(not worker.run.exists(),'original instruction already started')
    base,C,_,W,_,config,root,controls=env
    # Original dependency validator runs; no environment/start/model operation.
    dep=C.dependency(recipe,directory)
    previous=read(old['descriptor'])
    origin=[*previous['origin'],native['initial_checkpoint'],native['renewal_hold'],receipt,result['takeover'],intent,dep,*evidence(p)]
    descriptor=A.retain(directory/'descriptor.json',dict(previous,origin=origin,initial=dict(selection=old['selection'],lease=base.encode(lease))))
    current=A.retain(directory/'result.json',C._result('INSTRUCTION_READY',descriptor,selection,lease,evidence=origin,next_step=W.STEPS[0]))
    C.selected(descriptor);C._prior(current,descriptor)
    require(binding(s.tracker['path'])==result['tracker'] and not worker.run.exists(),'handoff changed owning state')
    return current

def validate_handoff(row,outcome,env):
    p=read(row);value=read(outcome);directory=Path(p['home'])/'initial-handoff'
    require(Path(outcome['path'])==directory/'outcome.json' and set(value)=={'schema_version','status','intent','resource','result','automatic_retry'},'closed initial handoff outcome required')
    require(value['schema_version']==HANDOFF_SCHEMA and value['status']=='INITIAL_HANDOFF_READY' and value['automatic_retry'] is False,'initial handoff did not complete')
    require(value['intent']==binding(directory/'intent.json') and read(value['intent'])==initial_intent(row,p),'initial handoff intent changed')
    resource=read(value['resource']);require(value['resource']==binding(directory/'resource.json') and resource['intent']==value['intent'] and resource['result']==value['result']
        and resource['returncode']==0 and resource['failure'] is None and resource['seconds']==900 and resource['memory_bytes']==8589934592 and resource['log']==binding(directory/'step.log'),'initial handoff guard differs')
    command=[sys.executable,'-I','-B',p['helper']['path'],'handoff-child','--plan',row['path'],'--plan-sha256',row['sha256'],'--intent',value['intent']['path'],'--intent-sha256',value['intent']['sha256']]
    require(resource['command']==command,'initial handoff command differs')
    current=read(value['result']);require(value['result']==binding(directory/'result.json') and current['descriptor']==binding(directory/'descriptor.json'),'initial handoff result detached')
    base,C,_,W,_,config,root,controls=env
    # Full original T validation remains mandatory in every parent/child reader.
    T=sys.modules['scripts.batch.harbor_lease_takeover'];native=json.loads(read(p['recipe'])['log']);s=T._selection(native['selection'])
    receipt,result,after,lease,old,worker,recipe,selection=initial_values(row,env,T,s,native,p)
    expected_origin=[*read(old['descriptor'])['origin'],native['initial_checkpoint'],native['renewal_hold'],receipt,result['takeover'],value['intent'],binding(directory/'dependency.json'),*evidence(p)]
    desc=read(current['descriptor']);old_desc=read(old['descriptor'])
    require(desc==dict(old_desc,origin=expected_origin,initial=dict(selection=old['selection'],lease=base.encode(lease))),'initial descriptor differs beyond declared custody')
    require(current==C._result('INSTRUCTION_READY',current['descriptor'],selection,lease,evidence=expected_origin,next_step=W.STEPS[0]),'initial handoff changed original selection/phase/history')
    C.selected(current['descriptor']);C._prior(value['result'],current['descriptor'])
    return value['result'],current

def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot author')
    p,M,env,A,dispatcher,old,T,s,native,policy=load(row);base,C,P,W,R,config,root,controls=env
    actual=initial_values(row,env,T,s,native,p)
    require(actual[2]['leases']['task_agent']['expires_at']-time.time_ns()>4500*10**9,'initial handoff plus original step runway unavailable')
    require(not Path(p['home']).exists() and not Path(p['operations']).exists() and not (Path(config['run_root'])/'instruction').exists(),'remaining namespace consumed')
    custody(p,policy,active=True);selected,se=selected_dispatcher(M,dispatcher,env,A,row,p)
    home=Path(p['home']);home.mkdir();A.retain(home/'intent.json',dict(plan=row,takeover=binding(Path(p['takeover_home'])/'complete.json'),automatic_retry=False))
    directory=home/'initial-handoff';directory.mkdir();intent=A.retain(directory/'intent.json',initial_intent(row,p))
    command=[sys.executable,'-I','-B',p['helper']['path'],'handoff-child','--plan',row['path'],'--plan-sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
    rc=None;failure=None;metrics={}
    try:
        with selected.fleet_slot('0077') as fds:
            with (directory/'step.log').open('xb') as log:
                try:rc=base._run_in_process_group(command,stdout=log,timeout_seconds=900,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=root,guardian_lock_fds=fds)
                except BaseException as exc:failure=type(exc).__name__
        current=binding(directory/'result.json') if (directory/'result.json').is_file() else None
        resource=A.retain(directory/'resource.json',dict(intent=intent,command=command,seconds=900,memory_bytes=8589934592,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),result=current))
        outcome=A.retain(directory/'outcome.json',dict(schema_version=HANDOFF_SCHEMA,status='INITIAL_HANDOFF_READY' if rc==0 and failure is None else 'RECONCILIATION_REQUIRED',intent=intent,resource=resource,result=current,automatic_retry=False))
        selected.successful(outcome,se)
        job=A.retain(home/'authoring-job.json',dict(old,outcome=outcome,operations=p['operations']));selected.run(job)
        held=read(binding(Path(p['operations'])/'hold.json'));checkpoint,value=selected.successful(held['outcome'],se)
        require(value['status']=='PUBLICATION_READY','remaining original authoring held')
        result=dict(status='PUBLICATION_READY',workbook='0077',plan=row,job=job,config=old['config'],checkpoint=checkpoint,outcome=held['outcome'],publication_performed=False,automatic_retry=False)
    except Exception as exc:
        A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0077',plan=row,failure_type=type(exc).__name__,publication_performed=False,automatic_retry=False));raise
    return A.retain(home/'result.json',result)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','replace','run','owning-child','handoff-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True)
    a.add_argument('--kind',choices=['environment','instruction','renewal']);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();r=dict(path=v.plan,sha256=v.plan_sha256)
    result=owning_child(r,v.kind,dict(path=v.intent,sha256=v.intent_sha256)) if v.action=='owning-child' else handoff_child(r,dict(path=v.intent,sha256=v.intent_sha256)) if v.action=='handoff-child' else {'probe':probe,'replace':replace_once,'run':run}[v.action](r)
    print(json.dumps(result))
