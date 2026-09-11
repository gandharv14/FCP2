"""Canonical consumption cannot reuse a rejected or differently selected aggregate."""
from dataclasses import replace,asdict
import json
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_dialogue_partition_continuation as D
from scripts.batch import harbor_instruction_worker as W,harbor_transition_codec as K,harbor_nat_retry as N
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_admission import record
from test_harbor_complete_readiness import naturalization_fixture


@pytest.fixture
def boundary(tmp_path,monkeypatch):
    selection,_=naturalization_fixture(tmp_path)
    lease=Lease(owner='fixture-owner',token='fixture-token',revision=7,sha256='a'*64)
    authority=tmp_path/'authority.json';authority.write_text('{}')
    after=tmp_path/'tracker.json';after.write_text('{}')
    result=dict(passed=True,authority=record(authority),selection=K.encode(selection),after=record(after),lease=asdict(lease))
    monkeypatch.setattr(D,'completion',lambda worker:(None,result))
    monkeypatch.setattr(W,'active',lambda worker,s,l:(None,{'exact':'current'},None))
    proofs=[]
    def chain(binding,old,current,new):
        assert binding==record(after) and old==asdict(lease) and current=={'exact':'current'} and new==lease
        proofs.append(True)
    monkeypatch.setattr(N,'_renewal_chain',chain)
    return selection,lease,result,proofs


@pytest.mark.parametrize('phase',['dialogue-writer','dialogue-paraphraser'])
def test_completed_real_shards_consume_without_new_author_call_or_selection_change(boundary,phase):
    selection,lease,_,proofs=boundary
    result=D.consume(SimpleNamespace(),selection,lease,phase)
    assert result==(selection,lease,None) and proofs==[True]


@pytest.mark.parametrize('fault',['rejected','phase','selection'])
def test_consumption_rejects_wrong_boundary_before_application(boundary,fault):
    selection,lease,result,_=boundary;phase='dialogue-writer'
    if fault=='rejected':result['passed']=False
    elif fault=='phase':phase='disclosure-build'
    else:selection=replace(selection,content=replace(selection.content,stage_sha256='b'*64))
    with pytest.raises(ValueError):D.consume(SimpleNamespace(),selection,lease,phase)

from test_dialogue_partitions import completed,planned,setup


@pytest.mark.parametrize('fault',[None,'missing_report','changed_draft','failed_mechanics'])
def test_review_precondition_rederives_full_actual_author_assembly(completed,tmp_path,fault):
    from scripts.batch import harbor_dialogue_partition_review as R,harbor_dialogue_partition_plan as Plan
    task,claims,projection,plan,authority,writers,paras,reviews,draft=completed
    checked=[]
    for index,(writer,para) in enumerate(zip(writers,paras)):
        actual=R.validate_author(writer,authority,index,'writer',task.policy,root=task.root)
        checked.append({'checked':R.validate_author(para,authority,index,'paraphraser',task.policy,root=task.root,incoming=actual['filled'])})
    worker=SimpleNamespace(run=tmp_path/'draft-check-worker',root=task.root);directory=D.home(worker)/'round-01';directory.mkdir(parents=True)
    if fault=='failed_mechanics':
        # A completed transport can contain a mechanically invalid senior span.
        slot=next(k for k in checked[0]['checked']['slots']['slots'] if k.endswith('-senior'))
        checked[0]['checked']['slots']['slots'][slot]='Use cell A1 with the answer 0.123.'
    composer=W.producer.skill_module(task.root,'additional-assumptions-dialogue','compose_draft')
    full=composer.strip_slot_scaffold(Plan.assemble(projection,[x['checked']['slots'] for x in checked],root=task.root))
    (directory/'draft.md').write_text(full)
    validator=W.producer.skill_module(task.root,'additional-assumptions-dialogue','validate_dialogue')
    source=W.producer.dialogue_source_workbook(task)
    report=validator.check_draft(full,claims,task.stage,source_workbook=source)
    if fault!='missing_report':(directory/'draft-mechanics.json').write_text(json.dumps(report))
    if fault=='changed_draft':(directory/'draft.md').write_text(full+' changed')
    selected=SimpleNamespace(content=SimpleNamespace(stage=task.stage,bindings={'source_file':source}))
    if fault is None:assert D._checked_draft(worker,{'claims':claims,'projection':projection},checked,selected)==full
    else:
        if fault=='failed_mechanics':assert report['passed'] is False
        with pytest.raises((ValueError,FileNotFoundError)):D._checked_draft(worker,{'claims':claims,'projection':projection},checked,selected)
