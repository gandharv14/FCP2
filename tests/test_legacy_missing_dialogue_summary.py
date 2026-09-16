from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_staged_history as S,harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories-007/0162.json'

def test_actual_complete_history_uses_recorded_review_not_invented_summary():
 p=L.read(L.binding(P));v=S.validate(L.binding(P),at_ns=p['created_at_ns'])
 assert L.read(v['tracker']).get('additional_assumptions')is None
 assert v['staged_history']['dialogue_rounds_upper_bound']==1
 assert v['staged_history']['new_authoring_authorized']is False
 assert len(v['audit_files'])==20

@pytest.mark.parametrize('defect',['round-reset','existing-summary','review','draft','apply','instruction','notes','marker','manifest','validation','claims','extra-history'])
def test_exact_terminal_history_and_consumption_remain_required(tmp_path,defect):
 p=L.read(L.binding(P));real=L.read;aa=next(k for k in p['staged_history']['namespaces']if k.endswith('additional-assumptions'))
 if defect=='round-reset':p['staged_history']['dialogue_rounds_upper_bound']=0
 if defect=='extra-history':p['staged_history']['namespaces'][aa]['unknown.json']={'path':'/absent','sha256':'0'*64}
 q=tmp_path/'p.json';q.write_text(json.dumps(p))
 def read(row):
  v=copy.deepcopy(real(row));path=row['path']
  if defect=='existing-summary' and row==p['tracker']:v['additional_assumptions']={'review_passed':False}
  if defect=='review' and path==aa+'/review.json':v['passed']=False
  if defect=='draft' and path==aa+'/draft-check.json':v['faults']=['unresolved']
  if path==aa+'/apply.json':
   if defect=='apply':v['applied']=False
   if defect=='instruction':v['instruction_sha256']='0'*64
   if defect=='notes':v['notes']='/foreign'
  if defect=='marker' and path.endswith('/tests/dialogue-applied.json'):v['review_passed']=False
  if defect=='manifest' and path.endswith('/disclosure-faithfulness.json'):v['manifest_sha256']='0'*64
  if defect=='validation' and path.endswith('/disclosure-faithfulness-validation.json'):v['valid']=False
  if defect=='claims' and path==aa+'/claims.json':v['workbook']='0000'
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):S.validate(L.binding(q),at_ns=p['created_at_ns'])
