"""The private recovery cannot substitute the prepared request or use a spent scope."""
import importlib.util,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def recovery(monkeypatch):
    p=ROOT/'diagnostics/requested-fixes-20260910/recover_0184_partition_batch0.py'
    spec=importlib.util.spec_from_file_location('batch0_recovery_tests',p);D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
    env=D.setup();original,root,C,P,w,s,controls,recipe=env
    from scripts.batch import harbor_disclosure_partition_review as R
    permit,p=P.permit(w);plan=D.read(p['review_plan']);projection=D.read(plan['projection'])
    # Full E.plan/current-input equivalence was independently run on the actual
    # payload. These tests isolate the pretransport identity/absence boundary.
    monkeypatch.setattr(R,'validate_plan',lambda *a,**k:(plan,projection,[]))
    return D,env

def test_saved_request_uuid_and_empty_executor_are_retained(recovery):
    D,env=recovery
    lease,tracker,renewal,request_binding,request,validated,permit,journal=D.preflight(env)
    assert request_binding==D.REQUEST and request.prepared.attempt_id==D.ATTEMPT_ID
    assert renewal==D.ORIGINAL_RENEWAL and lease.revision==21 and not journal.exists()
    assert not D.HOME.exists()

@pytest.mark.parametrize('fault',['uuid','saved-request','renewal'])
def test_no_new_identity_can_replace_the_preserved_request(recovery,monkeypatch,fault):
    D,env=recovery
    if fault=='uuid':monkeypatch.setattr(D,'ATTEMPT_ID','00000000-0000-0000-0000-000000000000')
    elif fault=='saved-request':monkeypatch.setattr(D,'REQUEST',{**D.REQUEST,'sha256':'0'*64})
    else:monkeypatch.setattr(D,'ORIGINAL_RENEWAL',{**D.ORIGINAL_RENEWAL,'sha256':'0'*64})
    with pytest.raises(AssertionError):D.preflight(env)
    assert not D.HOME.exists()

def test_any_existing_executor_location_stops_recovery(recovery,monkeypatch,tmp_path):
    D,env=recovery;env[2].worker.executor.location
    occupied=tmp_path/'attempt-01';occupied.mkdir()
    monkeypatch.setattr(env[2].worker.executor,'location',lambda *a:occupied)
    with pytest.raises(AssertionError,match='already used or uncertain'):D.preflight(env)
    assert not D.HOME.exists()
