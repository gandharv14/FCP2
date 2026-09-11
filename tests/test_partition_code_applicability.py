from partition_historical_fixture import historical_profile_modules
import urllib.error
from pathlib import Path
from dataclasses import asdict,replace
import json,uuid
import pytest
from scripts.batch import harbor_partition_code_applicability as A
from scripts.batch import harbor_normalization_partitioned as P,harbor_normalization_compact as K,harbor_normalization_producer as N,harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup
from test_normalization_partitioned import respond

@pytest.fixture
def historical(setup,monkeypatch,tmp_path):
    s,campaign,_,_=setup();monkeypatch.setattr(K,'MAX_REFERENCES',2)
    N.prepare(s)
    current=P.codes();prior=A.profile(current)
    # Build historical-format fixture bytes with the same current deterministic
    # preparation, selecting the old declared code map only before creation.
    # Neither archived module is imported or executed.
    with monkeypatch.context() as m:
        m.setattr(P,'codes',lambda:prior)
        prepared=P.prepare(s,campaign,tmp_path/'partitioned')
    return s,campaign,prepared


def test_old_manifest_current_code_full_revalidation_retains_existing_call(historical,monkeypatch):
    s,campaign,prepared=historical;calls=[];respond(monkeypatch,s,calls=calls)
    before=Path(prepared['path']).read_bytes();pc=P._prepared_call(prepared,0,1)
    first=P.dispatch(prepared,0,1,credential='synthetic');assert first.status=='completed'
    journal=E.location(pc,campaign);retained={p:p.read_bytes() for p in journal.iterdir() if p.is_file()}
    P._current(prepared)
    assert P.dispatch(prepared,0,1,credential='synthetic').status=='completed' and len(calls)==1
    assert all(p.read_bytes()==v for p,v in retained.items())
    assert P._prepared_call(prepared,0,1)==pc
    assert P.dispatch(prepared,1,1,credential='synthetic').status=='completed'
    aggregate=P.aggregate(prepared);assert len(aggregate['partitions'])==2 and aggregate['semantic_attempt']==1
    assert Path(prepared['path']).read_bytes()==before
    for path,sha in A.code_records().items():assert aggregate['checked_files'][path]==sha
    assert aggregate['semantic_budget_reset'] is False


@pytest.mark.parametrize('target',['source','budget','request','context','owner'])
def test_noncode_changes_still_reject_before_dispatch(historical,monkeypatch,target):
    s,campaign,prepared=historical;m=read(prepared)
    binding={'source':s.bindings['source_file'],'budget':s.budget.evidence[0],'request':m['partitions'][0]['request'],'context':m['legacy']['review-evidence.json'],'owner':record(P.owner(s,campaign)/'owner.json')}[target]
    Path(binding['path']).write_bytes(Path(binding['path']).read_bytes()+b' ')
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no actual call permitted'))
    with pytest.raises(ValueError):P.dispatch(prepared,0,1,credential='synthetic')


@pytest.mark.parametrize('kind',['foreign_code','missing_code','current_profile','old_e_digest','p_extra_delta'])
def test_exact_only_code_profile(historical,monkeypatch,kind):
    s,campaign,prepared=historical;m=read(prepared);current=P.codes()
    if kind=='foreign_code':m['code']['unrelated.py']='a'*64
    elif kind=='missing_code':m['code'].pop(str(Path(N.__file__).resolve()))
    elif kind=='current_profile':current[str(Path(N.__file__).resolve())]='a'*64
    elif kind=='old_e_digest':m['code'][str(Path(E.__file__).resolve())]='a'*64
    else:
        old=Path.read_bytes
        def changed(p):return old(p)+b'\n# unreviewed\n' if p==Path(P.__file__) else old(p)
        monkeypatch.setattr(Path,'read_bytes',changed)
    with pytest.raises(ValueError):A.validate(m,current,s)


@pytest.mark.parametrize('kind',['executor','partition'])
def test_altered_old_archive_rejected(historical,monkeypatch,kind):
    s,campaign,prepared=historical
    selected=A.archives()[0 if kind=='executor' else 1];original=A.record
    def altered(p):
        value=original(p)
        return {**value,'sha256':'a'*64} if Path(p)==selected else value
    monkeypatch.setattr(A,'record',altered)
    with pytest.raises(ValueError):P._current(prepared)


def test_future_executor_delta_is_not_admitted(historical,monkeypatch):
    s,campaign,prepared=historical;old=A.record
    def altered(p):
        value=old(p)
        return {**value,'sha256':'a'*64} if Path(p)==Path(E.__file__) else value
    monkeypatch.setattr(A,'record',altered)
    with pytest.raises(ValueError):P._current(prepared)


def test_normalization_scope_default_is_json_and_not_dialogue(historical):
    s,campaign,prepared=historical;m=read(prepared)
    proof=A.validate(m,P.codes(),s)
    assert proof['changed_executor_branch_reachable'] is True and proof['historical_code_executed'] is False
    assert proof['dialogue_mode_transition_reachable'] is False and proof['sealed_journal_replay'] is False
    for index in range(len(m['partitions'])):
        for ordinal in (1,2):
            pc=P._prepared_call(prepared,index,ordinal)
            assert pc.response_mode=='json_object' and pc.scope.lane=='harbor-normalization-engineer'


def test_failed_original_round_remains_consumed(historical,monkeypatch):
    s,campaign,prepared=historical;firstrefs=read(prepared)['partitions'][0]['references']
    respond(monkeypatch,s,invalid=lambda refs:refs==firstrefs)
    for index in (0,1):P.dispatch(prepared,index,1,credential='synthetic')
    respond(monkeypatch,s);P.dispatch(prepared,0,2,credential='synthetic')
    proof=P.aggregate(prepared)
    assert proof['semantic_attempt']==2 and [p['ordinal'] for p in proof['partitions']]==[1,2,1]
    with pytest.raises(ValueError):P.dispatch(prepared,0,3,credential='synthetic')
    with pytest.raises(ValueError):P.prepare(replace(s,budget=replace(s.budget,attempt_id=str(uuid.uuid4()))),campaign,s.run.parent/'reset')


def test_uncertain_call_stays_blocked_under_compatible_code(historical,monkeypatch):
    s,campaign,prepared=historical
    monkeypatch.setattr(E,'_open',lambda *a,**k:(_ for _ in ()).throw(urllib.error.URLError('opaque synthetic delivery state')))
    assert P.dispatch(prepared,0,1,credential='synthetic').status=='incomplete'
    with pytest.raises(ValueError):P.dispatch(prepared,0,2,credential='synthetic')
    assert P.dispatch(prepared,0,1,credential='synthetic').status=='incomplete'
