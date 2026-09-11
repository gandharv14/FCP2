"""Hermetic contract/transaction tests; owning model path checked separately on retained0042."""
from pathlib import Path
import importlib.util
from types import SimpleNamespace as NS
from contextlib import contextmanager
import copy
import pytest
from test_harbor_admission import setup
from scripts import harbor_dialogue_terminal as T
P=Path(T.__file__)

@pytest.mark.parametrize('change',['ordinal','limit','bool','structural','kind','scope','original'])
def test_budget_rejects_nonsecond_semantic(tmp_path,change):
    p=NS(ordinal=2,limit=2,scope=NS(workbook_id='0001',original_sha256='a'*64,lane=T.P.DIALOGUE,budget_id='dialogue-review'))
    c=dict(kind='dialogue-review');cfile=tmp_path/'context.json'
    if change=='ordinal':p.ordinal=1
    if change=='limit':p.limit=3
    if change=='bool':p.ordinal=True
    if change=='structural':c['structural_round']=2
    if change=='kind':c['kind']='dialogue-writer'
    if change=='scope':p.scope.budget_id='new-budget'
    if change=='original':p.scope.original_sha256='b'*64
    binding=T.keep(cfile,c)
    with pytest.raises(ValueError):T._budget(NS(prepared=p,context=binding),'dialogue-review','0001','a'*64)

def test_budget_accepts_exact(tmp_path):
    p=NS(ordinal=2,limit=2,scope=NS(workbook_id='0001',original_sha256='a'*64,lane=T.P.DIALOGUE,budget_id='dialogue-review'))
    T._budget(NS(prepared=p,context=T.keep(tmp_path/'c.json',dict(kind='dialogue-review'))),'dialogue-review','0001','a'*64)

@pytest.mark.parametrize('change',['passed','round','mechanics','faults','publication'])
def test_verdict_never_turns_success_or_missingreview_into_failure(change):
    r=dict(round=2,passed=False,publication_ready=False,review_faults=['contradiction'],draft_report={'passed':True})
    if change=='passed':r['passed']=True
    if change=='round':r['round']=1
    if change=='mechanics':r['draft_report']['passed']=False
    if change=='faults':r['review_faults']=[]
    if change=='publication':r['publication_ready']=True
    with pytest.raises(ValueError):T._verdict(r)

@pytest.fixture
def transaction(setup,monkeypatch):
    admission,policy,_,_,tmp=setup;created=admission.create('0001');path=policy.tracker_root/'0001.json'
    authority=T.L.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    acquired=T.L.lease_operation(path,authority=authority,operation='acquire',lease_kind='task_agent',owner='fixture',scope='workbook:0001',duration_ns=10**12,expected_revision=0,expected_sha256=created['sha256'])
    b=acquired['tracker'];lease=T.Lease('fixture',b['leases']['task_agent']['token'],b['revision'],T.record(path)['sha256'])
    w=NS(context=T.N.RootContext(policy.tracker_root));s=NS(content=NS(workbook='0001'))
    monkeypatch.setattr(T.I,'worker_encode',lambda w:{'fixture':True});monkeypatch.setattr(T.I,'worker_decode',lambda v:w)
    monkeypatch.setattr(T.codec,'encode',lambda s:{'workbook':'0001'});monkeypatch.setattr(T.codec,'decode',lambda v:s)
    monkeypatch.setattr(T.W,'code',lambda w:{str(P.resolve()):T.record(P)['sha256']})
    def active(w,s,l):
        facade=T.N.LaneNotes(w.context);facade.require_synchronized('0001');raw=path.read_bytes();before=T.read(T.record(path))
        facade._active(before,raw,l.revision,l.sha256,l.owner,l.token,'workbook:0001')
        return facade,before,T.record(path)
    monkeypatch.setattr(T.W,'active',active)
    @contextmanager
    def locked(*a):yield None
    monkeypatch.setattr(T.parallel,'lane_lock',locked)
    source=T.keep(tmp/'retained-response.json',{'review':'actual synthetic rejection'})
    def inspect(w,s,b,blocked,diagnostic):
        T.L.checked_record(source)
        return dict(lane=T.P.DIALOGUE,gate='dialogue',passed=False,bindings={},evidence=[b,source],classification=T.CLASSIFICATION,failure_class='dialogue',terminal=True,fixability='exhausted',diagnostic_sha256=diagnostic['sha256'],attempt=2)
    monkeypatch.setattr(T,'inspect',inspect)
    blocked=T.keep(tmp/'blocked.json',{'status':'fixture'});diagnostic=T.keep(tmp/'diagnostic.json',{'source_invalidity':False})
    tx=T.prepare(w,s,lease,blocked=blocked,diagnostic=diagnostic,authority=authority,contract={},activation={},transaction_name='terminal-01')
    return NS(tx=tx,path=path,before=path.read_bytes(),authority=authority,source=source,worker=w,selected=s,lease=lease,blocked=blocked,diagnostic=diagnostic)

def commit(t,**kw):return T.reconcile(t.tx,authority=t.authority,contract={},activation={},**kw)

def test_prepare_no_mutation_then_commit_terminal_exact_budget(transaction):
    t=transaction;assert t.path.read_bytes()==t.before
    result=commit(t);after=T.read(result['tracker'])
    assert after['execution_state']=='STOPPED' and after['lane_state'][T.P.DIALOGUE]['attempt']==2
    assert after['promotion_failure']['class']=='dialogue' and after['promotion_failure']['subclass']==T.CLASSIFICATION
    assert after['bindings']==T.S.strict_json(t.before)['bindings']
    assert after['handoffs']==T.S.strict_json(t.before)['handoffs']
    assert commit(t)==result
    T.N.LaneNotes(t.worker.context).require_synchronized('0001')

@pytest.mark.parametrize('point',['notes','tracker'])
def test_interrupted_pair_reconciliation(transaction,point):
    t=transaction
    def fault(p):
        if p==point:raise RuntimeError('fixture crash')
    with pytest.raises(RuntimeError):commit(t,_fault=fault)
    with pytest.raises(ValueError):T.N.LaneNotes(t.worker.context).require_synchronized('0001')
    assert commit(t)['status']=='STOPPED'
    T.N.LaneNotes(t.worker.context).require_synchronized('0001')

@pytest.mark.parametrize('change',['response','after','notes','foreign_cas','unauthorized'])
def test_changed_evidence_or_authority_rejects(transaction,change):
    t=transaction;d=T.read(t.tx)
    if change=='response':Path(t.source['path']).write_text('altered')
    if change in ('after','notes'):Path(d[change]['path']).write_text('altered')
    if change=='foreign_cas':t.path.write_bytes(t.before+b' ')
    if change=='unauthorized':t.authority=T.L.RootAuthority('harbor-orchestrator',10**12,lambda *a:False)
    current=t.path.read_bytes()
    with pytest.raises((ValueError,KeyError)):commit(t)
    assert t.path.read_bytes()==current

def test_repeated_preparation_never_replaces(transaction):
    t=transaction
    with pytest.raises(ValueError,match='pending notes transaction|retained preparation'):
        T.prepare(t.worker,t.selected,t.lease,blocked=t.blocked,diagnostic=t.diagnostic,authority=t.authority,contract={},activation={},transaction_name='terminal-01')


def test_lane_policy_cannot_be_swapped(transaction):
    with pytest.raises(ValueError,match='policy substitution'):
        T.reconcile(transaction.tx,authority=transaction.authority,contract={'different':True},activation={})

def test_hash_bound_context_and_archive_records_reject_tampering(tmp_path):
    archived=T.keep(tmp_path/'archive.json',{'retained':'exact'})
    assert T.records({'copies':[{'retained':archived}]})==[archived]
    Path(archived['path']).write_text('changed')
    with pytest.raises(ValueError):T.records({'copies':[{'retained':archived}]})


@pytest.mark.parametrize('notes_written',[False,True])
def test_expired_uncommitted_transaction_never_writes(transaction,monkeypatch,notes_written):
    t=transaction
    if notes_written:
        def interrupt(point):
            if point=='notes':raise RuntimeError('after notes, before CAS')
        with pytest.raises(RuntimeError):commit(t,_fault=interrupt)
    d=T.read(t.tx);note=Path(d['notes_path']);before_notes=note.read_bytes() if note.exists() else None
    before_tracker=t.path.read_bytes()
    deadline=T.read(d['before'])['leases']['task_agent']['expires_at']
    monkeypatch.setattr(T.N.time,'time_ns',lambda:deadline)
    writes=[]
    monkeypatch.setattr(T.N,'_replace',lambda *a:writes.append(a))
    with pytest.raises(ValueError,match='lease expired'):commit(t)
    assert writes==[] and t.path.read_bytes()==before_tracker
    assert (note.read_bytes() if note.exists() else None)==before_notes
    assert not (Path(t.tx['path']).parent/'complete.json').exists()


def test_committed_tracker_can_be_sealed_after_expiry(transaction,monkeypatch):
    t=transaction
    def interrupt(point):
        if point=='tracker':raise RuntimeError('CAS committed, completion missing')
    with pytest.raises(RuntimeError):commit(t,_fault=interrupt)
    d=T.read(t.tx);committed=t.path.read_bytes();note=Path(d['notes_path']);notes=note.read_bytes()
    assert committed==T.L.canonical(T.read(d['after']))
    deadline=T.read(d['before'])['leases']['task_agent']['expires_at']
    monkeypatch.setattr(T.N.time,'time_ns',lambda:deadline)
    result=commit(t)
    assert result['status']=='STOPPED' and t.path.read_bytes()==committed and note.read_bytes()==notes
    assert (Path(t.tx['path']).parent/'complete.json').is_file()
