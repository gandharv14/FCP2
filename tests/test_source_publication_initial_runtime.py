"""Explicit fixture only: original takeover/start history must remain complete."""
from pathlib import Path
import copy,hashlib,json
import pytest
from scripts.fleet import source_publication as S

def require(v,m):
 if not v:raise ValueError(m)
def binding(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def checked(r):require(binding(r['path'])==r,'hash differs');return json.loads(Path(r['path']).read_bytes())
def put(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
def fixture(tmp_path,monkeypatch):
 h=tmp_path;home=h/'home';start=home/'instruction-start';ops=h/'run/dispatcher-v2/expired-initial-001';ops.mkdir(parents=True)
 source={'path':'/selected-source','sha256':S.INITIAL_SOURCE_SHA};code=put(h/'code.json',{});config=put(h/'config.json',dict(run_root=str(h/'run')));original=put(h/'original.json',dict(config=config,driver=code,outcome='old',operations='old',models=True,maximum_steps=90))
 environment=put(h/'environment.json',dict(status='ENVIRONMENT_READY',descriptor=code,selection={'retained':1},lease={'old':1},evidence=[code]));initial=put(h/'initial.json',dict(status='INSTRUCTION_READY',checkpoint=None))
 before=dict(revision=19,updated_at=1,leases={'task_agent':{'owner':'old'}},handoffs=[],source_sha='unchanged')
 stopped=put(h/'stopped.json',dict(before=put(h/'before.json',before),evidence=[code],selection=dict(worker_kind='environment',checkpoint=environment,config=config)))
 event=dict(at=2,operation='replace_expired',before=before['leases']['task_agent'],after=dict(owner='hhu-server-3:slot-1:0036-initial-expired-continuation-001',expires_at=2+7200*10**9),stopped_owner_proof=stopped)
 after=put(h/'after.json',{**before,'revision':20,'updated_at':2,'handoffs':[event],'leases':{'task_agent':event['after']}})
 lease=dict(owner=event['after']['owner'],token='fixture-only',revision=20,sha256=after['sha256']);takeover=put(h/'takeover.json',dict(stopped_owner_proof=stopped,after=after,lease=lease))
 custodyplan=code;custody=put(h/'custody.json',dict(status='INITIAL_ENVIRONMENT_CUSTODY_REPLACED',takeover=takeover,plan=custodyplan,replacement_count=1,max_replacements=3,duration_ns=7200*10**9,models_dispatched=0,review_budget_reset=False))
 p=put(h/'plan.json',dict(source_plan=source,custody_completion=custody,custody_plan=custodyplan,helper=code,runtime=code,stdlib=code,original_authoring_job=original,environment_checkpoint=environment,initial_checkpoint=initial,home=str(home),operations=str(ops)))
 monkeypatch.setattr(S,'INITIAL_PLAN',p);monkeypatch.setattr(S,'INITIAL_BOOTSTRAP',code)
 encoded=dict(type='Lease',value={k:dict(type='scalar',value=v) for k,v in lease.items()});env=checked(environment)
 projected=put(home/'renewed-environment.json',{**env,'lease':encoded,'evidence':[code,custody,takeover,initial],'initial_custody_projection':p})
 intent=put(start/'intent.json',dict(action='start',arguments=dict(environment_descriptor=code,checkpoint=projected,worker_run=str(h/'run/instruction')),models=False,commands=False,containers=False,timeout_seconds=3300,memory_limit_bytes=8*1024**3,code=code,base_code=code,executable=code))
 log=put(start/'step.log',None);resource=put(start/'resource.json',dict(intent=intent,returncode=0,failure=None,log=log));result=put(start/'result.json',dict(status='INSTRUCTION_READY',checkpoint=None,selection=env['selection'],lease=encoded));outcome=put(start/'outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=resource,publication_performed=False))
 job=put(home/'authoring-job.json',{**checked(original),'outcome':outcome,'operations':str(ops)})
 ready=dict(workbook='0036',initial_continuation=p,custody=custody,job=job,config=config,initial_runtime_sidecars=[])
 return ready,source,h

def test_exact_sealed_initial_history(tmp_path,monkeypatch):
 ready,source,h=fixture(tmp_path,monkeypatch);assert len(S.initial_runtime(ready,source,checked=checked,binding=binding,require=require))==4

@pytest.mark.parametrize('name,change',[
 ('custody.json',lambda v:v.update(replacement_count=2)),
 ('after.json',lambda v:v.update(source_sha='changed')),
 ('home/renewed-environment.json',lambda v:v['evidence'].pop()),
 ('home/instruction-start/intent.json',lambda v:v.update(timeout_seconds=6600)),
 ('home/instruction-start/resource.json',lambda v:v.update(returncode=1)),
 ('home/instruction-start/result.json',lambda v:v.update(checkpoint={'invented':1})),
 ('home/authoring-job.json',lambda v:v.update(maximum_steps=900)),
])
def test_semantic_history_tamper_rejected_before_publication(tmp_path,monkeypatch,name,change):
 ready,source,h=fixture(tmp_path,monkeypatch)
 def changed(row):
  value=checked(row)
  if row['path']==str(h/name):change(value)
  return value
 with pytest.raises(ValueError):S.initial_runtime(ready,source,checked=changed,binding=binding,require=require)

def test_missing_or_foreign_declared_history_rejected():
 with pytest.raises(ValueError):S.initial_runtime({},dict(sha256=S.INITIAL_SOURCE_SHA),checked=checked,binding=binding,require=require)
 with pytest.raises(ValueError):S.initial_runtime(dict(initial_continuation=S.INITIAL_PLAN),dict(sha256='other'),checked=checked,binding=binding,require=require)
