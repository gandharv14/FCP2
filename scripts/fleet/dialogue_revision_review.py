"""One explicitly authorized additional review of a deterministic source derivative.

Both original review rounds and both author pairs remain immutable. This runner
never applies a draft or claims publication readiness. A durable cumulative
reservation consumes the next ordinal before transport, with no replay API.
"""
from __future__ import annotations
import inspect
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
SCHEMA='fleet-dialogue-source-revision-review/v1'
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
FIELDS={'schema_version','workbook','helper','stdlib','revision','semantic_context','source','golden','outcome','result','descriptor','blocked','tracker','claims','draft','candidate','selection','proof','allowance','allowance_module','home','owner','fence','env_file'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(p):
    p=Path(p);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in (p,*p.parents)),'canonical bound input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def checked(b):
    require(binding(b['path'])==b,'bound review input changed');return json.loads(Path(b['path']).read_bytes())

def module(name,b):
    require(binding(b['path'])==b,'bound review implementation changed')
    s=importlib.util.spec_from_file_location(name,b['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def historical_call(E,call,campaign,scope,ordinal):
    p=checked(binding(Path(call.submitted['path']).parent/'plan.json'))
    require(p['scope']==scope and p['ordinal']==ordinal and p['limit']==2 and p['response_mode']=='json_object'
        and all(p.get(k)is None for k in ('physical_repair','audit_context','review_reservation')),'original consumed review scope changed')
    from scripts.batch.harbor_model_review_provenance import Policy
    prepared=E.PreparedCall(p['request'],Policy(**p['policy']),E.SemanticScope(**p['scope']),ordinal,2,p['attempt_id'],tuple(p['budget_evidence']))
    require(binding(E.location(prepared,campaign)/'plan.json')==binding(Path(call.submitted['path']).parent/'plan.json'),'old review execution namespace changed')
    result=E.inspect(prepared,campaign)
    require(result.status=='completed' and result.call==call,'old review is not terminal with exact Call')
    return prepared,result

def load(b):
    from contextlib import ExitStack
    with ExitStack() as stack:return _load(b,stack)


def _load(b,stack):
    p=checked(b)
    if p.get('schema_version')=='fleet-rejected-reader-review/v1':
        adapter=module('selected_rejected_reader_context',p['context_helper'])
        return adapter.load(p,sys.modules[__name__])
    require(set(p)-{'operand_projection'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed source-review plan required')
    module('source_revision_stdlib',p['stdlib']).preload_inspect()
    cp=checked(p['semantic_context']['plan']);require(cp['helper']==p['semantic_context']['helper'] and cp['source']==p['source'],'original semantic context changed')
    M=module('source_revision_retained_context',p['semantic_context']['helper'])
    fresh,job,D,env=M.validated_authoring(cp);C,I,_,W,_,config,root,controls=env
    if cp.get('schema_version')=='fleet-retained-completed-review-tail/v1':
        require(M.SCHEMA==cp['schema_version'],'retained review consumer differs')
        stack.callback(M.close,D)
    require(p['workbook']==fresh['workbook'],'foreign selected review workbook')
    value,selection,lease=M.completed_failure(p,I,controls);worker,observed,_=I.selected(p['descriptor'])
    require(observed==controls and worker.root==root and worker.dialogue_semantic_retry is not None,'actual completed round-two worker required')
    require(p['tracker']==binding(worker.context.tracker_root/(fresh['workbook']+'.json')),'current tracker changed')
    current=checked(p['tracker']);require(current['bindings']['original_raw_source']==p['source'] and current['bindings']['source_file']==p['golden'],'review source scope changed')
    require(p['blocked']==binding(W.step_directory(worker,'dialogue-review')/'blocked.json'),'exact current second-round block required')
    W.active(worker,selection,lease)
    task=W.task(worker,selection,p['tracker']);producer=W.producer
    directory,claims,_,_=producer._dialogue(task,2,root)
    require(p['claims']==binding(directory/'claims.json') and p['draft']==binding(directory/'round-02/draft.md'),'original claims/draft identity changed')
    require(claims==checked(p['claims']),'original claim extraction changed')
    from scripts.batch import harbor_model_executor as E,harbor_continuation_allowance as A,harbor_dialogue_authoring as authors
    from scripts.batch.harbor_model_review_provenance import Call,validate_call,require_independent
    A=module('scripts.batch.harbor_continuation_allowance',p['allowance_module'])
    import scripts.batch as batch_package
    batch_package.harbor_continuation_allowance=A
    policy=A.validate(p['allowance'],expected_scope=A.scope(fresh['workbook'],p['source']['sha256']),at=time.time_ns())
    require(policy['authorization']['sha256']==AUTH and policy['limits']['review_attempts']>=3 and policy['limits']['transport_retries']==0,'explicit three-total zero-retry allowance required')
    v=producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue');prior=[];author_proofs=[]
    scope=dict(workbook_id=fresh['workbook'],original_sha256=p['source']['sha256'],lane=producer.DIALOGUE,budget_id='dialogue-review')
    for number in (1,2):
        call=Call(**checked(binding(directory/('review-call-%02d.json'%number))))
        prepared,result=historical_call(E,call,worker.campaign,scope,number)
        old_draft=(directory/('round-%02d/draft.md'%number)).read_text();request=checked(call.request)
        expected,prompt,_=producer.dialogue_review_context(task,claims,old_draft,number,root=root)
        require(request['messages']==[dict(role='system',content=prompt),dict(role='user',content=json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=False))] or (request['messages'][0]==dict(role='system',content=prompt) and json.loads(request['messages'][1]['content'])==expected and len(request['messages'])==2),'original complete reviewer context changed')
        proof=validate_call(call,request,policy=task.policy)
        saved=checked(binding(directory/('round-%02d/review.json'%number)))
        require(saved==proof['review'] and saved.get('round')==number and saved.get('passed') is False and v.review_faults(saved,claims,draft_text=old_draft),'actual rejected review required')
        pair=tuple(Call(**checked(binding(directory/(kind+'-call-%02d.json'%number))))for kind in ('writer','paraphraser'))
        ap=authors.verify_pair(pair,policy=task.policy,claims=claims,draft=old_draft,round=number,root=root,workbook=fresh['workbook'],original_sha256=p['source']['sha256'])
        require_independent(proof,ap);prior.append((prepared,result,proof));author_proofs.extend(ap)
    derivative=module('selected_dialogue_source_revision',p['revision'])
    require(binding(p['source']['path'])==p['source'] and binding(p['golden']['path'])==p['golden'],'source bytes changed')
    fixed,proof=derivative.repair(Path(p['draft']['path']).read_text(),claims,p['golden']['path'],checked(p['selection']))
    require(fixed==Path(p['candidate']['path']).read_text() and binding(p['candidate']['path'])==p['candidate'] and proof==checked(p['proof']),'source derivative or provenance changed')
    mechanics=v.check_draft(fixed,claims,task.stage,source_workbook=producer.dialogue_source_workbook(task))
    require(mechanics['passed'] is True,'source-derived candidate failed original mechanics')
    body,prompt,_=producer.dialogue_review_context(task,claims,fixed,2,root=root)
    if 'operand_projection'in p:
        operands=module('selected_additional_review_operands',p['operand_projection'])
        require('operand_evidence'in body and prompt.endswith(operands.GUIDANCE),'original reviewer operand policy differs')
        body['operand_evidence']=operands.project(claims,fixed,source=p['golden'],answer_key=binding(task.stage/'tests/answer_key.json'),task_dir=task.stage,root=root)
    body['round']=3;body['source_revision']=dict(proof=proof,original_review_rounds_consumed=2,derived_from_author_round=2,generated_author_calls_added=0,prior_review=checked(binding(directory/'round-02/review.json')))
    # Only the declared additional ordinal changes the response schema. Every
    # original accuracy, source operand, leakage and naturalness rule remains.
    prompt=prompt.replace('<1 or 2>','3')+'\nThis is explicitly authorized cumulative review round 3. Return round: 3. The draft is a separately declared deterministic derivative of author round 2, not a new author response. Independently review all claims and all source formulas. Recheck prior entailment findings; they can be wrong. The source_revision evidence proves a text transformation, not semantic approval. Do not waive accuracy, sign, range, coverage, leakage or naturalness requirements.'
    request=dict(model=task.policy.model,reasoning_effort=task.policy.reasoning_effort,messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False))])
    return p,E,A,worker,task,v,claims,prior,author_proofs,proof,mechanics,request

def probe(b):
    p,E,A,w,task,v,claims,prior,authors,proof,mechanics,request=load(b)
    require(not Path(p['home']).exists(),'third review namespace already consumed')
    return dict(status='SOURCE_DERIVATIVE_REVIEW_VALIDATED_NOT_STARTED',plan=b,original_consumed=2,next_ordinal=3,limit=3,claims=len(claims['claims']),changed_claims=len(proof['edits']),unchanged_claims=proof['unchanged_claims'],mechanics=mechanics,request_bytes=len(E.canonical(request)),models_dispatched=0,task_mutations=0,qualified=False)

def run(b):
    p,E,A,w,task,v,claims,prior,authors,proof,mechanics,request=load(b)
    from scripts.fleet import model_gate
    require(model_gate.configured(),'owning central custody required')
    client,owner,_=model_gate.context();require(owner['workbook']==p['workbook'] and owner['owner']==p['owner'] and owner['fence']==p['fence'],'foreign review owner')
    client.call('heartbeat',**owner)
    home=E.safe(p['home']);require(not home.exists(),'review already attempted; reconcile without replay');home.mkdir(parents=True)
    E.save(home/'intent.json',dict(schema_version=SCHEMA,plan=b,automatic_retry=False,task_mutations=0))
    records=[x[1].outcome for x in prior]
    def closed(row,scope,ordinal):
        old,result,_=prior[ordinal-1];return row==result.outcome and scope==asdict(old.scope) and E.inspect(old,w.campaign).call==result.call
    ledger=Path(p['allowance']['path']).parent/'review-reservations';ledger.mkdir(exist_ok=True)
    reservation=A.reserve_review(ledger,allowance=p['allowance'],semantic_scope=asdict(prior[0][0].scope),prior_records=records,original_limit=2,expected_ordinal=3,prove_closed=closed,cumulative_limit=3)
    E.save(home/'reservation.json',reservation);E.save(home/'request.json',request)
    prepared=E.PreparedCall(binding(home/'request.json'),task.policy,prior[0][0].scope,3,3,str(uuid.uuid4()),tuple([b,p['proof'],p['allowance'],reservation,*records]),review_reservation=reservation)
    E.save(home/'prepared.json',asdict(prepared))
    from xl_task_build import read_env_key
    os.environ['HARBOR_MODEL_SLOTS']=str(w.campaign.model_slots);os.environ['HARBOR_MODEL_SLOT_ROOT']=str(w.campaign.slot_root)
    result=E.execute(prepared,w.campaign,credential=read_env_key(Path(p['env_file'])))
    require(result.status=='completed' and result.call is not None,'third transport unresolved; inspect without replay')
    after=load(b);require(after[-1]==request and after[-3]==proof,'source context changed during review')
    from scripts.batch.harbor_model_review_provenance import validate_call,require_independent
    actual=validate_call(result.call,request,policy=task.policy);require_independent(actual,[*authors,*(x[2]for x in prior)])
    review=actual['review'];require(type(review)is dict and review.get('round')==3,'third review returned wrong ordinal')
    faults=v.review_faults(review,claims,draft_text=Path(p['candidate']['path']).read_text())
    status='SOURCE_DERIVATIVE_REVIEW_PASSED_NOT_ADOPTED' if not faults and v.review_passed(review,claims) else 'SOURCE_DERIVATIVE_REVIEW_REJECTED'
    E.save(home/'result.json',dict(status=status,plan=b,call=asdict(result.call),outcome=result.outcome,reservation=reservation,review=review,faults=faults,mechanics=mechanics,prior_consumed=2,cumulative_consumed=3,task_adopted=False,qualified=False))
    print(json.dumps(dict(status=status,result=binding(home/'result.json'),qualified=False)))

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();b=dict(path=v.plan,sha256=v.sha256)
    if v.action=='probe':print(json.dumps(probe(b)))
    else:run(b)
if __name__=='__main__':main()
