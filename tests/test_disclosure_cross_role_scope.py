"""Copied source formulas keep full coverage across distinct method-role labels."""
import copy
import pytest
from test_disclosure_copied_scope import disclose,fixture,record,faithcheck,SPAN


def records(gold):
 out=[]
 for key,entry in zip(SPAN,['method_working_capital','method_tax','method_debt_movement']):
  r=record(gold,[key]);profile=disclose.formula_profile(gold,dict(cell_keys=[key],label=gold.row_label(key),pattern=''))
  r.update(entry=entry,family=entry,role=entry,source='custom_method_detector',coverage_complete=True,
   method_profile={k:v for k,v in profile.items()if k!='ast'},leak_flag=False)
  out.append(r)
 return out


def test_actual_faithcheck_accepts_complete_source_derived_cross_role_family(tmp_path):
 gold,path,task=fixture(tmp_path);rows=records(gold)
 assert disclose._source_method_scope_partitioned(gold,SPAN,rows,'method_tax',set())
 result=faithcheck(gold,path,task,rows)
 assert result['passed'],result


@pytest.mark.parametrize('change',['missing','duplicate','unsupported_entry','wrong_formula','wrong_steps','wrong_label','graded','overbroad','machine_keys','incomplete','uniform'])
def test_cross_role_coverage_is_not_a_general_scope_waiver(tmp_path,change):
 gold,_,_=fixture(tmp_path,uniform=change=='uniform');rows=records(gold);closure=set()
 if change=='missing':rows.pop()
 elif change=='duplicate':rows.append(copy.deepcopy(rows[0]))
 elif change=='unsupported_entry':rows[0]['entry']='aggregate_scope'
 elif change=='wrong_formula':rows[0]['method_profile']['formula']='=9000/13'
 elif change=='wrong_steps':rows[0]['fields']['steps']='use a different divisor'
 elif change=='wrong_label':rows[0]['fields']['label']='"Other"'
 elif change=='graded':closure.add(SPAN[1])
 elif change=='overbroad':rows[0]['cells'].append('S!K156');rows[0]['cell_keys'].append('S!K156')
 elif change=='machine_keys':rows[0]['cell_keys']=[]
 elif change=='incomplete':rows[0]['coverage_complete']=False
 assert not disclose._source_method_scope_partitioned(gold,SPAN,rows,'method_tax',closure)
