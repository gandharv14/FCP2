import importlib.util,json,time,copy
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];H=R/'diagnostics/vm-fleet-20260912/control/0029-publication-003'
s=importlib.util.spec_from_file_location('pubexecute3',H/'execute.py');E=importlib.util.module_from_spec(s);s.loader.exec_module(E)
def status():
    v=json.loads((H/'central-failed-002.json').read_bytes());v['at']=time.time()
    for op in v['operations']:
        if op['kind']=='publication':op['state']='abandoned'
    return v

def test_exact_retained_publication_boundary():assert E.selected_row(status())['fence']==4
@pytest.mark.parametrize('field,value',[('owner','hhu-server-3:publication:0029:001'),('fence',3),('source_sha256','0'*64),('state','active')])
def test_wrong_native_custody_rejected(field,value):
    s=status();next(w for w in s['workbooks'] if w['workbook']=='0029')[field]=value
    with pytest.raises(ValueError):E.selected_row(s)

def test_both_prior_publications_require_reconciliation():
    for ident in ['5d13df9b-dcc3-44bd-b496-f0fd27cdeea8','5ed412fe-a01f-4116-b419-e28165ae106f']:
        s=status();next(o for o in s['operations'] if o['operation_id']==ident)['state']='hold'
        with pytest.raises(ValueError):E.selected_row(s)

def test_original_owning_intent_only_outer_journal_changes():
    p=E.checked(E.PLAN);old=json.loads((H.with_name('0029-publication-002')/'plan.json').read_bytes())
    assert p=={**old,'journal':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-publication-003','owning_resume':{'journal':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-publication-002/owning-publication','intent':{'path':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-publication-002/owning-publication/intent.json','sha256':'b5703876cbbe4b8919a16d011252632fd59be6ba49441e0dcfe5180c42cc4f95'},'proof':E.RESUME}}
