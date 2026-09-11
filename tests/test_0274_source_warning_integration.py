"""Immutable staged validator integration; all real campaign data read-only."""
from pathlib import Path
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROGRAM=r'''
from pathlib import Path
from openpyxl.utils.exceptions import InvalidFileException
import sys,copy
F=Path.cwd()/'diagnostics/requested-fixes-20260910'
sys.path.insert(0,str(F.parent/'authoring-resume-20260910/runner'))
from author_workbook import imports,checked,binding
root=F/'code-0274-source-warning-v2';base,C,_=imports(root)
from scripts.batch import harbor_dialogue_source_warnings as H,harbor_instruction_worker as W
r=checked(binding(F/'0274-post-partition-authoring/017-dialogue-review/result.json'));s=C.codec.decode(r['selection'])
p=Path(s.content.dialogue_claims['path']).parent;rd=p/'round-01'
b=[binding(x) for x in (rd/'review.json',p/'claims.json',rd/'draft.md',p/'draft_template.md',rd/'writer.filled.md',rd/'paraphraser.filled.md')]+[s.content.bindings['source_file']]
proof=H.validate(*b,task_dir=s.content.stage,root=root);review=checked(b[0]);claims=checked(b[1]);draft=Path(b[2]['path']).read_text();v=W.producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
assert not v.review_passed(review,claims,draft_text=draft)
mode=sys.argv[1]
if mode=='proof':proof['claim_count']-=1
elif mode=='review':review=copy.deepcopy(review);review['accuracy']['claims'][0]['must_say']='contradicted'
elif mode=='draft':draft+='\nUnsupported statement.\n'
elif mode=='claims':claims=copy.deepcopy(claims);claims['claims'].pop()
elif mode=='source':proof=copy.deepcopy(proof);proof['evidence'][6]=proof['evidence'][0]
try:
 accepted=v.review_passed(review,claims,draft_text=draft,source_warning_proof=proof)
except (ValueError,InvalidFileException):accepted=False
assert accepted==(mode=='positive'),mode
assert checked(b[0])['passed'] is False
print('PASS')
'''
@pytest.mark.parametrize('mode',['positive','proof','review','draft','claims','source'])
def test_real_proof_only_waives_exact_retained_warning(mode):
    if not (ROOT/'diagnostics/requested-fixes-20260910/code-0274-source-warning-v2').exists():pytest.skip('immutable campaign fixture unavailable')
    run=subprocess.run([sys.executable,'-B','-c',PROGRAM,mode],cwd=ROOT,text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    assert run.stdout.strip()=='PASS'

APPLY=PROGRAM[:PROGRAM.index("mode=sys.argv[1]")].replace("code-0274-source-warning-v2","code-0274-source-warning-v3")+r'''
import shutil,json
copy_stage=Path(sys.argv[1])/'0274-outputs';shutil.copytree(s.content.stage,copy_stage)
proof=H.validate(*b,task_dir=copy_stage,root=root)
result=v.apply(copy_stage,Path(b[2]['path']),claims,review,require_review_pass=True,skip_smoke=True,source_warning_proof=proof)
assert result['review_passed'] and result['draft_passed']
assert {x['code'] for x in result['review_warnings']}=={'source_formula_operand','frozen_junior_prefix','dialogue_style_only'}
serialized=json.dumps(result['review_warnings']);assert 'SUBTOTAL' not in serialized and '150' not in serialized and str(F) not in serialized
assert H.revalidate(proof,review,claims,draft,root=root)==proof
marker=json.loads(Path(result['marker']).read_text());assert marker['review_warnings']==result['review_warnings']
assert checked(b[0])['passed'] is False
print('PASS')
'''
def test_actual_apply_on_copy_preserves_revalidation_and_private_proof(tmp_path):
    if not (ROOT/'diagnostics/requested-fixes-20260910/code-0274-source-warning-v3').exists():pytest.skip('immutable campaign fixture unavailable')
    run=subprocess.run([sys.executable,'-B','-c',APPLY,str(tmp_path)],cwd=ROOT,text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    assert run.stdout.strip()=='PASS'
