"""Exact numeric OOXML citation correction; no model replay or changed verdict."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal,InvalidOperation
import argparse,inspect,json,math,sys
from pathlib import Path


def citation_matches(value, cell):
    expected=cell.get('value')
    if type(value) is type(expected) and value==expected:return True
    # Excel numeric cells are retained lexically from XML. JSON reviewer numbers
    # may represent the exact same decimal; bools and text cells are not numbers.
    if cell.get('type')!='n' or not isinstance(expected,str) or type(value) not in (int,float):return False
    if type(value)is float and not math.isfinite(value):return False
    try:
        left,right=Decimal(str(value)),Decimal(expected)
        return left.is_finite() and right.is_finite() and left==right
    except (InvalidOperation,ValueError):return False


@contextmanager
def installed(R,root):
    sys.path.insert(0,str(root))
    from scripts.batch import harbor_response_guidance as G
    R.require(Path(G.__file__).resolve().is_relative_to(root),'foreign numeric citation source verifier')
    original=G.qualify
    source=inspect.getsource(original)
    old='and ref["value"] == source[ref["sheet"]][ref["cell"]]["value"],'
    new='and _exact_numeric_citation(ref["value"], source[ref["sheet"]][ref["cell"]]),'
    R.require(source.count(old)==1,'unrecognized original citation comparison')
    scope=dict(G.__dict__);scope['_exact_numeric_citation']=citation_matches
    exec(compile(source.replace(old,new),G.__file__,'exec'),scope)
    G.qualify=scope['qualify']
    try:yield G
    finally:G.qualify=original


def runtime(plan):
    import importlib.util,hashlib
    row=plan['response_helper'];path=Path(row['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('response helper changed')
    spec=importlib.util.spec_from_file_location('numeric_original_response_helper',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m


def requalify(bound):
    plan=json.loads(Path(bound['path']).read_text());R=runtime(plan);R.checked(bound)
    R.require(set(plan)=={'schema_version','helper','response_helper','code_root','review_plan','previous_result','output'} and plan['schema_version']=='harbor-exact-numeric-review-requalification/v1' and plan['helper']==R.binding(__file__),'closed numeric citation correction plan required')
    old=R.checked(plan['previous_result']);original=R.checked(plan['review_plan']);directory=Path(original['directory']);out=Path(plan['output'])
    R.require(old['status']=='RECONCILIATION_REQUIRED' and old['failure_type']=='ValueError' and old['plan']==plan['review_plan'] and plan['previous_result']==R.binding(directory/'result.json'),'exact original consumed rejection required')
    R.require(out==directory.parent/'response-review-numeric-correction' and not out.exists(),'one immutable citation correction required')
    ready,job,D,env,value,request=R.owning_ready(original['ready'],plan['code_root']);content=request.selection.content
    from scripts.batch import harbor_response_guidance as G
    index=R.checked(original['guidance_index']);ctx=index['contexts'][content.workbook];context=G.validate_context(ctx)
    R.require(context['source']['sha256']==content.bindings['original_raw_source']['sha256'],'original source differs')
    author_request=R.checked(content.naturalization_writer_request);body=json.loads(author_request['messages'][1]['content'])
    R.require(body['private_response_guidance']['context_binding']==ctx and body['private_response_guidance']['index_binding']==original['guidance_index'],'actual author guidance differs')
    authors=[(content.naturalization_writer_call,author_request,content.model_policy)]
    authors += [(c,R.checked(c.request),content.model_policy)for c in request.selection.semantic.dialogue_writer_calls]
    call_binding=R.binding(directory/'call.json');call=G.Call(**R.checked(call_binding));instruction=content.stage/'instruction.md';key=content.stage/'tests/answer_key.json'
    try:G.qualify(ctx,instruction,key,review_call=call,author_calls=authors,policy=content.model_policy)
    except ValueError as exc:R.require(str(exc)=='review source citation differs from workbook','original hold is not the numeric citation comparison')
    else:raise ValueError('original citation gate did not reject; correction unnecessary')
    with installed(R,Path(plan['code_root']))as fixed:
        receipt=fixed.qualify(ctx,instruction,key,review_call=call,author_calls=authors,policy=content.model_policy)
        receipt_binding=R.retain(out/'qualification.json',receipt)
        fixed.revalidate(receipt_binding,instruction=instruction,answer_key=key,task_root=content.stage)
    correction=dict(helper=plan['helper'],plan=bound,original_rejection=plan['previous_result'],consumed_call=call_binding,model_calls=0,numeric_comparison='exact finite Decimal equality only for OOXML numeric cells; no tolerance')
    return R.retain(out/'result.json',dict(schema_version='harbor-response-review-result/v1',status='PUBLICATION_READY',response_review_status='QUALIFIED',workbook=content.workbook,plan=plan['review_plan'],ready=original['ready'],qualification=receipt_binding,call=call_binding,citation_correction=correction,publication_performed=False,automatic_retry=False))


def check_correction(R,result):
    edge=result['citation_correction'];plan=R.checked(edge['plan'])
    R.require(edge['helper']==plan['helper']==R.binding(__file__) and plan['response_helper']==R.binding(R.__file__) and plan['review_plan']==result['plan'] and plan['previous_result']==edge['original_rejection'] and edge['consumed_call']==result['call'] and edge['model_calls']==0,'exact consumed-call correction lineage required')
    previous=R.checked(edge['original_rejection']);R.checked(result['call'])
    R.require(previous['status']=='RECONCILIATION_REQUIRED' and previous['failure_type']=='ValueError' and previous['plan']==result['plan'],'original rejected result changed')
    return plan


def publication_gate(R,*args,**kwargs):
    import os
    profile=R.checked(dict(path=os.environ['FCP_RESPONSE_PUBLICATION_PROFILE'],sha256=os.environ['FCP_RESPONSE_PUBLICATION_PROFILE_SHA256']))
    result=R.checked(profile['review_result']);plan=check_correction(R,result)
    with installed(R,Path(plan['code_root'])):return R.publication_gate(*args,**kwargs)


def evidence_files(R,bound):
    result=R.checked(bound);check_correction(R,result);rows=R.evidence_files(bound)
    rows += [result['citation_correction']['plan'],result['citation_correction']['original_rejection'],R.binding(__file__)]
    return list({r['path']:r for r in rows}.values())


def build_publisher(bound):
    p=json.loads(Path(bound['path']).read_text());R=runtime(p);R.checked(bound)
    R.require(set(p)=={'schema_version','helper','response_helper','prior_publisher','output'} and p['schema_version']=='harbor-exact-numeric-publication-adapter/v1' and p['helper']==R.binding(__file__),'closed corrected publication adapter required')
    R.require(R.binding(p['prior_publisher']['path'])==p['prior_publisher'],'prior publisher changed')
    source=Path(p['prior_publisher']['path']).read_text();old='    return adapter.publication_gate(*args, **kwargs)'
    new='    correction = load("selected_exact_numeric_citation", '+repr(p['helper'])+')\n    return correction.publication_gate(adapter, *args, **kwargs)'
    R.require(source.count(old)==1,'unrecognized strict response publication gate')
    out=Path(p['output']);out.parent.mkdir(parents=True,exist_ok=True);source=source.replace(old,new);compile(source,str(out),'exec')
    with out.open('x')as f:f.write(source)
    return R.retain(out.with_suffix('.provenance.json'),dict(plan=bound,publisher=R.binding(out),only_change='exact numeric citation gate; all native and response publication checks preserved'))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('requalify','build-publisher'));p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);a=p.parse_args();print(json.dumps(globals()[a.action.replace('-','_')](dict(path=a.plan,sha256=a.plan_sha256))))
if __name__=='__main__':main()
