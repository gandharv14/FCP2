"""Explicit owning continuation of one sealed disclosure HTTP 400.

Keeps the failed step, request, UUID and logical budget. A new physical request
may omit only independently rederived irrelevant assessment rows. This receipt
never supplies a semantic verdict and cannot reopen an uncertain transport.
"""
from dataclasses import asdict, replace
from pathlib import Path
import fcntl
from scripts.batch.harbor_admission import read, record, require
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_disclosure_payload_repair as payload
from scripts.batch import harbor_transition_codec as codec

SCHEMA='harbor-disclosure-review-resume/v1'


def campaign(permit):
    value=payload.read(permit)['campaign']
    return E.Campaign(Path(value['root']),Path(value['slot_root']),value['model_slots'])


def original_review_request(call):
    """Validate the transport proof, then return the full retained review body."""
    submitted=read(call.submitted);binding=submitted['physical_repair']
    old=payload.original(binding);value=payload.read(binding)
    prepared=replace(old,request=value['request'],physical_repair=binding)
    c=campaign(binding);payload.validate_prepared(prepared,c)
    result=E.inspect(prepared,c)
    require(result.status=='completed' and result.call==call,'review is not the completed physical repair')
    require(read(call.request)==payload.project(read(old.request),projection=value.get('projection')),'physical review projection changed')
    return read(old.request)


def validate_request_context(task,request):
    from scripts.batch import harbor_instruction_producer as p
    value=read(request.context);binding=request.prepared.physical_repair
    payload.validate_prepared(request.prepared,campaign(binding))
    require(value['original_context']==record(task.run/'requests/disclosure-review/attempt-01/context.json'),
            'physical context detached from original owning request')
    old=read(value['original_context']);original=payload.original(binding)
    require(old['kind']=='disclosure-review' and old['request']==original.request
            and old['budget_evidence']==list(original.budget_evidence), 'original disclosure context changed')
    for evidence in old['code']+old['evidence']+old['budget_evidence']:read_bytes(evidence)
    current=p.code_evidence(task)
    if value['code']!=current and getattr(task,'code_repair',None) is not None:
        repair=read(task.code_repair)
        continuation_binding=repair.get('prior_continuations',{}).get('disclosure_repair')
        require(continuation_binding is not None,'historical physical context lacks disclosure continuation')
        prior=read(continuation_binding)
        from scripts.batch import harbor_instruction_worker as w
        require(str(task.run)==str(Path(prior['worker_run'])/'producer')
                and prior['request']==record(Path(prior['request']['path']))
                and read(prior['request'])['context']==request.context
                and repair['current_code']==w.code(task),
                'historical physical context detached from code repair')
        for item in value['code']:read_bytes(item)
        actual=E.inspect(request.prepared,campaign(request.prepared.physical_repair))
        require(actual.status=='completed' and actual.call is not None,'historical physical request is not complete')
        current=value['code']
    expected={**old,'request':request.prepared.request,'code':current,
              'original_context':value['original_context'],'physical_repair':binding}
    require(value==expected,'physical request context changed beyond explicit code/request transition')
    return value


def read_bytes(binding):
    return E.checked_bytes(binding)


def changes(binding,root):
    from scripts.batch.harbor_dialogue_warning_requalification import ALLOWED as DIALOGUE
    allowed=DIALOGUE|{'scripts/batch/harbor_model_executor.py','scripts/batch/harbor_model_review_provenance.py',
        'scripts/batch/harbor_disclosure_payload_repair.py','scripts/batch/harbor_disclosure_review_resume.py',
        'xl_release_publication.py','scripts/qualified_publication.py'}
    old=read(binding);oldroot=Path(old['root']);expected={r['relative']:r['sha256'] for r in old['files']}
    changed=[]
    for name,digest in expected.items():
        require(record(oldroot/name)['sha256']==digest,'historical code bytes changed')
        require((root/name).is_file(),'historical code removed')
        if record(root/name)['sha256']!=digest:changed.append(name)
    for path in root.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and str(path.relative_to(root)) not in expected:changed.append(str(path.relative_to(root)))
    guard='scripts/harbor_campaign.py'
    if guard in changed:
        require(guard in expected,'campaign guard cannot be introduced by repair')
        old_guard=(oldroot/guard).read_bytes();new_guard=(root/guard).read_bytes()
        require(old_guard.count(b'\nSECONDS=900\n')==1
                and new_guard==old_guard.replace(b'\nSECONDS=900\n',b'\nSECONDS=3300\n',1),
                'only exact reviewed campaign guard extension is supported')
        allowed=allowed|{guard}
    # An already-installed payload repair needs no implementation change.
    # Without a payload delta, permit only this owning adapter's compatibility
    # update; do not turn a same-code recovery into a general code rebase.
    payload_changed='scripts/batch/harbor_disclosure_payload_repair.py' in changed
    adapter_only=set(changed)<={'scripts/batch/harbor_disclosure_review_resume.py',guard}
    require(set(changed)<=allowed and (payload_changed or adapter_only),
            'unrelated disclosure repair code transition')
    return sorted(changed)


def authorize(worker,selection,lease,*,blocked,old_snapshot,directory,takeover=None,projection=None):
    from scripts.batch import harbor_instruction_worker as w
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_lease_takeover import continuation_chain, completion_records
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(all(getattr(worker,k) is None for k in ('retry','dialogue_retry','boundary_recovery',
            'dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair')),
            'disclosure repair cannot combine independent histories')
        require(not (worker.run/'disclosure-repair.json').exists(),'disclosure continuation already authorized')
        _,before,tracker=w.active(worker,selection,lease)
        step=worker.run/'steps/disclosure-review';failure=read(blocked)
        require(blocked==record(step/'blocked.json') and failure['status']=='MODEL_RECONCILIATION_REQUIRED'
                and failure['step']=='disclosure-review' and not (step/'complete.json').exists()
                and not (step/'exception.json').exists(),'exact rejected disclosure step required')
        old=codec.decode(failure['selection'])
        require(selection==old,'disclosure repair cannot change selected financial content')
        predecessor=record(worker.run/'steps/disclosure-build/complete.json')
        previous=read(predecessor);intent=read(failure['intent'])
        require(previous['selection']==failure['selection'] and previous['lease']==failure['lease']
                and intent['previous']==predecessor and intent['initial']==record(worker.run/'initial.json'),
                'disclosure predecessor changed')
        require(not any((worker.run/'steps'/name).exists() for name in w.STEPS[2:]),'later instruction work already exists')
        original=w.request_saved(step/'request.json');context=read(original.context)
        continuation_chain(context['budget_evidence'][0],failure['lease'],before,lease,
                           takeover=takeover,tracker_path=tracker['path'])
        for path,digest in intent['code'].items():
            require(Path(path).is_relative_to(Path(read(old_snapshot)['root'])) and record(path)['sha256']==digest,
                    'failed request code snapshot changed')
        changed=changes(old_snapshot,worker.root)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        payload._rejected(original.prepared,worker.campaign)
        require(context['stage_tree']==w.static.tree(selection.content.stage),'retained disclosure stage changed')
        directory=E.safe(directory);require(not directory.exists(),'disclosure continuation already prepared')
        require(w.active(worker,selection,lease)[1]==before,'tracker changed during disclosure authorization')
        repaired=payload.prepare(original.prepared,worker.campaign,directory/'physical',projection=projection)
        t=w.task(worker,selection,tracker)
        newcontext=w.keep(directory/'context.json',{**context,'request':repaired.request,
            'code':w.producer.code_evidence(t),'original_context':original.context,'physical_repair':repaired.physical_repair})
        request=w.producer.Request(repaired,newcontext);w.producer.validate_request(t,request,'disclosure-review')
        retained=[]
        for result in proof:
            for binding in result['result']['evidence']:
                retained.append(w.snapshot(directory/'prefix-evidence'/('%05d.blob'%len(retained)),binding))
        nextstep=directory/'steps/disclosure-review'
        w.request_saved(nextstep/'request.json',request)
        for name in ('manifest.json','instruction.snapshot.md'):w.snapshot(nextstep/name,record(step/name))
        permit=w.keep(directory/'permit.json',dict(schema_version=SCHEMA,worker_run=str(worker.run),
            initial=record(worker.run/'initial.json'),blocked=blocked,predecessor=predecessor,
            original=before['bindings']['original_raw_source'],old_snapshot=old_snapshot,
            code_changes=changed,current_code=w.code(worker),tracker=w.snapshot(directory/'tracker.before.json',tracker),
            takeover=takeover,takeover_records=completion_records(takeover) if takeover else [],
            proof=proof,retained_prefix=retained,request=record(nextstep/'request.json'),
            original_request=record(step/'request.json'),original_context=original.context,
            selection=codec.encode(selection),lease=asdict(lease),physical_repair=repaired.physical_repair,
            semantic_budget_reset=False,physical_count=1))
        w.keep(worker.run/'disclosure-repair.json',permit)
        return permit


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as w
    require(read(record(worker.run/'disclosure-repair.json'))==worker.disclosure_repair,
            'disclosure continuation differs from owning reservation')
    receipt=read(worker.disclosure_repair)
    require(receipt['schema_version']==SCHEMA and receipt['worker_run']==str(worker.run)
            and receipt['initial']==record(worker.run/'initial.json')
            and receipt['original']==before['bindings']['original_raw_source'], 'foreign disclosure continuation')
    if receipt['current_code']!=w.code(worker):
        if worker.disclosure_revision is not None:
            from scripts.batch.harbor_disclosure_revision import validate as validate_revision
            revision=validate_revision(worker,before)
            require(revision['prior_continuations'].get('disclosure_repair')==worker.disclosure_repair,'revision detached original disclosure repair')
            return validate_historical_integrity(worker,before)[0]
        if worker.lease_renewal is not None:
            from scripts.batch.harbor_instruction_lease_renewal import validate as validate_renewal
            renewal=validate_renewal(worker,before)
            require(renewal['prior_continuations'].get('disclosure_repair')==worker.disclosure_repair,'renewal detached disclosure history')
            return validate_historical_integrity(worker,before)[0]
        require(worker.code_repair is not None,'disclosure continuation code changed')
        repair=read(worker.code_repair)
        require(repair.get('prior_continuations',{}).get('disclosure_repair')==worker.disclosure_repair
                and repair['current_code']==w.code(worker),'disclosure code repair history detached')
        return validate_historical_integrity(worker,before)[0]
    require(receipt['code_changes']==changes(receipt['old_snapshot'],worker.root),'disclosure continuation code changed')
    require(receipt['physical_count']==1 and receipt['semantic_budget_reset'] is False,'disclosure retry budget changed')
    for binding in [receipt['blocked'],receipt['predecessor'],receipt['tracker'],*receipt['retained_prefix'],*receipt['takeover_records']]:read_bytes(binding)
    request=w.request_saved(Path(receipt['request']['path']));read_bytes(receipt['request'])
    original=w.request_saved(Path(receipt['original_request']['path']));read_bytes(receipt['original_request'])
    require(receipt['original_request']==record(worker.run/'steps/disclosure-review/request.json')
            and original.context==receipt['original_context']==read(request.context)['original_context']
            and payload.original(receipt['physical_repair'])==original.prepared,'original owning request substituted')
    require(request.prepared.physical_repair==receipt['physical_repair'],'detached physical request')
    payload.validate_prepared(request.prepared,worker.campaign)
    return receipt


def validate_historical_integrity(worker,before):
    """Read-only proof of the completed physical review under its original code."""
    from scripts.batch import harbor_instruction_worker as w
    require(read(record(worker.run/'disclosure-repair.json'))==worker.disclosure_repair,'disclosure reservation changed')
    receipt=read(worker.disclosure_repair)
    require(receipt['schema_version']==SCHEMA and receipt['worker_run']==str(worker.run)
            and receipt['initial']==record(worker.run/'initial.json')
            and receipt['original']==before['bindings']['original_raw_source'],'foreign historical disclosure')
    require(receipt['physical_count']==1 and receipt['semantic_budget_reset'] is False,'historical disclosure budget changed')
    prior=read(receipt['tracker'])
    require(prior['workbook_id']==before['workbook_id'] and prior['revision']<=before['revision']
            and prior['bindings']['original_raw_source']==receipt['original'],'historical disclosure tracker changed')
    for path,digest in receipt['current_code'].items():require(record(path)['sha256']==digest,'historical disclosure code changed')
    snapshot=read(receipt['old_snapshot'])
    for row in snapshot['files']:require(record(Path(snapshot['root'])/row['relative'])['sha256']==row['sha256'],'original disclosure snapshot changed')
    records=[worker.disclosure_repair,*[receipt[k] for k in ('initial','blocked','predecessor','tracker','old_snapshot','request','original_request','original_context','physical_repair')],
             *receipt['retained_prefix'],*receipt['takeover_records']]
    for binding in records:read_bytes(binding)
    original=w.request_saved(Path(receipt['original_request']['path']));request=w.request_saved(Path(receipt['request']['path']))
    require(receipt['original_request']==record(worker.run/'steps/disclosure-review/request.json')
            and original.context==receipt['original_context']==read(request.context)['original_context']
            and payload.original(receipt['physical_repair'])==original.prepared
            and request.prepared.physical_repair==receipt['physical_repair'],'historical disclosure request substituted')
    payload.validate_prepared(request.prepared,worker.campaign)
    result=E.inspect(request.prepared,worker.campaign)
    require(result.status=='completed' and result.call is not None,'historical disclosure review is not complete')
    require(original_review_request(result.call)==read(original.prepared.request),'historical disclosure projection changed')
    from scripts.batch.harbor_complete_readiness import evidence_records
    records.extend(evidence_records(result.call));records.append(result.outcome)
    for prepared in (original.prepared,request.prepared):
        records.extend(record(path) for path in sorted(E.location(prepared,worker.campaign).iterdir()) if path.is_file() and not path.name.endswith('.lock'))
    return receipt,records
