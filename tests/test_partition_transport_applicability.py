from partition_historical_fixture import historical_profile_modules
import ast
import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_partition_code_applicability as A
from scripts.batch import harbor_partition_transport_applicability as T
from scripts.batch import harbor_normalization_partitioned as P, harbor_normalization_compact as K
from scripts.batch import harbor_normalization_producer as N, harbor_model_executor as E
from scripts.batch.harbor_admission import read, record
from test_harbor_normalization_producer import setup
from test_normalization_partitioned import respond
from test_completed_partition_code_applicability import completed, historical

PROFILES=['original-json-partition','prepared-code-applicability-v1','completed-dialogue-code-applicability-v1']


def test_archived_c5_to_a0_changes_only_prospective_execution_contract():
    old=ast.parse(T.archives()[0].read_bytes());new=ast.parse(Path(E.__file__).read_bytes())
    nodes=lambda tree:{node.name:ast.dump(node,include_attributes=False) for node in tree.body
                       if isinstance(node,(ast.FunctionDef,ast.ClassDef))}
    before,after=nodes(old),nodes(new)
    assert set(after)==set(before)|{'transient_open_failure'}
    assert all(before[name]==after[name] for name in before if name!='execute')
    assert before['execute']!=after['execute'] and E.MAX_RETRIES==3


def test_three_exact_vectors_and_complete_code_export():
    current=P.codes();profiles=A.profiles(current)
    pp,ep,ap=map(lambda m:str(Path(m.__file__).resolve()),(P,E,A))
    assert set(profiles)==set(PROFILES)
    assert [(profiles[n][pp],profiles[n][ep]) for n in PROFILES]==[
        (A.OLD_P,A.OLD_E),(A.PREVIOUS_P,T.OLD_E),(T.OLD_P,T.OLD_E)]
    assert profiles[PROFILES[1]][ap]==A.PREVIOUS_A and profiles[PROFILES[2]][ap]==T.OLD_A
    assert all(current[path]==sha for path,sha in T.code_records().items())


@pytest.mark.parametrize('profile',PROFILES)
def test_exact_old_profile_retains_json_request_and_call_identity(setup,monkeypatch,tmp_path,profile):
    s,campaign,_,_=setup();monkeypatch.setattr(K,'MAX_REFERENCES',2);N.prepare(s)
    old=A.profiles(P.codes())[profile]
    with monkeypatch.context() as scoped:
        scoped.setattr(P,'codes',lambda:old)
        prepared=P.prepare(s,campaign,tmp_path/'retained')
    original=Path(prepared['path']).read_bytes();pc=P._prepared_call(prepared,0,1)
    request=Path(pc.request['path']).read_bytes();seen=[];respond(monkeypatch,s,calls=seen)
    assert P.dispatch(prepared,0,1,credential='synthetic').status=='completed'
    root=E.location(pc,campaign);journal={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no replay'))
    assert P.dispatch(prepared,0,1,credential='synthetic').status=='completed'
    assert P._prepared_call(prepared,0,1)==pc and pc.response_mode=='json_object'
    assert Path(pc.request['path']).read_bytes()==request and Path(prepared['path']).read_bytes()==original
    assert all(p.read_bytes()==raw for p,raw in journal.items()) and len(seen)==1
    proof=A.validate(read(prepared),P.codes(),s)
    assert proof['schema_version']==A.SCHEMA and proof['changed_executor_branch_reachable'] is True
    assert proof['dialogue_mode_transition_reachable'] is False and proof['sealed_journal_replay'] is False


@pytest.mark.parametrize('status',['incomplete','response_rejected'])
def test_prior_current_nonpassing_sealed_journal_is_not_resubmitted(setup,monkeypatch,tmp_path,status):
    from test_harbor_model_executor import Response
    s,campaign,_,_=setup();monkeypatch.setattr(K,'MAX_REFERENCES',2);N.prepare(s)
    prior=A.profiles(P.codes())[PROFILES[-1]]
    with monkeypatch.context() as scoped:
        scoped.setattr(P,'codes',lambda:prior)
        prepared=P.prepare(s,campaign,tmp_path/'retained')
    pc=P._prepared_call(prepared,0,1)
    def stopped(point):
        if status=='incomplete' and point=='after_dispatch_started':raise TimeoutError('old synthetic timeout')
    monkeypatch.setattr(E,'_open',lambda *a:Response(b'not-a-completion'))
    assert E.execute(pc,campaign,credential='synthetic',_failpoint=stopped).status==status
    journal=E.location(pc,campaign);before={p:p.read_bytes() for p in journal.iterdir() if p.is_file()}
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no replay'))
    assert P.dispatch(prepared,0,1,credential='synthetic').status==status
    assert all(p.read_bytes()==raw for p,raw in before.items())
    if status=='incomplete':
        with pytest.raises(ValueError):P.dispatch(prepared,0,2,credential='synthetic')


@pytest.mark.parametrize('profile',PROFILES)
def test_aggregate_projection_changes_code_rows_only(completed,profile):
    current=completed[-1];old=copy.deepcopy(current);codes=P.codes()
    old['checked_files']={k:v for k,v in old['checked_files'].items() if k not in codes}
    old['checked_files'].update(A.profiles(codes)[profile]);before=copy.deepcopy(old)
    proof=A.validate_aggregate(old,current,codes)
    assert old==before and proof['profile']==profile
    assert proof['prospective_transport_retries'] is True and proof['sealed_journal_replay'] is False
    old['review']['reviewer']='changed'
    with pytest.raises(ValueError):A.validate_aggregate(old,current,codes)


@pytest.mark.parametrize('target',['executor_archive','applicability_archive','current_executor','current_producer'])
def test_altered_exact_source_or_archive_rejects(target,monkeypatch):
    current=P.codes();e,a=T.archives()
    chosen={'executor_archive':e,'applicability_archive':a,'current_executor':Path(E.__file__),'current_producer':Path(P.__file__)}[target]
    original=Path.read_bytes
    def changed(path):return original(path)+b'\n# changed\n' if path==chosen else original(path)
    monkeypatch.setattr(Path,'read_bytes',changed)
    original_record=T.record
    def changed_record(path):
        value=original_record(path)
        return {**value,'sha256':E.sha(changed(Path(path)))} if Path(path)==chosen else value
    monkeypatch.setattr(T,'record',changed_record)
    with pytest.raises(ValueError):A.profiles(current)


def test_missing_exported_transport_evidence_rejects():
    current=P.codes();current.pop(str(Path(T.__file__).resolve()))
    with pytest.raises(ValueError):A.profiles(current)
