"""The validator-only continuation cannot change prepared calls or code roles."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_disclosure_request_recovery as Q
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_instruction_producer as P
from scripts.batch.harbor_admission import record


def put(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E.canonical(value));return record(path)


@pytest.fixture
def profile(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';source=Path(P.__file__).read_text()
    for before,after in reversed(Q.EDITS):
        assert source.count(after)==1;source=source.replace(after,before,1)
    names={Q.PFILE:source,Q.RFILE:'original reviewed revision adapter','xl_seg/evaluate.py':'original financial evaluator'}
    files=[]
    for name,value in names.items():
        path=old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value);files.append(dict(relative=name,sha256=record(path)['sha256']))
        target=new/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
    (new/Q.PFILE).write_text(Path(P.__file__).read_text());(new/Q.RFILE).write_text('explicit validator routing')
    (new/Q.SELF).write_text('owning validator recovery')
    snapshot=put(tmp_path/'snapshot.json',dict(root=str(old),files=files))
    original={'current_code':{str(old/Q.RFILE):record(old/Q.RFILE)['sha256']}}
    return snapshot,new,original


def test_exact_bytes_fix_and_owned_routing_profile(profile):
    snapshot,new,original=profile
    assert Q.PFILE in Q.implementation(snapshot,new,original)


@pytest.mark.parametrize('kind',['evaluator','prompt','budget','unrelated'])
def test_profile_rejects_nonvalidator_changes(profile,kind):
    snapshot,new,original=profile
    if kind=='evaluator':(new/'xl_seg/evaluate.py').write_text('looser financial evaluation')
    if kind=='prompt':(new/Q.PFILE).write_text((new/Q.PFILE).read_text()+'\n# unreviewed request change\n')
    if kind=='budget':(new/Q.PFILE).write_text((new/Q.PFILE).read_text().replace('disclosure-review-revision-02','disclosure-review-revision-03'))
    if kind=='unrelated':(new/'unrelated.py').write_text('extra code')
    with pytest.raises(ValueError):Q.implementation(snapshot,new,original)


def test_existing_executor_scope_cannot_be_reopened(tmp_path,monkeypatch):
    location=tmp_path/'scope/attempt-01';location.parent.mkdir()
    monkeypatch.setattr(E,'location',lambda *a:location)
    monkeypatch.setattr(E,'prepared_plan',lambda *a:pytest.fail('do not touch an existing scope'))
    with pytest.raises(ValueError,match='already exists'):Q.absent(SimpleNamespace(prepared=None),None)


@pytest.mark.parametrize('changed',[None,'code','original','physical'])
def test_historical_validator_requires_exact_inventory_original_and_physical_receipt(tmp_path,monkeypatch,changed):
    from scripts.batch import harbor_instruction_lease_renewal as L
    inventory={str(tmp_path/'old.py'):'checked-historical-digest'}
    original={'path':'original.xlsx','sha256':'original'}
    physical={'path':'physical.json','sha256':'physical'}
    worker=SimpleNamespace(root=tmp_path,run=tmp_path/'run',disclosure_revision={'path':'revision','sha256':'revision'},disclosure_repair=physical)
    p={'current_code':inventory,'original':original,'predecessor':physical}
    if changed=='code':p['current_code']={str(tmp_path/'other.py'):'checked-historical-digest'}
    elif changed=='original':p['original']={**original,'sha256':'different'}
    elif changed=='physical':p['predecessor']={**physical,'sha256':'different'}
    monkeypatch.setattr(L,'_historical_inventory',lambda *a:inventory)
    def receipt(binding,*,_historical_root=None):
        assert binding==worker.disclosure_revision and _historical_root==worker.root
        return p,{'worker_run':str(worker.run)},None,None
    monkeypatch.setattr(Q,'receipt',receipt)
    if changed:
        with pytest.raises(ValueError,match='foreign historical'):Q.validate_historical_integrity(worker,{'bindings':{'original_raw_source':original}}, {})
    else:
        assert Q.validate_historical_integrity(worker,{'bindings':{'original_raw_source':original}}, {})==p


@pytest.mark.parametrize('changed',[None,'missing','ambiguous','tamper'])
def test_completed_review_reader_pins_unique_actual_implementation(tmp_path,monkeypatch,changed):
    old=tmp_path/'old';path=old/Q.SELF;path.parent.mkdir(parents=True);path.write_text('historical validator')
    code={str(path):record(path)['sha256']}
    if changed=='missing':code={}
    elif changed=='ambiguous':code[str(tmp_path/'other'/Q.SELF)]='other'
    elif changed=='tamper':path.write_text('changed')
    recovery=put(tmp_path/'recovery.json',{'current_code':code})
    binding=put(tmp_path/'revision.json',{'validation_recovery':recovery})
    calls=[]
    def receipt(selected,*,_historical_root=None):
        assert selected==binding and _historical_root==old
        calls.append(selected);return 'checked completed receipt'
    monkeypatch.setattr(Q,'receipt',receipt)
    if changed:
        with pytest.raises(ValueError):Q.completed_receipt(binding)
        assert calls==[]
    else:
        assert Q.completed_receipt(binding)=='checked completed receipt'
        assert calls==[binding]
