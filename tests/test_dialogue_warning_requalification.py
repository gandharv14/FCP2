"""No-dispatch round-two warning recovery keeps actual original call journals."""
from pathlib import Path
from dataclasses import replace
import json
import pytest
from scripts.batch import harbor_dialogue_warning_requalification as q
from scripts.batch import harbor_instruction_worker as w
from scripts.batch import harbor_instruction_producer as p
from scripts.batch import harbor_dialogue_semantic_retry as retry
from scripts.batch.harbor_admission import record
import test_dialogue_semantic_retry as previous
from test_harbor_instruction_producer import setup
from test_dialogue_slot_producer import values

@pytest.fixture
def failed(setup,monkeypatch):
    from scripts.batch.harbor_complete_readiness import SemanticSelection
    selection=previous.Selection
    readiness=previous.ReadinessSelection
    def with_claims(*args,**kwargs):
        return replace(selection(*args,**kwargs),dialogue_claims=record(Path(args[4]).parent/'dialogue/claims.json'))
    monkeypatch.setattr(previous,'Selection',with_claims)
    monkeypatch.setattr(previous,'ReadinessSelection',lambda *a,**k:replace(readiness(*a,**k),semantic=SemanticSelection(None,None,None,None,None,())))
    return previous.failed.__wrapped__(setup,monkeypatch)


def test_snapshot_rejects_unrelated_change(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';name='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
    for root in (old,new):(root/name).parent.mkdir(parents=True)
    (old/name).write_text('old');(new/name).write_text('new')
    bound=p.put(tmp_path/'snapshot.json',{'root':str(old),'files':[{'relative':name,'sha256':record(old/name)['sha256']}]})
    assert q.changes(bound,new)==[name]
    (new/'foreign.py').write_text('changed financial evaluator')
    with pytest.raises(ValueError,match='unrelated'):q.changes(bound,new)


@pytest.mark.parametrize('change',[None,'review_tamper','repeat','substantive'])
def test_second_round_review_is_requalified_without_dispatch(failed,monkeypatch,change):
    worker,s,lease,blocked,t,campaign,respond,calls=failed
    worker,s,lease,permit=retry.authorize(worker,s,lease,blocked=blocked)
    pending=w.step(worker,s,lease,checkpoint=permit)
    writer=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json');respond(writer,values(t))
    done=w.step(worker,s,lease,checkpoint=pending.evidence)
    pending=w.step(worker,done.selection,done.lease,checkpoint=done.evidence)
    para=w.request_saved(w.step_directory(worker,'dialogue-paraphraser')/'request.json');respond(para,values(t))
    done=w.step(worker,pending.selection,pending.lease,checkpoint=pending.evidence)
    pending=w.step(worker,done.selection,done.lease,checkpoint=done.evidence)
    request=w.request_saved(w.step_directory(worker,'dialogue-review')/'request.json')
    body=json.loads(p.read(request.prepared.request)['messages'][1]['content']);claims=body['claims.json']
    sheet=claims['claims'][0]['sheet']
    assert sheet+': For ' in body['draft.md']
    response={'agent_model':'gpt-5.6-sol-high','round':2,'accuracy':{'verdict':'fail','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[] if i else [f'The frozen junior_scope header is altered by inserting "{sheet}:" before the required scoped-cell prefix.']} for i,c in enumerate(claims['claims'])],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':False}
    if change=='substantive':response['accuracy']['claims'][0]['must_say']='contradicted'
    respond(request,response)
    validator=p.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
    original_pass=validator.review_passed
    # Simulate the historical policy rejecting an otherwise unchanged review.
    with monkeypatch.context() as old:
        old.setattr(validator,'review_passed',lambda *a,**k:False)
        result=w.step(worker,pending.selection,pending.lease,checkpoint=pending.evidence)
    assert result.status=='DIALOGUE_REPAIR_REQUIRED' and len(calls)==6
    assert original_pass(response,claims,draft_text=body['draft.md']) is (change!='substantive')
    old_review=record(t.run/'dialogue/round-02/review.json');old_receipt=record(t.run/'dialogue/round-02/review-result.json')
    # Fixture source/runtime is external to this recovery test; actual calls,
    # reviews, budget history, worker and application remain production paths.
    monkeypatch.setattr(q.repair,'prefix',lambda *a,**k:[])
    monkeypatch.setattr(q,'changes',lambda *a:[])
    applications=[]
    monkeypatch.setattr(validator,'apply',lambda *a,**k:applications.append((a,k)))
    snapshot=p.put(t.run/'snapshot.json',{'root':str(worker.root),'files':[]})
    if change=='substantive':
        with pytest.raises(ValueError,match='substantive'):
            q.authorize(worker,result.selection,result.lease,blocked=result.evidence,old_snapshot=snapshot,directory=t.run/'warning-requalification')
        assert len(calls)==6
        return
    worker,s,lease,permit=q.authorize(worker,result.selection,result.lease,blocked=result.evidence,old_snapshot=snapshot,directory=t.run/'warning-requalification')
    assert len(calls)==6 and record(old_review['path'])==old_review and record(old_receipt['path'])==old_receipt
    if change=='review_tamper':
        Path(old_review['path']).write_text('{}')
        with pytest.raises(ValueError):w.step(worker,s,lease,checkpoint=permit)
        assert not applications and len(calls)==6
        return
    if change=='repeat':
        with pytest.raises(ValueError,match='already attempted'):
            q.authorize(replace(worker,warning_requalification=None),s,lease,blocked=result.evidence,old_snapshot=snapshot,directory=t.run/'warning-requalification')
        assert len(calls)==6
    completed=w.step(worker,s,lease,checkpoint=permit)
    assert completed.status=='STEP_COMPLETE' and completed.next_step=='final-runtime' and len(calls)==6
    assert len(applications)==1 and applications[0][1]=={'require_review_pass':True,'skip_smoke':True,'source_workbook':s.content.bindings['source_file'],'source_warning_proof':None}
    assert record(old_receipt['path'])==old_receipt
    assert w.step_directory(worker,'dialogue-review')!=worker.run/'steps/dialogue-review/semantic-round-02'

def test_completed_first_review_style_warning_preserves_three_calls(setup,monkeypatch):
    task,campaign,respond,calls=setup
    validator=p.skill_module(task.root,'additional-assumptions-dialogue','validate_dialogue')
    requests={}
    original_fixture=previous.dialogue_fixture
    def dialogue_fixture(arg):
        task,campaign,respond,calls=original_fixture(arg)
        def style_response(request,value):
            requests[p.read(request.context)['kind']]=request
            if isinstance(value,dict) and 'naturalness' in value:
                value={**value,'naturalness':{'verdict':'fail','findings':['The dialogue is repetitive.']}}
            return respond(request,value)
        return task,campaign,style_response,calls
    monkeypatch.setattr(previous,'dialogue_fixture',dialogue_fixture)
    with monkeypatch.context() as old:
        old.setattr(validator,'review_passed',lambda *a,**k:False)
        result=failed.__wrapped__(setup,monkeypatch)
    worker,s,lease,blocked,t,campaign,respond,calls=result
    for kind in ('dialogue-writer','dialogue-paraphraser'):
        w.request_saved(w.step_directory(worker,kind)/'request.json',requests[kind])
    repaired=p.put(t.run/'historical-code-repair.json',{'phase':'dialogue-init'})
    old_init=p.read(record(w.step_directory(worker,'dialogue-init')/'complete.json'))
    worker=replace(worker,code_repair=repaired)
    w.keep(w.step_directory(worker,'dialogue-init')/'complete.json',old_init)
    monkeypatch.setattr(q.repair,'validate_historical_integrity',lambda *a:({'current_code':{}},[]))
    monkeypatch.setattr(q.repair,'prefix',lambda *a,**k:[])
    monkeypatch.setattr(q,'changes',lambda *a:[])
    applied=[];monkeypatch.setattr(validator,'apply',lambda *a,**k:applied.append(True))
    snapshot=p.put(t.run/'snapshot.json',{'root':str(worker.root),'files':[]})
    old_review=record(t.run/'dialogue/round-01/review.json')
    worker,s,lease,permit=q.authorize(worker,s,lease,blocked=blocked,old_snapshot=snapshot,directory=t.run/'warning-first')
    assert p.read(permit)['round']==1 and p.read(permit)['semantic'] is None
    assert p.read(permit)['code_repair']==repaired and len(calls)==3
    done=w.step(worker,s,lease,checkpoint=permit)
    assert done.next_step=='final-runtime' and applied==[True] and len(calls)==3
    assert old_review==record(old_review['path'])


def test_warning_codec_accepts_only_bound_repaired_semantic_history(tmp_path):
    from scripts import harbor_instruction_campaign as c
    repair=p.put(tmp_path/'repair.json',{'retained':'original repair'})
    semantic_permit=p.put(tmp_path/'semantic-permit.json',dict(schema_version=retry.SCHEMA,prior_continuations={'code_repair':repair}))
    semantic=p.put(tmp_path/'semantic-complete.json',dict(schema_version=retry.SCHEMA,permit=semantic_permit))
    warning=p.put(tmp_path/'warning.json',{'retained':'current warning'})
    c._warning_history(semantic,repair,warning)
    other=p.put(tmp_path/'foreign-repair.json',{'retained':'different repair'})
    with pytest.raises(ValueError,match='detached'):
        c._warning_history(semantic,other,warning)
    with pytest.raises(ValueError,match='requires'):
        c._warning_history(None,None,warning)


@pytest.mark.parametrize('change',[None,'omitted_revision','substituted_repair','substituted_renewal'])
def test_revised_warning_requires_exact_history_and_both_historical_validators(tmp_path,monkeypatch,change):
    from dataclasses import dataclass
    from scripts.batch import harbor_instruction_lease_renewal as renewal
    from scripts.batch import harbor_disclosure_request_recovery as recovery
    @dataclass
    class HistoricalWorker:
        root: Path
        run: Path
        disclosure_revision: dict
        disclosure_repair: dict
        lease_renewal: dict
        warning_requalification: dict | None = None
    old=tmp_path/'old';new=tmp_path/'new';run=tmp_path/'run'
    run.mkdir();p.put(run/'initial.json',{'original':'immutable'})
    bindings={k:p.put(tmp_path/(k+'.json'),{'retained':k}) for k in q.REVISED_CONTINUATIONS}
    worker=HistoricalWorker(new,run,**bindings)
    snapshot=p.put(tmp_path/'snapshot.json',{'root':str(old),'files':[]})
    permit=p.put(tmp_path/'warning.json',dict(schema_version=q.SCHEMA,worker_run=str(run),initial=record(run/'initial.json'),
        code={},code_changes=[],old_snapshot=snapshot,prior_continuations=bindings,semantic=None,code_repair=None))
    worker=replace(worker,warning_requalification=permit)
    monkeypatch.setattr(w,'code',lambda _: {})
    monkeypatch.setattr(q,'changes',lambda *a:[])
    inspected=[]
    def check_renewal(historical,before,bound):
        assert historical.root==old and historical.warning_requalification is None and bound==snapshot
        assert q.revised_continuations(historical)==bindings
        inspected.append('renewal');return {'current_code':{}}
    def check_revision(historical,before,bound):
        assert historical.root==old and historical.warning_requalification is None
        assert bound==snapshot
        assert q.revised_continuations(historical)==bindings
        inspected.append('revision')
    monkeypatch.setattr(renewal,'validate_historical_integrity',check_renewal)
    monkeypatch.setattr(recovery,'validate_historical_integrity',check_revision)
    if change=='omitted_revision':worker=replace(worker,disclosure_revision=None)
    elif change=='substituted_repair':worker=replace(worker,disclosure_repair=p.put(tmp_path/'foreign.json',{}))
    elif change=='substituted_renewal':worker=replace(worker,lease_renewal=p.put(tmp_path/'foreign.json',{}))
    if change:
        with pytest.raises(ValueError,match='detached'):q.historical_lease(worker,{})
        assert inspected==[]
    else:
        assert q.historical_lease(worker,{})=={'current_code':{}}
        assert inspected==['renewal','revision']

@pytest.mark.parametrize('renewed',[False,True])
def test_ordinary_warning_history_can_have_no_instruction_renewal(tmp_path,monkeypatch,renewed):
    from types import SimpleNamespace
    from scripts.batch import harbor_dialogue_first_round_warning as first
    run=tmp_path/'run';run.mkdir();initial=p.put(run/'initial.json',{})
    lease={'path':'/retained-renewal','sha256':'r'} if renewed else None
    worker=SimpleNamespace(root=tmp_path,run=run,lease_renewal=lease,disclosure_revision=None,disclosure_repair=None)
    history={'original':'full validated history'}
    if renewed:history.update(lease_renewal=lease,renewal_history={'checkpoint':'original'})
    permit=p.put(tmp_path/'warning.json',dict(schema_version=q.SCHEMA,worker_run=str(run),initial=initial,code={},
        prior_continuations={'lease_renewal':lease} if renewed else {},semantic=None,code_repair=None,
        code_changes=[],old_snapshot={},ordinary_first_round=history))
    worker.warning_requalification=permit
    monkeypatch.setattr(w,'code',lambda _:{});monkeypatch.setattr(q,'changes',lambda *a:[])
    calls=[]
    def validate(worker,before,permit):calls.append(permit);return history
    monkeypatch.setattr(first,'validate_history',validate)
    assert q.historical_lease(worker,{})==(history['renewal_history'] if renewed else history)
    assert len(calls)==1
    if not renewed:
        history['renewal_history']={}
        with pytest.raises(ValueError,match='invented'):q.historical_lease(worker,{})
