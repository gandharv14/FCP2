from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_staged_history as S
H=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-001'
@pytest.fixture
def actual():return json.loads((H/'0032-history-002.json').read_text())
def test_actual_baseline_reports_and_review_artifacts_prove_terminal_custody(actual):
 assert S.validate(L.binding(H/'0032-history-002.json'))==actual
 assert actual['staged_history']['dialogue_rounds_upper_bound']==1
 assert actual['completed_builds']['scanned_source']==actual['baseline']
 with pytest.raises(ValueError,match='source-only'):L.author_survey(actual)
@pytest.mark.parametrize('defect',['summary_false','summary_foreign','review_fail','draft_fail','notes','marker','mask_missing','mask_extra','mask_nonzero','mask_wrong_book','mask_duplicate','scan_source','old_schema','round_reset'])
def test_exact_artifacts_cannot_be_inferred_from_contradictions(actual,tmp_path,defect):
 p=copy.deepcopy(actual);real=L.read
 if defect=='scan_source':p['completed_builds']['scanned_source']=p['original']
 if defect=='old_schema':p['completed_builds']['schema_version']='harbor-legacy-completed-mcp-builds/v1'
 if defect=='round_reset':p['staged_history']['dialogue_rounds_upper_bound']=0
 path=tmp_path/'proof.json';path.write_text(json.dumps(p))
 def read(row):
  v=real(row)
  if row==p['tracker']:
   if defect=='summary_false':v['additional_assumptions']['review_passed']=False
   if defect=='summary_foreign':v['additional_assumptions']['run']='/foreign'
   if defect=='notes':v['additional_assumptions']['notes_sha256']='0'*64
  if row['path'].endswith('/review.json')and defect=='review_fail':v['accuracy']['verdict']='fail'
  if row['path'].endswith('/apply.json')and defect=='draft_fail':v['draft_passed']=False
  if row['path'].endswith('/tests/dialogue-applied.json')and defect=='marker':v['instruction_sha256']='0'*64
  if row in [p['completed_builds']['maskability'],p['completed_builds']['leakscan']]:
   if defect=='mask_missing':v.pop('unapproved_value_leaks')
   if defect=='mask_extra':v['undocumented_waiver']=True
   if defect=='mask_nonzero':v['unapproved_value_leaks']=['actual finding']
   if defect=='mask_wrong_book':v['workbook_scanned']='inputs_out/9999-inputs.xlsx'
  if row['path'].endswith('/mcp/mask_cells.json')and defect=='mask_duplicate':v[-1]=v[0]
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises((ValueError,KeyError)):S.validate(L.binding(path))
