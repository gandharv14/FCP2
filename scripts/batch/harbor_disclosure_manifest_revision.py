"""One manifest-only corrective review; retain the original independent rows.

No synthetic model response is created. Two independently validated Calls cover
an exact disjoint partition of the unchanged disclosure records.
"""
import copy,json,fcntl,shutil
from pathlib import Path
from dataclasses import asdict
from collections import Counter
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E,harbor_transition_codec as codec

SCHEMA='harbor-disclosure-manifest-revision/v1'
BUDGET='disclosure-review-revision-02'
ALLOWED={'scripts/batch/harbor_disclosure_manifest_revision.py','scripts/batch/harbor_disclosure_revision.py',
 'scripts/batch/harbor_semantic_readiness.py','scripts/batch/harbor_instruction_producer.py',
 'scripts/batch/harbor_instruction_worker.py','scripts/harbor_instruction_campaign.py',
 'scripts/batch/harbor_instruction_lease_renewal.py','scripts/batch/harbor_complete_readiness.py',
 'scripts/batch/harbor_transition_codec.py','scripts/batch/harbor_nat_retry.py',
 'scripts/batch/harbor_dialogue_semantic_retry.py'}


def partition(old,new,review,old_sha):
    from scripts.batch.harbor_disclosure_revision import rejected
    blocked=rejected(review,old,old_sha)
    require(set(old)==set(new)=={'schema_version','input_hashes','agent_record_ids','sentences'},'manifest schema changed')
    require(all(old[k]==new[k] for k in ('schema_version','input_hashes','agent_record_ids')),'manifest source, renderer or record identity changed')
    require(len(old['sentences'])==len(new['sentences']),'manifest coverage changed')
    changed=[]
    for i,(a,b) in enumerate(zip(old['sentences'],new['sentences'])):
        require(set(a)==set(b)=={'order','text','sentence_id','record_ids'},'manifest unit schema changed')
        require(a['order']==b['order']==i and a['record_ids']==b['record_ids'],'manifest order/records changed')
        if a==b:continue
        # The sole permitted delta is restoring the suffix after an embedded
        # newline. Both versions are independently extracted from identical text.
        require('\n' in b['text'] and b['text'].split('\n',1)[0]==a['text'],'not a multiline extraction correction')
        changed.append(i)
    require(changed==blocked,'changed units must exactly equal original blocking units')
    records=[rid for s in new['sentences'] for rid in s['record_ids']]
    require(len(records)==len(set(records)) and Counter(records)==Counter(new['agent_record_ids']),'full manifest record coverage changed')
    scope=copy.deepcopy(new);scope['sentences']=[copy.deepcopy(new['sentences'][i]) for i in changed]
    scope['agent_record_ids']=[rid for s in scope['sentences'] for rid in s['record_ids']]
    return scope,{'changed_indices':changed,'retained_indices':[i for i in range(len(new['sentences'])) if i not in changed],
                  'sentences':len(new['sentences']),'records':len(records)}


def compose(old,new,old_review,old_sha,scope,new_review,scope_sha):
    from scripts.batch.harbor_semantic_readiness import validate_disclosure_review
    expected,delta=partition(old,new,old_review,old_sha)
    require(scope==expected,'scoped manifest differs from exact corrected partition')
    checked=validate_disclosure_review(new_review,scope,scope_sha)
    retained=[old_review['sentences'][i] for i in delta['retained_indices']]
    all_rows=retained+new_review['sentences']
    records=[r for row in all_rows for r in row['record_ids']]
    require(len(records)==len(set(records)) and Counter(records)==Counter(new['agent_record_ids']),'composed record coverage incomplete or duplicated')
    require({row['sentence_id'] for row in all_rows}=={row['sentence_id'] for row in new['sentences']},'composed sentence coverage differs')
    return dict(sentences=delta['sentences'],records=delta['records'],retained_sentences=len(retained),
                newly_reviewed_sentences=checked['sentences'],composition='disjoint-retained-and-corrected/v1')


def changes(binding,root):
    old=read(binding);expected={r['relative']:r['sha256'] for r in old['files']}
    for name,digest in expected.items():require(record(Path(old['root'])/name)['sha256']==digest,'historical snapshot changed')
    actual={str(p.relative_to(root)):record(p)['sha256'] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    require(set(expected)<=set(actual),'manifest successor removed code')
    delta=sorted(k for k in actual if actual[k]!=expected.get(k))
    require(set(delta)<=ALLOWED and 'scripts/batch/harbor_semantic_readiness.py' in delta,'unreviewed manifest successor')
    return delta


def historical(p,before):
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_lease_renewal as R
    changes(p['old_snapshot'],Path(p['current_root']))
    descriptor=read(p['old_descriptor']);old=read(p['old_snapshot']);root=Path(old['root'])
    require(descriptor['schema_version']==C.SCHEMA and descriptor['code']==record(root/'scripts/harbor_instruction_campaign.py'),'historical descriptor code changed')
    worker=C.worker_decode(descriptor['worker'])
    require(worker.root==root and str(worker.run)==p['worker_run'] and worker.lease_renewal==p['prior_continuations']['lease_renewal'],'historical worker detached')
    require(all(getattr(worker,k,None) is None for k in R.CONTINUATIONS if k!='lease_renewal'),'unexpected historical continuation')
    return R.validate_historical_integrity(worker,before,p['old_snapshot'])


def historical_lease(worker,before):
    p=read(worker.disclosure_revision)
    require(p['schema_version']==SCHEMA and worker.lease_renewal==p['prior_continuations'].get('lease_renewal'),'not the retained historical renewal')
    require(p['worker_run']==str(worker.run) and p['initial']==record(worker.run/'initial.json'),'foreign historical manifest authority')
    owned(worker.disclosure_revision,p)
    return historical(p,before)


def owned(binding,p):
    run=Path(p['worker_run']);E.checked_bytes(binding)
    require(p['schema_version']==SCHEMA and p['revision']==p['limit']==2 and p['budget_id']==BUDGET and p['semantic_budget_reset'] is False,'invalid manifest revision authority')
    require(read(record(run/'disclosure-revision-complete.json'))==binding,'manifest revision completion changed')
    require(read(record(run/'disclosure-revision.json'))==dict(schema_version=SCHEMA,directory=str(Path(binding['path']).parent),revision=2,limit=2,predecessor=p['predecessor']),'manifest revision reservation changed')
    require(p['predecessor']==p['prior_continuations'].get('lease_renewal') and set(p['prior_continuations'])=={'lease_renewal'},'detached manifest predecessor')
    from scripts.batch.harbor_workbook_bootstrap import Lease
    validate_renewal(p['failed_lease'],p['renewal'],read(p['tracker']),Lease(**p['lease']))
    for b in [p['instruction'],p['disclosure'],p['manifest'],p['full_manifest'],p['old_manifest'],p['old_request'],p['tracker'],p['old_descriptor'],*p['retained_history']]:E.checked_bytes(b)


def original(p):
    from scripts.batch.harbor_model_review_provenance import validate_call,Policy
    call=codec.decode(p['old_call']);submitted=read(call.submitted)
    require(submitted['scope']==p['original_scope'] and submitted['semantic_ordinal']==submitted['semantic_limit']==1
            and submitted['attempt_id']==p['original_attempt_id'] and submitted.get('physical_repair') is None,'original review budget changed')
    result=validate_call(call,read(call.request),policy=Policy(**p['policy']))
    body=json.loads(read(call.request)['messages'][1]['content'])
    from scripts.batch.harbor_disclosure_evidence import decode_body
    if 'evidence_projection' in body:body=decode_body(body)
    require(body['manifest']==read(p['old_manifest']) and body['manifest_sha256']==p['old_manifest']['sha256'],'old manifest detached from actual call')
    require(body['instruction']==Path(p['instruction']['path']).read_text(),'old instruction differs')
    scope,delta=partition(body['manifest'],read(p['full_manifest']),result['review'],body['manifest_sha256'])
    require(scope==read(p['manifest']) and delta==p['delta'],'partition changed')
    return call,result,body


def _validate(worker,before,*,historical_snapshot=None):
    from scripts.batch import harbor_instruction_worker as W
    binding=worker.disclosure_revision;p=read(binding);owned(binding,p)
    require(p['worker_run']==str(worker.run) and p['initial']==record(worker.run/'initial.json') and p['original']==before['bindings']['original_raw_source'],'foreign manifest revision')
    if historical_snapshot is None:expected=W.code(worker)
    else:
        from scripts.batch.harbor_instruction_lease_renewal import _historical_inventory
        expected=_historical_inventory(worker,historical_snapshot)
    require(p['current_code']==expected and p['current_root']==str(worker.root),'manifest revision code changed')
    require(changes(p['old_snapshot'],worker.root)==p['code_changes'],'manifest code scope changed')
    original(p)
    historical(p,before)
    # Later owning renewals retain this exact revision. Their validation is
    # independent of the historical current-reader validation above and cannot reopen its call.
    if worker.lease_renewal!=p['prior_continuations']['lease_renewal']:
        from scripts.batch import harbor_instruction_lease_renewal as R
        if historical_snapshot is None:R.validate(worker,before)
        else:R.validate_historical_integrity(worker,before,historical_snapshot)
    return p


def validate(worker,before):
    return _validate(worker,before)


def validate_historical_integrity(worker,before,old_snapshot):
    return _validate(worker,before,historical_snapshot=old_snapshot)


def validate_task(task):
    from scripts.batch.harbor_instruction_worker import code
    p=read(task.disclosure_revision);owned(task.disclosure_revision,p)
    require(task.run==Path(p['worker_run'])/'producer' and task.stage==codec.decode(p['selection']).content.stage,'manifest request namespace changed')
    require(p['current_code']==code(task),'manifest request code changed')
    require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256'] and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'manifest-only correction edited task')
    original(p)
    return p


def validate_review_history(binding,call,manifest,instruction,disclosure):
    p=read(binding);owned(binding,p);old,prior,_=original(p);submitted=read(call.submitted)
    require(submitted['scope']=={**p['original_scope'],'budget_id':BUDGET} and submitted['semantic_ordinal']==submitted['semantic_limit']==1
            and submitted['attempt_id']!=p['original_attempt_id'] and submitted.get('physical_repair') is None,'corrective review scope changed')
    require(any(E.checked_bytes(b)==E.checked_bytes(binding) for b in submitted['budget_evidence']),'corrective review omitted revision reservation')
    require(manifest['sha256']==p['manifest']['sha256'] and instruction['sha256']==p['instruction']['sha256'] and disclosure['sha256']==p['disclosure']['sha256'],'reviewed manifest version changed')
    return dict(disclosure_revision=binding,retained_original_call=p['old_call'],global_revision=2,global_limit=2)


def authorize(worker,selection,lease,*,old_snapshot,old_descriptor,directory,renewal=None):
    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as R
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_model_review_provenance import validate_call
    from scripts.batch.harbor_semantic_readiness import disclosure_manifest,workbook_evidence
    from scripts.batch.harbor_disclosure_evidence import decode_body,scoped_disclosure
    with (worker.run/'worker.lock').open('a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(worker.lease_renewal is not None and all(getattr(worker,k,None) is None for k in R.CONTINUATIONS if k!='lease_renewal'),'unexpected manifest correction history')
        _,before,tracker=W.active(worker,selection,lease)
        directory=E.safe(directory);require(not directory.exists(),'manifest revision namespace used')
        reservation=worker.run/'disclosure-revision.json';require(not reservation.exists(),'global disclosure revision consumed')
        step=worker.run/'steps/disclosure-review';request_binding=record(step/'request.json');request=W.request_saved(step/'request.json')
        intent_binding=record(step/'intent.json');intent=read(intent_binding);exception=record(step/'exception.json')
        require(intent['step']=='disclosure-review' and intent['selection']==codec.encode(selection)
                and intent['previous']==worker.lease_renewal and intent['initial']==record(worker.run/'initial.json'),'failed review boundary changed')
        validate_renewal(intent['lease'],renewal,before,lease)
        require(read(exception)==dict(type='AdmissionError',message_sha256=E.sha(b'blocking or unknown sentence verdict'),automatic_replay=False),'not a consumed semantic review rejection')
        require(not (step/'complete.json').exists() and not any(W.step_directory(worker,n).exists() for n in W.STEPS[2:]),'later work or completed disclosure cannot revise')
        result=E.inspect(request.prepared,worker.campaign);require(result.status=='completed' and result.call is not None,'pending or uncertain review cannot revise')
        require(request.prepared.scope.budget_id=='disclosure-review' and request.prepared.ordinal==request.prepared.limit==1
                and request.prepared.physical_repair is None,'original disclosure budget changed')
        review=validate_call(result.call,read(result.call.request),policy=request.prepared.policy)['review']
        context=read(request.context);old=read(old_snapshot);base=Path(old['root']);files={r['relative']:r['sha256'] for r in old['files']}
        changed=changes(old_snapshot,worker.root)
        for path,digest in intent['code'].items():
            path=Path(path);require(path.is_relative_to(base) and files.get(str(path.relative_to(base)))==digest and record(path)['sha256']==digest,'failed intent code differs from old snapshot')
        for b in context['code']:
            path=Path(b['path']);require(path.is_relative_to(base) and files.get(str(path.relative_to(base)))==b['sha256'],'request code differs from old snapshot');E.checked_bytes(b)
        require(context['kind']=='disclosure-review' and context['request']==request.prepared.request and context['stage_tree']==W.static.tree(selection.content.stage)
                and context['budget_evidence']==list(request.prepared.budget_evidence),'old request context changed')
        for b in [*context['evidence'],*context['budget_evidence']]:E.checked_bytes(b)
        body=json.loads(read(result.call.request)['messages'][1]['content']);projected='evidence_projection' in body
        if projected:body=decode_body(body)
        stage=selection.content.stage;instruction=record(stage/'instruction.md');disclosure=record(stage/'tests/disclosure.json')
        golden=selection.content.bindings['source_file'];delivered=record(W.producer.delivered_workbook(stage,selection.content.workbook))
        require(set(body)=={'manifest','manifest_sha256','workbooks','instruction','disclosure'} and body['instruction']==stage.joinpath('instruction.md').read_text(),'old request instruction changed')
        require(body['disclosure']==(scoped_disclosure(read(disclosure)) if projected else read(disclosure)),'old disclosure evidence changed')
        for role,b in [('golden',golden),('delivered',delivered)]:
            fresh=workbook_evidence(b);prior=copy.deepcopy(body['workbooks'][role]);prior['path']=fresh['path'];require(prior==fresh,'old workbook evidence changed')
        full=disclosure_manifest(instruction,disclosure,golden,delivered,root=worker.root)
        scope,delta=partition(body['manifest'],full,review,body['manifest_sha256'])
        oldbindings=[b for b in context['evidence'] if b['sha256']==body['manifest_sha256']]
        require(len(oldbindings)==1 and read(oldbindings[0])==body['manifest'],'old manifest evidence ambiguous')
        seed=dict(old_snapshot=old_snapshot,current_root=str(worker.root),old_descriptor=old_descriptor,worker_run=str(worker.run),
                  prior_continuations={'lease_renewal':worker.lease_renewal},tracker=tracker)
        historical(seed,before)
        proof=prefix(before,selection,worker.context,stop='disclosure')
        require(W.active(worker,selection,lease)[1]==before,'tracker changed before manifest reservation')
        W.keep(reservation,dict(schema_version=SCHEMA,directory=str(directory),revision=2,limit=2,predecessor=worker.lease_renewal))
        producer=directory/'producer';shutil.copytree(worker.run/'producer/disclosure-build',producer/'disclosure-build')
        # Retain exact immutable candidate evidence before later NAT changes it.
        saved_instruction=W.snapshot(directory/'candidate/instruction.md',instruction)
        saved_disclosure=W.snapshot(directory/'candidate/disclosure.json',disclosure)
        old_manifest=W.snapshot(directory/'original/manifest.json',oldbindings[0])
        full_manifest=W.keep(directory/'candidate/full-manifest.json',full)
        manifest=W.keep(directory/'candidate/scoped-manifest.json',scope)
        from scripts.batch.harbor_complete_readiness import evidence_records
        history=[request_binding,request.context,intent_binding,exception,result.outcome,*evidence_records(result.call),*context['evidence'],*context['budget_evidence']]
        retained=[W.snapshot(directory/'history'/('%05d.blob'%i),b) for i,b in enumerate(history)]
        require(W.active(worker,selection,lease)[1]==before,'tracker changed during manifest reservation')
        permit=W.keep(directory/'permit.json',dict(schema_version=SCHEMA,revision=2,limit=2,phase='disclosure-review',
            worker_run=str(worker.run),producer_run=str(producer),initial=record(worker.run/'initial.json'),
            predecessor=worker.lease_renewal,prior_continuations={'lease_renewal':worker.lease_renewal},
            original=before['bindings']['original_raw_source'],tracker=W.snapshot(directory/'tracker.before.json',tracker),
            old_snapshot=old_snapshot,old_descriptor=old_descriptor,current_root=str(worker.root),code_changes=changed,current_code=W.code(worker),
            selection=codec.encode(selection),lease=asdict(lease),failed_lease=intent['lease'],renewal=renewal,old_request=request_binding,retained_history=retained,
            old_call=codec.encode(result.call),original_scope=read(result.call.submitted)['scope'],original_attempt_id=request.prepared.attempt_id,
            policy=asdict(request.prepared.policy),semantic_budget_reset=False,budget_id=BUDGET,instruction=saved_instruction,disclosure=saved_disclosure,
            manifest=manifest,full_manifest=full_manifest,old_manifest=old_manifest,delta=delta,proof=proof))
        W.keep(worker.run/'disclosure-revision-complete.json',permit)
        return permit


def validate_renewal(failed,binding,before,lease):
    from scripts import harbor_campaign as C
    from scripts.batch.harbor_nat_retry import _renewal_chain
    if binding is None:
        require(failed==asdict(lease),'changed manifest lease requires explicit renewal');return
    value=read(binding);prior=C.decode(value['previous']);current=C.decode(value['lease'])
    require(asdict(prior)==failed and current==lease and lease.revision==prior.revision+1,'renewal did not extend exact rejected review lease')
    require(value['result']['revision']==lease.revision and value['result']['sha256']==lease.sha256,'renewal CAS result changed')
    require(value['before']['sha256']==prior.sha256 and value['tracker']['sha256']==lease.sha256,'renewal snapshots differ')
    after=read(value['tracker']);require(after==before,'renewal tracker differs')
    _renewal_chain(value['before'],failed,after,lease)
