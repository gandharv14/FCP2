import pytest
from scripts.batch import harbor_lease_takeover as T
from scripts.batch.harbor_admission import read,record
from test_harbor_instruction_producer import setup
from test_nat_worker_retry import modules
from test_nat_boundary_resume import ready
from test_dialogue_slot_producer import authoring
from test_dialogue_retry_worker import failed as dialogue_failed,worker_modules


def test_boundary_requires_explicit_fixed_takeover_bridge_before_any_continuation(ready,monkeypatch,tmp_path):
    from scripts.batch import harbor_nat_boundary_resume as B
    w,worker,s,lease,blocked,t,calls=ready;before=record(t.tracker['path'])
    permit={'path':str(tmp_path/'explicit-complete.json'),'sha256':'a'*64}
    def rejected(original,old,current,new,*,takeover,tracker_path):
        assert takeover==permit and tracker_path==t.tracker['path']
        assert old['token']==lease.token and new==lease
        raise ValueError('fixed takeover validator rejected')
    monkeypatch.setattr(T,'continuation_chain',rejected)
    with pytest.raises(ValueError,match='fixed takeover validator'):B.authorize(worker,s,lease,blocked=blocked,takeover=permit)
    assert record(t.tracker['path'])==before and len(calls)==2
    assert not (worker.run/'boundary-resume-v1').exists()


def test_dialogue_requires_explicit_fixed_takeover_bridge_before_any_continuation(dialogue_failed,monkeypatch,tmp_path):
    w,R,worker,s,lease,checkpoint,task,campaign,respond,calls=dialogue_failed;before=record(task.tracker['path'])
    permit={'path':str(tmp_path/'explicit-complete.json'),'sha256':'a'*64}
    def rejected(original,old,current,new,*,takeover,tracker_path):
        assert takeover==permit and tracker_path==task.tracker['path']
        assert old['token']==lease.token and new==lease
        raise ValueError('fixed takeover validator rejected')
    monkeypatch.setattr(T,'continuation_chain',rejected)
    with pytest.raises(ValueError,match='fixed takeover validator'):R.authorize(worker,s,lease,checkpoint=checkpoint,takeover=permit)
    assert record(task.tracker['path'])==before and len(calls)==1
    assert not (worker.run/'dialogue-structural-retry-01').exists()
