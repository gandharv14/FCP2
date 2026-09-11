"""A preparation exception cannot replay authors or consume another round."""
from dataclasses import replace,asdict
from pathlib import Path
import pytest
from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as L,harbor_transition_codec as codec
from scripts.batch import harbor_dialogue_semantic_retry as D,harbor_dialogue_review_preparation_repair as R
from scripts.batch.harbor_admission import read,record
from test_dialogue_pre_review_worker_integration import repaired
from test_instruction_lease_renewal import boundary,case,instruction_setup

@pytest.fixture
def preparation(repaired,monkeypatch):
    worker,s,lease,_,q,blocked,tracker=repaired
    semantic_value=dict(initial=record(worker.run/'initial.json'),blocked=blocked,selection=codec.encode(s),lease=asdict(lease))
    permit=W.keep(worker.run/'semantic/permit.json',semantic_value)
    semantic=W.keep(worker.run/'semantic/complete.json',dict(schema_version=D.SCHEMA,permit=permit))
    worker=replace(worker,dialogue_semantic_retry=semantic)
    monkeypatch.setattr(D,'historical_pre_review',lambda *a:read(q))
    monkeypatch.setattr(D,'validate',lambda *a:semantic_value)
    previous=semantic
    for phase in ('dialogue-writer','dialogue-paraphraser'):
        result=W.step(worker,s,lease,checkpoint=previous);previous=result.evidence
    failed=W.step_directory(worker,'dialogue-review');failed.mkdir(parents=True)
    exception=W.keep(failed/'exception.json',{'automatic_replay':False,'type':'AdmissionError'})
    receipt=W.keep(worker.run/'preparation/permit.json',dict(schema_version=R.SCHEMA,phase='dialogue-review',predecessor=previous,selection=codec.encode(s),lease=asdict(lease)))
    monkeypatch.setattr(R,'historical',lambda *a:(read(receipt),semantic_value))
    return replace(worker,dialogue_review_preparation_repair=receipt),s,lease,receipt,exception,previous

def test_only_review_moves_to_new_namespace(preparation):
    worker,s,lease,permit,exception,para=preparation
    before=Path(exception['path']).read_bytes()
    assert W.step_directory(worker,'dialogue-paraphraser')/'complete.json'==Path(para['path'])
    with pytest.raises(ValueError,match='preceding checkpoint'):W.step(worker,s,lease,checkpoint=para)
    result=W.step(worker,s,lease,checkpoint=permit)
    assert read(result.evidence)['step']=='dialogue-review'
    assert read(read(result.evidence)['intent'])['previous']==permit
    assert Path(result.evidence['path']).parent==Path(permit['path']).parent/'steps/dialogue-review'
    assert Path(exception['path']).read_bytes()==before
    result2=W.step(worker,s,lease,checkpoint=result.evidence)
    assert read(result2.evidence)['step']=='final-runtime'

def test_closed_codec_requires_existing_round_and_correction(preparation):
    from scripts import harbor_instruction_campaign as C
    worker,*_=preparation
    assert C.worker_decode(C.worker_encode(worker))==worker
    for field in ('dialogue_semantic_retry','dialogue_pre_review_repair'):
        with pytest.raises(ValueError):C.worker_encode(replace(worker,**{field:None}))
    value=C.worker_encode(worker)
    with pytest.raises(ValueError):C.worker_decode({**value,'dialogue_review_preparation_repair':{'path':'x'}})

def test_author_phase_cannot_be_selected_by_repair(preparation):
    worker,s,lease,permit,*_=preparation
    with pytest.raises(ValueError,match='noncanonical'):W.step(worker,s,lease,phase='dialogue-writer',checkpoint=permit)

def test_late_renewal_keeps_operand_projection_distinct_from_cas(boundary,monkeypatch):
    from scripts import harbor_campaign as base
    from scripts.batch import harbor_nat_retry as N
    worker,s,lease,checkpoint,snapshot,tracker=boundary
    projection={'schema_version':'dialogue-source-operands/v1','claims':[{'record_id':'unchanged-clause','operands':[{'restatement_warning':{'code':'redundant_zero_floor_restatement'}}]}]}
    old=W.keep(worker.run/'old-code.json',{'root':str(worker.root),'files':[]})
    failure=W.keep(worker.run/'failed-review/exception.json',{'type':'AdmissionError'})
    W.keep(Path(failure['path']).parent/'intent.json',{'previous':checkpoint})
    before=read(record(tracker));before['bindings'].update(source_file=old,original_raw_source=old)
    monkeypatch.setattr(W,'active',lambda *a:(None,before,record(tracker)))
    monkeypatch.setattr(R,'preflight',lambda *a,**kw:(before,record(tracker),projection,[],[]))
    monkeypatch.setattr(R,'inventory',lambda *a:{'scripts/batch/harbor_dialogue_operand_evidence.py':'a'*64})
    monkeypatch.setattr(R,'owned',lambda b:read(b))
    monkeypatch.setattr(base,'_authorize',lambda *a:None)
    cas={'revision':lease.revision+1,'sha256':'b'*64,'marker':'lease-CAS-only'}
    monkeypatch.setattr(base.lifecycle,'lease_operation',lambda *a,**kw:cas)
    monkeypatch.setattr(N,'_renewal_chain',lambda *a:None)
    _,_,new_lease,binding=R.authorize(worker,s,lease,descriptor=old,checkpoint=checkpoint,exception=failure,old_snapshot=old,renew_duration_ns=base.DURATION)
    permit=read(binding)
    assert read(permit['projection'])==projection
    assert read(permit['renewal'])['result']==cas
    assert new_lease.revision==lease.revision+1
    assert 'marker' not in read(permit['projection'])
    with pytest.raises(ValueError,match='retained preparation'):
        R.authorize(worker,s,lease,descriptor=old,checkpoint=checkpoint,exception=failure,old_snapshot=old,renew_duration_ns=base.DURATION)
