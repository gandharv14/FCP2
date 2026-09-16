import copy,importlib.util,json,time
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];P=R/'diagnostics/vm-fleet-20260912/control/0029-publication-002/execute.py'
s=importlib.util.spec_from_file_location('pubexecution2',P);E=importlib.util.module_from_spec(s);s.loader.exec_module(E)
def status():
    v=json.loads((E.PRIOR/'central-failed-001.json').read_bytes());v['at']=time.time()
    op=next(o for o in v['operations'] if o['operation_id']=='5d13df9b-dcc3-44bd-b496-f0fd27cdeea8');op['state']='abandoned';return v

def test_current_failed_publication_requires_explicit_abandon():
    v=status();assert E.selected_row(v)['fence']==3
    next(o for o in v['operations'] if o['kind']=='publication')['state']='hold'
    with pytest.raises(ValueError):E.selected_row(v)

@pytest.mark.parametrize('field,value',[('fence',2),('source_sha256','0'*64),('owner','hhu-server-3:slot-2:0029-pre-review-001'),('state','active')])
def test_foreign_or_used_custody_blocks(field,value):
    v=status();next(w for w in v['workbooks'] if w['workbook']=='0029')[field]=value
    with pytest.raises(ValueError):E.selected_row(v)

def test_plan_only_new_journal_delta_and_real_paths_validated():
    before=json.loads((E.PRIOR/'plan.json').read_bytes());after=E.checked(E.PLAN)
    assert after=={**before,'journal':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-publication-002'}
    proof=E.checked(E.REPAIR);assert proof['owning_paths_validated'] and proof['tracker_unchanged'] and not proof['publication_performed']
    assert E.checked(E.FAILURE)['owning_journal_absent']

def test_all_other_operations_confirmed_and_limits_preserved():
    v=status();v['model_limit']=2
    with pytest.raises(ValueError):E.selected_row(v)

def test_native_paths_check_present_before_spawn():
    import inspect
    code=inspect.getsource(E.native_probe)
    assert "env[2]._paths(request,Path(plan['journal'])/'owning-publication')" in code
    assert 'f860ec396975e9db774e44070f2efdbf8f0527130da4b51cc7fc303cba993bb3' in code
