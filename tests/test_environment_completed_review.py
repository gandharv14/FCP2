import json,sys,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import environment_gate_context as M
from scripts.fleet.disclosure_command_continue import binding,read,require


def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)

def fixture(tmp_path,monkeypatch):
 files={}
 for n in ['C','I','A','entry','descriptor']:
  p=tmp_path/(n+'.py');p.write_text(n);files[n]=p
 env=(SimpleNamespace(__file__=str(files['C'])),SimpleNamespace(__file__=str(files['I'])),None,None,None,{},tmp_path,{})
 root=tmp_path/'ops';home=tmp_path/'home';first=put(home/'first-step/outcome.json',{'first':True});firstcp=put(home/'first-step/result.json',{})
 original=SimpleNamespace(dependencies=lambda p:[p['source']]);source=put(tmp_path/'source.json',{});old=dict(operations=str(root),home=str(home),source=source)
 plan=put(tmp_path/'old-plan.json',old);job={'original':'job'};actual=dict(job,outcome=first,operations=str(root));put(home/'authoring-job.json',actual);put(root/'job.json',actual)
 p=dict(plan=plan,entry=binding(files['entry']),source=source);values={first['path']:(firstcp,{})};cp=firstcp
 A=SimpleNamespace(__file__=str(files['A']),SCHEMA='actual-adapter',contract=lambda *args:3300)
 for i in range(3):
  d=root/('%03d'%i);args=dict(checkpoint=cp,descriptor=binding(files['descriptor']));intent=put(d/'intent.json',dict(action='step',code=binding(files['I']),arguments=args))
  log=d/'step.log';log.write_text('scripts.batch.harbor_admission.AdmissionError: review has blocking findings\n'if i==2 else'')
  guard=put(d/'resource.json',dict(intent=intent,returncode=1 if i==2 else 0,failure=None,log=binding(log)))
  out=put(d/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=guard,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None)if i==2 else dict(status='COMPLETED_OPERATION',resource=guard))
  side=dict(schema_version=A.SCHEMA,adapter=binding(files['A']),producer_code=binding(files['I']),base_code=binding(files['C']),kind='instruction',reader_plan=plan,child_wrapper=p['entry'],reader_evidence=[source],intent=intent,original_command=['actual'],selected_command=[sys.executable,'-I','-B',str(files['entry']),'owning-child','--plan',plan['path'],'--plan-sha256',plan['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']],timeout_seconds=3300,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False)
  put(d/'declared-runtime-adapter.json',side)
  if i<2:
   v=dict(status='STEP_COMPLETE',next_step='disclosure-review'if i==1 else'disclosure-build',descriptor=args['descriptor']);cp=put(d/'result.json',v);values[out['path']]=(cp,v)
  if i==1:p['build']=dict(outcome=out,result=cp,descriptor=args['descriptor'])
  if i==2:p['failed_review']=out
 put(root/'hold.json',dict(outcome=p['failed_review']))
 D=SimpleNamespace(successful=lambda row,env:values[row['path']]);E=SimpleNamespace(binding=binding,checked=read,require=require)
 # Stop at the independent Call validator; every preceding guard check is real.
 class Boundary(Exception):pass
 def load(*args):raise Boundary('original rejected Call validator reached')
 E.module=load;p['rejection']=put(tmp_path/'rejection.json',{'implementation':source})
 return p,old,original,D,env,job,A,E,Boundary


def test_contiguous_original_prefix_reaches_independent_call_validation(tmp_path,monkeypatch):
 p,old,O,D,env,job,A,E,Boundary=fixture(tmp_path,monkeypatch)
 with pytest.raises(Boundary):M.completed_review_prefix(E,p,old,O,D,env,job,A)

@pytest.mark.parametrize('fault',['gap','extra','predecessor','build','guard','failure','effect','adapter','hold','job'])
def test_changed_guard_or_later_effect_fails_before_call_reader(tmp_path,monkeypatch,fault):
 p,old,O,D,env,job,A,E,Boundary=fixture(tmp_path,monkeypatch);root=Path(old['operations']);d=root/'002'
 if fault=='gap':(root/'001').rename(root/'009')
 elif fault=='extra':(root/'unexpected').write_text('')
 elif fault=='predecessor':v=read(binding(d/'intent.json'));v['arguments']['checkpoint']={'foreign':True};put(d/'intent.json',v)
 elif fault=='build':p['build']['result']=p['build']['outcome']
 elif fault=='guard':v=read(binding(d/'resource.json'));v['returncode']=0;put(d/'resource.json',v)
 elif fault=='failure':(d/'step.log').write_text('TimeoutError')
 elif fault=='effect':put(d/'result.json',{})
 elif fault=='adapter':v=read(binding(d/'declared-runtime-adapter.json'));v['semantic_budget_reset']=True;put(d/'declared-runtime-adapter.json',v)
 elif fault=='hold':put(root/'hold.json',{'outcome':p['build']['outcome']})
 else:put(root/'job.json',{'foreign':'job'})
 with pytest.raises((ValueError,KeyError)):M.completed_review_prefix(E,p,old,O,D,env,job,A)
