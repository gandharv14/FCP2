from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import copy,json
import pytest
from scripts.batch import harbor_dialogue_noop as noop
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_complete_readiness as complete
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection,SemanticSelection
from test_harbor_instruction_producer import setup as instruction_setup,nat_writer

ROOT=Path.cwd()
@pytest.fixture
def case(instruction_setup):
    task,campaign,respond,calls=instruction_setup
    writer=nat_writer(instruction_setup);review=producer.prepare_naturalization_review(task,root=ROOT)
    body=json.loads(producer.read(review.prepared.request)['messages'][1]['content']);claims=producer.editable_claims(ROOT,body['source'])
    respond(review,{'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':line,'candidate_claim':line,'preserved':True} for line in claims]})
    producer.consume_naturalization_review(task,review,writer,campaign,apply=True,root=ROOT)
    # Real completed frozen NAT and semantic transport, synthetic disclosure with
    # no agent records; full source reconstruction remains separately mandatory.
    producer.put(task.stage/'tests/disclosure.json',{'agent_records':[]})
    before=producer.read(record(task.tracker['path']));before['lane_state']['harbor-independent-verifier']['state']='passed'
    b=before['bindings'];b['source_file']=before['raw_source']
    b['source_generation']=before['raw_source'];b['segmentation_generation']=before['raw_source']
    tracker=producer.put(task.run/'before-dialogue.json',before)
    extractor=producer.skill_module(ROOT,'additional-assumptions-dialogue','extract_claims')
    claims=producer.put(task.run/'claims.json',extractor.extract(task.stage))
    proof=producer.put(task.run/'noop.json',dict(schema_version='harbor-empty-dialogue/v1',before_tracker=tracker,stage=str(task.stage),stage_tree=producer.tree(task.stage),claims=claims))
    content=Selection('0245',task.stage,'unused',b,task.run/'normalization',task.run/'naturalization',claims,None,dialogue_noop=proof)
    semantic=SemanticSelection(None,None,None,None,None,())
    selected=ReadinessSelection(content,semantic,None,None,None)
    return selected,calls


def test_actual_empty_extraction_completed_nat_no_calls_and_no_mutation(case):
    selected,calls=case;before=producer.tree(selected.content.stage)
    result=noop.validate(selected,root=ROOT)
    assert result['round']==0 and result['disposition']=='not_applicable' and result['reason']=='no_agent_records'
    assert complete.check_dialogue(selected,root=ROOT)==result
    assert producer.tree(selected.content.stage)==before and len(calls)==2
    assert selected.semantic.dialogue_call is None


@pytest.mark.parametrize('defect',['missing_records','nonempty','false_empty','changed_body','invented_notes','invented_marker','review','draft','call','writer','historical','changed_candidate','changed_metadata','previous_round','previous_evidence','previous_history','failed_before','foreign_source','stage_locator','claims_locator','extra_schema'])
def test_noop_fails_closed(case,defect):
    selected,_=case;s=selected.content;p=selected.semantic
    proof=producer.read(s.dialogue_noop)
    if defect=='missing_records':(s.stage/'tests/disclosure.json').write_text('{}')
    elif defect=='nonempty':(s.stage/'tests/disclosure.json').write_text('{"agent_records":[{}]}')
    elif defect=='false_empty':Path(s.dialogue_claims['path']).write_text('{"empty":true}')
    elif defect=='changed_body':(s.stage/'instruction.md').write_text('changed')
    elif defect=='invented_notes':(s.stage/'environment/additional-assumptions.md').write_text('fake')
    elif defect=='invented_marker':(s.stage/'tests/dialogue-applied.json').write_text('{}')
    elif defect=='review':s=replace(s,dialogue_review=s.dialogue_claims)
    elif defect=='draft':s=replace(s,dialogue_reviewed_draft=s.dialogue_claims)
    elif defect=='call':p=replace(p,dialogue_call=object())
    elif defect=='writer':p=replace(p,dialogue_writer_calls=(object(),))
    elif defect=='historical':p=replace(p,historical_dialogue=object())
    elif defect=='changed_candidate':
        state=json.loads((s.naturalization_root/'state.json').read_text());(s.naturalization_root/state['final_candidate']).write_text('changed')
    elif defect=='changed_metadata':(s.stage/'task.toml').write_text('[metadata]')
    elif defect.startswith('previous_') or defect=='failed_before':
        before=producer.read(proof['before_tracker']);row=before['lane_state']['harbor-dialogue-author']
        if defect=='previous_round':row['attempt']=1
        elif defect=='previous_evidence':row['evidence_paths']=[s.dialogue_claims['path']]
        elif defect=='previous_history':row['confidence_history']=[{'level':'high','reason':'previous','revision':0}]
        else:before['lane_state']['harbor-independent-verifier']['state']='pending'
        path=Path(proof['before_tracker']['path']);path.write_text(json.dumps(before));proof['before_tracker']=record(path)
    elif defect=='foreign_source':s=replace(s,bindings={**s.bindings,'source_file':s.dialogue_claims})
    elif defect=='stage_locator':proof['stage']='/foreign/0245-outputs'
    elif defect=='claims_locator':proof['claims']=s.dialogue_noop
    else:proof['unknown']=True
    if defect in {'nonempty','missing_records','changed_body','invented_notes','invented_marker','changed_metadata'}:
        # Even a caller who supplies updated observations cannot turn altered
        # semantic content into a proven zero-call scope.
        proof['stage_tree']=producer.tree(s.stage)
    path=Path(s.dialogue_noop['path']);path.write_text(json.dumps(proof));s=replace(s,dialogue_noop=record(path))
    with pytest.raises((Exception,SystemExit)):
        noop.validate(replace(selected,content=s,semantic=p),root=ROOT)


def test_final_budget_bounds_allow_only_proved_zero_without_erasing_history(case):
    from scripts.batch import harbor_ready_transitions as ready
    selected,_=case
    before=producer.read(producer.read(selected.content.dialogue_noop)['before_tracker'])
    # This helper is budget enforcement only, not financial approval. The actual
    # no-op validator above and full proof must pass before its result is used.
    proof={'checks':{'dialogue_review_provenance':{'result':{'round':0}}}}
    assert ready._semantic_attempts(before,selected,proof)=={'harbor-instruction-naturalizer':1,'harbor-dialogue-author':0}
    for lane in ('harbor-instruction-naturalizer','harbor-dialogue-author'):
        changed=copy.deepcopy(before);changed['lane_state'][lane]['attempt']=2
        with pytest.raises(ValueError,match='consumed attempts'):ready._semantic_attempts(changed,selected,proof)
    with pytest.raises(ValueError):ready._semantic_attempts(before,replace(selected,content=replace(selected.content,dialogue_noop=None)),proof)
    for bad in (True,3,-1,'0'):
        wrong={'checks':{'dialogue_review_provenance':{'result':{'round':bad}}}}
        with pytest.raises(ValueError):ready._semantic_attempts(before,selected,wrong)
