from dataclasses import replace,asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_lease_renewal as R,harbor_instruction_worker as w
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import record,read
from scripts.batch.harbor_workbook_bootstrap import Lease
from test_harbor_tracker_lifecycle import created,acquire
from test_instruction_worker import case,instruction_setup

@pytest.fixture
def boundary(case,tmp_path,monkeypatch):
    worker,s,_,*_=case
    root=tmp_path/'real-tracker';root.mkdir();path,start=created(root);a=acquire(path,start)
    worker=replace(worker,context=replace(worker.context,tracker_root=root))
    old=a['tracker']['leases']['task_agent'];lease=Lease(old['owner'],old['token'],a['revision'],a['sha256'])
    monkeypatch.setattr(w,'active',lambda *args:(None,read(record(path)),record(path)))
    monkeypatch.setattr(w,'_operation',lambda worker,s,l,*args,**kw:(s,l,None))
    result=w.step(worker,s,lease)
    monkeypatch.setattr(R,'_prefix',lambda *args:[])
    # Financial prefix composition is covered by owning suites; this fixture
    # uses the real lifecycle CAS and real immutable worker checkpoint.
    codefile=tmp_path/'tiny-code.py';codefile.write_text('original\n')
    oldsnapshot=w.keep(tmp_path/'snapshot.json',{'root':str(tmp_path),'files':[]})
    monkeypatch.setattr(R,'changes',lambda *args:[])
    monkeypatch.setattr(R,'historical_code',lambda *args:None)
    return worker,s,lease,result.evidence,oldsnapshot,path

def authorize(b):
    worker,s,l,cp,snap,path=b
    return R.authorize(worker,s,l,cp,snap,duration_ns=200,clock=lambda:30)

def test_renewal_preserves_checkpoint_and_executes_only_next_step(boundary):
    worker,s,old,cp,snap,path=boundary;raw=Path(cp['path']).read_bytes();before=read(record(path))
    complete=authorize(boundary);new=Lease(**read(complete)['lease']);renewed=replace(worker,lease_renewal=complete)
    permit=R.validate(renewed,read(record(path)))
    assert Path(cp['path']).read_bytes()==raw and new.owner==old.owner and new.token==old.token
    assert read(record(path))['lane_state']==before['lane_state'] and permit['semantic_budget_reset'] is False
    result=w.step(renewed,s,new,checkpoint=complete)
    assert read(result.evidence)['step']=='disclosure-review'
    assert R.checkpoint_predecessor(renewed,complete)==cp
    with pytest.raises(ValueError):authorize(boundary)


def test_pending_model_after_actual_renewal_keeps_renewed_predecessor(boundary,monkeypatch):
    worker,s,old,cp,snap,path=boundary
    complete=authorize(boundary);lease=Lease(**read(complete)['lease']);worker=replace(worker,lease_renewal=complete)
    original=Path(cp['path']).read_bytes();tracker=path.read_bytes();calls=[]
    request=worker.run/'immutable-review-request.json'
    def operation(worker,selection,current,phase,directory,**options):
        calls.append((phase,options['models'],current))
        if not request.exists():request.write_bytes(b'original prepared request\n')
        assert request.read_bytes()==b'original prepared request\n'
        return selection,current,None if options['models'] else 'MODEL_REQUIRED'
    monkeypatch.setattr(w,'_operation',operation)
    first=w.step(worker,s,lease,checkpoint=complete,models=False)
    second=w.step(worker,first.selection,first.lease,checkpoint=first.evidence,models=False)
    done=w.step(worker,second.selection,second.lease,checkpoint=second.evidence,models=True)
    assert first.status==second.status=='MODEL_REQUIRED' and done.status=='STEP_COMPLETE'
    assert calls==[('disclosure-review',False,lease),('disclosure-review',False,lease),('disclosure-review',True,lease)]
    assert Path(cp['path']).read_bytes()==original and path.read_bytes()==tracker
    assert read(first.evidence)['intent']==read(second.evidence)['intent']==read(done.evidence)['intent']


@pytest.mark.parametrize('fault',['old-lease','foreign-predecessor','pending-selection','pending-lease'])
def test_pending_renewal_cannot_change_custody_or_predecessor(boundary,monkeypatch,fault):
    worker,s,old,cp,snap,path=boundary
    complete=authorize(boundary);lease=Lease(**read(complete)['lease']);worker=replace(worker,lease_renewal=complete)
    monkeypatch.setattr(w,'_operation',lambda worker,s,l,*args,**kw:(s,l,'MODEL_REQUIRED'))
    pending=w.step(worker,s,lease,checkpoint=complete,models=False);v=read(pending.evidence)
    if fault=='foreign-predecessor':
        intent=read(v['intent']);intent['previous']=cp
        Path(v['intent']['path']).write_bytes(L.canonical(intent));v['intent']=record(v['intent']['path'])
    elif fault=='pending-selection':v['selection']['unbound']=True
    elif fault=='pending-lease':v['lease']=asdict(old)
    if fault!='old-lease':Path(pending.evidence['path']).write_bytes(L.canonical(v))
    def forbidden(*args,**kw):raise AssertionError('invalid pending custody reached an operation')
    monkeypatch.setattr(w,'_operation',forbidden)
    with pytest.raises(ValueError):w.step(worker,s,old if fault=='old-lease' else lease,checkpoint=record(pending.evidence['path']),models=True)

@pytest.mark.parametrize('fault',['owner','token','selection','pending','uncertain','checkpoint','expired'])
def test_invalid_boundary_cannot_renew(boundary,fault):
    worker,s,l,cp,snap,path=boundary;before=path.read_bytes()
    if fault in ('owner','token'):l=replace(l,**{fault:'wrong'})
    elif fault=='selection':s=replace(s,content=replace(s.content,stage_sha256='0'*64))
    elif fault in ('pending','uncertain'):
        p=w.step_directory(worker,'disclosure-review');p.mkdir();(p/('request.json' if fault=='uncertain' else 'pending-0000.json')).write_text('{}')
    elif fault=='checkpoint':cp={**cp,'sha256':'0'*64}
    with pytest.raises(ValueError):R.authorize(worker,s,l,cp,snap,duration_ns=200,clock=lambda:120 if fault=='expired' else 30)
    assert path.read_bytes()==before and not (worker.run/'lease-renewals').exists()

@pytest.mark.parametrize('fault',['checkpoint','before','after','code','selection','duration'])
def test_tampered_receipt_or_retained_evidence_rejects(boundary,fault):
    worker,s,l,cp,snap,path=boundary;complete=authorize(boundary);v=read(complete);permit=read(v['permit'])
    if fault=='checkpoint':Path(cp['path']).write_text('{}')
    elif fault in ('before','after'):Path((permit['tracker'] if fault=='before' else v['tracker'])['path']).write_text('{}')
    else:
        if fault=='code':permit['current_code']={}
        elif fault=='selection':permit['selection']={}
        else:permit['duration_ns']=201
        # Rehashing the changed receipt does not confer authority.
        p=Path(v['permit']['path']);p.write_bytes(L.canonical(permit));v['permit']=record(p)
        p=Path(complete['path']);p.write_bytes(L.canonical(v));complete=record(p)
    with pytest.raises(ValueError):R.validate(replace(worker,lease_renewal=complete),read(record(path)))

def test_worker_codec_keeps_optional_renewal_closed(boundary):
    from scripts import harbor_instruction_campaign as C
    worker,*_=boundary
    assert 'lease_renewal' not in C.worker_encode(worker)
    complete=authorize(boundary);other=replace(worker,lease_renewal=complete)
    assert C.worker_decode(C.worker_encode(other))==other
    value=C.worker_encode(worker);value['lease_renewal']={'unbound':True}
    with pytest.raises(ValueError):C.worker_decode(value)

def test_interrupted_after_cas_recovers_evidence_without_second_renewal(boundary,monkeypatch):
    worker,s,l,cp,snap,path=boundary;keep=w.keep
    def fail(path,value):
        if path.name=='complete.json' and 'lease-renewals' in path.parts:raise KeyboardInterrupt('after CAS')
        return keep(path,value)
    monkeypatch.setattr(w,'keep',fail)
    with pytest.raises(KeyboardInterrupt):authorize(boundary)
    after=path.read_bytes();permit=record(worker.run/'lease-renewals'/cp['sha256']/'permit.json')
    monkeypatch.setattr(w,'keep',keep)
    complete=R.recover(worker,permit)
    assert path.read_bytes()==after and R.recover(worker,permit)==complete
    with pytest.raises(ValueError):authorize(boundary)

def test_next_completed_boundary_retains_and_validates_previous_renewal(boundary):
    worker,s,l,cp,snap,path=boundary;first=authorize(boundary)
    worker=replace(worker,lease_renewal=first);lease=Lease(**read(first)['lease'])
    result=w.step(worker,s,lease,checkpoint=first)
    second=R.authorize(worker,result.selection,result.lease,result.evidence,snap,duration_ns=300,clock=lambda:40)
    current=replace(worker,lease_renewal=second)
    assert R.validate(current,read(record(path)))['prior_continuations']['lease_renewal']==first
    assert R.checkpoint_predecessor(current,first)==cp
    Path(first['path']).write_text('{}')
    with pytest.raises(ValueError):R.validate(current,read(record(path)))

def test_code_successor_only_accepts_explicit_renewal_files(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    name='scripts/batch/harbor_instruction_worker.py'
    for root,body in ((old,'old'),(new,'new')):
        p=root/name;p.parent.mkdir(parents=True);p.write_text(body)
    snap=w.keep(tmp_path/'old.json',{'root':str(old),'files':[{'relative':name,'sha256':record(old/name)['sha256']}]})
    assert R.changes(snap,new)==[name]
    (new/'financial.py').write_text('unrelated')
    with pytest.raises(ValueError):R.changes(snap,new)
    (new/'financial.py').unlink();(old/name).write_text('tampered')
    with pytest.raises(ValueError):R.changes(snap,new)


def test_prefix_rejection_preserves_tracker_and_creates_no_reservation(boundary,monkeypatch):
    worker,_,_,_,_,path=boundary;before=path.read_bytes()
    def reject(*args):raise ValueError('owning reviewer rejected')
    monkeypatch.setattr(R,'_prefix',reject)
    with pytest.raises(ValueError,match='reviewer rejected'):authorize(boundary)
    assert path.read_bytes()==before and not (worker.run/'lease-renewals').exists()


def test_added_unbound_continuation_is_not_authorized_by_renewal(boundary):
    worker,*_=boundary;complete=authorize(boundary)
    foreign=w.keep(worker.run/'foreign.json',{'schema_version':'wrong'})
    with pytest.raises((ValueError,KeyError)):R.validate(replace(worker,lease_renewal=complete,retry=foreign),read(record(boundary[-1])))

def test_later_nat_cas_must_retain_exact_renewal_receipt(boundary):
    from scripts.batch import harbor_nat_retry as N,harbor_tracker as S
    worker,s,l,cp,snap,path=boundary;renewal=authorize(boundary)
    worker=replace(worker,lease_renewal=renewal);before=read(record(path));lease=Lease(**read(renewal)['lease'])
    old=w.snapshot(worker.run/'retry-before.json',record(path))
    permit=w.keep(worker.run/'synthetic-nat-permit.json',{'schema_version':N.SCHEMA,'worker_run':str(worker.run),'before':old,'lease':asdict(lease),'code':w.code(worker),'prior_continuations':N._prior_continuations(worker)})
    def mutate(view):view.update(N._project(view,permit))
    result=S.cas_mutate(path,expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token,scope='workbook:0245',mutate=mutate,allowed_fields={'handoffs','lane_state'},allowed_lanes={w.producer.NAT},now_ns=31)
    after=w.snapshot(worker.run/'retry-after.json',record(path));newlease=replace(lease,revision=result['revision'],sha256=result['sha256'])
    complete=w.keep(worker.run/'synthetic-nat-complete.json',{'schema_version':N.SCHEMA,'permit':permit,'after':after,'lease':asdict(newlease)})
    changed=replace(worker,retry=complete)
    assert R.validate(changed,read(record(path)))['checkpoint']==cp
    # Rehashing a detached downstream permit still lacks the recorded CAS.
    value=read(permit);value['prior_continuations']={};p=Path(permit['path']);p.write_bytes(L.canonical(value))
    with pytest.raises(ValueError):R.validate(changed,read(record(path)))

@pytest.mark.parametrize('phase',['dialogue-commit','enqueue'])
def test_final_runtime_boundaries_require_full_current_readiness(boundary,monkeypatch,phase):
    from scripts.batch import harbor_instruction_repair
    worker,s,_,_,_,path=boundary
    monkeypatch.setattr(harbor_instruction_repair,'prefix',lambda *args,**kwargs:[])
    called=[]
    monkeypatch.setattr(w,'complete_readiness',lambda *args,**kwargs:(called.append(phase) or {'publication_ready':False}))
    # Use the real prefix function despite the transaction fixture's gate stub.
    import importlib.util
    spec=importlib.util.spec_from_file_location('renewal_prefix_test',R.__file__);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(ValueError,match='dialogue readiness'):module._prefix(read(record(path)),s,worker,phase)
    assert called==[phase]


def test_historical_snapshot_must_be_actual_completed_intent_code(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    (old/'worker.py').write_text('old');(new/'worker.py').write_text('new')
    intent=w.keep(tmp_path/'intent.json',{'code':{str(old/'worker.py'):record(old/'worker.py')['sha256']}})
    complete=w.keep(tmp_path/'complete.json',{'intent':intent})
    def snapshot(root):return w.keep(tmp_path/(root.name+'.json'),{'root':str(root),'files':[{'relative':'worker.py','sha256':record(root/'worker.py')['sha256']}]})
    R.historical_code(snapshot(old),complete)
    with pytest.raises(ValueError,match='completed intent root'):R.historical_code(snapshot(new),complete)


def test_known_disclosure_command_repair_keeps_its_permit_across_real_renewal(boundary,monkeypatch):
    from scripts.batch import harbor_disclosure_command_repair as K
    worker,s,old,cp,snap,path=boundary
    permit=w.keep(worker.run/'command-repair/permit.json',dict(schema_version=K.SCHEMA,phase='disclosure-build'))
    selected=replace(worker,code_repair=permit);original_step=w.step_directory;seen=[]
    # Preserve the existing actual completed checkpoint in this fixture; the
    # owning adapter independently binds its relocated completed command step.
    monkeypatch.setattr(w,'step_directory',lambda worker,phase:original_step(replace(worker,code_repair=None),phase))
    monkeypatch.setattr(K,'validate',lambda candidate,before:seen.append((candidate.code_repair,before['revision'])))
    before=path.read_bytes();complete=R.authorize(selected,s,old,cp,snap,duration_ns=200,clock=lambda:30)
    current=read(record(path));renewed=replace(selected,lease_renewal=complete);value=R.validate(renewed,current)
    assert seen==[(permit,old.revision)]and current['revision']==old.revision+1
    assert value['prior_continuations']['code_repair']==permit and value['checkpoint']==cp
    assert read(complete)['lease']['owner']==old.owner and read(complete)['lease']['token']==old.token
    assert value['semantic_budget_reset']is False


def test_unrecognized_code_repair_cannot_reach_renewal_cas(boundary,monkeypatch):
    worker,s,old,cp,snap,path=boundary
    permit=w.keep(worker.run/'foreign-repair.json',dict(schema_version='unrecognized',phase='other'))
    selected=replace(worker,code_repair=permit);raw=path.read_bytes()
    with pytest.raises(ValueError,match='unsupported code repair renewal'):R.authorize(selected,s,old,cp,snap,duration_ns=200,clock=lambda:30)
    assert path.read_bytes()==raw and not(worker.run/'lease-renewals').exists()

@pytest.mark.parametrize('fault',[None,'foreign','changed','missing','wrong-snapshot'])
def test_completed_command_repair_requires_exact_two_code_inventories(tmp_path,monkeypatch,fault):
    from scripts.batch import harbor_disclosure_command_repair as K
    roots=[tmp_path/'old',tmp_path/'selected'];rows=[];code={}
    for root in roots:
        root.mkdir();f=root/'module.py';f.write_text(root.name+'\n');code[str(f)]=record(f)['sha256'];rows.append(w.keep(tmp_path/(root.name+'.json'),dict(root=str(root),files=[dict(relative='module.py',sha256=record(f)['sha256'])])))
    profile=w.keep(tmp_path/'profile.json',dict(workbook='0280',old_snapshot=rows[0],snapshot=rows[1]));permit=w.keep(tmp_path/'permit.json',dict(profile=profile,current_code=code))
    (tmp_path/'0280.json').write_text('{}');worker=SimpleNamespace(code_repair=permit,context=SimpleNamespace(tracker_root=tmp_path))
    monkeypatch.setattr(K,'selected',lambda worker:True);monkeypatch.setattr(K,'validate',lambda worker,before:read(permit))
    supplied=dict(code)
    if fault=='foreign':supplied[str(tmp_path/'foreign.py')]='a'*64
    if fault=='missing':supplied.pop(next(iter(supplied)))
    if fault=='changed':(roots[0]/'module.py').write_text('changed')
    intent=w.keep(tmp_path/'intent.json',dict(code=supplied));checkpoint=w.keep(tmp_path/'complete.json',dict(intent=intent));snapshot=rows[0]if fault=='wrong-snapshot'else rows[1]
    if fault is None:R.command_repair_history(worker,snapshot,checkpoint)
    else:
        with pytest.raises(ValueError):R.command_repair_history(worker,snapshot,checkpoint)
