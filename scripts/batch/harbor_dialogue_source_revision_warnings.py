"""Source and sealed-Call proof for presentation findings on a reviewed derivative.

No rejected review is rewritten. This read-only proof grants no attempt, lease,
continuation, or publication. The source correction has its own explicit lineage.
"""
from dataclasses import asdict
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import sys

SCHEMA='harbor-dialogue-source-revision-warning/v1'
STYLE='The long sequence of isolated source-link exchanges repeatedly cycles through the same junior-question and senior-answer patterns, so the file reads like a generated source catalogue or checklist rather than a Slack conversation.'
SLOTS='The slot comments before claims 013 and 014 expose drafting instructions and make the file read like documentation rather than natural deal-team chat.'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical warning input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(binding(row['path'])==row,'warning input changed')
    return json.loads(Path(row['path']).read_bytes())


def module(row):
    require(binding(row['path'])==row,'warning reader changed')
    name='source_revision_warning_'+row['sha256'][:16]
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m


def blocks(text):
    matches=list(re.finditer(r'<!-- claim:(.*?) -->',text,re.S))
    require(matches and not text[:matches[0].start()].strip(),'claim preamble differs')
    result=[(m[1],text[m.end():matches[i+1].start()if i+1<len(matches)else len(text)])for i,m in enumerate(matches)]
    require(len({x[0]for x in result})==len(result),'duplicate claim blocks')
    return result


def classify(review,claims,text,template,original,validator):
    """Recognize only exact findings, proving their alleged changes are frozen."""
    rows=claims['claims'];ids=[r['record_id']for r in rows]
    require(not claims.get('shared_source_links'),'individual source claims required')
    require(set(review)=={'agent_model','round','accuracy','naturalness','passed'}and review['round']==3
        and review['passed']is False,'exact third rejected review required')
    a=review['accuracy'];checks=a['claims']
    require(set(a)=={'verdict','claims','extras','cell_refs_in_senior_turns'}and a['verdict']=='fail'
        and a['extras']==[]and a['cell_refs_in_senior_turns']==[],'unexpected accuracy findings')
    require([r['record_id']for r in checks]==ids and len(ids)==len(set(ids)),'complete ordered entailment required')
    frozen=dict(blocks(template));before=dict(blocks(original));after=dict(blocks(text))
    require(list(frozen)==list(before)==list(after)==ids,'claim block identity differs')
    mapped,faults=validator.map_turns_to_claims(validator.parse_turns(text),rows)
    require(not faults and all(len(mapped.get(i,[]))==2 for i in ids),'complete two-turn claim mapping required')
    warnings=[]
    for claim,check in zip(rows,checks):
        require(set(check)=={'record_id','must_say','findings'}and check['must_say']=='entailed'
            and isinstance(check['findings'],list),'substantive or malformed claim finding')
        for finding in check['findings']:
            match=re.fullmatch(r'The frozen junior_scope header is altered by inserting "([^"\n]+):" before the required scoped-cell prefix\.',finding)
            require(match is not None and match[1]==claim['sheet'],'unrecognized scope finding')
            rid=claim['record_id'];prefix=next((x for x in frozen[rid].splitlines()if x.startswith('**')),None)
            scope=validator.junior_scope_text(claim)
            require(prefix is not None and prefix.split('** ',1)[-1]==claim['sheet']+': '+scope+':','prefix is not frozen source scope')
            for value in(before[rid],after[rid]):
                actual=next((x for x in value.splitlines()if x.startswith('**')),None)
                require(actual is not None and actual.startswith(prefix),'junior prefix actually changed')
            warnings.append(dict(code='frozen_junior_prefix',record_id=rid,finding=finding,template_prefix=prefix))
    require(warnings,'explicit frozen-prefix evidence required')
    require(review['naturalness']==dict(verdict='fail',findings=[STYLE,SLOTS]),'unknown substantive or style finding')
    require(sum(r.get('reviewer_only',{}).get('entry')=='source_selection'for r in rows)>=24,'documentary finding lacks source-selection sequence')
    actual_slots=[]
    for index,(rid,chunk)in enumerate(after.items(),1):
        for comment in re.findall(r'<!-- slot:.*?-->',chunk,re.S):
            require(index in(13,14)and comment in frozen[rid]and comment in before[rid], 'slot is not retained frozen metadata')
            require(re.fullmatch(r'<!-- slot:c%03d-junior \| ask how to build .+; prose only, no new modelling facts -->'%index,comment,re.S)is not None,'unknown slot metadata')
            require(validator.parse_turns(chunk)==validator.parse_turns(chunk.replace(comment,'')), 'slot changes visible conversation')
            actual_slots.append(dict(claim=index,record_id=rid,comment=comment))
    require([r['claim']for r in actual_slots]==[13,14],'exact two retained slot comments required')
    warnings.extend([dict(code='dialogue_style_only',finding=STYLE),dict(code='hidden_frozen_scaffold',finding=SLOTS,slots=actual_slots)])
    return dict(claim_count=len(rows),entailed=len(checks),warnings=warnings)


def inspect(profile,result):
    p=read(profile);r=read(result)
    require(p['schema_version']=='fleet-rejected-reader-review/v1'and r['plan']==profile
        and result==binding(Path(p['home'])/'result.json')and r['status']=='SOURCE_DERIVATIVE_REVIEW_REJECTED'
        and r['prior_consumed']==2 and r['cumulative_consumed']==3 and r['task_adopted']is False
        and r['qualified']is False,'actual completed third derivative rejection required')
    caller=module(p['helper'])
    selected,E,A,w,task,validator,claims,prior,authors,derivative,mechanics,request=caller.load(profile)
    require(selected==p and mechanics==r['mechanics']and mechanics['passed']is True,'original source mechanics differ')
    # load() reproduces the exact original two reviews, writer pairs, source AST,
    # sole SUM edit, all untouched claims, and full third-review operand request.
    execution=caller.module('selected_warning_third_executor',p['executor'])
    from scripts.batch.harbor_model_review_provenance import Call,Policy,validate_call,require_independent
    saved=read(binding(Path(p['home'])/'prepared.json'))
    prepared=execution.PreparedCall(saved['request'],Policy(**saved['policy']),execution.SemanticScope(**saved['scope']),saved['ordinal'],saved['limit'],saved['attempt_id'],tuple(saved['budget_evidence']),review_reservation=saved['review_reservation'])
    require(saved['ordinal']==saved['limit']==3 and saved['policy']==asdict(task.policy)
        and saved['scope']==asdict(prior[0][0].scope)and prepared.review_reservation==r['reservation']
        and profile in prepared.budget_evidence,'third review authority changed')
    reservation=read(r['reservation']);require(reservation['prior_records']==[x[1].outcome for x in prior]
        and reservation['allowance']==p['allowance']and reservation['original_limit']==2,'three consumed review history differs')
    A.validate_review_reservation(r['reservation'],saved['scope'],3,3)
    observed=execution.inspect(prepared,w.campaign);call=Call(**r['call'])
    require(observed.status=='completed'and observed.call==call and observed.outcome==r['outcome']
        and read(call.request)==request,'third sealed Call or full source request differs')
    actual=validate_call(call,request,policy=task.policy);require_independent(actual,[*authors,*(x[2]for x in prior)])
    require(actual['review']==r['review'],'review interpretation differs')
    selection=read(p['reader_selection']);revision=read(selection['revision'])
    original=Path(revision['original_draft']['path']);template=original.parent.parent/'draft_template.md'
    for row in(revision['claims'],revision['original_draft'],revision['corrected_draft'],revision['source']):require(binding(row['path'])==row,'source derivative input changed')
    require(read(revision['claims'])==claims and revision['corrected_draft']==p['candidate'],'selected derivative claims differ')
    classified=classify(r['review'],claims,Path(p['candidate']['path']).read_text(),template.read_text(),original.read_text(),validator)
    require(classified['claim_count']==derivative['unchanged_claims']+len(derivative['edits']),'source correction coverage differs')
    return dict(schema_version=SCHEMA,profile=profile,result=result,source=p['source'],candidate=p['candidate'],
        template=binding(template),claims=revision['claims'],source_proof=p['proof'],derivative=derivative,
        **classified,mechanics=mechanics,review_call=asdict(call),review_outcome=r['outcome'],
        reservation=r['reservation'],original_review_outcomes=[x[1].outcome for x in prior],
        original_review_preserved=True,models_dispatched=0,task_mutations=0,qualified=False)


def revalidate(proof,review_payload,claims_payload,draft_text,*,root):
    """Recheck presentation evidence against exact current validator inputs.

    This does not cache or replace author/Call/source-history validation. The
    owning derivative-history reader remains responsible for those gates.
    """
    require(proof.get('schema_version')==SCHEMA,'foreign derivative warning proof')
    p=read(proof['profile']);r=read(proof['result'])
    require(p['schema_version']=='fleet-rejected-reader-review/v1'
        and r['plan']==proof['profile']and r['review']==review_payload
        and r['call']==proof['review_call']and r['outcome']==proof['review_outcome']
        and r['reservation']==proof['reservation']and r['mechanics']==proof['mechanics']
        and r['status']=='SOURCE_DERIVATIVE_REVIEW_REJECTED'
        and r['prior_consumed']==2 and r['cumulative_consumed']==3
        and not r['task_adopted']and not r['qualified'],'warning review lineage differs')
    selected=read(p['reader_selection']);revision=read(selected['revision'])
    snapshot=read(selected['snapshot']);original_root=Path(snapshot.get('snapshot',snapshot)['root'])
    require(Path(root).resolve()==original_root.resolve(),'warning validator root differs')
    require(proof['source']==p['source']==selected['source']
        and proof['source_proof']==p['proof']==selected['revision']
        and proof['claims']==revision['claims']and read(proof['claims'])==claims_payload
        and proof['candidate']==p['candidate']==revision['corrected_draft']
        and binding(p['candidate']['path'])==p['candidate']
        and Path(p['candidate']['path']).read_text()==draft_text,'warning source inputs differ')
    for row in (p['source'],revision['source'],revision['original_draft'],proof['template']):
        require(binding(row['path'])==row,'warning frozen evidence changed')
    original=Path(revision['original_draft']['path'])
    require(proof['template']==binding(original.parent.parent/'draft_template.md')
        and proof['original_review_preserved']is True and proof['models_dispatched']==0
        and proof['task_mutations']==0 and proof['qualified']is False,'warning authority differs')
    from scripts.batch import harbor_instruction_producer as producer
    validator=producer.skill_module(original_root,'additional-assumptions-dialogue','validate_dialogue')
    actual=classify(review_payload,claims_payload,draft_text,
        Path(proof['template']['path']).read_text(),original.read_text(),validator)
    require(all(proof[k]==v for k,v in actual.items()),'warning classification changed')
    return proof


def main():
    import argparse
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);args=a.parse_args()
    selected=dict(path=args.plan,sha256=args.sha256);p=read(selected)
    require(set(p)=={'schema_version','helper','profile','result'}and p['schema_version']=='harbor-dialogue-source-revision-warning-selection/v1'
        and p['helper']==binding(__file__),'closed source warning selection required')
    print(json.dumps(inspect(p['profile'],p['result']),sort_keys=True))

if __name__=='__main__':main()
