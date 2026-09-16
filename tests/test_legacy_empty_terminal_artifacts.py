from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_staged_history as S,harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories-004/0047.json'

def test_actual_terminal_empty_history_retains_conservative_allowance():
 p=S.validate(L.binding(P));old=L.read(p['tracker'])
 assert 'writer_launched'not in old['additional_assumptions']
 assert p['staged_history']['dialogue_custody_kind']=='empty_terminal_artifacts'
 assert p['staged_history']['dialogue_rounds_upper_bound']==2
 assert p['staged_history']['new_authoring_authorized']is False
 assert len(p['audit_files'])==44

@pytest.mark.parametrize('defect',['zero','one','writer','reviewer','paraphraser','applied','unfinished','review_file','claims','pack'])
def test_no_missing_metadata_can_reset_consumption_or_conceal_operations(tmp_path,defect):
 p=L.read(L.binding(P));real=L.read;aa=next(k for k in p['staged_history']['namespaces']if k.endswith('additional-assumptions'))
 if defect in {'zero','one'}:p['staged_history']['dialogue_rounds_upper_bound']=0 if defect=='zero'else 1
 if defect=='review_file':p['staged_history']['namespaces'][aa]['review.json']={'path':'/absent','sha256':'0'*64}
 f=tmp_path/'p.json';f.write_text(json.dumps(p))
 def read(b):
  v=real(b)
  if b==p['tracker']:
   v=copy.deepcopy(v)
   if defect in {'writer','reviewer','paraphraser'}:v['additional_assumptions'][defect+'_launched']=True
   if defect=='applied':v['additional_assumptions']['apply']=aa+'/apply.json'
   if defect=='unfinished':v['status']='running'
  if defect=='claims'and b['path']==aa+'/claims.json':v={**v,'claims':[{'id':'consumed'}]}
  if defect=='pack'and b['path']==aa+'/writer_pack.json':v={**v,'claims':[{'id':'consumed'}]}
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):S.validate(L.binding(f))
