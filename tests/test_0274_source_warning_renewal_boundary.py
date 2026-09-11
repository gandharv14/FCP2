"""Staged renewal integration with real isolated lifecycle CAS.

Financial prefix and warning-proof validation are independent owning gates; their
proofs are covered by the actual-data tests. This suite tests the new routing,
retention and budget fence, never a production tracker or fabricated checkpoint.
"""
from dataclasses import replace
from pathlib import Path
import importlib.util
import pytest
from test_instruction_lease_renewal import boundary
from test_instruction_worker import case,instruction_setup
from scripts.batch import harbor_instruction_worker as W,harbor_dialogue_warning_requalification as Q
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_workbook_bootstrap import Lease

@pytest.fixture
def staged(boundary,monkeypatch,tmp_path):
    file=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/code-0274-source-warning-v3/scripts/batch/harbor_instruction_lease_renewal.py'
    if not file.exists():pytest.skip('frozen campaign component unavailable')
    spec=importlib.util.spec_from_file_location('staged_warning_renewal',file);R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
    worker,s,l,cp,snap,path=boundary
    monkeypatch.setattr(R,'_prefix',lambda *a:[]);monkeypatch.setattr(R,'changes',lambda *a:[]);monkeypatch.setattr(R,'historical_code',lambda *a:None)
    evidence=W.keep(tmp_path/'verified-warning-evidence.json',{'fixture':'independently checked source proof'})
    value=dict(source_first_round=True,round=1,old_snapshot={'sha256':'5b47506d75ba350926ccb81946dea1789ac6f20d539342f6ae26387c6e51545b'},semantic_budget_reset=False,model_calls=0,blocked=evidence,tracker=evidence,review=evidence,original_receipt=evidence,source_warning_proof={'evidence':[evidence]})
    permit=W.keep(tmp_path/'warning.json',value);worker=replace(worker,warning_requalification=permit)
    called=[]
    def validate(worker,before):
        called.append(worker.warning_requalification);return read(worker.warning_requalification)
    monkeypatch.setattr(Q,'validate',validate)
    return R,worker,s,l,cp,snap,path,permit,called

def test_validated_warning_retained_across_real_completed_boundary_cas(staged):
    R,w,s,l,cp,snap,path,warning,called=staged;before=read(record(path));oldcp=Path(cp['path']).read_bytes()
    result=R.authorize(w,s,l,cp,snap,duration_ns=200,clock=lambda:30)
    after=read(record(path));permit=read(read(result)['permit']);new=Lease(**read(result)['lease'])
    assert called==[warning] and warning in permit['history']
    assert permit['prior_continuations']['warning_requalification']==warning
    assert after['revision']==before['revision']+1 and after['lane_state']==before['lane_state']
    assert new.owner==l.owner and new.token==l.token and Path(cp['path']).read_bytes()==oldcp
    assert R.validate(replace(w,lease_renewal=result),after)==permit

@pytest.mark.parametrize('field,value',[('source_first_round',False),('round',2),('semantic_budget_reset',True),('model_calls',1),('old_snapshot',{'sha256':'0'*64})])
def test_warning_budget_or_source_authority_change_stops_before_cas(staged,field,value):
    R,w,s,l,cp,snap,path,warning,called=staged;before=path.read_bytes();p=read(warning);p[field]=value
    Path(warning['path']).write_bytes(R.life.canonical(p) if hasattr(R,'life') else __import__('json').dumps(p).encode())
    w=replace(w,warning_requalification=record(warning['path']))
    with pytest.raises(ValueError):R.authorize(w,s,l,cp,snap,duration_ns=200,clock=lambda:30)
    assert path.read_bytes()==before and not (w.run/'lease-renewals').exists()

def test_owning_warning_rejection_stops_before_reservation(staged,monkeypatch):
    R,w,s,l,cp,snap,path,warning,called=staged;before=path.read_bytes()
    def reject(*a):raise ValueError('source/claim/proof mismatch')
    monkeypatch.setattr(Q,'validate',reject)
    with pytest.raises(ValueError):R.authorize(w,s,l,cp,snap,duration_ns=200,clock=lambda:30)
    assert path.read_bytes()==before and not (w.run/'lease-renewals').exists()
