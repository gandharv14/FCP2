from dataclasses import asdict,replace
from pathlib import Path
import json
import pytest
from scripts.batch import harbor_model_executor as E,harbor_continuation_allowance as A
from scripts.fleet.dialogue_revision_review import historical_call
from test_harbor_model_executor import setup,Response,response_bytes,TOKEN
from test_executor_continuation_allowance import selected

def test_exact_two_historical_calls_then_narrow_third_round(setup,tmp_path,monkeypatch):
    campaign,prepare=setup;old=[prepare(),prepare(ordinal=2)];count=[]
    def opened(*args):count.append(1);return Response(response_bytes(old[0].policy))
    monkeypatch.setattr(E,'_open',opened);results=[E.execute(p,campaign,credential=TOKEN)for p in old]
    kept={str(p):p.read_bytes()for p in campaign.root.rglob('*')if p.is_file()}
    for ordinal,r in enumerate(results,1):
        p,observed=historical_call(E,r.call,campaign,asdict(old[0].scope),ordinal)
        assert p==old[ordinal-1] and observed.call==r.call
    allowance,ledger=selected(tmp_path,old[0].scope)
    reservation=A.reserve_review(ledger,allowance=allowance,semantic_scope=asdict(old[0].scope),prior_records=[r.outcome for r in results],original_limit=2,expected_ordinal=3,prove_closed=lambda row,scope,n:historical_call(E,results[n-1].call,campaign,scope,n)[1].outcome==row,cumulative_limit=3)
    p=replace(prepare(ordinal=3,limit=3),review_reservation=reservation)
    r=E.execute(p,campaign,credential=TOKEN)
    assert r.status=='completed' and count==[1,1,1]
    assert all(Path(p).read_bytes()==raw for p,raw in kept.items())
    v=json.loads((E.location(p,campaign)/'plan.json').read_bytes())
    assert v['ordinal']==v['limit']==3 and v['max_transport_retries']==0
    with pytest.raises(Exception):historical_call(E,results[0].call,campaign,{**asdict(old[0].scope),'workbook_id':'9999'},1)
    with pytest.raises(Exception):historical_call(E,results[0].call,campaign,asdict(old[0].scope),2)
