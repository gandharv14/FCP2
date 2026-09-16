"""Actual immutable first-round evidence; no production mutation or model calls."""
from pathlib import Path
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROGRAM=r'''
import sys,json,copy
from pathlib import Path
from dataclasses import replace
H=Path.cwd()/'diagnostics/authoring-queue-extension-20260911T185027Z/0150-warning-qualification';root=H/'code-v1';sys.path.insert(0,str(root))
from scripts import harbor_instruction_campaign as I
from scripts.batch import harbor_dialogue_first_round_warning as F,harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
book=H.parent/'authoring/0150';r=read(record(book/'dispatcher-v2/after-disk-003/018/result.json'));w=replace(I.worker_decode(read(r['descriptor'])['worker']),root=root);s=I.codec.decode(r['selection']);before=read(record(w.context.tracker_root/'0150.json'))
d=w.run/'producer/dialogue';rd=d/'round-01';claims=read(record(d/'claims.json'));review=read(record(rd/'review.json'));draft=(rd/'draft.md').read_text();v=W.producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
mode=sys.argv[1]
if mode=='positive':
 p=F.history(w,before,record(book/'frozen-code.json'));assert len(p['records'])==111
 assert v.review_passed(review,claims,draft_text=draft) and len(v.review_warnings(review,claims,draft_text=draft))==2
elif mode=='apply-copy':
 import shutil
 stage=Path(sys.argv[2])/'stage';shutil.copytree(s.content.stage,stage)
 out=v.apply(stage,rd/'draft.md',claims,review,require_review_pass=True,skip_smoke=True,source_workbook=s.content.bindings['source_file'])
 assert out['review_passed'] and out['draft_passed']
 assert read(record(rd/'review.json'))['passed'] is False
elif mode.startswith('history-'):
 if mode=='history-specialized':w=replace(w,retry=record(book/'frozen-code.json'))
 elif mode=='history-source':before=copy.deepcopy(before);before['bindings']['original_raw_source']['sha256']='0'*64
 elif mode=='history-code':
  original=F.inventory
  def altered(root_):
   result=original(root_)
   if root_==root:result['scripts/unrelated.py']='0'*64
   return result
  F.inventory=altered
 elif mode=='history-completed':
  original=F.read
  def changed(binding):
   result=original(binding)
   if binding['path'].endswith('/dialogue-writer/complete.json'):result=copy.deepcopy(result);result['status']='unknown'
   return result
  F.read=changed
 try:F.history(w,before,record(book/'frozen-code.json'))
 except ValueError:pass
 else:raise AssertionError(mode+' accepted')
else:
 if mode=='claim-missing':claims['claims'].pop()
 elif mode=='entailment':review['accuracy']['claims'][0]['must_say']='contradicted'
 elif mode=='extra':review['accuracy']['extras'].append('unsupported answer')
 elif mode=='unknown-style':review['naturalness']['findings'].append('Incorrect arithmetic.')
 elif mode=='unknown-accuracy':review['accuracy']['claims'][1]['findings'].append('Incorrect arithmetic.')
 elif mode=='scope':draft=draft.replace('LBO: For `LBO!O17`:', 'Other: For `LBO!O17`:')
 elif mode=='arithmetic':draft=draft.replace('product is less debt','debt is less product')
 elif mode=='source-equation':claims['claims'][0]['reviewer_only']['evidence']='=O65-PRODUCT(EBITDA,D7)+O55'
 elif mode=='style-quote':review['naturalness']['findings'][0]+=' The result is incorrect.'
 assert not v.review_passed(review,claims,draft_text=draft),mode
print('PASS')
'''
@pytest.mark.parametrize('mode',['positive','apply-copy','history-specialized','history-source','history-code','history-completed','claim-missing','entailment','extra','unknown-style','unknown-accuracy','scope','arithmetic','source-equation','style-quote'])
def test_actual_first_round_warning_is_closed(mode,tmp_path):
 p=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/0150-warning-qualification/code-v1'
 if not p.exists():pytest.skip('immutable selected fixture unavailable')
 run=subprocess.run([sys.executable,'-I','-B','-c',PROGRAM,mode,str(tmp_path)],cwd=ROOT,text=True,capture_output=True)
 assert run.returncode==0,run.stderr
 assert run.stdout.strip()=='PASS'
