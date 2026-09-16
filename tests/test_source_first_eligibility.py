import pytest
from scripts.fleet.source_first import eligible_source

@pytest.mark.parametrize('route,reasons',[('pass',[]),('unsupported',['data_tables_present'])])
def test_existing_routes_remain_eligible(route,reasons):
 assert eligible_source(dict(route=route,reason_codes=reasons),'/source')=='existing'

@pytest.mark.parametrize('reasons',[['unsupported_volatile_formulas_present'],['external_links_present'],['external_links_present','unsupported_volatile_formulas_present']])
def test_only_existing_complete_rewrite_plan_admits_source(reasons,monkeypatch):
 import xl_volatile_formula_remediation as M
 observed=[]
 monkeypatch.setattr(M,'build_plan',lambda path:observed.append(path)or dict(status='eligible',unresolved=[]))
 assert eligible_source(dict(route='unsupported',reason_codes=reasons),'/bound-original')=='verified_rewrite_plan'
 assert observed==['/bound-original']

@pytest.mark.parametrize('fault',['unresolved','ineligible','unknown_reason','mixed_tables','empty'])
def test_rewrite_eligibility_does_not_relax_unresolved_source(fault,monkeypatch):
 import xl_volatile_formula_remediation as M
 reasons=['unsupported_volatile_formulas_present'];plan=dict(status='eligible',unresolved=[])
 if fault=='unresolved':plan['unresolved']=[{'kind':'today_unresolved'}]
 if fault=='ineligible':plan['status']='ineligible'
 if fault=='unknown_reason':reasons=['unsupported_functions']
 if fault=='mixed_tables':reasons+=['data_tables_present']
 if fault=='empty':reasons=[]
 monkeypatch.setattr(M,'build_plan',lambda path:plan)
 with pytest.raises(ValueError):eligible_source(dict(route='unsupported',reason_codes=reasons),'/source')
