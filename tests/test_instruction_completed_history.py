"""Historical prefix reads must not reapply admission for the following step."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_lease_renewal as R
from scripts.batch import harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_tracker_lifecycle import canonical
from test_instruction_lease_renewal import boundary
from test_instruction_worker import case,instruction_setup


def test_actual_pending_boundary_preserves_history_but_cannot_authorize(boundary,monkeypatch):
    worker,selection,lease,checkpoint,snapshot,tracker=boundary
    before=tracker.read_bytes();historical=R._boundary(worker,selection,lease,checkpoint)
    monkeypatch.setattr(W,'_operation',lambda *args,**kw:(selection,lease,'MODEL_REQUIRED'))
    pending=W.step(worker,selection,lease,checkpoint=checkpoint,models=False)
    assert pending.status=='MODEL_REQUIRED'
    assert R.completed_history(worker,selection,lease,checkpoint)==historical
    with pytest.raises(ValueError,match='pending or uncertain'):
        R._boundary(worker,selection,lease,checkpoint)
    with pytest.raises(ValueError,match='pending or uncertain'):
        R.authorize(worker,selection,lease,checkpoint,snapshot,duration_ns=200,clock=lambda:30)
    assert tracker.read_bytes()==before and not (worker.run/'lease-renewals').exists()


def test_later_completed_step_does_not_change_selected_prefix(boundary):
    worker,selection,lease,checkpoint,_,_=boundary
    historical=R.completed_history(worker,selection,lease,checkpoint)
    later=W.step(worker,selection,lease,checkpoint=checkpoint)
    assert later.status=='STEP_COMPLETE'
    assert R.completed_history(worker,selection,lease,checkpoint)==historical
    assert R.completed_history(worker,selection,lease,later.evidence)[0]=='disclosure-commit'
    with pytest.raises(ValueError,match='exact latest'):
        R._boundary(worker,selection,lease,checkpoint)


@pytest.mark.parametrize('later',['intent.json','exception.json','response.json'])
def test_uncertain_later_evidence_remains_ineligible_and_is_never_read(boundary,monkeypatch,later):
    worker,s,l,cp,snapshot,tracker=boundary;before=tracker.read_bytes();expected=R.completed_history(worker,s,l,cp)
    directory=W.step_directory(worker,'disclosure-review');directory.mkdir();path=directory/later;path.write_bytes(b'uncertain or interrupted later operation\n')
    original_read=R.read
    def selected_only(binding):
        assert not Path(binding['path']).is_relative_to(directory)
        return original_read(binding)
    monkeypatch.setattr(R,'read',selected_only)
    assert R.completed_history(worker,s,l,cp)==expected
    with pytest.raises(ValueError,match='pending or uncertain'):R.authorize(worker,s,l,cp,snapshot,duration_ns=200,clock=lambda:30)
    assert tracker.read_bytes()==before and path.read_bytes()==b'uncertain or interrupted later operation\n'


@pytest.mark.parametrize('fault',['checkpoint-hash','foreign-checkpoint','selection','lease','intent','code'])
def test_history_still_authenticates_original_prefix(boundary,monkeypatch,fault):
    worker,s,l,cp,_,_=boundary
    if fault=='checkpoint-hash':cp=dict(cp,sha256='0'*64)
    elif fault=='foreign-checkpoint':cp=W.keep(worker.run/'foreign-complete.json',read(cp))
    elif fault=='selection':s=replace(s,content=replace(s.content,stage_sha256='0'*64))
    elif fault=='lease':l=replace(l,token='other')
    elif fault=='intent':Path(read(cp)['intent']['path']).write_bytes(b'{}\n')
    else:
        path=worker.run/'producer-proof.py';path.write_bytes(b'original\n');intent=read(read(cp)['intent']);intent['code'][str(path)]=record(path)['sha256'];intent_row=W.keep(worker.run/'changed-intent.json',intent);value=read(cp);value['intent']=intent_row;Path(cp['path']).write_bytes(canonical(value));cp=record(cp['path']);path.write_bytes(b'changed\n')
    with pytest.raises(ValueError):R.completed_history(worker,s,l,cp)


def test_completed_model_evidence_is_inspected_but_never_dispatched(boundary,monkeypatch):
    worker,s,l,cp,_,_=boundary;path=Path(cp['path']).parent/'request.json';path.write_bytes(b'exact request witness\n')
    context=W.keep(worker.run/'request-context.json',{'fixture':'source context'})
    outcome=W.keep(worker.run/'call-outcome.json',{'fixture':'completed Call'})
    raw=W.keep(worker.run/'raw-response.json',{'fixture':'raw response'})
    request=SimpleNamespace(prepared=object(),context=context);call=object();seen=[]
    monkeypatch.setattr(W,'request_saved',lambda p:request)
    monkeypatch.setattr(W.executor,'inspect',lambda p,c:(seen.append((p,c))or SimpleNamespace(status='completed',call=call,outcome=outcome)))
    monkeypatch.setattr(W.executor,'execute',lambda *a,**kw:pytest.fail('history dispatched'))
    from scripts.batch import harbor_complete_readiness as ready
    monkeypatch.setattr(ready,'evidence_records',lambda c:[raw]if c is call else pytest.fail('foreign Call'))
    phase,history=R.completed_history(worker,s,l,cp)
    assert phase=='disclosure-review' and seen==[(request.prepared,worker.campaign)]
    assert history[-4:]==[record(path),context,raw,outcome]
    monkeypatch.setattr(W.executor,'inspect',lambda *a:SimpleNamespace(status='incomplete',call=None,outcome=outcome))
    with pytest.raises(ValueError,match='completed call'):R.completed_history(worker,s,l,cp)
