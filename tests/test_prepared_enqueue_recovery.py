from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
import pytest
from scripts.batch import harbor_prepared_enqueue_recovery as H
from scripts import harbor_publish as P
from test_harbor_ready_transitions import fixture,kw

@pytest.fixture
def prepared(tmp_path,monkeypatch):
    root=tmp_path/'trackers';root.mkdir();facade,path,start,selection,proof=fixture(root,monkeypatch)
    def stop(point):
        if point=='prepared':raise RuntimeError('retained prepared failure')
    with pytest.raises(RuntimeError):facade.transition('0245','enqueue',selection,**kw(start),_fault=stop)
    tx=next((root/'0245-notes-transactions').glob('*/transaction.json'))
    run=tmp_path/'worker';run.mkdir();initial=P._keep(run/'initial.json',{})
    before=P.read(P.record(path));lease0=before['leases']['task_agent'];lease=P.Lease(lease0['owner'],lease0['token'],before['revision'],P.schema.sha(P.lifecycle.canonical(before)))
    code=P.instruction.code(type('Installed',(),{'root':P.ROOT})());commit=run/'dialogue-commit';commit.mkdir()
    prev=P._keep(commit/'complete.json',dict(step='dialogue-commit',status='completed',selection=P.codec.encode(selection),lease=asdict(lease)))
    failed=run/'enqueue';failed.mkdir()
    intent=P._keep(failed/'intent.json',dict(step='enqueue',previous=prev,initial=initial,code=code,selection=P.codec.encode(selection),lease=asdict(lease)))
    exc=P._keep(failed/'exception.json',dict(automatic_replay=False,type='AdmissionError'))
    ready=P._keep(failed/'readiness.json',dict(publication_ready=True,blockers=[],mutation_performed=False))
    W=SimpleNamespace(read=P.read,record=P.record,require=P.require,keep=P._keep,code=lambda w:code,codec=P.codec,Lease=P.Lease)
    worker=SimpleNamespace(run=run,context=facade.context)
    args=dict(transaction=P.record(tx),failed_intent=intent,failed_exception=exc,failed_readiness=ready,predecessor=prev,directory=tmp_path/'recovery')
    return W,worker,selection,lease,args,path

def test_actual_original_recover_and_checkpoint_preserve_failed_journal(prepared):
    W,w,s,l,args,path=prepared;old={k:Path(args[k]['path']).read_bytes()for k in ('failed_intent','failed_exception','failed_readiness','predecessor')}
    lease,checkpoint,receipt=H.recover(W,w,s,l,**args)
    current=P.read(P.record(path));assert current['execution_state']=='publication_queued' and lease.revision==l.revision+1
    assert P.read(receipt)['status']=='PUBLICATION_READY' and P.read(checkpoint)['intent']==args['failed_intent']
    assert all(Path(args[k]['path']).read_bytes()==v for k,v in old.items())
    request=P.Request(s,lease,checkpoint,path.parent,path.parent/'pub',path.parent/'output',path.parent/'q',path.parent/'slot')
    P._checkpoint(request)
    with pytest.raises(ValueError):H.recover(W,w,s,l,**args)

@pytest.mark.parametrize('change',['foreign_transaction','selection','lease','prior','readiness','old_complete'])
def test_wrong_boundary_cannot_recover(prepared,change):
    W,w,s,l,a,path=prepared;before=path.read_bytes()
    key={'foreign_transaction':'transaction','selection':'failed_intent','lease':'failed_intent','prior':'predecessor','readiness':'failed_readiness'}.get(change)
    if key:
        v=P.read(a[key])
        if change=='foreign_transaction':v['action']='promoted'
        elif change=='selection':v['selection']=P.codec.encode(None)
        elif change=='lease':v['lease']['revision']+=1
        elif change=='prior':v['status']='failed'
        else:v['publication_ready']=False
        p=Path(a[key]['path']);p.write_bytes(P.lifecycle.canonical(v));a[key]=P.record(p)
    else:P._keep(Path(a['failed_intent']['path']).parent/'complete.json',{})
    with pytest.raises(ValueError):H.recover(W,w,s,l,**a)
    assert path.read_bytes()==before and not a['directory'].exists()

def test_lost_reply_after_original_cas_never_automatically_repeats(prepared,monkeypatch):
    W,w,s,l,a,path=prepared;old=W.keep
    def fail(p,v):
        if Path(p)==Path(a['failed_intent']['path']).parent/'complete.json':raise RuntimeError('lost after CAS')
        return old(p,v)
    W.keep=fail
    with pytest.raises(RuntimeError):H.recover(W,w,s,l,**a)
    after=path.read_bytes();assert P.read(P.record(path))['execution_state']=='publication_queued'
    with pytest.raises(ValueError,match='already attempted'):H.recover(W,w,s,l,**a)
    assert path.read_bytes()==after
