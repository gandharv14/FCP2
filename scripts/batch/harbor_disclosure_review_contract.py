"""Source-bound review of an empty disclosure, without revealing graded formulas.

This module supplies review evidence and validates a real prior rejected Call.
It neither grants a review allowance nor changes a task or a reviewer verdict.
"""
from pathlib import Path
from dataclasses import asdict
import copy,json,re
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_model_review_provenance import Policy,validate_call

SCHEMA='harbor-completed-disclosure-review-rejection/v1'
CONTRACT='harbor-empty-disclosure-review-contract/v1'
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
POLICY=('The listed cells are graded reconstruction outputs. Their formulas and numeric answers must not be added to the agent disclosure. '
        'Independently assess whether the visible inputs, labels, and disclosed nongraded rules suffice to reconstruct them. '
        'Reject missing nongraded source rules, missing inputs, ambiguous labels, semantic errors, or answer leakage. '
        'An empty sentence manifest is not evidence of correctness and does not waive required coverage.')


def closed_rejection(binding,scope,ordinal):
    p=read(binding)
    require(set(p)=={'schema_version','implementation','execution','outcome'}and p['schema_version']==SCHEMA
        and p['implementation']==record(__file__),'closed original semantic rejection required')
    plan=read(p['execution']);root=Path(p['execution']['path']).parent
    require(p['execution']==record(root/'plan.json')and p['outcome']==record(root/'outcome.json')
        and root==Path(plan['campaign_root'])/E.sha(E.canonical(scope))/('attempt-%02d'%ordinal)
        and plan['scope']==scope and plan['ordinal']==ordinal==plan['limit']==1
        and plan.get('physical_repair')is None and plan.get('max_transport_retries')==0,
        'original semantic review source, ordinal or journal differs')
    request=read(plan['request']);require(record(root/'request.json')['sha256']==plan['request']['sha256'],
        'original review request differs')
    actual=E.inspect_locked(root,plan,request)
    require(actual.status=='completed'and actual.call is not None and actual.outcome==p['outcome'],
        'original review is not a completed physical Call')
    result=validate_call(actual.call,request,policy=Policy(**plan['policy']));review=result['review']
    body=json.loads(request['messages'][1]['content'])
    require(review.get('passed')is False and isinstance(review.get('blocking_findings'),list)
        and bool(review['blocking_findings']),'actual blocking review required')
    checked=copy.deepcopy(review)
    for sentence in checked.get('sentences',[]):
        require(sentence.get('verdict')in {'pass','non_blocking','blocking'},'unknown original verdict')
        if sentence['verdict']=='blocking':
            require(bool(sentence.get('findings')),'blocking sentence omitted its findings')
            sentence['verdict']='pass'
    checked.update(passed=True,blocking_findings=[])
    from scripts.batch.harbor_semantic_readiness import validate_disclosure_review
    # This checks response identity and complete coverage, without replacing the
    # retained rejected response or authorizing a passing result.
    validate_disclosure_review(checked,body['manifest'],body['manifest_sha256'])
    return True


def normalized_target(value):
    require(type(value)is str and '!'in value,'explicit target cell required')
    sheet,cell=value.rsplit('!',1)
    if sheet.startswith("'")and sheet.endswith("'"):sheet=sheet[1:-1].replace("''", "'")
    cell=cell.replace('$','').upper()
    require(bool(sheet)and re.fullmatch(r'[A-Z]+[1-9][0-9]*',cell)is not None,'single target cell required')
    return sheet+'!'+cell


def review_scope(binding,body,workbooks):
    p=read(binding)
    require(set(p)=={'schema_version','implementation','authorization','build_result','instruction','disclosure',
        'answer_key','stage_manifest','golden','delivered','targets'}and p['schema_version']==CONTRACT
        and p['implementation']==record(__file__),'closed empty disclosure contract required')
    require(p['authorization']['sha256']==AUTH and record(p['authorization']['path'])==p['authorization'],
        'explicit review-scope authorization required')
    for key in ('instruction','disclosure','answer_key','stage_manifest','golden','delivered','build_result'):
        require(record(p[key]['path'])==p[key],'review contract input changed')
    require(body['manifest']['sentences']==[]and body['manifest']['agent_record_ids']==[]
        and body['disclosure']['records']==[]and body['disclosure']['agent_records']==[],
        'empty review contract cannot replace nonempty disclosure')
    require(Path(p['instruction']['path']).read_text()==body['instruction']and read(p['disclosure'])==body['disclosure']
        and all(p[k]==workbooks[k].binding for k in ('golden','delivered')),
        'review contract changed complete current inputs')
    from scripts.batch import harbor_transition_codec as codec,harbor_instruction_worker as W
    build=read(p['build_result']);selection=codec.decode(build['selection']);stage=selection.content.stage
    inventory=read(p['stage_manifest'])
    require(build['status']=='STEP_COMPLETE'and build['next_step']=='disclosure-review'
        and W.static.publication.canonical_hash(inventory)==selection.content.stage_sha256,
        'review contract detached from completed build stage')
    for key,name in [('instruction','instruction.md'),('disclosure','tests/disclosure.json'),('answer_key','tests/answer_key.json')]:
        require(inventory[name]==dict(algorithm='sha256',path=name,sha256=p[key]['sha256'],
            size_bytes=Path(p[key]['path']).stat().st_size),'retained review input differs from completed stage')
    targets=sorted(normalized_target(k)for k in read(p['answer_key'])['targets'])
    require(targets and len(targets)==len(set(targets))and p['targets']==targets,'graded target inventory differs')
    for target in targets:
        golden=workbooks['golden'].cells.get(target);delivered=workbooks['delivered'].cells.get(target)
        require(golden is not None and golden['data_type']=='f'
            and(delivered is None or delivered['value']is None),'graded reconstruction cell must be a stripped source formula')
    return dict(schema_version=CONTRACT,contract_sha256=binding['sha256'],graded_targets=targets,coverage_policy=POLICY)
