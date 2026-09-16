"""Real executor journals prove remaining attempts preserve a rejected producer."""
from pathlib import Path
import json,uuid
from dataclasses import replace
import pytest
from test_harbor_profile_producer import environment,write,transport
from scripts.batch import harbor_profile_producer as P,harbor_profile_readiness as R,harbor_profile_correction as Q


def selected(environment,monkeypatch):
    fixture,root=environment;inp,profile,campaign=fixture();old=P.prepare(inp,root/'original-prepared')
    seen=transport(monkeypatch,profile,change='producer_invalid');stopped=P.run(old,dispatch=True,credential='test-private');assert stopped['status']=='STOPPED'and len(seen)==1
    auth=write(root/'authority.json',{'bounded':'remaining two-attempt lane'});monkeypatch.setattr(Q,'AUTH',auth['sha256'])
    correction=write(root/'correction.json',dict(schema_version=Q.SCHEMA,helper=P.record(Q.__file__),original_plan=old,stop=stopped['evidence'][0],producer=P.record(P.__file__),reader=P.record(R.__file__),code_root=str(P.ROOT),source=P.read(inp)['original_source'],phase1=P.read(inp)['phase1'],inventory=P.read(inp)['inventory'],captures=P.read(inp)['captures'],authorization=auth,attempt_id=str(uuid.uuid4())))
    new=write(root/'next-input.json',dict(P.read(inp),schema_version=P.CORRECTION_INPUT,producer_recovery=correction))
    return root,profile,campaign,old,correction,new


def test_second_producer_then_first_independent_review_no_replay(environment,monkeypatch):
    root,profile,campaign,old,correction,inp=selected(environment,monkeypatch)
    before={str(p):p.read_bytes()for p in root.rglob('*')if p.is_file()}
    q=P.prepare(inp,root/'remaining-prepared');calls=transport(monkeypatch,profile)
    result=P.run(q,dispatch=True,credential='test-private');assert result['status']=='COMPLETE',result
    assert len(calls)==2 and result['model_calls']==3 and len(calls[0]['messages'])==2
    assert 'correction'in json.loads(calls[0]['messages'][1]['content'])
    call=result['selection'].calls[0];assert call.producer_recovery==correction
    assert P.read(call.producer.submitted)['semantic_ordinal']==2
    assert P.read(call.reviewer.submitted)['semantic_ordinal']==1
    assert R.validate_profiles(result['selection'])['accepted_profiles']==1
    assert all(Path(p).read_bytes()==raw for p,raw in before.items())
    assert P.run(q,dispatch=True,credential='test-private')['receipt']==result['receipt']and len(calls)==2


@pytest.mark.parametrize('change',['source','captures','failure','ordinal','successful','attempt','reviewed'])
def test_correction_rejects_changed_or_already_consumed_boundary(environment,monkeypatch,change):
    root,profile,campaign,old,row,inp=selected(environment,monkeypatch);q=P.read(row)
    if change=='source':q['source']=write(root/'foreign.json',{'foreign':True})
    elif change=='captures':q['captures']=write(root/'foreign.json',{'sources':[]})
    elif change=='failure':
        v=P.read(q['stop']);v['error_sha256']='f'*64;q['stop']=write(Path(q['stop']['path']),v)
    elif change=='ordinal':
        op=Path(P.read(q['stop'])['outcomes'][0]['path']).parent;v=P.read(P.record(op/'plan.json'));v['ordinal']=2;write(op/'plan.json',v)
    elif change=='successful':
        v=P.read(q['stop']);v['code']='not_a_failure';q['stop']=write(Path(q['stop']['path']),v)
    elif change=='attempt':q['attempt_id']='not-a-uuid'
    else:
        job=P.read(old)['jobs'][0];write(Path(old['path']).parent/job['key']/'reviewer-request.json',{})
    altered=write(root/'altered-correction.json',q)
    with pytest.raises((ValueError,KeyError,AssertionError)):
        Q.validate(altered,root=P.ROOT)
