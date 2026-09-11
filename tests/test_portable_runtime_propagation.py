"""Mechanical propagation only; owning runtime tests prove observation validity."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_complete_readiness as C
from scripts.batch import harbor_completed_lanes as L
from scripts.batch import harbor_promotion_evidence as P
from scripts.batch import harbor_ready_transitions as T
from scripts.batch import harbor_runtime_readiness as R


@pytest.fixture
def selection(tmp_path):
    stage=tmp_path/'0001-outputs';stage.mkdir()
    bindings={key: {} for key in ('original_raw_source','source_generation','source_file',
                                  'segmentation_generation','curation')}
    bindings['source_generation']={'path':str(tmp_path/'source')}
    bindings['segmentation_generation']={'path':str(tmp_path/'segment')}
    content=C.static.Selection('0001',stage,'d'*64,bindings,tmp_path/'norm',tmp_path/'nat',{}, {})
    runtime=R.RuntimeSelection('0001',stage,{},'plain',{})
    semantic=C.SemanticSelection(None,{}, {},None,{},())
    return C.ReadinessSelection(content,semantic,runtime,object())


@pytest.fixture(params=[False,True],ids=['native-default','explicit-producer'])
def identity(request):
    return R.RuntimeProducerIdentity('/original/bin/python','/original/bin/docker') if request.param else None


def kwargs(identity):
    return {} if identity is None else {'runtime_producer':identity}


def test_promotion_forwards_only_explicit_identity(tmp_path,monkeypatch,identity):
    from test_harbor_promotion_evidence import promoted
    path,state,source,selected,proof=promoted(tmp_path,monkeypatch)
    original=T._proof;observed=[]
    def invoke(before, selection, **received):
        observed.append(received)
        return original(before,selection)
    monkeypatch.setattr(T,'_proof',invoke)
    result=P.validate(source,path,trusted_public_key=selected.content.trusted_public_key,**kwargs(identity))
    assert result['verified_promoted'] is True
    assert observed == [kwargs(identity)]


def test_ready_proof_forwards_to_every_completed_gate_and_composition(selection,monkeypatch,identity):
    selected=selection
    before={'workbook_id':'0001','bindings':selected.content.bindings,
            'lane_state':{lane:{} for _,lane in L.STEPS}}
    observed=[]
    def gate(view, packet, context, **received):
        observed.append((packet.kind,received))
        return {'passed':True,'confidence':'high','bindings':{},'evidence':[],
                'phase':'phase1_complete' if packet.kind=='normalization_phase1' else None}
    def complete(selected,*,root,**received):
        observed.append(('complete',received))
        return {'schema_version':'harbor-complete-readiness/v1','workbook':'0001',
                'stage_sha256':'d'*64,'publication_ready':True,'blockers':[],
                'checks':{'accepted_public_profiles':{'checked':True,'result':{'confidence':'high'}}},
                'evidence':[]}
    monkeypatch.setattr(T,'record',lambda path: {'path':str(path),'sha256':'a'*64})
    monkeypatch.setattr(T.notes,'check_gate',gate)
    monkeypatch.setattr(L,'check_completed_gate',gate)
    monkeypatch.setattr(C,'complete_readiness',complete)
    monkeypatch.setattr(T.lifecycle,'check_records',lambda rows:None)
    monkeypatch.setattr(T,'_semantic_attempts',lambda *args:{})
    result=T._proof(before,selected,**kwargs(identity))
    assert result['publication_ready'] is True
    for kind,received in observed:
        assert received == ({} if kind in ('source','segmentation') else kwargs(identity))
    assert len(observed)==len(L.STEPS)+1


def test_completed_verification_forwards_to_runtime_only(selection,monkeypatch,identity):
    monkeypatch.setattr(L,'predecessors',lambda *args:'harbor-independent-verifier')
    monkeypatch.setattr(L,'pair_and_audit',lambda *args:[])
    monkeypatch.setattr(C,'evidence_records',lambda value:[])
    monkeypatch.setattr(L.static,'tree',lambda stage:{})
    observed=[]
    def runtime(selected,observation,*,root,**received):
        observed.append(received)
        return {}
    monkeypatch.setattr(R,'validate_runtime',runtime)
    result=L.check_completed_gate({'workbook_id':'0001'},L.CompletionEvidence('verification',selection),
                                  object(),**kwargs(identity))
    assert result['passed'] is True
    assert observed == [({} if identity is None else {'producer_identity':identity})]


def test_complete_readiness_forwards_runtime_identity_preserving_other_gates(selection,monkeypatch,identity):
    observed=[]
    monkeypatch.setattr(C.static,'tree',lambda stage:{})
    monkeypatch.setattr(C.static.publication,'_tree_hash',lambda stage:('d'*64,{}))
    monkeypatch.setattr(C.static,'inspect_readiness',lambda *args,**kw:{'checks':{},'blockers':[]})
    monkeypatch.setattr(C,'evidence_records',lambda value:[])
    for name in ('check_disclosure','check_naturalization','check_dialogue','check_profiles'):
        def gate(selected,*,root,_name=name):
            observed.append((_name,{}));return {}
        monkeypatch.setattr(C,name,gate)
    monkeypatch.setattr(C.static,'context_bindings',lambda *args:{})
    monkeypatch.setattr(C,'selected_content_evidence',lambda *args:{})
    def runtime(selected,observation,*,root,**received):
        observed.append(('runtime',received));return {}
    monkeypatch.setattr(C,'validate_runtime',runtime)
    result=C.complete_readiness(selection,**kwargs(identity))
    assert result['publication_ready'] is True
    assert observed[-1] == ('runtime',{} if identity is None else {'producer_identity':identity})
    assert all(received=={} for name,received in observed if name!='runtime')


def test_runtime_failure_still_blocks_complete_readiness(selection,monkeypatch):
    monkeypatch.setattr(C.static,'tree',lambda stage:{})
    monkeypatch.setattr(C.static.publication,'_tree_hash',lambda stage:('d'*64,{}))
    monkeypatch.setattr(C.static,'inspect_readiness',lambda *args,**kw:{'checks':{},'blockers':[]})
    monkeypatch.setattr(C,'evidence_records',lambda value:[])
    for name in ('check_disclosure','check_naturalization','check_dialogue','check_profiles'):
        monkeypatch.setattr(C,name,lambda *args,**kw:{})
    monkeypatch.setattr(C.static,'context_bindings',lambda *args:{})
    monkeypatch.setattr(C,'selected_content_evidence',lambda *args:{})
    def rejected(*args,**kw):raise ValueError('invalid producer identity')
    monkeypatch.setattr(C,'validate_runtime',rejected)
    result=C.complete_readiness(selection,runtime_producer=object())
    assert result['publication_ready'] is False
    assert result['blockers']==['runtime_observation']
