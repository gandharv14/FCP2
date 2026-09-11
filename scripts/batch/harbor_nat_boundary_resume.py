"""Root CAS continuation from one proved second-writer assembly defect to review.

No writer retry, call, semantic approval or generic phase skipping is provided.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy,fcntl
from scripts.batch import harbor_tracker as schema,harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_nat_retry as prior
from scripts.batch import harbor_nat_boundary_recovery as boundary
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_workbook_bootstrap import Lease
SCHEMA='harbor-nat-boundary-resume/v1'


def _worker():
    from scripts.batch import harbor_instruction_worker
    return harbor_instruction_worker


def _history(worker,before):
    require(worker.retry is not None,'original root-authorized second writer required')
    complete=read(worker.retry);p=read(complete['permit']);old=read(p['before']);after=read(complete['after'])
    require(complete['schema_version']==p['schema_version']==prior.SCHEMA,'unknown prior NAT continuation')
    expected=prior._project(old,complete['permit']);expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(expected==after and dict(schema_version=prior.SCHEMA,permit=complete['permit']) in before['handoffs'],'prior NAT CAS changed')
    require(complete['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),'prior NAT lease evidence changed')
    records=[worker.retry,complete['permit'],p['before'],complete['after'],p['initial'],p['blocked'],*p['prefix']]
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['original']['sha256']==row['retained']['sha256'],'prior NAT archive altered');records.append(row['retained'])
    return records


def _failure(worker,selected,lease,blocked,takeover=None):
    w=_worker();require(worker.boundary_recovery is None and worker.dialogue_retry is None,'another continuation cannot authorize boundary recovery')
    _,before,tracker=w.active(worker,selected,lease)
    require(not any(x.get('schema_version')==SCHEMA for x in before['handoffs']),'boundary continuation already authorized')
    step=w.step_directory(worker,'nat-writer');require(step.name=='round-02' and blocked==record(step/'blocked.json'),'exact completed second-writer failure required')
    old=read(blocked);require(old['status']=='NAT_REPAIR_REQUIRED' and old['step']=='nat-writer' and not (step/'exception.json').exists(),'unknown writer failure is not recoverable')
    from scripts.batch.harbor_continuation_audit import validate_selection
    validate_selection(w.refreshed(codec.decode(old['selection'])),selected)
    request=w.request_saved(step/'request.json');context=read(request.context)
    from scripts.batch.harbor_lease_takeover import continuation_chain
    continuation_chain(context['budget_evidence'][0],old['lease'],before,lease,takeover=takeover,tracker_path=tracker['path'])
    n=selected.content.naturalization_root;api=producer.skill_module(worker.root,'naturalize-finance-task-instruction','naturalize_recovery');state=api._read_state(n)
    receipt=boundary.validate(n,state,api=api)
    require(receipt is not None and state['status']=='validated' and not state.get('semantic_reviews') and not state.get('accepted_semantic_review'),'unreviewed current boundary proof required')
    require(receipt['writer']==boundary.request_value(request) and receipt['campaign']==boundary.campaign_value(worker.campaign),'boundary proof refers to a different invocation')
    require(selected.content.naturalization_writer_call==producer.observed(request,worker.campaign) and selected.content.naturalization_writer_request==request.prepared.request,'selected original writer provenance differs')
    for name in w.STEPS[w.STEPS.index('nat-review'):]:require(not (worker.run/'steps'/name).exists(),'later step already attempted')
    initial=record(worker.run/'initial.json');previous=None;records=[tracker,blocked,old['intent'],record(step/'request.json'),initial]
    for name in prior.PREFIX:
        b=record(w.step_directory(worker,name)/'complete.json');v=read(b);intent=read(v['intent'])
        require(v['status']=='completed' and v['step']==name and intent['initial']==initial,'initial completed prefix changed')
        current=intent['previous'];seen=set()
        while current!=previous:
            require(current is not None and current['path'] not in seen,'broken completed prefix chain');seen.add(current['path'])
            pending=read(current);require(pending['status'] in ('COMMANDS_REQUIRED','MODEL_REQUIRED','RUNTIME_REQUIRED'),'unexpected prefix predecessor');current=read(pending['intent'])['previous']
        previous=b;records += [b,v['intent']]
    intent=read(old['intent']);require(intent['initial']==initial and intent['step']=='nat-writer','failed writer initial changed')
    current=intent['previous'];seen=set()
    while current!=worker.retry:
        require(current is not None and current['path'] not in seen,'second writer lacks prior root permit');seen.add(current['path'])
        pending=read(current);require(pending['status']=='MODEL_REQUIRED','unknown second-writer predecessor');current=read(pending['intent'])['previous']
    records+=_history(worker,before)
    if takeover is not None:
        from scripts.batch.harbor_lease_takeover import completion_records
        records+=completion_records(takeover)
    for path in sorted((n/boundary.DIRECTORY).iterdir()):records.append(record(path))
    return before,tracker,receipt,records


def _project(before,permit):
    after=copy.deepcopy(before);after['handoffs'].append(dict(schema_version=SCHEMA,permit=permit))
    after['lane_state'][producer.NAT].update(state='repairing',attempt=2)
    return after


def _seal(worker,permit,after):
    p=read(permit);old=read(p['before']);expected=_project(old,permit)
    expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected,'boundary CAS differs from exact projection')
    directory=Path(permit['path']).parent;bound=producer.put(directory/'after-tracker.json',after)
    lease=Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))
    complete=producer.put(directory/'complete.json',dict(schema_version=SCHEMA,permit=permit,after=bound,lease=asdict(lease)))
    return replace(worker,boundary_recovery=complete),codec.decode(p['selection']),lease,complete


def authorize(worker,selected,lease,*,blocked,takeover=None,_fault=lambda _:None):
    w=_worker()
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before,tracker,receipt,records=_failure(worker,selected,lease,blocked,takeover)
        directory=worker.run/'boundary-resume-v1';require(not directory.exists(),'boundary transaction exists; explicit recovery required');directory.mkdir()
        code=w.code(worker);producer.put(directory/'intent.json',dict(blocked=blocked,tracker=tracker,code=code))
        records+=prior._fresh_prefix(before,selected,worker.context)
        from scripts.batch.harbor_complete_readiness import evidence_records
        records+=evidence_records(selected)
        for path in sorted((selected.content.naturalization_root).rglob('*')):
            schema.safe_path(path)
            if path.is_file() and not path.name.endswith('.lock'):records.append(record(path))
        copies=prior._archive(directory/'archive',records);saved=next(x['retained'] for x in copies if x['original']==tracker)
        require(w.code(worker)==code,'current implementation changed during prefix proof')
        permit=producer.put(directory/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),before=saved,selection=codec.encode(selected),lease=asdict(lease),code=code,copies=copies,blocked=blocked,initial=record(worker.run/'initial.json'),boundary_receipt=record(selected.content.naturalization_root/boundary.DIRECTORY/'receipt.json'),spent=2,remaining_writers=0,next_phase='nat-review',semantic_budget_reset=False,lease_takeover=takeover))
        _fault('prepared')
        def mutate(after):
            require(w.code(worker)==code,'current implementation changed before boundary CAS')
            fresh=_failure(worker,selected,lease,blocked,takeover);require(fresh[0]==before and fresh[2]==receipt,'boundary proof/context changed before CAS')
            life.check_records([x['original'] for x in copies]);projection=_project(before,permit)
            after['handoffs']=projection['handoffs'];after['lane_state']=projection['lane_state']
        result=schema.cas_mutate(tracker['path'],expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token,scope='workbook:'+selected.content.workbook,mutate=mutate,allowed_fields={'handoffs','lane_state'},allowed_lanes={producer.NAT},fault_hook=_fault)
        _fault('committed');return _seal(worker,permit,result['tracker'])


def recover(worker,permit,lease):
    w=_worker();p=read(permit)
    require(p['worker_run']==str(worker.run) and p['code']==w.code(worker) and p['lease']==asdict(lease),'boundary recovery identity/code changed')
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for row in p['copies']:life.checked_record(row['retained'])
        s=codec.decode(p['selection']);after=read(record(worker.context.tracker_root/(s.content.workbook+'.json')))
        require(after['revision']==lease.revision+1,'boundary CAS did not commit or advanced')
        w.active(worker,s,Lease(lease.owner,lease.token,after['revision'],schema.sha(life.canonical(after))))
        return _seal(worker,permit,after)


def validate(worker,before):
    w=_worker();complete=read(worker.boundary_recovery);p=read(complete['permit'])
    require(complete['schema_version']==p['schema_version']==SCHEMA and p['worker_run']==str(worker.run) and p['code']==w.code(worker),'boundary continuation identity/code changed')
    require(dict(schema_version=SCHEMA,permit=complete['permit']) in before['handoffs'],'boundary continuation lacks root CAS')
    after=read(complete['after']);old=read(p['before']);expected=_project(old,complete['permit'])
    expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected and before['revision']>=after['revision'],'boundary tracker projection changed')
    require(complete['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),'boundary lease record changed')
    if p.get('lease_takeover') is not None:
        from scripts.batch.harbor_lease_takeover import validate_completion
        validate_completion(p['lease_takeover'])
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['retained']['sha256']==row['original']['sha256'],'boundary archive changed')
    for b in (p['blocked'],p['initial'],p['boundary_receipt']):life.checked_record(b)
    s=codec.decode(p['selection']);n=s.content.naturalization_root;api=producer.skill_module(worker.root,'naturalize-finance-task-instruction','naturalize_recovery')
    boundary.validate(n,api._read_state(n),api=api)
    require(before['lane_state'][producer.NAT]['attempt']==2 and p['spent']==2 and p['remaining_writers']==0 and p['next_phase']=='nat-review','consumed writer budget changed')
    return {**p,'lease':complete['lease']}
