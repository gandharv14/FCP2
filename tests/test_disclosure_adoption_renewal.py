"""Actual lifecycle renewal followed by archived-round authorization plumbing."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_disclosure_review_round as Q
from scripts.batch import harbor_instruction_worker as W,harbor_tracker_lifecycle as life
from scripts.fleet import continuation_lease as L,disclosure_adoption_continue as S
from scripts.batch.harbor_admission import read,record
from test_instruction_worker import case,instruction_setup
from test_harbor_environment_worker import worker,selected,setup

@pytest.fixture
def renewed(case,tmp_path,monkeypatch):
    w,s,lease,*_=case
    path=w.context.tracker_root/(s.content.workbook+'.json');before_value=read(record(path))
    acquired=life.lease_operation(path,operation='acquire',authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True),lease_kind='task_agent',owner='fixture',scope='workbook:'+s.content.workbook,expected_revision=before_value['revision'],expected_sha256=record(path)['sha256'],duration_ns=10**12)
    current_value=read(record(path));lease=W.Lease('fixture',current_value['leases']['task_agent']['token'],acquired['revision'],acquired['sha256'])
    def active(worker,selection,current):
        value=read(record(path));life.validate_lifecycle(value)
        assert record(path)['sha256']==current.sha256 and value['revision']==current.revision
        assert (value['leases']['task_agent']['owner'],value['leases']['task_agent']['token'])==(current.owner,current.token)
        return SimpleNamespace(),value,record(path)
    monkeypatch.setattr(W,'active',active)
    monkeypatch.setattr(W,'_operation',lambda w,s,l,*a,**kw:(s,l,None))
    build=W.step(w,s,lease);old=record(w.context.tracker_root/(s.content.workbook+'.json'))
    before=W.snapshot(tmp_path/'before.json',old)
    build_result=W.keep(tmp_path/'build-result.json',dict(checkpoint=build.evidence))
    plan=W.keep(tmp_path/'round-plan.json',dict(build=dict(result=build_result)))
    review_plan=W.keep(tmp_path/'review-plan.json',{})
    result=W.keep(tmp_path/'round-result.json',dict(review_plan=review_plan,aggregate=review_plan))
    authority=W.keep(tmp_path/'authority.json',dict(adoption=record(A.__file__),context=dict(plan=plan),round_result=result,source=read(old)['bindings']['original_raw_source'],workbook=s.content.workbook,renewal_module=record(L.__file__)))
    current,complete=L.renew(life=life,read=read,record=record,keep=W.keep,path=old['path'],lease=lease,authority_record=authority,validation=lambda b,l:[old],custody=lambda:None,directory=tmp_path/'lease')
    after=W.snapshot(tmp_path/'after.json',record(old['path']))
    instruction=record(s.content.stage/'instruction.md');instruction_archive=W.snapshot(tmp_path/'instruction.snapshot.md',instruction)
    archives=[dict(original=old,archive=before),dict(original=instruction,archive=instruction_archive)]
    certificate=W.keep(tmp_path/'renewal.json',dict(schema_version=A.RENEWAL,implementation=record(A.__file__),authority=authority,round_plan=plan,round_result=result,module=record(L.__file__),completion=complete,before=before,after=after,historical_records=archives,historical_instruction=instruction))
    return w,s,current,certificate,plan,result,old,before,after,build_result,instruction_archive


def test_actual_changed_tracker_uses_authentic_archive_and_fresh_owning_custody(renewed,monkeypatch,tmp_path):
    w,s,current,certificate,plan,result,old,before,after,build_result,instruction_archive=renewed
    original=W.active(w,s,current)[2];assert original['sha256']!=old['sha256']
    A.validate_renewal(certificate,w,current,plan,result,live=True)
    submitted=W.keep(tmp_path/'submitted.json',dict(budget_evidence=[before,before]))
    call=SimpleNamespace(submitted=submitted)
    inputs=dict(original_source=s.content.bindings['original_raw_source'],workbook=s.content.workbook,instruction=instruction_archive)
    checked=(inputs,{},[old]);seen=[]
    def completed(*a,**kw):
        # This is the original round's historical tracker requirement; a live
        # check of the old hash would fail after the real L.renew CAS above.
        assert Q.life.checked_record(old)==old and Q.read(old)==read(before)
        assert W.active(w,s,current)[2]==original
        seen.append('validated old round, live new custody')
        return inputs,[call],[]
    monkeypatch.setattr(A.history,'completed_run',completed)
    monkeypatch.setattr(Q,'completed_build',lambda *a,**kw:(w,s,current))
    monkeypatch.setattr(A.review,'validate_plan',lambda *a,**kw:checked)
    permit=A.authorize(w,s,current,round_plan=plan,round_result=result,directory=tmp_path/'adoption',renewal=certificate)
    value=read(permit)
    assert value['renewal']==certificate and value['lease']==asdict(current)and value['tracker']==original
    assert value['historical_records'][0]==dict(original=old,archive=before)
    assert seen and W.active(w,s,current)[2]==original
    assert Path(before['path']).read_bytes()!=Path(old['path']).read_bytes()
    with pytest.raises(ValueError):Q.life.checked_record(old)


@pytest.mark.parametrize('defect',['missing_before','altered_before','altered_after','live_drift','foreign_authority'])
def test_missing_archive_or_live_drift_blocks_before_adoption(renewed,tmp_path,defect):
    w,s,current,certificate,plan,result,old,before,after,*_=renewed
    if defect=='missing_before':Path(before['path']).unlink()
    elif defect=='altered_before':Path(before['path']).write_text('{}')
    elif defect=='altered_after':Path(after['path']).write_text('{}')
    elif defect=='live_drift':Path(old['path']).write_text('{}')
    else:plan=W.keep(tmp_path/'foreign-plan.json',{})
    with pytest.raises((ValueError,FileNotFoundError)):
        A.validate_renewal(certificate,w,current,plan,result,live=True)
    assert not (w.run/'disclosure-round-adoption.json').exists()


def test_completed_context_does_not_substitute_binding_or_read_functions(monkeypatch):
    from scripts.fleet import disclosure_review_continue as M
    old_context=M.context;old_binding=M.binding;old_read=M.read
    changed=S.completed_review_context(M,{}, {},Path('/unselected'))
    assert changed is not old_context and M.context is old_context
    assert M.binding is old_binding and M.read is old_read


def test_cold_completed_reader_checks_actual_old_archive_and_new_live_tracker(renewed,tmp_path):
    import sys
    import scripts.batch as package
    from scripts.fleet import disclosure_review_continue as M
    w,s,current,certificate,plan,result,old,before,after,*_=renewed
    home=tmp_path/'cold';home.mkdir();W.keep(home/'round-renewal.json',Path(certificate['path']).read_bytes())
    row=read(certificate)['authority'];p=dict(history=record(A.history.__file__),adoption=record(A.__file__),context=dict(plan=plan),round_result=result)
    old_binding=M.binding;old_read=M.read
    names=['harbor_disclosure_round_history','harbor_disclosure_round_adoption'];prior={n:(sys.modules.get('scripts.batch.'+n),getattr(package,n,None))for n in names}
    try:
        adapted=S.completed_review_context(M,p,row,home)
        check=adapted.__wrapped__.__globals__['_adoption_completed_custody']
        previous=W.Lease(**read(read(certificate)['completion'])['previous'])
        assert check(w,previous,before)is True
        assert M.binding is old_binding and M.read is old_read
        assert M.binding(old['path'])==after|{'path':old['path']}
        Path(old['path']).write_text('{}')
        with pytest.raises(ValueError):check(w,previous,before)
    finally:
        for name,(module,attribute)in prior.items():
            if module is None:sys.modules.pop('scripts.batch.'+name,None)
            else:sys.modules['scripts.batch.'+name]=module
            if attribute is None:
                if hasattr(package,name):delattr(package,name)
            else:setattr(package,name,attribute)


@pytest.mark.parametrize('defect',[None,'stale_checkpoint','foreign_descriptor','wrong_owner','missing_archive','altered_archive','live_drift'])
def test_original_checkpoint_cold_reentry_after_actual_later_cas(renewed,tmp_path,monkeypatch,defect):
    from contextlib import nullcontext
    from dataclasses import replace
    from scripts import harbor_instruction_campaign as I
    from scripts.fleet import instruction_semantic_continue as U
    w,s,current,certificate,plan,result,old,before,after,*_=renewed
    home=tmp_path/'continued';home.mkdir()
    W.keep(home/'round-renewal.json',Path(certificate['path']).read_bytes())
    receipt=dict.fromkeys(A.KEYS,None);receipt.update(schema_version=A.SCHEMA,implementation=record(A.__file__),prior_continuations={})
    permit=W.keep(home/'permit/permit.json',receipt);new=replace(w,disclosure_repair=permit)
    # An actual second lifecycle CAS models a later owning tracker checkpoint.
    later,completion=L.renew(life=life,read=read,record=record,keep=W.keep,path=old['path'],lease=current,authority_record=read(certificate)['authority'],validation=lambda b,l:[after],custody=lambda:None,directory=tmp_path/'later-lease')
    chosen=current if defect=='stale_checkpoint'else replace(later,owner='foreign')if defect=='wrong_owner'else later
    controls=I.base.Controls(tmp_path/'cold-workbook.lock')
    recipe=I.base.admission.DependencyRecipe(record(Path(I.base.sys.executable).resolve()),(),(),False,4,2)
    descriptor=W.keep(home/'descriptor.json',dict(schema_version=I.SCHEMA,worker=I.worker_encode(new),controls=I.base.encode(controls),dependencies=I.base.encode(recipe),origin=[],code=record(I.__file__),initial=dict(selection=I.codec.encode(s),lease=I.base.encode(chosen))))
    completed=W.keep(home/'completed.json',dict(status='completed',selection=I.codec.encode(s),lease=asdict(chosen)))
    checkpoint=W.keep(home/'checkpoint.json',I._result('STEP_COMPLETE',descriptor,s,chosen,checkpoint=completed,next_step='nat-init',evidence=[completed]))
    if defect=='foreign_descriptor':descriptor=W.keep(home/'foreign.json',read(descriptor))
    intent=W.keep(home/'first-step/intent.json',dict(schema_version=I.SCHEMA,action='step',models=False,code=record(I.__file__),base_code=record(I.base.__file__),controls=I.base.encode(controls),arguments=dict(descriptor=descriptor,checkpoint=checkpoint)))
    job=W.keep(home/'job.json',dict(driver=record(U.__file__)))
    authority=read(certificate)['authority'];p=dict(_binding=authority,output=str(home),helper=record(S.__file__),utilities=record(U.__file__),original_job=job,source=s.content.bindings['original_raw_source'],owning_profile={})
    command=[str(Path(I.base.sys.executable)),'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']]
    side=W.keep(home/'first-step/aggregate-bootstrap.json',dict(schema_version=S.SCHEMA,plan=authority,helper=p['helper'],kind='instruction',intent=intent,original_command=command,seconds=I.guard_seconds('step',read(intent)['arguments']),memory_bytes=8589934592,automatic_retry=False))
    env=(I.base,I,None,W,None,dict(run_root=str(w.run.parent)),w.root,controls)
    if defect=='missing_archive':Path(after['path']).unlink()
    elif defect=='altered_archive':Path(after['path']).write_text('{}')
    elif defect=='live_drift':Path(old['path']).write_text('{}')
    adapter=SimpleNamespace(installed=lambda *a,**k:nullcontext())
    if defect is None:
        # Historical custody is valid despite a different live SHA; exact I
        # descriptor/prior and W live CAS checks are still executed below.
        A.validate_renewal(certificate,w,current,plan,result,live=False)
        actual=S.current_checkpoint(p,env,w,current,adapter,(intent,side))
        assert actual==(new,s,later) and later.sha256!=current.sha256
        with pytest.raises(ValueError):A.validate_renewal(certificate,w,current,plan,result,live=True)
    else:
        with pytest.raises((ValueError,AssertionError,FileNotFoundError)):
            S.current_checkpoint(p,env,w,current,adapter,(intent,side))
