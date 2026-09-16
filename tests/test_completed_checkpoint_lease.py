from dataclasses import asdict,replace
from pathlib import Path
import pytest
from scripts.fleet import completed_checkpoint_lease as K,completed_checkpoint_takeover as B,continuation_lease as L
from scripts import harbor_instruction_campaign as I
from scripts.batch import harbor_instruction_worker as W,harbor_dialogue_semantic_retry as S,harbor_tracker_lifecycle as life
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup


def case(failed,tmp_path,monkeypatch):
    worker,selection,lease,blocked,task,_,_,calls=failed
    worker,selection,lease,permit=S.authorize(worker,selection,lease,blocked=blocked)
    descriptor=W.keep(tmp_path/'descriptor.json',dict(worker=I.worker_encode(worker),origin=[],
        initial=dict(selection=I.codec.encode(selection),lease=I.base.encode(lease))))
    cp=W.keep(tmp_path/'checkpoint.json',I._result('RETRY_READY',descriptor,selection,lease,
        checkpoint=permit,next_step='dialogue-writer',evidence=[permit]))
    monkeypatch.setattr(I,'selected',lambda row:(I.worker_decode(K.read(row)['worker']),None,None))
    authority=W.keep(tmp_path/'authority.json',{'explicit_duration_ns':L.DURATION_NS})
    row=K.renew(cp,descriptor,I=I,W=W,L=L,B=B,life=life,authority=authority,directory=tmp_path/'renewal',
        keep=W.keep,validation=lambda w,s,l,b:(S.validate(w,b),[w.dialogue_semantic_retry])[1],custody=lambda:None)
    return row,worker,selection,lease,permit,calls


def test_actual_semantic_cas_renewal_and_original_worker_preserve_spent_calls(failed,tmp_path,monkeypatch):
    row,worker,selection,previous,permit,calls=case(failed,tmp_path,monkeypatch)
    p,value,_,_,_,current=K.validate(row,I=I,W=W,L=L,B=B,life=life)
    assert current.owner==previous.owner and current.token==previous.token and current.revision==previous.revision+1
    assert len(calls)==3 and K.read(permit)['lease']==asdict(previous)
    with K.installed(row,I=I,W=W,L=L,B=B,life=life):
        _,actual,lease=I._prior(p['result'],p['descriptor'])
        result=W.step(worker,actual,lease,checkpoint=permit)
        assert result.status=='MODEL_REQUIRED'
    assert len(calls)==3
    request=W.request_saved(W.step_directory(worker,'dialogue-writer')/'request.json')
    assert request.prepared.ordinal==request.prepared.limit==2
    with pytest.raises(Exception):I._prior(p['result'],p['descriptor'])


@pytest.mark.parametrize('part',['before','after','completion'])
def test_archives_are_required_after_actual_tracker_changed(failed,tmp_path,monkeypatch,part):
    row,*_=case(failed,tmp_path,monkeypatch)
    p=K.read(row);Path(p[part]['path']).write_bytes(b'{}')
    with pytest.raises(ValueError,match='bytes changed'):
        K.validate(row,I=I,W=W,L=L,B=B,life=life)


def test_historical_reentry_does_not_require_old_after_hash_but_live_worker_does(failed,tmp_path,monkeypatch):
    row,worker,selection,previous,permit,calls=case(failed,tmp_path,monkeypatch)
    p,value,_,_,_,current=K.validate(row,I=I,W=W,L=L,B=B,life=life)
    path=Path(p['tracker_path']);old=path.read_bytes()
    # A later real lifecycle renewal changes the current tracker. Historical
    # decoding remains valid, but old renewed custody cannot perform work.
    life.lease_operation(path,authority=life.RootAuthority('harbor-orchestrator',L.DURATION_NS,lambda *a:True),
        operation='renew',lease_kind='task_agent',owner=current.owner,scope='workbook:'+selection.content.workbook,
        token=current.token,duration_ns=L.DURATION_NS,expected_revision=current.revision,expected_sha256=current.sha256)
    assert path.read_bytes()!=old
    with K.installed(row,I=I,W=W,L=L,B=B,life=life):
        assert I._prior(p['result'],p['descriptor'])[2]==current
        with pytest.raises(Exception):W.step(worker,selection,current,checkpoint=permit)
    assert len(calls)==3


def initial_case(failed,tmp_path,monkeypatch):
    worker,selection,lease,*_=failed
    worker=replace(worker,run=tmp_path/'unused-instruction')
    descriptor=W.keep(tmp_path/'initial-descriptor.json',dict(worker=I.worker_encode(worker),origin=[],
        initial=dict(selection=I.codec.encode(selection),lease=I.base.encode(lease))))
    checkpoint=W.keep(tmp_path/'initial-result.json',I._result('INSTRUCTION_READY',descriptor,selection,lease,
        checkpoint=None,next_step=W.STEPS[0],evidence=[]))
    monkeypatch.setattr(I,'selected',lambda row:(I.worker_decode(K.read(row)['worker']),None,None))
    return worker,selection,lease,descriptor,checkpoint


def test_actual_initial_renewal_has_no_predecessor_projection(failed,tmp_path,monkeypatch):
    worker,selection,previous,descriptor,checkpoint=initial_case(failed,tmp_path,monkeypatch)
    authority=W.keep(tmp_path/'initial-authority.json',{'explicit_duration_ns':L.DURATION_NS})
    row=K.renew(checkpoint,descriptor,I=I,W=W,L=L,B=B,life=life,authority=authority,
        directory=tmp_path/'initial-renewal',keep=W.keep,validation=lambda *a:[checkpoint,descriptor],custody=lambda:None)
    p,value,_,_,_,current=K.validate(row,I=I,W=W,L=L,B=B,life=life)
    assert current.revision==previous.revision+1 and value['checkpoint'] is None
    assert not worker.run.exists() and len(failed[-1])==3
    original=I._prior
    def forbidden(*a,**kw):raise AssertionError('fresh startup must not install a predecessor bridge')
    monkeypatch.setattr(B,'step_scope',forbidden)
    with K.installed(row,I=I,W=W,L=L,B=B,life=life):
        result,actual,lease=I._prior(p['result'],p['descriptor'])
        assert result['checkpoint'] is None and actual==selection and lease==current
        assert W.active(worker,actual,lease)
    assert I._prior is original


@pytest.mark.parametrize('fault',['worker','checkpoint','phase','descriptor'])
def test_initial_renewal_refuses_consumed_or_mismatched_start(failed,tmp_path,monkeypatch,fault):
    worker,selection,lease,descriptor,checkpoint=initial_case(failed,tmp_path,monkeypatch)
    if fault=='worker':worker.run.mkdir()
    else:
        value=K.read(checkpoint)
        if fault=='checkpoint':value['checkpoint']={'path':'/invented','sha256':'bad'}
        elif fault=='phase':value['next_step']=W.STEPS[1]
        else:value['lease']=I.base.encode(replace(lease,revision=lease.revision+1))
        Path(checkpoint['path']).write_text(__import__('json').dumps(value));checkpoint=K.binding(checkpoint['path'])
    authority=W.keep(tmp_path/'authority.json',{})
    before=K.binding(worker.context.tracker_root/(selection.content.workbook+'.json'))
    with pytest.raises(Exception):
        K.renew(checkpoint,descriptor,I=I,W=W,L=L,B=B,life=life,authority=authority,
            directory=tmp_path/'not-created',keep=W.keep,validation=lambda *a:[],custody=lambda:None)
    assert K.binding(before['path'])==before and not(tmp_path/'not-created').exists()
