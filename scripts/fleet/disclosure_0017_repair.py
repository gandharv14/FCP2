"""Owning deterministic disclosure-build repair; never replay role arbitration."""
from dataclasses import asdict,replace
from pathlib import Path
import copy,fcntl,json,os,shutil
from scripts.fleet import disclosure_0017_proof as P
SCHEMA='harbor-0017-disclosure-build-repair/v1'

def selected(worker):
    return worker.code_repair is not None and P.read(worker.code_repair).get('schema_version')==SCHEMA

def producer_run(worker):
    return Path(worker.code_repair['path']).parent/'producer' if selected(worker) else worker.run/'producer'

def inspect_history(root):
    """Read actual complete Calls and original deterministic command receipts."""
    from scripts.batch import harbor_instruction_worker as W,harbor_model_executor as E
    from scripts.batch.harbor_model_review_provenance import Call,validate_call
    P.verified_code(root);p=P.profile();h=p['history'];original=P.read(h['initial']);failure=P.read(h['exception'])
    P.require(failure['automatic_replay'] is False and failure['type']=='AdmissionError','0017 original guarded failure differs')
    command=P.read(h['failed_command']);P.require(command['returncode']!=0,'0017 failed faithcheck became success')
    for row in h['commands']:
        complete=P.read(row);P.require(complete['returncode']==0,'0017 retained preceding command was not completed')
    request=W.request_saved(Path(h['role_request']['path']));observed=E.inspect(request.prepared,E.Campaign(**{**p['campaign'],'root':Path(p['campaign']['root']),'slot_root':Path(p['campaign']['slot_root'])}))
    P.require(observed.status=='completed' and observed.call is not None,'0017 arbitration is not a completed retained Call')
    role=P.read(h['role_result']);P.require(role['passed'] is True and role['errors']==[] and role['ordinal']==1 and role['call']==asdict(observed.call),'0017 original successful arbitration differs')
    parsed=validate_call(observed.call,P.read(observed.call.request),policy=request.prepared.policy,json_response=False)
    P.require(Path(role['raw']['path']).read_text()==parsed['review'],'0017 retained role response changed')
    renderer=W.producer.skill_module(root,'task-disclosure','disclose')
    cases=P.read(role['cases']);normalized,errors,warnings=W.roles._validation(renderer,Path(role['raw']['path']),cases)
    P.require(normalized is not None and not errors and normalized==P.read(h['role_resolutions']),'0017 original role resolution no longer validates')
    proof=P.read(p['source_proof']);P.require(P.source_proof(renderer,proof['bindings'])==proof,'0017 complete source proof differs')
    return dict(initial=original,role=role,source_proof=proof,completed_arbitration=request.prepared.attempt_id,model_calls=0)

def authorize(worker,selection,lease,*,old_snapshot,directory,takeover):
    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_repair as R,harbor_lease_takeover as T
    P.require_fleet_custody(worker.root);p=P.profile();h=p['history'];inspection=inspect_history(worker.root)
    P.require(old_snapshot==p['previous'] and all(getattr(worker,k,None) is None for k in ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair','lease_renewal','disclosure_revision','dialogue_pre_review_repair')),'0017 detached continuation or predecessor')
    initial=inspection['initial'];P.require(W.codec.encode(selection)==initial['selection'],'0017 original pre-build selection required')
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=worker.run/'scope-repair.json';P.require(not marker.exists() and not Path(directory).exists(),'0017 repair already reserved; reconcile without replay')
        P.require(not any((worker.run/'steps'/phase).exists() for phase in W.STEPS[1:]),'0017 later instruction work exists')
        current=W.refreshed(selection);_,before,tracker=W.active(worker,current,lease)
        T.continuation_chain(h['initial_tracker'],initial['lease'],before,lease,takeover=takeover,tracker_path=tracker['path'])
        actual={str(f.relative_to(current.content.stage)):P.sha(f.read_bytes()) for f in current.content.stage.rglob('*') if f.is_file()}
        P.require(actual==p['stage_tree'],'0017 original stopped stage changed')
        prefix=R.prefix(before,current,worker.context,stop='disclosure')
        directory=Path(directory);W.keep(marker,dict(schema_version=SCHEMA,directory=str(directory),source=p['source_proof'],takeover=takeover,automatic_retry=False))
        shutil.copytree(current.content.stage,directory/'retained-stage')
        retained={str(f.relative_to(directory/'retained-stage')):P.sha(f.read_bytes()) for f in (directory/'retained-stage').rglob('*') if f.is_file()}
        P.require(retained==actual,'0017 stopped stage retention failed')
        return W.keep(directory/'permit.json',dict(schema_version=SCHEMA,phase='disclosure-build',worker_run=str(worker.run),initial=h['initial'],original=before['bindings']['original_raw_source'],predecessor=None,old_snapshot=old_snapshot,current_code=W.code(worker),profile=P.PROFILE,source_proof=p['source_proof'],tracker=W.snapshot(directory/'tracker.before.json',tracker),takeover=takeover,retained_stage=retained,retained_directory=str(directory/'retained-stage'),prefix=prefix,selection=W.codec.encode(current),lease=asdict(lease),review_ordinal=1,review_limit=1,models_added=0,semantic_budget_reset=False))

def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W,harbor_lease_takeover as T
    P.verified_code(worker.root);p=P.read(worker.code_repair)
    P.require(p['schema_version']==SCHEMA and p['phase']=='disclosure-build' and p['worker_run']==str(worker.run) and p['initial']==P.profile()['history']['initial'] and p['profile']==P.PROFILE,'0017 repair identity changed')
    P.require(p['original']==before['bindings']['original_raw_source'] and p['current_code']==W.code(worker) and (p['review_ordinal'],p['review_limit'])==(1,1) and p['semantic_budget_reset'] is False,'0017 original source/code/budget changed')
    P.require(P.read(P.record(worker.run/'scope-repair.json'))['directory']==str(Path(worker.code_repair['path']).parent),'0017 global repair reservation changed')
    retained=Path(p['retained_directory']);actual={str(f.relative_to(retained)):P.sha(f.read_bytes()) for f in retained.rglob('*') if f.is_file()}
    P.require(actual==p['retained_stage']==P.profile()['stage_tree'],'0017 original task evidence changed')
    prior,after,lease=T.validate_completion(p['takeover'])
    P.require(lease.owner==p['lease']['owner'] and asdict(lease)==p['lease'] and after['workbook_id']=='0017' and after['bindings']['original_raw_source']==p['original'],'0017 owning takeover differs')
    inspect_history(worker.root)
    return p

def remaining_operation(worker,task,selection,lease,directory,*,commands,lock_fd):
    from scripts.batch import harbor_instruction_worker as W
    P.require_fleet_custody(worker.root);_,before,_=W.active(worker,selection,lease);permit=validate(worker,before)
    if not commands:return selection,lease,'COMMANDS_REQUIRED'
    destination=producer_run(worker);P.require(not destination.exists(),'0017 remaining deterministic operation already started; reconcile')
    # Every original draft/request/case/result remains byte-identical in its
    # original location and in this private prospective producer copy.
    shutil.copytree(worker.run/'producer',destination)
    renderer=W.producer.skill_module(worker.root,'task-disclosure','disclose');proof=P.read(permit['source_proof'])
    payload=P.corrected_records(renderer,proof)
    output=destination/'disclosure-build'/task.stage.name/'records.json'
    P.require(P.record(output)['sha256']==proof['bindings']['records']['sha256'],'0017 copied detector payload changed')
    # Keep the original copied bytes before writing the prospective detector.
    W.snapshot(directory/'original-records.json',P.record(output))
    output.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
    plan=W.producer.disclosure_command_plan(task);W.keep(directory/'remaining-command-plan.json',dict(original=P.profile()['history']['command_plan'],remaining=plan['after_zero_or_validated_roles'][1:],role_arbitration_replayed=False))
    for i,argv in enumerate(plan['after_zero_or_validated_roles'][1:],start=1):
        W._guarded(worker,task,directory,'after-%02d'%i,argv,lock_fd)
    return W.refreshed(selection),lease,None
