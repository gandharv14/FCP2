"""Recover only a completed second frozen writer's boundary-assembly failure.

Original attempts, invocation journals and exhausted state snapshot are retained.
No call or semantic review is made; the selected candidate requires fresh review.
"""
from dataclasses import asdict
from pathlib import Path
import copy,json,os,tempfile,shutil
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_admission import read,record,require

SCHEMA='harbor-nat-boundary-recovery/v1'
DIRECTORY='boundary-recovery-v1'
ROOT=producer.ROOT
KNOWN={
    'scripts/batch/harbor_instruction_producer.py':'d020c095a08f2d6546b972a85df39d7e468efb615fa6affe1a574249178e73a1',
    '.cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py':'5035f53af8b87bf9dd4a23e9ebf1a8120e81ec8228dacc96c3c719e75f17fa79',
}


def request_value(request):return json.loads(executor.canonical(dict(prepared=asdict(request.prepared),context=request.context)))


def request_from(value):
    p=dict(value['prepared']);p['policy']=producer.Policy(**p['policy']);p['scope']=executor.SemanticScope(**p['scope']);p['budget_evidence']=tuple(p['budget_evidence'])
    require(set(value)=={'prepared','context'},'boundary writer recipe fields changed')
    return producer.Request(executor.PreparedCall(**p),value['context'])


def campaign_value(campaign):return dict(root=str(campaign.root),slot_root=str(campaign.slot_root),model_slots=campaign.model_slots)


def api_default():return producer.skill_module(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')


def _legacy_prompt(before):
    prompt=producer.skill_prompt(ROOT,'naturalize-finance-task-instruction','You are a senior finance professional')
    prompt+='\nLine prefixes such as `10|` are tool metadata and must never be copied into either output.\nFor this stateless API call, return only one JSON object with exactly preamble_body and input_body string fields. Keep every frozen wrapper in those bodies. Do not output filenames or any other fields.'
    first=before['attempts'][0]
    prompt+='\nYour previous writer attempt failed mechanical validation with reason codes: '+json.dumps(first['reason_codes'])+'. Return only the editable bodies. In input_body, stop before the next level-two heading. Do not include any immutable section or its heading; the assembler retains those sections. Keep all financial meaning and protected spans unchanged.'
    return prompt


def derive(root,before,writer,campaign,original_code,*,api=None):
    api=api or api_default();root=Path(root)
    require(before['status']=='exhausted' and before['attempt_limit']==2 and api._attempts_used(before)==2 and [x['attempt'] for x in before['attempts']]==[1,2],'only retained exhausted second writer supported')
    require(not any(before.get(k) for k in ('budget_inheritance','inherited_attempts','semantic_reviews','accepted_semantic_review','staged_attempt','span_restoration','boundary_recovery')),'unknown/inherited/reviewed recovery history is unsupported')
    require(all(x['submission_mode']=='frozen' and x['valid'] is False for x in before['attempts']) and before['attempts'][-1]['reason_codes']==['protected_section_changed'],'only reproduced legacy boundary failure supported')
    source=api._verified_snapshot(root,before);api._verify_attempt_evidence(root,before)
    key=api._verified_answer_key(root,before)
    number=2;attempt=root/'attempt-02';raw=[(attempt/name).read_bytes() for name in ('preamble_body.md','input_body.md')]
    context=read(writer.context);p=writer.prepared
    require(context['schema_version']=='harbor-instruction-request/v1' and context['kind']=='naturalization-writer' and (p.ordinal,p.limit,p.response_mode)==(2,2,'json_object'),'writer attempt/protocol changed')
    require(context['ordinal']==2 and context['limit']==2 and context['request']==p.request and context['response_mode']=='json_object' and context['budget_evidence']==list(p.budget_evidence),'writer context budget changed')
    require(p.scope.lane==producer.NAT and p.scope.budget_id=='naturalization-writer' and p.scope.workbook_id==context['workbook'] and p.scope.original_sha256==context['original']['sha256'],'writer semantic scope changed')
    require(context['stage_tree']['instruction.md']==before['source_sha256'],'writer source snapshot differs')
    for row in p.budget_evidence:executor.checked_bytes(row)
    require(len(p.budget_evidence)>=4,'writer budget history missing')
    prior=read(p.budget_evidence[1]);require(api._attempts_used(prior)==1 and prior['attempts']==before['attempts'][:1] and prior['source_sha256']==before['source_sha256'],'first consumed attempt differs from original writer budget')
    require(prior['status']=='retry_ready' and prior['next_attempt']==2 and prior['attempt_limit']==2 and not prior.get('semantic_reviews') and bool(prior['attempts'][0]['reason_codes']) and set(prior['attempts'][0]['reason_codes'])<=api.RETRYABLE_REASON_CODES,'second writer lacked original remaining-attempt eligibility')
    originals=[row['original'] for row in original_code]
    require(originals==context['code'] and len({r['path'] for r in originals})==len(originals),'complete original code witnesses required')
    by_relative={str(Path(row['original']['path']).relative_to(ROOT)):row for row in original_code}
    for relative,expected in KNOWN.items():require(by_relative[relative]['original']['sha256']==expected,'unknown original writer/assembler implementation')
    for row in original_code:
        executor.checked_bytes(row['retained']);require(row['retained']['sha256']==row['original']['sha256'],'historical code bytes differ')
    # These unchanged engines reproduce original failure and prove new output;
    # no historical Python code witness is imported or executed.
    skill=ROOT/'.cursor/skills/naturalize-finance-task-instruction/scripts'
    for name in ('frozen_submission.py','freeze_protected_spans.py','instruction_spans.py'):
        require(by_relative[str((skill/name).relative_to(ROOT))]['original']==record(skill/name),'original frozen/span engine changed')
    payload=read(p.request)
    expected={'model':p.policy.model,'reasoning_effort':p.policy.reasoning_effort,'messages':[{'role':'system','content':_legacy_prompt(before)},{'role':'user','content':json.dumps({'writer_source':(root/'writer_source.md').read_text()},ensure_ascii=False,allow_nan=False)}]}
    require(payload==expected,'original second-writer prompt/context differs')
    call=producer.observed(writer,campaign);response=producer.validate_call(call,payload,policy=p.policy)['review']
    require(set(response)=={'preamble_body','input_body'} and all(isinstance(response[k],str) and response[k].encode('utf-8')==v for k,v in zip(('preamble_body','input_body'),raw)),'retained bodies differ from completed writer response')
    frozen=api._submission_engine('frozen');boundary=api._submission_engine('frozen-boundary-v1');inputs=frozen.read_inputs(attempt)
    _,old_restore,old_candidate=frozen.derive(source,*raw,inputs)
    require(old_restore['restored'] and old_candidate==(attempt/'candidate.md').read_bytes(),'original assembled candidate differs')
    try:api._load_validator().validate(source,old_candidate,key)
    except api._load_validator().RewriteValidationError as exc:require(exc.reason_codes==['protected_section_changed'],'original failure does not reproduce')
    else:raise ValueError('original candidate no longer fails; recovery not applicable')
    files,restored,candidate=boundary.derive(source,*raw,inputs)
    require(restored['restored'] and candidate and candidate!=old_candidate,'no proved boundary-only recovery')
    validation=api._load_validator().validate(source,candidate,key)
    evidence=[record(root/name) for name in ('source.snapshot.md','spans.json','writer_source.md','frozen_spans.json')]
    if before.get('answer_key_sha256'):evidence.append(record(root/'answer_key.snapshot.json'))
    for n in (1,2):
        for path in sorted((root/('attempt-%02d'%n)).iterdir()):
            if path.is_file() and path.name!='semantic-review.json':evidence.append(record(path))
    return dict(files=files,restoration=restored,candidate=candidate,validation=validation,call=call,evidence=evidence)


def validate(root,state,*,api=None):
    api=api or api_default();root=Path(root);binding=state.get('boundary_recovery')
    if binding is None:
        require(not state.get('final_candidate','').startswith(DIRECTORY+'/'),'boundary candidate lacks recovery receipt');return None
    require(binding['path']==DIRECTORY+'/receipt.json' and set(binding)=={'path','sha256'},'boundary receipt path changed')
    receipt=read({'path':str(root/binding['path']),'sha256':binding['sha256']})
    require(receipt['schema_version']==SCHEMA,'unsupported boundary receipt')
    before=read(receipt['before']);writer=request_from(receipt['writer']);c=receipt['campaign'];campaign=executor.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots'])
    result=derive(root,before,writer,campaign,receipt['original_code'],api=api)
    for field in ('attempts','attempt_limit','inherited_attempts','budget_inheritance','source_sha256','answer_key_sha256','endpoint'):
        require(state.get(field)==before.get(field),'boundary recovery changed source/budget: '+field)
    require(receipt['implementation']==record(__file__) and receipt['writer_attempts_used']==2 and receipt['new_writer_calls']==0 and receipt['semantic_approval']=='fresh_independent_review_required','boundary implementation/semantic policy changed')
    require(receipt['evidence']==result['evidence'] and receipt['call']==asdict(result['call']),'original recovery evidence changed')
    directory=root/DIRECTORY
    require((directory/'candidate.md').read_bytes()==result['candidate'] and read(record(directory/'validation.json'))==result['validation'] and read(record(directory/'restore.json'))==result['restoration'],'derived boundary candidate/validation changed')
    for name,raw in result['files'].items():require((directory/name).read_bytes()==raw,'boundary derived artifact changed: '+name)
    reviews=state.get('semantic_reviews',[])
    if state['status'] in ('validated','applied'):
        require(state.get('final_candidate')==DIRECTORY+'/candidate.md' and state.get('final_instruction_sha256')==executor.sha(result['candidate']) and state['next_attempt'] is None,'selected boundary candidate/budget changed')
    else:
        require(state['status']=='exhausted' and len(reviews)==1 and reviews[0]['accepted'] is False and state.get('final_candidate') is None and state.get('final_instruction_sha256') is None and state['next_attempt'] is None,'boundary review rejection must remain exhausted')
    if state.get('accepted_semantic_review'):
        require(len(reviews)==1 and reviews[0].get('accepted') is True and state['accepted_semantic_review']==reviews[0],'boundary approval lacks fresh persisted review')
    if reviews:
        require(len(reviews)==1 and reviews[0]['attempt']==2 and reviews[0]['source_sha256']==before['source_sha256'] and reviews[0]['candidate_sha256']==executor.sha(result['candidate']),'fresh semantic review identity differs')
        require(read(record(root/'attempt-02/semantic-review.json'))==reviews[0],'semantic review history detached')
    return receipt


def recover(root,*,expected_state,writer,campaign,original_code,api=None,failpoint=lambda _:None):
    """Publish derived artifacts then state CAS; no original evidence replacement."""
    api=api or api_default();root=Path(root)
    require(expected_state['path']==str(root/'state.json'),'explicit current state record required')
    with api.recovery_lock(root):
        current=(root/'state.json').read_bytes();state=json.loads(current)
        if state.get('boundary_recovery'):
            receipt=validate(root,state,api=api)
            require(receipt['before']['sha256']==expected_state['sha256'] and receipt['writer']==request_value(writer) and receipt['campaign']==campaign_value(campaign) and receipt['original_code']==original_code,'recovery reentry evidence changed');return receipt
        executor.checked_bytes(expected_state);api._check_live_source(state)
        result=derive(root,state,writer,campaign,original_code,api=api)
        directory=root/DIRECTORY
        before={'path':str(directory/'state.before.json'),'sha256':executor.sha(current)}
        receipt=dict(schema_version=SCHEMA,before=before,writer=request_value(writer),campaign=campaign_value(campaign),original_code=original_code,call=asdict(result['call']),evidence=result['evidence'],implementation=record(__file__),writer_attempts_used=2,new_writer_calls=0,semantic_approval='fresh_independent_review_required')
        payloads={**result['files'],'state.before.json':current,'candidate.md':result['candidate'],'validation.json':api._json_bytes(result['validation']),'restore.json':api._json_bytes(result['restoration']),'receipt.json':api._json_bytes(receipt)}
        if directory.exists():
            require(set(p.name for p in directory.iterdir())==set(payloads) and all((directory/name).read_bytes()==raw for name,raw in payloads.items()),'interrupted boundary preparation differs')
        else:
            temporary=Path(tempfile.mkdtemp(prefix='.boundary-preparation-',dir=root))
            try:
                for name,raw in payloads.items():api.atomic_write_bytes(temporary/name,raw,mode=0o400)
                api._fsync_directory(temporary);failpoint('before_directory_publish');os.rename(temporary,directory);api._fsync_directory(root)
            finally:
                if temporary.exists():shutil.rmtree(temporary)
        failpoint('after_directory_publish')
        updated=copy.deepcopy(state);updated.update(status='validated',next_attempt=None,final_candidate=DIRECTORY+'/candidate.md',final_instruction_sha256=executor.sha(result['candidate']),boundary_recovery=dict(path=DIRECTORY+'/receipt.json',sha256=executor.sha(payloads['receipt.json'])))
        validate(root,updated,api=api)
        require((root/'state.json').read_bytes()==current,'boundary state CAS conflict')
        api.atomic_write_json(root/'state.json',updated);failpoint('after_state_publish')
        return receipt


def validate_writer(task,request,campaign,*,api=None):
    """Historical writer only: exact completed call bound by current recovery.

    This exception cannot authorize dispatch or change writer budget/scope.
    """
    api=api or api_default();root=task.run/'naturalization';state=api._read_state(root)
    receipt=validate(root,state,api=api)
    require(receipt is not None and receipt['writer']==request_value(request) and receipt['campaign']==campaign_value(campaign),'writer differs from boundary recovery recipe')
    context=read(request.context)
    before=read(task.tracker)
    require(context['original']==before['bindings']['original_raw_source'] and context['workbook']==before['workbook_id'] and context['stage_tree']==task.stage_tree,'current task differs from recovered writer source')
    require((root/'source.snapshot.md').read_bytes()==(task.stage/'instruction.md').read_bytes(),'recovered review must use unchanged source instruction')
    return context
