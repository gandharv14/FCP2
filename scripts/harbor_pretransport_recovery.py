"""Root-only continuation of an exactly unsubmitted dialogue protocol failure.

No model or runtime runs here. The root supplies fresh independently validated
runtime and a selected immutable code review; task-nominated reviews are not
admission authority. Old requests, UUIDs, attempts and exception journals stay.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy,fcntl
from scripts.batch import harbor_tracker as schema,harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_producer as P,harbor_model_executor as E
from scripts.batch import harbor_dialogue_semantic_retry as S,harbor_dialogue_retry as D
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_workbook_bootstrap import Lease
SCHEMA='harbor-dialogue-pretransport-recovery/v1'
CODE_SCHEMA='harbor-reviewed-pretransport-code/v1'
# Source/parser/evaluator/skill changes are deliberately outside this repair.
ALLOWED=frozenset({'scripts/batch/harbor_model_executor.py','scripts/batch/harbor_instruction_producer.py',
 'scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_dialogue_semantic_retry.py',
 'scripts/batch/harbor_tracker_lifecycle.py','scripts/batch/harbor_pause_recovery.py',
 'scripts/batch/harbor_normalization_partitioned.py','scripts/batch/harbor_partition_code_applicability.py',
 'scripts/harbor_pretransport_recovery.py','scripts/harbor_instruction_code_applicability.py'})

def W():
    from scripts.batch import harbor_instruction_worker
    return harbor_instruction_worker


def code_review(binding,worker,old):
    review=read(binding);current=W().code(worker)
    require(set(review)=={'schema_version','root_actor','worker_run','before','after','changes'},'closed root code review required')
    require(review['schema_version']==CODE_SCHEMA and review['root_actor']=='harbor-orchestrator'
            and review['worker_run']==str(worker.run) and review['before']==old and review['after']==current,
            'reviewed code transition differs')
    changed={p for p in set(old)|set(current) if old.get(p)!=current.get(p)}
    require(type(review['changes']) is list and len(review['changes'])==len(changed)
            and {x.get('path') for x in review['changes']}==changed,'code review change set incomplete')
    for item in review['changes']:
        require(set(item)=={'path','before','after'},'closed reviewed module delta required')
        path=Path(item['path']);relative=str(path.relative_to(worker.root))
        require(relative in ALLOWED,'financial or unrelated code change outside recovery')
        if item['before'] is None:require(item['path'] not in old,'missing previous code witness')
        else:require(record(item['before']['path'])==item['before'] and item['before']['sha256']==old.get(item['path']), 'old code witness changed')
        require(item['after']==record(path) and current.get(item['path'])==item['after']['sha256'],'current reviewed code changed')
    return review


def original_worker(worker):return replace(worker,pretransport_recovery=None)


def _absence(worker,request):
    root=E.location(request.prepared,worker.campaign)
    identity=worker.campaign.root/'attempt-identities'/(request.prepared.attempt_id+'.json')
    require(not root.exists() and not root.is_symlink() and not identity.exists() and not identity.is_symlink(),
            'actual or uncertain model submission cannot be retried by this repair')
    return dict(logical_path=str(root),identity_path=str(identity),attempt_id=request.prepared.attempt_id)


def _renewals(old,original_lease,current,current_lease):
    # Semantic _seal retains a JSON projection; its lease hash is the canonical
    # tracker, not that pretty-printed projection. Its full CAS was validated
    # above before deriving these canonical bytes.
    old=copy.deepcopy(old);raw=life.canonical(old)
    require(old['revision']==original_lease['revision'] and schema.sha(raw)==original_lease['sha256'],'semantic canonical tracker differs')
    require((original_lease['owner'],original_lease['token'])==(current_lease.owner,current_lease.token),'renewal changed owner/token')
    require(current['handoffs'][:len(old['handoffs'])]==old['handoffs'],'lease history changed')
    for event in current['handoffs'][len(old['handoffs']):]:
        require(event.get('schema_version')==life.EVENT_SCHEMA and event.get('operation')=='renew'
                and event.get('lease_kind')=='task_agent' and event.get('root_actor')=='harbor-orchestrator','only root task renewal permitted')
        require(event['prior_tracker_sha256']==schema.sha(raw) and event['revision']==old['revision']+1,'renewal hash chain differs')
        prior=old['leases']['task_agent'];new=event['after']
        require(event['before']==prior and prior['heartbeat_at']<=event['at']<prior['expires_at'],'expired or changed renewal predecessor')
        require(all(new[k]==prior[k] for k in schema.LEASE_KEYS-{'heartbeat_at','expires_at'})
                and new['heartbeat_at']==event['at']<new['expires_at'],'renewal identity changed')
        old['handoffs'].append(event);old['leases']['task_agent']=new;old.update(revision=event['revision'],updated_at=event['at']);raw=life.canonical(old)
    require(old==current and old['revision']==current_lease.revision and schema.sha(raw)==current_lease.sha256,'tracker changed beyond exact renewal')


def _failure(worker,selected,lease,exception,review):
    require(worker.pretransport_recovery is None and worker.dialogue_semantic_retry is not None,
            'one original authorized semantic continuation required')
    w=W();_,before,tracker=w.active(worker,selected,lease)
    semantic=S.validate_completed_integrity(worker,before);code_review(review,worker,semantic['code'])
    original=codec.decode(semantic['selection'])
    require(replace(selected,runtime=original.runtime,observation=original.observation)==w.refreshed(original),
            'financial task/selection changed beyond current runtime and context hashes')
    step=w.step_directory(worker,'dialogue-writer');require(exception==record(step/'exception.json'),'exact owning exception required')
    require(read(exception)==dict(type='ExecutorError',message_sha256=E.sha(b'prior semantic scope/limit differs'),automatic_replay=False),
            'unrecognized exception is not a protocol failure')
    intent=record(step/'intent.json');value=read(intent)
    require(value['step']=='dialogue-writer' and value['selection']==semantic['selection']
            and value['lease']==semantic['lease'] and value['previous']==worker.dialogue_semantic_retry
            and value['initial']==semantic['initial'] and value['code']==semantic['code'],'failed writer context changed')
    require(not (step/'complete.json').exists() and not list(step.glob('pending-*.json'))
            and not (step/'blocked.json').exists(),'failed step has additional effects')
    for phase in ('dialogue-paraphraser','dialogue-review','final-runtime','dialogue-commit','enqueue'):
        require(not w.step_directory(worker,phase).exists(),'later step already exists')
    req=w.request_saved(step/'request.json');p=req.prepared
    require(p.scope.lane==P.DIALOGUE and p.scope.budget_id=='dialogue-writer' and p.ordinal==p.limit==2
            and p.response_mode=='json_object','exact remaining structured writer required')
    from scripts import harbor_instruction_code_applicability as applicability
    task=w.task(worker,selected,tracker)
    applicability.profile(P.code_evidence(task))
    P.validate_request(task,req,'dialogue-writer')
    absent=_absence(worker,req)
    prior=Path(absent['logical_path']).parent/'attempt-01';prior_plan=read(record(prior/'plan.json'))
    require(prior_plan['scope']==asdict(p.scope) and prior_plan['policy']==asdict(p.policy)
            and prior_plan['ordinal']==1 and prior_plan['limit']==2 and prior_plan['response_mode']=='text'
            and not prior_plan.get('audit_context'),'original text call scope changed')
    old_call=E.inspect_locked(prior,prior_plan,read(record(prior/'request.json')))
    require(old_call.status=='completed' and old_call.call is not None,'completed actual original call required')
    complete=read(worker.dialogue_semantic_retry)
    _renewals(read(complete['after']),complete['lease'],before,lease)
    w.runtime.validate_runtime(selected.runtime,selected.observation,root=worker.root)
    records=D._prefix(before,selected,worker.context)
    records += [exception,intent,record(step/'request.json'),req.context,req.prepared.request,worker.dialogue_semantic_retry,
                complete['permit'],complete['after'],review,record(prior/'plan.json'),record(prior/'outcome.json')]
    records += [getattr(old_call.call,k) for k in ('request','submitted','response','completed','parsed')]
    return before,tracker,semantic,req,absent,records


def _project(before,binding):
    out=copy.deepcopy(before);out['handoffs'].append(dict(schema_version=SCHEMA,permit=binding));return out


def _seal(worker,binding,after):
    p=read(binding);old=read(p['before']);expected=_project(old,binding)
    expected.update(revision=old['revision']+1,updated_at=after['updated_at'])
    expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected,'recovery CAS changed fields or budgets')
    out=Path(binding['path']).parent;after_record=P.put(out/'after-tracker.json',after)
    lease=Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))
    complete=P.put(out/'complete.json',dict(schema_version=SCHEMA,permit=binding,after=after_record,lease=asdict(lease)))
    return replace(worker,pretransport_recovery=complete),codec.decode(p['selection']),lease,complete


def authorize(worker,selected,lease,*,exception,review,_fault=lambda _:None):
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        request=W().request_saved(W().step_directory(worker,'dialogue-writer')/'request.json')
        with E.scope_lock(E.location(request.prepared,worker.campaign).parent,create=False):
            return _authorize_locked(worker,selected,lease,exception,review,_fault)


def _authorize_locked(worker,selected,lease,exception,review,_fault):
    before,tracker,semantic,req,absent,records=_failure(worker,selected,lease,exception,review)
    out=worker.run/'dialogue-pretransport-recovery-v1';require(not out.exists(),'retained recovery requires explicit reconciliation')
    out.mkdir();from scripts.batch.harbor_nat_retry import _archive
    copies=_archive(out/'archive',records+[tracker])
    old=next(x['retained'] for x in copies if x['original']==tracker)
    # Copy exact prepared envelope; producer request/context/UUID stay at their
    # original paths, not regenerated or relabelled as a new semantic attempt.
    target=out/'steps/dialogue-writer/request.json';P.put(target,E.checked_bytes(record(W().step_directory(worker,'dialogue-writer')/'request.json')))
    permit=P.put(out/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),before=old,lease=asdict(lease),
        selection=codec.encode(selected),semantic=worker.dialogue_semantic_retry,exception=exception,review=review,
        original_request=record(W().step_directory(worker,'dialogue-writer')/'request.json'),request=record(target),
        absence=absent,copies=copies,code=W().code(worker),consumed_rounds=1,round=2,semantic_limit=2,semantic_budget_reset=False))
    _fault('prepared')
    def mutate(after):
        fresh=_failure(worker,selected,lease,exception,review)
        require(fresh[0]==before and fresh[4]==absent,'pretransport evidence changed before CAS')
        life.check_records([x['original'] for x in copies]);after['handoffs']=_project(before,permit)['handoffs']
    result=schema.cas_mutate(tracker['path'],expected_revision=lease.revision,expected_sha256=lease.sha256,
        owner=lease.owner,token=lease.token,scope='workbook:'+selected.content.workbook,mutate=mutate,
        allowed_fields={'handoffs'},allowed_lanes=set(),fault_hook=_fault)
    _fault('committed');return _seal(worker,permit,result['tracker'])


def validate(worker,before):
    complete=read(worker.pretransport_recovery);p=read(complete['permit'])
    require(complete['schema_version']==p['schema_version']==SCHEMA and p['worker_run']==str(worker.run), 'foreign recovery')
    require(p['semantic']==worker.dialogue_semantic_retry and p['code']==W().code(worker),'recovery history/code changed')
    require((p['round'],p['consumed_rounds'],p['semantic_limit'],p['semantic_budget_reset'])==(2,1,2,False),'recovery budget changed')
    require(dict(schema_version=SCHEMA,permit=complete['permit']) in before['handoffs'],'recovery has no root CAS')
    old=read(p['before']);after=read(complete['after']);expected=_project(old,complete['permit'])
    expected.update(revision=old['revision']+1,updated_at=after['updated_at']);expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(after==expected and before['revision']>=after['revision'],'recovery projection differs')
    require(complete['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))), 'recovery lease differs')
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['original']['sha256']==row['retained']['sha256'],'recovery archive changed')
    require(E.checked_bytes(p['request'])==E.checked_bytes(p['original_request']),'prepared envelope changed')
    oldworker=original_worker(worker);semantic=S.validate_completed_integrity(oldworker,before)
    code_review(p['review'],worker,semantic['code'])
    require(semantic['initial']==read(read(worker.dialogue_semantic_retry)['permit'])['initial'],'original initial detached')
    return dict(semantic,selection=p['selection'],lease=complete['lease'],code=p['code'],pretransport=worker.pretransport_recovery)


def recover(worker,permit,lease):
    p=read(permit);require(worker.pretransport_recovery is None and p['worker_run']==str(worker.run)
                           and p['lease']==asdict(lease) and p['code']==W().code(worker),'recovery selection/code changed')
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=read(p['before']);current=read(record(worker.context.tracker_root/(before['workbook_id']+'.json')))
        # Seal only an exact committed CAS; never submits or performs a new CAS.
        for row in p['copies']:life.checked_record(row['retained'])
        return _seal(worker,permit,current)
