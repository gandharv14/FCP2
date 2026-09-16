"""Retained argv identity survives relocation without accepting changed validators."""
from dataclasses import replace
import shutil
import pytest
from scripts.batch import harbor_runtime_readiness as R
from scripts.batch import harbor_complete_readiness as C,harbor_completed_lanes as L
from test_harbor_runtime_readiness import capture,reseal
from test_portable_runtime_propagation import selection

@pytest.mark.parametrize('defect',[None,'current_code','historical_code','oracle_command','reward'])
def test_completed_gate_uses_exact_old_root_and_identical_current_code(tmp_path,monkeypatch,selection,defect):
    runtime,old,run,obs,calls,_=capture(tmp_path,monkeypatch)
    new=tmp_path/'successor';shutil.copytree(old,new)
    if defect=='current_code':(new/'xl_mcp_oracle.py').write_text('changed')
    if defect=='historical_code':(old/'xl_mcp_oracle.py').write_text('changed')
    if defect in ('oracle_command','reward'):
        import json
        p=run/('oracle.command.json' if defect=='oracle_command' else 'reward/reward.json');v=json.loads(p.read_text())
        if defect=='oracle_command':v['argv'][2]=str(new/'xl_mcp_oracle.py')
        else:v['score']=0.0
        p.write_text(json.dumps(v));obs=reseal(run,obs)
    monkeypatch.setattr(L.static,'ROOT',new)
    monkeypatch.setattr(L,'predecessors',lambda *a:'harbor-independent-verifier')
    monkeypatch.setattr(L,'pair_and_audit',lambda *a:[])
    monkeypatch.setattr(C,'evidence_records',lambda *a:[])
    selected=replace(selection,content=replace(selection.content,stage=runtime.stage),runtime=runtime,observation=obs)
    before=R.tree(run);count=len(calls)
    def check():return L.check_completed_gate({'workbook_id':'0001'},L.CompletionEvidence('verification',selected),object(),runtime_root=old)
    if defect:
        with pytest.raises(ValueError):check()
    else:
        assert check()['passed']
        with pytest.raises(ValueError,match='runtime command differs: oracle'):
            L.check_completed_gate({'workbook_id':'0001'},L.CompletionEvidence('verification',selected),object())
    assert R.tree(run)==before and len(calls)==count
