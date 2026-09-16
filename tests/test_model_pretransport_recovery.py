from dataclasses import asdict
from pathlib import Path
import json,pytest
from scripts.batch import harbor_model_executor as E,harbor_model_pretransport_recovery as P
from scripts.fleet import model_gate,disclosure_review_continue as M
from test_fleet_model_gate import case
from test_harbor_model_executor import Response,response_bytes


def setup(case,tmp_path,monkeypatch):
    c,campaign,prepared=case
    client,owner,_=model_gate.context();worker=tmp_path/'worker';journal=worker/'model-operations'
    monkeypatch.setattr(model_gate,'context',lambda:(client,owner,journal))
    def saved(p,v):E.save(p,v);return E.record(p)
    output=tmp_path/'output';output.mkdir()
    def refused():
        saved(output/'refused.json',dict(automatic_retry=False,failure_type='ValueError',message_sha256='a'*64,transport_started=False))
        raise ValueError('post-admission custody refused')
    monkeypatch.setattr(E,'_open',lambda *args:pytest.fail('original request must never transport'))
    with M.fresh_admitted_transport(E,refused):out=E.execute(prepared,campaign,credential='fixture-only')
    assert out.status=='incomplete'
    root=E.location(prepared,campaign)
    proof=dict(schema_version='fleet-pretransport-stop-proof/v1',operation=prepared.attempt_id,executor=str(root),no_transport=True,pid_exists=False,matching_processes=[],pid=123,start_ticks='123',worker=str(worker),
        executor_inventory=P.inventory(root),executor_outcome=P.read(out.outcome),refused=E.record(output/'refused.json'),
        prepared=saved(output/'request.json',dict(prepared=asdict(prepared))),
        process=saved(output/'process.json',dict(pid=123,start_ticks='123')),
        worker_outcome=saved(worker/'outcome.json',dict(returncode=1,failure_type=None,result=None,status='RECONCILIATION_REQUIRED')),
        worker_intent=saved(worker/'intent.json',dict(fixture=True)),model_operations=[E.record(p)for p in sorted(journal.rglob('*.json'))])
    proof_binding=saved(output/'proof.json',proof)
    result=c.reconcile_operation('model',prepared.attempt_id,json.dumps(proof_binding),'abandoned')
    receipt=saved(output/'settlement.json',dict(proof=proof_binding,result=result,after=c.status()))
    auth=saved(output/'authorization.json',dict(explicit_one_pretransport_continuation=True))
    return c,campaign,prepared,proof_binding,receipt,auth,output


def test_real_separate_dispatch_preserves_original_and_is_not_replayed(case,tmp_path,monkeypatch):
    c,campaign,old,proof,receipt,auth,out=setup(case,tmp_path,monkeypatch)
    original=P.inventory(E.location(old,campaign))
    repaired=P.prepare(old,campaign,out/'certificate',authorization=auth,proof=proof,reconciliation=receipt)
    assert repaired.request==old.request and repaired.attempt_id==old.attempt_id and repaired.budget_evidence==old.budget_evidence
    # A new fenced owning claim follows authenticated reconciliation.
    c.reconcile(old.scope.workbook_id,'old worker stopped')
    claim=c.claim(old.scope.workbook_id,'vm-1/new-worker','new-claim','bundle')
    client,_,_=model_gate.context();owner={k:claim[k]for k in('workbook','owner','token','fence')}
    monkeypatch.setattr(model_gate,'context',lambda:(client,owner,tmp_path/'new-worker/model-operations'))
    sent=[]
    def opened(*args):sent.append(1);return Response(response_bytes(old.policy))
    monkeypatch.setattr(E,'_open',opened)
    result=E.execute(repaired,campaign,credential='fixture-only')
    assert result.status=='completed' and result.call is not None and sent==[1]
    assert E.execute(repaired,campaign,credential='fixture-only').status=='completed' and sent==[1]
    assert P.inventory(E.location(old,campaign))==original
    assert [(o['operation_id'],o['state'])for o in c.status()['operations']]==[(old.attempt_id,'abandoned'),(old.attempt_id+'-physical-repair-01','confirmed')]


@pytest.mark.parametrize('defect',['started','body','unsealed','budget','request','process','abandoned','proof','identity','refusal','unknown'])
def test_uncertain_or_foreign_evidence_cannot_get_certificate(case,tmp_path,monkeypatch,defect):
    c,campaign,old,proof,receipt,auth,out=setup(case,tmp_path,monkeypatch)
    root=E.location(old,campaign)
    if defect in ('started','body','unknown'):
        E.save(root/{'started':'transport-00.started.json','body':'response.raw.json','unknown':'unexpected.json'}[defect],{})
    elif defect=='unsealed':(root/'outcome.json').unlink()
    elif defect=='budget':Path(old.budget_evidence[0]['path']).write_text('{}')
    elif defect=='request':Path(old.request['path']).write_text('{}')
    elif defect in ('process','refusal'):
        v=P.read(proof)
        if defect=='process':v['pid_exists']=True
        else:
            r=P.read(v['refused']);r['transport_started']=True;E.save(out/'bad-refusal.json',r);v['refused']=E.record(out/'bad-refusal.json')
        E.save(out/'bad-proof.json',v);proof=E.record(out/'bad-proof.json')
    else:
        v=P.read(receipt)
        if defect=='abandoned':v['after']['operations'][0]['state']='hold'
        elif defect=='identity':v['after']['operations'][0]['identity']='f'*64
        else:v['proof']={'path':'/foreign','sha256':'f'*64}
        E.save(out/'bad-settlement.json',v);receipt=E.record(out/'bad-settlement.json')
    with pytest.raises((ValueError,OSError)):
        P.prepare(old,campaign,out/'certificate',authorization=auth,proof=proof,reconciliation=receipt)


def unacquired(case,tmp,monkeypatch):
    import base64,time
    from dataclasses import replace
    c,campaign,prepared=case
    budget=tmp/'budget-two.json';budget.write_text('{"ordinal":1,"limit":2}')
    prepared=replace(prepared,limit=2,budget_evidence=(E.record(budget),),scope=replace(prepared.scope,lane='harbor-instruction-naturalizer',budget_id='naturalization-writer'))
    client,owner,_=model_gate.context();worker=tmp/'unacquired-worker';journal=worker/'model-operations'
    class Refused:
        def call(self,action,**args):
            if action=='begin':raise RuntimeError('coordinator HTTP 500')
            return client.call(action,**args)
    monkeypatch.setattr(model_gate,'context',lambda:(Refused(),owner,journal))
    monkeypatch.setattr(E,'_open',lambda *args:pytest.fail('unacquired request cannot transport'))
    result=E.execute(prepared,campaign,credential='fixture-only');assert result.status=='incomplete'and c.status()['operations']==[]
    def saved(path,value):E.save(path,value);return E.record(path)
    out=tmp/'unacquired-evidence';out.mkdir();root=E.location(prepared,campaign)
    job=saved(out/'job.json',dict(workbook=prepared.scope.workbook_id,source=dict(path='/source',sha256=prepared.scope.original_sha256),version='bundle'))
    wi=saved(worker/'intent.json',dict(job=job,owner=owner['owner']))
    outer=saved(worker/'outcome.json',dict(returncode=1,failure_type=None,result=None,status='RECONCILIATION_REQUIRED',intent=wi))
    proc=saved(out/'process.json',dict(pid=123,start_ticks='456',job=job))
    c.finish(**owner,evidence=json.dumps(outer),disposition='hold')
    rows=[E.record(x)for x in sorted(journal.rglob('*.json'))]
    captured=[dict(r,base64=base64.b64encode(E.checked_bytes(r)).decode())for r in[rows[0],wi,outer,proc,result.outcome]]
    native=saved(out/'native.json',dict(at=time.time(),processes=[],files=captured))
    proof=saved(out/'proof.json',dict(schema_version='fleet-unacquired-pretransport-stop/v1',operation=prepared.attempt_id,executor=str(root),no_transport=True,pid_exists=False,matching_processes=[],pid=123,start_ticks='456',worker=str(worker),executor_inventory=P.inventory(root),executor_outcome=P.read(result.outcome),prepared=saved(out/'request.json',dict(prepared=asdict(prepared))),process=proc,worker_outcome=outer,worker_intent=wi,model_operations=rows,native_inventory=native))
    central=saved(out/'central.json',dict(schema_version='fleet-central-operation-absence/v1',status='CENTRAL_OPERATION_ABSENCE_VERIFIED',at=time.time(),central_operations=[],central_events=[],native_processes=[],automatic_replay=False,owning_recovery_performed=False,semantic_budget_reset=False,operation_id=prepared.attempt_id,request_sha256=prepared.request['sha256'],model_intent=rows[0],native_inventory=native,workbook=c.status()['workbooks'][0]))
    auth=saved(out/'authorization.json',dict(explicit_one_pretransport_continuation=True))
    return c,campaign,prepared,proof,central,auth,out,client


def test_unacquired_writer_keeps_two_attempt_budget_and_sends_once(case,tmp_path,monkeypatch):
    c,campaign,old,proof,central,auth,out,client=unacquired(case,tmp_path,monkeypatch)
    prior=P.inventory(E.location(old,campaign));new=P.prepare(old,campaign,out/'permit',authorization=auth,proof=proof,reconciliation=central)
    assert (new.ordinal,new.limit,new.attempt_id)==(1,2,old.attempt_id)
    c.reconcile(old.scope.workbook_id,'proven stopped');claim=c.claim(old.scope.workbook_id,'vm-1/new','second','bundle');owner={k:claim[k]for k in('workbook','owner','token','fence')}
    monkeypatch.setattr(model_gate,'context',lambda:(client,owner,tmp_path/'physical-worker/model-operations'))
    sent=[]
    def opened(*args):sent.append(1);return Response(response_bytes(old.policy))
    monkeypatch.setattr(E,'_open',opened);result=E.execute(new,campaign,credential='fixture-only')
    assert result.status=='completed'and result.call is not None and sent==[1]
    assert E.execute(new,campaign,credential='fixture-only').status=='completed'and sent==[1]
    assert P.inventory(E.location(old,campaign))==prior
    assert [(o['operation_id'],o['state'])for o in c.status()['operations']]==[(old.attempt_id+'-physical-repair-01','confirmed')]


@pytest.mark.parametrize('fault',['operation','event','owner','request','acquired','archive','native_process','source'])
def test_unacquired_requires_exact_absence_and_sealed_native_bytes(case,tmp_path,monkeypatch,fault):
    c,campaign,old,proof,central,auth,out,_=unacquired(case,tmp_path,monkeypatch)
    v=P.read(central)
    if fault=='operation':v['central_operations']=[{'state':'active'}]
    elif fault=='event':v['central_events']=[{'sequence':1}]
    elif fault=='owner':v['workbook']['owner']='foreign'
    elif fault=='request':v['request_sha256']='f'*64
    elif fault=='source':v['workbook']['source_sha256']='f'*64
    elif fault=='native_process':v['native_processes']=[123]
    elif fault=='acquired':E.save(Path(P.read(proof)['model_operations'][0]['path']).parent/'acquired.json',{})
    else:
        n=P.read(v['native_inventory']);n['files'][0]['base64']='e30=';E.save(out/'bad-native.json',n);v['native_inventory']=E.record(out/'bad-native.json')
    E.save(out/'bad-central.json',v)
    with pytest.raises(ValueError):P.prepare(old,campaign,out/'permit',authorization=auth,proof=proof,reconciliation=E.record(out/'bad-central.json'))
