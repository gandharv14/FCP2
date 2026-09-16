"""Current warning custody keeps exact old semantic/renewal implementation evidence."""
from dataclasses import replace
from pathlib import Path
import pytest
from scripts.batch import harbor_dialogue_warning_requalification as Q
from scripts.batch import harbor_instruction_lease_renewal as R
from scripts.batch import harbor_instruction_worker as W
from scripts.batch import harbor_dialogue_semantic_retry as S
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_instruction_producer import put

@pytest.fixture
def selected(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';run=tmp_path/'run';run.mkdir()
    names=['scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_role_producer.py',
        'scripts/batch/harbor_instruction_producer.py','scripts/batch/hardened_runner.py',
        'scripts/batch/process_guardian.py','scripts/harbor_pretransport_recovery.py',
        'scripts/harbor_instruction_code_applicability.py','scripts/batch/harbor_dialogue_semantic_retry.py',
        '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py']
    for root in (old,new):
        for name in names:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
    (new/names[-1]).write_text('current warning policy')
    snapshot=put(tmp_path/'snapshot.json',dict(root=str(old),files=[dict(relative=n,sha256=record(old/n)['sha256'])for n in names]))
    worker=W.Worker(None,run,None,new,lease_renewal={'path':'/exact/renewal','sha256':'a'*64})
    inventory=R._historical_inventory(replace(worker,root=old),snapshot)
    permit=put(run/'semantic-permit.json',dict(schema_version=S.SCHEMA,worker_run=str(run),code=inventory))
    complete=put(run/'semantic-complete.json',dict(schema_version=S.SCHEMA,permit=permit))
    return replace(worker,dialogue_semantic_retry=complete),snapshot,old

@pytest.mark.parametrize('failure',[False,True])
def test_only_exact_renewal_history_projects_root_and_restores(selected,monkeypatch,failure):
    worker,snapshot,old=selected;events=[]
    code=lambda w:('current-code',w.root)
    monkeypatch.setattr(W,'code',code)
    def original(w,b):events.append(('original',w,b));return 'foreign'
    def historical(w,b,s):
        assert w.root==old and w.warning_requalification is None and s==snapshot
        assert W.code(w)==R._historical_inventory(w,snapshot)
        events.append(('historical',w,b));return 'history'
    monkeypatch.setattr(R,'validate',original);monkeypatch.setattr(R,'validate_historical_integrity',historical)
    def active(w,s,l):
        assert w is worker and w.root!=old and (s,l)==('selection','live-lease')
        assert W.code(w)==('current-code',worker.root)
        assert R.validate(w,{'revision':9})=='history'
        assert R.validate(replace(w,run=w.run/'foreign'),{})=='foreign'
        if failure:raise ValueError('current custody changed')
        return 'live-custody'
    monkeypatch.setattr(W,'active',active)
    if failure:
        with pytest.raises(ValueError,match='current custody'):Q.preparation_active(worker,'selection','live-lease',snapshot)
    else:assert Q.preparation_active(worker,'selection','live-lease',snapshot)=='live-custody'
    assert W.code is code
    assert R.validate is original and [x[0]for x in events]==['historical','original']

@pytest.mark.parametrize('changed',['old_bytes','extra_old','foreign_semantic_code','source_root'])
def test_changed_history_rejected_before_live_gate(selected,monkeypatch,changed):
    worker,snapshot,old=selected
    if changed=='old_bytes':(old/'scripts/batch/harbor_instruction_worker.py').write_text('changed')
    elif changed=='extra_old':(old/'extra.py').write_text('extra')
    elif changed=='source_root':worker=replace(worker,root=old)
    else:
        permit=put(worker.run/'foreign-permit.json',dict(schema_version=S.SCHEMA,worker_run=str(worker.run),code={}))
        worker=replace(worker,dialogue_semantic_retry=put(worker.run/'foreign-complete.json',dict(schema_version=S.SCHEMA,permit=permit)))
    monkeypatch.setattr(W,'active',lambda *a:pytest.fail('must reject before current gate'))
    with pytest.raises(ValueError):Q.preparation_active(worker,None,None,snapshot)


def test_completed_warning_history_requires_exact_new_permit(selected,monkeypatch):
    worker,snapshot,old=selected;initial=put(worker.run/'initial.json',{})
    permit=dict(schema_version=Q.SCHEMA,worker_run=str(worker.run),initial=initial,code={'current':'code'},
        semantic=worker.dialogue_semantic_retry,code_repair=None,original={'source':'exact'},
        prior_continuations=Q.revised_continuations(worker),round=2,model_calls=0,semantic_budget_reset=False,
        old_snapshot=snapshot,code_changes=Q.changes(snapshot,worker.root))
    worker=replace(worker,warning_requalification=put(worker.run/'warning.json',permit))
    monkeypatch.setattr(W,'code',lambda w:{'current':'code'})
    seen=[];monkeypatch.setattr(Q,'semantic_history',lambda w,b,s:seen.append((w,b,s))or 'checked')
    before={'bindings':{'original_raw_source':{'source':'exact'}}}
    assert S.validate_completed_integrity(worker,before)=='checked' and len(seen)==1
    changed=replace(worker,lease_renewal={'path':'/foreign','sha256':'b'*64})
    with pytest.raises(ValueError,match='detached'):S.validate_completed_integrity(changed,before)
    assert len(seen)==1
