"""Closed dialogue author protocols and exact deterministic assembly evidence.

Legacy complete text calls remain explicit text; fresh calls use slot JSON.
This module never edits a response or invents authored slot prose.
"""
from pathlib import Path
import hashlib
import json
from scripts.batch.harbor_admission import read,require,record
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_model_review_provenance import Call,validate_call,require_independent
from scripts.batch.harbor_readiness import skill_module

SCHEMA='dialogue-slot-prose/v1'
REPAIR='harbor-dialogue-structural-repair/v1'
END='Output only the filled Markdown conversation. Preserve every non-slot byte,\nincluding the template\'s exact trailing newlines and final blank lines.'
FORMAT='Return only one JSON object {"schema_version":"dialogue-slot-prose/v1","slots":{...}}. Include every slots.json slot ID exactly once and its authored prose as a string. No extra fields, turns, headings, comments, filenames or modelling facts. The deterministic assembler supplies every structural byte; never include template structure in a slot string.'

def prompt(root,kind,*,legacy=False):
    from scripts.batch.harbor_instruction_producer import skill_prompt
    starts={'writer':'You are writing notes from a conversation','paraphraser':'You are rewriting senior answers'}
    require(kind in starts,'unknown dialogue author role')
    original=skill_prompt(root,'additional-assumptions-dialogue',starts[kind])
    if legacy:return original
    require(original.count(END)==1,'owning dialogue response format changed')
    begin=original.index('Read writer_pack.json')
    end=original.index('HARD RULES',begin)
    intro='Read writer_pack.json, draft_template.md, and slots.json. '
    if kind=='paraphraser':intro+='Also read the current filled draft. Copy every junior slot string byte-for-byte; rewrite senior strings only. '
    intro+=FORMAT+'\n\n'
    return (original[:begin]+intro+original[end:]).replace(END,FORMAT)

def mode(call):
    submitted=read(call.submitted)
    value=submitted.get('response_mode','text')
    require(value in ('text','json_object'),'unknown dialogue author response protocol')
    return value

def content(call,policy):
    value=mode(call)
    result=validate_call(call,read(call.request),policy=policy,json_response=value=='json_object')
    if value=='json_object':
        from scripts.batch.harbor_model_executor import strict_json
        raw=read(call.response)['choices'][0]['message']['content']
        require(strict_json(raw.encode())==result['review'],'structured author JSON changed')
    return result

def assemble(raw,protocol,template,slots,*,root):
    v=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    c=skill_module(root,'additional-assumptions-dialogue','compose_draft')
    require(protocol in ('text','json_object'),'unsupported author protocol')
    if protocol=='text':
        require(isinstance(raw,str),'legacy text response changed type')
        filled=raw;faults=v.fill_faults(template,filled)
    else:
        try:filled=c.assemble_slots(template,slots,raw)
        except ValueError as exc:return None,[str(exc)],None
        faults=v.fill_faults(template,filled)
    if faults:return filled,faults,None
    _,proof=c.migrate_scaffold(template,filled,slots,template,slots)
    return filled,[],proof

def author_body(template,slots,pack,kind,*,incoming=None,previous=None):
    result={'writer_pack.json':pack,'draft_template.md':template,'slots.json':slots}
    if kind=='paraphraser':
        require(isinstance(incoming,str),'paraphraser requires exact accepted writer draft')
        result['draft.filled.md']=incoming
    if previous is not None:
        require(kind=='writer','only writer round2 receives prior semantic review')
        result.update(previous)
    return result

def repair_from_call(call,*,policy,body,root):
    """Validate exact failed prior invocation bound in one budget snapshot."""
    submitted=read(call.submitted);scope=submitted.get('semantic_scope') or submitted.get('scope')
    # Executor submitted evidence records the concrete logical scope.
    require(isinstance(scope,dict),'structural call lacks semantic scope')
    repair=body.get('structural_correction')
    require(isinstance(repair,dict) and set(repair)=={'round','kind','previous_response','faults','receipt_sha256'},'structural correction context malformed')
    round=repair['round'];kind=repair['kind']
    require(type(round) is int and round in (1,2) and kind in ('writer','paraphraser'),'structural round/role invalid')
    require(scope['budget_id']=='dialogue-structural-round-%02d'%round and submitted.get('semantic_ordinal')==submitted.get('semantic_limit')==1,'structural retry budget reset')
    matches=[]
    for binding in submitted.get('budget_evidence',[]):
        if binding['sha256']==repair['receipt_sha256']:matches.append(binding)
    require(len(matches)==1,'exact structural history budget snapshot required')
    receipt=read(matches[0]);require(receipt.get('schema_version')==REPAIR,'unknown structural history')
    require(receipt['round']==round and receipt['kind']==kind and receipt['faults']==repair['faults'],'structural history changed')
    old=Call(**receipt['call']);old_request=read(old.request);old_body=json.loads(old_request['messages'][1]['content'])
    require('structural_correction' not in old_body,'second structural correction prohibited')
    previous=content(old,policy)
    require(previous['review']==repair['previous_response'],'structural correction changed old response')
    require(old_request['messages'][0]['content'] in (prompt(root,kind),prompt(root,kind,legacy=True)),'old author prompt is not an exact supported contract')
    clean={k:v for k,v in body.items() if k!='structural_correction'}
    require(old_body==clean,'structural correction changed financial/card context')
    filled,faults,_=assemble(previous['review'],mode(old),clean['draft_template.md'],clean['slots.json'],root=root)
    require(faults and faults==repair['faults'],'structural fault is not currently reproduced')
    old_scope=read(old.submitted).get('scope',{})
    require(all(old_scope.get(key)==scope.get(key) for key in ('workbook_id','original_sha256','lane')),'structural history belongs to a different original')
    require(read(old.submitted).get('semantic_ordinal')==round,'prior semantic writer round changed')
    require(read(old.submitted).get('semantic_scope',read(old.submitted).get('scope',{})).get('budget_id')=='dialogue-'+kind,'prior producer role/scope changed')
    require(old.request['sha256']!=call.request['sha256'] and old.response['sha256']!=call.response['sha256'],'structural correction reused original call')
    return receipt

def verify_author(call,*,policy,kind,round,template,slots,pack,incoming=None,previous=None,root):
    request=read(call.request);body=json.loads(request['messages'][1]['content'])
    expected=author_body(template,slots,pack,kind,incoming=incoming,previous=previous)
    protocol=mode(call)
    submitted=read(call.submitted);scope=submitted.get('scope',{})
    require(scope.get('lane')=='harbor-dialogue-author','author invocation belongs to another lane')
    if 'structural_correction' not in body:
        require(scope.get('budget_id')=='dialogue-'+kind and submitted.get('semantic_ordinal')==round and submitted.get('semantic_limit')==2,'dialogue author round/scope changed')
    base_prompt=prompt(root,kind,legacy=protocol=='text')
    if 'structural_correction' in body:
        receipt=repair_from_call(call,policy=policy,body=body,root=root)
        require(receipt['round']==round and receipt['kind']==kind,'wrong structural correction owner')
        correction=body['structural_correction'];expected['structural_correction']=correction
        require(protocol=='json_object','fresh structural corrections require slot JSON')
        base_prompt+='\nThe previous response failed the fixed structural validator. Correct the same slot prose under the same claims and template. Exact validator errors: '+json.dumps(correction['faults'])
    if 'source_proved_feedback' in body:
        require(round==2 and 'structural_correction' not in body,'source feedback requires ordinary remaining author round')
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        feedback=body['source_proved_feedback'];authority=feedback['authority']
        require(feedback==Q.remaining_feedback(authority,root=root),'remaining author source feedback changed')
        require(any(checked_record(b) and Path(b['path']).read_bytes()==Path(authority['path']).read_bytes() for b in submitted['budget_evidence']),'remaining author omitted source authority')
        expected['source_proved_feedback']=feedback
    require(body==expected and request['messages'][0]['content']==base_prompt,'dialogue author context/prompt changed')
    result=content(call,policy);filled,faults,proof=assemble(result['review'],protocol,template,slots,root=root)
    return result,filled,faults,proof


def prior_round(call,*,policy,claims,previous,root,workbook,original_sha256):
    """Revalidate complete prior semantic round from actual budget snapshots."""
    matches=[]
    for binding in read(call.submitted).get('budget_evidence',[]):
        from scripts.batch.harbor_tracker_lifecycle import checked_record
        checked_record(binding)
        try:value=read(binding)
        except (ValueError,UnicodeError):continue
        if isinstance(value,dict) and value.get('schema_version') in ('dialogue-prior-round/v1','dialogue-prior-round/v2','dialogue-prior-round/pre-review-v1'):matches.append(value)
    require(len(matches)==1,'round2 requires exactly one complete prior-round journal')
    history=matches[0]
    keys={'schema_version','claims','draft','review','review_call','author_calls'}
    corrected=history['schema_version']=='dialogue-prior-round/pre-review-v1'
    if corrected:
        require(set(history)==keys|{'dialogue_pre_review_repair'} and history['claims']==claims,'source-corrected prior-round schema changed')
        original=claims
    elif history['schema_version']=='dialogue-prior-round/v2':
        require(set(history)==keys|{'source_scope_transition'},'grouped prior-round journal schema changed')
        from scripts.batch import harbor_dialogue_scope_context as scope_context
        original,current,_=scope_context.validate_transition(history['source_scope_transition'],current_claims=claims,root=root)
        require(history['claims']==original,'original failed claims changed')
    else:
        require(set(history)==keys and history['claims']==claims,'prior-round journal schema changed')
        original=claims
    require(history['claims']==original and history['draft']==previous['previous_draft'] and history['review']==previous['previous_review'],'prior-round financial/review context changed')
    reviewer=Call(**history['review_call']);request=read(reviewer.request)
    from scripts.batch.harbor_instruction_producer import skill_prompt
    prompt_text=skill_prompt(root,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
    expected={'claims.json':original,'draft.md':history['draft'],'round':1}
    if corrected:
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        expected['source_proved_correction']=Q.review_projection(history['dialogue_pre_review_repair'],original,history['draft'],root=root)
        prompt_text+=Q.REVIEW_GUIDANCE;Q.validate_review_call(history['dialogue_pre_review_repair'],reviewer)
    require(request['messages'][0]['content']==prompt_text,'prior reviewer prompt changed')
    require(json.loads(request['messages'][1]['content'])==expected,'prior reviewer context changed')
    submitted=read(reviewer.submitted);scope=submitted.get('scope',{})
    require(scope=={'workbook_id':workbook,'original_sha256':original_sha256,'lane':'harbor-dialogue-author','budget_id':'dialogue-review'} and submitted.get('semantic_ordinal')==1 and submitted.get('semantic_limit')==2,'prior reviewer original/round budget changed')
    checked=validate_call(reviewer,request,policy=policy)
    require(checked['review']==history['review'],'prior review is not actual response')
    if corrected:
        authors=Q.verify_pair(history['dialogue_pre_review_repair'],tuple(Call(**v) for v in history['author_calls']),policy=policy,claims=original,draft=history['draft'],round=1,root=root,workbook=workbook,original_sha256=original_sha256)
    else:
        authors=verify_pair(tuple(Call(**v) for v in history['author_calls']),policy=policy,claims=original,draft=history['draft'],round=1,root=root,workbook=workbook,original_sha256=original_sha256)
    require_independent(checked,authors)
    v=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    require(not v.review_passed(history['review'],original),'successful prior dialogue round must not be rewritten')
    current=content(call,policy);require_independent(current,[checked,*authors])
    return history

def verify_pair(calls,*,policy,claims,draft,round,root,workbook,original_sha256):
    require(len(calls)==2,'fresh dialogue requires writer and independent paraphraser')
    for call in calls:
        scope=read(call.submitted).get('scope',{})
        require(scope.get('workbook_id')==workbook and scope.get('original_sha256')==original_sha256,'dialogue author belongs to a different original')
    c=skill_module(root,'additional-assumptions-dialogue','compose_draft')
    template,slots=c.compose(claims,claims['writer_pack'])
    previous=None
    if round==2:
        body=json.loads(read(calls[0].request)['messages'][1]['content'])
        require('previous_draft' in body and 'previous_review' in body,'round2 prior semantic context missing')
        previous={k:body[k] for k in ('previous_draft','previous_review')}
        history_call=calls[0]
        if 'structural_correction' in body:
            history_call=Call(**repair_from_call(calls[0],policy=policy,body=body,root=root)['call'])
        prior_round(history_call,policy=policy,claims=claims,previous=previous,root=root,workbook=workbook,original_sha256=original_sha256)
    writer,incoming,faults,old=verify_author(calls[0],policy=policy,kind='writer',round=round,template=template,slots=slots,pack=claims['writer_pack'],previous=previous,root=root)
    require(not faults,'accepted writer structure invalid')
    para,filled,faults,new=verify_author(calls[1],policy=policy,kind='paraphraser',round=round,template=template,slots=slots,pack=claims['writer_pack'],incoming=incoming,root=root)
    require(not faults,'accepted paraphraser structure invalid');require_independent(para,[writer])
    for sid in slots['slot_order']:
        if slots['slots'][sid]['kind']=='junior':require(old['slot_prose_sha256'][sid]==new['slot_prose_sha256'][sid],'paraphraser changed junior prose')
    require(c.strip_slot_scaffold(filled)==draft,'reviewed draft is not exact assembled author output')
    return [writer,para]
