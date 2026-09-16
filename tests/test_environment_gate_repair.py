"""Original snapshot/CAS mechanics; fixture gate results are not financial proof."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_harbor_ready_transitions import fixture
from scripts import harbor_campaign as C
from scripts.batch import harbor_environment_gate_repair as K,harbor_lane_notes as N,harbor_completed_lanes as L
from scripts.batch.harbor_lane_lifecycle import HarborLaneLifecycle
from scripts.batch.harbor_admission import record,read,require


@pytest.fixture
def repair_case(tmp_path,monkeypatch):
    _,path,start,selection,proof=fixture(tmp_path,monkeypatch)
    before,notes=N._failure_after(start['tracker'],dict(lane='harbor-normalization-engineer',gate='normalization_phase1',
        classification='owning_validator_rejected',diagnostic_sha256='c'*64,evidence=proof['evidence'],terminal=False),
        25,path.with_name('0245-agent-notes.md'))
    path.write_bytes(N.lifecycle.canonical(before));Path(before['agent_notes']['path']).write_bytes(notes)
    facade=HarborLaneLifecycle(N.RootContext(tmp_path));lease=before['leases']['task_agent']
    worker=SimpleNamespace(run=tmp_path/'environment',bootstrap=SimpleNamespace(workbook='0245',
        policy=SimpleNamespace(tracker_root=tmp_path,trusted_public_key=None)))
    descriptor=tmp_path/'descriptor.json';descriptor.write_text('{}')
    cp=tmp_path/'failed.json';cp.write_text(json.dumps(dict(status='OWNING_GATE_FAILED',step='normalization_phase1',
        descriptor=record(descriptor),lease=C.encode(C.bootstrap.Lease(lease['owner'],lease['token'],before['revision'],record(path)['sha256'])),selection={'original':'selection'})))
    c=SimpleNamespace(**C.__dict__);c.selected=lambda d:(worker,None,None)
    c.content_codec=SimpleNamespace(decode=lambda value:selection)
    c.worker=SimpleNamespace(_active=lambda w,l:(facade,read(record(path))),_context=lambda w:facade.context)
    c._lease=lambda w,l,d:l
    monkeypatch.setattr(L,'check_completed_gate',lambda *a:dict(passed=True,evidence=proof['evidence']))
    return c,path,record(cp),worker,selection,before


@pytest.mark.parametrize('fault',[None,'attempt','source','snapshot','lease','selection','failed-custody'])
def test_repair_uses_original_snapshots_and_preserves_consumption(repair_case,tmp_path,fault):
    c,path,cp,worker,selection,original=repair_case;receipt=K.prepare(c,cp,record(path),tmp_path/'repair',custody=lambda:None)
    v,after=K.validate(c,receipt)
    assert after['repairs']==original['repairs']
    assert [x['attempt'] for x in after['lane_state'].values()]==[x['attempt']for x in original['lane_state'].values()]
    assert after['promotion_failure'] is not None and v['models_dispatched']==0
    assert v['failed_tracker']['sha256']==C.decode(read(cp)['lease']).sha256
    if fault:
        changed=read(receipt)
        if fault=='attempt':
            state=read(changed['after_repair']);state['lane_state']['harbor-normalization-engineer']['attempt']=0
            p=tmp_path/'forged-after.json';p.write_bytes(N.lifecycle.canonical(state));changed['after_repair']=record(p)
        if fault=='source':changed['source']={'path':'foreign','sha256':'0'*64}
        if fault=='snapshot':changed['snapshot_transaction']=cp
        if fault=='lease':changed['lease']=C.encode(C.bootstrap.Lease('foreign','token',after['revision'],v['after_repair']['sha256']))
        if fault=='selection':changed['selection']={'foreign':'selection'}
        if fault=='failed-custody':
            state=read(changed['failed_tracker']);state['leases']['task_agent']['token']='foreign'
            p=tmp_path/'forged-failed.json';p.write_bytes(N.lifecycle.canonical(state));changed['failed_tracker']=record(p)
        p=tmp_path/'forged-receipt.json';p.write_text(json.dumps(changed))
        saved=path.read_bytes()
        with pytest.raises((ValueError,KeyError)):K.validate(c,record(p))
        assert path.read_bytes()==saved


active_state=None

def original_active(worker,selection,lease):
    before=active_state
    require(lease=='live','original live check')
    require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')
    require(selection=='actual','original evidence check')
    return None,before,None


@pytest.mark.parametrize('fault',[None,'lease','selection','failure','history','run'])
def test_instruction_scope_keeps_real_checks_and_exact_failed_history(tmp_path,monkeypatch,fault):
    global active_state
    base=dict(bindings={'original_raw_source':{'same':'source'}},failure_history=[{'event':'record'}],handoffs=[{'snapshot':'original'}],promotion_failure={'signature':'actual'})
    active_state=copy.deepcopy(base);run=tmp_path/'instruction';v=dict(source=base['bindings']['original_raw_source'],instruction_run=str(run))
    monkeypatch.setattr(K,'validate',lambda *a:(v,base));prior=lambda *a:None;operation=lambda *a,**k:None
    c=SimpleNamespace(_prior=prior);w=SimpleNamespace(active=original_active,_operation=operation)
    worker=SimpleNamespace(run=run)
    if fault=='failure':active_state['promotion_failure']={'signature':'foreign'}
    if fault=='history':active_state['handoffs']=[]
    if fault=='run':worker.run=tmp_path/'foreign'
    with K.scope(c,None,w,{}):
        args=(worker,'foreign'if fault=='selection'else'actual','expired'if fault=='lease'else'live')
        if fault:
            with pytest.raises(ValueError):w.active(*args)
        else:assert w.active(*args)[1]==base
    assert w.active is original_active and w._operation is operation and c._prior is prior

@pytest.mark.parametrize('foreign',[False,True])
def test_renewal_preserves_other_lease_evidence(repair_case,tmp_path,foreign):
    from dataclasses import replace
    from test_harbor_tracker_lifecycle import authority
    c,path,cp,worker,selection,original=repair_case
    def renew(w,lease,directory):
        result=c.lifecycle.lease_operation(path,authority=authority(),operation='renew',lease_kind='task_agent',
            owner=lease.owner,scope='workbook:0245',token=lease.token,duration_ns=1000,
            expected_revision=lease.revision,expected_sha256=lease.sha256,clock=lambda:26)
        new=replace(lease,revision=result['revision'],sha256=result['sha256'])
        c.keep(directory/'lease-renewal.json',dict(previous=c.encode(lease),lease=c.encode(new),tracker=record(path)))
        return new
    c._lease=renew
    receipt=K.prepare(c,cp,record(path),tmp_path/'repair',custody=lambda:None)
    if foreign:
        v=read(receipt);before=read(v['before_snapshot']);before['leases']['normalization_agent']=copy.deepcopy(before['leases']['task_agent'])
        changed=tmp_path/'foreign-before.json';changed.write_bytes(N.lifecycle.canonical(before));v['before_snapshot']=record(changed)
        renewal=read(v['renewal']);renewal['tracker']=record(changed);p=tmp_path/'renewal.json';p.write_text(json.dumps(renewal));v['renewal']=record(p)
        p=tmp_path/'receipt.json';p.write_text(json.dumps(v))
        with pytest.raises((ValueError,KeyError)):K.validate(c,record(p))
    else:K.validate(c,receipt)


def test_first_expiry_repair_preserves_real_replacement_and_refuses_replay(repair_case,tmp_path,monkeypatch):
    """Real lease CAS and repair transactions; only configured recipe I/O is a fixture."""
    from scripts.batch import harbor_lease_takeover as T
    c,path,cp,worker,selection,original=repair_case
    lock=tmp_path/'slot.lock';lock.touch();worker.run.mkdir()
    chosen=T.Selection(tmp_path,'0245',record(path),'environment',cp,cp,'repair-owner',1000)
    monkeypatch.setattr(T,'_layout',lambda s:(path,tmp_path,worker.run,lock))
    def recipe(s,*,historical=None):
        before=read(s.tracker)if historical is None else historical
        assert before['workbook_id']=='0245'and before['bindings']['original_raw_source']==original['bindings']['original_raw_source']
        return before,before['leases']['task_agent'],worker.run,[cp]
    monkeypatch.setattr(T,'_recipe',recipe);monkeypatch.setattr(K.time,'time_ns',lambda:130)
    c.worker.HarborLaneLifecycle=HarborLaneLifecycle
    selected=SimpleNamespace(**T.__dict__);selected.replace_expired=lambda s,d:T.replace_expired(s,d,clock=lambda:130)
    declaration=tmp_path/'policy.json';declaration.write_text('{}')
    receipt=K.prepare(c,cp,chosen.tracker,tmp_path/'repair',custody=lambda:None,
        takeover=(selected,chosen,tmp_path/'lease-takeovers/one',record(declaration)))
    v,after=K.validate(c,receipt)
    assert T.life.validate_lifecycle(after)==1 and v['takeover'] is not None and v['renewal'] is None
    assert after['repairs']==original['repairs']
    assert [r['attempt']for r in after['lane_state'].values()]==[r['attempt']for r in original['lane_state'].values()]
    assert after['leases']['task_agent']['token']!=original['leases']['task_agent']['token']
    with pytest.raises(ValueError):K.prepare(c,cp,chosen.tracker,tmp_path/'different',custody=lambda:None,
        takeover=(selected,chosen,tmp_path/'lease-takeovers/two',record(declaration)))
    assert not(tmp_path/'different').exists()
