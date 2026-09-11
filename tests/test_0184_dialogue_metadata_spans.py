"""Exact span licensing; no global allowance for a coincident numeric answer."""
from pathlib import Path
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=r'''
from pathlib import Path
from types import SimpleNamespace
import sys,importlib.util,json,copy
F=Path.cwd()/'diagnostics/requested-fixes-20260910';sys.path.insert(0,str(F.parent/'authoring-resume-20260910/runner'))
from author_workbook import imports,binding,checked
root=F/('code-0184-source-metadata-v2' if sys.argv[1]=='static' else 'code-0184-dialogue-partition-v1');imports(root)
from scripts.batch import harbor_dialogue_partition_review as R
R.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
p=F/'0184-dialogue-metadata-development/.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py';spec=importlib.util.spec_from_file_location('span_validator',p);v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
mode=sys.argv[1]
if mode in ('actual','apply','static'):
 h=F.parent/'authoring-resume-20260910/authoring/0184/instruction-local-restart/dialogue-partition/round-01';a=checked(binding(h/'authority.json'));plan=checked(a['plan']);text=(h/'draft.md').read_text()
 old=v.check_draft(text,plan['claims'],Path(a['task_dir']));assert len(old['faults'])==177 and not old['passed']
 now=v.check_draft(text,plan['claims'],Path(a['task_dir']),source_workbook=a['source']);assert now['passed'] and now['turns']==2104
 assert checked(binding(h/'draft-mechanics.json'))['passed'] is False
 assert 'metadata_warnings' not in old
 warning=now['metadata_warnings'];assert warning['source']==a['source'] and warning['count']==177
 from collections import Counter
 roles=Counter()
 for item in warning['items']:
  assert set(item)=={'code','record_id','turn_role','span_role','character_span','count'}
  roles[item['span_role']]+=item['count']
 assert roles=={'junior_scope':166,'junior_sheet_header':1,'junior_row_label':1,'source_row_label':9}
 if mode in ('apply','static'):
  import tempfile,shutil
  # This is a synthetic review fixture solely for copied-stage API propagation.
  # It is never recorded as an actual review or used in the campaign.
  review=dict(round=1,passed=True,accuracy=dict(verdict='pass',extras=[],cell_refs_in_senior_turns=[],claims=[dict(record_id=c['record_id'],must_say='entailed',findings=[]) for c in plan['claims']['claims']]),naturalness=dict(verdict='pass',findings=[]))
  with tempfile.TemporaryDirectory() as directory:
   directory=str(Path(directory).resolve());task=Path(directory)/'0184-outputs';shutil.copytree(a['task_dir'],task)
   report=v.apply(task,h/'draft.md',plan['claims'],review,True,True,source_workbook=a['source'])
   assert report['draft_passed'] and report['review_passed']
   if mode=='static':
    from dataclasses import replace
    from scripts.batch import harbor_readiness as S,harbor_transition_codec as K
    reviewpath=Path(directory)/'synthetic-review-fixture.json';reviewpath.write_text(json.dumps(review))
    selection=K.decode(a['selection']).content
    selected=replace(selection,stage=task,dialogue_review=binding(reviewpath),dialogue_reviewed_draft=binding(h/'draft.md'))
    result=S.prose_content(selected,root=root)
    assert result=={'disclosure_mechanics':True,'naturalization_spans_and_budget':True,'dialogue_claim_coverage':True}

elif mode=='hash':
 try:v.checked_metadata_source({'path':str(p),'sha256':'0'*64})
 except ValueError:pass
 else:raise AssertionError('forged source accepted')
else:
 target=v.disclose.parse_ref("'Cut #1'!D10",'');source=v.disclose.parse_ref("'Total Market'!D191",'')
 c=dict(record_id='x',sheet='Cut #1',row_label='Target',junior_scope=["'Cut #1'!D10"],must_say=['the row labelled "MORTGAGE 1 INC"'],reviewer_only=dict(entry='source_selection',source='convention_detector',cells=["'Cut #1'!D10"]))
 gold=SimpleNamespace(formula={target:"='Total Market'!D191"},row_label=lambda key:{target:'Target',source:'MORTGAGE 1 INC'}.get(key,''))
 junior=mode.startswith('junior');scope=v.junior_scope_text(c)
 text=('Cut #1: '+scope+': How should I use the row labelled "Target"?') if junior else 'Use the row labelled "MORTGAGE 1 INC".'
 if mode in ('junior_extra','senior_extra'):text+=' Set the result to 1.'
 if mode=='senior_duplicate':text+=' Repeat the row labelled "MORTGAGE 1 INC".'
 if mode=='wrong_source':gold.formula[target]="='Total Market'!D192"
 if mode=='wrong_target':c['row_label']='Other'
 if mode=='overlapping_budget':
  c['row_label']='Target 1';gold.row_label=lambda key:{target:'Target 1',source:'MORTGAGE 1 INC'}.get(key,'')
 if mode=='missing_atom':c['must_say']=[]
 if mode=='formula':gold.formula[target]="='Total Market'!D191+1"
 if mode=='junior_wrong_scope':text=text.replace('D10','D11')
 if mode=='unbound_quote':text='The figure is "MORTGAGE 1 INC".'
 masked=v.source_metadata_mask(text,c,gold,junior=junior)
 if mode in ('wrong_source','wrong_target','missing_atom','formula','junior_wrong_scope','unbound_quote','overlapping_budget'):assert masked==text
 elif mode in ('junior_extra','senior_extra'):assert 'Set the result to 1.' in masked and masked.count('1')==1
 elif mode=='senior_duplicate':assert masked.count('MORTGAGE 1 INC')==1
 else:assert '1' not in masked
print('PASS')
'''
@pytest.mark.parametrize('mode',['actual','apply','static','hash','junior','senior','junior_extra','senior_extra','senior_duplicate','wrong_source','wrong_target','missing_atom','formula','junior_wrong_scope','unbound_quote','overlapping_budget'])
def test_scoped_source_metadata_only(mode):
 if not (ROOT/'diagnostics/requested-fixes-20260910/code-0184-dialogue-partition-v1').exists():pytest.skip('immutable campaign fixture unavailable')
 r=subprocess.run([sys.executable,'-B','-c',SCRIPT,mode],cwd=ROOT,text=True,capture_output=True)
 assert r.returncode==0,r.stderr
 assert r.stdout.strip()=='PASS'
