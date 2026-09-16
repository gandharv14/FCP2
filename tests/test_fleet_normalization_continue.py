"""Continuation orchestration rejects replay and retains original guards."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_continue as M


def test_bindings_and_intent_are_immutable(tmp_path):
    row=M.retain(tmp_path/'evidence.json', {'budget': {'ordinal':1,'limit':2,'attempt_id':'original'}})
    assert M.checked(row)['budget']['attempt_id']=='original'
    with pytest.raises(FileExistsError): M.retain(tmp_path/'evidence.json', {})
    Path(row['path']).write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'): M.checked(row)


def test_active_claim_is_required_before_network(monkeypatch):
    monkeypatch.delenv('HARBOR_FLEET_WORKBOOK',raising=False)
    with pytest.raises(ValueError,match='live fleet workbook'): M.active('0270')


def test_guard_failure_is_retained_and_never_replayed(tmp_path,monkeypatch):
    from scripts.batch import hardened_runner as H
    plan=M.retain(tmp_path/'plan.json', {'home':str(tmp_path/'continuation'),'code_root':str(tmp_path)})
    calls=[]
    monkeypatch.setattr(M.shutil,'disk_usage',lambda _:SimpleNamespace(free=12*1024**3))
    def stop(command,**kw):
        calls.append(kw)
        kw['stdout'].write(b'retained uncertain call evidence')
        raise RuntimeError('uncertain response')
    monkeypatch.setattr(H,'_run_in_process_group',stop)
    with pytest.raises(ValueError,match='never replay'): M.guarded(plan,'dispatch',{},'originallease','round-1',None)
    resource=json.loads((tmp_path/'continuation/stages/round-1/resource.json').read_text())
    assert resource['failure_type']=='RuntimeError' and resource['automatic_retry'] is False
    assert calls[0]['timeout_seconds']==3300 and calls[0]['memory_limit_bytes']==8*1024**3
    with pytest.raises(ValueError,match='already attempted'): M.guarded(plan,'dispatch',{},'originallease','round-1',None)
    assert len(calls)==1


def test_child_rejects_changed_driver_before_claim(tmp_path,monkeypatch):
    plan=M.retain(tmp_path/'plan.json',{'workbook':'0270'})
    intent=M.retain(tmp_path/'intent.json',{'plan':plan,'driver':{'path':'fake','sha256':'0'*64},'seconds':3300,'memory':8*1024**3})
    monkeypatch.setattr(M,'load',lambda p:(None,None,None,None,None))
    monkeypatch.setattr(M,'active',lambda w:pytest.fail('claim contacted for changed helper'))
    with pytest.raises(ValueError,match='helper changed'): M.child(intent)


def test_run_probe_rejection_has_no_mutation(tmp_path,monkeypatch):
    plan=M.retain(tmp_path/'plan.json',{'workbook':'0270','home':str(tmp_path/'continuation')})
    def reject(_): raise ValueError('original normalization already submitted or uncertain')
    monkeypatch.setattr(M,'probe',reject)
    monkeypatch.setattr(M,'active',lambda _:pytest.fail('invalid preparation contacted claim'))
    with pytest.raises(ValueError,match='already submitted'): M.run(plan)
    assert not (tmp_path/'continuation').exists()


def test_disk_reserve_blocks_stage_without_intent_or_call(tmp_path,monkeypatch):
    from scripts.batch import hardened_runner as H
    plan=M.retain(tmp_path/'plan.json', {'home':str(tmp_path/'continuation'),'code_root':str(tmp_path)})
    monkeypatch.setattr(M.shutil,'disk_usage',lambda _:SimpleNamespace(free=M.MEMORY-1))
    monkeypatch.setattr(H,'_run_in_process_group',lambda *a,**k:pytest.fail('dispatched below disk reserve'))
    with pytest.raises(ValueError,match='free-space reserve'): M.guarded(plan,'dispatch',{},'originallease','round-1',None)
    assert not (tmp_path/'continuation').exists()


def test_campaign_configuration_passes_actual_executor_slot_guards(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import uuid
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_model_executor as E
    campaign=E.Campaign(tmp_path/'journal',tmp_path/'slots',1)
    policy=E.Policy('fixture-project')
    request=M.retain(tmp_path/'request.json',dict(model=policy.model,reasoning_effort=policy.reasoning_effort,
        messages=[dict(role='system',content='Fixture review.'),dict(role='user',content='Fixture only.')]))
    evidence=M.retain(tmp_path/'budget.json',dict(ordinal=1,limit=2))
    prepared=E.PreparedCall(request,policy,E.SemanticScope('0270','a'*64,'normalization','fixture'),1,2,str(uuid.uuid4()),(evidence,))
    monkeypatch.delenv('HARBOR_MODEL_SLOTS',raising=False);monkeypatch.delenv('HARBOR_MODEL_SLOT_ROOT',raising=False)
    monkeypatch.delenv('HARBOR_FLEET_URL',raising=False)
    with pytest.raises(E.ExecutorError,match='model capacity must be configured'):
        E.execute(prepared,campaign,credential='fixture-only')
    assert not campaign.root.exists()
    M.configure_campaign(C,campaign)
    class ReachedGuard(Exception): pass
    @contextmanager
    def stopped_scope(*args,**kwargs):
        raise ReachedGuard('both real executor environment checks passed')
        yield
    monkeypatch.setattr(E,'scope_lock',stopped_scope)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no fixture transport authorized'))
    with pytest.raises(ReachedGuard): E.execute(prepared,campaign,credential='fixture-only')
    assert not campaign.root.exists()


def test_campaign_configuration_cannot_expand_global_limit(monkeypatch):
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_model_executor as E
    with pytest.raises(ValueError,match='remain one'):
        M.configure_campaign(C,E.Campaign(Path('/tmp/journal'),Path('/tmp/slots'),2))


def test_prepared_continuation_never_reprepares(tmp_path,monkeypatch):
    previous=M.retain(tmp_path/'previous.json',{'outcome':'retained'})
    prepared=M.retain(tmp_path/'prepared.json',{'partitions':[{'index':0}]})
    unresolved=M.retain(tmp_path/'unresolved.json',{'lease':'old-lease'})
    job=M.retain(tmp_path/'job.json',{})
    plan=M.retain(tmp_path/'plan.json',dict(home=str(tmp_path/'new-continuation'),workbook='0270',
        original_job=job,unresolved=unresolved,prepared_continuation={'previous_result':previous}))
    monkeypatch.setattr(M,'probe',lambda _: {'supported':True})
    env=(None,None,None,None,None,None,None,None)
    monkeypatch.setattr(M,'load',lambda _:(None,env,None,None,None))
    monkeypatch.setattr(M,'active',lambda _:None)
    monkeypatch.setattr(M,'retained_preparation',lambda *a:(prepared,'retained-lease',None,None))
    seen=[]
    def stop(_plan,action,args,lease,name,_env):
        seen.append(action)
        assert action=='dispatch' and args['prepared']==prepared and lease=='retained-lease'
        raise RuntimeError('stop before model for fixture')
    monkeypatch.setattr(M,'guarded',stop)
    with pytest.raises(RuntimeError):M.run(plan)
    assert seen==['dispatch']
    assert json.loads((tmp_path/'new-continuation/prepared-custody.json').read_text())['preparation_replayed'] is False
