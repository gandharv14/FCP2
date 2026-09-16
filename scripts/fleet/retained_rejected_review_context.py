"""Isolated historical context for one cumulative review after consumer failure.

The old reader runs in a cold interpreter. Its failed step and expired lease
remain unchanged; a new centrally fenced worker owns only the additional review.
"""
from pathlib import Path
from dataclasses import asdict
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from types import SimpleNamespace

SCHEMA = 'fleet-rejected-reader-review/v1'
FIELDS = {'schema_version','helper','context_helper','reader_selection','source','workbook',
          'proof','candidate','allowance','allowance_module','executor','home','owner','fence',
          'env_file','python'}


def require(ok,message):
    if not ok:raise ValueError(message)


def module(name,row):
    from scripts.fleet.migration import binding
    require(binding(row['path'])==row,'bound rejected-context code changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def cold_payload(selection):
    """Run only in a fresh process, so historical imports cannot mix with runtime."""
    # Deliberately load the reader by path before importing the current package.
    import hashlib
    def read(b):
        raw=Path(b['path']).read_bytes();require(hashlib.sha256(raw).hexdigest()==b['sha256'],'cold selection changed');return json.loads(raw)
    p=read(selection);helper=p['helper'];require(Path(helper['path']).is_file(),'missing reader helper')
    require(hashlib.sha256(Path(helper['path']).read_bytes()).hexdigest()==helper['sha256'],'reader helper changed')
    sp=importlib.util.spec_from_file_location('selected_rejected_reader',helper['path']);R=importlib.util.module_from_spec(sp);sp.loader.exec_module(R)
    inspection=R.inspect(selection)
    rejected=R.read(p['rejection']);revision=R.read(p['revision']);reader=R.module(p['reader'])
    root,base,C,W,P,Q,codec,w,s,lease=reader.setup()
    permit=R.read(rejected['preparation']);task=W.task(w,s,permit['tracker']);directory=Path(rejected['draft']['path']).parents[1]
    claims=R.read(rejected['claims']);fixed=Path(revision['corrected_draft']['path']).read_text()
    from scripts.batch.harbor_model_review_provenance import Call,validate_call,require_independent
    from scripts.batch import harbor_dialogue_authoring as authors
    calls=[];proofs=[];author_proofs=[]
    for ordinal in (1,2):
        call=Call(**(R.read(R.binding(directory/'review-call-01.json')) if ordinal==1 else rejected['call']))
        request=R.read(call.request);proof=validate_call(call,request,policy=task.policy)
        original=(directory/('round-%02d/draft.md'%ordinal)).read_text()
        body,prompt,_=P.dialogue_review_context(task,claims,original,ordinal,root=root)
        require(len(request['messages'])==2 and request['messages'][0]==dict(role='system',content=prompt)
                and json.loads(request['messages'][1]['content'])==body,'original complete review context differs')
        pair=tuple(Call(**R.read(R.binding(directory/(kind+'-call-%02d.json'%ordinal))))for kind in('writer','paraphraser'))
        identity=P.task_context(task)[1]
        if ordinal==1 and w.dialogue_pre_review_repair is not None:
            from scripts.batch import harbor_dialogue_pre_review_repair as prior_repair
            prior_repair.validate_review_call(w.dialogue_pre_review_repair,call)
            author=prior_repair.verify_pair(w.dialogue_pre_review_repair,pair,policy=task.policy,
                 claims=claims,draft=original,round=ordinal,root=root,workbook=p['workbook'],
                 original_sha256=identity['original']['sha256'])
        else:
            author=authors.verify_pair(pair,policy=task.policy,claims=claims,draft=original,round=ordinal,
                                      root=root,workbook=p['workbook'],original_sha256=identity['original']['sha256'])
        require_independent(proof,author)
        v=P.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
        review=proof['review'];require(review.get('round')==ordinal and review.get('passed')is False
                                      and v.review_faults(review,claims,draft_text=original),'actual rejected review required')
        calls.append(asdict(call));proofs.append(proof);author_proofs.extend(author)
    body,prompt,_=P.dialogue_review_context(task,claims,fixed,2,root=root)
    require(body.get('operand_evidence')==R.read(revision['operand_evidence']),
            'complete corrected source operand evidence differs from retained proof')
    derivative=dict(schema_version='retained-source-sum-revision/v1',original_proof=p['revision'],
                    rejected_reader=p['rejection'],edits=[dict(record_id=revision['record_id'],
                    before=revision['old_sentence'],after=revision['new_sentence'])],
                    unchanged_claims=revision['unchanged_count'],independent_review_pass=False)
    body['round']=3;body['source_revision']=dict(proof=derivative,original_review_rounds_consumed=2,
          derived_from_author_round=2,generated_author_calls_added=0,prior_review=rejected['review'])
    prompt=prompt.replace('<1 or 2>','3')+'\nThis is explicitly authorized cumulative review round 3. Return round: 3. Independently review the complete corrected draft and every source formula. Original author round 2 and both rejected reviews remain historical evidence. The source_revision is a deterministic postprocessor proof, not semantic approval. Do not waive accuracy, sign, range, coverage, leakage or naturalness requirements.'
    request=dict(model=task.policy.model,reasoning_effort=task.policy.reasoning_effort,messages=[
          dict(role='system',content=prompt),dict(role='user',content=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False))])
    return dict(inspection=inspection,selection=selection,policy=asdict(task.policy),campaign=asdict(w.campaign),
                claims=claims,root=str(root),calls=calls,review_proofs=proofs,author_proofs=author_proofs,
                derivative=derivative,mechanics=inspection['correction']['mechanics'],request=request)


def historical_executor(E):
    """Read local pre-fleet calls under their exact original transport identity.

    The selected executor still validates every request and budget, then its
    sealed reader authenticates the original journal under the original lock.
    Only completed original rounds 1/2 may use this read-only projection.
    """
    def inspect(prepared,campaign):
        require(prepared.ordinal in (1,2) and prepared.limit==2
                and prepared.review_reservation is None and prepared.physical_repair is None
                and prepared.audit_context is None,'only original completed review reads permitted')
        selected,_,request,_=E.prepared_plan(prepared,campaign)
        root=E.location(prepared,campaign)
        saved=E.strict_json((root/'plan.json').read_bytes())
        expected=dict(selected);expected.pop('fleet_transport_policy',None)
        expected['max_transport_retries']=E.MAX_RETRIES
        require(saved==expected and 'fleet_transport_policy'not in saved,
                'original local execution identity changed')
        with E.scope_lock(root.parent,create=False):result=E.inspect_locked(root,saved,request)
        require(result.status=='completed' and result.call is not None,
                'original local review not sealed complete')
        return result
    values=dict(vars(E));values['inspect']=inspect
    return SimpleNamespace(**values)


def require_review_reservation_api(allowance_module):
    """Validate the exact upcoming call without reserving or touching a ledger."""
    try:
        inspect.signature(allowance_module.reserve_review).bind(
            None, allowance=None, semantic_scope=None, prior_records=None,
            original_limit=2, expected_ordinal=3, prove_closed=None,
            cumulative_limit=3)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('selected allowance lacks cumulative review reservation API') from exc


def load(p,caller):
    from scripts.fleet.migration import binding,checked
    require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(caller.__file__)
            and p['context_helper']==binding(__file__),'closed rejected-reader review required')
    selected=checked(p['reader_selection']);rev=checked(selected['revision'])
    require(selected['workbook']==p['workbook'] and selected['source']==p['source']
            and selected['revision']==p['proof'] and rev['corrected_draft']==p['candidate'],
            'foreign retained source correction')
    A=module('scripts.batch.harbor_continuation_allowance',p['allowance_module'])
    require_review_reservation_api(A)
    # No credentials are loaded in the historical reader process.
    script='import importlib.util,json,sys;from pathlib import Path;p=Path(sys.argv[1]);s=importlib.util.spec_from_file_location("cold_review",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps(m.cold_payload(json.loads(sys.argv[2])),default=str))'
    historical_env=dict(os.environ);historical_env.pop('HARBOR_FLEET_URL',None)
    historical_env['HARBOR_FLEET_CONTINUATION_READ_ONLY']='1'
    q=subprocess.run([p['python'],'-I','-B','-c',script,__file__,json.dumps(p['reader_selection'])],
                     capture_output=True,text=True,timeout=1500,env=historical_env)
    require(q.returncode==0,'historical rejected review revalidation failed: '+q.stderr[-1600:])
    payload=json.loads(q.stdout)
    require(payload['selection']==p['reader_selection'] and payload['inspection']['canonical_worker_checkpoint']is False,
            'historical reader cannot fabricate a checkpoint')
    E=historical_executor(module('selected_rejected_review_executor',p['executor']))
    import scripts.batch as batch_package
    batch_package.harbor_continuation_allowance=A
    policy=A.validate(p['allowance'],expected_scope=A.scope(p['workbook'],p['source']['sha256']))
    require(policy['authorization']['sha256']==caller.AUTH and policy['limits']['review_attempts']==3
            and policy['limits']['transport_retries']==0,'exact three-total zero-retry allowance required')
    from scripts.batch.harbor_model_review_provenance import Policy,Call,validate_call
    campaign=E.Campaign(Path(payload['campaign']['root']),Path(payload['campaign']['slot_root']),payload['campaign']['model_slots'])
    scope=dict(workbook_id=p['workbook'],original_sha256=p['source']['sha256'],lane='harbor-dialogue-author',budget_id='dialogue-review')
    prior=[]
    for ordinal,raw in enumerate(payload['calls'],1):
        call=Call(**raw);prepared,result=caller.historical_call(E,call,campaign,scope,ordinal)
        actual=validate_call(call,checked(call.request),policy=Policy(**payload['policy']))
        require(actual==payload['review_proofs'][ordinal-1],'completed review interpretation changed')
        prior.append((prepared,result,actual))
    from scripts.batch import harbor_instruction_producer as producer
    validator=producer.skill_module(Path(payload['root']),'additional-assumptions-dialogue','validate_dialogue')
    worker=SimpleNamespace(campaign=campaign)
    task=SimpleNamespace(policy=Policy(**payload['policy']))
    return p,E,A,worker,task,validator,payload['claims'],prior,payload['author_proofs'],payload['derivative'],payload['mechanics'],payload['request']
