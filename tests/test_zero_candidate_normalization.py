"""Actual tiny duplicate-closure fixture; no model or private workbook jobs."""
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import openpyxl

from scripts.batch import harbor_normalization_producer as producer
from scripts.batch import harbor_zero_candidate_normalization as zero
from scripts.batch import harbor_completed_lanes as lanes
from scripts.batch import harbor_readiness as readiness
from scripts.batch.harbor_admission import record
import test_harbor_normalization_producer as fixtures


@pytest.fixture
def case(tmp_path,monkeypatch):
    original=fixtures.write_two_variable_fixture
    def workbook(root):
        artifact,seg=original(root)
        book=openpyxl.load_workbook(artifact);sheet=book['Inputs']
        sheet['A3']='Fee';sheet['B3']=7
        for cell,value in [('Z1',0.25),('Z2',100),('AA2',110),('AB2',121),('Z3',7)]:sheet[cell]=value
        book.save(artifact);book.close()
        p=seg/'segments.json';value=json.loads(p.read_text());value['inputs'].append(dict(band='Inputs!B3',sheet='Inputs',label='Fee',kind='input',vtype='numeric',cells=['Inputs!B3']));p.write_text(json.dumps(value))
        return artifact,seg
    monkeypatch.setattr(fixtures,'write_two_variable_fixture',workbook)
    monkeypatch.setattr(fixtures,'GOOD_MODEL_TABLE',fixtures.GOOD_MODEL_TABLE.rstrip()+'\n| Fee | R0003 | Supplied internal workbook fee assumption; exact external provenance unverified. |\n')
    factory=fixtures.setup.__wrapped__(tmp_path,monkeypatch);recipe,campaign,resolver,live=factory('0001')
    prepared=producer.prepare(recipe)
    manifest=producer.read(prepared)
    assert manifest['candidate_count']==0 and manifest['request'] is None
    result=producer.read(producer.emit_phase1(recipe,prepared))
    assert result['status']=='phase1_evidence_ready'
    run=recipe.run;b=copy.deepcopy(recipe.bindings)
    b.update(normalization_draft=record(run/'draft.json'),normalized_final=record(run/'normalized.json'),normalization_report=record(run/'normalization_report.json'),normalized_phase1=record(run/'normalized.phase1.json'))
    stage=tmp_path/'stage';(stage/'tests').mkdir(parents=True);(stage/'tests/pipeline_bindings.json').write_text(json.dumps(producer.read(record(run/'production-selection.json'))['pipeline_bindings']))
    s=SimpleNamespace(workbook='0001',normalization_run=run,normalization_call=None,normalization_prepared=prepared,bindings=b,model_policy=recipe.policy,stage=stage)
    monkeypatch.setattr(lanes.static.publication,'resolve_build_context',resolver)
    return recipe,s,resolver


def test_zero_candidates_independent_reanalysis_and_both_owning_projections(case):
    recipe,s,_=case;before={p:p.read_bytes() for p in recipe.run.iterdir() if p.is_file()}
    result=zero.validate_no_candidates(s)
    assert result['candidate_count']==0 and result['analyzed_cells']==5 and result['semantic_attempts_added']==0
    assert result['mode']=='plain' and result['semantic_review_required'] is False
    assert lanes.normalization_projection(s,phase1=True)['mode']=='plain'
    assert lanes.normalization_projection(s)['mode']=='plain'
    assert readiness.normalized_content(s)['mode']=='plain'
    assert all(p.read_bytes()==v for p,v in before.items())
    assert not (recipe.run/'normalization-review.json').exists()
    assert not (recipe.run/'review-request.json').exists()


@pytest.mark.parametrize('change',['call','request','audit','analysis','evidence','manifest','code','first_count','review','forced','draft','missing_selection'])
def test_no_candidate_claim_cannot_bypass_missing_or_changed_evidence(case,change):
    recipe,s,_=case;run=recipe.run
    if change=='call':s.normalization_call=object()
    elif change=='request':s.bindings['normalization_prepared_request']={'path':'invented','sha256':'0'*64}
    elif change=='audit':Path(s.bindings['variable_source_audit']['path']).write_text('changed')
    elif change=='code':
        p=run/'production-selection.json';v=json.loads(p.read_text());v['code']={};p.write_text(json.dumps(v))
    elif change=='first_count':
        p=run/'first_normalization.json';v=json.loads(p.read_text());v['atomic_variables']=4;p.write_text(json.dumps(v))
    elif change=='review':(run/'normalization-review.json').write_text('{}')
    elif change=='forced':(run/'oracle_forced_exclusions.json').write_text('[]')
    elif change=='missing_selection':(run/'production-selection.json').unlink()
    else:
        file={'analysis':'analysis.json','evidence':'review-evidence.json','manifest':'prepared.json','draft':'draft.json'}[change]
        (run/file).write_text('{}')
    before={p:p.read_bytes() for p in run.iterdir() if p.is_file()}
    with pytest.raises((ValueError,SystemExit)):zero.validate_no_candidates(s)
    assert all(p.read_bytes()==v for p,v in before.items())


def test_mechanically_surviving_candidate_cannot_claim_no_review(tmp_path,monkeypatch):
    factory=fixtures.setup.__wrapped__(tmp_path,monkeypatch);recipe,*_=factory('0001');prepared=producer.prepare(recipe)
    b=copy.deepcopy(recipe.bindings);b['normalization_draft']=record(recipe.run/'draft.json')
    s=SimpleNamespace(workbook='0001',normalization_run=recipe.run,normalization_call=None,normalization_prepared=prepared,bindings=b,model_policy=recipe.policy)
    with pytest.raises(ValueError,match='eligible candidates require review'):zero.validate_no_candidates(s)


def test_plain_floor_and_original_first_count_remain_mandatory(case):
    recipe,s,_=case;p=recipe.run/'first_normalization.json';v=json.loads(p.read_text());v['atomic_variables']=1;p.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='cannot become plain'):zero.validate_no_candidates(s)
    with pytest.raises(ValueError):lanes.normalization_projection(s,phase1=True)


def test_two_row_zero_candidate_audit_still_fails_plain_floor(tmp_path,monkeypatch):
    original=fixtures.write_two_variable_fixture
    def workbook(root):
        artifact,seg=original(root);book=openpyxl.load_workbook(artifact);sheet=book['Inputs']
        for cell,value in [('Z1',0.25),('Z2',100),('AA2',110),('AB2',121)]:sheet[cell]=value
        book.save(artifact);book.close();return artifact,seg
    monkeypatch.setattr(fixtures,'write_two_variable_fixture',workbook)
    factory=fixtures.setup.__wrapped__(tmp_path,monkeypatch);recipe,*_=factory('0001');prepared=producer.prepare(recipe)
    result=producer.read(producer.emit_phase1(recipe,prepared))
    assert result['status']=='normalization_rejected'
    b=copy.deepcopy(recipe.bindings);b['normalization_draft']=record(recipe.run/'draft.json')
    s=SimpleNamespace(workbook='0001',normalization_run=recipe.run,normalization_call=None,normalization_prepared=prepared,bindings=b,model_policy=recipe.policy)
    with pytest.raises(ValueError,match='plain eligibility rejects'):zero.validate_no_candidates(s)


def test_context_binding_skip_applies_only_to_proven_empty_normalization(case,tmp_path):
    recipe,s,_=case
    # Existing naturalization/model Call requirements remain mandatory. This
    # fixture supplies the independent context tree records but omits that Call.
    s.naturalization_root=tmp_path/'naturalization';s.naturalization_root.mkdir();(s.naturalization_root/'evidence').write_text('fixture')
    s.pre_dialogue_witness=s.dialogue_noop=None
    s.mcp_build_a=s.mcp_build_b=None
    s.context_trees={str(p):readiness.publication._tree_hash(p)[0] for p in [recipe.run,s.naturalization_root]}
    s.normalization_records=();s.dialogue_claims=s.dialogue_review=record(s.naturalization_root/'evidence')
    s.dialogue_reviewed_draft=s.scope_evidence=s.allowlist=s.dialogue_scope_receipt=None
    s.naturalization_request=s.naturalization_writer_request=record(s.naturalization_root/'evidence')
    s.naturalization_call=s.naturalization_writer_call=None
    with pytest.raises(ValueError,match='missing required model Call'):readiness.context_bindings(s)
