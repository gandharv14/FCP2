from partition_historical_fixture import historical_profile_modules
from pathlib import Path
from dataclasses import asdict
import copy
import pytest
from scripts.batch import harbor_partition_code_applicability as A,harbor_normalization_partitioned as P
from scripts.batch.harbor_admission import read,record
from scripts.batch import harbor_model_executor as E
from test_partition_code_applicability import historical,setup
from test_normalization_partitioned import respond

@pytest.fixture
def completed(historical,monkeypatch):
    s,campaign,prepared=historical;respond(monkeypatch,s)
    for index in range(2):assert P.dispatch(prepared,index,1,credential='synthetic').status=='completed'
    current=P.aggregate(prepared);previous=copy.deepcopy(current)
    old_codes=A.profile(P.codes())
    previous['checked_files']={k:v for k,v in previous['checked_files'].items() if k not in P.codes()}
    previous['checked_files'].update(old_codes)
    aggregate=P.N.keep(Path(read(prepared)['root'])/'historical-aggregate.json',previous)
    return s,campaign,prepared,aggregate,current


def validate(c):
    s,campaign,prepared,aggregate,current=c
    return P.validate(aggregate,prepared=prepared,source=s.bindings['source_file'],baseline=s.bindings['baseline_inputs'],draft=record(s.run/'draft.json'),rows=read(record(s.run/'analysis.json')),policy=s.policy,bindings=s.bindings)


def test_completed_aggregate_revalidated_same_calls_without_rewriting_old_proof(completed,monkeypatch):
    s,campaign,prepared,aggregate,current=completed;old=Path(aggregate['path']).read_bytes();calls={}
    for i in (0,1):
        root=E.location(P._prepared_call(prepared,i,1),campaign)
        calls.update({p:p.read_bytes() for p in root.iterdir() if p.is_file()})
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no new call'))
    result=validate(completed)
    assert result['review']==current['review'] and result['semantic_attempt']==1
    assert Path(aggregate['path']).read_bytes()==old and all(p.read_bytes()==v for p,v in calls.items())
    assert all(result['checked_files'][k]==v for k,v in A.code_records().items())
    proof=A.validate_aggregate(read(aggregate),P.aggregate(prepared),P.codes())
    assert proof['original_checked_files']==read(aggregate)['checked_files']
    assert proof['current_checked_files']==P.aggregate(prepared)['checked_files']


@pytest.mark.parametrize('field',['review','partitions','semantic_attempt','logical_limit','prepared','budget','noncode','unknowncode','missingfile'])
def test_old_complete_proof_cannot_smuggle_other_changes(completed,field):
    s,campaign,prepared,aggregate,current=completed;v=read(aggregate)
    if field=='review':v['review']['reviewer']='changed'
    elif field=='partitions':v['partitions'].pop()
    elif field=='semantic_attempt':v['semantic_attempt']=2
    elif field=='logical_limit':v['logical_limit']=3
    elif field=='prepared':v['prepared']['sha256']='0'*64
    elif field=='budget':v['semantic_budget_reset']=True
    elif field=='noncode':v['checked_files'][s.bindings['source_file']['path']]='0'*64
    elif field=='unknowncode':v['checked_files'][str(Path(E.__file__).resolve())]='0'*64
    else:v['checked_files'].pop(s.bindings['source_file']['path'])
    altered=P.N.keep(Path(aggregate['path']).with_name('altered.json'),v)
    with pytest.raises(ValueError):validate((s,campaign,prepared,altered,current))


def test_intermediate_prepared_profile_is_closed_and_preserves_budget(completed):
    s,campaign,prepared,aggregate,current=completed;prior=copy.deepcopy(current)
    middle=A.profiles(P.codes())['prepared-code-applicability-v1']
    prior['checked_files']={k:v for k,v in prior['checked_files'].items() if k not in P.codes()};prior['checked_files'].update(middle)
    assert A.validate_aggregate(prior,current,P.codes())['profile']=='prepared-code-applicability-v1'
    prior['checked_files'][str(Path(A.__file__).resolve())]='0'*64
    with pytest.raises(ValueError):A.validate_aggregate(prior,current,P.codes())


def test_fresh_current_profile_requires_no_exception(completed):
    assert A.validate_aggregate(completed[-1],completed[-1],P.codes()) is None
