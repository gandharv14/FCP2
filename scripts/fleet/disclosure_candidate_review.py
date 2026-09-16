"""One additional source-bound disclosure review; never adopts or qualifies a task.

The original source, failed Call and task remain immutable. A cumulative review
reservation consumes the next ordinal before transport. The original executor
and semantic validator remain authoritative; uncertainty has no replay path.
"""
from __future__ import annotations
import inspect
import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid

SCHEMA='harbor-disclosure-candidate-review/v1'
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
KEYS={'schema_version','workbook','source','snapshot','original_execution','original_task','candidate','proof','allowance','home','env_file'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(p):
    p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(b):
    require(binding(b['path'])==b,'bound review input changed')
    return json.loads(Path(b['path']).read_bytes())


def tree(p):
    return {str(f.relative_to(p)):binding(f)['sha256'] for f in Path(p).rglob('*') if f.is_file()}


def candidate(p,D):
    proof=checked(p['proof']);old=Path(p['original_task']);new=Path(p['candidate'])
    require(proof['status']=='ISOLATED_SOURCE_DISCLOSURE_CANDIDATE' and proof['models']==proof['original_task_mutations']==0
        and proof['qualified'] is False,'actual isolated source proof required')
    before=tree(old);after=tree(new)
    require(before==proof['original_tree'] and after==proof['candidate_tree'],'original or candidate task changed')
    require(set(before)==set(after) and {k for k in before if before[k]!=after[k]}=={'instruction.md','tests/disclosure.json'},'financial task artifacts changed')
    require(binding(new/'instruction.md')==proof['candidate_instruction'] and binding(new/'tests/disclosure.json')==proof['candidate_disclosure'],'candidate proof points elsewhere')
    original_text=D.strip_agent_sections((old/'instruction.md').read_text())
    require(original_text.count(D.OUTPUT_HEADING)==1,'ambiguous original output boundary')
    section=D.render_section(D.agent_records(checked(binding(new/'tests/disclosure.json'))['records']))
    expected=original_text.replace(D.OUTPUT_HEADING,'\n'+section+'\n## Output\n',1)
    require((new/'instruction.md').read_text()==expected,'non-disclosure instructions changed')
    golden=proof['source'];checked_bytes(golden)
    faith=D.faithcheck_task(new,golden=golden['path']);verify=D.verify_task(new)
    require(faith['passed'] is True and faith['faults']==[] and verify['passed'] is True and verify['faults']==[],'candidate failed original mechanical gates')
    return proof,golden,faith,verify


def checked_bytes(b):
    require(binding(b['path'])==b,'bound bytes changed');return Path(b['path']).read_bytes()


def validate_original_body(body,full):
    """Authenticate the supported projection against the full original inputs."""
    from scripts.batch import harbor_disclosure_evidence as evidence
    record_version=getattr(evidence,'RECORD_VERSION',None)
    if 'evidence_projection'not in body:
        expected=full
    else:
        require(body['evidence_projection']in tuple(v for v in(evidence.VERSION,record_version)if v is not None),
                'unknown original disclosure projection')
        expected=evidence.project_body(full)
        if body['evidence_projection']==evidence.VERSION and expected['evidence_projection']==record_version:
            expected['disclosure']['records']=evidence.decode_records(expected['disclosure']['records'])
            expected['evidence_projection']=evidence.VERSION
        elif body['evidence_projection']==record_version and expected['evidence_projection']==evidence.VERSION:
            expected=evidence.compact_records(expected)
    def packed(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    require(packed(body)==packed(expected),
            'original rejected review evidence differs from source task')


def load(b):
    p=checked(b);require(set(p)==KEYS and p['schema_version']==SCHEMA,'closed candidate-review profile required')
    snapshot=checked(p['snapshot']);root=Path(snapshot['root']);actual=tree(root)
    require(actual=={r['relative']:r['sha256']for r in snapshot['files']},'complete selected implementation changed')
    require(checked(binding(root/'.pipeline-code.json'))=={k:v for k,v in actual.items()if k!='.pipeline-code.json'},'implementation inventory differs')
    sys.path.insert(0,str(root))
    from scripts.batch import harbor_model_executor as E,harbor_continuation_allowance as A
    from scripts.batch.harbor_model_review_provenance import Policy,validate_call
    from scripts.batch.harbor_instruction_producer import skill_module
    D=skill_module(root,'task-disclosure','disclose')
    policy=A.validate(p['allowance'],expected_scope=A.scope(p['workbook'],p['source']['sha256']),at=time.time_ns())
    require(policy['authorization']['sha256']==AUTH and policy['limits']['transport_retries']==0,'explicit zero-retry source authorization required')
    checked_bytes(p['source']);v=checked(p['original_execution'])
    require(v['scope']==dict(workbook_id=p['workbook'],original_sha256=p['source']['sha256'],lane='harbor-disclosure-writer',budget_id='disclosure-review')
        and v['ordinal']==v['limit']==1 and v['response_mode']=='json_object'
        and all(v.get(k)is None for k in ('physical_repair','audit_context','review_reservation')),'exact original first disclosure review required')
    call=E.PreparedCall(v['request'],Policy(**v['policy']),E.SemanticScope(**v['scope']),1,1,v['attempt_id'],tuple(v['budget_evidence']))
    campaign=E.Campaign(Path(v['campaign_root']),Path(v['slot_root']),v['model_slots'])
    require(binding(E.location(call,campaign)/'plan.json')==p['original_execution'],'original review namespace changed')
    observed=E.inspect(call,campaign);require(observed.status=='completed' and observed.call is not None,'uncertain or rejected transport cannot authorize source revision')
    provenance=validate_call(observed.call,checked(call.request),policy=call.policy)
    require(provenance['review'].get('passed') is False and provenance['review'].get('blocking_findings'),'actual rejected semantic review required')
    proof,golden,faith,verify=candidate(p,D)
    from scripts.batch.harbor_semantic_readiness import disclosure_manifest,workbook_evidence
    stage=Path(p['candidate']);instruction=binding(stage/'instruction.md');disclosure=binding(stage/'tests/disclosure.json')
    delivered=binding(D.find_environment(stage));manifest=disclosure_manifest(instruction,disclosure,golden,delivered,root=root)
    old_request=checked(call.request);body=json.loads(old_request['messages'][1]['content'])
    original=Path(p['original_task']);original_instruction=binding(original/'instruction.md')
    original_disclosure=binding(original/'tests/disclosure.json');original_delivered=binding(D.find_environment(original))
    require(body['manifest']['input_hashes']['instruction']==original_instruction['sha256']
        and body['manifest']['input_hashes']['disclosure']==original_disclosure['sha256']
        and body['manifest']['input_hashes']['golden']==golden['sha256']
        and body['manifest']['input_hashes']['delivered']==original_delivered['sha256'],
        'original review manifest belongs to another task')
    validate_original_body(body,dict(manifest=body['manifest'],manifest_sha256=body['manifest_sha256'],
        instruction=(original/'instruction.md').read_text(),disclosure=checked(original_disclosure),
        workbooks={'golden':workbook_evidence(golden),'delivered':workbook_evidence(original_delivered)}))
    # Full source snapshots are regenerated; no evidence projection is borrowed
    # from the failed review and no semantic conclusion is supplied as evidence.
    body=dict(manifest=manifest,manifest_sha256=hashlib.sha256(E.canonical(manifest)).hexdigest(),
        instruction=Path(instruction['path']).read_text(),disclosure=checked(disclosure),
        workbooks={'golden':workbook_evidence(golden),'delivered':workbook_evidence(delivered)})
    from scripts.batch import harbor_disclosure_evidence as evidence
    body=evidence.project_body(body)
    schema=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=manifest['input_hashes'],manifest_sha256=body['manifest_sha256'],sentences=[dict(sentence_id='exact manifest id',text='exact manifest text',record_ids=[],golden_evidence=[],delivered_evidence=[],verdict='pass|blocking|non_blocking',findings=[])],blocking_findings=[],non_blocking_flags=[],passed=False)
    prompt='You are a fresh independent workbook disclosure reviewer. Independently inspect both supplied complete workbook snapshots and every disclosed sentence. Check reference identity, copied scope, operators, signs, assumptions, required coverage and answer leakage. Never infer correctness from rendered text or an upstream passed flag. Cover each sentence and record exactly once in manifest order. Record concrete independent observations from both workbooks. Fail missing evidence or any semantic change. Graded output formulas must not be supplied as answer disclosures. Evaluate whether the disclosed nongraded source rules and visible inputs suffice for the required task; report any ambiguous source heading or mismatch between a financial term and its source operands explicitly. Return only the exact JSON schema; passed is true only if every sentence is pass or non_blocking and blocking_findings is empty. '+json.dumps(schema)
    prompt+=' Workbook cells use the declared lossless table encoding; every cell and workbook metadata item is retained. All disclosed records are retained. Auxiliary method assessments omit only explicit cell scopes unrelated to every disclosed record; uncertain scopes are retained.'
    if body['evidence_projection']==getattr(evidence,'RECORD_VERSION',None):
        prompt+=' Disclosure records use the declared lossless column dictionaries: each row is a complete original record, integer indices select exact values from the corresponding column dictionary, and -1 means an absent field. Decode those indices when inspecting record evidence. The unencoded agent_records and manifest identify the required review units. No original record, nested field or excluded record is omitted.'
    request=dict(model=call.policy.model,reasoning_effort=call.policy.reasoning_effort,messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False))])
    return p,E,A,call,campaign,observed,provenance,manifest,request


def probe(b):
    p,E,A,call,campaign,observed,prior,manifest,request=load(b)
    require(not Path(p['home']).exists(),'review namespace already consumed')
    return dict(status='SOURCE_CANDIDATE_REVIEW_VALIDATED_NOT_STARTED',plan=b,original_call=asdict(observed.call),original_outcome=observed.outcome,original_consumed=1,next_ordinal=2,limit=A.read(p['allowance'])['limits']['review_attempts'],sentences=len(manifest['sentences']),request_bytes=len(E.canonical(request)),models_dispatched=0,task_mutations=0)


def semantic_status(review,manifest,manifest_sha,validate):
    """A malformed response is unresolved, never evidence for quarantine."""
    keys={'schema_version','model','input_hashes','manifest_sha256','sentences','blocking_findings','non_blocking_flags','passed'}
    if (set(review)!=keys or review['schema_version']!='harbor-disclosure-faithfulness/v1'
        or review['model']!='gpt-5.6-sol-high' or review['input_hashes']!=manifest['input_hashes']
        or review['manifest_sha256']!=manifest_sha or type(review['passed'])is not bool
        or not isinstance(review['sentences'],list) or len(review['sentences'])!=len(manifest['sentences'])
        or not isinstance(review['blocking_findings'],list) or not isinstance(review['non_blocking_flags'],list)):
        return 'CANDIDATE_REVIEW_UNRESOLVED'
    for got,want in zip(review['sentences'],manifest['sentences']):
        if (set(got)!={'sentence_id','text','record_ids','golden_evidence','delivered_evidence','verdict','findings'}
            or any(got[k]!=want[k]for k in ('sentence_id','text','record_ids'))
            or got['verdict']not in ('pass','non_blocking','blocking')
            or not all(isinstance(got[k],list)for k in ('golden_evidence','delivered_evidence','findings'))
            or not got['golden_evidence'] or not got['delivered_evidence']):
            return 'CANDIDATE_REVIEW_UNRESOLVED'
    blocked=bool(review['blocking_findings'])or any(r['verdict']=='blocking'for r in review['sentences'])
    if blocked and review['passed']is False:return 'CANDIDATE_REVIEW_REJECTED'
    if blocked or review['passed']is not True:return 'CANDIDATE_REVIEW_UNRESOLVED'
    validate(review,manifest,manifest_sha)
    return 'CANDIDATE_REVIEW_PASSED_NOT_ADOPTED'


def run(b):
    p,E,A,old,campaign,observed,prior,manifest,request=load(b)
    from scripts.fleet import model_gate
    require(model_gate.configured(),'centrally claimed review worker required')
    client,owner,_=model_gate.context();require(owner['workbook']==p['workbook'],'foreign review custody')
    client.call('heartbeat',**owner)
    home=E.safe(p['home']);require(not home.exists(),'review namespace consumed; inspect without replay');home.mkdir(parents=True)
    E.save(home/'intent.json',dict(schema_version=SCHEMA,plan=b,original_outcome=observed.outcome,automatic_replay=False))
    def prove(row,scope,ordinal):
        return row==observed.outcome and scope==asdict(old.scope) and ordinal==1 and E.inspect(old,campaign).call==observed.call
    directory=Path(p['allowance']['path']).parent/'review-reservations';directory.mkdir(exist_ok=True)
    reservation=A.reserve_review(directory,allowance=p['allowance'],semantic_scope=asdict(old.scope),prior_records=[observed.outcome],original_limit=1,expected_ordinal=2,prove_closed=prove)
    E.save(home/'reservation.json',reservation);E.save(home/'manifest.json',manifest)
    require(binding(home/'manifest.json')['sha256']==json.loads(request['messages'][1]['content'])['manifest_sha256'],'manifest serialization differs')
    E.save(home/'request.json',request)
    prepared=E.PreparedCall(binding(home/'request.json'),old.policy,old.scope,2,A.read(p['allowance'])['limits']['review_attempts'],str(uuid.uuid4()),tuple([b,p['proof'],p['snapshot'],p['allowance'],reservation,observed.outcome]),review_reservation=reservation)
    E.save(home/'prepared.json',asdict(prepared))
    os.environ['HARBOR_MODEL_SLOTS']=str(campaign.model_slots);os.environ['HARBOR_MODEL_SLOT_ROOT']=str(campaign.slot_root)
    from xl_task_build import read_env_key
    credential=read_env_key(Path(p['env_file']));result=E.execute(prepared,campaign,credential=credential)
    require(result.status=='completed' and result.call is not None,'review transport did not complete; reconcile without replay')
    # Fresh immutable-context revalidation after transport; a changed task/source
    # cannot inherit the returned verdict. This performs no new execution.
    after=load(b);require(after[-2]==manifest and after[-1]==request,'review context changed during transport')
    from scripts.batch.harbor_model_review_provenance import validate_call,require_independent
    actual=validate_call(result.call,request,policy=old.policy);require_independent(actual,[prior])
    from scripts.batch.harbor_semantic_readiness import validate_disclosure_review
    validation_error=None
    try:status=semantic_status(actual['review'],manifest,binding(home/'manifest.json')['sha256'],validate_disclosure_review)
    except Exception as exc:
        status='CANDIDATE_REVIEW_UNRESOLVED';validation_error=dict(type=type(exc).__name__,message=str(exc))
    output=dict(status=status,plan=b,original_outcome=observed.outcome,reservation=reservation,call=asdict(result.call),outcome=result.outcome,validation_error=validation_error,review_passed=status=='CANDIDATE_REVIEW_PASSED_NOT_ADOPTED',task_adopted=False,qualified=False,original_consumed=1,new_physical_calls=1)
    E.save(home/'result.json',output);print(json.dumps(dict(status=output['status'],result=binding(home/'result.json'),qualified=False)))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['probe','run']);p.add_argument('--plan',required=True);p.add_argument('--sha256',required=True);a=p.parse_args();b=dict(path=a.plan,sha256=a.sha256)
    if a.action=='probe':print(json.dumps(probe(b)))
    else:run(b)

if __name__=='__main__':main()
