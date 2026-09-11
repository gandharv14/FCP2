"""Explicit, history-preserving root repair of pre-model row-policy extraction.

No task content is changed and no calls are replayed. A fresh code snapshot and
all completed owning gates are checked before the failed step receives a new
directory. The original exception and pre-dialogue witness stay immutable.
"""
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

from scripts.batch.harbor_admission import read, record, require
from scripts.batch import harbor_instruction_worker as w
from scripts.batch import harbor_transition_codec as codec
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_completed_lanes import STEPS, CompletionEvidence, check_completed_gate

SCHEMA='harbor-row-policy-extraction-repair/v1'
ALLOWED={'.cursor/skills/additional-assumptions-dialogue/scripts/extract_claims.py',
         '.cursor/skills/additional-assumptions-dialogue/scripts/spoken_formula.py',
         '.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py',
         'scripts/batch/harbor_disclosure_payload_repair.py',
         'scripts/batch/harbor_disclosure_review_resume.py',
         'scripts/batch/harbor_instruction_worker.py',
         'scripts/batch/harbor_instruction_producer.py',
         'scripts/batch/harbor_readiness.py',
         'scripts/batch/harbor_nat_coverage_retry.py',
         'scripts/batch/harbor_instruction_repair.py'}


def prefix(before,selection,context,*,stop='dialogue'):
    view=copy.deepcopy(before);results=[]
    for row in view['lane_state'].values():row.update(state='pending',phase=None)
    for kind,lane in STEPS:
        if kind==stop:break
        if kind in ('source','segmentation'):
            generation=selection.content.bindings[kind+'_generation']
            result=notes.check_gate(view,notes.GateEvidence(kind,
                record(Path(generation['path'])/'generation-manifest.json'),
                selection.content.normalization_records),context)
        else:result=check_completed_gate(view,CompletionEvidence(kind,selection),context)
        require(result['passed'],'repaired-code prefix failed: '+kind)
        results.append(dict(kind=kind,result=result))
        view['bindings'].update(result['bindings'])
        view['lane_state'][lane].update(state='running' if kind=='normalization_phase1' else 'passed',phase=result.get('phase'))
    return results


def snapshot_changes(old_snapshot,new_root):
    old=read(old_snapshot);old_root=Path(old['root'])
    expected={item['relative']:item['sha256'] for item in old['files']}
    changes=[]
    for relative,digest in expected.items():
        require(record(old_root/relative)['sha256']==digest,'historical frozen code changed')
        target=new_root/relative
        require(target.is_file(),'repair snapshot removed code')
        if record(target)['sha256']!=digest:changes.append(relative)
    for target in new_root.rglob('*'):
        if target.is_file() and '__pycache__' not in target.parts:
            relative=str(target.relative_to(new_root))
            if relative not in expected:changes.append(relative)
    require(set(changes)<=ALLOWED and '.cursor/skills/additional-assumptions-dialogue/scripts/extract_claims.py' in changes,
            'repair snapshot contains unrelated code changes')
    return sorted(changes)


def extraction_kind(failure, claims, stage, root):
    """Bind a known pre-model parser error to a successfully rederived policy."""
    expected=lambda message:dict(type='ValueError',message_sha256=w.executor.sha(message),automatic_replay=False)
    if failure==expected(b'calculation scope requires explicit cell identities'):
        require(any(c['reviewer_only']['entry']=='row_populated' for c in claims['claims']),
                'repaired row policy is not present')
        return 'row-policy'
    require(failure==expected(b'source selection requires a complete named source reference'),
            'unsupported pre-model extraction failure')
    spoken=w.producer.skill_module(root,'additional-assumptions-dialogue','spoken_formula')
    disclosure=read(record(Path(stage)/'tests/disclosure.json'))
    shipped=disclosure.get('agent_records',[])
    require(len(claims['claims'])==len(shipped),'repaired extraction omitted disclosed claims')
    matched=[]
    for item in shipped:
        if item.get('entry')!='source_selection':continue
        ingredient=str(item.get('fields',{}).get('ingredient','')).strip()
        match=spoken.CELL_PHRASE_RE.fullmatch(ingredient)
        if match and match.group('label'):
            label=match.group('label')
            if label.startswith("'") and label.endswith("'") and '"' in label[1:-1]:matched.append(item)
    require(matched,'repaired quoted source label is not present')
    return 'quoted-source-label'


def preserved_disclosure(worker,before,receipt=None):
    selected=getattr(worker,'disclosure_repair',None)
    if receipt is not None:
        require(receipt.get('prior_continuations',{}).get('disclosure_repair')==selected,
                'instruction repair disclosure history changed')
    if selected is None:return []
    from scripts.batch.harbor_disclosure_review_resume import validate_historical_integrity
    return validate_historical_integrity(worker,before)[1]


def authorize(worker,selection,lease,*,old_snapshot,directory):
    require(worker.code_repair is None and all(getattr(worker,key) is None for key in
        ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery')),
        'cannot combine repair histories')
    _,before,tracker=w.active(worker,selection,lease)
    failure=record(w.step_directory(worker,'dialogue-init')/'exception.json')
    preserved=preserved_disclosure(worker,before)
    require(not (worker.run/'producer/dialogue').exists(),'dialogue has already initialized')
    require(not any(w.step_directory(worker,name).exists() for name in w.STEPS[w.STEPS.index('dialogue-writer'):]),
            'later dialogue work already exists')
    predecessor=record(w.step_directory(worker,'verification-commit')/'complete.json')
    saved=read(predecessor)
    old_selection=codec.decode(saved['selection'])
    require(saved['selection']==codec.encode(replace(selection,runtime=old_selection.runtime,observation=old_selection.observation)) and saved['lease']==asdict(lease),
            'exact completed verification predecessor required')
    changes=snapshot_changes(old_snapshot,worker.root)
    proof=prefix(before,selection,worker.context)
    claims=w.producer.skill_module(worker.root,'additional-assumptions-dialogue','extract_claims').extract(selection.content.stage)
    kind=extraction_kind(read(failure),claims,selection.content.stage,worker.root)
    directory=Path(directory)
    with (worker.run/'worker.lock').open('a+b') as lock:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(not directory.exists(),'repair already prepared; select retained receipt')
        retained=[]
        for result in proof:
            for binding in result['result']['evidence']:
                copy=w.snapshot(directory/'prefix-evidence'/('%05d.blob'%len(retained)),binding)
                retained.append(dict(original=binding,retained=copy))
        return w.keep(directory/'permit.json',dict(schema_version=SCHEMA,phase='dialogue-init',
            worker_run=str(worker.run),initial=record(worker.run/'initial.json'),
            original=before['bindings']['original_raw_source'],tracker=w.snapshot(directory/'tracker.before.json',tracker),
            predecessor=predecessor,failure=failure,old_snapshot=old_snapshot,
            code_changes=changes,current_code=w.code(worker),proof=proof,retained_prefix=retained,
            repair_kind=kind,retained_disclosure=preserved,
            **({'prior_continuations':{'disclosure_repair':worker.disclosure_repair}} if worker.disclosure_repair else {}),
            selection=codec.encode(selection),lease=asdict(lease)))


def validate(worker,before):
    receipt=read(worker.code_repair)
    require(receipt['schema_version']==SCHEMA and receipt['phase'] in ('dialogue-init','nat-review')
            and receipt['worker_run']==str(worker.run),'foreign instruction repair')
    require(receipt['initial']==record(worker.run/'initial.json') and
            receipt['original']==before['bindings']['original_raw_source'],
            'repair history or original changed')
    require(receipt['current_code']==w.code(worker),'repair implementation changed')
    require(receipt['code_changes']==snapshot_changes(receipt['old_snapshot'],worker.root),'repair snapshot changed')
    read(receipt['failure']);read(receipt['predecessor']);read(receipt['tracker'])
    retained=preserved_disclosure(worker,before,receipt)
    require(receipt.get('retained_disclosure',[])==retained,'retained disclosure proof changed')
    if receipt.get('previous_repair'):
        previous=read(receipt['previous_repair']);read(receipt['repair_exception'])
        require(previous['original']==receipt['original'] and previous['retained_requests']==receipt['retained_requests'],
                'rebase changed historical calls')
    for item in receipt['retained_prefix']:
        require(item['retained']['sha256']==item['original']['sha256'],'retained prefix hash differs')
        w.checked_record(item['retained'])
    return receipt


def validate_historical_integrity(worker, before):
    """Retain an earlier extraction/coverage repair during a new dialogue round.

    This establishes historical integrity only. The dialogue continuation must
    separately run every current prefix gate before issuing its CAS permit.
    Historical producer code is checked as bytes and is never executed.
    """
    receipt = read(worker.code_repair)
    require(receipt['schema_version'] == SCHEMA and receipt['phase'] in ('dialogue-init', 'nat-review')
            and receipt['worker_run'] == str(worker.run), 'foreign historical instruction repair')
    require(receipt['initial'] == record(worker.run/'initial.json')
            and receipt['original'] == before['bindings']['original_raw_source'],
            'historical repair original or initial changed')
    require(receipt['current_code'] and all(record(path)['sha256'] == digest
            for path, digest in receipt['current_code'].items()), 'historical repair code bytes changed')
    records = [worker.code_repair, receipt['initial'], receipt['failure'],
               receipt['predecessor'], receipt['tracker'], receipt['old_snapshot']]
    prior = read(receipt['tracker'])
    require(prior['workbook_id'] == before['workbook_id']
            and prior['bindings']['original_raw_source'] == receipt['original']
            and prior['revision'] <= before['revision'], 'historical repair tracker differs')
    old = read(receipt['old_snapshot'])
    for item in old['files']:
        require(record(Path(old['root'])/item['relative'])['sha256'] == item['sha256'],
                'historical frozen snapshot changed')
    for item in receipt['retained_prefix']:
        require(item['retained']['sha256'] == item['original']['sha256'], 'historical prefix copy changed')
        records.append(item['retained'])
    for binding in records:
        w.checked_record(binding)
    retained=preserved_disclosure(worker,before,receipt)
    require(receipt.get('retained_disclosure',[])==retained,'historical disclosure proof changed')
    records.extend(retained)
    # These are actual completed calls, not authorization to dispatch them.
    from scripts.batch.harbor_model_review_provenance import Call
    selected = codec.decode(receipt['selection'])
    for item in receipt.get('retained_requests', []):
        request = item['request']; call = Call(**item['call'])
        context = read(request['context'])
        require(context['original'] == receipt['original']
                and context['request'] == request['prepared']['request']
                and call.request['sha256'] == request['prepared']['request']['sha256'],
                'historical repair call identity changed')
        w.producer.validate_call(call, read(call.request), policy=selected.content.model_policy)
    return receipt, records


def rebase_before_dispatch(worker,selection,lease,*,previous,directory):
    """Repair JSON tuple/list comparison before any corrective request exists."""
    _,before,_=w.active(worker,selection,lease)
    old=read(previous)
    require(old['phase']=='nat-review' and old['worker_run']==str(worker.run), 'foreign coverage repair')
    require(old['selection']==codec.encode(selection) and old['lease']==asdict(lease),'rebase context changed')
    exception=record(Path(previous['path']).parent/'steps/nat-review/exception.json')
    require(read(exception) in [dict(type='AdmissionError',message_sha256=w.executor.sha(message),automatic_replay=False)
        for message in (b'request is not a retained completed repair call',b'historical call request differs')],
        'unknown pre-dispatch exception')
    require(not list((worker.run/'producer/requests').glob('naturalization-coverage-review-*')),
            'correction already prepared; cannot rebase before dispatch')
    for path,digest in old['current_code'].items():
        require(record(path)['sha256']==digest,'previous repair code changed')
    require(not Path(directory).exists(),'rebase already prepared')
    updated={**old,'previous_repair':previous,'repair_exception':exception,
        'code_changes':snapshot_changes(old['old_snapshot'],worker.root),'current_code':w.code(worker)}
    permit=w.keep(Path(directory)/'permit.json',updated)
    request=w.request_saved(Path(previous['path']).parent/'steps/nat-review/request.json')
    w.request_saved(Path(directory)/'steps/nat-review/request.json',request)
    return permit


def validate_retained_request(task,request):
    """Allow inspection of exact completed historical calls, never dispatch."""
    receipt=read(task.code_repair)
    require(receipt['schema_version']==SCHEMA and receipt['phase']=='nat-review'
            and str(task.run)==str(Path(receipt['worker_run'])/'producer'),'foreign retained request repair')
    saved_request=json.loads(json.dumps(asdict(request)))
    rows=[item for item in receipt['retained_requests'] if item['request']==saved_request]
    require(len(rows)==1,'request is not a retained completed repair call')
    context=read(request.context)
    require(context['request']==request.prepared.request and
            (context['ordinal'],context['limit'])==(request.prepared.ordinal,request.prepared.limit),
            'historical request budget changed')
    require(context['original']==receipt['original'],'historical original changed')
    from scripts.batch.harbor_model_review_provenance import Call
    call=Call(**rows[0]['call'])
    require(call.request['sha256']==request.prepared.request['sha256'] and
            read(call.request)==read(request.prepared.request),'historical call request differs')
    w.producer.validate_call(call,read(request.prepared.request),policy=task.policy)
    for binding in context['budget_evidence']:w.checked_record(binding)
    return context


def authorize_coverage(worker,selection,lease,*,old_snapshot,directory):
    """Reopen only the same candidate after one coverage-only rejection."""
    require(worker.code_repair is None,'repair already selected')
    _,before,tracker=w.active(worker,selection,lease)
    failure=record(worker.run/'steps/nat-review/blocked.json')
    blocked=read(failure)
    require(blocked['status']=='NAT_REPAIR_REQUIRED' and blocked['step']=='nat-review',
            'exact rejected coverage step required')
    predecessor=record(worker.run/'steps/nat-writer/complete.json')
    saved=read(predecessor)
    require(saved['selection']==codec.encode(selection) and saved['lease']==asdict(lease),
            'exact original writer predecessor required')
    require(not any((worker.run/'steps'/name).exists() for name in w.STEPS[w.STEPS.index('nat-commit'):]),
            'later naturalization work already exists')
    original=w.request_saved(worker.run/'steps/nat-review/request.json')
    writer=w.request_saved(worker.run/'steps/nat-writer/request.json')
    calls=[w.producer.observed(request,worker.campaign) for request in (writer,original)]
    task=w.task(worker,selection,tracker);r,n,state=w.producer._nat(task,worker.root)
    require(state['status']=='retry_ready' and state['next_attempt']==2 and state['attempt_limit']==2
        and len(state['attempts'])==1 and state['attempts'][0]['valid'] is True
        and len(state.get('semantic_reviews',[]))==1,'only first coverage rejection may be reopened')
    rejection=state['semantic_reviews'][0]
    require(rejection['accepted'] is False and rejection['message'] in (
        'independent NAT source-clause coverage incomplete','independent NAT candidate-clause coverage incomplete'),
        'semantic mismatch cannot use coverage continuation')
    candidate=record(n/'attempt-01/candidate.md');source=record(n/'source.snapshot.md')
    require(candidate['sha256']==rejection['candidate_sha256'] and source['sha256']==rejection['source_sha256'],
            'rejected candidate changed')
    result=w.producer.naturalization_review(calls[1],original.prepared.request,
        source=w.producer.text(source),candidate=w.producer.text(candidate),policy=task.policy,
        writer_call=calls[0],writer_request=writer.prepared.request,
        writer_bodies={k:record(n/'attempt-01'/(k+'.md')) for k in ('preamble_body','input_body')},root=worker.root)
    from scripts.batch.harbor_nat_coverage_retry import coverage_feedback
    feedback=coverage_feedback(w.producer.text(source),w.producer.text(candidate),result['review'],root=worker.root)
    require(not feedback['source_order_matches'] or feedback['missing_candidate_lines'],'coverage rejection does not reproduce')
    require(all(item['candidate_claim'] in w.producer.text(candidate) for item in result['review']['clause_checks']),
            'invented evidence cannot use coverage continuation')
    changes=snapshot_changes(old_snapshot,worker.root)
    proof=prefix(before,selection,worker.context,stop='naturalization')
    directory=Path(directory)
    with (worker.run/'worker.lock').open('a+b') as lock:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(not directory.exists(),'repair already prepared; reconcile retained intent')
        state_before=w.snapshot(directory/'state.before.json',record(n/'state.json'))
        w.snapshot(directory/'semantic-review.before.json',record(n/'attempt-01/semantic-review.json'))
        retained=[]
        for result in proof:
            for binding in result['result']['evidence']:
                retained.append(dict(original=binding,retained=w.snapshot(directory/'prefix-evidence'/('%05d.blob'%len(retained)),binding)))
        updated=copy.deepcopy(state)
        updated.update(status='validated',next_attempt=None,final_candidate='attempt-01/candidate.md',final_instruction_sha256=candidate['sha256'])
        with r.recovery_lock(n):
            require(record(n/'state.json')==state_before or record(n/'state.json')['sha256']==state_before['sha256'],'coverage state changed before CAS')
            r.atomic_write_json(n/'state.json',updated)
        selection=w.refreshed(selection)
        permit=w.keep(directory/'permit.json',dict(schema_version=SCHEMA,phase='nat-review',
            worker_run=str(worker.run),initial=record(worker.run/'initial.json'),
            original=before['bindings']['original_raw_source'],tracker=w.snapshot(directory/'tracker.before.json',tracker),
            predecessor=predecessor,failure=failure,old_snapshot=old_snapshot,
            code_changes=changes,current_code=w.code(worker),proof=proof,retained_prefix=retained,
            selection=codec.encode(selection),lease=asdict(lease),state_before=state_before,
            retained_requests=[dict(request=asdict(request),call=asdict(call)) for request,call in zip((writer,original),calls)],
            original_review_consumed=1,additional_review_limit=2,writer_attempts_unchanged=1))
        w.request_saved(directory/'steps/nat-review/request.json',original)
        return permit
