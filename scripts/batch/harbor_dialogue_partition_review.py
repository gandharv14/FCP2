"""Pure bounded dialogue author/reviewer Calls and truthful global reconciliation.

The root controller reserves rounds and prepares/dispatches requests. This module
never emits a synthetic Call, writes artifacts, applies text, or changes budgets.
"""
from dataclasses import asdict
from pathlib import Path
import json
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_model_review_provenance import Call,validate_call,require_independent
from scripts.batch.harbor_readiness import skill_module
from scripts.batch import harbor_model_executor as executor
from scripts.batch import harbor_dialogue_partition_plan as D
ROOT=Path(__file__).resolve().parents[2]
AUTHORITY='harbor-dialogue-partition-round/v1'
AGGREGATE='harbor-dialogue-partition-aggregate/v1'


def _reservation(binding,a,*,root):
    """Read the permanent owning reservation; never import its controller."""
    continuation=read(a['continuation']);run=executor.safe(continuation['worker_run']);home=run/'dialogue-partition'
    require(continuation.get('schema_version')=='harbor-dialogue-partition-continuation/v1','foreign dialogue continuation')
    require(a['continuation']==record(home/'permit.json'),'foreign dialogue continuation namespace')
    marker=read(record(run/'dialogue-partition-continuation.json'))
    require(marker==dict(schema_version='harbor-dialogue-partition-continuation/v1',permit=a['continuation']),'permanent dialogue reservation missing or changed')
    directory=home/('round-%02d'%a['global_round'])
    require(binding==record(directory/'authority.json') and a['plan']==record(home/'round-01/plan.json'),'noncanonical dialogue round or plan namespace')
    require(a['initial']==continuation['initial']==record(run/'initial.json'),'dialogue initial identity changed')
    require(continuation['root']==str(Path(root)) and a['implementation']==continuation['implementation']==record(Path(root)/'scripts/batch/harbor_dialogue_partition_continuation.py'),'dialogue reservation implementation changed')
    for key in ('workbook','original_source','source','global_limit'):
        require(a[key]==continuation[key],'dialogue reservation source or budget changed')
    require(a['tracker']==record(directory/'tracker-before.json'),'dialogue round tracker namespace changed')
    before=read(a['tracker'])
    from scripts.batch.harbor_tracker import validate_tracker
    from scripts.batch.harbor_tracker_lifecycle import validate_lifecycle
    validate_tracker(before);validate_lifecycle(before)
    require(before['workbook_id']==a['workbook'] and before['bindings']['source_file']==a['source'] and before['bindings']['original_raw_source']==a['original_source'],'dialogue round tracker source differs')
    attempt=before['lane_state']['harbor-dialogue-author']['attempt']
    require(type(attempt) is int and (attempt==0 if a['global_round']==1 else 0<=attempt<=1),'dialogue global round cannot reset consumed attempt')
    predecessor=read(a['predecessor'])
    require(Path(a['predecessor']['path']).is_relative_to(run) and predecessor.get('step')=='dialogue-init' and predecessor.get('status')=='completed','dialogue round lacks owning completed initialization')
    require(predecessor['selection']==a['selection'] and predecessor['lease']==a['lease'],'dialogue reservation detached from selected checkpoint')
    if a['global_round']==2:
        require(read(a['previous_round'])['authority']==record(home/'round-01/authority.json'),'remaining round detached from canonical first round')
    return continuation


def selected(binding,policy,*,root=ROOT):
    a=read(binding)
    require(a.get('schema_version')==AUTHORITY and a.get('global_limit')==2 and type(a.get('global_round')) is int and a['global_round'] in (1,2),'closed global dialogue round required')
    require(a['policy']==asdict(policy),'dialogue partition policy changed')
    _reservation(binding,a,root=root)
    for b in (a['plan'],a['original_source'],a['source'],a['answer_key'],a['initial']):executor.checked_bytes(b)
    p=read(a['plan']);require(set(p)=={'claims','projection'},'closed bound dialogue plan required')
    D.validate(p['projection'],p['claims'],root=root)
    require(p['claims']['workbook']==a['workbook'],'partition workbook identity changed')
    require(Path(a['answer_key']['path'])==Path(a['task_dir'])/'tests/answer_key.json','authority answer key is not the original stage key')
    if a.get('scope_evidence') is not None:executor.checked_bytes(a['scope_evidence'])
    if a['global_round']==1:require(a.get('previous_round') is None,'first round has foreign history')
    else:
        old=read(a['previous_round']);require(old['schema_version']==AGGREGATE and old['round']==1 and old['passed'] is False,'remaining round needs a rejected complete prior round')
        previous=read(old['authority']);require(previous['plan']==a['plan'] and previous['original_source']==a['original_source'] and previous['initial']==a['initial'] and previous['source']==a['source'] and previous['answer_key']==a['answer_key'],'remaining round changed original plan or source')
        # Full completed prior Call validation is done by aggregate(), below.
    return a,p


def scope(a,kind,index):
    require(kind in ('writer','paraphraser','review') and type(index) is int and index>=0,'invalid partition kind/index')
    return executor.SemanticScope(a['workbook'],a['original_source']['sha256'],'harbor-dialogue-author','dialogue-partition-round-%02d-%s-%04d'%(a['global_round'],kind,index+1))


def _part(p,index):
    require(type(index) is int and 0<=index<len(p['projection']['partitions']),'partition index outside frozen plan')
    return p['projection']['partitions'][index]


def _payload(body,prompt,policy,maximum):
    content=json.dumps(body,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True)
    require(len(content.encode())<=maximum,'dialogue partition request exceeds explicit byte bound')
    return dict(model=policy.model,reasoning_effort=policy.reasoning_effort,messages=[dict(role='system',content=prompt),dict(role='user',content=content)])


def author_payload(a,p,index,kind,policy,*,root=ROOT,incoming=None):
    from scripts.batch import harbor_dialogue_authoring as author
    require(kind in ('writer','paraphraser'),'unknown author role');part=_part(p,index)
    body=author.author_body(part['template'],part['slots'],part['claims']['writer_pack'],kind,incoming=incoming)
    body['partition_context']=dict(plan_sha256=a['plan']['sha256'],global_round=a['global_round'],global_limit=2,index=index,claim_ids=part['claim_ids'],global_slot_ids=part['slot_ids'])
    if a['global_round']==2 and kind=='writer':
        old=read(a['previous_round']);require(len(old['review_calls'])==len(p['projection']['partitions']),'prior round coverage changed')
        call=Call(**old['review_calls'][index]);result=validate_call(call,read(call.request),policy=policy)
        body['previous_partition_review']=result['review'];body['previous_round']=a['previous_round']
        previous_para=Call(**old['paraphraser_calls'][index])
        composer=skill_module(root,'additional-assumptions-dialogue','compose_draft')
        body['previous_partition_draft']=composer.assemble_slots(part['template'],part['slots'],read(previous_para.parsed))
    prompt=author.prompt(root,kind)+'\nThis is one exact contiguous slice of a larger fixed conversation. Global slot IDs, speaker rotation and established sheet context are already fixed in the supplied template. Do not restart introductions or renumber slots. Cover every assigned claim; do not invent other exchanges.'
    return _payload(body,prompt,policy,p['projection']['max_bytes']*2)


def _call(call,expected,a,binding,kind,index,policy):
    result=validate_call(call,expected,policy=policy)
    submitted=read(call.submitted)
    require(submitted.get('scope')==asdict(scope(a,kind,index)) and submitted.get('semantic_ordinal')==submitted.get('semantic_limit')==1,'partition physical scope or allowance changed')
    require(submitted.get('physical_repair') is None,'undeclared partition physical repair')
    require(any(executor.checked_bytes(b)==executor.checked_bytes(binding) for b in submitted.get('budget_evidence',[])),'partition call omitted immutable global authority')
    require(submitted.get('response_mode')=='json_object','partition requires exact JSON protocol')
    return result


def validate_author(call,authority,index,kind,policy,*,root=ROOT,incoming=None,_validated=None):
    from scripts.batch import harbor_dialogue_authoring as author
    a,p=_validated or selected(authority,policy,root=root);part=_part(p,index)
    result=_call(call,author_payload(a,p,index,kind,policy,root=root,incoming=incoming),a,authority,kind,index,policy)
    raw=result['review'];filled,faults,proof=author.assemble(raw,'json_object',part['template'],part['slots'],root=root)
    require(not faults,'partition authored slots failed owning scaffold validation')
    if kind=='paraphraser':
        require(isinstance(incoming,str),'paraphraser lost exact writer scaffold')
        composer=skill_module(root,'additional-assumptions-dialogue','compose_draft')
        _,oldproof=composer.migrate_scaffold(part['template'],incoming,part['slots'],part['template'],part['slots'])
        for sid in part['slot_ids']:
            if part['slots']['slots'][sid]['kind']=='junior':require(proof['slot_prose_sha256'][sid]==oldproof['slot_prose_sha256'][sid],'paraphraser changed junior bytes')
    return dict(provenance=result,slots=raw,filled=filled)


def _window(p,index,draft,*,root):
    projection=p['projection'];part=_part(p,index);es=projection['exchanges']
    starts=[draft.index('<!-- claim:'+e['claim_ids'][0]+' -->') for e in es]
    require(starts==sorted(starts) and starts[0]==0,'review draft global exchange order changed')
    first=max(0,part['first_exchange']-1);last=min(len(es),part['last_exchange']+1)
    text=draft[starts[first]:(starts[last] if last<len(es) else len(draft))]
    neighbors=[x for e in es[first:last] for x in e['claim_ids'] if x not in part['claim_ids']]
    window_ids=[x for e in es[first:last] for x in e['claim_ids']]
    return dict(window_claims=D.projected(p['claims'],window_ids,text,root=root),assigned_claim_ids=part['claim_ids'],context_claim_ids=neighbors,draft=text,
        seams=[s for s in projection['seams'] if s['owner']==index])


def review_payload(a,p,index,policy,full_draft,*,root=ROOT):
    body=_window(p,index,full_draft,root=root)
    from scripts.batch import harbor_dialogue_operand_evidence as operands
    body['operand_evidence']=operands.project(body['window_claims'],body['draft'],source=a['source'],answer_key=a['answer_key'],task_dir=Path(a['task_dir']),root=root)
    body.update(plan_sha256=a['plan']['sha256'],global_round=a['global_round'],index=index,full_draft_sha256=executor.sha(full_draft.encode()))
    validator=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    prompt='You are a fresh independent reviewer of a contiguous window from a fixed deal-team conversation. Verify every assigned claim exactly once in its supplied order, including every operator, sign, row/source identity, scope and must_say atom. Neighbor exchanges and context claims are supplied to review continuity, not to add assigned verdicts. Check every explicit seam for coherent speakers, sheet context, contradictory assumptions and natural transitions. Do not infer approval from another partition. Return only {"review":'+validator.REVIEW_SCHEMA_TEMPLATE+',"seams":[{"seam_id":"exact supplied id","passed":true,"findings":[]}]}. Use the supplied global_round. A review passes only with all assigned claims entailed, no extra facts, no senior cell references, and naturalness passing. Every seam must appear exactly once; a failing seam must have concrete findings.'
    return _payload(body,prompt+operands.GUIDANCE,policy,p['projection']['max_bytes']*2)


def validate_review(call,authority,index,policy,full_draft,*,root=ROOT,_validated=None):
    a,p=_validated or selected(authority,policy,root=root);part=_part(p,index)
    result=_call(call,review_payload(a,p,index,policy,full_draft,root=root),a,authority,'review',index,policy)
    raw=result['review'];require(set(raw)=={'review','seams'},'closed partition reviewer response required')
    review=raw['review'];require(set(review)=={'agent_model','round','accuracy','naturalness','passed'},'closed owning review required')
    require(review['round']==a['global_round'] and review['agent_model']=='gpt-5.6-sol-high','review round/model changed')
    accuracy=review['accuracy'];require(set(accuracy)=={'verdict','claims','extras','cell_refs_in_senior_turns'},'accuracy shape changed')
    require([r['record_id'] for r in accuracy['claims']]==part['claim_ids'],'review claim coverage/order incomplete')
    require(all(r.get('must_say') in ('entailed','missing','contradicted') for r in accuracy['claims']),'unknown claim verdict')
    require(type(review['passed']) is bool and accuracy['verdict'] in ('pass','fail'),'explicit review verdict required')
    require(set(review['naturalness'])=={'verdict','findings'} and review['naturalness']['verdict'] in ('pass','fail'),'naturalness shape changed')
    for values in (accuracy['extras'],accuracy['cell_refs_in_senior_turns'],review['naturalness']['findings']):require(isinstance(values,list),'review finding arrays required')
    seams=[s for s in p['projection']['seams'] if s['owner']==index]
    require([s['seam_id'] for s in raw['seams']]==[s['id'] for s in seams],'seam coverage incomplete or reordered')
    for s in raw['seams']:
        require(set(s)=={'seam_id','passed','findings'} and type(s['passed']) is bool and isinstance(s['findings'],list),'invalid seam verdict')
        require((s['passed'] and not s['findings']) or (not s['passed'] and bool(s['findings'])),'seam verdict/findings inconsistent')
    validator=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    exchanges=p['projection']['exchanges'];first=part['first_exchange'];last=part['last_exchange']
    start=full_draft.index('<!-- claim:'+exchanges[first]['claim_ids'][0]+' -->')
    end=full_draft.index('<!-- claim:'+exchanges[last]['claim_ids'][0]+' -->') if last<len(exchanges) else len(full_draft)
    assigned_draft=full_draft[start:end]
    passed=validator.review_passed(review,part['claims'],draft_text=assigned_draft) and all(s['passed'] for s in raw['seams'])
    return dict(provenance=result,review=review,seams=raw['seams'],passed=passed)


def aggregate(authority,writer_calls,paraphraser_calls,review_calls,draft,policy,*,root=ROOT,task_dir):
    a,p=selected(authority,policy,root=root);n=len(p['projection']['partitions'])
    require(all(len(cs)==n for cs in (writer_calls,paraphraser_calls,review_calls)),'complete actual author/reviewer arrays required')
    provenance=[];outputs=[]
    for i,(writer,para) in enumerate(zip(writer_calls,paraphraser_calls)):
        w=validate_author(writer,authority,i,'writer',policy,root=root,_validated=(a,p));require_independent(w['provenance'],provenance);provenance.append(w['provenance'])
        q=validate_author(para,authority,i,'paraphraser',policy,root=root,incoming=w['filled'],_validated=(a,p));require_independent(q['provenance'],provenance);provenance.append(q['provenance']);outputs.append(q['slots'])
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');full=composer.strip_slot_scaffold(D.assemble(p['projection'],outputs,root=root))
    require(executor.checked_bytes(draft)==full.encode(),'selected draft is not exact actual paraphraser assembly')
    reviews=[]
    for i,call in enumerate(review_calls):
        r=validate_review(call,authority,i,policy,full,root=root,_validated=(a,p));require_independent(r['provenance'],provenance);provenance.append(r['provenance']);reviews.append(r)
    require(len({c.request['sha256'] for cs in (writer_calls,paraphraser_calls,review_calls) for c in cs})==n*3,'reused partition request')
    raw=[r['review'] for r in reviews]
    report=dict(agent_model='gpt-5.6-sol-high',round=a['global_round'],accuracy=dict(verdict='pass' if all(r['accuracy']['verdict']=='pass' for r in raw) else 'fail',claims=[c for r in raw for c in r['accuracy']['claims']],extras=[x for r in raw for x in r['accuracy']['extras']],cell_refs_in_senior_turns=[x for r in raw for x in r['accuracy']['cell_refs_in_senior_turns']]),naturalness=dict(verdict='pass' if all(r['naturalness']['verdict']=='pass' for r in raw) else 'fail',findings=[x for r in raw for x in r['naturalness']['findings']]),passed=all(r['passed'] for r in raw))
    seam_failures=[s for r in reviews for s in r['seams'] if not s['passed']]
    if seam_failures:
        report['passed']=False;report['naturalness']['verdict']='fail';report['naturalness']['findings'] += ['Partition seam '+s['seam_id']+': '+str(f) for s in seam_failures for f in s['findings']]
    validator=skill_module(root,'additional-assumptions-dialogue','validate_dialogue');mechanics=validator.check_draft(full,p['claims'],Path(task_dir),source_workbook=a['source'])
    passed=all(r['passed'] for r in reviews) and mechanics['passed'] is True and validator.review_passed(report,p['claims'],draft_text=full)
    if a['global_round']==2:
        previous=read(a['previous_round'])
        old=aggregate(previous['authority'],*[tuple(Call(**c) for c in previous[k]) for k in ('writer_calls','paraphraser_calls','review_calls')],previous['draft'],policy,root=root,task_dir=task_dir)
        require(previous==old and old['passed'] is False,'remaining round prior history changed or passed')
        old_provenance=[validate_call(Call(**c),read(c['request']),policy=policy) for k in ('writer_calls','paraphraser_calls','review_calls') for c in old[k]]
        for item in provenance:require_independent(item,old_provenance)
    return dict(schema_version=AGGREGATE,authority=authority,plan=a['plan'],round=a['global_round'],global_limit=2,writer_calls=[asdict(c) for c in writer_calls],paraphraser_calls=[asdict(c) for c in paraphraser_calls],review_calls=[asdict(c) for c in review_calls],draft=draft,review=report,seams=[s for r in reviews for s in r['seams']],mechanics=mechanics,passed=passed)


def validate_aggregate(binding,*,claims,reviewed_claims,draft,notes,policy,workbook,original_sha256,source,answer_key,task_dir,root=ROOT):
    value=read(binding);a,p=selected(value['authority'],policy,root=root)
    require(a['workbook']==workbook and a['original_source']['sha256']==original_sha256,'aggregate original identity changed')
    executor.checked_bytes(source);executor.checked_bytes(answer_key)
    require(source['sha256']==a['source']['sha256'] and executor.checked_bytes(source)==executor.checked_bytes(a['source']),'aggregate source is not current selected source')
    require(answer_key['sha256']==a['answer_key']['sha256'] and executor.checked_bytes(answer_key)==executor.checked_bytes(a['answer_key']),'aggregate answer key changed')
    extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    options={} if a.get('scope_evidence') is None else dict(source_workbook=source['path'],scope_evidence=a['scope_evidence']['path'])
    require(extractor.extract(Path(task_dir),**options)==p['claims'],'aggregate claims differ from actual current disclosure/source')
    require(read(claims)==read(reviewed_claims)==p['claims'] and draft['sha256']==value['draft']['sha256'] and executor.checked_bytes(draft)==executor.checked_bytes(value['draft']),'aggregate claim/draft binding changed')
    result=aggregate(value['authority'],*[tuple(Call(**c) for c in value[k]) for k in ('writer_calls','paraphraser_calls','review_calls')],value['draft'],policy,root=root,task_dir=task_dir)
    require(result==value and result['passed'] is True,'dialogue aggregate incomplete, changed or rejected')
    validator=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    require(executor.checked_bytes(notes)==validator.strip_claim_comments(executor.checked_bytes(draft).decode()).encode(),'published notes differ from fully reviewed draft')
    evidence=[binding,claims,reviewed_claims,draft,notes,source,answer_key]
    history=value
    while True:
        current=read(history['authority'])
        evidence += [history['authority'],history['plan'],history['draft'],current['initial'],current['original_source'],current['source'],current['answer_key']]
        if current.get('scope_evidence') is not None:evidence.append(current['scope_evidence'])
        for key in ('writer_calls','paraphraser_calls','review_calls'):
            for c in history[key]:evidence += [c[k] for k in ('request','submitted','response','completed','parsed') if c.get(k) is not None]
        if history['round']==1:break
        evidence.append(current['previous_round']);history=read(current['previous_round'])
    return dict(claims=len(p['claims']['claims']),round=value['round'],partitions=len(value['review_calls']),actual_author_calls=len(value['writer_calls'])+len(value['paraphraser_calls']),actual_review_calls=len(value['review_calls']),review=result['review'],evidence=evidence)
