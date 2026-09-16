"""Actual 0274 proof and closed negative variants, isolated from imported roots."""
from pathlib import Path
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROGRAM=r'''
import importlib.util,json,copy,hashlib
from pathlib import Path
F=Path.cwd()/'diagnostics/requested-fixes-20260910'
from scripts.batch import harbor_transition_codec as codec
def binding(path):
 path=Path(path);return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
def checked(bound):
 assert binding(bound['path'])==bound
 return json.loads(Path(bound['path']).read_text())
d={'binding':binding,'checked':checked,'codec':codec,'INITIAL':{'path':str(F/'0274-partition-consume-reconciliation/result.json'),'sha256':'56ad06b119aa3ff4f5417d8b4e5a406c1342e51425570b5b58bf53610f2a711b'}}
mod=Path.cwd()/'scripts/batch/harbor_dialogue_source_warnings.py'
sp=importlib.util.spec_from_file_location('source_warnings_new',mod);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
p=Path.cwd()/'diagnostics/authoring-resume-20260910/authoring/0274/instruction-role-repair/producer/dialogue';r=p/'round-01'
c=d['checked'](d['INITIAL']);s=d['codec'].decode(c['selection']);source=s.content.bindings['source_file'];source={k:source[k] for k in ('path','sha256')}
b=[d['binding'](x) for x in (r/'review.json',p/'claims.json',r/'draft.md',p/'draft_template.md',r/'writer.filled.md',r/'paraphraser.filled.md')]+[source]
mode=sys.argv[1];temp=Path(sys.argv[2])
def change(index,value):
 path=temp/('changed-%s'%index);path.write_text(value if isinstance(value,str) else json.dumps(value));b[index]=d['binding'](path)
if mode in ('unknown','contradiction','missing','style','constant','index'):
 value=copy.deepcopy(d['checked'](b[0]))
 if mode=='unknown':value['accuracy']['extras'].append('An unsupported extra numeric fact appears.')
 if mode=='contradiction':value['accuracy']['claims'][0]['must_say']='contradicted'
 if mode=='missing':value['accuracy']['claims'].pop()
 if mode=='style':value['naturalness']['findings'].append('The formula sign is incorrect.')
 if mode=='constant':value['accuracy']['extras'][0]=value['accuracy']['extras'][0].replace('literal 9','literal 8')
 if mode=='index':value['accuracy']['extras'][1]=value['accuracy']['extras'][1].replace('001,','151,')
 change(0,value)
elif mode=='junior':change(5,Path(b[5]['path']).read_text().replace('How should I build','How must I build',1))
elif mode=='source':
 value=copy.deepcopy(d['checked'](b[1]));value['claims'][1]['reviewer_only']['evidence']='=SUBTOTAL(8,E12:E21)';change(1,value)
elif mode=='prefix':change(3,Path(b[3]['path']).read_text().replace('**Analyst:** Consolidated IS:','**Analyst:** Wrong sheet:',1))
try:
 proof=m.validate(*b,task_dir=s.content.stage,root=Path.cwd())
 if mode=='tamper':
  proof['claim_count']-=1;m.revalidate(proof,d['checked'](b[0]),d['checked'](b[1]),Path(b[2]['path']).read_text(),root=Path.cwd())
 assert mode=='positive','negative case accepted: '+mode
 assert proof['claim_count']==150 and len(proof['warnings'])==11 and 'passed' not in proof
 assert m.revalidate(proof,d['checked'](b[0]),d['checked'](b[1]),Path(b[2]['path']).read_text(),root=Path.cwd())==proof
except (ValueError,AssertionError) as exc:
 if mode=='positive' or str(exc).startswith('negative case accepted'):raise
print('PASS',mode)
'''

@pytest.mark.parametrize('mode',['positive','unknown','contradiction','missing','style','constant','index','junior','source','prefix','tamper'])
def test_exact_retained_evidence_and_rejection(mode,tmp_path):
    if not (ROOT/'diagnostics/requested-fixes-20260910/0274-partition-snapshot.json').exists():pytest.skip('retained authoring campaign fixture unavailable')
    result=subprocess.run([sys.executable,'-B','-c','import sys\n'+PROGRAM,mode,str(tmp_path)],cwd=ROOT,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='PASS '+mode
