from pathlib import Path
import json,copy
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_staged_history as S,harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories-005'
@pytest.mark.parametrize('wb',['0049','0174'])
def test_full_actual_old_custody_remains_source_only(wb):
 p=S.validate(L.binding(H/(wb+'.json')))
 assert p['staged_history']['new_authoring_authorized']is False
 assert p['staged_history']['dialogue_rounds_upper_bound']==(2 if wb=='0049'else 1)
 assert p['new_source_reconstruction_ordinal']==2

@pytest.mark.parametrize('defect',['leak','foreign_workbook','oracle','patch','patch_hash','other_scan'])
def test_extra_scan_requires_exact_original_coverage_and_zero_findings(defect):
 p=L.read(L.binding(H/'0174.json'));a=copy.deepcopy(p['audit_files']);real=L.read
 if defect=='other_scan':a['masked-leakscan-report.json']=a['mcp-input-leakscan.json']
 def read(b):
  v=real(b)
  if b==a['mcp-input-leakscan.json']:
   if defect=='leak':v={**v,'unapproved_value_leaks':[{'cell':'A1'}]}
   if defect=='foreign_workbook':v={**v,'workbook_scanned':'inputs_out_mcp/9999-inputs.xlsx'}
  if b==a['oracle-report.json']and defect=='oracle':v={**v,'workbook_checks':{}}
  if b==a['leak_patch.json']:
   if defect=='patch':v={**v,'proposals':[{'cell':'A1'}]}
   if defect=='patch_hash':v={**v,'source_report_sha256':'0'*64}
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):S.terminal_masked_scan_evidence(p,a)

@pytest.mark.parametrize('defect',['edited_notes','edited_draft','stale_digest','hidden_sentence'])
def test_notes_projection_is_exact(tmp_path,defect):
 p=L.read(L.binding(H/'0174.json'));ns=p['staged_history']['namespaces'];draft=next(v['draft.md']for k,v in ns.items()if k.endswith('additional-assumptions'));notes=ns[p['staged_history']['package']]['environment/additional-assumptions.md'];d=tmp_path/'draft.md';n=tmp_path/'notes.md';d.write_bytes(Path(draft['path']).read_bytes());n.write_bytes(Path(notes['path']).read_bytes());db=L.binding(d);nb=L.binding(n)
 if defect=='edited_notes':n.write_text(n.read_text().replace('Battery','Modified',1));nb=L.binding(n)
 if defect=='edited_draft':d.write_text(d.read_text()+'\nDifferent instruction.');db=L.binding(d)
 if defect=='stale_digest':n.write_text('mutated')
 if defect=='hidden_sentence':n.write_text(n.read_text()+'<!-- A different hidden instruction. -->\n');nb=L.binding(n)
 with pytest.raises(ValueError):S.notes_from_retained_draft(db,nb)
