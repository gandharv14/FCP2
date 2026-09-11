import dataclasses
import json
from pathlib import Path
import pytest
import xl_variable_source_audit as audit
from xl_audit_inventory_upgrade import restore_inventory_lookup
import xl_audit_validator_upgrade as upgrade
import xl_audit_renderer_recovery as R
import xl_audit_equivalence as eq
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_audit_bridge as B
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_admission import setup
from test_harbor_environment_worker import selected,worker,responses

@pytest.fixture
def retained(worker,monkeypatch,request):
    s,lease=worker;seen=responses(monkeypatch)
    second=getattr(request,'param',False)
    if second:
        opening=E._open
        def first_bad(*a,**k):
            response=opening(*a,**k)
            if len(seen)==1:
                payload=json.loads(response.read());payload['choices'][0]['message']['content']=payload['choices'][0]['message']['content'].replace('R0001','R9999')
                response=type(response)(json.dumps(payload).encode())
            return response
        monkeypatch.setattr(E,'_open',first_bad)
    original=Path(audit.__file__);raw=restore_inventory_lookup(original.read_bytes());before,after=upgrade.LABEL_RENDERING
    old=s.run.parent/'original-generator.py';old.write_bytes(raw.replace(after,before,1));assert record(old)['sha256']==R.ORIGINAL_SHA256
    pending=W.advance(s,lease);assert pending['status']=='PENDING_MODEL'
    plan_path=s.run/'audit-plan.json';plan_value=read(record(plan_path));plan_value['entry']=record(old);plan_path.write_bytes(E.canonical(plan_value))
    # Test-only historical scope, without executing any historical module.
    # The fixed current code generates and seals the mocked actual response;
    # final-render failure leaves its original unfinished metadata in place.
    with monkeypatch.context() as m:
        m.setattr(audit,'__file__',str(old))
        m.setattr(audit,'validate_audit_coverage',lambda *a,**k:['fixture final Markdown rendering failure'])
        with pytest.raises(RuntimeError,match='rendering failure'):
            B.run(record(plan_path),root=s.run/'audit-bridge',campaign=s.campaign,policy=s.bootstrap.model_policy,credential='synthetic-token')
    assert len(seen)==(2 if second else 1)
    plan=record(s.run/'audit-plan.json');target=s.bootstrap.run/'audit/0001-variable-sources'
    assert read(record(target/'0001-inputs-variable-sources.metadata.json'))['status']=='in_progress'
    selection=R.Selection(plan,record(old),record(target/'0001-inputs-variable-sources.metadata.json'),tuple(record(p) for p in sorted((s.run/'audit-bridge').glob('batch-*/prepared.json'))),s.run.parent/'recovered/0001-variable-sources',s.campaign,s.bootstrap.model_policy)
    return selection,s,lease,seen


def test_complete_transport_recovery_preserves_original_and_budget(retained):
    r,s,lease,seen=retained
    original={str(p):p.read_bytes() for p in Path(r.original_metadata['path']).parent.rglob('*') if p.is_file()}
    receipt=R.publish(r);value=read(receipt);meta=read(value['audit_bindings']['variable_source_metadata'])
    assert meta['model_calls_added']==0 and meta['api_calls']==1 and meta['generator_sha256']==R.ORIGINAL_SHA256
    assert meta['completion_kind']=='derived_from_retained_completed_transport'
    assert len(seen)==1 and original=={str(p):p.read_bytes() for p in Path(r.original_metadata['path']).parent.rglob('*') if p.is_file()}
    assert R.validate_receipt(receipt,bindings=value['audit_bindings'],original_code=r.original_code)
    bindings={**value['report']['original_bindings'],**value['audit_bindings']}
    proposal=eq.propose_audit_equivalence(bindings,bindings,validator_upgrade=receipt,original_auditor=r.original_code)
    assert proposal['differences']=={} and proposal['semantic_attempts_added']==0
    path=r.output/'applicability.json';E.save(path,proposal);equivalence=record(path)
    result=R.install_research(s,lease,receipt=receipt,equivalence=equivalence)
    assert result['status']=='STEP_COMPLETE',result
    assert result['lease'].revision==lease.revision+1 and len(seen)==1
    tracker=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert tracker['lane_state']['harbor-research-auditor']['state']=='passed'
    proof=B.validate_completed(s.run/'audit-bridge',Path(r.original_metadata['path']).parent,r.root_plan,r.policy,r.campaign)
    assert proof['bindings']['variable_source_metadata']==value['audit_bindings']['variable_source_metadata']
    assert record(s.run/'audit-bridge/renderer-recovery.json') in read(record(s.run/'research.json'))['evidence']


@pytest.mark.parametrize('change',['response','metadata','inventory','prepared','missing_attempt','old_code','current_code','unknown'])
def test_changed_or_incomplete_evidence_rejected(retained,monkeypatch,change):
    r,s,lease,seen=retained
    prepared=B.prepared_from(read(r.attempts[0]))
    if change=='missing_attempt':r=dataclasses.replace(r,attempts=())
    elif change=='old_code':
        path=Path(r.original_code['path']);path.write_bytes(path.read_bytes()+b'\n');r=dataclasses.replace(r,original_code=record(path))
    elif change=='current_code':
        path=s.run.parent/'unreviewed-generator.py';path.write_bytes(Path(audit.__file__).read_bytes()+b'\n');monkeypatch.setattr(audit,'__file__',str(path))
    elif change=='unknown':
        outcome=E.inspect(prepared,r.campaign,original_renderer_code=r.original_code).outcome
        data=read(outcome);data['status']='unresolved';Path(outcome['path']).write_bytes(E.canonical(data))
    else:
        path=Path({'response':prepared.audit_context.attempt['path'].replace('.request.','.response.'),'metadata':r.original_metadata['path'],'inventory':prepared.audit_context.inventory['path'],'prepared':r.attempts[0]['path']}[change]);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises((ValueError,KeyError,E.ExecutorError)):R.publish(r)
    assert not r.output.exists() and len(seen)==1


def test_execute_has_no_historical_code_override(retained):
    r,*_=retained;p=B.prepared_from(read(r.attempts[0]))
    with pytest.raises(TypeError):E.execute(p,r.campaign,credential='synthetic-token',original_renderer_code=r.original_code)
    with pytest.raises((ValueError,E.ExecutorError)):E.execute(p,r.campaign,credential='synthetic-token')


@pytest.mark.parametrize('change',['text','receipt','model'])
def test_owning_upgrade_revalidates_derived_evidence(retained,change):
    r,*_=retained;receipt=R.publish(r);value=read(receipt)
    model=r.policy.model
    if change=='text':Path(value['audit_bindings']['variable_source_audit']['path']).write_text('altered')
    if change=='receipt':
        value['semantic_attempts_added']=1;Path(receipt['path']).write_bytes(E.canonical(value));receipt=record(receipt['path'])
    if change=='model':model='different-model'
    with pytest.raises((ValueError,E.ExecutorError)):
        upgrade.validate_upgrade(receipt,bindings=value['audit_bindings'],original_code=r.original_code,model=model,endpoint=r.policy.endpoint.removesuffix('/chat/completions'),project_id=r.policy.project_id)


def applicable(retained):
    r,s,lease,seen=retained;receipt=R.publish(r);value=read(receipt)
    bindings={**value['report']['original_bindings'],**value['audit_bindings']}
    proposal=eq.propose_audit_equivalence(bindings,bindings,validator_upgrade=receipt,original_auditor=r.original_code)
    path=r.output/'applicability.json';E.save(path,proposal)
    context,_,_=eq._context(eq.selected(bindings),historical=False)
    return r,receipt,bindings,path,context


def test_packager_loads_explicit_current_pair_without_model(retained,monkeypatch):
    import xl_output_task as output
    r,receipt,b,path,context=applicable(retained)
    monkeypatch.setattr(audit,'_call_model',lambda *a,**k:pytest.fail('unexpected model call'))
    result=output.load_applicable_audit('0001',b['baseline_inputs']['path'],r.output.parent,path,context,model=r.policy.model,project_id=r.policy.project_id)
    assert result['cache_hit'] and result['model_calls_added']==0


@pytest.mark.parametrize('change',['receipt','original','inventory','generation','source','metadata_complete','missing_response'])
def test_packager_or_recovery_rejects_changed_inputs(retained,change):
    import xl_output_task as output
    r,s,lease,seen=retained
    if change in {'source','metadata_complete','missing_response'}:
        plan=read(r.root_plan);bindings=read(plan['baseline'])['bindings']
        if change=='source':
            source=Path(bindings['source_file']['path']);source.chmod(0o600);source.write_bytes(b'changed')
        if change=='metadata_complete':
            value=read(r.original_metadata);value['status']='complete';Path(r.original_metadata['path']).write_bytes(E.canonical(value));r=dataclasses.replace(r,original_metadata=record(r.original_metadata['path']))
        if change=='missing_response':
            p=B.prepared_from(read(r.attempts[0]));Path(p.audit_context.attempt['path'].replace('.request.','.response.')).unlink()
        with pytest.raises((ValueError,OSError)):R.publish(r)
        return
    r,receipt,b,path,context=applicable(retained)
    if change=='receipt':
        value=read(receipt);value['report']['consumption']['submitted_counts']['1']=0;Path(receipt['path']).write_bytes(E.canonical(value))
    if change=='original':Path(r.original_code['path']).write_bytes(b'changed')
    if change=='inventory':Path(b['variable_source_inventory']['path']).write_bytes(b'{}')
    if change=='generation':context['bindings']['segmentation_generation_id']='0'*64
    with pytest.raises((ValueError,OSError)):
        output.load_applicable_audit('0001',b['baseline_inputs']['path'],r.output.parent,path,context,model=r.policy.model,project_id=r.policy.project_id)


def test_same_pair_requires_renderer_receipt():
    inventory={'metadata':{'segmentation':{'generation_id':'a'*64,'manifest_sha256':'b'*64}},'variables':[]}
    with pytest.raises(ValueError,match='exactly a two-locator'):eq.inventory_equivalence(inventory,inventory)


@pytest.mark.parametrize('retained',[True],indirect=True)
def test_both_original_semantic_rounds_retained_without_replay(retained):
    r,s,lease,seen=retained;receipt=R.publish(r);value=read(receipt)
    assert len(seen)==2 and read(value['audit_bindings']['variable_source_metadata'])['api_calls']==2
    assert value['report']['consumption']['submitted_counts']=={'1':2}
    omitted=dataclasses.replace(r,attempts=r.attempts[1:],output=r.output.parent/'omitted')
    with pytest.raises(ValueError,match='attempt set'):R.publish(omitted)


def test_unaccounted_original_scope_is_not_hidden(retained):
    r,*_=retained;extra=Path(r.original_metadata['path']).parent/'.audit-attempts'/'another-scope'/'batch-0001'/'01.request.json'
    extra.parent.mkdir(parents=True);extra.write_text('{}')
    with pytest.raises(ValueError,match='another original audit scope'):R.publish(r)
