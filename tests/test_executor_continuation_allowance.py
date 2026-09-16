from dataclasses import asdict,replace
from pathlib import Path
import json,time
import pytest
from scripts.batch import harbor_continuation_allowance as A
from scripts.batch import harbor_model_executor as E
from test_harbor_model_executor import setup,Response,response_bytes,TOKEN


def selected(tmp_path,scope):
    home=tmp_path/'allowance';home.mkdir();ledger=home/'review-reservations';ledger.mkdir()
    declaration=tmp_path/'authorization.txt';declaration.write_text('Root-authorized fixture: two additional cumulative reviews, no retries.')
    now=time.time_ns();row=A.issue(home,expected_previous=None,selected_scope=A.scope(scope.workbook_id,scope.original_sha256),
        limits=dict(takeovers=6,lease_duration_ns=14400*10**9,stage_seconds=7200,memory_bytes=16*1024**3,review_attempts=4,transport_retries=0),
        authorization=A.record(declaration),reason='bounded fixture',expires_at_ns=now+10**13,approve=lambda _:True)
    return row,ledger


def prepare_extra(tmp_path,campaign,prepare,prior,ordinal):
    p=prepare(ordinal=ordinal,limit=4)
    if ordinal==3:row,ledger=selected(tmp_path,p.scope)
    else:
        ledger=tmp_path/'allowance/review-reservations';row=A.record(tmp_path/'allowance/authorization-000001.json')
    reservation=A.reserve_review(ledger,allowance=row,semantic_scope=asdict(p.scope),prior_records=prior,original_limit=2,
              expected_ordinal=ordinal,prove_closed=lambda *args:True)
    return replace(p,review_reservation=reservation)


def test_additional_review_executes_in_same_scope_without_changing_old_limits(setup,tmp_path,monkeypatch):
    campaign,prepare=setup;calls=[]
    def opened(request,timeout):calls.append(request.data);return Response(response_bytes(first.policy))
    first=prepare();monkeypatch.setattr(E,'_open',opened)
    prepared=[first,prepare(ordinal=2)]
    results=[E.execute(p,campaign,credential=TOKEN) for p in prepared]
    old={str(p):p.read_bytes()for p in campaign.root.rglob('*')if p.is_file()}
    extra=prepare_extra(tmp_path,campaign,prepare,[r.outcome for r in results],3)
    result=E.execute(extra,campaign,credential=TOKEN)
    assert result.status=='completed' and result.call is not None and len(calls)==3
    assert all(Path(path).read_bytes()==raw for path,raw in old.items())
    plan=json.loads((E.location(extra,campaign)/'plan.json').read_bytes())
    assert plan['ordinal']==3 and plan['limit']==4 and plan['max_transport_retries']==0
    assert E.inspect(first,campaign).status==E.inspect(prepared[1],campaign).status=='completed'
    assert E.execute(extra,campaign,credential=TOKEN).status=='completed' and len(calls)==3


def test_uncertain_extra_review_cannot_be_reset_by_new_policy_or_next_ordinal(setup,tmp_path,monkeypatch):
    campaign,prepare=setup;first=prepare();calls=[]
    monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(first.policy)))
    prior=[E.execute(p,campaign,credential=TOKEN).outcome for p in (first,prepare(ordinal=2))]
    extra=prepare_extra(tmp_path,campaign,prepare,prior,3)
    def uncertain(*args):calls.append(True);raise ConnectionError('fixture lost response')
    monkeypatch.setattr(E,'_open',uncertain)
    outcome=E.execute(extra,campaign,credential=TOKEN);assert outcome.status=='incomplete' and calls==[True]
    # Even a root reservation with a mistaken closure callback cannot bypass
    # the executor's independent, real previous journal inspection.
    next_call=prepare_extra(tmp_path,campaign,prepare,prior+[outcome.outcome],4)
    with pytest.raises(E.ExecutorError,match='unresolved prior'):E.execute(next_call,campaign,credential=TOKEN)
    assert calls==[True] and not E.location(next_call,campaign).exists()


def test_rehashed_unrelated_prior_receipts_do_not_authorize_an_extra_call(setup,tmp_path,monkeypatch):
    campaign,prepare=setup;first=prepare();monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(first.policy)))
    prior=[E.execute(p,campaign,credential=TOKEN).outcome for p in (first,prepare(ordinal=2))]
    copied=tmp_path/'different-outcome.json';copied.write_bytes(Path(prior[1]['path']).read_bytes())
    extra=prepare_extra(tmp_path,campaign,prepare,[prior[0],A.record(copied)],3)
    with pytest.raises(E.ExecutorError,match='reserved prior'):E.execute(extra,campaign,credential=TOKEN)
    assert not E.location(extra,campaign).exists()
