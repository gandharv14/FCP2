import dataclasses,json
from pathlib import Path
import pytest
from scripts.batch import harbor_complete_readiness as c
from scripts.batch import harbor_readiness as base
from scripts.batch.harbor_admission import record
from test_instruction_naturalization import SOURCE,CANDIDATE,RECOVERY as R,SPANS
from test_harbor_model_review_provenance import fixture as journal
from test_harbor_semantic_readiness import set_call
ROOT=Path(R.__file__).parents[4]


def naturalization_fixture(tmp_path):
    stage=tmp_path/'0001-outputs';(stage/'tests').mkdir(parents=True);(stage/'environment').mkdir()
    instruction=stage/'instruction.md';instruction.write_text(SOURCE)
    toml=stage/'task.toml';toml.write_text('[metadata]\nname="fixture"\n[metadata.naturalizer]\nenabled=false\n')
    key=stage/'tests/answer_key.json';key.write_text(json.dumps({'targets':{'Valuation!C10':.123,'Valuation!C20':456.7}}))
    recovery=tmp_path/'naturalization';R.init_recovery(instruction,recovery,instruction_path=instruction,task_toml_path=toml,answer_key_path=key)
    pre,inp=SPANS.extract_editable_bodies(CANDIDATE.encode(),SPANS.scan_instruction(CANDIDATE.encode()))
    assert R.submit_attempt(recovery,pre,inp)['valid']
    reviewer_dir=tmp_path/'reviewer';reviewer_dir.mkdir();call,policy,request=journal(reviewer_dir,'naturalization')
    request['messages'][0]['content']='You are a fresh independent semantic reviewer. Review clause by clause.'
    request['messages'][1]['content']=json.dumps({'source':SOURCE,'candidate':(recovery/'attempt-01/candidate.md').read_text()})
    set_call(call,request,{'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':SOURCE,'candidate_claim':CANDIDATE,'preserved':True}]})
    writer_dir=tmp_path/'writer';writer_dir.mkdir();writer,_,request=journal(writer_dir,'naturalization')
    set_call(writer,request,{'preamble_body':pre.decode(),'input_body':inp.decode()})
    response=json.loads(Path(writer.response['path']).read_text());response['id']='writer-response';Path(writer.response['path']).write_text(json.dumps(response));object.__setattr__(writer,'response',record(Path(writer.response['path'])))
    value=json.loads(Path(writer.completed['path']).read_text());value['response']=writer.response;Path(writer.completed['path']).write_text(json.dumps(value));object.__setattr__(writer,'completed',record(Path(writer.completed['path'])))
    R.accept_semantic_review(recovery,message='Independent exact-context review passed',reviewer='fresh reviewer');R.apply_recovery(recovery)
    content=base.Selection('0001',stage,base.publication._tree_hash(stage)[0],{},tmp_path/'unused-normalization',recovery,{}, {},naturalization_call=call,naturalization_writer_call=writer,naturalization_request=call.request,naturalization_writer_request=writer.request,model_policy=policy)
    return c.ReadinessSelection(content,None,None,None),recovery


@pytest.mark.parametrize('change',[None,'candidate','instruction','writer_body','raw_review','budget','answer_key','metadata','source'])
def test_naturalization_lane_can_validate_before_dialogue_without_writes(tmp_path,change):
    selection,recovery=naturalization_fixture(tmp_path);s=selection.content
    paths={'candidate':recovery/'attempt-01/candidate.md','instruction':s.stage/'instruction.md','writer_body':recovery/'attempt-01/input_body.md','raw_review':Path(s.naturalization_call.response['path']),'answer_key':s.stage/'tests/answer_key.json','metadata':s.stage/'task.toml','source':recovery/'source.snapshot.md'}
    if change=='budget':
        p=recovery/'state.json';state=json.loads(p.read_text());state['attempts']=[];p.write_text(json.dumps(state))
    elif change:
        p=paths[change];p.chmod(0o600);p.write_text(p.read_text()+' changed')
    before=base.tree(s.stage);prior=base.tree(recovery)
    if change is None:
        result=c.check_naturalization(selection,root=ROOT);assert result['valid'] and result['attempts_used']==1
    else:
        with pytest.raises(Exception):c.check_naturalization(selection,root=ROOT)
    assert base.tree(s.stage)==before and base.tree(recovery)==prior


def test_evidence_enumeration_rejects_conflicting_root_bindings(tmp_path):
    p=tmp_path/'one';p.write_text('one');bound=record(p)
    assert c.evidence_records([bound,bound])==[bound]
    with pytest.raises(ValueError):c.evidence_records([bound,{**bound,'sha256':'f'*64}])


@pytest.mark.parametrize('confidence',['high','medium','low',None])
def test_composition_cannot_promote_non_high_profile_confidence(tmp_path,monkeypatch,confidence):
    from test_harbor_profile_readiness import composed
    from scripts.batch import harbor_profile_readiness as module
    selection=composed(tmp_path,'0001');value=selection.public_profiles
    def owning_validator(s,*,root):
        assert s is value and root==ROOT
        return {'confidence':confidence,'evidence':[]}
    monkeypatch.setattr(module,'validate_profiles',owning_validator)
    if confidence=='high':assert c.check_profiles(selection,root=ROOT)['confidence']=='high'
    else:
        with pytest.raises(ValueError,match='confidence'):c.check_profiles(selection,root=ROOT)
