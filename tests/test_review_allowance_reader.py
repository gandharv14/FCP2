from pathlib import Path
from types import SimpleNamespace
import copy,json
import pytest
from scripts.batch import harbor_review_allowance_reader as reader,harbor_ready_transitions as ready,harbor_continuation_allowance as A,harbor_readiness as static
from test_continuation_allowance import policy


def build(tmp_path,monkeypatch,n=3):
    _,allowance,_=policy(tmp_path,reviews=5)
    scope=dict(workbook_id='0245',original_sha256='a'*64,lane='harbor-dialogue-author',budget_id='dialogue-review')
    def row(name,value):p=tmp_path/name;p.write_text(json.dumps(value));return A.record(p)
    outcomes=[row('outcome%d.json'%i,dict(ordinal=i,terminal=True))for i in range(1,n+1)]
    requests=[row('request%d.json'%i,dict(round=i))for i in range(1,n+1)]
    ledger=Path(allowance['path']).parent/'review-reservations';ledger.mkdir()
    reservation=A.reserve_review(ledger,allowance=allowance,semantic_scope=scope,prior_records=outcomes[:-1],original_limit=2,expected_ordinal=n,prove_closed=lambda *args:True,clock=lambda:2,cumulative_limit=n)
    witness=dict(scope=scope,reservation=reservation,ordinal=n,limit=n,requests=requests,outcomes=outcomes)
    chosen=SimpleNamespace(content=SimpleNamespace(workbook='0245',bindings=dict(original_raw_source=dict(sha256='a'*64)),naturalization_root=tmp_path/'nat',dialogue_noop=None))
    proof=dict(checks=dict(dialogue_review_provenance=dict(result=dict(round=n))))
    before=dict(lane_state={'harbor-dialogue-author':dict(attempt=2),'harbor-instruction-naturalizer':dict(attempt=1)})
    monkeypatch.setattr(static,'skill_module',lambda *a:SimpleNamespace(_read_state=lambda p:{},_attempts_used=lambda p:1))
    module=SimpleNamespace(__file__=ready.__file__,_semantic_attempts=ready._semantic_attempts)
    selected=reader.install(module,expected_code=A.record(Path(ready.__file__)),validate=lambda *args:copy.deepcopy(witness))
    return selected,chosen,proof,before,witness,module


@pytest.mark.parametrize('count',[3,4])
def test_actual_reservation_extends_only_dialogue_limit(tmp_path,monkeypatch,count):
    f,s,p,b,w,m=build(tmp_path,monkeypatch,count)
    assert f(b,s,p)=={'harbor-dialogue-author':count,'harbor-instruction-naturalizer':1}
    with pytest.raises(ValueError):ready._semantic_attempts(b,s,p)
    assert reader.install(m,expected_code=A.record(Path(ready.__file__)),validate=lambda *a:w)(b,s,p)['harbor-dialogue-author']==count


@pytest.mark.parametrize('change',['duplicate_request','ordinal','scope','outcome','limit'])
def test_unsupported_allowance_or_consumption_rejected(tmp_path,monkeypatch,change):
    f,s,p,b,w,m=build(tmp_path,monkeypatch)
    if change=='duplicate_request':w['requests'][1]=w['requests'][0]
    elif change=='ordinal':w['ordinal']=4
    elif change=='scope':w['scope']['workbook_id']='9999'
    elif change=='outcome':w['outcomes'][0]=w['outcomes'][1]
    else:w['limit']=4
    with pytest.raises(ValueError):f(b,s,p)
