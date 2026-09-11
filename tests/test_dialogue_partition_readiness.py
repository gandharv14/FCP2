"""Typed partition provenance cannot silently fall through the legacy Call gate."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace,ModuleType
import json,sys
import pytest
from scripts.batch import harbor_complete_readiness as C,harbor_transition_codec as K
from scripts.batch.harbor_admission import record


def test_absent_partition_field_preserves_old_codec_bytes():
    old=C.SemanticSelection(None,{}, {},None,{},())
    encoded=K.encode(old)
    assert 'dialogue_partition_aggregate' not in encoded['value']
    assert K.encode(K.decode(encoded))==encoded
    current=replace(old,dialogue_partition_aggregate={'path':'/bound/aggregate.json','sha256':'a'*64})
    assert K.decode(K.encode(current))==current
    bad={**encoded,'value':{**encoded['value'],'unrecognized_aggregate':K.encode(None)}}
    with pytest.raises(ValueError):K.decode(bad)


@pytest.fixture
def selected(tmp_path,monkeypatch):
    def bind(name,value):
        path=tmp_path/name;path.parent.mkdir(exist_ok=True,parents=True)
        path.write_text(json.dumps(value) if not isinstance(value,str) else value)
        return record(path)
    notes=bind('environment/additional-assumptions.md','reviewed draft')
    answer=bind('tests/answer_key.json',{})
    draft=bind('reviewed.md','reviewed draft');claims=bind('claims.json',{'claims':[]})
    report=bind('review.json',{'actual_partition_findings':[]})
    aggregate=bind('aggregate.json',{'typed_fixture':True})
    source=bind('source.xlsx','sourcebytes');raw=bind('original.xlsx','originalbytes')
    p=C.SemanticSelection(None,{}, {},None,claims,(),dialogue_partition_aggregate=aggregate)
    s=SimpleNamespace(stage=tmp_path,workbook='fixture',dialogue_noop=None,dialogue_claims=claims,
        dialogue_reviewed_draft=draft,dialogue_review=report,bindings={'source_file':source,'original_raw_source':raw},model_policy='policy')
    calls=[];module=ModuleType('scripts.batch.harbor_dialogue_partition_review')
    expected=dict(claims=claims,reviewed_claims=claims,draft=draft,notes=notes,policy='policy',workbook='fixture',
        original_sha256=raw['sha256'],source=source,answer_key=answer,task_dir=tmp_path,root=C.ROOT)
    def validate(binding,**kwargs):
        assert binding==aggregate and kwargs==expected
        calls.append(binding)
        return {'review':{'actual_partition_findings':[]},'actual_review_calls':2,'actual_author_calls':4,'evidence':[source,raw]}
    module.validate_aggregate=validate;monkeypatch.setitem(sys.modules,module.__name__,module)
    return SimpleNamespace(content=s,semantic=p),calls,module


def test_current_context_and_all_evidence_flow_through_owning_validator(selected):
    value,calls,_=selected;result=C.check_dialogue(value)
    assert len(calls)==1 and result['actual_author_calls']==4 and result['actual_review_calls']==2
    assert value.semantic.dialogue_partition_aggregate in result['evidence']
    assert 'provenance' not in result


@pytest.mark.parametrize('fault',['legacy_call','legacy_authors','historical','repair','noop','report','aggregate_rejected'])
def test_ambiguous_or_unvalidated_partition_provenance_never_passes(selected,fault):
    value,calls,module=selected;p=value.semantic
    if fault=='legacy_call':value.semantic=replace(p,dialogue_call=object())
    elif fault=='legacy_authors':value.semantic=replace(p,dialogue_writer_calls=(object(),))
    elif fault=='historical':value.semantic=replace(p,historical_dialogue=object())
    elif fault=='repair':value.semantic=replace(p,dialogue_pre_review_repair={})
    elif fault=='noop':value.content.dialogue_noop={}
    elif fault=='report':
        path=Path(value.content.dialogue_review['path']);path.write_text('{}');value.content.dialogue_review=record(path)
    else:
        def reject(*args,**kwargs):raise ValueError('actual incomplete aggregate rejected')
        module.validate_aggregate=reject
    with pytest.raises(ValueError):C.check_dialogue(value)
    if fault not in ('report','aggregate_rejected'):assert not calls


def test_missing_both_call_and_aggregate_fails_closed(selected):
    value,_,_=selected;value.semantic=replace(value.semantic,dialogue_partition_aggregate=None)
    with pytest.raises(ValueError,match='Call or complete partition'):C.check_dialogue(value)


def test_disclosure_partition_selection_accepts_absent_revision(tmp_path,monkeypatch):
    """Regression for b3's missing optional dataclass fields before final consume."""
    instruction=tmp_path/'instruction.md';instruction.write_text('exact reviewed input')
    (tmp_path/'tests').mkdir();disclosure=tmp_path/'tests/disclosure.json';disclosure.write_text('{}')
    source=tmp_path/'golden.xlsx';source.write_text('golden')
    delivered=tmp_path/'delivered.xlsx';delivered.write_text('delivered')
    manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    aggregate=tmp_path/'aggregate.json';aggregate.write_text('{}')
    p=C.SemanticSelection(None,record(manifest),record(instruction),None,None,(),
        disclosure_partition_aggregate=record(aggregate))
    assert p.disclosure_revision is None
    s=SimpleNamespace(stage=tmp_path,naturalization_root=tmp_path/'unused',workbook='fixture',
        bindings={'source_file':record(source)},model_policy=None)
    module=ModuleType('scripts.batch.harbor_disclosure_partition_review')
    def validate(binding,manifest_binding,**kwargs):
        assert binding==record(aggregate) and manifest_binding==record(manifest)
        assert kwargs['instruction']==record(instruction) and kwargs['disclosure']==record(disclosure)
        return {'actual_review_calls':2,'evidence':[]}
    module.validate_aggregate=validate;monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setattr(C.static,'skill_module',lambda *a:object())
    monkeypatch.setattr(C,'delivered_workbook',lambda *a:delivered)
    result=C.check_disclosure(SimpleNamespace(content=s,semantic=p))
    assert result['actual_review_calls']==2 and result['instruction_applicability']=='staged_instruction'


# Shared deterministic executor-journal fixture; no model transport is enabled.
from test_dialogue_partitions import completed,planned,setup


def test_owning_readiness_reconsumes_all_real_partition_call_journals(completed,tmp_path):
    from scripts.batch import harbor_dialogue_partition_review as R,harbor_instruction_producer as P
    task,claims,projection,plan,authority,writers,paras,reviews,draft=completed
    def bind(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value));return record(path)
    aggregate=R.aggregate(authority,writers,paras,reviews,draft,task.policy,root=C.ROOT,task_dir=task.stage)
    aggregate_binding=bind('owning-aggregate.json',aggregate)
    claim_binding=bind('owning-claims.json',claims);review=bind('owning-review.json',aggregate['review'])
    validator=P.skill_module(C.ROOT,'additional-assumptions-dialogue','validate_dialogue')
    (task.stage/'environment/additional-assumptions.md').write_text(validator.strip_claim_comments(Path(draft['path']).read_text()))
    tracker=P.read(task.tracker)
    p=C.SemanticSelection(None,{}, {},None,claim_binding,(),dialogue_partition_aggregate=aggregate_binding)
    s=SimpleNamespace(stage=task.stage,workbook=tracker['workbook_id'],dialogue_noop=None,
        dialogue_claims=claim_binding,dialogue_reviewed_draft=draft,dialogue_review=review,
        bindings=tracker['bindings'],model_policy=task.policy)
    result=C.check_dialogue(SimpleNamespace(content=s,semantic=p))
    assert result['actual_author_calls']==6 and result['actual_review_calls']==3
    assert len(result['review']['accuracy']['claims'])==3 and 'provenance' not in result
    Path(reviews[1].response['path']).write_text('{}')
    with pytest.raises(ValueError):C.check_dialogue(SimpleNamespace(content=s,semantic=p))
