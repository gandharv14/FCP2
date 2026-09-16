import ast,copy,importlib.util,json,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('new0077',ROOT/'scripts/fleet/initial_0077_continuation_v2.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def fixture(monkeypatch,tmp_path):
 def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return M.binding(p)
 directory=tmp_path/'home/initial-handoff';directory.mkdir(parents=True)
 helper=write(tmp_path/'helper.json',{})
 p=dict(home=str(tmp_path/'home'),helper=helper,takeover_home=str(tmp_path/'takeover'),recipe=write(tmp_path/'recipe.json',dict(log=json.dumps({'selection':{},'initial_checkpoint':{'path':'/initial','sha256':'a'},'renewal_hold':{'path':'/held','sha256':'b'}}))))
 for name in ('old_driver','old_plan','takeover','capacity','model_gate','stdlib','stopped_central','stopped_process'):p[name]=helper
 plan=write(tmp_path/'plan.json',p);receipt=write(tmp_path/'takeover/complete.json',{})
 intent=write(directory/'intent.json',M.initial_intent(plan,p));write(directory/'dependency.json',{})
 (directory/'step.log').write_bytes(b'')
 olddesc=write(tmp_path/'old-desc.json',dict(origin=[helper],initial=dict(selection={'unchanged':'s'},lease={'old':'lease'}),worker={'history':None},code=helper))
 old=dict(descriptor=olddesc,selection={'unchanged':'s'})
 native=json.loads(M.read(p['recipe'])['log']);tcomplete=helper
 encoded={'typed':'new-lease'};selection={'unchanged':'s'}
 base=SimpleNamespace(encode=lambda x:encoded)
 def result(status,d,s,l,**kw):return dict(status=status,descriptor=d,selection=s,lease=encoded,**kw)
 C=SimpleNamespace(_result=result,selected=lambda d:None,_prior=lambda r,d:None)
 W=SimpleNamespace(STEPS=['disclosure-build'])
 env=(base,C,None,W,None,{},None,None)
 monkeypatch.setitem(sys.modules,'scripts.batch.harbor_lease_takeover',SimpleNamespace(_selection=lambda s:s))
 monkeypatch.setattr(M,'initial_values',lambda *a:(receipt,{'takeover':tcomplete},{},'typed',old,None,None,selection))
 origin=[helper,native['initial_checkpoint'],native['renewal_hold'],receipt,tcomplete,intent,M.binding(directory/'dependency.json'),*M.evidence(p)]
 descriptor=write(directory/'descriptor.json',dict(M.read(olddesc),origin=origin,initial=dict(selection=old['selection'],lease=encoded)))
 current=write(directory/'result.json',C._result('INSTRUCTION_READY',descriptor,selection,'typed',evidence=origin,next_step='disclosure-build'))
 command=[sys.executable,'-I','-B',helper['path'],'handoff-child','--plan',plan['path'],'--plan-sha256',plan['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
 resource=write(directory/'resource.json',dict(intent=intent,result=current,returncode=0,failure=None,seconds=900,memory_bytes=8589934592,log=M.binding(directory/'step.log'),command=command))
 outcome=write(directory/'outcome.json',dict(schema_version=M.HANDOFF_SCHEMA,status='INITIAL_HANDOFF_READY',intent=intent,resource=resource,result=current,automatic_retry=False))
 return write,plan,outcome,env,directory

def test_distinct_initial_handoff_accepts_complete_guard(monkeypatch,tmp_path):
 write,plan,outcome,env,d=fixture(monkeypatch,tmp_path)
 assert M.validate_handoff(plan,outcome,env)[1]['status']=='INSTRUCTION_READY'

@pytest.mark.parametrize('field,value',[('seconds',1800),('memory_bytes',1),('returncode',1),('failure','Timeout'),('command',['wrong'])])
def test_handoff_rejects_modified_rebound_guard(monkeypatch,tmp_path,field,value):
 write,plan,outcome,env,d=fixture(monkeypatch,tmp_path)
 resource=M.read(M.read(outcome)['resource']);resource[field]=value;new=write(d/'resource.json',resource)
 body=M.read(outcome);body['resource']=new;outcome=write(d/'outcome.json',body)
 with pytest.raises(ValueError):M.validate_handoff(plan,outcome,env)

@pytest.mark.parametrize('field,value',[('next_step','dialogue-review'),('status','STEP_COMPLETE'),('evidence',[])])
def test_handoff_rejects_rebound_invented_history(monkeypatch,tmp_path,field,value):
 write,plan,outcome,env,d=fixture(monkeypatch,tmp_path)
 body=M.read(outcome);current=M.read(body['result']);current[field]=value;new=write(d/'result.json',current)
 resource=M.read(body['resource']);resource['result']=new;body['resource']=write(d/'resource.json',resource);body['result']=new;outcome=write(d/'outcome.json',body)
 with pytest.raises(ValueError):M.validate_handoff(plan,outcome,env)

def test_no_environment_start_projection_and_no_repair_budget_modification():
 source=Path(M.__file__).read_text();tree=ast.parse(source)
 run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
 assert 'renewed-environment' not in ast.get_source_segment(source,run)
 assert "run('start'" not in source
 assert 'C._prior' in source and 'T.validate_completion' in source

def test_owning_guard_call_matches_original_required_interface(tmp_path):
 import inspect,os
 from scripts.batch.hardened_runner import _run_in_process_group
 source=Path(M.__file__).read_text();tree=ast.parse(source)
 call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='_run_in_process_group')
 keywords={k.arg for k in call.keywords}
 required={name for name,p in inspect.signature(_run_in_process_group).parameters.items() if p.kind==p.KEYWORD_ONLY and p.default is p.empty}
 assert required<=keywords
 if sys.platform!='linux':pytest.skip('actual capped process-group execution is validated on Linux')
 with (tmp_path/'guard.log').open('wb') as log:
  assert _run_in_process_group([sys.executable,'-I','-B','-c','import time; time.sleep(0.2)'],stdout=log,timeout_seconds=2,grace_seconds=3,memory_limit_bytes=256*1024**2,metrics={})==0
