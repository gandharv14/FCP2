from dataclasses import replace
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_nat_warning_history as H
from scripts.batch import harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as I
from scripts.batch.harbor_admission import record
from test_nat_worker_retry import setup,modules,failed


def test_historical_nat_reader_retains_actual_cas_and_budget(failed,monkeypatch):
    w,N,worker,selection,lease,blocked,task,_,calls,*_=failed
    worker,_,_,completion=N.authorize(worker,selection,lease,blocked=blocked)
    before=json.loads(Path(task.tracker['path']).read_text())
    expected=w.code(worker)
    from scripts.batch import harbor_instruction_lease_renewal as R
    seen=[]
    monkeypatch.setattr(R,'_historical_inventory',lambda selected,snapshot:seen.append((selected,snapshot))or expected)
    proof=N.validate_historical_integrity(worker,before,{'selected':'old-snapshot'})
    assert proof==N.validate(worker,before) and len(calls)==1
    assert seen==[(worker,{'selected':'old-snapshot'})]
    broken=json.loads(Path(completion['path']).read_text());broken['lease']['revision']+=1
    Path(completion['path']).write_text(json.dumps(broken))
    with pytest.raises(ValueError):N.validate_historical_integrity(worker,before,{'selected':'old-snapshot'})
    assert len(calls)==1


@pytest.mark.parametrize('field',['dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','disclosure_repair','disclosure_revision','dialogue_pre_review_repair','lease_renewal'])
def test_nat_warning_does_not_borrow_unrelated_history(field):
    worker=W.Worker(None,Path('/run'),None,Path('/code'),retry={'path':'/retry','sha256':'a'*64})
    assert H.eligible(worker)
    assert not H.eligible(replace(worker,**{field:{'path':'/foreign','sha256':'b'*64}}))
    assert not H.eligible(replace(worker,retry=None))


@pytest.mark.parametrize('defect',[None,'retry','extra','round','calls','reset','proof'])
def test_nat_warning_validation_requires_exact_history(monkeypatch,defect):
    worker=W.Worker(None,Path('/run'),None,Path('/code'),retry={'path':'/retry','sha256':'a'*64})
    evidence={'actual':'retained'}
    permit=dict(nat_first_round=evidence,round=1,semantic=None,code_repair=None,model_calls=0,semantic_budget_reset=False,prior_continuations={'retry':worker.retry},old_snapshot={})
    if defect=='retry':permit['prior_continuations']['retry']={}
    elif defect=='extra':permit['prior_continuations']['lease_renewal']={}
    elif defect=='round':permit['round']=2
    elif defect=='calls':permit['model_calls']=1
    elif defect=='reset':permit['semantic_budget_reset']=True
    elif defect=='proof':permit['nat_first_round']={}
    monkeypatch.setattr(H,'history',lambda *args:evidence)
    if defect:
        with pytest.raises(ValueError):H.validate(worker,{},permit)
    else:assert H.validate(worker,{},permit)==evidence


@pytest.mark.parametrize('defect',[None,'foreign','omitted','renewal'])
def test_codec_retains_exact_nat_warning_predecessor(tmp_path,defect):
    retry={'path':'/retry','sha256':'a'*64};warning=tmp_path/'warning.json'
    warning.write_text(json.dumps(dict(schema_version='harbor-dialogue-warning-requalification/v1',nat_first_round={'retry':retry},prior_continuations={'retry':retry})))
    selected={}if defect=='foreign'else None if defect=='omitted'else retry
    renewal={}if defect=='renewal'else None
    if defect:
        with pytest.raises(ValueError):I._warning_history(None,None,record(warning),renewal=renewal,retry=selected)
    else:I._warning_history(None,None,record(warning),retry=selected)

@pytest.mark.parametrize('defect',[None,'changed','missing','symlink'])
def test_historical_nat_uses_current_skill_only_after_complete_byte_equivalence(tmp_path,monkeypatch,defect):
    from scripts.batch import harbor_nat_retry as N,harbor_instruction_lease_renewal as R
    from types import SimpleNamespace
    old=tmp_path/'old';new=tmp_path/'new';relative=Path('.cursor/skills/naturalize-finance-task-instruction/scripts')
    for root in (old,new):
        (root/relative).mkdir(parents=True);(root/relative/'instruction_spans.py').write_text('unchanged parser')
    if defect=='changed':(new/relative/'instruction_spans.py').write_text('different parser')
    elif defect=='missing':(new/relative/'instruction_spans.py').unlink()
    elif defect=='symlink':
        target=new/relative/'instruction_spans.py';target.unlink();target.symlink_to(old/relative/'instruction_spans.py')
    monkeypatch.setattr(N,'__file__',str(new/'scripts/batch/harbor_nat_retry.py'))
    monkeypatch.setattr(R,'_historical_inventory',lambda *a:{'exact':'old-code'})
    calls=[];monkeypatch.setattr(N,'_validate',lambda w,b,c,**kw:calls.append((c,kw))or 'retained')
    if defect:
        with pytest.raises(ValueError):N.validate_historical_integrity(SimpleNamespace(root=old),{}, {})
        assert not calls
    else:
        assert N.validate_historical_integrity(SimpleNamespace(root=old),{}, {})=='retained'
        assert calls==[({'exact':'old-code'},{'skill_root':new})]
