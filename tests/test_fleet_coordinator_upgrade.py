"""Read-only cutover contracts over retained launch evidence and synthetic clocks.

No process signal, live coordinator request, or live database write is allowed.
"""
from pathlib import Path
import copy,hashlib,json,sqlite3
import pytest
from scripts.fleet import coordinator_upgrade as U

def retain(tmp,name,value):
 p=tmp/name;p.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');return U.binding(p)

def actual_target():
 p=U.checked(U.binding(U.CONTROL/'operational-controls-012/0064/plan.json'))
 process=U.checked(p['process_copy']);process=process.get('remote',process);intent=U.checked(process['root_intent']);job=U.checked(p['job'])
 current=dict(workbook='0064',state='active',owner=p['owner'],fence=intent['before']['fence']+1,version=job['version'],source_sha256=job['source']['sha256'])
 return p,process,current

def composite(tmp):
 p,process,current=actual_target();helper=U.binding(U.CONTROL/'maintenance-pause-002/pause.py')
 plan=dict(schema_version='fleet-maintenance-pause/v1',helper=helper,directory=str(tmp/'native'),maximum_pause_seconds=45,targets=[p['process']]);pb=retain(tmp,'maintenance.json',plan)
 watcher=dict(path=str(tmp/'native/watchdog-ready.json'),sha256='b'*64)
 frozen=dict(status='FROZEN',plan=pb,frozen_at=999,deadline_epoch=1030,deadline_monotonic=1230,remaining_pause_seconds=31,processes=[process],preserved=[],watchdog=watcher,wall_clocks_paused=False)
 raw=(json.dumps(frozen,sort_keys=True,indent=2)+'\n').encode();fb=dict(path=str(tmp/'native/frozen.json'),sha256=hashlib.sha256(raw).hexdigest())
 status=dict(status='FROZEN',at=1000,deadline_epoch=1030,remaining_pause_seconds=30,watchdog_alive=True,owner_coverage=True,files=[fb,watcher])
 value=dict(schema_version='fleet-maintenance-observation/v1',host=p['host'],helper=helper,plan=pb,frozen=dict(binding=fb,value=frozen),status=status,observed_at=1000)
 return value,current

def test_actual_native_projection_matches_exact_retained_root_launches():
 for wb in ('0064','0173','0013','0303'):
  p=U.checked(U.binding(U.CONTROL/f'operational-controls-012/{wb}/plan.json'));r=U.checked(p['process_copy']);r=r.get('remote',r);i=U.checked(r['root_intent']);j=U.checked(p['job'])
  current=dict(workbook=wb,state='active',owner=p['owner'],fence=i['before']['fence']+1,source_sha256=j['source']['sha256'],version=j['version'])
  assert U.target_projection(p['process'],p['host'],current)==r

def test_composite_requires_actual_frozen_bytes_and_fresh_watchdog(tmp_path):
 v,c=composite(tmp_path);assert U.frozen_proofs([retain(tmp_path,'proof.json',v)],[c],1000)==1030

@pytest.mark.parametrize('path,value',[(('observed_at',),1001),(('observed_at',),960),(('status','watchdog_alive'),False),(('status','owner_coverage'),False),(('status','status'),'NOT_FROZEN'),(('status','at'),1001),(('status','deadline_epoch'),float('inf')),(('status','remaining_pause_seconds'),float('nan')),(('status','files'),[]),(('frozen','value','wall_clocks_paused'),True),(('frozen','value','processes'),[]),(('helper','sha256'),'a'*64)])
def test_bad_freeze_proofs_refused(tmp_path,path,value):
 v,c=composite(tmp_path);target=v
 for key in path[:-1]:target=target[key]
 target[path[-1]]=value
 with pytest.raises(ValueError):U.frozen_proofs([retain(tmp_path,'proof.json',v)],[c],1000)

@pytest.mark.parametrize('key,value',[('source_sha256','a'*64),('version','b'*64),('owner','foreign'),('fence',999),('state','hold')])
def test_changed_current_claim_refused(tmp_path,key,value):
 v,c=composite(tmp_path);c[key]=value
 with pytest.raises(ValueError):U.frozen_proofs([retain(tmp_path,'proof.json',v)],[c],1000)

def selected():
 oldrow=U.binding(U.CONTROL/'coordinator-process.json');old=U.checked(oldrow);cmd=list(old['command']);cmd[2]=str(U.HOME/'coordinator-v2.py')
 return dict(old_receipt=oldrow,original_process_identity='read-only fixture',successor=U.binding(U.HOME/'coordinator-v2.py'),authorization=U.binding(U.HOME/'authorization.json'),command=cmd,change_id=U.CHANGE_ID,driver=U.binding(Path(U.__file__).absolute()),active_workbooks=[],model_operations_active=0,original_ledger={},automatic_retry=False)

def test_selected_plan_derives_exact_original_command():assert U.selected_plan(selected())['pid']==66715

@pytest.mark.parametrize('field,value',[('command',['/bin/sh','-c','foreign']),('change_id','foreign'),('automatic_retry',True),('successor',{'path':'/foreign','sha256':U.COORDINATOR_SHA})])
def test_changed_upgrade_admission_refused(field,value):
 p=selected();p[field]=value
 with pytest.raises(ValueError):U.selected_plan(p)

def test_boundary_runway_rechecked_after_blocking_probes(monkeypatch):
 monkeypatch.setattr(U.time,'time',lambda:1000)
 U.runway(1008)
 for value in (1007.999,float('nan'),float('inf')):
  with pytest.raises(ValueError):U.runway(value)

def test_old_listener_close_does_not_prove_process_exit(monkeypatch):
 from types import SimpleNamespace
 monkeypatch.setattr(U.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='66715'))
 assert U.original_absent(66715) is False
 monkeypatch.setattr(U.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
 assert U.original_absent(66715) is True

def test_ledger_digest_covers_all_private_fields_without_exposure(tmp_path):
 p=tmp_path/'database'
 with sqlite3.connect(p) as db:
  for name in ('workbooks','claims','operations'):db.execute(f'CREATE TABLE {name}(id TEXT,secret TEXT)');db.execute(f'INSERT INTO {name} VALUES(?,?)',(name,'private-claim-token'))
 before=U.ledger(p);assert 'private-claim-token' not in json.dumps(before)
 with sqlite3.connect(p) as db:db.execute("UPDATE operations SET secret='changed'")
 after=U.ledger(p);assert before['operations']!=after['operations'];assert before['claims']==after['claims']
