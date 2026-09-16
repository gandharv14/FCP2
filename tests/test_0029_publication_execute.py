import copy,importlib.util,time
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/0029-publication-001/execute.py'
s=importlib.util.spec_from_file_location('pubexecution',P);E=importlib.util.module_from_spec(s);s.loader.exec_module(E)
def status():
    v=copy.deepcopy(E.checked(E.STOPPED)['central']);v['at']=time.time();return v

def test_actual_stopped_and_exact_runtime_proof():
    assert E.selected_row(status())['fence']==2
    r=E.checked(E.RUNTIME);assert r['actual_native_runtime_verified'] and r['grader_exact']==35

@pytest.mark.parametrize('field,value',[('fence',3),('source_sha256','0'*64),('owner','other'),('state','active')])
def test_foreign_or_used_custody_blocks(field,value):
    v=status();next(w for w in v['workbooks'] if w['workbook']=='0029')[field]=value
    with pytest.raises(ValueError):E.selected_row(v)

def test_unknown_or_unresolved_operation_blocks():
    for state in ['active','hold','unknown']:
        v=status();v['operations'][0]['state']=state
        with pytest.raises(ValueError):E.selected_row(v)

def test_exact_argv_not_embedded_probe_text():
    assert E.owned_argv(['python','--plan=/tasks/0029/job.json'],['/tasks/0029'])
    assert not E.owned_argv(['python','-c',"print('/tasks/0029/job.json')"],['/tasks/0029'])

def test_ready_reconciliation_does_not_accept_old_owner():
    v=status();w=next(w for w in v['workbooks'] if w['workbook']=='0029');w['state']='ready'
    with pytest.raises(ValueError):E.selected_row(v,'ready')
    w['owner']=w['claim_id']=None
    assert E.selected_row(v,'ready')['state']=='ready'
