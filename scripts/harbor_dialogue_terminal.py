"""Explicit root terminalization of an actual exhausted dialogue semantic review.

No dispatch, application, publication, budget mutation or historical code execution.
Causality is a separately selected root diagnostic, not a model/source verdict.
"""
from dataclasses import asdict
from pathlib import Path
import fcntl
import time
from scripts.batch import harbor_instruction_worker as W
from scripts.batch import harbor_instruction_producer as P
from scripts.batch import harbor_lane_notes as N
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch import harbor_tracker as S
from scripts.batch import harbor_transition_codec as codec
from scripts import harbor_instruction_campaign as I
from scripts import harbor_parallel_policy as parallel
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_workbook_bootstrap import Lease
from xl_release_publication import _tree_hash

SCHEMA='harbor-dialogue-terminal/v1'
CLASSIFICATION='pipeline_claim_card_defect_review_budget_exhausted'


def records(value):
    if isinstance(value,dict):
        if set(value)=={'path','sha256'}:
            L.checked_record(value);return [value]
        return [r for v in value.values() for r in records(v)]
    if isinstance(value,(tuple,list)):return [r for v in value for r in records(v)]
    return []


def keep(path,value):
    raw=value if isinstance(value,bytes) else L.canonical(value)
    if path.exists():require(path.read_bytes()==raw,'immutable terminal evidence changed')
    else:N._immutable(path,raw)
    return record(path)


def _budget(request,kind,workbook,original):
    p=request.prepared;c=read(request.context)
    require(type(p.ordinal) is int and type(p.limit) is int and p.ordinal==p.limit==2,
            'exact completed ordinal two of two required')
    require(c['kind']==kind and c.get('structural_round') is None and c.get('writer_round') is None,
            'semantic attempt cannot be relabelled from structural retry')
    require(p.scope.workbook_id==workbook and p.scope.original_sha256==original
            and p.scope.lane==P.DIALOGUE and p.scope.budget_id==kind,'semantic scope differs')


def _verdict(receipt):
    require(receipt['round']==2 and receipt['passed'] is False
            and receipt['publication_ready'] is False and receipt['review_faults'],
            'actual final semantic rejection required')
    require(receipt['draft_report']['passed'] is True,'mechanical failure needs separate classification')


def inspect(worker,selected,before_binding,blocked,diagnostic):
    """Reconsume actual calls against immutable pre-CAS tracker, including recovery.

    The snapshot is selected by the transaction's exact root CAS. This permits
    repeat verification after STOPPED without asking producers to generate prose
    from a terminal live tracker. No data or code is loaded from historical modules.
    """
    before=read(before_binding);wb=selected.content.workbook
    S.validate_tracker(before);L.validate_lifecycle(before)
    require(before['workbook_id']==wb and before['execution_state'] not in {'STOPPED','PROMOTED','publication_queued'},'wrong pre-terminal tracker')
    require(_tree_hash(selected.content.stage)[0]==selected.content.stage_sha256,'current staged task changed')
    require(worker.dialogue_semantic_retry is not None,'root second-round permit required')
    from scripts.batch import harbor_dialogue_semantic_retry as retry
    if worker.pretransport_recovery is not None:
        from scripts import harbor_pretransport_recovery
        history=harbor_pretransport_recovery.validate(worker,before)
    else:history=retry.validate(worker,before)
    step=W.step_directory(worker,'dialogue-review')
    require(blocked==record(step/'blocked.json'),'exact final blocked checkpoint required')
    failure=read(blocked)
    require(failure['status']=='DIALOGUE_REPAIR_REQUIRED' and failure['step']=='dialogue-review'
            and failure['selection']==codec.encode(selected),'blocked selection/status differs')
    require(not (step/'complete.json').exists() and not (step/'exception.json').exists(),'completed/uncertain step cannot be terminal semantic rejection')
    intent=read(failure['intent'])
    require(failure['intent']==record(step/'intent.json') and intent['selection']==failure['selection']
            and intent['lease']==failure['lease'] and intent['step']=='dialogue-review'
            and intent['initial']==record(worker.run/'initial.json'),'blocked intent detached')
    # Root may select a subsequently renewed same-owner/token lease; ordinary
    # instruction history proves the exact original lease at the failed step.
    from scripts.batch.harbor_nat_retry import _renewal_chain
    initial_context=read(W.request_saved(step/'request.json').context)
    _renewal_chain(initial_context['budget_evidence'][0],failure['lease'],before,
                   Lease(before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'],before['revision'],before_binding['sha256']))
    requests={kind:W.request_saved(W.step_directory(worker,kind)/'request.json')
              for kind in ('dialogue-writer','dialogue-paraphraser','dialogue-review')}
    for kind,request in requests.items():_budget(request,kind,wb,before['bindings']['original_raw_source']['sha256'])
    task=W.task(worker,selected,before_binding)
    directory,claims,_,_=P._dialogue(task,2,worker.root)
    # Every destination already exists. The owning put() compares bytes and
    # cannot silently create missing proof: presence is independently required.
    destinations=[task.run/'identity.json',directory/'round-02/review.json',
                  directory/'review-call-02.json',directory/'round-02/review-result.json']
    retained=[record(p) for p in destinations]
    receipt=P.consume_dialogue_review(task,requests['dialogue-review'],requests['dialogue-writer'],
                                    requests['dialogue-paraphraser'],worker.campaign,apply=False,root=worker.root)
    require(retained==[record(p) for p in destinations],'owning reconsumption changed evidence')
    _verdict(receipt)
    calls=[P.observed(requests[k],worker.campaign) for k in requests]
    # Executor inspect validates the complete prior ordinal as well as this one.
    require(len({read(c.submitted)['attempt_id'] for c in calls})==3,'author/reviewer call identities overlap')
    diagnosis=read(diagnostic)
    require(diagnosis.get('source_invalidity') is False and diagnosis.get('semantic_round')==2
            and diagnosis.get('semantic_limit')==2 and diagnosis.get('no_new_semantic_attempt') is True,
            'root causal diagnostic must preserve actual scope and budget')
    require(diagnosis.get('causes') and diagnosis.get('contradicted_claims'),'specific root pipeline causal evidence required')
    review=read(receipt['review'])
    contradicted={c['record_id'] for c in review['accuracy']['claims'] if c['must_say']=='contradicted'}
    require(set(diagnosis['contradicted_claims'])<=contradicted,'causal diagnostic cites no actual contradiction')
    evidence=[before_binding,blocked,failure['intent'],diagnostic,*retained,*[dict(path=p,sha256=h) for p,h in W.code(worker).items()],record(__file__)]
    evidence+=records(diagnosis)+retry._history(worker,before)
    for request in requests.values():evidence += [request.context,*records(asdict(request.prepared))]
    for call in calls:evidence+=records(asdict(call))
    for field in ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery'):
        b=getattr(worker,field)
        if b:
            complete=read(b);permit=read(complete['permit'])
            evidence += [b,complete['permit'],complete['after'],permit['before']]
            if 'initial' in permit:evidence.append(permit['initial'])
            for copy in permit['copies']:
                L.checked_record(copy['retained'])
                require(copy['original']['sha256']==copy['retained']['sha256'],'history archive altered')
                evidence.append(copy['retained'])
    evidence += [record(p) for p in sorted(selected.content.stage.rglob('*')) if p.is_file()]
    return dict(lane=P.DIALOGUE,gate='dialogue',passed=False,bindings={},evidence=N.evidence_list(evidence),
                classification=CLASSIFICATION,failure_class='dialogue',terminal=True,fixability='exhausted',
                diagnostic_sha256=diagnostic['sha256'],attempt=2)


def _material(transaction):
    d=read(transaction)
    require(d['schema_version']==SCHEMA and d['adapter']==record(__file__),'terminal adapter identity differs')
    tx=Path(transaction['path']).parent
    for name in ('before','after','notes','selection','proof'):
        require(d[name]==record(tx/(name+('.md' if name=='notes' else '.json'))),'foreign transaction artifact')
    worker=I.worker_decode(d['worker']);selected=codec.decode(read(d['selection']))
    require(W.code(worker)==d['code'],'terminal current code changed')
    before=read(d['before']);after=read(d['after'])
    require(d['lease']==asdict(Lease(before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'],before['revision'],d['before']['sha256'])), 'terminal root lease detached')
    require(before['leases']['task_agent']['acquired_at'] <= d['now'] < before['leases']['task_agent']['expires_at'], 'terminal preparation was outside original lease')
    fresh=inspect(worker,selected,d['before'],d['blocked'],d['diagnostic'])
    require(fresh==read(d['proof']),'terminal proof changed')
    result=dict(fresh,evidence=N.evidence_list(fresh['evidence']+[d['proof'],d['selection']]))
    expected,raw=N._failure_after(before,result,d['now'],Path(d['notes_path']))
    require(after==expected and Path(d['notes']['path']).read_bytes()==raw,'terminal projection changed')
    S.validate_tracker(after);L.validate_lifecycle(after);N.validate_notes_pair(after,raw)
    require(tx.parent==worker.context.tracker_root/(before['workbook_id']+'-notes-transactions'),'foreign terminal journal')
    require(d['tracker_path']==str(worker.context.tracker_root/(before['workbook_id']+'.json'))
            and d['notes_path']==str(worker.context.tracker_root/(before['workbook_id']+'-agent-notes.md')),'terminal destination changed')
    return d,worker,before,after,raw


def reconcile(transaction,*,authority,contract,activation,_fault=lambda _:None):
    # Same explicit root authorization is required for recovery. A committed
    # exact pair can be sealed after expiry; no new mutation/attempt is created.
    d=read(transaction);wb=read(d['before'])['workbook_id']
    require((contract,activation)==(d['contract'],d['activation']),'terminal lane policy substitution')
    with parallel.lane_lock(contract,activation,wb):
        with L.locked(d['tracker_path']):
            d,worker,before,after,raw=_material(transaction)
            path=Path(d['tracker_path']);current=path.read_bytes()
            L.authorize(authority,'dialogue-terminal',path,before,dict(transaction=transaction,diagnostic=d['diagnostic']))
            require(current in (Path(d['before']['path']).read_bytes(),L.canonical(after)),'unrelated tracker CAS cannot be reconciled')
            if current == Path(d['before']['path']).read_bytes():
                lease=Lease(**d['lease'])
                N.LaneNotes(worker.context)._active(before,current,lease.revision,lease.sha256,
                    lease.owner,lease.token,'workbook:'+wb)
            oldnotes=None if d['prior_notes'] is None else Path(L.checked_record(d['prior_notes'])['path']).read_bytes()
            note=Path(d['notes_path']);current_notes=note.read_bytes() if note.exists() else None
            require(current_notes in (oldnotes,raw),'unrelated notes cannot be overwritten')
            N._replace(note,raw);_fault('notes')
            N._replace(path,L.canonical(after));_fault('tracker')
            N.validate_notes_pair(read(record(path)),note.read_bytes())
            completion=keep(Path(transaction['path']).parent/'complete.json',dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256']))
            return dict(transaction=transaction,completion=completion,tracker=record(path),status='STOPPED',publication_performed=False)


def prepare(worker,selected,lease,*,blocked,diagnostic,authority,contract,activation,transaction_name):
    wb=selected.content.workbook;path=worker.context.tracker_root/(wb+'.json')
    require(transaction_name and Path(transaction_name).name==transaction_name and transaction_name not in ('.','..'),'fresh named terminal transaction required')
    with parallel.lane_lock(contract,activation,wb):
        with L.locked(path):
            _,before,binding=W.active(worker,selected,lease)
            L.authorize(authority,'dialogue-terminal-prepare',path,before,dict(blocked=blocked,diagnostic=diagnostic,transaction_name=transaction_name))
            parent=path.parent/(wb+'-notes-transactions');parent.mkdir(exist_ok=True)
            tx=parent/transaction_name
            require(not tx.exists(),'retained preparation exists: reconcile transaction.json, or retain incomplete pre-transaction evidence; never replay')
            tx.mkdir(mode=0o700);before_binding=keep(tx/'before.json',path.read_bytes())
            selection=keep(tx/'selection.json',codec.encode(selected))
            proof=keep(tx/'proof.json',inspect(worker,selected,before_binding,blocked,diagnostic))
            now=time.time_ns();N.LaneNotes(worker.context)._active(before,path.read_bytes(),lease.revision,lease.sha256,lease.owner,lease.token,'workbook:'+wb)
            result=read(proof);result['evidence']=N.evidence_list(result['evidence']+[proof,selection])
            notespath=path.with_name(wb+'-agent-notes.md');after,raw=N._failure_after(before,result,now,notespath)
            S.validate_tracker(after);L.validate_lifecycle(after);N.validate_notes_pair(after,raw)
            prior=keep(tx/'prior-notes.md',notespath.read_bytes()) if notespath.exists() else None
            d=dict(schema_version=SCHEMA,adapter=record(__file__),worker=I.worker_encode(worker),code=W.code(worker),
                   before=before_binding,after=keep(tx/'after.json',after),notes=keep(tx/'notes.md',raw),prior_notes=prior,
                   proof=proof,selection=selection,blocked=blocked,diagnostic=diagnostic,now=now,
                   tracker_path=str(path),notes_path=str(notespath),lease=asdict(lease),contract=contract,activation=activation)
            return keep(tx/'transaction.json',d)
