from pathlib import Path
from types import SimpleNamespace
import json,sys
import pytest
from scripts.fleet import retained_warning_continue as R

def keep(p,v):p.write_text(json.dumps(v));return R.binding(p)

@pytest.fixture
def case(tmp_path):
 c=tmp_path/'campaign.py';c.write_text('# original campaign')
 i=tmp_path/'instruction.py';i.write_text('# original instruction')
 helper=tmp_path/'selected.py';helper.write_text('# selected runtime')
 plan=keep(tmp_path/'plan.json',dict(helper=R.binding(helper)))
 calls=[]
 C=SimpleNamespace(__file__=str(c),_run_in_process_group=lambda *a,**kw:calls.append((a,kw)))
 I=SimpleNamespace(__file__=str(i),SCHEMA='original-I')
 intent=keep(tmp_path/'intent.json',dict(schema_version=I.SCHEMA,action='step',models=False,code=R.binding(i),base_code=R.binding(c)))
 command=[sys.executable,'-B',str(i),'_child',intent['path'],intent['sha256']]
 return plan,C,I,intent,command,calls

def test_original_guard_options_and_logical_command_retained(case):
 p,C,I,intent,command,calls=case
 R.install_child_bootstrap(p,C,I)
 C._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592,guardian_lock_fds=(17,))
 (args,kw),=calls
 assert args[0][:3]==[sys.executable,'-I','-B']
 assert kw==dict(timeout_seconds=3300,memory_limit_bytes=8589934592,guardian_lock_fds=(17,))
 side=R.binding(Path(intent['path']).parent/'warning-bootstrap.json')
 R.validate_bootstrap(side,p,intent,command)
 assert R.read(side)['original_command']==command

@pytest.mark.parametrize('field,value',[('models',True),('action','start'),('schema_version','foreign'),('code',{'path':'foreign','sha256':'a'*64}),('base_code',{'path':'foreign','sha256':'a'*64})])
def test_no_owning_child_for_changed_intent(case,field,value):
 p,C,I,intent,command,calls=case;v=R.read(intent);v[field]=value
 Path(intent['path']).write_text(json.dumps(v));new=R.binding(intent['path']);command[-1]=new['sha256']
 R.install_child_bootstrap(p,C,I)
 with pytest.raises(ValueError):C._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592)
 assert calls==[] and not (Path(intent['path']).parent/'warning-bootstrap.json').exists()

def test_changed_logical_command_cannot_use_existing_sidecar(case):
 p,C,I,intent,command,calls=case;R.install_child_bootstrap(p,C,I)
 C._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592)
 side=R.binding(Path(intent['path']).parent/'warning-bootstrap.json')
 wrong=[*command];wrong[3]='start'
 with pytest.raises(ValueError):R.validate_bootstrap(side,p,intent,wrong)

@pytest.fixture
def legacy_history(tmp_path,monkeypatch):
 for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH','HARBOR_FLEET_LEGACY_RESEARCH_PATH','HARBOR_FLEET_LEGACY_RESEARCH_SHA256'):
  monkeypatch.delenv(k,raising=False)
 source=dict(path='/retained/0570.xlsx',sha256='a'*64)
 history=keep(tmp_path/'history.json',dict(workbook='0570',original=source))
 fresh=keep(tmp_path/'fresh.json',dict(workbook='0570',legacy_research_history=history))
 prior=keep(tmp_path/'prior.json',dict(workbook='0570',source=source,fresh_plan=fresh))
 return dict(workbook='0570',source_sha256=source['sha256'],live_renewal=dict(plan=prior)),history

def test_live_warning_carries_exact_original_history_without_mutation(legacy_history):
 import os
 p,h=legacy_history;before=Path(h['path']).read_bytes()
 R.select_live_history(p);R.select_live_history(p)
 assert os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH']==h['path']
 assert os.environ['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']==h['sha256']
 assert Path(h['path']).read_bytes()==before

@pytest.mark.parametrize('key',['HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH','HARBOR_FLEET_LEGACY_RESEARCH_PATH','HARBOR_FLEET_LEGACY_RESEARCH_SHA256'])
def test_live_warning_rejects_foreign_history(legacy_history,monkeypatch,key):
 p,h=legacy_history;monkeypatch.setenv(key,'foreign')
 with pytest.raises(ValueError):R.select_live_history(p)

def test_live_warning_rejects_changed_history_bytes(legacy_history):
 p,h=legacy_history;Path(h['path']).write_text('{}')
 with pytest.raises(ValueError):R.select_live_history(p)

def test_live_warning_rejects_source_change(legacy_history):
 p,h=legacy_history;p['source_sha256']='b'*64
 with pytest.raises(ValueError):R.select_live_history(p)

def test_first_warning_creates_missing_parent_but_never_reuses_output(tmp_path,monkeypatch):
 monkeypatch.setattr(R.shutil,'disk_usage',lambda _:SimpleNamespace(free=20*1024**3))
 output=tmp_path/'continuations'/'warning-001'
 assert R.create_output(output,tmp_path)==output and output.is_dir()
 with pytest.raises(ValueError):R.create_output(output,tmp_path)

@pytest.mark.parametrize('kind',['low_disk','symlink'])
def test_output_rejects_low_disk_or_symlink_without_creating_namespace(tmp_path,monkeypatch,kind):
 root=tmp_path/'continuations';output=root/'warning-001'
 monkeypatch.setattr(R.shutil,'disk_usage',lambda _:SimpleNamespace(free=(1 if kind=='low_disk'else 20)*1024**3))
 if kind=='symlink':root.symlink_to(tmp_path,target_is_directory=True)
 with pytest.raises(ValueError):R.create_output(output,tmp_path)
 assert not output.exists()


@pytest.fixture
def semantic_review(tmp_path):
 helper=tmp_path/'semantic.py';helper.write_text('# original semantic driver');renew=tmp_path/'lease.py';renew.write_text('# selected original scheduler')
 descriptor=keep(tmp_path/'descriptor.json',{});blocked=keep(tmp_path/'blocked.json',{})
 value=dict(status='DIALOGUE_REPAIR_REQUIRED',next_step='dialogue-review',descriptor=descriptor,checkpoint=blocked)
 result=keep(tmp_path/'result.json',value);intent=keep(tmp_path/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor)))
 (tmp_path/'step.log').write_text('');resource=keep(tmp_path/'resource.json',dict(returncode=0,failure=None,intent=intent,log=R.binding(tmp_path/'step.log')))
 outcome=keep(tmp_path/'outcome.json',dict(status='COMPLETED_OPERATION',automatic_retry=False,result=result,resource=resource))
 old=dict(helper=R.binding(helper),workbook='0291',source=dict(sha256='source'),owner='native-owner',fence=4,tracker=dict(path='/tracker',sha256='old'),outcome='first-outcome',result='first-result')
 prior=keep(tmp_path/'prior.json',old)
 actual=dict(outcome=outcome,result=result,descriptor=descriptor,blocked=blocked,tracker=dict(path='/tracker',sha256='current'),renewal_module=R.binding(renew))
 p=dict(live_renewal=dict(context=R.binding(helper),plan=prior,completed_semantic=actual),workbook='0291',source_sha256='source',owner='native-owner',fence=5,old_result=result)
 return p,old,actual


def test_actual_second_review_projection_preserves_original_plan(semantic_review):
 p,old,actual=semantic_review;before=Path(p['live_renewal']['plan']['path']).read_bytes();v=R.live_review(p)
 assert v['outcome']==actual['outcome']and v['result']==actual['result']and v['fence']==5
 assert old['result']=='first-result'and Path(p['live_renewal']['plan']['path']).read_bytes()==before
 assert 'renewal_module'not in v


@pytest.mark.parametrize('fault',['source','owner','fence','tracker','descriptor','result','extra'])
def test_completed_warning_rejects_foreign_review_or_custody(semantic_review,fault):
 p,old,actual=semantic_review
 if fault=='source':p['source_sha256']='other'
 elif fault=='owner':p['owner']='other'
 elif fault=='fence':p['fence']=7
 elif fault=='tracker':actual['tracker']['path']='/other'
 elif fault=='descriptor':actual['descriptor']={}
 elif fault=='result':p['old_result']={}
 else:actual['unexpected']=True
 with pytest.raises(ValueError):R.live_review(p)


@pytest.mark.parametrize('fault',[None,'archive','runway','authority','count'])
def test_retained_live_warning_requires_authentic_before_and_runway(tmp_path,monkeypatch,fault):
 from dataclasses import make_dataclass,asdict
 Lease=make_dataclass('Lease',['owner','token','revision','sha256'])
 before=keep(tmp_path/'before.json',dict(leases=dict(task_agent=dict(expires_at=9000*10**9,heartbeat_at=1))))
 lease=Lease('owner','token',7,before['sha256']);tracker=dict(path='/live-tracker',sha256=before['sha256']);old=dict(lease=asdict(lease))
 p=dict(output=str(tmp_path/'out'),live_renewal={'completed_semantic':{}});d=Path(p['output'])/'live-renewal';d.mkdir(parents=True)
 receipt=dict(schema_version='fleet-retained-live-continuation-lease/v1',authority='root-plan',lease=asdict(lease),tracker=tracker,
     validated_evidence=[before],minimum_remaining_ns=7200*10**9,observed_at_ns=100*10**9,original_expired_replacements=2,
     lease_operations=0,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False)
 if fault=='archive':Path(before['path']).write_text('{}')
 elif fault=='runway':receipt['observed_at_ns']=3000*10**9
 elif fault=='authority':receipt['authority']='foreign'
 elif fault=='count':receipt['lease_operations']=1
 keep(d/'retained-live.json',receipt);monkeypatch.setattr(R,'live_review',lambda p:dict(tracker=tracker));C=SimpleNamespace(decode=lambda v:Lease(**v))
 if fault:
  with pytest.raises(ValueError):R.live_lease('root-plan',p,C,old)
 else:assert R.live_lease('root-plan',p,C,old)==lease


@pytest.mark.parametrize('name,schema',[('complete.json','fleet-validated-continuation-lease/v1'),('retained-live.json','fleet-retained-live-continuation-lease/v1')])
def test_handoff_selects_actual_renewed_or_retained_lease_receipt(tmp_path,name,schema):
 directory=tmp_path/'live-renewal';directory.mkdir();row=keep(directory/name,dict(schema_version=schema))
 assert R.live_renewal_receipt(tmp_path)==row
 other='complete.json'if name=='retained-live.json'else'retained-live.json';keep(directory/other,dict(schema_version=schema))
 with pytest.raises(ValueError,match='exactly one'):R.live_renewal_receipt(tmp_path)


def test_direct_isolated_cli_resolves_completed_semantic_reader_before_rejecting(tmp_path):
 import subprocess
 script=Path(R.__file__).resolve();prior=keep(tmp_path/'prior.json',{})
 plan=keep(tmp_path/'plan.json',dict(helper=R.binding(script),completion=None,takeover_plan=None,
     live_renewal=dict(context=R.binding(script),plan=prior,completed_semantic={})))
 result=subprocess.run([sys.executable,'-I','-B',str(script),'live-renew-child','--plan',plan['path'],'--sha256',plan['sha256']],capture_output=True,text=True)
 assert result.returncode==1 and 'ValueError: completed semantic warning changed original authority or custody' in result.stderr
 assert 'NameError'not in result.stderr
 assert sorted(p.name for p in tmp_path.iterdir())==['plan.json','prior.json']
