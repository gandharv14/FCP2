"""Root-owned continuation of one precisely observed dialogue structural fault.

Current fixed gates revalidate all completed lanes, including a root-captured
fresh runtime observation. No model, Docker, publication or evidence repair is
performed here. One structural scope is shared by writer and paraphraser.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy
import fcntl
import json
from scripts.batch import harbor_tracker as schema,harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_producer as producer,harbor_dialogue_authoring as author
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_completed_lanes import STEPS,CompletionEvidence,check_completed_gate
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch import harbor_transition_codec as codec
SCHEMA='harbor-dialogue-structural-retry/v1'
ERROR='dialogue structural failure; retain exact response and request bounded same-producer repair'


def _worker():
    from scripts.batch import harbor_instruction_worker
    return harbor_instruction_worker


def _prefix(before,selected,context):
    view=copy.deepcopy(before);evidence=[]
    for row in view['lane_state'].values():row.update(state='pending',phase=None)
    for kind,lane in STEPS:
        if kind=='dialogue':break
        if kind in ('source','segmentation'):
            g=selected.content.bindings[kind+'_generation']
            result=notes.check_gate(view,notes.GateEvidence(kind,record(Path(g['path'])/'generation-manifest.json'),selected.content.normalization_records),context)
        else:result=check_completed_gate(view,CompletionEvidence(kind,selected),context)
        require(result['passed'] and result.get('confidence','high')=='high','current owning prefix failed: '+kind)
        evidence+=result['evidence'];view['bindings'].update(result['bindings'])
        view['lane_state'][lane].update(state='running' if kind=='normalization_phase1' else 'passed',phase=result.get('phase'))
    # Runtime validation returns its evidence in the root-selected observation.
    from scripts.batch.harbor_complete_readiness import evidence_records
    return notes.evidence_list(evidence+evidence_records(selected))


def _history(worker,before):
    """Old NAT permit integrity, not an assertion that its code is current."""
    if worker.retry is None:return []
    from scripts.batch import harbor_nat_retry as nat
    completed=read(worker.retry);p=read(completed['permit']);old=read(p['before']);after=read(completed['after'])
    require(completed['schema_version']==p['schema_version']==nat.SCHEMA,'unknown prior NAT continuation')
    expected=nat._project(old,completed['permit']);expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(expected==after and dict(schema_version=nat.SCHEMA,permit=completed['permit']) in before['handoffs'],'prior NAT CAS changed')
    require(completed['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),'prior NAT lease evidence changed')
    records=[worker.retry,completed['permit'],p['before'],completed['after'],p['initial'],p['blocked'],*p['prefix']]
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['original']['sha256']==row['retained']['sha256'],'prior NAT archive altered');records.append(row['retained'])
    return records


def _failure(worker,selected,lease,checkpoint,phase,takeover=None):
    w=_worker();require(worker.dialogue_retry is None,'structural continuation already selected')
    require(phase in ('dialogue-writer','dialogue-paraphraser'),'unsupported structural owner')
    _,before,tracker=w.active(worker,selected,lease)
    require(not any(h.get('schema_version')==SCHEMA for h in before['handoffs']),'shared structural correction already authorized')
    previous=read(checkpoint);index=w.STEPS.index(phase)
    require(previous['status']=='completed' and previous['step']==w.STEPS[index-1] and checkpoint==record(w.step_directory(worker,w.STEPS[index-1])/'complete.json'),'exact completed preceding checkpoint required')
    old_selection=codec.decode(previous['selection'])
    from scripts.batch.harbor_continuation_audit import validate_selection
    validate_selection(replace(old_selection,runtime=selected.runtime,observation=selected.observation),selected)
    step=w.step_directory(worker,phase);intent=record(step/'intent.json');plan=read(intent)
    require(plan['step']==phase and plan['selection']==previous['selection'] and plan['lease']==previous['lease'],'failed step original context changed')
    request=w.request_saved(step/'request.json');context=read(request.context)
    from scripts.batch.harbor_lease_takeover import continuation_chain
    continuation_chain(context['budget_evidence'][0],previous['lease'],before,lease,takeover=takeover,tracker_path=tracker['path'])
    if (step/'exception.json').exists():
        error=record(step/'exception.json');value=read(error)
        require(value==dict(type='AdmissionError',message_sha256=schema.sha(ERROR.encode()),automatic_replay=False),'unknown exception cannot authorize a structural retry')
    else:
        error=record(step/'blocked.json');value=read(error)
        require(value['status']=='DIALOGUE_STRUCTURAL_REPAIR_REQUIRED' and value['step']==phase and value['intent']==intent,'unclassified failure cannot authorize a structural retry')
    require(not (step/'complete.json').exists(),'completed author cannot be retried')
    for name in w.STEPS[index+1:]:require(not (worker.run/'steps'/name).exists(),'later work already attempted')
    t=w.task(worker,selected,tracker);round=request.prepared.ordinal
    require(round==1 and context.get('structural_round') is None,'this continuation is only first-round structural correction')
    directory,claims,template,slots=producer._dialogue(t,round,worker.root)
    require(not (directory/'round-01/structural-repair.json').exists(),'structural repair already prepared')
    call=producer.observed(request,worker.campaign);incoming=None
    if phase=='dialogue-paraphraser':_,_,incoming,_=producer._accepted_author(t,directory,round,'writer',claims,template,slots,root=worker.root)
    checked,filled,faults,_=author.verify_author(call,policy=t.policy,kind=phase.removeprefix('dialogue-'),round=round,template=template,slots=slots,pack=claims['writer_pack'],incoming=incoming,root=worker.root)
    require(faults,'failure does not reproduce under owning validator')
    report=record(directory/'round-01'/('writer.fill.json' if phase=='dialogue-writer' else 'paraphraser.fill.json'))
    saved=read(report);require(saved['structure_faults']==faults,'retained structural diagnostic changed')
    if saved.get('input') is not None:
        require(producer.text(saved['input'])==filled,'retained filled body changed')
    require(saved['template']==record(directory/'draft_template.md'),'retained template differs')
    prefix=[]
    for name in w.STEPS[:index]:
        binding=record(w.step_directory(worker,name)/'complete.json');v=read(binding)
        require(v['status']=='completed' and v['step']==name and read(v['intent'])['initial']==record(worker.run/'initial.json'),'completed prefix identity changed')
        prefix.extend([binding,v['intent']])
    require(plan['initial']==record(worker.run/'initial.json'),'failed initial context changed')
    records=[tracker,checkpoint,intent,error,report,request.context,record(step/'request.json'),record(worker.run/'initial.json'),*prefix,*_history(worker,before)]
    records += [getattr(call,key) for key in ('request','submitted','response','completed','parsed') if getattr(call,key) is not None]
    return before,tracker,request,faults,records


def _project(before,permit):
    after=copy.deepcopy(before);after['handoffs'].append(dict(schema_version=SCHEMA,permit=permit))
    after['lane_state'][producer.DIALOGUE].update(state='repairing',attempt=1)
    return after


def _seal(worker,binding,after):
    p=read(binding);old=read(p['before']);expected=_project(old,binding)
    expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected,'structural CAS differs from exact projection')
    out=Path(binding['path']).parent;after_record=producer.put(out/'after-tracker.json',after)
    lease=Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))
    complete=producer.put(out/'complete.json',dict(schema_version=SCHEMA,permit=binding,after=after_record,lease=asdict(lease)))
    return replace(worker,dialogue_retry=complete),codec.decode(p['selection']),lease,complete


def authorize(worker,selected,lease,*,checkpoint,phase='dialogue-writer',takeover=None,_fault=lambda _:None):
    w=_worker()
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before,tracker,request,faults,records=_failure(worker,selected,lease,checkpoint,phase,takeover)
        if takeover is not None:
            from scripts.batch.harbor_lease_takeover import completion_records
            records+=completion_records(takeover)
        directory=worker.run/'dialogue-structural-retry-01';require(not directory.exists(),'prepared structural transaction requires explicit reconciliation');directory.mkdir()
        current=w.code(worker);producer.put(directory/'intent.json',dict(checkpoint=checkpoint,phase=phase,tracker=tracker,code=current))
        records+=_prefix(before,selected,worker.context)
        for path in sorted((worker.run/'producer/dialogue').rglob('*')):
            schema.safe_path(path)
            if path.is_file():records.append(record(path))
        from scripts.batch.harbor_nat_retry import _archive
        copies=_archive(directory/'archive',records);before_copy=next(r['retained'] for r in copies if r['original']==tracker)
        require(w.code(worker)==current,'code changed during prefix refresh')
        permit=producer.put(directory/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),before=before_copy,selection=codec.encode(selected),lease=asdict(lease),code=current,copies=copies,checkpoint=checkpoint,phase=phase,initial=record(worker.run/'initial.json'),prior_request=record(w.step_directory(worker,phase)/'request.json'),faults=faults,round=1,structural_remaining=1,semantic_budget_reset=False,lease_takeover=takeover))
        _fault('prepared')
        def mutate(after):
            require(w.code(worker)==current,'code changed before structural CAS')
            fresh=_failure(worker,selected,lease,checkpoint,phase,takeover);require(fresh[0]==before and fresh[3]==faults,'structural fault/context changed before CAS')
            life.check_records([x['original'] for x in copies]);projection=_project(before,permit)
            after['handoffs']=projection['handoffs'];after['lane_state']=projection['lane_state']
        result=schema.cas_mutate(tracker['path'],expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token,scope='workbook:'+selected.content.workbook,mutate=mutate,allowed_fields={'handoffs','lane_state'},allowed_lanes={producer.DIALOGUE},fault_hook=_fault)
        _fault('committed');return _seal(worker,permit,result['tracker'])


def recover(worker,permit,lease):
    w=_worker();p=read(permit);require(p['worker_run']==str(worker.run) and p['code']==w.code(worker) and p['lease']==asdict(lease),'structural recovery identity/code changed')
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for row in p['copies']:life.checked_record(row['retained'])
        selected=codec.decode(p['selection']);path=worker.context.tracker_root/(selected.content.workbook+'.json');after=read(record(path))
        require(after['revision']==lease.revision+1,'structural CAS not committed or advanced')
        w.active(worker,selected,Lease(lease.owner,lease.token,after['revision'],record(path)['sha256']))
        return _seal(worker,permit,after)


def validate(worker,before):
    w=_worker();complete=read(worker.dialogue_retry);p=read(complete['permit'])
    require(complete['schema_version']==p['schema_version']==SCHEMA and p['worker_run']==str(worker.run) and p['code']==w.code(worker),'structural continuation context/code changed')
    require(dict(schema_version=SCHEMA,permit=complete['permit']) in before['handoffs'],'structural permit lacks root CAS')
    after=read(complete['after']);old=read(p['before']);expected=_project(old,complete['permit'])
    expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected and before['revision']>=after['revision'],'structural tracker projection changed')
    require(complete['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),'structural lease record changed')
    if p.get('lease_takeover') is not None:
        from scripts.batch.harbor_lease_takeover import validate_completion
        validate_completion(p['lease_takeover'])
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['retained']['sha256']==row['original']['sha256'],'structural archive changed')
    for binding in (p['initial'],p['prior_request'],p['checkpoint']):life.checked_record(binding)
    require(before['lane_state'][producer.DIALOGUE].get('attempt',0)>=1,'dialogue round consumption regressed')
    return {**p,'lease':complete['lease']}
