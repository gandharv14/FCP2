"""A resumed round requires actual sealed executor calls, never copied pass flags."""
from dataclasses import asdict
from pathlib import Path
import json,uuid
import pytest
from scripts.batch import harbor_disclosure_round_history as H
from scripts.batch import harbor_disclosure_partition_review as R
from scripts.batch.harbor_admission import record,read
from test_disclosure_partition_review import reviewed
from test_harbor_semantic_readiness import bind_json,ROOT
from test_harbor_model_executor import Response,response_bytes

@pytest.fixture
def sealed(reviewed,tmp_path,monkeypatch):
    plan,p,projection,args,policy,oldcalls=reviewed
    p={**p,'workbook':'0001'};plan=bind_json(Path(plan['path']),p)
    marker=Path(p['worker_run'])/'disclosure-partition-continuation.json';old=read(read(record(marker))['permit'])
    permit=bind_json(Path(p['worker_run'])/'permit.json',{**old,'workbook':'0001','review_plan':plan})
    bind_json(marker,dict(schema_version=R.CONTINUATION,permit=permit))
    campaign=R.E.Campaign(tmp_path/'execution',tmp_path/'slots',1)
    monkeypatch.delenv('HARBOR_FLEET_URL',raising=False);monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    request=bind_json(tmp_path/'request.json',R.payload(p,projection,0,policy))
    prepared=R.E.PreparedCall(request,policy,R.scope(p,0),1,1,str(uuid.uuid4()),(plan,))
    monkeypatch.setattr(R.E,'_open',lambda *a:Response(response_bytes(policy,read(oldcalls[0].parsed))))
    observed=R.E.execute(prepared,campaign,credential='fixture-only')
    assert observed.status=='completed'and observed.call is not None
    R.validate_batch(observed.call,plan,0,policy,ROOT)
    directory=tmp_path/'round';home=directory/'batches/0000';home.mkdir(parents=True)
    context=bind_json(tmp_path/'context.json',dict(schema_version=R.REQUEST,plan=plan,index=0,batch_id=projection['batches'][0]['batch_id'],request=request,budget_evidence=[plan],scope=asdict(prepared.scope),ordinal=1,limit=1))
    bind_json(home/'request.json',dict(prepared=asdict(prepared),context=context))
    bind_json(home/'admission.json',dict(tracker=plan,observed_at_ns=1,required_remaining_seconds=660,physical_policy=None,transport_started=False))
    bind_json(home/'transport-complete.json',dict(index=0,plan=plan,outcome=observed.outcome,call=asdict(observed.call),semantic_approval=False,automatic_retry=False))
    bind_json(home/'complete.json',dict(index=0,plan=plan,call=asdict(observed.call),cached_input_recheck_seconds=.1))
    return plan,directory,policy,campaign,observed


def test_actual_sealed_call_prefix_is_read_without_second_transport(sealed,monkeypatch):
    plan,directory,policy,campaign,observed=sealed
    monkeypatch.setattr(R.E,'_open',lambda *a:(_ for _ in()).throw(AssertionError('no transport during history')))
    calls,total,evidence=H.sealed_batches(plan,directory,policy=policy,root=ROOT,campaign=campaign)
    assert calls==[observed.call]and total>1 and len(evidence)==5

@pytest.mark.parametrize('fault',['unsealed','extra','context','outcome','call','gap'])
def test_incomplete_or_foreign_prefix_never_authorizes_resume(sealed,fault):
    plan,directory,policy,campaign,observed=sealed;home=directory/'batches/0000'
    if fault=='unsealed':(home/'complete.json').unlink()
    elif fault=='extra':(home/'admission-refused.json').write_text('{}')
    elif fault=='context':
        value=read(record(home/'request.json'));bind_json(Path(value['context']['path']),{})
    elif fault=='outcome':Path(observed.outcome['path']).write_text('{}')
    elif fault=='call':
        value=read(record(home/'complete.json'));value['call']['request']['sha256']='a'*64;bind_json(home/'complete.json',value)
    else:home.rename(home.parent/'0001')
    with pytest.raises(ValueError):H.sealed_batches(plan,directory,policy=policy,root=ROOT,campaign=campaign)


@pytest.mark.parametrize('fault',[None,'source','fence','retained_call','extra_effect','count','uncertain'])
def test_completed_split_round_reuses_actual_call_with_exact_history(sealed,tmp_path,monkeypatch,fault):
    plan,before,policy,campaign,observed=sealed
    code=record(__file__);after=tmp_path/'continued';after.mkdir()
    old=dict(schema_version='fleet-disclosure-review-continuation/v1',helper=code,owner='old',fence=3,
        workbook='0001',source=plan,output=str(before),reserved_round=plan,round_reader=None,max_calls=2)
    old_plan=bind_json(tmp_path/'old-plan.json',old)
    bind_json(before/'intent.json',dict(plan=old_plan,automatic_retry=False))
    bind_json(before/'continued.json',plan);bind_json(before/'reserved.json',dict(plan=plan,automatic_retry=False))
    bind_json(before/'validation-metrics.json',{})
    run=tmp_path/'old-worker';run.mkdir();job=bind_json(tmp_path/'worker-job.json',dict(driver=code,source=plan,workbook='0001',
        result=str(before/'result.json'),run=str(run),inputs=[old_plan]))
    wi=bind_json(run/'intent.json',dict(owner='old',job=job))
    outer=bind_json(run/'outcome.json',dict(intent=wi,status='RECONCILIATION_REQUIRED',returncode=1,
        failure_type='TimeoutError'if fault=='uncertain'else None,result=None,automatic_retry=False,publication_performed=False))
    process=bind_json(tmp_path/'process.json',dict(job=job,automatic_retry=False))
    proof=bind_json(tmp_path/'proof.json',dict(schema_version='fleet-disclosure-completed-batch-prefix/v1',plan=old_plan,
        worker_outcome=outer,process=process,tracker=plan,completed_count=2 if fault=='count'else 1))
    selected={k:v for k,v in old.items()if k not in ('reserved_round','round_reader')}
    selected.update(owner='new',fence=5 if fault=='fence'else 4,output=str(after),completed_prefix=proof,progress_reader=code)
    if fault=='source':selected['source']=code
    if fault=='extra_effect':bind_json(before/'unexpected.json',{})
    bind_json(after/'continued.json',proof);bind_json(after/'reserved.json',dict(plan=plan,automatic_retry=False))
    calls=[asdict(observed.call)]
    if fault=='retained_call':calls[0]['parsed']['sha256']='a'*64
    bind_json(after/'retained-calls.json',dict(proof=proof,plan=plan,calls=calls,calls_replayed=0))
    checked=R.validate_plan(plan,policy=policy,root=ROOT,current_inputs=True)
    monkeypatch.setattr(R.E,'_open',lambda *a:(_ for _ in()).throw(AssertionError('completed Call cannot be replayed')))
    if fault:
        with pytest.raises(ValueError):H.retained_prefix(selected,plan,policy=policy,root=ROOT,campaign=campaign,validated=checked)
    else:
        actual,total,evidence=H.retained_prefix(selected,plan,policy=policy,root=ROOT,campaign=campaign,validated=checked)
        assert actual==[observed.call]and total>1 and proof in evidence
        assert H.batch_completion(selected,plan,0)['call']==asdict(observed.call)
        assert not (after/'batches/0000').exists()
