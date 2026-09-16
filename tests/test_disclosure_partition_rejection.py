from dataclasses import asdict
from pathlib import Path
import copy,uuid
import pytest
from scripts.batch import harbor_disclosure_partition_rejection as J
from scripts.batch import harbor_disclosure_partition_review as R
from scripts.batch.harbor_admission import record,read
from test_disclosure_partition_review import reviewed
from test_harbor_semantic_readiness import bind_json,ROOT
from test_harbor_model_executor import Response,response_bytes

@pytest.fixture
def rejected_prefix(reviewed,tmp_path,monkeypatch):
    plan,p,projection,args,policy,calls=reviewed
    p={**p,'workbook':'0001'};plan=bind_json(Path(plan['path']),p)
    marker=Path(p['worker_run'])/'disclosure-partition-continuation.json'
    old=read(read(record(marker))['permit'])
    permit=bind_json(Path(p['worker_run'])/'permit.json',{**old,'workbook':'0001','review_plan':plan})
    bind_json(marker,dict(schema_version=R.CONTINUATION,permit=permit))
    review=copy.deepcopy(read(calls[0].parsed));review['passed']=False
    review['blocking_findings']=['Source formula differs']
    review['sentences'][0].update(verdict='blocking',findings=['Source formula differs'])
    campaign=R.E.Campaign(tmp_path/'execution',tmp_path/'slots',1)
    monkeypatch.delenv('HARBOR_FLEET_URL',raising=False)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    request=bind_json(tmp_path/'request.json',R.payload(p,projection,0,policy))
    prepared=R.E.PreparedCall(request,policy,R.scope(p,0),1,1,str(uuid.uuid4()),(plan,))
    monkeypatch.setattr(R.E,'_open',lambda *_:Response(response_bytes(policy,review)))
    observed=R.E.execute(prepared,campaign,credential='fixture-only')
    assert observed.status=='completed'
    directory=tmp_path/'round';home=directory/'batches/0000';home.mkdir(parents=True)
    context=bind_json(tmp_path/'context.json',dict(schema_version=R.REQUEST,plan=plan,index=0,
        batch_id=projection['batches'][0]['batch_id'],request=request,budget_evidence=[plan],scope=asdict(prepared.scope),ordinal=1,limit=1))
    bind_json(home/'request.json',dict(prepared=asdict(prepared),context=context))
    bind_json(home/'admission.json',dict(tracker=plan,observed_at_ns=1,required_remaining_seconds=660,physical_policy=None,transport_started=False))
    bind_json(home/'transport-complete.json',dict(index=0,plan=plan,outcome=observed.outcome,call=asdict(observed.call),semantic_approval=False,automatic_retry=False))
    monkeypatch.setattr(R.E,'_open',lambda *_:pytest.fail('history must never send a model request'))
    return plan,p,directory,policy,campaign,observed


def check(case):
    plan,p,directory,policy,campaign,observed=case
    return J.sealed_rejected_batches(plan,directory,policy=policy,root=ROOT,campaign=campaign)


def test_actual_completed_rejection_retained_without_synthetic_pass(rejected_prefix):
    plan,p,directory,policy,campaign,observed=rejected_prefix
    before={str(x):x.read_bytes()for x in directory.rglob('*')if x.is_file()}
    calls,total,evidence,finding=check(rejected_prefix)
    assert calls==[observed.call] and total>1 and finding['blocked_indices']==[0]
    assert finding['semantic_approval']is False and finding['review']['passed']is False
    assert before=={str(x):x.read_bytes()for x in directory.rglob('*')if x.is_file()}


def test_retained_transport_mode_survives_different_probe_environment(rejected_prefix,monkeypatch):
    import os
    monkeypatch.setenv('HARBOR_FLEET_URL','http://current-probe.invalid')
    calls,total,evidence,finding=check(rejected_prefix)
    assert calls==[rejected_prefix[-1].call] and finding['semantic_approval']is False
    assert os.environ['HARBOR_FLEET_URL']=='http://current-probe.invalid'


def test_retained_transport_plan_cannot_be_rewritten(rejected_prefix):
    Path(rejected_prefix[-1].outcome['path']).with_name('plan.json').write_text('{}')
    with pytest.raises(ValueError,match='retained executor plan changed'):
        check(rejected_prefix)


@pytest.mark.parametrize('fault',['incomplete','synthetic_complete','context','outcome','scope','gap','later_executor','later_prepared','extra_attempt'])
def test_uncertain_or_foreign_prefix_cannot_authorize_correction(rejected_prefix,fault):
    plan,p,directory,policy,campaign,observed=rejected_prefix;home=directory/'batches/0000'
    if fault=='incomplete':(home/'transport-complete.json').unlink()
    elif fault=='synthetic_complete':bind_json(home/'complete.json',{})
    elif fault=='context':bind_json(Path(read(record(home/'request.json'))['context']['path']),{})
    elif fault=='outcome':Path(observed.outcome['path']).write_text('{}')
    elif fault=='scope':
        value=read(record(home/'request.json'));value['prepared']['scope']['budget_id']='foreign';bind_json(home/'request.json',value)
    elif fault=='gap':home.rename(home.parent/'0001')
    elif fault=='later_executor':(Path(campaign.root)/R.E.sha(R.E.canonical(asdict(R.scope(p,1))))).mkdir()
    elif fault=='later_prepared':(Path(p['worker_run'])/'producer/requests'/R.scope(p,1).budget_id).mkdir(parents=True)
    else:(Path(observed.outcome['path']).parent.parent/'attempt-02').mkdir()
    with pytest.raises(ValueError):check(rejected_prefix)


def test_typed_rejection_requires_exact_closed_reader_identity(tmp_path):
    value=dict(schema_version=J.SCHEMA,implementation=record(J.__file__),run_plan={},
               worker_outcome={},review_plan={},round=2,limit=2,final_batch=0)
    for bad in [dict(value,extra=True),dict(value,round=True),dict(value,final_batch=-1),
                dict(value,implementation={'path':J.__file__,'sha256':'0'*64})]:
        selected=bind_json(tmp_path/'selected.json',bad)
        with pytest.raises(ValueError,match='closed completed partition'):
            J.closed_rejection(selected,{},2)


def test_round_hook_preserves_typed_rejection_and_rejects_hidden_primary_attempt(tmp_path,monkeypatch):
    from scripts.batch import harbor_disclosure_review_round as Q
    from types import SimpleNamespace
    scope=dict(workbook_id='0001',original_sha256='a'*64,lane='harbor-disclosure-writer',budget_id='disclosure-review')
    campaign=R.E.Campaign(tmp_path/'execution',tmp_path/'slots',1)
    root=Path(campaign.root)/R.E.sha(R.E.canonical(scope));(root/'attempt-01').mkdir(parents=True)
    (root/'scope.lock').write_text('')
    first=bind_json(root/'attempt-01/outcome.json',{'status':'fixture-terminal'})
    second=bind_json(tmp_path/'rejection.json',dict(schema_version=J.SCHEMA,implementation=record(J.__file__)))
    seen=[]
    monkeypatch.setattr(Q,'bound_module',lambda _:SimpleNamespace(SCHEMA=J.SCHEMA,
        closed_rejection=lambda row,s,n:seen.append((row,s,n)) or True))
    Q._primary_history(campaign,scope,3,[first,second])
    assert seen==[(second,scope,2)]
    (root/'attempt-02').mkdir()
    with pytest.raises(ValueError,match='unlisted or uncertain'):
        Q._primary_history(campaign,scope,3,[first,second])
