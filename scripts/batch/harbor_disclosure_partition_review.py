"""Explicit independent Calls over a frozen, complete disclosure partition.

This module does not grant workflow authority, renew leases, dispatch requests,
or turn a summary into a synthetic Call. The root controller owns those effects.
"""
from dataclasses import asdict
from pathlib import Path
import json,uuid
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_model_review_provenance import Call,Policy,validate_call,require_independent
from scripts.batch import harbor_model_executor as E
ROOT=Path(__file__).resolve().parents[2]
PLAN='harbor-disclosure-partition-review-plan/v1'
AGGREGATE='harbor-disclosure-partition-review-aggregate/v1'
REQUEST='harbor-disclosure-partition-request/v1'
PLAN_KEYS={'schema_version','full_body','projection','manifest','instruction','disclosure','golden','delivered','workbook','original_source','policy','worker_run','initial'}
CONTINUATION='harbor-disclosure-partition-continuation/v1'


def full_body(manifest,instruction,disclosure,golden,delivered,*,root=ROOT):
    from scripts.batch.harbor_semantic_readiness import disclosure_manifest,workbook_evidence
    for b in (manifest,instruction,disclosure,golden,delivered):checked_record(b)
    fresh=disclosure_manifest(instruction,disclosure,golden,delivered,root=root)
    require(read(manifest)==fresh,'partition full manifest differs from actual current inputs')
    return dict(manifest=fresh,manifest_sha256=manifest['sha256'],instruction=Path(instruction['path']).read_text(),
        disclosure=read(disclosure),workbooks={name:workbook_evidence(b) for name,b in (('golden',golden),('delivered',delivered))})


def validate_plan(binding,*,policy,root=ROOT,current_inputs=False):
    from scripts.batch import harbor_disclosure_partition_evidence as evidence
    p=read(binding);extra={'review_round','producer_run'} if 'review_round' in p else set()
    require(set(p)==PLAN_KEYS|extra and p['schema_version']==PLAN,'closed partition review plan required')
    require(isinstance(policy,Policy) and p['policy']==asdict(policy),'partition review policy changed')
    run=E.safe(p['worker_run']);require(p['initial']==record(run/'initial.json'),'partition initial identity changed')
    if extra:
        from scripts.batch import harbor_disclosure_review_round as rounds
        authority=rounds.validate(p['review_round'],{k:v for k,v in p.items() if k!='review_round'})
        require(Path(binding['path'])==Path(p['review_round']['path']).parent/'review-plan.json','round plan moved')
        reserved=[p['review_round'],authority['reservation'],authority['allowance'],*authority['prior_rejections'],*authority['build'].values(),authority['prefix'],*authority['prefix_evidence']]
        if 'source_derivative'in authority:
            derivative=read(authority['source_derivative'])
            require(derivative['schema_version']=='harbor-disclosure-partition-source-derivative/v1',
                    'unknown reserved source derivative')
            reserved += [authority['source_derivative'],*[derivative[k]for k in
                ('implementation','source','original_renderer','renderer','repair','proof','bands','original_instruction')]]
    else:
        marker=record(run/'disclosure-partition-continuation.json');reservation=read(marker)
        require(set(reservation)=={'schema_version','permit'} and reservation['schema_version']==CONTINUATION,'partition global reservation changed')
        permit=read(reservation['permit'])
        require(permit['schema_version']==CONTINUATION and permit['review_plan']==binding and permit['global_round']==permit['global_limit']==1,'partition plan is not the globally reserved first review')
        require(permit['initial']==p['initial'],'partition reservation initial identity changed')
        require(permit['worker_run']==p['worker_run'] and permit['workbook']==p['workbook']
                and permit['original_source']==p['original_source'],'partition reservation source or worker changed')
        reserved=[marker,reservation['permit']]
    for name in ('full_body','projection','manifest','instruction','disclosure','golden','delivered','original_source'):checked_record(p[name])
    body=read(p['full_body']);projection=read(p['projection'])
    if 'empty_review_contract'in projection:
        contract=read(projection['empty_review_contract'])
        require(extra and contract['build_result']==authority['build']['result'],
                'empty review contract requires the actual reserved build')
        reserved += [projection['empty_review_contract'],*[contract[k]for k in
            ('implementation','authorization','build_result','instruction','disclosure','answer_key','stage_manifest','golden','delivered')]]
    require(body['manifest']==read(p['manifest']) and body['manifest_sha256']==p['manifest']['sha256'],'partition full manifest binding changed')
    require(body['instruction']==Path(p['instruction']['path']).read_text() and body['disclosure']==read(p['disclosure']),'partition source text changed')
    if current_inputs:
        require(body==full_body(p['manifest'],p['instruction'],p['disclosure'],p['golden'],p['delivered'],root=root),'partition full source snapshot differs')
    evidence.validate_plan(projection,body,p['golden']['path'],p['delivered']['path'],root=root)
    require(projection['source_bindings']=={'golden':p['golden'],'delivered':p['delivered']},'partition workbook bindings differ')
    return p,projection,[binding,*reserved,p['initial'],*[p[k] for k in ('full_body','projection','manifest','instruction','disclosure','golden','delivered','original_source')]]


def round_numbers(p):
    if 'review_round' not in p:return 1,1
    value=read(p['review_round'])
    require(type(value['round']) is int and type(value['limit']) is int and 1<=value['round']<=value['limit'],'invalid selected cumulative round')
    return value['round'],value['limit']


def scope(p,index):
    number,_=round_numbers(p)
    return E.SemanticScope(p['workbook'],p['original_source']['sha256'],'harbor-disclosure-writer','disclosure-review-round-%02d-shard-%04d'%(number,index+1))


def payload(p,projection,index,policy):
    require(type(index) is int and 0<=index<len(projection['batches']),'invalid disclosure partition index')
    batch=projection['batches'][index]
    schema=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=batch['manifest']['input_hashes'],manifest_sha256=batch['manifest_sha256'],
        sentences=[dict(sentence_id='exact manifest id',text='exact manifest text',record_ids=[],golden_evidence=[],delivered_evidence=[],verdict='pass|blocking|non_blocking',findings=[])],blocking_findings=[],non_blocking_flags=[],passed=False)
    prompt='You are a fresh independent workbook disclosure reviewer. Review ONLY every exact sentence and record in this batch manifest, once and in its original order. The supplied instruction contains shared context and the assigned disclosure units; do not add other review units. Independently inspect BOTH supplied source-derived workbook evidence sets, including exact AST dependencies, selected rows, sheet labels and explicit blanks. Verify reference identity, copied scope, operators, signs, source constants, assumptions, required coverage and answer leakage. Source formula constants are permitted modelling inputs, not computed output answers. Never infer correctness from rendered prose, another batch, or an upstream passed flag. Record concrete observations from BOTH workbooks for every sentence. Fail missing evidence or semantic changes. Return only the exact JSON schema; passed is true only if every sentence is pass or non_blocking and blocking_findings is empty. '+json.dumps(schema)
    if 'review_scope'in batch:
        prompt=prompt.replace('Review ONLY every exact sentence and record in this batch manifest, once and in its original order. The supplied instruction contains shared context and the assigned disclosure units; do not add other review units.', 'Review the complete supplied reconstruction task and both complete source-derived workbooks. The disclosed-sentence manifest is empty; independently determine whether all required nongraded information is present.')
        schema['sentences']=[]
        prompt=prompt[:prompt.index('{"schema_version"')]+json.dumps(schema)
        prompt+=' '+batch['review_scope']['coverage_policy']+' Independently review the full reconstruction task even though its disclosed-sentence list is empty; return any missing nongraded coverage or other defect in blocking_findings. Do not invent a disclosed sentence.'
    content=json.dumps(batch,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True)
    require(len(content.encode())<=projection['max_bytes'],'serialized review shard exceeds its bound')
    return {'model':policy.model,'reasoning_effort':policy.reasoning_effort,'messages':[{'role':'system','content':prompt},{'role':'user','content':content}]}


def task_content(task,p,before):
    """Keep the live original task bound while reviewing a proved derivative.

    Full source derivation remains part of validate_plan (including cached
    validation). This boundary checks both complete trees again before writing
    the first request; a candidate is never installed in the live stage here.
    """
    authority=read(p['review_round']) if 'review_round'in p else {}
    if 'source_derivative'not in authority:
        require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256']
                and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],
                'partition task content changed')
        return
    from scripts.batch import harbor_disclosure_review_round as Q
    derivative=read(authority['source_derivative']);D=Q.bound_module(derivative['implementation'])
    require(D.SCHEMA=='harbor-disclosure-partition-source-derivative/v1','unknown reserved derivative reader')
    proof=read(derivative['proof'])
    require(authority['inputs']=={k:v for k,v in p.items()if k!='review_round'}
            and set(derivative)==D.KEYS and derivative['schema_version']==D.SCHEMA
            and derivative['build']==authority['build']
            and derivative['prior_rejections']==authority['prior_rejections']
            and derivative['source']==before['bindings']['original_raw_source']==p['original_source'],
            'reserved derivative task authority changed')
    candidate=Path(proof['candidate'])
    require(proof['status']=='ISOLATED_SOURCE_DISCLOSURE_CANDIDATE' and proof['qualified']is False
            and proof['models']==proof['original_task_mutations']==0
            and proof['original_task']==str(task.stage) and candidate!=task.stage
            and not candidate.is_relative_to(task.stage) and not task.stage.is_relative_to(candidate)
            and D.tree(task.stage)==task.stage_tree==proof['original_tree']
            and D.tree(candidate)==proof['candidate_tree']
            and set(proof['original_tree'])==set(proof['candidate_tree'])
            and {k for k in proof['original_tree']if proof['original_tree'][k]!=proof['candidate_tree'][k]}
                =={'instruction.md','tests/disclosure.json'},
            'reserved derivative original or candidate tree changed')
    source=before['bindings']['source_file'];golden={k:source[k]for k in ('path','sha256')}
    require(proof['candidate_instruction']==record(candidate/'instruction.md')
            and proof['candidate_disclosure']==record(candidate/'tests/disclosure.json')==p['disclosure']
            and proof['candidate_instruction']['sha256']==p['instruction']['sha256']
            and proof['source']==golden==p['golden']
            and Path(p['delivered']['path']).is_relative_to(candidate)
            and proof['candidate_tree'][str(Path(p['delivered']['path']).relative_to(candidate))]==p['delivered']['sha256']
            and derivative['original_instruction']['sha256']==task.stage_tree['instruction.md'],
            'reserved derivative reviewed bytes changed')
    for row in (p['instruction'],p['disclosure'],p['golden'],p['delivered'],derivative['original_instruction']):
        checked_record(row)


def prepare(task,plan,index,*,_validated=None):
    """Prepare one deterministic bounded request; root dispatches its executor."""
    from scripts.batch import harbor_instruction_producer as producer
    before,identity=producer.task_context(task)
    p,projection,evidence=_validated or validate_plan(plan,policy=task.policy,root=task.root)
    require(p==read(plan) and p['policy']==asdict(task.policy),'prepared cached plan or policy changed')
    producer_run=Path(p['producer_run']) if 'review_round' in p else Path(p['worker_run'])/'producer'
    require(task.run==producer_run and identity['workbook']==p['workbook'] and identity['original']==p['original_source'],'partition producer identity changed')
    if 'review_round' in p:
        import os
        require(bool(os.environ.get('HARBOR_FLEET_URL')),'explicit round requires zero-retry fleet transport')
    task_content(task,p,before)
    directory=task.run/'requests'/scope(p,index).budget_id/'attempt-01'
    request=producer.put(directory/'request.json',payload(p,projection,index,task.policy))
    snapshots=[producer.put(directory/('budget-%03d.snapshot'%i),E.checked_bytes(b)) for i,b in enumerate([task.tracker,*evidence])]
    attempt_path=directory/'attempt-id.json';attempt=read(record(attempt_path))['attempt_id'] if attempt_path.exists() else str(uuid.uuid4())
    producer.put(attempt_path,{'attempt_id':attempt})
    context=producer.put(directory/'context.json',dict(schema_version=REQUEST,plan=plan,index=index,batch_id=projection['batches'][index]['batch_id'],request=request,budget_evidence=snapshots,scope=asdict(scope(p,index)),ordinal=1,limit=1))
    return producer.Request(E.PreparedCall(request,task.policy,scope(p,index),1,1,attempt,tuple(snapshots),response_mode='json_object'),context)


def validate_batch(call,plan,index,policy,root=ROOT,*,_validated=None):
    from scripts.batch.harbor_semantic_readiness import validate_disclosure_review
    p,projection,evidence=_validated or validate_plan(plan,policy=policy,root=root)
    expected=payload(p,projection,index,policy);result=validate_call(call,expected,policy=policy)
    submitted=read(call.submitted)
    require(submitted.get('scope')==asdict(scope(p,index)) and submitted.get('semantic_ordinal')==submitted.get('semantic_limit')==1,'partition call scope or allowance changed')
    if submitted.get('physical_repair') is not None:
        from scripts.batch import harbor_model_pretransport_recovery as recovery
        certificate,old,campaign=recovery.validate_original(submitted['physical_repair'])
        require(old.scope==scope(p,index) and old.request['sha256']==call.request['sha256']
            and submitted['attempt_id']==old.attempt_id and submitted['budget_evidence']==list(old.budget_evidence),
            'partition pretransport continuation changed original request or budget')
    snapshots=submitted.get('budget_evidence',[])
    require(any(E.checked_bytes(b)==E.checked_bytes(plan) for b in snapshots),'partition call omitted frozen plan reservation')
    batch=projection['batches'][index];counts=validate_disclosure_review(result['review'],batch['manifest'],batch['manifest_sha256'])
    return dict(index=index,batch_id=batch['batch_id'],**counts,provenance=result)


def aggregate(plan,calls,policy,root=ROOT,*,_validated=None):
    checked=_validated or validate_plan(plan,policy=policy,root=root);p,projection,_=checked
    require(p==read(plan) and p['policy']==asdict(policy),'aggregate cached plan or policy changed')
    require(type(calls) in (tuple,list) and len(calls)==len(projection['batches']),'partition review coverage incomplete')
    results=[]
    for index,call in enumerate(calls):
        result=validate_batch(call,plan,index,policy,root,_validated=checked)
        require_independent(result['provenance'],[r['provenance'] for r in results]);results.append(result)
    require(len({c.request['sha256'] for c in calls})==len(calls),'partition request reused')
    number,limit=round_numbers(p)
    return dict(schema_version=AGGREGATE,plan=plan,global_round=number,global_limit=limit,calls=[asdict(c) for c in calls],
        batch_ids=[b['batch_id'] for b in projection['batches']],coverage=projection['coverage'])


def validate_aggregate(binding,manifest_binding,*,instruction,disclosure,golden,delivered,policy,root=ROOT):
    value=read(binding);require(set(value)=={'schema_version','plan','global_round','global_limit','calls','batch_ids','coverage'} and value['schema_version']==AGGREGATE,'closed actual-Call aggregate required')
    p,projection,evidence=validate_plan(value['plan'],policy=policy,root=root,current_inputs=True)
    require(p['manifest']==manifest_binding and p['instruction']==instruction,'partition aggregate is not the selected manifest or instruction')
    for old,current in ((p['disclosure'],disclosure),(p['golden'],golden),(p['delivered'],delivered)):
        checked_record(current);require(old['sha256']==current['sha256'],'partition aggregate differs from current workbook or disclosure bytes')
    calls=tuple(Call(**c) for c in value['calls']);require(value==aggregate(value['plan'],calls,policy,root),'partition aggregate coverage or actual Calls changed')
    from scripts.batch.harbor_complete_readiness import evidence_records
    return dict(sentences=len(projection['full_manifest']['sentences']),records=len(projection['full_manifest']['agent_record_ids']),batches=len(calls),
        actual_review_calls=len(calls),evidence=evidence_records([binding,*evidence,*calls]),global_round=value['global_round'],global_limit=value['global_limit'])
