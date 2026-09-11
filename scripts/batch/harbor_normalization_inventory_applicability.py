"""Current proof around a completed, exactly retained two-attempt normalization.

This wrapper never invokes historical validators, rewrites a model response,
resets a budget or approves a lane. Current projection remains mandatory.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy
import json
import xl_audit_inventory_lineage as L
import xl_audit_equivalence as Q
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_recovery as R
from scripts.batch import harbor_review_context as C
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record,require
from scripts.qualified_publication import validate_audit_bindings

SCHEMA='normalization-inventory-proof-applicability/v1'
ORIGINAL_RECOVERY_SHA256='e91af43fd36a4f58ce3b748f58f6457bd346206def4b9e60417b1bf9f3268076'


def _selected(plan):return R.selection_from({k:plan[k] for k in ('workbook','bindings','run','policy','budget','first_normalization')})


def _review(s,prepared,call,campaign,*,first):
    m=read(prepared)
    for k in ('selection','request','evidence','analysis','draft'):N.raw(m[k])
    require(m['selection']==record(s.run/'production-selection.json') and m['draft']==record(s.run/'draft.json'),'original review locators changed')
    draft,rows=N.analyse(s);require(draft==read(m['draft']) and C.json_value(rows)==read(m['analysis']),'original analysis no longer reconstructs')
    body=read(m['evidence']);b=s.bindings
    C.normalization_body(body,source=b['source_file'],baseline=b['baseline_inputs'],draft=m['draft'],rows=rows,bindings=b)
    context,checks=N.workbook_context(b['source_file']['path'],b['baseline_inputs']['path'],rows)
    require((body['context'],body['candidate_primitive_checks'])==(context,checks),'full original financial evidence changed')
    expected=dict(schema_version='prepared-normalization-review/v1',status='review_required',request=m['request'],evidence=m['evidence'],analysis=m['analysis'],draft=m['draft'],selection=m['selection'],candidate_count=sum(r['clean'] for r in rows),candidate_references=sorted(r['reference'] for r in rows if r['clean']),policy=asdict(s.policy),budget=C.json_value(asdict(s.budget)),semantic_budget_reset=False)
    require(m==expected and m['candidate_count']>0,'original prepared review/budget changed')
    request=dict(model=s.policy.model,reasoning_effort=s.policy.reasoning_effort,messages=[dict(role='system',content=R.old_prompt() if first else N.PROMPT),dict(role='user',content=N.raw(m['evidence']).decode())])
    require(read(m['request'])==request,'original model prompt or payload changed')
    pc=E.PreparedCall(m['request'],s.policy,E.SemanticScope(s.workbook,b['original_raw_source']['sha256'],N.LANE,'normalization-review'),s.budget.ordinal,s.budget.limit,s.budget.attempt_id,s.budget.evidence)
    observed=E.inspect(pc,campaign)
    require(observed.status=='completed' and observed.call==call,'original completed executor invocation required')
    result=C.validate_call(call,request,policy=s.policy)
    review=result['review']
    if first:
        require('schema_version' not in review and review.get('schema')=='normalization-review/v1','first invocation no longer has the demonstrated rejection')
        diagnostic=dict(review);diagnostic['schema_version']=diagnostic.pop('schema')
        C.validate_normalization_review(diagnostic,rows)  # Only diagnostic, never retained/applied.
        try:C.validate_normalization_review(review,rows)
        except C.NormalizationReviewRejected as exc:require(exc.reason_code=='invalid_schema','wrong original failure')
        else:raise ValueError('valid first response cannot spend a format retry')
    else:C.validate_normalization_review(review,rows)
    retained=record(s.run/'normalization-review.json') if (s.run/'normalization-review.json').exists() else None
    if retained:require(read(retained)==review,'retained model review was rewritten')
    files=L.records([prepared,asdict(call),observed.outcome,m])
    journal=Path(observed.outcome['path']).parent
    for name,sha in read(observed.outcome)['evidence'].items():files.update(L.records(dict(path=str(journal/name),sha256=sha)))
    failure=dict(outcome=observed.outcome,call=asdict(call),prepared=prepared,rejected_review=retained,failure_class='invalid_model_content',reason_code='invalid_schema',semantic_attempt_consumed=True,source_invalidity_claimed=False,automatic_retry=False,semantic_budget_reset=False)
    return result,rows,files,failure


def propose(prepared,call,*,source,baseline,draft,rows,bindings,policy,original_locator_code,original_recovery_code):
    files=validate_audit_bindings(bindings,expected_generations={k:bindings[k+'_generation']['id'] for k in ('source','segmentation')})
    locator=read(bindings['audit_equivalence']);require(locator.get('schema_version')==L.LOCATOR_SCHEMA,'explicit current locator inventory wrapper required')
    require(locator['original_locator_code']==original_locator_code,'root original locator witness differs')
    L.witness(original_recovery_code,ORIGINAL_RECOVERY_SHA256)
    require(record(Path(R.__file__).resolve())['sha256']==ORIGINAL_RECOVERY_SHA256,'normalization recovery validator changed')
    manifest=read(prepared);plan=read(manifest['selection']);second=_selected(plan)
    require(second.policy==policy and second.run==Path(draft['path']).parent,'selected original normalization differs')
    require(second.bindings['source_file']==source and second.bindings['baseline_inputs']==baseline,'original source/baseline changed')
    require(second.budget.ordinal==second.budget.limit==2,'only retained completed final second attempt is supported')
    require(second.first_normalization is None,'inherited normalization count needs a separate proof')
    recovery=second.budget.evidence[-1];proof=read(recovery)
    require(proof.get('schema_version')==R.SCHEMA and proof['validator']==record(Path(R.__file__).resolve()),'original recovery proof identity changed')
    require(set(proof)=={'schema_version','validator','prior_selection','prior_prepared','prior_call','campaign','code_witnesses','prior_worker_failure','failure','next'},'original recovery proof fields changed')
    prior=R.selection_from(proof['prior_selection'])
    require(prior.budget.ordinal==1 and prior.budget.limit==2 and prior.first_normalization is None,'original first budget changed')
    require(not any(read(x).get('schema_version')==R.SCHEMA for x in prior.budget.evidence),'nested recovery forbidden')
    require(not any((prior.run/n).exists() for n in ('normalized.json','normalized.phase1.json','phase1-result.json','first_normalization.json')),'first normalization was already emitted')
    following=proof['next'];require(set(following)=={'run','attempt_id'} and following==dict(run=str(second.run),attempt_id=second.budget.attempt_id),'second attempt identity changed')
    require(second.budget.attempt_id!=prior.budget.attempt_id and second==replace(prior,run=second.run,budget=N.Budget(2,2,second.budget.attempt_id,prior.budget.evidence+(recovery,))),'two-attempt chain changed')
    require(second.run!=prior.run and prior.run not in second.run.parents and second.run not in prior.run.parents,'original runs overlap')
    expected_failure=dict(automatic_retry=False,error_sha256='abf6742f443f82708972fdcd79d40a24e52b83ef3310fe734e7757c04038597b',error_type='SystemExit',source_invalidity_claimed=False,status='UNRESOLVED',step='normalization_phase1')
    require(read(proof['prior_worker_failure'])==expected_failure,'original owning rejection changed')
    old_locator=read(locator['previous']);old_bindings=prior.bindings
    require(old_bindings['audit_equivalence']==locator['previous'] and old_bindings['audit_validator_upgrade']==read(locator['audit_applicability'])['previous']
        and old_bindings['audit_upgrade_original_code']==locator['original_code'],'original audit proof chain differs')
    audit_keys={'audit_equivalence','audit_validator_upgrade','audit_upgrade_original_code'}
    require(all(bindings.get(k)==v for k,v in old_bindings.items() if k not in audit_keys),'normalization source/inventory/data changed')
    new_bindings={k:bindings[k] for k in old_bindings}
    # Current source/audit and all untouched generator dependencies are actually
    # revalidated. The first ordinal has no recursive recovery receipt.
    current,_=N.selection_data(replace(prior,bindings=new_bindings))
    old_audit_files={old_bindings[k]['path']:old_bindings[k]['sha256'] for k in ('variable_source_metadata','variable_source_inventory','variable_source_audit')}
    old_audit_files.update(old_locator['checked_files']);old_audit_files[locator['previous']['path']]=locator['previous']['sha256']
    for selected,is_first in ((prior,True),(second,False)):
        saved=read(record(selected.run/'production-selection.json'));expected=copy.deepcopy(current)
        expected.update(bindings=old_bindings,run=str(selected.run),budget=C.json_value(asdict(selected.budget)),audit_validation_files=old_audit_files,code=saved['code'])
        require(saved==expected,'original plan changed beyond independently proved audit/code identities')
        old_codes={Path(k).name:v for k,v in saved['code'].items()};new_codes={Path(k).name:v for k,v in current['code'].items()}
        require(len(old_codes)==len(saved['code']) and set(old_codes)==set(new_codes),'normalization code identities ambiguous')
        for name,before in old_codes.items():
            if is_first and name in R.UPGRADES:
                require((before,new_codes[name])==tuple(R.UPGRADES[name]),'unreviewed original format repair')
                L.witness(proof['code_witnesses'][name],before)
            else:require(before==new_codes[name],'normalization generator or validator changed')
    require(set(proof['code_witnesses'])==set(R.UPGRADES),'format upgrade byte witnesses incomplete')
    c=proof['campaign'];campaign=E.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots'])
    first_review,first_rows,first_files,failure=_review(prior,proof['prior_prepared'],N.Call(**proof['prior_call']),campaign,first=True)
    require(failure==proof['failure'],'original rejection and completed attempt evidence changed')
    review,current_rows,second_files,_=_review(second,prepared,call,campaign,first=False)
    C.require_independent(review,[first_review])
    require(C.json_value(current_rows)==C.json_value(rows) and record(second.run/'draft.json')==draft,'current final normalization analysis differs')
    files.update(first_files);files.update(second_files)
    files.update(L.records([recovery,proof,original_locator_code,original_recovery_code,record(Path(__file__).resolve())]))
    # Historical byte-code roles are retained as witnesses; never mark changed
    # live code bytes with an old hash in the final current evidence list.
    files.update({str(Path(k).resolve()):v for k,v in current['code'].items()})
    return dict(schema_version=SCHEMA,bound=dict(prepared=prepared,call=asdict(call),source=source,baseline=baseline,draft=draft,policy=asdict(policy)),
        current_bindings=Q.selected(bindings),audit_equivalence=bindings['audit_equivalence'],recovery=recovery,
        original_locator_code=original_locator_code,original_recovery_code=original_recovery_code,
        review=review['review'],retained_ordinals=[1,2],limit=2,checked_files=dict(sorted(files.items())),
        semantic_attempts_added=0,semantic_budget_reset=False,lane_approval=False)


def validate(binding,prepared,call,*,source,baseline,draft,rows,bindings,policy,original_locator_code):
    value=read(binding);require(value.get('schema_version')==SCHEMA,'wrong normalization inventory wrapper schema')
    expected=propose(prepared,call,source=source,baseline=baseline,draft=draft,rows=rows,bindings=bindings,policy=policy,
        original_locator_code=original_locator_code,original_recovery_code=value['original_recovery_code'])
    require(value==expected,'normalization inventory wrapper stale or altered')
    return dict(review=expected['review'],checked_files={**expected['checked_files'],binding['path']:binding['sha256']})
