import json
from pathlib import Path
import dataclasses
import pytest
from scripts.batch import harbor_instruction_producer as p
from scripts.batch.harbor_admission import record
from test_harbor_instruction_producer import setup,dialogue_fixture
ROOT=Path.cwd()

@pytest.fixture
def authoring():
    from scripts.batch import harbor_dialogue_authoring
    return harbor_dialogue_authoring

def values(task):
    slots=p.read(record(task.run/'dialogue/slots.json'))
    return {'schema_version':'dialogue-slot-prose/v1','slots':{k:('Which source should I use?' if v['kind']=='junior' else 'Take Revenue from the Opening balance row, using the S tab.') for k,v in slots['slots'].items()}}

def test_json_authoring_and_readiness_reconstruct_exact_draft(setup,authoring):
    task,campaign,respond,calls=dialogue_fixture(setup)
    writer=p.prepare_dialogue_writer(task);assert writer.prepared.response_mode=='json_object'
    respond(writer,values(task));first=p.consume_dialogue_writer(task,writer,campaign);assert first['passed']
    para=p.prepare_dialogue_paraphraser(task);respond(para,values(task));result=p.consume_dialogue_paraphraser(task,para,writer,campaign);assert result['passed'] and result['draft_report']['passed']
    claims=p.read(record(task.run/'dialogue/claims.json'));draft=p.text(result['draft'])
    checked=authoring.verify_pair((p.observed(writer,campaign),p.observed(para,campaign)),policy=task.policy,claims=claims,draft=draft,round=1,root=ROOT,workbook=p.task_context(task)[1]['workbook'],original_sha256=p.task_context(task)[1]['original']['sha256'])
    assert len(checked)==2 and len(calls)==2
    with pytest.raises(ValueError,match='exact assembled'):authoring.verify_pair((p.observed(writer,campaign),p.observed(para,campaign)),policy=task.policy,claims=claims,draft=draft+'changed',round=1,root=ROOT,workbook=p.task_context(task)[1]['workbook'],original_sha256=p.task_context(task)[1]['original']['sha256'])

def legacy_failed(task,campaign,respond,author):
    directory,claims,template,slots=p._dialogue(task,1,ROOT)
    body=author.author_body(template,slots,claims['writer_pack'],'writer')
    old=p._request(task,'dialogue-writer',p.DIALOGUE,1,2,body,author.prompt(ROOT,'writer',legacy=True),[record(directory/'claims.json')],mode='text')
    filled=p.skill_module(ROOT,'additional-assumptions-dialogue','compose_draft').assemble_slots(template,slots,values(task))
    respond(old,filled[:-1]);return old,filled

def test_exact_legacy_first_call_gets_one_shared_structural_correction(setup,authoring):
    task,campaign,respond,calls=dialogue_fixture(setup);old,filled=legacy_failed(task,campaign,respond,authoring)
    result=p.consume_dialogue_writer(task,old,campaign);assert not result['passed']
    raw=record(task.run/'dialogue/round-01/writer.filled.md')
    retry=p.prepare_dialogue_structural_correction(task,old,campaign)
    assert retry.prepared.ordinal==retry.prepared.limit==1 and retry.prepared.scope.budget_id=='dialogue-structural-round-01'
    assert p._author_round(retry)==1
    respond(retry,values(task));fixed=p.consume_dialogue_writer(task,retry,campaign);assert fixed['passed']
    assert record(raw['path'])==raw and Path(raw['path']).read_text()==filled[:-1]
    assert p.consume_dialogue_writer(task,retry,campaign)['passed'] and len(calls)==2
    with pytest.raises(ValueError):p.prepare_dialogue_structural_correction(task,retry,campaign)
    para=p.prepare_dialogue_paraphraser(task);respond(para,values(task));result=p.consume_dialogue_paraphraser(task,para,retry,campaign);assert result['passed']
    claims=p.read(record(task.run/'dialogue/claims.json'));authoring.verify_pair((p.observed(retry,campaign),p.observed(para,campaign)),policy=task.policy,claims=claims,draft=p.text(result['draft']),round=1,root=ROOT,workbook=p.task_context(task)[1]['workbook'],original_sha256=p.task_context(task)[1]['original']['sha256'])
    assert len(calls)==3

@pytest.mark.parametrize('fault',['missing','unknown','empty','schema','structural'])
def test_bad_slot_json_records_exact_failed_call_and_does_not_accept(setup,fault):
    task,campaign,respond,calls=dialogue_fixture(setup);writer=p.prepare_dialogue_writer(task);v=values(task);sid=next(iter(v['slots']))
    if fault=='missing':del v['slots'][sid]
    elif fault=='unknown':v['slots']['invented']='extra'
    elif fault=='empty':v['slots'][sid]=''
    elif fault=='schema':v['schema_version']='changed'
    else:v['slots'][sid]='## inserted'
    respond(writer,v);result=p.consume_dialogue_writer(task,writer,campaign)
    assert not result['passed'] and p.read(result['structural_failure'])['structure_faults']
    assert not (task.run/'dialogue/round-01/writer-accepted.json').exists() and len(calls)==1

def test_paraphraser_cannot_change_junior_json_prose(setup):
    task,campaign,respond,calls=dialogue_fixture(setup);writer=p.prepare_dialogue_writer(task);v=values(task);respond(writer,v);p.consume_dialogue_writer(task,writer,campaign)
    para=p.prepare_dialogue_paraphraser(task);sid=next(iter(v['slots']));v['slots'][sid]='Changed junior';respond(para,v)
    with pytest.raises(ValueError,match='junior'):p.consume_dialogue_paraphraser(task,para,writer,campaign)

def test_missing_candidate_on_frozen_failure_can_prepare_remaining_nat_writer(setup):
    task,campaign,respond,calls=setup;p.initialize_naturalization(task);writer=p.prepare_naturalization_writer(task)
    respond(writer,{'preamble_body':'Missing protected markers','input_body':'Missing protected markers'})
    result=p.consume_naturalization_writer(task,writer,campaign);assert not result['validation']['valid']
    assert not (task.run/'naturalization/attempt-01/candidate.md').exists()
    second=p.prepare_naturalization_writer(task);assert second.prepared.ordinal==2
    assert 'next level-two heading' in p.read(writer.prepared.request)['messages'][0]['content']
    evidence=p.read(second.context)['evidence'];assert any(x['path'].endswith('validation.json') for x in evidence)
    assert not any(x['path'].endswith('candidate.md') for x in evidence)

def test_shared_structural_correction_cannot_be_spent_again_by_paraphraser(setup,authoring):
    task,campaign,respond,calls=dialogue_fixture(setup);old,_=legacy_failed(task,campaign,respond,authoring)
    p.consume_dialogue_writer(task,old,campaign);retry=p.prepare_dialogue_structural_correction(task,old,campaign)
    respond(retry,values(task));p.consume_dialogue_writer(task,retry,campaign)
    para=p.prepare_dialogue_paraphraser(task);bad=values(task);bad['slots'].pop(next(iter(bad['slots'])));respond(para,bad)
    assert not p.consume_dialogue_paraphraser(task,para,retry,campaign)['passed']
    with pytest.raises(ValueError):p.prepare_dialogue_structural_correction(task,para,campaign)
    assert len(calls)==3


def test_paraphraser_can_use_the_unused_shared_structural_correction(setup,authoring):
    task,campaign,respond,calls=dialogue_fixture(setup);writer=p.prepare_dialogue_writer(task);respond(writer,values(task));p.consume_dialogue_writer(task,writer,campaign)
    para=p.prepare_dialogue_paraphraser(task);bad=values(task);bad['slots'].pop(next(iter(bad['slots'])));respond(para,bad)
    assert not p.consume_dialogue_paraphraser(task,para,writer,campaign)['passed']
    retry=p.prepare_dialogue_structural_correction(task,para,campaign);respond(retry,values(task));result=p.consume_dialogue_paraphraser(task,retry,writer,campaign)
    assert result['passed'];identity=p.task_context(task)[1]
    authoring.verify_pair((p.observed(writer,campaign),p.observed(retry,campaign)),policy=task.policy,claims=p.read(record(task.run/'dialogue/claims.json')),draft=p.text(result['draft']),round=1,root=ROOT,workbook=identity['workbook'],original_sha256=identity['original']['sha256'])
    assert len(calls)==3


def test_complete_json_round_requires_actual_independent_review(setup):
    task,campaign,respond,calls=dialogue_fixture(setup);writer=p.prepare_dialogue_writer(task);respond(writer,values(task));p.consume_dialogue_writer(task,writer,campaign)
    para=p.prepare_dialogue_paraphraser(task);respond(para,values(task));p.consume_dialogue_paraphraser(task,para,writer,campaign)
    review=p.prepare_dialogue_review(task);claims=p.read(record(task.run/'dialogue/claims.json'))
    response={'agent_model':'gpt-5.6-sol-high','round':1,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in claims['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':True}
    respond(review,response);assert p.consume_dialogue_review(task,review,writer,para,campaign)['passed']
    assert len(calls)==3
    with pytest.raises(ValueError):p.prepare_dialogue_writer(task,2)


def test_original_workbook_scope_is_mandatory_for_readiness(setup,authoring):
    task,campaign,respond,calls=dialogue_fixture(setup);writer=p.prepare_dialogue_writer(task);respond(writer,values(task));p.consume_dialogue_writer(task,writer,campaign)
    para=p.prepare_dialogue_paraphraser(task);respond(para,values(task));r=p.consume_dialogue_paraphraser(task,para,writer,campaign)
    with pytest.raises(ValueError,match='different original'):
        authoring.verify_pair((p.observed(writer,campaign),p.observed(para,campaign)),policy=task.policy,claims=p.read(record(task.run/'dialogue/claims.json')),draft=p.text(r['draft']),round=1,root=ROOT,workbook='9999',original_sha256='0'*64)


def test_existing_owning_second_nat_writer_reviewer_regression(setup):
    from test_harbor_naturalization_review_budget import test_valid_second_writer_after_mechanical_failure_gets_one_real_review
    test_valid_second_writer_after_mechanical_failure_gets_one_real_review(setup)

@pytest.mark.parametrize('change',[None,'missing_history','changed_history','changed_prior_response'])
def test_round2_requires_actual_complete_failed_round1_journal(setup,authoring,change):
    task,campaign,respond,calls=dialogue_fixture(setup)
    def round_calls(number):
        writer=p.prepare_dialogue_writer(task,number);respond(writer,values(task));p.consume_dialogue_writer(task,writer,campaign)
        para=p.prepare_dialogue_paraphraser(task,number);respond(para,values(task));result=p.consume_dialogue_paraphraser(task,para,writer,campaign)
        return writer,para,result
    first,para,_=round_calls(1);review=p.prepare_dialogue_review(task);claims=p.read(record(task.run/'dialogue/claims.json'))
    value={'agent_model':'gpt-5.6-sol-high','round':1,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in claims['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'fail','findings':['Repeated phrase']},'passed':False}
    respond(review,value);assert not p.consume_dialogue_review(task,review,first,para,campaign)['passed']
    second,para,result=round_calls(2);writer_call=p.observed(second,campaign);identity=p.task_context(task)[1]
    if change=='missing_history':
        submitted=p.read(writer_call.submitted);submitted['budget_evidence']=[]
        Path(writer_call.submitted['path']).write_text(json.dumps(submitted));writer_call=dataclasses.replace(writer_call,submitted=record(writer_call.submitted['path']))
    elif change=='changed_history':
        for b in p.read(writer_call.submitted)['budget_evidence']:
            try:v=p.read(b)
            except ValueError:continue
            if isinstance(v,dict) and v.get('schema_version')=='dialogue-prior-round/v1':
                v['review']['passed']=True;Path(b['path']).write_text(json.dumps(v));break
    elif change=='changed_prior_response':Path(p.observed(review,campaign).response['path']).write_text('{}')
    args=dict(policy=task.policy,claims=claims,draft=p.text(result['draft']),round=2,root=ROOT,workbook=identity['workbook'],original_sha256=identity['original']['sha256'])
    if change is None:assert len(authoring.verify_pair((writer_call,p.observed(para,campaign)),**args))==2
    else:
        with pytest.raises(ValueError):authoring.verify_pair((writer_call,p.observed(para,campaign)),**args)
    assert len(calls)==5
