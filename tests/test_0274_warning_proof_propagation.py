"""Real-call proof transfer; campaign and frozen artifacts remain read-only."""
from pathlib import Path
import subprocess, sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROGRAM=r'''
from pathlib import Path
from dataclasses import replace
import sys,copy,shutil
F=Path.cwd()/'diagnostics/requested-fixes-20260910'
sys.path.insert(0,str(F.parent/'authoring-resume-20260910/runner'))
from author_workbook import imports,checked,binding
root=Path(sys.argv[1])/'code';shutil.copytree(F/'code-0274-source-warning-v6',root)
shutil.copytree(F/'0274-warning-development',root,dirs_exist_ok=True)
base,C,_=imports(root)
from scripts.batch import harbor_complete_readiness as G,harbor_readiness as S,harbor_dialogue_source_warnings as H
r=checked(dict(path=str(F/'0274-source-warning-successor-qualification/003-runtime/result.json'),sha256='fd5b9d34fd29c680e8ad234ad3ad654bbb56aab286afc978dd1692694b144a93'))
s=C.codec.decode(r['selection'])
result=G.check_dialogue(s,root=root);proof=result['source_warning_proof']
assert proof['evidence'][0]==s.content.dialogue_review
assert result['source_warning_review_copy']==dict(parsed=s.semantic.dialogue_call.parsed,selected=s.content.dialogue_review)
assert s.semantic.dialogue_call.parsed['path']!=s.content.dialogue_review['path']
assert S.prose_content(s.content,root=root,source_warning_proof=proof)['dialogue_claim_coverage']
def rejects(fn):
 try:fn()
 except (Exception,SystemExit):return
 raise AssertionError('changed or absent authority accepted')
rejects(lambda:S.prose_content(s.content,root=root))
for index in (0,1,2,6):
 changed=copy.deepcopy(proof);changed['evidence'][index]=proof['evidence'][(index+1)%7]
 rejects(lambda:S.prose_content(s.content,root=root,source_warning_proof=changed))
# Even identical copied bytes need explicit semantic transfer to the selected path.
reviewcopy=Path(sys.argv[1])/'review-copy.json';shutil.copyfile(s.content.dialogue_review['path'],reviewcopy)
changed=copy.deepcopy(proof);changed['evidence'][0]=binding(reviewcopy)
rejects(lambda:S.prose_content(s.content,root=root,source_warning_proof=changed))
# Broken actual response provenance cannot manufacture a static privilege.
badcall=replace(s.semantic.dialogue_call,response=s.semantic.dialogue_call.request)
bad=replace(s,semantic=replace(s.semantic,dialogue_call=badcall))
rejects(lambda:G.check_dialogue(bad,root=root))
# Publication copy gets its own task-directory proof from the same real Calls.
copy_stage=Path(sys.argv[1])/'0274-outputs';shutil.copytree(s.content.stage,copy_stage)
cloned=replace(s,content=replace(s.content,stage=copy_stage))
rederived=G.check_dialogue(cloned,root=root)['source_warning_proof']
assert rederived['task_dir']==str(copy_stage.resolve()) and rederived['task_dir']!=proof['task_dir']
assert H.revalidate(rederived,checked(cloned.content.dialogue_review),checked(cloned.content.dialogue_claims),Path(cloned.content.dialogue_reviewed_draft['path']).read_text(),root=root)==rederived
assert checked(s.content.dialogue_review)['passed'] is False
print('PASS')
'''
def test_actual_provenance_transfer_and_negative_bindings(tmp_path):
    if not (ROOT/'diagnostics/requested-fixes-20260910/0274-warning-development').exists():
        pytest.skip('staged real campaign fixture unavailable')
    result=subprocess.run([sys.executable,'-B','-c',PROGRAM,str(tmp_path)],cwd=ROOT,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='PASS'
