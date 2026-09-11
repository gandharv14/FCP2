"""Real lifecycle/notes transactions; financial/release I/O is a test boundary.

No model, Excel, container or production task operations occur in these tests.
Owning financial and release suites supply the independent semantic coverage.
"""
from dataclasses import replace,asdict
from pathlib import Path
import copy,json,os,fcntl
import pytest
from scripts import harbor_publish as p
from scripts.batch import harbor_promotion_evidence as q
from scripts.batch import harbor_ready_transitions as ready
from scripts.batch import harbor_tracker_lifecycle as life
from scripts.batch.harbor_admission import record,read
from test_harbor_ready_transitions import fixture,kw

REAL_PATHS=p._paths


def setup(tmp_path,monkeypatch):
    trackerroot=tmp_path/'trackers';trackerroot.mkdir()
    facade,path,start,selection,proof=fixture(trackerroot,monkeypatch)
    (selection.content.stage/'environment').mkdir()
    (selection.content.stage/'environment/Dockerfile').write_text('FROM synthetic-fixture\n')
    queued=facade.transition('0245','enqueue',selection,**kw(start))
    lease0=queued['tracker']['leases']['task_agent']
    lease=p.Lease(lease0['owner'],lease0['token'],queued['revision'],queued['sha256'])
    work=tmp_path/'worker';work.mkdir();initial=work/'initial.json';initial.write_text('{}')
    intent=p._keep(work/'intent.json',dict(step='enqueue',selection=p.codec.encode(selection),
        code=p.instruction.code(type('Installed',(),{'root':p.ROOT})()),initial=record(initial)))
    checkpoint=p._keep(work/'complete.json',dict(step='enqueue',status='completed',
        selection=p.codec.encode(selection),lease=asdict(lease),intent=intent))
    journal=tmp_path/'journal';journal.mkdir()
    public=tmp_path/'published';public.mkdir();source=tmp_path/'sources';source.mkdir();seg=tmp_path/'segments';seg.mkdir()
    request=p.Request(selection,lease,checkpoint,trackerroot,public,tmp_path/'output',tmp_path/'queue.lock',tmp_path/'slot.lock')
    # This fixture's fake generation binding and stage live under its tracker
    # directory. Path-separation policy is tested separately with concrete roots.
    monkeypatch.setattr(p,'_paths',lambda r,j:(path,source,seg))
    queued_binding=p._keep(journal/'queued-tracker.json',path.read_bytes())
    bound=p._keep(journal/'intent.json',dict(schema_version=p.SCHEMA,request=p.encode(request),queued_tracker=queued_binding,
        initial_output_sha256=None,code=p._codes()))
    monkeypatch.setattr(p.release,'resolve_build_context',lambda *a,**k:{'bindings':{'source_generation_id':'a'*64,'segmentation_generation_id':'a'*64}})
    monkeypatch.setattr(p.qualified,'validate_audit_bindings',lambda *a,**k:{})
    publications=[]
    def publish(root,wb,**options):
        directory=public/'releases'/('c'*64);directory.mkdir(parents=True,exist_ok=True)
        manifest={'source_generation':{'generation_path':str(source)},'segmentation_generation':{'generation_path':str(seg)},'release_id':'c'*64,'task_generation':{'generation_path':str(public/'task-generations'/options['task_generation_id'])}}
        p._keep(directory/'release-manifest.json',manifest);p._keep(public/'current-release.json',{'release_id':'c'*64})
        publications.append(options)
        return directory,manifest
    monkeypatch.setattr(p.qualified,'publish_or_resume',publish)
    monkeypatch.setattr(p.release,'resolve_current_release',lambda *a,**k:(public/'releases'/('c'*64),read(record(public/'releases'/('c'*64)/'release-manifest.json'))))
    def fixed_release(before,chosen,published):
        assert chosen==selection
        task=read(record(public/'releases'/('c'*64)/'release-manifest.json'))['task_generation']['generation_path']
        assert p.release._tree_hash(Path(task))[1]['instruction.md']==p.release._tree_hash(selection.content.stage)[1]['instruction.md']
        return {'task_generation_id':Path(task).name,'release_id':'c'*64,'previous_release_id':None},[published.pointer,published.manifest]
    monkeypatch.setattr(ready,'_release',fixed_release)
    monkeypatch.setattr(q,'historical_release',lambda *a:{'release_path':'synthetic-immutable-release'})
    monkeypatch.setattr(q,'historical_evidence',lambda *a:[])
    return request,bound,path,queued,journal,publications


def test_full_strict_transaction_and_independent_lease_release_chain(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    result=read(p.execute(bound));current=read(record(path))
    assert result['status']=='PROMOTED' and current['execution_state']=='PROMOTED'
    assert all(v is None for v in current['leases'].values())
    assert current['repairs']==queued['tracker']['repairs']
    assert all(current['lane_state'][k]['attempt']==2 for k in current['lane_state'])
    assert calls[0]['resuming'] is False
    old=path.read_bytes();assert read(p.execute(bound))==result and path.read_bytes()==old
    assert len(calls)==1  # completed promotion performs no release mutation


@pytest.mark.parametrize('changed',['selection','lease','status','step','code','initial'])
def test_bad_queued_checkpoint_rejected_before_publication(tmp_path,monkeypatch,changed):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    checkpoint=read(r.checkpoint)
    if changed in {'code','initial'}:
        item=read(checkpoint['intent'])
        if changed=='code':item['code']={}
        else:item['initial']['sha256']='f'*64
        file=Path(checkpoint['intent']['path']);file.write_bytes(life.canonical(item));checkpoint['intent']=record(file)
    elif changed=='lease':checkpoint['lease']['token']='foreign'
    elif changed=='selection':checkpoint['selection']={'type':'scalar','value':None}
    else:checkpoint[changed]='not-completed'
    file=Path(r.checkpoint['path']);file.write_bytes(life.canonical(checkpoint))
    r=replace(r,checkpoint=record(file))
    old=path.read_bytes()
    with pytest.raises(ValueError):p._checkpoint(r)
    assert path.read_bytes()==old and not calls


@pytest.mark.parametrize('point',['task_materialized','after_release','notes_replaced','tracker_replaced','lease_release'])
def test_explicit_resume_after_exact_interrupted_mutation(tmp_path,monkeypatch,point):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    original_task=p.release.publish_task_generation
    original_pub=p.qualified.publish_or_resume
    original_transition=ready.ReadyTransitions.transition
    original_lease=p.lifecycle.lease_operation
    armed=[True]
    def fail():
        if armed[0]:armed[0]=False;raise RuntimeError('synthetic interruption')
    if point=='task_materialized':
        def task(*a,**k):
            value=original_task(*a,**k);fail();return value
        monkeypatch.setattr(p.release,'publish_task_generation',task)
    elif point=='after_release':
        def pub(*a,**k):
            value=original_pub(*a,**k);fail();return value
        monkeypatch.setattr(p.qualified,'publish_or_resume',pub)
    elif point in {'notes_replaced','tracker_replaced'}:
        def transition(self,*a,**k):
            if a[1]=='promoted':k['_fault']=lambda value:fail() if value==point else None
            return original_transition(self,*a,**k)
        monkeypatch.setattr(ready.ReadyTransitions,'transition',transition)
    else:
        def lease(*a,**k):
            value=original_lease(*a,**k)
            if k['operation']=='release':fail()
            return value
        monkeypatch.setattr(p.lifecycle,'lease_operation',lease)
    with pytest.raises(RuntimeError,match='interruption'):p.execute(bound)
    assert read(bound)['queued_tracker']['sha256']==queued['sha256']
    assert read(p.execute(bound))['status']=='PROMOTED'
    assert all(x is None for x in read(record(path))['leases'].values())


def test_foreign_output_change_aborts_before_release(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    r.output.mkdir();(r.output/'foreign.txt').write_text('unrelated output')
    with pytest.raises(ValueError,match='output changed'):p.execute(bound)
    assert not calls and read(record(path))['execution_state']=='publication_queued'
    assert (r.output/'foreign.txt').read_text()=='unrelated output'


def test_failed_independent_qualification_keeps_leases(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(q,'validate',lambda *a,**k:{'verified_promoted':False})
    with pytest.raises(ValueError,match='independent current qualification'):p.execute(bound)
    current=read(record(path));assert current['execution_state']=='PROMOTED'
    assert current['leases']['publication'] and current['leases']['task_agent']


def test_changed_readiness_proof_stops_before_any_lease_mutation(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(ready,'_proof',lambda *a:(_ for _ in ()).throw(ValueError('stale current gate')))
    old=path.read_bytes()
    with pytest.raises(ValueError,match='stale current gate'):p.execute(bound)
    assert path.read_bytes()==old and not calls


@pytest.mark.parametrize('which',['queue_lock','slot_lock'])
def test_real_shared_locks_refuse_concurrent_owner(tmp_path,which):
    r=p.Request(None,None,None,tmp_path,tmp_path,tmp_path,tmp_path/'queue',tmp_path/'slot')
    with p.locks(r):
        fd=os.open(getattr(r,which),os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
    with p.locks(r):pass


def test_symlink_lock_ancestry_rejected(tmp_path):
    alias=tmp_path/'alias';alias.symlink_to(tmp_path,target_is_directory=True)
    r=p.Request(None,None,None,tmp_path,tmp_path,tmp_path,alias/'queue',tmp_path/'slot')
    with pytest.raises(ValueError,match='symlink'):
        with p.locks(r):pass


def test_stale_lease_compare_and_swap_refuses_acquisition(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    before=read(record(path));before['last_gate']='foreign_change';path.write_bytes(life.canonical(before))
    with pytest.raises(ValueError):p.execute(bound)
    assert not calls and read(record(path))['leases']['publication'] is None


def test_unrelated_tracker_change_after_task_staging_cannot_publish(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    original=p.release.publish_task_generation
    def stop(*a,**k):
        value=original(*a,**k)
        current=read(record(path));current['last_gate']='foreign_change';path.write_bytes(life.canonical(current))
        return value
    monkeypatch.setattr(p.release,'publish_task_generation',stop)
    with pytest.raises(ValueError,match='diverged before release'):p.execute(bound)
    assert not calls


def test_corrupted_task_after_interruption_is_not_replaced_or_published(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    original=p.qualified.publish_or_resume
    monkeypatch.setattr(p.qualified,'publish_or_resume',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('hold')))
    with pytest.raises(RuntimeError):p.execute(bound)
    task=read(record(journal/'task.json'));directory=Path(task['manifest']['path']).parent
    (directory/'instruction.md').write_text('changed task')
    monkeypatch.setattr(p.qualified,'publish_or_resume',original)
    with pytest.raises(ValueError):p.execute(bound)
    assert not calls


def test_publication_conflict_preserves_queued_tracker_and_leases(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(p.qualified,'publish_or_resume',lambda *a,**k:(_ for _ in ()).throw(p.release.ReleasePublicationError('prior release changed')))
    with pytest.raises(ValueError,match='prior release changed'):p.execute(bound)
    current=read(record(path));assert current['execution_state']=='publication_queued'
    assert current['leases']['publication'] and current['leases']['task_agent']
    assert (journal/'release-intent.json').is_file()


def test_acquisition_crash_before_coordinator_receipt_is_exactly_recovered(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    original=p.lifecycle.lease_operation;armed=[True]
    def lease(*a,**k):
        value=original(*a,**k)
        if k['operation']=='acquire' and armed[0]:armed[0]=False;raise RuntimeError('after atomic acquire')
        return value
    monkeypatch.setattr(p.lifecycle,'lease_operation',lease)
    with pytest.raises(RuntimeError):p.execute(bound)
    token=read(record(path))['leases']['publication']['token']
    assert read(p.execute(bound))['status']=='PROMOTED'
    acquire=[e for e in read(record(path))['handoffs'] if e.get('operation')=='acquire' and e.get('lease_kind')=='publication']
    assert len(acquire)==1 and acquire[0]['after']['token']==token


def test_expired_task_lease_is_never_replaced_or_renewed(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(p.time,'time_ns',lambda:200)
    with pytest.raises(ValueError,match='live lease'):p.execute(bound)
    assert not calls
    events=read(record(path))['handoffs']
    assert not any(e.get('operation') in {'renew','replace_expired'} for e in events)


def test_guard_records_failure_without_terminal_source_claim(tmp_path,monkeypatch):
    r,bound,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    fresh=tmp_path/'guarded'
    observed=[]
    def guard(argv,**kwargs):
        observed.append((argv,kwargs))
        assert len(kwargs['guardian_lock_fds'])==2
        assert kwargs['timeout_seconds']==900 and kwargs['memory_limit_bytes']==8*1024**3
        kwargs['stdout'].write(b'synthetic interrupted child\n')
        return 9
    monkeypatch.setattr(p.guard,'_run_in_process_group',guard)
    output=read(p.run(r,fresh))
    assert output['status']=='RECONCILIATION_REQUIRED' and output['automatic_retry'] is False
    assert output['source_invalidity_claimed'] is False and len(observed)==1
    with pytest.raises(ValueError,match='explicit exact resume'):p.run(r,fresh)


def test_closed_request_encoding_rejects_unrecognized_fields(tmp_path,monkeypatch):
    r,*_=setup(tmp_path,monkeypatch)
    assert p.decode(p.encode(r))==r
    value=p.encode(r);value['skip_readiness']=True
    with pytest.raises(ValueError,match='closed publication'):p.decode(value)


@pytest.mark.parametrize('change',['none','overlap','same_locks','relative','symlink'])
def test_actual_path_policy_is_closed_and_disjoint(tmp_path,monkeypatch,change):
    r,*_=setup(tmp_path,monkeypatch)
    stage=tmp_path/'separate-stage';stage.mkdir()
    b=copy.deepcopy(r.selection.content.bindings)
    b['source_generation']['path']=str(tmp_path/'source-root/generations'/('a'*64))
    b['segmentation_generation']=dict(path=str(tmp_path/'seg-root/generations'/('a'*64)),id='a'*64,manifest_sha256='b'*64)
    r=replace(r,selection=replace(r.selection,content=replace(r.selection.content,stage=stage,bindings=b)))
    if change=='overlap':r=replace(r,output=stage/'output')
    elif change=='same_locks':r=replace(r,slot_lock=r.queue_lock)
    elif change=='relative':r=replace(r,output=Path('relative'))
    elif change=='symlink':
        alias=tmp_path/'aliased';alias.symlink_to(stage,target_is_directory=True)
        r=replace(r,output=alias/'output')
    if change=='none':assert REAL_PATHS(r,tmp_path/'fresh-journal')[0]==r.tracker_root/'0245.json'
    else:
        with pytest.raises(ValueError):REAL_PATHS(r,tmp_path/'fresh-journal')
