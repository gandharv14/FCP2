"""Alias proof fails closed on changed source/value/provenance witnesses."""
from pathlib import Path
import importlib.util,copy,json
import pytest
import gen_normalizer as G

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'diagnostics/requested-fixes-20260910/0274-alias-development/scripts/batch/harbor_normalization_alias_successor.py'
spec=importlib.util.spec_from_file_location('alias_successor_under_test',MODULE);A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)

def fixtures(tmp_path,monkeypatch):
    monkeypatch.setattr(A,'validate_spec',lambda value:None)
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    def var(vid,source):return dict(id=vid,name='Security expense',entity='Entity',period='2025',scenario='Actual',basis='reported',unit='USD',status='Supplied workbook input',value=123,question='What expense?',source_notes='same note',workbook={'cells':['Model!A1']},sources=[{'id':source,'name':source}])
    a,b=var('a','first'),var('b','second');original=dict(schema_version='2.0.0',environment_id='fcp-0274',seed=274,variables=[a,b])
    aliases=dict(schema_version='same-physical-atom-aliases/v1',original_entries=2,canonical_atoms=1,groups=[dict(canonical_id='a',cells=['Model!A1'],aliases=[{'id':'a','sources':a['sources'],'definition_sha256':'a'*64},{'id':'b','sources':b['sources'],'definition_sha256':'b'*64}])])
    chosen=copy.deepcopy(a);chosen['sources']=a['sources']+b['sources'];current={**original,'variables':[chosen],'variable_aliases':aliases}
    for folder,name,value in [(old,'normalized.json',original),(new,'normalized.phase1.json',current),(old,'exclusions.json',{'exclusions':[]}),(new,'exclusions.json',{'exclusions':[]})]:(folder/name).write_text(json.dumps(value))
    return old,new,aliases

def test_same_atom_source_union_projection(tmp_path,monkeypatch):
    old,new,aliases=fixtures(tmp_path,monkeypatch)
    assert len(A.prove_outputs(old,new,aliases)['variables'])==1

@pytest.mark.parametrize('field',['value','workbook','sources','question','variable_aliases'])
def test_successor_value_identity_source_and_alias_tampering(tmp_path,monkeypatch,field):
    old,new,aliases=fixtures(tmp_path,monkeypatch);p=new/'normalized.phase1.json';v=json.loads(p.read_text())
    if field=='variable_aliases':v[field]={}
    else:v['variables'][0][field]=None
    p.write_text(json.dumps(v))
    with pytest.raises(ValueError):A.prove_outputs(old,new,aliases)

def test_eligibility_exclusion_change_rejected(tmp_path,monkeypatch):
    old,new,aliases=fixtures(tmp_path,monkeypatch);(new/'exclusions.json').write_text('{}')
    with pytest.raises(ValueError,match='eligibility'):A.prove_outputs(old,new,aliases)

def phase_boundary(tmp_path,monkeypatch):
    run=tmp_path/'run';run.mkdir();names=(*A.OUTPUTS,'normalize_0274.py');witnesses={}
    for name in names:
        (run/name).write_text('first:'+name);witnesses[name]=A.record(run/name)
    def phase2(*args):
        assert all((run/n).read_text()=='second:'+n for n in names),'mixed or tampered stage'
    monkeypatch.setattr(A,'validate_emission',phase2)
    return run,names,witnesses

def test_complete_phase1_survives_catalog_boundary(tmp_path,monkeypatch):
    run,names,witnesses=phase_boundary(tmp_path,monkeypatch)
    assert A.validate_live_emission('0274',None,None,run,None,witnesses)=='immutable_phase1'

def test_complete_current_phase2_passes(tmp_path,monkeypatch):
    run,names,witnesses=phase_boundary(tmp_path,monkeypatch)
    for n in names:(run/n).write_text('second:'+n)
    assert A.validate_live_emission('0274',None,None,run,None,witnesses)=='current_phase2'

@pytest.mark.parametrize('changed',['normalized.json','normalization_report.json','exclusions.json','normalize_0274.py'])
def test_hybrid_phase_outputs_rejected(tmp_path,monkeypatch,changed):
    run,names,witnesses=phase_boundary(tmp_path,monkeypatch);(run/changed).write_text('second:'+changed)
    with pytest.raises(AssertionError,match='mixed'):A.validate_live_emission('0274',None,None,run,None,witnesses)

def test_phase_witness_inventory_must_be_complete(tmp_path,monkeypatch):
    run,names,witnesses=phase_boundary(tmp_path,monkeypatch);witnesses.pop('normalized.json')
    with pytest.raises(ValueError,match='inventory'):A.validate_live_emission('0274',None,None,run,None,witnesses)

def test_original_receipt_hash_tampering_rejected(tmp_path):
    p=tmp_path/'receipt.json';p.write_text('{}')
    with pytest.raises(ValueError):A.read({'path':str(p),'sha256':'0'*64})

@pytest.mark.parametrize('role',['current_refresh','current_source','historical_helper'])
def test_phase_boundary_code_role_tampering_rejected(monkeypatch,role):
    base=ROOT/'diagnostics/requested-fixes-20260910';current=base/'code-0274-alias-v4';old=base/'code-0274-alias-v2'
    monkeypatch.setattr(A,'ROOT',current)
    target={'current_refresh':current/'scripts/batch/harbor_environment_refresh.py','current_source':current/'gen_normalizer.py','historical_helper':old/'scripts/batch/harbor_normalization_alias_successor.py'}[role]
    original=A.record
    def observed(path):
        value=original(path)
        return {**value,'sha256':'0'*64} if Path(path)==target else value
    monkeypatch.setattr(A,'record',observed)
    with pytest.raises(ValueError):A.current({'path':str(base/'0274-alias-snapshot-v2.json'),'sha256':'0484d0ae1ae344506f2a2ed9a886aad010a6a987b819d75c179b1bc14443ed0a'})
