from dataclasses import asdict,replace
from pathlib import Path
import pytest
from scripts.batch import harbor_environment_refresh as F
from scripts.batch import harbor_environment_worker as W
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_transition_codec import encode
from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import selected as bootstrap_selected
from test_harbor_environment_worker import selected,worker,responses
from test_harbor_normalization_producer import mock_review


def phase1(worker,monkeypatch):
    s,lease=worker;responses(monkeypatch)
    result=W.advance(s,lease,dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE'
    c=result['selection'].content;mock_review(monkeypatch,W.normalization.Selection(c.workbook,c.bindings,c.normalization_run,c.model_policy,s.normalization_budget))
    result=W.advance(s,result['lease'],dispatch=True,credential='synthetic-token');assert result['status']=='STEP_COMPLETE'
    prior=F.keep(s.run.parent/'root-phase1-result.json',{**result,'lease':asdict(result['lease']),'selection':encode(result['selection'])})
    return s,result['lease'],result['selection'],prior


def test_exact_prefix_current_gate_and_commit_preserve_checkpoint_budget(worker,monkeypatch,tmp_path):
    s,lease,current,prior=phase1(worker,monkeypatch)
    originals={p:p.read_bytes() for p in s.run.glob('*.json')};tracker=s.bootstrap.policy.tracker_root/'0001.json';before=read(record(tracker))
    binding=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    fresh,selected,receipt=F.commit(s,lease,binding)
    assert fresh.revision==lease.revision+1 and selected==current
    assert read(receipt)['prepared']==binding
    after=read(record(tracker));assert after['lane_state']['harbor-normalization-engineer']['attempt']==before['lane_state']['harbor-normalization-engineer']['attempt']==1
    assert after['failure_history']==before['failure_history']
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    # Advance through fixed maintained profile producer without another model call.
    monkeypatch.setattr(W.audit_bridge,'validate_completed',lambda *a,**k:pytest.fail('old bridge may not run'))
    result=F.advance(s,fresh,binding,tmp_path/'next');assert result['status']=='STEP_COMPLETE' and result['step']=='source_profile'


@pytest.mark.parametrize('what',['source','call','root','old_result','extra_binding'])
def test_unrelated_or_tampered_selection_rejected(worker,monkeypatch,tmp_path,what):
    s,lease,current,prior=phase1(worker,monkeypatch);c=current.content
    if what=='source':current=replace(current,content=replace(c,bindings={**c.bindings,'source_file':c.bindings['baseline_inputs']}))
    if what=='call':current=replace(current,content=replace(c,normalization_call=None))
    if what=='root':current=replace(current,content=replace(c,normalization_run=tmp_path/'other'))
    if what=='old_result':Path(prior['path']).write_text('{}')
    if what=='extra_binding':current=replace(current,content=replace(c,bindings={**c.bindings,'unreviewed':{'path':'x','sha256':'0'*64}}))
    before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    with pytest.raises((ValueError,KeyError)):F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    assert before==(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()


def test_after_replace_interruption_is_reconciled_without_replaying_cas(worker,monkeypatch,tmp_path):
    s,lease,current,prior=phase1(worker,monkeypatch);binding=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    def fault(at):
        if at=='after_replace':raise RuntimeError('simulated interrupt')
    with pytest.raises(RuntimeError,match='interrupt'):F.commit(s,lease,binding,fault=fault)
    tracker=s.bootstrap.policy.tracker_root/'0001.json';raw=tracker.read_bytes();receipt=F.reconcile(s,binding)
    assert read(receipt)['revision']==lease.revision+1 and tracker.read_bytes()==raw
    with pytest.raises(ValueError):F.commit(s,lease,binding)


def test_before_replace_interrupt_requires_reconciliation_not_fake_completion(worker,monkeypatch,tmp_path):
    s,lease,current,prior=phase1(worker,monkeypatch);binding=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    def fault(at):
        if at=='before_replace':raise RuntimeError('simulated interrupt')
    with pytest.raises(RuntimeError):F.commit(s,lease,binding,fault=fault)
    with pytest.raises(ValueError,match='not exactly committed'):F.reconcile(s,binding)


def test_current_refresh_continues_through_phase2_without_reentering_legacy_bridge(worker,monkeypatch,tmp_path):
    s,lease,current,prior=phase1(worker,monkeypatch)
    binding=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    lease,_,_=F.commit(s,lease,binding)
    monkeypatch.setattr(W.audit_bridge,'validate_completed',lambda *a,**k:pytest.fail('old bridge may not run'))
    for step in ('source_profile','normalization_phase2'):
        result=F.advance(s,lease,binding,tmp_path/step)
        assert result['status']=='STEP_COMPLETE' and result['step']==step,result
        lease=result['lease']
    # Before environment starts, the actual phase2 results remain independently current.
    F.validate(s,binding)
    prior_run=W.environment.subprocess.run
    def local(argv,**kwargs):
        if len(argv)>1 and Path(argv[1]).name in {'xl_variable_mcp.py','xl_input_mask.py','xl_output_task.py'}:
            import importlib
            from types import SimpleNamespace
            code=importlib.import_module(Path(argv[1]).stem).main(argv[2:]);return SimpleNamespace(returncode=code or 0)
        return prior_run(argv,**kwargs)
    monkeypatch.setattr(W.environment.subprocess,'run',local)
    result=F.advance(s,lease,binding,tmp_path/'environment')
    assert result['status']=='STEP_COMPLETE' and result['step']=='environment',result
    final=F.advance(s,result['lease'],binding,tmp_path/'finished')
    assert final['status']=='ENVIRONMENT_READY'


def test_maintained_coordinator_refresh_uses_closed_selection_and_actual_cas(worker,monkeypatch,tmp_path):
    from scripts import harbor_campaign as C
    s,lease,current,prior=phase1(worker,monkeypatch)
    legacy=F.keep(tmp_path/'legacy.json',C.bootstrap.serial(dict(worker=asdict(s),lease=asdict(lease))))
    selected=F.keep(tmp_path/'current.json',encode(current));lease_record=F.keep(tmp_path/'renewed.json',C.encode(lease))
    controls=C.Controls(tmp_path/'actual-workbook-slot.lock')
    def guarded(command,**kw):
        assert kw['memory_limit_bytes']==8*1024**3 and kw['timeout_seconds']==3300 and len(kw['guardian_lock_fds'])==1
        C.child(dict(path=command[-2],sha256=command[-1]));return 0
    monkeypatch.setattr(C,'_run_in_process_group',guarded)
    outcome=C.run('refresh',dict(legacy=legacy,checkpoint=prior,current=selected,lease=lease_record),controls,tmp_path/'import')
    result=read(read(outcome)['result']);assert result['status']=='IMPORTED'
    assert C.selected(result['descriptor'])[0]==s
    descriptor=read(result['descriptor']);assert descriptor['schema_version']=='harbor-environment-coordinator-refresh/v1'
    monkeypatch.setattr(W.audit_bridge,'validate_completed',lambda *a,**k:pytest.fail('legacy audit reentry'))
    next_=C.run('step',dict(descriptor=result['descriptor'],checkpoint=read(outcome)['result'],captures=None),controls,tmp_path/'profile')
    current=read(read(next_)['result']);assert current['status']=='STEP_COMPLETE' and current['step']=='source_profile'


def test_real_partition_sibling_adopts_exact_research_predecessor(worker,monkeypatch,tmp_path):
    from scripts.batch import harbor_normalization_partitioned as P
    from test_normalization_partitioned import respond
    s,lease=worker;responses(monkeypatch);result=W.advance(s,lease,dispatch=True,credential='synthetic')
    old=result['selection'];lease=result['lease'];c=old.content
    prior=F.keep(tmp_path/'research-result.json',{**result,'lease':asdict(lease),'selection':encode(old)})
    ns=W.normalization.Selection(c.workbook,c.bindings,c.normalization_run,c.model_policy,s.normalization_budget)
    W.normalization.prepare(ns);new=replace(ns,run=tmp_path/'fresh'/ns.run.name)
    prepared=P.prepare(new,s.campaign,tmp_path/'parts',prior_plan=record(ns.run/'production-selection.json'))
    respond(monkeypatch,new);P.dispatch(prepared,0,1,credential='synthetic');emitted=read(P.emit_phase1(prepared));r=new.run
    b={**c.bindings,'normalization_partitioned_review':emitted['normalization_partitioned_review'],'normalization_draft':record(r/'draft.json'),'normalizer':record(r/'normalize_0001.py'),'normalization_prepared_manifest':prepared,'normalized_phase1':emitted['phase1'],'normalized_final':record(r/'normalized.json'),'normalization_report':record(r/'normalization_report.json'),'normalization_exclusions':record(r/'exclusions.json')}
    current=replace(old,content=replace(c,normalization_run=r,normalization_prepared=prepared,normalization_call=None,bindings=b))
    proof=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh')
    fresh,selected,_=F.commit(s,lease,proof)
    assert selected.content.normalization_call is None
    assert read(record(s.bootstrap.policy.tracker_root/'0001.json'))['lane_state']['harbor-normalization-engineer']['attempt']==1
    assert read(prepared)['original_budget']['attempt_id']==s.normalization_budget.attempt_id
    for step in ('source_profile','normalization_phase2'):
        result=F.advance(s,fresh,proof,tmp_path/step)
        assert result['status']=='STEP_COMPLETE' and result['step']==step,result
        fresh=result['lease']
    F.validate(s,proof)


from test_audit_renderer_recovery import retained
from test_audit_inventory_lineage import audit_chain,normalized


def test_complete_two_call_renderer_locator_upgrade_keeps_exact_calls(normalized,audit_chain):
    second,prepared,call,args,receipt,original,recovery,campaign,emitted=normalized
    old_b,new_b,_,_,worker,_=audit_chain;r=second.run
    base={**old_b,'normalization_draft':record(r/'draft.json'),'normalizer':record(r/f'normalize_{second.workbook}.py'),'normalization_prepared_manifest':prepared,'normalization_prepared_request':read(prepared)['request'],'normalized_phase1':read(emitted)['phase1'],'normalized_final':record(r/'normalized.json'),'normalization_report':record(r/'normalization_report.json'),'normalization_exclusions':record(r/'exclusions.json')}
    content=replace(W._content(worker,base,()),normalization_run=r,normalization_prepared=prepared,normalization_call=call)
    old=W._selection(content)
    current=replace(old,content=replace(content,bindings={**base,**new_b,'normalization_audit_applicability':receipt,'normalization_original_locator_code':args['original_locator_code']}))
    raw=Path(original.response['path']).read_bytes()
    assert F.unchanged(old,current) is False
    assert W.lanes.normalization_projection(current.content,phase1=True)['semantic_attempt']==2
    assert Path(original.response['path']).read_bytes()==raw
    assert current.content.normalization_call==old.content.normalization_call


@pytest.mark.parametrize('which',['code','budget','evidence','witness','current_file','call'])
def test_refresh_proof_and_retained_witness_tamper_fails_closed(worker,monkeypatch,tmp_path,which):
    s,lease,current,prior=phase1(worker,monkeypatch)
    binding=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh');v=read(binding)
    if which=='code':v['code']={**v['code'],'sha256':'0'*64}
    if which=='budget':v['semantic_budget_reset']=True
    if which=='evidence':v['evidence']=v['evidence'][1:]
    if which=='witness':Path(v['mutable_witnesses'][0]['copy']['path']).write_bytes(b'altered')
    if which=='current_file':Path(current.content.bindings['normalized_final']['path']).write_bytes(b'altered')
    if which=='call':Path(current.content.normalization_call.response['path']).write_bytes(b'altered')
    if which in {'code','budget','evidence'}:binding=F.keep(tmp_path/'forged.json',v)
    before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    with pytest.raises((ValueError,KeyError)):F.validate(s,binding)
    assert (s.bootstrap.policy.tracker_root/'0001.json').read_bytes()==before


def test_changed_worker_recipe_cannot_be_inferred_from_new_root_path(worker,monkeypatch,tmp_path):
    s,lease,current,prior=phase1(worker,monkeypatch)
    altered=replace(s,campaign=replace(s.campaign,root=tmp_path/'other-model-budget'))
    with pytest.raises(ValueError,match='worker recipe'):F.prepare(altered,lease,prior,current,'normalization_phase1',tmp_path/'refresh')


def test_maintained_recover_refresh_after_committed_interruption(worker,monkeypatch,tmp_path):
    from scripts import harbor_campaign as C
    s,lease,current,prior=phase1(worker,monkeypatch)
    legacy=F.keep(tmp_path/'legacy.json',C.bootstrap.serial(dict(worker=asdict(s),lease=asdict(lease))))
    proof=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'prefix')
    def fault(at):
        if at=='after_replace':raise RuntimeError('simulated parent loss')
    with pytest.raises(RuntimeError):F.commit(s,lease,proof,fault=fault)
    tracker_path=s.bootstrap.policy.tracker_root/'0001.json';tracker=read(record(tracker_path))
    fresh=replace(lease,revision=tracker['revision'],sha256=record(tracker_path)['sha256'])
    lease_record=F.keep(tmp_path/'lease.json',C.encode(fresh))
    def guarded(command,**kw):C.child(dict(path=command[-2],sha256=command[-1]));return 0
    monkeypatch.setattr(C,'_run_in_process_group',guarded)
    before=tracker_path.read_bytes()
    outcome=C.run('recover-refresh',dict(legacy=legacy,refresh=proof,lease=lease_record),C.Controls(tmp_path/'actual.lock'),tmp_path/'recover')
    result=read(read(outcome)['result']);assert result['status']=='IMPORTED'
    assert C.selected(result['descriptor'])[1]==fresh and tracker_path.read_bytes()==before

@pytest.fixture
def refreshed_profile(worker,monkeypatch,tmp_path):
    from scripts import harbor_campaign as C
    s,lease,current,prior=phase1(worker,monkeypatch)
    proof=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'first-prefix')
    committed_lease,_,receipt=F.commit(s,lease,proof)
    controls=C.Controls(tmp_path/'shared.lock')
    descriptor=C._descriptor(s,committed_lease,controls,(proof,receipt));descriptor.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=proof)
    descriptor=F.keep(tmp_path/'descriptor.json',descriptor)
    result=F.advance(s,committed_lease,proof,tmp_path/'profile')
    assert result['step']=='source_profile'
    saved=F.keep(tmp_path/'profile-result.json',{**result,'descriptor':descriptor,'lease':C.encode(result['lease']),'selection':encode(result['selection'])})
    return s,result['lease'],result['selection'],saved,proof,descriptor


def test_second_prefix_uses_exact_refreshed_checkpoint_and_finishes_phase2(refreshed_profile,tmp_path):
    s,lease,current,prior,old,descriptor=refreshed_profile
    before=read(old);raw=Path(old['path']).read_bytes()
    proof=F.prepare(s,lease,prior,current,'source_profile',tmp_path/'second-prefix')
    assert read(proof)['checkpoint']==record(Path(old['path']).parent/'continuation/source_profile.json')
    lease,_,_=F.commit(s,lease,proof)
    result=F.advance(s,lease,proof,tmp_path/'phase2')
    assert result['status']=='STEP_COMPLETE' and result['step']=='normalization_phase2'
    F.validate(s,proof)
    assert Path(old['path']).read_bytes()==raw
    assert not (s.run/'source_profile.json').exists()

@pytest.mark.parametrize('change',['checkpoint','competing','later','commit','owner','cycle'])
def test_refreshed_predecessor_rejects_foreign_or_incomplete_history(refreshed_profile,tmp_path,change):
    from scripts import harbor_campaign as C
    s,lease,current,prior,old,descriptor=refreshed_profile
    directory=Path(old['path']).parent
    if change=='checkpoint':(directory/'continuation/source_profile.json').write_text('{}')
    elif change=='competing':F.keep(s.run/'source_profile.json',read(record(directory/'continuation/source_profile.json')))
    elif change=='later':F.keep(directory/'continuation/environment.json',{'unfinished':True})
    elif change=='commit':(directory/'committed.json').write_text('{}')
    elif change=='owner':
        value=read(descriptor);value['worker']=C.encode(replace(s,run=tmp_path/'foreign'))
        d=F.keep(tmp_path/'foreign-descriptor.json',value);value=read(prior);value['descriptor']=d;prior=F.keep(tmp_path/'foreign-result.json',value)
    if change=='cycle':
        with pytest.raises(ValueError,match='cyclic'):
            F.predecessor(s,prior,current=current,_seen=((old['path'],old['sha256']),))
    else:
        with pytest.raises((ValueError,KeyError)):
            F.prepare(s,lease,prior,current,'source_profile',tmp_path/'must-not-exist')


def test_three_prefix_ancestry_keeps_original_checkpoints_and_profiles(refreshed_profile,tmp_path):
    from scripts import harbor_campaign as C
    s,lease,current,prior,first,_=refreshed_profile
    second=F.prepare(s,lease,prior,current,'source_profile',tmp_path/'second')
    committed,_,receipt=F.commit(s,lease,second)
    result=F.advance(s,committed,second,tmp_path/'phase2')
    descriptor=C._descriptor(s,committed,C.Controls(tmp_path/'shared.lock'),(second,receipt))
    descriptor.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=second)
    descriptor=F.keep(tmp_path/'second-descriptor.json',descriptor)
    prior=F.keep(tmp_path/'phase2-result.json',{**result,'descriptor':descriptor,'lease':C.encode(result['lease']),'selection':encode(result['selection'])})
    third=F.prepare(s,result['lease'],prior,result['selection'],'normalization_phase2',tmp_path/'third')
    F.validate(s,third)
    assert read(third)['checkpoint']==record(Path(second['path']).parent/'continuation/normalization_phase2.json')
    assert read(third)['selection']==encode(result['selection'])
    assert not (s.run/'source_profile.json').exists()
