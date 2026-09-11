"""Private merge preview only; never qualification or campaign mutations."""
from pathlib import Path
import importlib.util,subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'tests/test_0184_dialogue_metadata_spans.py'
spec=importlib.util.spec_from_file_location('existing_metadata_test',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
SCRIPT=module.SCRIPT.replace('0184-dialogue-metadata-development/.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py','shared-warning-integration/.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py')
SCRIPT=SCRIPT.replace("len(old['faults'])==177", "len(old['faults'])==168").replace("warning['count']==177", "warning['count']==168").replace("'junior_row_label':1,'source_row_label':9", "'junior_row_label':1")
SCRIPT=SCRIPT.replace("if mode in ('actual','apply','static'):", "if mode in ('actual','apply','static','actual_extra'):")
SCRIPT=SCRIPT.replace("if mode in ('apply','static'):", "if mode=='actual_extra':\n  senior=next(t for t in v.parse_turns(text) if v.is_senior(t['speaker']))\n  changed=text.replace(senior['text'],senior['text']+' Set the computed result to 1.',1)\n  bad=v.check_draft(changed,plan['claims'],Path(a['task_dir']),source_workbook=a['source'])\n  assert not bad['passed'] and any('numeric literal 1' in f for f in bad['faults'])\n if mode in ('apply','static'):")
# Shared row-budget code already licenses one exact source-label occurrence;
# the metadata helper must leave those occurrences to that existing budget.
SCRIPT=SCRIPT.replace("if mode in ('wrong_source'", "if mode in ('senior','senior_extra','senior_duplicate','wrong_source'")
@pytest.mark.parametrize('mode',['actual','actual_extra','apply','hash','junior','senior','junior_extra','senior_extra','senior_duplicate','wrong_source','wrong_target','missing_atom','formula','junior_wrong_scope','unbound_quote','overlapping_budget'])
def test_preview_source_metadata_semantics(mode):
 r=subprocess.run([sys.executable,'-B','-c',SCRIPT,mode],cwd=ROOT,text=True,capture_output=True)
 assert r.returncode==0,r.stderr
 assert r.stdout.strip()=='PASS'
