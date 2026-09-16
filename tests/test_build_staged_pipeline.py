from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.fleet import build_staged_pipeline as B
from scripts.fleet.migration import binding
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-001'
@pytest.mark.parametrize('fault',[None,'duplicate','foreign_source','foreign_host','native_prior','expired_native'])
def test_exact_history_builder_generates_only_finite_data(tmp_path,fault):
 p=json.loads((H/'preparation-selection.json').read_text());real=B.checked
 if fault=='duplicate':p['candidates'].append(copy.deepcopy(p['candidates'][0]))
 if fault=='foreign_source':p['candidates'][0]['source']['sha256']='0'*64
 if fault=='foreign_host':p['candidates'][0]['host']='foreign'
 selected=tmp_path/'selection.json';selected.write_text(json.dumps(p));out=tmp_path/'output';observed=real(next(iter(p['native_history'].values())))['observed_at']
 def checked(row):
  v=real(row)
  if row in p['native_history'].values():
   if fault=='native_prior':v['histories']['0032']=['prior attempt']
   if fault=='expired_native':v['observed_at']=observed-10000
  return v
 with patch.object(B,'checked',side_effect=checked),patch.object(B.time,'time',return_value=observed+60),patch.object(B.A,'issue',return_value=p['authorization']):
  if fault:
   with pytest.raises(ValueError):B.prepare(binding(selected),out)
   assert not out.exists()
  else:
   result=B.prepare(binding(selected),out);prepared=real(result);assert len(prepared['rows'])==1 and prepared['workers_started']==0
   job=real(prepared['rows'][0]['job']);plan=real(job['inputs'][0]);source=real(plan['source_plan']);author=real(plan['author_template'])
   assert source['next_attempt']==source['cumulative_reconstruction_limit']==2 and source['models']==0
   assert job['physical_model_policy']==p['physical_model_policy']and job['result']==author['home']+'/result.json'
   assert plan['total_seconds']==job['timeout_seconds']==86400 and plan['author_memory_bytes']==8*1024**3
   assert 'legacy_research_history'not in author and author['reconstruction']['handoff']is None
   with pytest.raises(ValueError,match='consumed'):B.prepare(binding(selected),out)
