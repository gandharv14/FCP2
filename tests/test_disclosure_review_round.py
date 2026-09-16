"""Real sealed transport, original guarded build and append-only round authority."""
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import copy,io,json,time,urllib.error
import pytest
from scripts.batch import harbor_disclosure_review_round as Q
from scripts.batch import harbor_disclosure_partition_review as R
from scripts.batch import harbor_continuation_allowance as A
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record,read
from test_fleet_model_gate import case as gate_case
from test_harbor_instruction_campaign import instruction,inprocess,execute,case,instruction_setup
from test_harbor_environment_worker import worker,selected,setup
from test_disclosure_partition_review import reviewed


def policy(tmp_path,source,workbook='0001'):
    directory=tmp_path/'allowance';directory.mkdir();(directory/'review-reservations').mkdir()
    auth=tmp_path/'authorization';auth.write_text('Explicit bounded fixture review allowance, no replay.')
    return A.issue(directory,expected_previous=None,selected_scope=A.scope(workbook,source),
        limits=dict(takeovers=6,lease_duration_ns=14400*10**9,stage_seconds=7200,memory_bytes=16*1024**3,review_attempts=4,transport_retries=0),
        authorization=record(auth),reason='Explicit second disclosure round',expires_at_ns=time.time_ns()+10**12,
        approve=lambda value:True)


def rejected(gate_case,monkeypatch):
    _,campaign,prepared=gate_case
    prepared=replace(prepared,scope=E.SemanticScope('0001','a'*64,'harbor-disclosure-writer','disclosure-review'))
    sent=[]
    def opened(*args):
        sent.append(1);raise urllib.error.HTTPError('https://fixture.invalid',400,'fixture',{},io.BytesIO(b'{"error":"request rejected"}'))
    monkeypatch.setattr(E,'_open',opened)
    result=E.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='http_rejected' and result.call is None and sent==[1]
    return campaign,prepared,record(E.location(prepared,campaign)/'outcome.json')


def test_real_terminal400_reserves_distinct_round_without_changing_consumed_bytes(gate_case,monkeypatch,tmp_path):
    campaign,prepared,outcome=rejected(gate_case,monkeypatch);scope=asdict(prepared.scope)
    before={p.name:p.read_bytes()for p in Path(outcome['path']).parent.iterdir()}
    allowance=policy(tmp_path,'a'*64);ledger=Path(allowance['path']).parent/'review-reservations'
    reservation=A.reserve_review(ledger,allowance=allowance,semantic_scope=scope,prior_records=[outcome],
        original_limit=1,expected_ordinal=2,cumulative_limit=2,prove_closed=Q.closed_rejection)
    assert Q.validate_reservation(reservation,scope,2,2)['prior_records']==[outcome]
    Q._primary_history(campaign,scope,2,[outcome])
    assert before=={p.name:p.read_bytes()for p in Path(outcome['path']).parent.iterdir()}
    with pytest.raises(ValueError):
        A.reserve_review(ledger,allowance=allowance,semantic_scope=scope,prior_records=[outcome],
            original_limit=1,expected_ordinal=2,cumulative_limit=2,prove_closed=Q.closed_rejection)


@pytest.mark.parametrize('fault',['scope','ordinal','request','incomplete','response','extra_attempt','foreign_outcome'])
def test_changed_or_unknown_primary_history_never_grants_another_round(gate_case,monkeypatch,fault):
    campaign,prepared,outcome=rejected(gate_case,monkeypatch);scope=asdict(prepared.scope);ordinal=1
    root=Path(outcome['path']).parent
    if fault=='scope':scope={**scope,'original_sha256':'b'*64}
    elif fault=='ordinal':ordinal=2
    elif fault=='request':(root/'request.json').write_text('{}')
    elif fault=='incomplete':(root/'transport-00.http-error.json').unlink()
    elif fault=='response':
        (root/'response.raw.json').write_text('{}');v=read(outcome);v['evidence']=E.all_files(root);(root/'outcome.json').write_bytes(E.canonical(v));outcome=record(root/'outcome.json')
    elif fault=='extra_attempt':
        (root.parent/'attempt-02').mkdir()
        with pytest.raises(ValueError):Q._primary_history(campaign,scope,2,[outcome])
        return
    elif fault=='foreign_outcome':
        with pytest.raises(ValueError):Q._primary_history(campaign,scope,2,[record(root/'plan.json')])
        return
    with pytest.raises(ValueError):Q.closed_rejection(outcome,scope,ordinal)


def test_explicit_unused_first_round_shares_ledger_and_cannot_reset(tmp_path):
    allowance=policy(tmp_path,'a'*64);scope=dict(workbook_id='0001',original_sha256='a'*64,lane='harbor-disclosure-writer',budget_id='disclosure-review')
    row=Q._first_reservation(allowance,scope,limit=1,now=time.time_ns())
    assert Q.validate_reservation(row,scope,1,1)['prior_records']==[]
    with pytest.raises(FileExistsError):Q._first_reservation(allowance,scope,limit=1,now=time.time_ns())
    with pytest.raises(ValueError):Q.validate_reservation(row,scope,1,2)


def test_real_instruction_build_guard_and_checkpoint_are_required(instruction,inprocess,monkeypatch):
    from scripts import harbor_instruction_campaign as C
    from scripts.fleet import advance_authoring_v8 as D
    monkeypatch.setattr(C.worker,'_operation',lambda w,s,l,*a,**kw:(s,l,None))
    outer,result=execute(instruction,0,commands=True)
    build=dict(outcome=record(instruction[-1]/'attempt-00/outcome.json'),result=outer['result'],descriptor=result['descriptor'],dispatcher=record(D.__file__),instruction=record(C.__file__))
    w,s,_=Q.completed_build(build,worker=instruction[0],selection=instruction[1])
    assert w.run==instruction[0].run
    foreign={**build,'result':instruction[5]}
    with pytest.raises(ValueError):Q.completed_build(foreign)
    resource=Path(read(build['outcome'])['resource']['path']);value=json.loads(resource.read_bytes());value['returncode']=1;resource.write_text(json.dumps(value))
    with pytest.raises(ValueError):Q.completed_build(build)


def test_second_round_scopes_and_aggregate_never_claim_first_review(reviewed,tmp_path,monkeypatch):
    plan,p,projection,args,review_policy,calls=reviewed
    authority=tmp_path/'round.json';authority.write_text(json.dumps(dict(round=2,limit=2)))
    current={**p,'review_round':record(authority),'producer_run':str(Path(p['worker_run'])/'retained-producer')}
    assert R.scope(p,0).budget_id=='disclosure-review-round-01-shard-0001'
    assert R.scope(current,0).budget_id=='disclosure-review-round-02-shard-0001'
    assert asdict(R.scope(current,0))!=asdict(R.scope(p,0))
    selected=tmp_path/'selected-plan.json';selected.write_bytes(E.canonical(current));plan=record(selected)
    # Full real Call validation still rejects the old first-round submitted scopes.
    monkeypatch.setattr(R,'validate_plan',lambda *a,**kw:(current,projection,[]))
    with pytest.raises(ValueError,match='scope'):R.aggregate(plan,calls,review_policy)


def test_reserve_uses_instruction_repair_prefix_not_renewal_env_slot(reviewed,tmp_path,monkeypatch):
    from scripts.batch import harbor_instruction_repair as repair
    _,original,projection,args,model_policy,_=reviewed
    inputs={**original,'workbook':'0001','producer_run':str(Path(original['worker_run'])/'producer')}
    stage=Path(args['instruction']['path']).parent
    selected=SimpleNamespace(content=SimpleNamespace(model_policy=model_policy,stage=stage))
    worker=SimpleNamespace(run=Path(inputs['worker_run']),root=Path(R.ROOT),context=object(),campaign=E.Campaign(tmp_path/'unused-campaign',tmp_path/'unused-slots',1))
    before=dict(workbook_id='0001',bindings={'original_raw_source':inputs['original_source']})
    tracker=record(tmp_path/'plan.json')
    W=SimpleNamespace(active=lambda *a:(None,before,tracker),task=lambda *a:SimpleNamespace(run=Path(inputs['producer_run'])))
    environment=(None,None,None,W,SimpleNamespace(_prefix='renewal helper has no prefix'),None,None,None)
    observed=[]
    def prefix(*a,**k):
        observed.append(k)
        return [dict(kind=kind,result=dict(passed=True,bindings={}))for kind in ['source','segmentation','research','normalization_phase1','source_profile','normalization_phase2','environment']]
    monkeypatch.setattr(repair,'prefix',prefix)
    monkeypatch.setattr(Q,'completed_build',lambda *a,**k:None)
    allowance=policy(tmp_path,inputs['original_source']['sha256'])
    class Boundary(Exception):pass
    def first(a,scope,**kwargs):
        assert a==allowance and scope==Q.semantic_scope(inputs) and kwargs['limit']==1
        assert observed==[{'stop':'disclosure'}]
        raise Boundary('before first durable review reservation')
    monkeypatch.setattr(Q,'_first_reservation',first)
    with pytest.raises(Boundary):
        Q.reserve(inputs,worker=worker,selection=selected,lease=object(),environment=environment,
            allowance=allowance,build={},prior_rejections=[],ordinal=1,limit=1)
    assert not (worker.run/'disclosure-review-rounds').exists()


def test_retained_dispatcher_alias_does_not_require_canonical_package_import(tmp_path,monkeypatch):
    import sys
    source=tmp_path/'retained_dispatcher.py';source.write_text('raise AssertionError("must reuse exact already loaded code")')
    selected=SimpleNamespace(__file__=str(source),successful='original validator')
    monkeypatch.setitem(sys.modules,'a_historical_dispatcher_alias',selected)
    assert Q.bound_module(record(source)) is selected
    source.write_text('changed')
    with pytest.raises(ValueError):Q.bound_module(dict(path=str(source),sha256='0'*64))


def test_prefix_typed_directory_keeps_all_files_and_rejects_new_members(tmp_path):
    from scripts.batch import harbor_readiness as static
    stage=tmp_path/'stage';stage.mkdir();(stage/'instruction.md').write_text('exact source')
    digest,_=static.publication._tree_hash(stage)
    prefix=[dict(kind='environment',result=dict(bindings={'staged_bundle':dict(path=str(stage),sha256=digest)},evidence=[],passed=True))]
    files,directories=Q.prefix_records(prefix)
    assert record(stage/'instruction.md')in files
    assert directories[0]['binding']==prefix[0]['result']['bindings']['staged_bundle']
    (stage/'additional.txt').write_text('new member')
    with pytest.raises(ValueError,match='directory changed'):Q.prefix_records(prefix)


def test_prefix_directory_symlink_is_rejected(tmp_path):
    target=tmp_path/'target';target.mkdir();(target/'data').write_text('source')
    alias=tmp_path/'alias';alias.symlink_to(target,target_is_directory=True)
    prefix=[dict(kind='environment',result=dict(bindings={'staged_bundle':dict(path=str(alias),sha256='a'*64)},evidence=[],passed=True))]
    with pytest.raises(ValueError,match='canonical typed'):Q.prefix_records(prefix)


def test_prefix_generation_retains_exact_manifest_and_membership(tmp_path):
    generation=tmp_path/('a'*64);generation.mkdir()
    manifest=generation/'generation-manifest.json';manifest.write_text('{"source":"retained"}')
    (generation/'source.xlsx').write_bytes(b'original workbook')
    binding=dict(path=str(generation),id=generation.name,manifest_sha256=record(manifest)['sha256'])
    prefix=[dict(kind='source',result=dict(bindings={'source_generation':binding},evidence=[],passed=True))]
    files,directories=Q.prefix_records(prefix)
    assert record(manifest)in files and record(generation/'source.xlsx')in files
    assert directories[0]['binding']==binding
    manifest.write_text('{"source":"changed"}')
    with pytest.raises(ValueError,match='manifest changed'):Q.prefix_records(prefix)
