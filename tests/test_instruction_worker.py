from dataclasses import replace,asdict
from pathlib import Path
from types import SimpleNamespace
import json
import fcntl
import time
import pytest
from scripts.batch import harbor_instruction_worker as w
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection,SemanticSelection
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_lane_notes import RootContext
from test_harbor_instruction_producer import setup as instruction_setup

ROOT=Path.cwd()

@pytest.fixture
def case(instruction_setup,monkeypatch):
    t,campaign,respond,calls=instruction_setup
    value=json.loads(Path(t.tracker['path']).read_text())
    b=value['bindings'];b['source_file']=value['raw_source'];b['source_generation']=value['raw_source'];b['segmentation_generation']=value['raw_source'];b['curation']=value['raw_source']
    value['lane_state'][producer.DISCLOSURE]['state']='pending'
    Path(t.tracker['path']).write_text(json.dumps(value));t=replace(t,tracker=record(t.tracker['path']))
    norm=t.run/'0245-variable-sources';norm.mkdir(parents=True);(norm/'normalized.json').write_text('{}')
    s=Selection('0245',t.stage,w.static.publication._tree_hash(t.stage)[0],b,norm,t.run/'nat',None,None,model_policy=t.policy)
    selected=ReadinessSelection(s,None,None,None,None)
    worker=w.Worker(RootContext(Path(t.tracker['path']).parent),t.run/'worker',campaign,ROOT)
    lease=Lease('synthetic','token',0,t.tracker['sha256'])
    # Composition fixtures replace financial admission/expensive gate replay,
    # never production callbacks. Owning producer tests below stay real.
    monkeypatch.setattr(w,'active',lambda a,s,l:(SimpleNamespace(),value,record(t.tracker['path'])))
    monkeypatch.setattr(w,'check_completed_gate',lambda *a,**k: {'synthetic_boundary':True})
    return worker,selected,lease,t,respond,calls


def test_request_round_trip_preserves_budget_and_protocol(instruction_setup,tmp_path):
    t,c,respond,calls=instruction_setup
    producer.initialize_naturalization(t,root=ROOT)
    original=producer.prepare_naturalization_writer(t,root=ROOT)
    path=tmp_path/'saved.json';w.request_saved(path,original)
    assert w.request_saved(path)==original
    doc=json.loads(path.read_text());doc['prepared']['ordinal']=3;path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):producer.validate_request(t,w.request_saved(path))


def test_model_default_never_dispatches_and_completed_response_is_not_recalled(instruction_setup,monkeypatch):
    t,c,respond,calls=instruction_setup;producer.initialize_naturalization(t,root=ROOT)
    req=producer.prepare_naturalization_writer(t,root=ROOT)
    worker=w.Worker(RootContext(t.stage.parent),t.run/'worker',c,ROOT)
    assert w._model(worker,t,req,False,None)=='MODEL_REQUIRED' and not calls
    source=producer.text(record(t.run/'naturalization/writer_source.md'))
    spans=producer.skill_module(ROOT,'naturalize-finance-task-instruction','instruction_spans')
    a,b=spans.extract_editable_bodies(source.encode(),spans.scan_instruction(source.encode()))
    respond(req,{'preamble_body':a.decode(),'input_body':b.decode()})
    assert w._model(worker,t,req,True,'synthetic-token') is None and len(calls)==1


def test_unknown_submitted_model_is_not_recalled(instruction_setup,monkeypatch):
    t,c,respond,calls=instruction_setup;producer.initialize_naturalization(t,root=ROOT)
    req=producer.prepare_naturalization_writer(t,root=ROOT)
    worker=w.Worker(RootContext(t.stage.parent),t.run/'worker',c,ROOT)
    def crash(*a,**k):raise RuntimeError('synthetic network interruption')
    monkeypatch.setattr(executor,'_open',crash)
    observed=producer.dispatch(t,req,c,credential='synthetic')
    assert observed.status!='completed'
    assert w._model(worker,t,req,True,'synthetic')=='MODEL_RECONCILIATION_REQUIRED'
    assert not calls


def test_canonical_phase_order_and_second_runtime_before_enqueue(case,monkeypatch):
    worker,selection,lease,*_=case;seen=[]
    def operation(worker,s,l,phase,directory,**options):
        seen.append(phase)
        return s,l,None
    monkeypatch.setattr(w,'_operation',operation)
    result=w.run(worker,selection,lease)
    assert seen==list(w.STEPS) and result.status=='PUBLICATION_READY'
    assert seen.index('verification-runtime')<seen.index('dialogue-init')<seen.index('final-runtime')<seen.index('enqueue')
    assert result.publication_performed is False
    assert len(list(worker.run.glob('steps/*/complete.json')))==len(w.STEPS)
    with pytest.raises(ValueError):w.step(worker,selection,lease)


@pytest.mark.parametrize('at',w.STEPS)
def test_interruption_retains_exact_intent_and_never_replays(case,monkeypatch,at):
    worker,s,l,*_=case;calls=[]
    def op(worker,s,l,phase,directory,**options):
        calls.append(phase)
        if phase==at:raise KeyboardInterrupt('synthetic crash')
        return s,l,None
    monkeypatch.setattr(w,'_operation',op)
    with pytest.raises(KeyboardInterrupt):w.run(worker,s,l)
    completed=list(worker.run.glob('steps/*/complete.json'))
    if completed:
        prior=w.STEPS[w.STEPS.index(at)-1];v=w.read(record(worker.run/'steps'/prior/'complete.json'));s=w.codec.decode(v['selection']);l=Lease(**v['lease'])
    checkpoint=record(worker.run/'steps'/w.STEPS[w.STEPS.index(at)-1]/'complete.json') if completed else None
    before=list(calls);result=w.step(worker,s,l,checkpoint=checkpoint)
    assert result.status=='RECONCILIATION_REQUIRED' and calls==before
    assert (worker.run/'steps'/at/'exception.json').exists()
    assert not list(worker.context.tracker_root.glob('*notes-transactions*'))


def test_changed_code_and_noncanonical_phase_fail_before_effect(case,monkeypatch):
    worker,s,l,*_=case
    monkeypatch.setattr(w,'_operation',lambda *a,**k:(s,l,'COMMANDS_REQUIRED'))
    with pytest.raises(ValueError):w.step(worker,s,l,phase='enqueue')
    result=w.step(worker,s,l);assert result.status=='COMMANDS_REQUIRED'
    monkeypatch.setattr(w,'code',lambda _: {'changed':'0'*64})
    with pytest.raises(ValueError,match='implementation changed'):w.step(worker,s,l,checkpoint=result.evidence)


def test_pending_capabilities_can_advance_without_overwriting_prior_evidence(case,monkeypatch):
    worker,s,l,*_=case;states=iter(['COMMANDS_REQUIRED','MODEL_REQUIRED',None])
    monkeypatch.setattr(w,'_operation',lambda *a,**k:(s,l,next(states)))
    a=w.step(worker,s,l);assert a.status=='COMMANDS_REQUIRED'
    b=w.step(worker,s,l,checkpoint=a.evidence);assert b.status=='MODEL_REQUIRED'
    assert w.step(worker,s,l,checkpoint=b.evidence).status=='STEP_COMPLETE'
    assert len(list(worker.run.glob('steps/disclosure-build/pending-*.json')))==2


def test_rejected_semantics_stop_without_fabricated_terminal_tracker(case,monkeypatch):
    worker,s,l,*_=case
    monkeypatch.setattr(w,'_operation',lambda *a,**k:(s,l,'NAT_REPAIR_REQUIRED'))
    result=w.run(worker,s,l)
    assert result.status=='NAT_REPAIR_REQUIRED'
    assert w.read(record(worker.context.tracker_root/'0245.json'))['terminal_verdict'] is None
    assert w.step(worker,s,l).status=='RECONCILIATION_REQUIRED'


def test_same_workbook_concurrent_step_rejected(case):
    worker,s,l,*_=case;worker.run.mkdir(parents=True,exist_ok=True)
    with (worker.run/'worker.lock').open('a+b') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):w.step(worker,s,l)


def test_guarded_command_rejects_arbitrary_entry_before_execution(case):
    worker,s,l,t,*_=case
    with pytest.raises(ValueError,match='fixed disclosure'):
        w._guarded(worker,t,worker.run,'evil',['/bin/sh','-c','false'],9)


def test_guarded_command_uses_fixed_memory_deadline_and_guardian_lock(case,monkeypatch):
    worker,s,l,t,*_=case;seen=[]
    def fake(argv,**kw):
        seen.append((argv,kw));kw['stdout'].write(b'synthetic guarded command');return 0
    monkeypatch.setattr(w,'_run_in_process_group',fake)
    argv=[w.sys.executable,'-B',str(ROOT/'.cursor/skills/task-disclosure/scripts/disclose.py'),'--runs-root',str(t.run),'verify','--task-dir',str(t.stage)]
    w._guarded(worker,t,worker.run,'verify',argv,17)
    assert seen[0][1]['memory_limit_bytes']==8*1024**3 and seen[0][1]['timeout_seconds']==900
    assert seen[0][1]['guardian_lock_fds']==(17,)
    w._guarded(worker,t,worker.run,'verify',argv,17)
    assert len(seen)==1


def test_active_real_strict_lease_and_identity_not_caller_flags(tmp_path,monkeypatch):
    from scripts.batch import harbor_tracker_lifecycle as life
    from scripts.batch import harbor_tracker as schema
    from test_harbor_tracker_lifecycle import created
    tracker,initial=created(tmp_path)
    authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    acquired=life.lease_operation(tracker,operation='acquire',authority=authority,lease_kind='task_agent',owner='fixture-worker',scope='workbook:0245',expected_revision=0,expected_sha256=initial['sha256'],duration_ns=10**11)
    before=acquired['tracker'];before['lane_state']['harbor-environment-packager']['state']='passed'
    before['bindings'].update(source_file=before['raw_source'],source_generation=before['raw_source'],segmentation_generation=before['raw_source'],curation=before['raw_source'])
    tracker.write_bytes(life.canonical(before));schema.validate_tracker(before)
    stage=tmp_path/'0245-outputs';stage.mkdir();(stage/'instruction.md').write_text('synthetic')
    s=Selection('0245',stage,w.static.publication._tree_hash(stage)[0],before['bindings'],tmp_path/'norm',tmp_path/'nat',None,None)
    selected=ReadinessSelection(s,None,None,None)
    worker=w.Worker(RootContext(tmp_path),tmp_path/'worker',executor.Campaign(tmp_path/'models',tmp_path/'slots'),ROOT)
    grant=before['leases']['task_agent'];lease=Lease(grant['owner'],grant['token'],before['revision'],record(tracker)['sha256'])
    assert w.active(worker,selected,lease)[1]==before
    for bad in (replace(lease,token='wrong'),replace(lease,owner='wrong'),replace(lease,revision=lease.revision+1),replace(lease,sha256='0'*64)):
        with pytest.raises(ValueError):w.active(worker,selected,bad)
    with pytest.raises(ValueError):w.active(worker,replace(selected,content=replace(s,bindings={**s.bindings,'source_file':record(stage/'instruction.md')})),lease)
    (stage/'instruction.md').write_text('changed')
    with pytest.raises(ValueError,match='stage changed'):w.active(worker,selected,lease)


def test_role_zero_case_requires_current_independent_reconstruction(case,monkeypatch):
    worker,s,l,t,*_=case
    directory=t.run/'disclosure-build'/t.stage.name;directory.mkdir(parents=True)
    before=producer.read(t.tracker);before['bindings']['source_file']=before['raw_source']
    Path(t.tracker['path']).write_text(json.dumps(before));t=replace(t,tracker=record(t.tracker['path']))
    producer.put(directory/'bands.json',{'golden':before['raw_source']['path'],'bands':[]})
    producer.put(directory/'ambiguous_roles.json',dict(schema_version='1.0',task=t.stage.name,cases=[],case_count=0,band_count=0))
    renderer=SimpleNamespace(Book=lambda p: p,prepare_method_items=lambda b,bands:bands,collect_ambiguous_role_cases=lambda items:[])
    monkeypatch.setattr(producer,'skill_module',lambda *a:renderer)
    plan={'cases_path':str(directory/'ambiguous_roles.json')}
    assert w.role_inventory(t,plan)['case_count']==0
    renderer.collect_ambiguous_role_cases=lambda items:[{'band_count':1,'case_id':'collision'}]
    with pytest.raises(ValueError,match='independent current'):w.role_inventory(t,plan)


def test_worker_runs_actual_frozen_nat_executor_and_independent_review(case,monkeypatch):
    import io
    worker,s,l,t,_,calls=case
    value=producer.read(record(t.tracker['path']));value['lane_state'][producer.DISCLOSURE]['state']='passed'
    Path(t.tracker['path']).write_text(json.dumps(value))
    monkeypatch.setattr(w,'active',lambda a,s,l:(SimpleNamespace(),value,record(t.tracker['path'])))
    def opened(req,timeout):
        payload=json.loads(req.data);body=json.loads(payload['messages'][1]['content'])
        if 'writer_source' in body:
            spans=producer.skill_module(ROOT,'naturalize-finance-task-instruction','instruction_spans')
            a,b=spans.extract_editable_bodies(body['writer_source'].encode(),spans.scan_instruction(body['writer_source'].encode()))
            answer={'preamble_body':a.decode(),'input_body':b.decode()}
        else:
            answer={'valid':True,'blocking_findings':[],'clause_checks':[{'source_claim':line,'candidate_claim':line,'preserved':True} for line in producer.editable_claims(ROOT,body['source'])]}
        calls.append(req)
        raw=dict(model=t.policy.model,id='synthetic-'+str(len(calls)),object='chat.completion',created=time.time_ns()//10**9,choices=[dict(finish_reason='stop',message=dict(role='assistant',content=json.dumps(answer)))])
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        return Response(json.dumps(raw).encode())
    monkeypatch.setattr(executor,'_open',opened)
    for phase in ('nat-init','nat-writer','nat-review'):
        directory=worker.run/'steps'/phase;directory.mkdir(parents=True)
        s,l,status=w._operation(worker,s,l,phase,directory,models=True,containers=False,commands=False,credential='synthetic-only',lock_fd=7)
        assert status is None
    assert len(calls)==2
    assert producer.read(record(s.content.naturalization_root/'state.json'))['status']=='applied'
    assert s.content.naturalization_writer_call!=s.content.naturalization_call
    assert w.static.publication._tree_hash(s.content.stage)[0]==s.content.stage_sha256
    assert producer.read(record(s.content.naturalization_root/'state.json'))['attempts'][0]['frozen_submission']


def test_resume_requires_root_selected_checkpoint_hash_not_only_same_selection(case,monkeypatch):
    worker,s,l,*_=case
    monkeypatch.setattr(w,'_operation',lambda *a,**k:(s,l,'COMMANDS_REQUIRED'))
    result=w.step(worker,s,l)
    with pytest.raises(ValueError,match='checkpoint hash'):w.step(worker,s,l)
    saved=Path(result.evidence['path']);value=json.loads(saved.read_text());value['status']='MODEL_REQUIRED';saved.write_text(json.dumps(value))
    with pytest.raises(ValueError):w.step(worker,s,l,checkpoint=result.evidence)


def test_previous_intent_chain_is_hash_revalidated(case,monkeypatch):
    worker,s,l,*_=case
    monkeypatch.setattr(w,'_operation',lambda *a,**k:(s,l,None))
    first=w.step(worker,s,l)
    intent=worker.run/'steps/disclosure-build/intent.json';value=json.loads(intent.read_text());value['step']='enqueue';intent.write_text(json.dumps(value))
    with pytest.raises(ValueError):w.step(worker,first.selection,first.lease,checkpoint=first.evidence)
