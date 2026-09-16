import copy
import pytest
from openpyxl import Workbook
import xl_source_health as H

@pytest.fixture
def reports(tmp_path):
    p=tmp_path/'book.xlsx';w=Workbook();w.active['A1']=7;w.save(p)
    new=H.inspect_workbook(p);old=copy.deepcopy(new)
    assert old['counts'].pop('formula_caches_blank_string')==0
    old['report_sha256']=H._report_id(old)
    return p,old,new

def test_authentic_absent_zero_preserves_original_reports(reports):
    p,old,new=reports;before=copy.deepcopy((old,new))
    assert H.matches_observed_report(old,new,p)
    assert H.matches_observed_report(new,new,p)
    assert (old,new)==before

@pytest.mark.parametrize('fault',['nonzero','other_count','other_field','explicit_nonzero','boolean','missing_other_count'])
def test_only_exact_absent_integer_zero_is_compatible(reports,fault):
    p,old,new=reports
    if fault=='nonzero':new['counts']['formula_caches_blank_string']=1
    elif fault=='boolean':new['counts']['formula_caches_blank_string']=False
    elif fault=='other_count':new['counts']['formula_cells']+=1
    elif fault=='other_field':new['extra_observation']='different'
    elif fault=='explicit_nonzero':old['counts']['formula_caches_blank_string']=1;old['report_sha256']=H._report_id(old)
    elif fault=='missing_other_count':old['counts'].pop('formula_cells');old['report_sha256']=H._report_id(old)
    new['report_sha256']=H._report_id(new)
    assert not H.matches_observed_report(old,new,p)

def test_tampered_original_hash_rejected(reports):
    p,old,new=reports;old['counts']['sheets']=99
    with pytest.raises(H.SourceHealthError):H.matches_observed_report(old,new,p)

def test_changed_current_source_rejected(reports):
    p,old,new=reports;p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises(H.SourceHealthError):H.matches_observed_report(old,new,p)
