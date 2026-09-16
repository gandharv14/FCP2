from pathlib import Path
from types import SimpleNamespace as NS
import json,pytest
from scripts.fleet import retained_aggregate_context as M,advance_authoring_v8 as D
from scripts.fleet.migration import binding

def keep(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)

@pytest.mark.parametrize('fault',[None,'guard','sidecar','previous','extra','endpoint','models'])
def test_actual_original_completed_guards_and_rejection(tmp_path,fault):
 home=tmp_path/'author';ops=tmp_path/'run/dispatcher-v2/author';prior=keep(home/'permit/result.json',{})
 p=dict(output=str(home),helper=binding(M.__file__));row=keep(tmp_path/'plan',p);outcomes=[]
 for i,folder in enumerate([home/'first-step',ops/'000']):
  intent=keep(folder/'intent.json',dict(action='step',arguments=dict(checkpoint=prior,descriptor={}),models=bool(fault=='models'and i==0),commands=False,containers=False))
  log=folder/'step.log';log.write_text('original completed command');guard=keep(folder/'resource.json',dict(intent=intent,log=binding(log),returncode=0,failure=None))
  result=keep(folder/'result.json',dict(status='STEP_COMPLETE'if i==0 else'DIALOGUE_REPAIR_REQUIRED',next_step='disclosure-commit'if i==0 else'dialogue-review'))
  out=keep(folder/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result,automatic_retry=False));outcomes.append(out)
  keep(folder/'aggregate-bootstrap.json',dict(schema_version='aggregate/v1',plan=row,helper=p['helper'],kind='instruction',intent=intent,original_command=[intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False));prior=result
 q=dict(outcome=outcomes[-1],result=prior)
 if fault=='guard':keep(ops/'000/resource.json',{})
 if fault=='sidecar':keep(ops/'000/aggregate-bootstrap.json',{})
 if fault=='previous':keep(ops/'000/intent.json',{})
 if fault=='extra':(ops/'002').mkdir()
 if fault=='endpoint':q['outcome']={}
 env=(None,NS(guard_seconds=lambda *x:3300),None,None,None,dict(run_root=str(tmp_path/'run')))
 S=NS(SCHEMA='aggregate/v1',adoption_home=lambda p:home);U=NS(retained_command=lambda c,*a:('instruction',dict(path=c[0],sha256=c[1])));dispatcher=NS(successful=lambda out,env:D.successful(out))
 if fault:
  with pytest.raises((ValueError,KeyError)):M.completed_chain(S,U,dispatcher,env,p,row,q)
 else:assert M.completed_chain(S,U,dispatcher,env,p,row,q)==outcomes

@pytest.mark.parametrize('fault',[None,'source','root','module','original','projection','guard'])
def test_closed_selected_and_original_roots(monkeypatch,fault):
 b=lambda x:dict(path=x,sha256=x)
 q=dict(helper=b('/reader'),config=b('/config'),original_snapshot=b('/old'),snapshot=b('/new'),source=b('/source'),workbook='0171')
 q.update({k:b('/'+k)for k in('original_job','outcome','result','descriptor','blocked')});p=dict(q)
 selected=dict(helper=q['helper'],plan=b('/plan'),projection=dict(environment=3,dispatcher=2,job=1),config=q['config'],snapshot=q['snapshot'],kind='completed_disclosure')
 config=dict(code=q['original_snapshot'],workbook='0171');data={'/plan':q,'/config':config,'/old':dict(root='/code/old'),'/new':dict(root='/code/new')}
 mods=[NS(__file__='/code/'+r+'/'+str(i))for i,r in enumerate(('old','old','old','new','new'))];mods[0].SECONDS=3300;mods[0].MEMORY=8589934592
 env=(*mods,config,Path('/code/new'),None)
 monkeypatch.setattr(M,'read',lambda r:data[r['path']]);monkeypatch.setattr(M,'binding',lambda p:b('/reader'))
 if fault=='source':p['source']={}
 if fault=='root':env=(*env[:6],Path('/foreign'),None)
 if fault=='module':mods[3].__file__='/code/old/worker'
 if fault=='original':config['code']=q['snapshot']
 if fault=='projection':selected['projection']['job']=8
 if fault=='guard':mods[0].SECONDS=6600
 if fault:
  with pytest.raises(ValueError):M.validate_inventory(selected,env,p)
 else:assert M.validate_inventory(selected,env,p)==q

@pytest.mark.parametrize('reader_name',['retained_aggregate_context','retained_disclosure_context'])
@pytest.mark.parametrize('fault',[None,'extra-symbol','wrong-name','wrong-root','unrelated-code','guard','read-call','tampered'])
def test_runtime_is_two_exact_function_scopes(tmp_path,monkeypatch,fault,reader_name):
 import importlib
 M=importlib.import_module("scripts.fleet."+reader_name)
 from contextlib import nullcontext
 import sys
 old=tmp_path/'old';new=tmp_path/'new';helper=keep(tmp_path/'helper',{})
 p=dict(helper=helper,original_snapshot=keep(tmp_path/'old.json',dict(root=str(old))),snapshot=keep(tmp_path/'new.json',dict(root=str(new))))
 rows=[]
 for name,function,root in [('harbor_dialogue_semantic_retry','_history',old),('harbor_instruction_repair','validate_historical_integrity',new)]:
  f=root/'scripts/batch'/(name+'.py');f.parent.mkdir(parents=True,exist_ok=True);f.write_text('OTHER=1\ndef '+function+'(*args):\n return 1\n');target=tmp_path/(name+'.py');target.write_text(f.read_text().replace('return 1','return 2'))
  rows.append(dict(module_name='scripts.batch.'+name,names=[function],original=binding(f),selected=binding(target)))
  monkeypatch.setitem(sys.modules,'scripts.batch.'+name,NS())
 profile=dict(schema_version='fleet-retained-runtime/v1',runtime=helper,entrypoint=helper,read_call=dict(function='context',args=[],kwargs=dict(initial=False)),patches=[],guard_modules=[],child_plans=[],journal_roots=[],dependencies=[],symbols=rows)
 if fault=='extra-symbol':rows.append(rows[0])
 if fault=='wrong-name':rows[0]['names']=['authorize']
 if fault=='wrong-root':rows[0]['original']=rows[1]['original']
 if fault=='unrelated-code':
  target=Path(rows[0]['selected']['path']);target.write_text(target.read_text().replace('OTHER=1','OTHER=2'));rows[0]['selected']=binding(target)
 if fault=='guard':profile['guard_modules']=[helper]
 if fault=='read-call':profile['read_call']['function']='run'
 p['runtime_profile']=keep(tmp_path/'runtime.json',profile)
 if fault=='tampered':Path(rows[0]['selected']['path']).write_text('raise RuntimeError()')
 called=[];monkeypatch.setattr(M,'module',lambda *a:NS(installed=lambda row:(called.append(row),nullcontext())[1]))
 if fault:
  with pytest.raises((ValueError,KeyError)):M.history_runtime(p,())
  assert not called
 else:
  with M.history_runtime(p,()):pass
  assert called==[p['runtime_profile']]

def test_aggregate_adapter_keeps_original_fallback_and_restores(monkeypatch,tmp_path):
 from scripts.batch import harbor_nat_retry as N
 old=N._disclosure_history;root=Path(N.__file__).parents[2]
 archive=keep(tmp_path/'archive',{});helper=keep(tmp_path/'history-helper',{})
 selected=dict(helper=helper,archive=archive,original=binding(N.__file__),function_sha256='exact-function')
 p=dict(aggregate_history=selected,original_snapshot=keep(tmp_path/'snapshot',dict(root=str(root))))
 worker=NS(root=root,run=tmp_path/'run',context='current-context',disclosure_repair='exact-receipt');A=NS(function_sha=lambda f:'exact-function',selected=lambda w:w.disclosure_repair is not None)
 observed=[]
 def history(A,R,w,before,**kwargs):
  assert w is worker and kwargs['historical_evidence']=='real-archive-reader';observed.append(before);return {},['validated-archive-record']
 monkeypatch.setattr(M,'module',lambda name,row:NS(adoption_history=history)if row==helper else NS(SCHEMA='fleet-disclosure-semantic-archive/v1',historical_evidence='real-archive-reader'))
 with M.aggregate_history(p,(),worker,A,dict(semantic_archive=dict(helper=archive))):
  assert N._disclosure_history(worker,{'revision':7})==['validated-archive-record']
  foreign=NS(root=root,run=tmp_path/'foreign',context='current-context',disclosure_repair='exact-receipt')
  with pytest.raises(ValueError):N._disclosure_history(foreign,{})
  ordinary=NS(disclosure_repair=None,disclosure_revision=None)
  assert N._disclosure_history(ordinary,{})==old(ordinary,{})==[]
 assert N._disclosure_history is old and observed==[{'revision':7}]
