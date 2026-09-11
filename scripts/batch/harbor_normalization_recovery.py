"""Explicit root-only format recovery; no dispatch, lane approval or tracker writes.

Only the reviewed schema-field defect is supported. Historical modules remain
byte witnesses, never imported. A new response is mandatory and ordinal two is
final; the rejected actual first response is never repaired or reused as review.
"""
from dataclasses import asdict, replace
from pathlib import Path
import json
import uuid
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_review_context as C
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record, read, require

SCHEMA='normalization-format-recovery/v1'
# The exact reviewed pair is filled at proposal freeze. No arbitrary historical
# implementation or caller-supplied equivalence callback is accepted.
UPGRADES = {'harbor_normalization_producer.py': ['9f0548da66b022ea6fe5a248b48dc397b9544af73b1ead825be03179ab4eace8', '7e3f38bd68a257a769b3588357edffc412eb437547df24ff20b13930e06bd4cf'], 'harbor_review_context.py': ['c4873f9491575517e1bd1dc7510949bbbc3c57a883bdc18e4a36ab007edf91b1', '26ce506a6c814bf1aad81f6714cf78ad2c789958e48d06f9aec3995255ce8ab0']}


def selection_value(s):return N.json_value(asdict(s))


def selection_from(value):
    require(set(value)=={'workbook','bindings','run','policy','budget','first_normalization'},'invalid root selection')
    b=value['budget'];require(set(b)=={'ordinal','limit','attempt_id','evidence'},'invalid budget shape')
    return N.Selection(value['workbook'],value['bindings'],Path(value['run']),N.Policy(**value['policy']),
        N.Budget(b['ordinal'],b['limit'],b['attempt_id'],tuple(b['evidence'])),value['first_normalization'])


def old_prompt():
    new='were retrieved. Return one JSON object with the exact top-level field\n"schema_version": "normalization-review/v1" (not "schema"), and exact\n'
    require(new in N.PROMPT,'reviewed producer prompt changed')
    return N.PROMPT.replace(new,'were retrieved. Return one JSON object using normalization-review/v1 with exact\n')


def inspect_prior(s, prepared, campaign, call, witnesses):
    require(s.budget.ordinal==1 and s.budget.limit==2,'only original first of two may recover')
    require(not any(read(x).get('schema_version')==SCHEMA for x in s.budget.evidence),'chained recovery forbidden')
    require(not any((s.run/name).exists() for name in ('normalized.json','normalized.phase1.json','phase1-result.json','first_normalization.json')),
        'completed normalization is not response-format recovery')
    current,_=N.selection_data(s);saved=read(record(s.run/'production-selection.json'))
    expected=dict(current);expected['code']=saved.get('code')
    require(saved==expected,'old selection/input/budget context changed')
    old_codes={Path(path).name:(path,sha) for path,sha in saved['code'].items()}
    current_codes={Path(path).name:sha for path,sha in current['code'].items()}
    require(len(old_codes)==len(saved['code']) and set(old_codes)==set(current_codes),'code identities changed')
    require(set(witnesses)==set(UPGRADES),'exact reviewed code witnesses required')
    for name,(_,before) in old_codes.items():
        if name in UPGRADES:
            require((before,current_codes[name])==tuple(UPGRADES[name]),'unsupported producer/context upgrade')
            require(E.sha(N.raw(witnesses[name]))==before,'historical code byte witness changed')
        else:require(before==current_codes[name],'unreviewed normalization dependency changed')
    m=read(prepared)
    for name in ('selection','request','evidence','analysis','draft'):N.raw(m[name])
    require(m['selection']==record(s.run/'production-selection.json') and m['draft']==record(s.run/'draft.json'),'prior manifest locators changed')
    draft,rows=N.analyse(s)
    require(draft==read(m['draft']) and N.json_value(rows)==read(m['analysis']),'current atomic analysis differs')
    b=s.bindings;body=read(m['evidence'])
    C.normalization_body(body,source=b['source_file'],baseline=b['baseline_inputs'],draft=m['draft'],rows=rows,bindings=b)
    context,checks=N.workbook_context(b['source_file']['path'],b['baseline_inputs']['path'],rows)
    require(body['context']==context and body['candidate_primitive_checks']==checks,'full original context differs')
    expected_manifest=dict(schema_version='prepared-normalization-review/v1',status='review_required',
        request=m['request'],evidence=m['evidence'],analysis=m['analysis'],draft=m['draft'],selection=m['selection'],
        candidate_count=sum(r['clean'] for r in rows),candidate_references=sorted(r['reference'] for r in rows if r['clean']),
        policy=asdict(s.policy),budget=N.json_value(asdict(s.budget)),semantic_budget_reset=False)
    require(m==expected_manifest and m['candidate_count']>0,'prior prepared manifest changed')
    request=dict(model=s.policy.model,reasoning_effort=s.policy.reasoning_effort,messages=[
        dict(role='system',content=old_prompt()),dict(role='user',content=N.raw(m['evidence']).decode())])
    require(read(m['request'])==request,'old prompt or financial payload changed')
    pc=E.PreparedCall(m['request'],s.policy,E.SemanticScope(s.workbook,b['original_raw_source']['sha256'],N.LANE,'normalization-review'),
        1,2,s.budget.attempt_id,s.budget.evidence)
    actual=E.inspect(pc,campaign)
    require(actual.status=='completed' and actual.call==call,'actual completed prior executor call required')
    result=C.validate_call(call,request,policy=s.policy);review=result['review']
    require('schema_version' not in review and review.get('schema')=='normalization-review/v1','not demonstrated schema-field failure')
    # A diagnostic-only copy establishes that this is solely the field-name
    # failure. It is never saved, returned, applied, or accepted as model output.
    diagnostic=dict(review);diagnostic['schema_version']=diagnostic.pop('schema')
    C.validate_normalization_review(diagnostic,rows)
    try:C.validate_normalization_review(review,rows)
    except C.NormalizationReviewRejected as exc:require(exc.reason_code=='invalid_schema','wrong original rejection')
    else:raise ValueError('valid review must not spend another attempt')
    retained=record(s.run/'normalization-review.json') if (s.run/'normalization-review.json').exists() else None
    if retained:require(read(retained)==review,'retained rejected response differs')
    return dict(outcome=actual.outcome,call=asdict(call),prepared=prepared,rejected_review=retained,
        failure_class='invalid_model_content',reason_code='invalid_schema',semantic_attempt_consumed=True,
        source_invalidity_claimed=False,automatic_retry=False,semantic_budget_reset=False)


def validate_receipt(binding, *, selection):
    value=read(binding)
    require(set(value)=={'schema_version','validator','prior_selection','prior_prepared','prior_call','campaign','code_witnesses','prior_worker_failure','failure','next'},'invalid recovery receipt fields')
    require(value['schema_version']==SCHEMA and value['validator']==record(Path(__file__).resolve()),'recovery validator changed')
    prior_failure=read(value['prior_worker_failure'])
    require(prior_failure==dict(automatic_retry=False,error_sha256='abf6742f443f82708972fdcd79d40a24e52b83ef3310fe734e7757c04038597b',
        error_type='SystemExit',source_invalidity_claimed=False,status='UNRESOLVED',step='normalization_phase1'),
        'original owning failure record changed')
    old=selection_from(value['prior_selection']);campaign=E.Campaign(**{**value['campaign'],
        'root':Path(value['campaign']['root']),'slot_root':Path(value['campaign']['slot_root'])})
    failure=inspect_prior(old,value['prior_prepared'],campaign,N.Call(**value['prior_call']),value['code_witnesses'])
    require(failure==value['failure'],'retained first failure changed')
    following=value['next'];require(set(following)=={'run','attempt_id'},'invalid following attempt')
    require(str(uuid.UUID(following['attempt_id']))==following['attempt_id']!=old.budget.attempt_id,'new distinct canonical attempt required')
    run=E.safe(Path(following['run']));oldrun=E.safe(old.run)
    require(run!=oldrun and oldrun not in run.parents and run not in oldrun.parents,'fresh sibling run required')
    expected=replace(old,run=run,budget=N.Budget(2,2,following['attempt_id'],old.budget.evidence+(binding,)))
    require(selection==expected,'recovery source/run/budget/identity changed')
    return failure


def prepare_recovery(old, prepared, call, campaign, *, code_witnesses, prior_worker_failure, root, new_run, attempt_id):
    """Prepare only. Root may dispatch returned Call after reviewing this receipt."""
    root=E.safe(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    failure=inspect_prior(old,prepared,campaign,call,code_witnesses)
    value=dict(schema_version=SCHEMA,validator=record(Path(__file__).resolve()),prior_selection=selection_value(old),
        prior_prepared=prepared,prior_call=asdict(call),campaign=N.json_value(asdict(campaign)),code_witnesses=code_witnesses,
        prior_worker_failure=prior_worker_failure,failure=failure,next=dict(run=str(E.safe(new_run)),attempt_id=attempt_id))
    receipt=N.keep(root/'recovery.json',value)
    selection=replace(old,run=E.safe(new_run),budget=N.Budget(2,2,attempt_id,old.budget.evidence+(receipt,)))
    validate_receipt(receipt,selection=selection)
    fresh=N.prepare(selection)
    # Exact financial payload is invariant; only the bound new draft locator may
    # differ. Its contents/hash and every candidate/context observation must match.
    before=read(read(prepared)['evidence']);after=read(read(fresh)['evidence'])
    require(before['draft']['sha256']==after['draft']['sha256'],'lossless draft changed')
    compare=json.loads(json.dumps(after));compare['draft']=before['draft']
    for prior_row,new_row in zip(before['candidates'],compare['candidates']):
        require(prior_row['evidence_paths']['draft_sha256']==before['draft']['path']
            and new_row['evidence_paths']['draft_sha256']==after['draft']['path'], 'unexpected candidate draft locator')
        new_row['evidence_paths']['draft_sha256']=before['draft']['path']
    require(compare==before,'financial review payload changed during format recovery')
    return selection,fresh,N.prepared_call(selection,fresh),receipt


def validate_prepared_budget(binding, manifest, *, source, baseline, draft, bindings, policy):
    """Current owning lane/final review must retain the spent first invocation."""
    saved=read(manifest['selection'])
    fields=('workbook','bindings','run','policy','budget','first_normalization')
    selected=selection_from({k:saved[k] for k in fields})
    require(selected.policy==policy and selected.run==Path(draft['path']).parent,'recovery selected normalization differs')
    require(selected.bindings['source_file']==source and selected.bindings['baseline_inputs']==baseline,'recovery selected workbook differs')
    require(all(bindings.get(k)==v for k,v in selected.bindings.items()),'current recovery applicability changed')
    require(manifest['budget']==N.json_value(asdict(selected.budget)),'recovery prepared budget changed')
    validate_receipt(binding,selection=selected)
    current,_=N.selection_data(selected)
    require(current==saved,'current recovery producer/context selection changed')
    return True
