"""Real naturalization validator failure routed through fixed notes APIs.

Source/segmentation adapters are independently exercised by ordinary real
workbook tests; only that already-tested prerequisite is isolated here.
"""
from dataclasses import replace
from pathlib import Path
import sys
import pytest
from test_harbor_ready_transitions import fixture,kw,Interrupted,ROOT
from test_harbor_complete_readiness import naturalization_fixture
from scripts.batch import harbor_completed_lanes as gates
from scripts.batch import harbor_lane_notes as n
from scripts.batch import harbor_failure_snapshot as snapshots
from scripts.batch import harbor_tracker_lifecycle as lifecycle


def context(tmp_path,monkeypatch):
    base=tmp_path/'mechanics';base.mkdir();ready,path,start,unused,proof=fixture(base,monkeypatch)
    actual=tmp_path/'actual';actual.mkdir();selected,nat=naturalization_fixture(actual)
    selected=replace(selected,content=replace(selected.content,workbook='0245'))
    monkeypatch.setattr(gates,'pair_and_audit',lambda *args:[])
    return n.LaneNotes(n.RootContext(base)),path,start,selected,nat


def test_real_nat_gate_pass_records_actual_consumption(tmp_path,monkeypatch):
    f,p,start,selection,nat=context(tmp_path,monkeypatch)
    before=start['tracker'];before['lane_state']['harbor-instruction-naturalizer']['attempt']=0
    p.write_bytes(lifecycle.canonical(before));start['sha256']=n.record(p)['sha256']
    gates.check_completed_gate(before,gates.CompletionEvidence('naturalization',selection),f.context)
    result=f.complete('0245',gates.CompletionEvidence('naturalization',selection),**kw(start))
    row=result['tracker']['lane_state']['harbor-instruction-naturalizer']
    assert row['state']=='passed' and row['attempt']==1


def test_old_consumed_nat_budget_cannot_reset(tmp_path,monkeypatch):
    f,p,start,selection,nat=context(tmp_path,monkeypatch);raw=p.read_bytes()
    with pytest.raises(ValueError,match='consumed semantic attempts decreased'):
        f.complete('0245',gates.CompletionEvidence('naturalization',selection),**kw(start))
    assert p.read_bytes()==raw


def test_real_nat_stage_failure_is_classified_and_snapshot_preserves_it(tmp_path,monkeypatch):
    f,p,start,selection,nat=context(tmp_path,monkeypatch)
    (selection.content.stage/'instruction.md').write_text('invalid changed instruction')
    packet=gates.CompletionEvidence('naturalization',selection)
    verdict=n.check_gate(start['tracker'],packet,f.context)
    assert verdict['passed'] is False and verdict['failure_class']=='naturalization' and not verdict['terminal']
    failed=f.record_failure('0245',packet,**kw(start));old=failed['tracker']['promotion_failure']
    assert old['class']=='naturalization' and old['subclass']=='owning_validator_rejected'
    assert 'invalid changed instruction' not in Path(failed['tracker']['agent_notes']['path']).read_text()
    archived=snapshots.FailureSnapshots(f.context).snapshot_failure('0245',**kw(failed))
    (nat/'state.json').write_text('{"status":"changed by later repair"}')
    assert f.require_synchronized('0245')
    assert archived['tracker']['repairs']==start['tracker']['repairs']


@pytest.mark.parametrize('where',['prepared','notes_replaced','tracker_replaced'])
def test_failed_real_nat_journal_recovers_exact_classification(tmp_path,monkeypatch,where):
    f,p,start,selection,nat=context(tmp_path,monkeypatch)
    (selection.content.stage/'instruction.md').write_text('invalid changed instruction')
    def stop(point):
        if point==where:raise Interrupted()
    with pytest.raises(Interrupted):f.record_failure('0245',gates.CompletionEvidence('naturalization',selection),**kw(start),_fault=stop)
    journal=n.record(next((p.parent/'0245-notes-transactions').glob('*/transaction.json')))
    result=f.recover('0245',journal,owner='agent1',token=kw(start)['token'])
    assert result['tracker']['promotion_failure']['class']=='naturalization' and f.require_synchronized('0245')


def test_upstream_context_failure_keeps_earliest_owning_lane(tmp_path,monkeypatch):
    f,p,start,selection,nat=context(tmp_path,monkeypatch)
    def fail(*args):raise gates.ContextRejection(n.SEGMENTATION,'segmentation','preflight',ValueError('current verifier changed'))
    monkeypatch.setattr(gates,'pair_and_audit',fail)
    verdict=n.check_gate(start['tracker'],gates.CompletionEvidence('naturalization',selection),f.context)
    assert verdict['lane']==n.SEGMENTATION and verdict['gate']=='segmentation' and verdict['failure_class']=='preflight'


def test_fixed_repair_invalidation_and_real_repass_do_not_clear_failure(tmp_path,monkeypatch):
    f,p,start,selection,nat=context(tmp_path,monkeypatch)
    start['tracker']['lane_state']['harbor-instruction-naturalizer']['attempt']=1
    p.write_bytes(lifecycle.canonical(start['tracker']));start['sha256']=n.record(p)['sha256']
    instruction=selection.content.stage/'instruction.md';valid=instruction.read_bytes();instruction.write_text('invalid changed instruction')
    packet=gates.CompletionEvidence('naturalization',selection)
    failed=f.record_failure('0245',packet,**kw(start))
    repair=snapshots.FailureSnapshots(f.context)
    with pytest.raises(ValueError,match='snapshot required'):repair.begin_repair('0245',**kw(failed))
    frozen=repair.snapshot_failure('0245',**kw(failed));invalidated=repair.begin_repair('0245',**kw(frozen))
    assert invalidated['tracker']['lane_state']['harbor-independent-verifier']['state']=='pending'
    assert invalidated['tracker']['lane_state']['harbor-dialogue-author']['state']=='pending'
    instruction.write_bytes(valid)
    repassed=f.complete('0245',packet,**kw(invalidated))
    assert repassed['tracker']['lane_state']['harbor-instruction-naturalizer']['state']=='passed'
    assert repassed['tracker']['promotion_failure']==invalidated['tracker']['promotion_failure']
    assert repassed['tracker']['repairs']==start['tracker']['repairs'] and f.require_synchronized('0245')
