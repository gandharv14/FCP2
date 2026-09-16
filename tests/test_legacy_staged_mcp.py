from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_staged_history as S
H=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/source-pool-legacy-staged-mcp-001'
@pytest.fixture(params=['0153','0084'])
def actual(request):return json.loads((H/(request.param+'-history.json')).read_text())

def test_actual_mcp_staged_custody_retains_builds_and_consumption(actual):
 assert L.validate(L.binding(H/(actual['workbook']+'-history.json')))==actual
 assert len(actual['audit_files'])==44
 assert actual['staged_history']['dialogue_rounds_upper_bound']==(0 if actual['workbook']=='0084'else 1)
 with pytest.raises(ValueError,match='source-only'):L.author_survey(actual)
 with patch.object(L,'validate',return_value=actual),pytest.raises(ValueError,match='source reconstruction only'):L.budget({},[],{},workbook=actual['workbook'],inherited_budget=None)

@pytest.mark.parametrize('defect',['mask','leak','profile','build','oracle','namespace','raw','artifact','count','mode'])
def test_mcp_staged_requires_full_deterministic_prior_evidence(actual,tmp_path,defect):
 p=copy.deepcopy(actual);real=L.read
 if defect=='namespace':p['audit_files'].pop('oracle-report.json')
 if defect=='raw':p['completed_builds']['scanned_source']['sha256']='0'*64
 if defect=='build':p['audit_files']['mcp-build-b/server.py']['sha256']='0'*64
 if defect=='count':p['retained_opaque_research_calls']=0
 if defect=='mode':p['staged_history']['dialogue_custody_kind']='unknown'
 path=tmp_path/'proof.json';path.write_text(json.dumps(p))
 def read(b):
  v=real(b)
  if defect in {'mask','leak'}and b['path'].endswith(('maskability_report.json'if defect=='mask'else 'leakscan_report.json')):v={**v,'valid':False}
  if defect=='profile'and b['path'].endswith('source_profiles.json'):v={**v,'profiles':[{}]}
  if defect=='oracle'and b['path'].endswith('oracle-report.json'):v={**v,'bundle':'/foreign'}
  if defect=='artifact'and b['path'].endswith('.metadata.json'):v={**v,'artifact':'../inputs_out/9999-inputs.xlsx'}
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises((ValueError,KeyError)):L.validate(L.binding(path))

@pytest.mark.parametrize('defect',['writer','reviewer','claims','pack','extra_file','rounds'])
def test_empty_no_launch_cannot_conceal_prior_dialogue(tmp_path,defect):
 p=json.loads((H/'0084-history.json').read_text());real=L.read
 aa=next(k for k in p['staged_history']['namespaces']if k.endswith('additional-assumptions'))
 if defect=='extra_file':p['staged_history']['namespaces'][aa]['review.json']={'path':'/absent','sha256':'0'*64}
 if defect=='rounds':p['staged_history']['dialogue_rounds_upper_bound']=1
 path=tmp_path/'proof.json';path.write_text(json.dumps(p))
 def read(b):
  v=real(b)
  if b==p['tracker']and defect in {'writer','reviewer'}:v={**v,'additional_assumptions':{**v['additional_assumptions'],defect+'_launched':True}}
  if defect=='claims'and b['path']==aa+'/claims.json':v={**v,'claims':[{'id':'unknown'}]}
  if defect=='pack'and b['path']==aa+'/writer_pack.json':v={**v,'claims':[{'id':'unknown'}]}
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):L.validate(L.binding(path))
