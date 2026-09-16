"""Owning source/research/normalization progress uses real CAS and review gates."""
from test_harbor_admission import setup
from test_harbor_environment_worker import selected,worker,responses
from test_normalization_partitioned import respond
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_routing as R
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record


def test_pending_partition_is_durable_without_premature_lane_or_budget_commit(worker,monkeypatch):
    s,lease=worker;responses(monkeypatch)
    research=W.advance(s,lease,dispatch=True,credential='synthetic')
    assert research['status']=='STEP_COMPLETE' and research['step']=='research'
    lease=research['lease'];content=research['selection'].content
    ns=N.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,s.normalization_budget)
    monkeypatch.setattr(N,'MAX_CONTEXT_BYTES',1);calls=[];respond(monkeypatch,ns,calls=calls)
    tracker=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json');before=tracker.read_bytes()
    pending=W.advance(s,lease,dispatch=True,credential='synthetic')
    assert pending['status']=='PENDING_MODEL' and pending['step']=='normalization_phase1',pending
    assert len(calls)==1 and len(pending['evidence'])==1
    proof=R.check_progress(pending['evidence'][0])
    assert proof['lane_approval'] is False and proof['semantic_budget_reset'] is False
    assert pending['lease']==lease and tracker.read_bytes()==before
    assert not (s.run/'normalization_phase1.json').exists()
    assert W.decode(W.encode(pending['selection']))==pending['selection']
    completed=W.advance(s,lease,dispatch=True,credential='synthetic')
    assert completed['status']=='STEP_COMPLETE' and completed['step']=='normalization_phase1',completed
    assert len(calls)==1 and completed['lease'].revision==lease.revision+1
    assert read(record(tracker))['lane_state']['harbor-normalization-engineer']['attempt']==1
