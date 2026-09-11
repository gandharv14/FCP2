"""Completed-build code upgrade and bounded, separately journalled review round.

Every physical review remains a real Call. This module never retries an uncertain
request and never treats its aggregate as another model response.
"""
from pathlib import Path
from dataclasses import asdict,replace
import json
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_instruction_worker as W
from scripts.batch import harbor_transition_codec as K
from scripts.batch import harbor_tracker_lifecycle as life
from scripts.batch.harbor_nat_retry import _renewal_chain
from scripts.batch.harbor_workbook_bootstrap import Lease

SCHEMA='harbor-disclosure-partition-continuation/v1'
SELF='scripts/batch/harbor_disclosure_partition_continuation.py'
ALLOWED={SELF,'scripts/batch/harbor_disclosure_partition_review.py',
 'scripts/batch/harbor_disclosure_partition_evidence.py','scripts/batch/harbor_instruction_worker.py',
 'scripts/harbor_instruction_campaign.py','scripts/batch/harbor_instruction_local_command.py',
 'scripts/batch/harbor_instruction_local_restart.py','scripts/batch/harbor_grouped_plain_qualification.py',
 'scripts/batch/harbor_complete_readiness.py','scripts/batch/harbor_transition_codec.py',
 'scripts/batch/harbor_semantic_readiness.py'}


def marker(worker):return worker.run/'disclosure-partition-continuation.json'
def exists(worker):return marker(worker).is_file()
def home(worker):return worker.run/'disclosure-partition-round-01'

def inventory(root):
    from scripts.batch.harbor_instruction_local_restart import tree
    return {k:v for k,v in tree(root).items() if '__pycache__' not in Path(k).parts}

def profile(old_snapshot,root):
    from scripts.batch.harbor_instruction_local_restart import snapshot
    old,files=snapshot(old_snapshot);now=inventory(root)
    require(set(files)<=set(now),'partition successor removed historical code')
    require({k for k,v in now.items() if files.get(k)!=v}<=ALLOWED,'partition successor changed unrelated code')
    require(record(Path(root)/SELF)==record(__file__),'partition authority differs from installed code')
    return old,now

def historical_worker_code(old_root):
    files=inventory(old_root)
    return {str(Path(old_root)/k):v for k,v in files.items()
        if (k.startswith('scripts/batch/harbor_') and k.endswith('.py')) or k in
        {'scripts/batch/hardened_runner.py','scripts/batch/process_guardian.py',
         'scripts/harbor_pretransport_recovery.py','scripts/harbor_instruction_code_applicability.py'}}

def permit(worker):
    m=read(record(marker(worker)))
    require(set(m)=={'schema_version','permit'} and m['schema_version']==SCHEMA,'closed partition reservation required')
    binding=m['permit'];p=read(binding)
    require(binding==record(home(worker)/'permit.json') and p['schema_version']==SCHEMA,
            'foreign partition reservation')
    require(p['worker_run']==str(worker.run) and p['root']==str(worker.root)
            and p['implementation']==record(__file__) and p['global_round']==p['global_limit']==1,
            'partition identity, code, or round changed')
    require(p['initial']==record(worker.run/'initial.json'),'partition initialization changed')
    _,now=profile(p['old_snapshot'],worker.root)
    require(now==p['current_files'],'partition frozen code changed')
    for b in p['evidence']:life.checked_record(b)
    return binding,p

def validate_code_successor(worker,prior_command):
    """Nonrecursive anchor used by current readers of older local receipts."""
    binding,p=permit(worker)
    require(read(p['local_command'])==prior_command,'partition detached original command continuation')
    return p

def validate(worker,before=None):
    binding,p=permit(worker)
    from scripts.batch import harbor_instruction_local_command as M
    old=M.validate_historical(worker,before,p['old_snapshot'])
    require(read(p['local_command'])==old,'partition local command history changed')
    if before is not None:
        require(before['workbook_id']==p['workbook'] and before['bindings']['original_raw_source']==p['original_source'],
                'partition source or workbook changed')
        require(before['bindings']['source_file']==p['source'],'partition selected source changed')
    return p

def validate_lease(worker,lease):
    _,before,tracker=W.active(worker,K.decode(read(read(record(marker(worker)))['permit'])['selection']),lease)
    p=validate(worker,before)
    _renewal_chain(p['tracker'],p['lease'],before,lease)
    return p,before,tracker

def authorize(worker,selection,lease,*,old_snapshot,old_descriptor,old_result,lease_origin=None):
    """Reserve once after a successful canonical disclosure-build checkpoint."""
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_local_command as M
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch import harbor_semantic_readiness as S,harbor_disclosure_partition_evidence as E
    from scripts.batch.harbor_runtime_readiness import delivered_workbook
    require(not exists(worker) and not home(worker).exists(),'partition round already reserved or prepared; reconcile retained evidence')
    d=read(old_descriptor);prior=read(old_result)
    require(prior['status']=='STEP_COMPLETE' and prior['next_step']=='disclosure-review'
            and prior['descriptor']==old_descriptor,'exact completed disclosure-build result required')
    old_worker=C.worker_decode(d['worker']);old_root,current=profile(old_snapshot,worker.root)
    require(old_worker.root==old_root and {**C.worker_encode(worker),'root':str(old_root)}==C.worker_encode(old_worker),
            'partition upgrade changed worker or prior continuations')
    require(prior['selection']==K.encode(selection),'partition predecessor selection changed')
    original_lease=C.base.decode(prior['lease'])
    checkpoint=prior['checkpoint'];saved=read(checkpoint);intent=read(saved['intent'])
    require(saved['status']=='completed' and saved['step']=='disclosure-build'
            and saved['selection']==K.encode(selection) and saved['lease']==asdict(original_lease),
            'partition predecessor is not the completed build')
    require(intent['initial']==record(worker.run/'initial.json'),'partition build initialization differs')
    require(intent['code']==historical_worker_code(old_root),'partition build implementation differs from snapshot')
    require(not (worker.run/'steps/disclosure-review').exists() and not (worker.run/'producer/requests').exists(),
            'a review or other instruction model request has already started')
    _,before,tracker=W.active(worker,selection,lease)
    if lease_origin is None:
        require(lease==original_lease,'partition renewal origin required')
        lease_origin=tracker
    _renewal_chain(lease_origin,asdict(original_lease),before,lease)
    old=M.validate_historical(worker,before,old_snapshot)
    require(checkpoint==record(Path(read(record(M.marker(worker)))['permit']['path']).parent/'steps/disclosure-build/complete.json'),
            'partition selected another build completion')
    for lane in (W.producer.DISCLOSURE,W.producer.NAT,W.producer.DIALOGUE):
        require(before['lane_state'][lane]['attempt']==0,'partition would reset an instruction budget')
    proof=prefix(before,selection,worker.context,stop='disclosure')
    stage=selection.content.stage;source=before['bindings']['source_file'];golden={k:source[k] for k in ('path','sha256')}
    instruction=record(stage/'instruction.md');disclosure=record(stage/'tests/disclosure.json');delivered=record(delivered_workbook(stage,before['workbook_id']))
    renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose')
    faith=renderer.faithcheck_task(stage,worker.run/'producer/disclosure-build',golden['path'])
    verified=renderer.verify_task(stage)
    require(faith['passed'] and faith['faults']==[] and verified['passed'] and verified['faults']==[],
            'partition source disclosure does not pass current mechanical checks')
    manifest=S.disclosure_manifest(instruction,disclosure,golden,delivered,root=worker.root)
    body={'manifest':manifest,'manifest_sha256':W.executor.sha(W.executor.canonical(manifest)),'instruction':Path(instruction['path']).read_text(),
          'disclosure':read(disclosure),'workbooks':{'golden':S.workbook_evidence(golden),'delivered':S.workbook_evidence(delivered)}}
    projected=E.plan(body,Path(golden['path']),Path(delivered['path']),root=worker.root)
    directory=home(worker);directory.mkdir()
    manifest_binding=W.keep(directory/'manifest.json',manifest)
    require(body['manifest_sha256']==manifest_binding['sha256'],'manifest encoding changed')
    frozen_instruction=W.snapshot(directory/'instruction.md',instruction)
    full_body=W.keep(directory/'full-body.json',body)
    projection=W.keep(directory/'projection.json',projected)
    plan=W.keep(directory/'review-plan.json',dict(schema_version='harbor-disclosure-partition-review-plan/v1',
        full_body=full_body,projection=projection,manifest=manifest_binding,instruction=frozen_instruction,
        disclosure=disclosure,golden=golden,delivered=delivered,workbook=before['workbook_id'],
        original_source=before['bindings']['original_raw_source'],policy=asdict(selection.content.model_policy),
        worker_run=str(worker.run),initial=record(worker.run/'initial.json')))
    tracker_copy=W.snapshot(directory/'tracker-before.json',tracker)
    local_command=read(record(M.marker(worker)))['permit']
    evidence=[old_snapshot,old_descriptor,old_result,checkpoint,saved['intent'],local_command,full_body,projection,
              plan,manifest_binding,frozen_instruction,disclosure,golden,delivered,tracker_copy,lease_origin]
    p=dict(schema_version=SCHEMA,worker_run=str(worker.run),root=str(worker.root),implementation=record(__file__),
        initial=record(worker.run/'initial.json'),old_snapshot=old_snapshot,current_files=current,
        workbook=before['workbook_id'],original_source=before['bindings']['original_raw_source'],source=source,
        global_round=1,global_limit=1,review_plan=plan,local_command=local_command,predecessor=checkpoint,
        selection=K.encode(selection),lease=asdict(lease),tracker=tracker_copy,evidence=evidence,prefix=proof,
        campaign=C.worker_encode(worker)['campaign'],context=C.worker_encode(worker)['context'])
    binding=W.keep(directory/'permit.json',p)
    _,latest,_=W.active(worker,selection,lease);require(latest==before,'tracker changed during partition reservation')
    from scripts.batch.harbor_instruction_local_restart import exclusive
    exclusive(marker(worker),dict(schema_version=SCHEMA,permit=binding))
    validate(worker,before)
    return binding


def completed_calls(worker,*,count=None):
    """Validate an ordered sealed prefix; any unsealed next directory is a hold."""
    from scripts.batch import harbor_disclosure_partition_review as R
    from scripts.batch.harbor_model_review_provenance import Call
    _,p=permit(worker);plan=read(p['review_plan'])
    policy=W.producer.Policy(**plan['policy'])
    validated=R.validate_plan(p['review_plan'],policy=policy,root=worker.root)
    projection=validated[1]
    total=len(projection['batches']);directory=home(worker)/'batches'
    names=sorted(x.name for x in directory.iterdir()) if directory.exists() else []
    require(names==['%04d'%i for i in range(len(names))],'partition review has noncontiguous or foreign batch directories')
    require(len(names)<=total,'too many partition calls')
    calls=[]
    for index,name in enumerate(names):
        path=directory/name/'complete.json';require(path.is_file(),'unsealed partition requires explicit reconciliation, not replay')
        value=read(record(path));require(value['index']==index and value['plan']==p['review_plan'],'batch completion belongs to another plan')
        call=Call(**value['call']);R.validate_batch(call,p['review_plan'],index,policy=policy,root=worker.root,_validated=validated)
        calls.append(call)
    if count is not None:require(len(calls)==count,'partition completed boundary changed')
    return calls,total


def renew_boundary(worker,lease,directory,*,allow_complete=False):
    """Only a wholly sealed prior batch prefix can acquire another one-hour lease."""
    p,before,tracker=validate_lease(worker,lease);calls,total=completed_calls(worker)
    require((len(calls)==total if allow_complete else len(calls)<total)
            and not (home(worker)/'complete.json').exists(),'partition renewal boundary differs')
    from scripts import harbor_campaign as C
    before_binding=W.snapshot(Path(directory)/'lease-before.json',tracker)
    path=Path(tracker['path'])
    result=life.lease_operation(path,authority=C._authorize(path,lease.owner,lease.revision,lease.token),
        operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+p['workbook'],token=lease.token,
        duration_ns=C.DURATION,expected_revision=lease.revision,expected_sha256=lease.sha256)
    current=replace(lease,revision=result['revision'],sha256=result['sha256'])
    _,after,current_tracker=W.active(worker,K.decode(p['selection']),current)
    _renewal_chain(p['tracker'],p['lease'],after,current)
    receipt=W.keep(Path(directory)/'lease-renewal.json',dict(schema_version=SCHEMA,permit=read(record(marker(worker)))['permit'],
        completed_batches=len(calls),before=before_binding,after=W.snapshot(Path(directory)/'lease-after.json',current_tracker),
        previous=asdict(lease),lease=asdict(current),result=result))
    return current,receipt


def execute_batch(worker,lease,index,*,credential,directory):
    """Called only inside the owning outer book/process/model guards."""
    from scripts.batch import harbor_disclosure_partition_review as R
    p,_,_=validate_lease(worker,lease);calls,total=completed_calls(worker,count=index)
    require(index<total,'all partition work already completed')
    lease,renewal=renew_boundary(worker,lease,directory)
    p,before,tracker=validate_lease(worker,lease)
    import time
    require(before['leases']['task_agent']['expires_at']>time.time_ns()+3330*10**9,'partition lease does not cover guard')
    out=home(worker)/'batches'/('%04d'%index);require(not out.exists(),'partition already attempted; no replay')
    out.mkdir(parents=True)
    task=W.task(worker,K.decode(p['selection']),tracker)
    request=R.prepare(task,p['review_plan'],index)
    W.request_saved(out/'request.json',request)
    outcome=W.executor.execute(request.prepared,worker.campaign,credential=credential)
    require(outcome.status=='completed' and outcome.call is not None,'partition model request incomplete or rejected; explicit reconciliation required')
    checked=R.validate_batch(outcome.call,p['review_plan'],index,policy=task.policy,root=worker.root)
    result=W.keep(out/'complete.json',dict(schema_version=SCHEMA,plan=p['review_plan'],index=index,
        call=asdict(outcome.call),review=checked,lease=asdict(lease),renewal=renewal))
    return result,lease


def finalize(worker,lease):
    from scripts.batch import harbor_disclosure_partition_review as R
    p,_,_=validate_lease(worker,lease);calls,total=completed_calls(worker)
    require(len(calls)==total and total>0,'partition aggregate lacks complete review coverage')
    lease,renewal=renew_boundary(worker,lease,home(worker)/'final-lease',allow_complete=True)
    plan=read(p['review_plan']);policy=W.producer.Policy(**plan['policy'])
    value=R.aggregate(p['review_plan'],calls,policy=policy,root=worker.root)
    aggregate=W.keep(home(worker)/'aggregate.json',value)
    R.validate_aggregate(aggregate,plan['manifest'],instruction=plan['instruction'],disclosure=plan['disclosure'],
        golden=plan['golden'],delivered=plan['delivered'],policy=policy,root=worker.root)
    return W.keep(home(worker)/'complete.json',dict(schema_version=SCHEMA,permit=read(record(marker(worker)))['permit'],
        phase='disclosure-review',previous=p['predecessor'],selection=p['selection'],lease=asdict(lease),aggregate=aggregate,renewal=renewal))


def completion(worker):
    binding=record(home(worker)/'complete.json');c=read(binding);_,p=permit(worker)
    require(set(c)=={'schema_version','permit','phase','previous','selection','lease','aggregate','renewal'}
        and c['schema_version']==SCHEMA and c['permit']==read(record(marker(worker)))['permit']
        and c['phase']=='disclosure-review' and c['previous']==p['predecessor'] and c['selection']==p['selection'],
        'partition completion changed its owning boundary')
    renewal=read(c['renewal'])
    require(renewal.get('lease')==c['lease'],'partition final renewal differs')
    require(set(renewal)=={'schema_version','permit','completed_batches','before','after','previous','lease','result'},
            'partition final renewal fields differ')
    require(c['renewal']==record(home(worker)/'final-lease/lease-renewal.json')
            and renewal['schema_version']==SCHEMA and renewal['permit']==c['permit'],
            'partition final renewal ownership differs')
    calls,total=completed_calls(worker)
    require(len(calls)==total>0 and renewal['completed_batches']==total,'partition final renewal coverage differs')
    require(renewal['before']==record(home(worker)/'final-lease/lease-before.json')
            and renewal['after']==record(home(worker)/'final-lease/lease-after.json'),
            'partition final renewal tracker location differs')
    after=read(renewal['after']);current=Lease(**renewal['lease'])
    _renewal_chain(renewal['before'],renewal['previous'],after,current)
    require(current.revision==renewal['previous']['revision']+1
            and renewal['result']['revision']==current.revision
            and renewal['result']['sha256']==current.sha256,'partition final renewal result differs')
    return binding,c


def consume(worker,selection,lease):
    from scripts.batch import harbor_disclosure_partition_review as R
    from scripts.batch.harbor_complete_readiness import SemanticSelection
    p,_,_=validate_lease(worker,lease);binding,c=completion(worker)
    require(c['lease']==asdict(lease) and c['selection']==K.encode(selection),'partition completion lease or selection changed')
    plan=read(p['review_plan'])
    R.validate_aggregate(c['aggregate'],plan['manifest'],instruction=plan['instruction'],disclosure=plan['disclosure'],
        golden=plan['golden'],delivered=plan['delivered'],policy=selection.content.model_policy,root=worker.root)
    semantic=SemanticSelection(None,plan['manifest'],plan['instruction'],None,None,(),disclosure_partition_aggregate=c['aggregate'])
    return replace(selection,semantic=semantic),lease,None
