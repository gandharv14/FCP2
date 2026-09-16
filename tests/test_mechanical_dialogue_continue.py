from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,sys
import pytest
from scripts.fleet import mechanical_dialogue_continue as M

def saved(tmp,name,value):
 p=tmp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return M.binding(p)

def test_checker_scope_only_selected_root_and_skill(tmp_path):
 calls=[]
 def old(root,skill,name):calls.append((root,skill,name));return 'old'
 P=SimpleNamespace(skill_module=old);module=SimpleNamespace(skill=old);chosen=object()
 with patch.dict(sys.modules,{'mechanical_test_scope':module}):
  with M.checker_scope(P,tmp_path,chosen):
   assert module.skill(tmp_path,'additional-assumptions-dialogue','validate_dialogue')is chosen
   assert module.skill(tmp_path,'task-disclosure','disclose')=='old'
   assert module.skill(tmp_path/'other','additional-assumptions-dialogue','validate_dialogue')=='old'
  assert module.skill is old
 assert len(calls)==2

def test_checker_scope_restores_after_failure(tmp_path):
 def old(*a):return 'old'
 module=SimpleNamespace(skill=old)
 with patch.dict(sys.modules,{'mechanical_test_scope':module}):
  with pytest.raises(RuntimeError):
   with M.checker_scope(SimpleNamespace(skill_module=old),tmp_path,object()):raise RuntimeError()
  assert module.skill is old

def test_runtime_profile_exact_source_and_validator(tmp_path):
 root=tmp_path/'root';validator=root/'.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py';validator.parent.mkdir(parents=True);validator.write_text('old')
 p={k:saved(tmp_path,k+'.json',{})for k in('helper','core','fresh_plan','original_job','validator')}
 expected=dict(schema_version=M.PROFILE,**{k:p[k]for k in p},original_validator=M.binding(validator))
 p['runtime_profile']=saved(tmp_path,'runtime.json',expected)
 assert M.profile(p,root)==expected
 for key in ('helper','core','fresh_plan','original_job','validator'):
  changed=dict(p);changed[key]=saved(tmp_path,key+'-other.json',{})
  with pytest.raises(ValueError,match='selection differs'):M.profile(changed,root)
 validator.write_text('changed')
 with pytest.raises(ValueError,match='selection differs'):M.profile(p,root)

@pytest.mark.parametrize('generation',[1,2])
def test_pair_verifier_delegates_exact_original_task_identity(tmp_path,generation):
 import types
 seen={};claims={'records':[]};task=SimpleNamespace(policy={'limit':2})
 def verify(calls,**kw):seen.update(kw);return calls
 fake=types.ModuleType('scripts.batch.harbor_dialogue_authoring');fake.verify_pair=verify
 calls=[SimpleNamespace(submitted=saved(tmp_path,'submitted.json',{'semantic_ordinal':generation})),object()]
 P=SimpleNamespace(task_context=lambda t:(None,{'workbook':'1234','original':{'sha256':'original'}}),_dialogue=lambda t,n,r:(None,claims,None,None))
 with patch.dict(sys.modules,{'scripts.batch.harbor_dialogue_authoring':fake}):
  import scripts.batch as batch
  with patch.object(batch,'harbor_dialogue_authoring',fake,create=True):
   assert M.pair_verifier(P,tmp_path,generation=generation)(task,'actual-draft',calls=calls)==calls
 assert seen==dict(policy=task.policy,claims=claims,draft='actual-draft',round=generation,root=tmp_path,workbook='1234',original_sha256='original')

@pytest.mark.parametrize('ordinal',[None,True,0,2,3])
def test_pair_verifier_refuses_other_rounds(tmp_path,ordinal):
 P=SimpleNamespace(task_context=lambda t:(None,{'workbook':'1234','original':{'sha256':'original'}}))
 call=SimpleNamespace(submitted=saved(tmp_path,'submitted.json',{'semantic_ordinal':ordinal}))
 with pytest.raises(ValueError,match='first author pair'):M.pair_verifier(P,tmp_path)(object(),'draft',calls=[call,call])

def stopped_fixture(tmp_path):
 from dataclasses import dataclass,asdict
 @dataclass
 class Lease:
  owner:str='original';revision:int=1;sha256:str='digest';token:str='token'
 lease=Lease();source=saved(tmp_path,'source.json',{});Cfile=tmp_path/'C.py';Ifile=tmp_path/'I.py';Cfile.write_text('C');Ifile.write_text('I')
 controls={'original':True};root=tmp_path/'root';run=tmp_path/'run';selection=SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':source}));worker=SimpleNamespace(root=root,run=run/'instruction',lease_renewal=None)
 C=SimpleNamespace(__file__=str(Cfile),encode=lambda x:x,decode=lambda x:lease)
 I=SimpleNamespace(__file__=str(Ifile),SCHEMA='I',guard_seconds=lambda *a:3300,selected=lambda x:(worker,controls,None),codec=SimpleNamespace(decode=lambda x:selection))
 descriptor=saved(tmp_path,'descriptor.json',{});prior=saved(tmp_path,'prior.json',{});blocked=saved(tmp_path,'blocked.json',{'selection':'selected','lease':asdict(lease)})
 intent=saved(tmp_path,'guard/intent.json',dict(code=M.binding(Ifile),base_code=M.binding(Cfile),controls=controls,action='step',arguments={'descriptor':descriptor,'checkpoint':prior},timeout_seconds=3300,memory_limit_bytes=8589934592))
 result=saved(tmp_path,'guard/result.json',dict(schema_version='I',status='DIALOGUE_REPAIR_REQUIRED',next_step='dialogue-paraphraser',checkpoint=blocked,descriptor=descriptor,publication_performed=False,evidence=[prior],selection='selected',lease='lease'))
 outcome=saved(tmp_path,'guard/outcome.json',{});D=SimpleNamespace(successful=lambda q:(result,M.read(result)));env=(C,I,None,None,None,{'run_root':str(run)},root,controls)
 p=dict(outcome=outcome,result=result,blocked=blocked,descriptor=descriptor,source=source,workbook='1234')
 return p,D,env,worker,selection,lease,intent

def test_original_mechanical_boundary_keeps_selected_author_lease(tmp_path):
 p,D,env,w,s,l,intent=stopped_fixture(tmp_path)
 assert M.boundary(p,D,env)==(w,s,l)

@pytest.mark.parametrize('change',[{'timeout_seconds':1},{'memory_limit_bytes':16*1024**3},{'action':'start'},{'controls':{}},{'code':{'path':'foreign','sha256':'foreign'}}])
def test_original_guard_changes_refuse_mechanical_authority(tmp_path,change):
 p,D,env,w,s,l,intent=stopped_fixture(tmp_path);path=Path(intent['path']);value=M.read(intent);value.update(change);path.write_text(json.dumps(value))
 with pytest.raises(ValueError,match='guard changed'):M.boundary(p,D,env)

def test_completed_ordinary_renewal_is_validated_not_discarded(tmp_path):
 p,D,env,w,s,l,intent=stopped_fixture(tmp_path);w.lease_renewal=saved(tmp_path,'renewal.json',{});w.context=SimpleNamespace(tracker_root=tmp_path/'trackers');tracker=saved(tmp_path,'trackers/1234.json',{'original':'current'});seen=[];env=list(env);env[4]=SimpleNamespace(validate=lambda actual,before:seen.append((actual,before)))
 assert M.boundary(p,D,tuple(env))==(w,s,l);assert seen==[(w,{'original':'current'})]

def test_prior_semantic_retry_cannot_use_mechanical_first_pair(tmp_path):
 p,D,env,w,s,l,intent=stopped_fixture(tmp_path);w.dialogue_semantic_retry={'foreign':'attempt'}
 with pytest.raises(ValueError,match='ordinary mechanical'):M.boundary(p,D,env)

def test_probe_dependency_checks_include_live_custody_policy(tmp_path):
 p={key:saved(tmp_path,key+'.json',{})for key in('helper','utilities','core','capacity_policy','authorization','validator','runtime_profile','renewal')}
 assert p['capacity_policy']in M.runtime_inputs(p)
 Path(p['capacity_policy']['path']).unlink()
 with pytest.raises(ValueError):M.runtime_inputs(p)

def test_completed_prefix_uses_actual_repair_module_not_renewal_api():
 from scripts.batch import harbor_instruction_repair as R,harbor_instruction_lease_renewal as L
 root=Path(R.__file__).resolve().parents[2];env=(None,None,None,None,L,None,root,None);w=SimpleNamespace(context=object());before=object();selection=object()
 assert not hasattr(L,'prefix')
 with patch.object(R,'prefix',return_value=['original'])as call:
  assert M.completed_prefix(env,before,selection,w)==['original']
  call.assert_called_once_with(before,selection,w.context,stop='dialogue')

def test_completed_prefix_rejects_foreign_original_root(tmp_path):
 from scripts.batch import harbor_instruction_repair as R
 foreign=tmp_path/'scripts/batch/harbor_instruction_repair.py';foreign.parent.mkdir(parents=True);foreign.write_text('foreign')
 with patch.object(R,'prefix')as call:
  with pytest.raises(ValueError,match='reader differs'):M.completed_prefix((None,)*6+(tmp_path,None),{},None,SimpleNamespace(context=None))
  call.assert_not_called()

def preauthorization_fixture(tmp):
 old={k:saved(tmp,k+'.json',{})for k in ('helper','core','validator','tracker')}
 old.update(completed_takeover={'original':True},fence=2,output=str(tmp/'old-output'),schema_version=M.SCHEMA)
 old['runtime_profile']=saved(tmp,'old-profile.json',{'helper':old['helper'],'validator':old['validator'],'original':'unchanged'})
 plan=saved(tmp,'old-plan.json',old);home=Path(old['output'])/'authorization';home.mkdir(parents=True)
 intent=saved(home,'intent.json',dict(schema_version=M.SCHEMA,plan=plan,seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False))
 log=home/'step.log';log.write_text("prefix=env[4].prefix(before,s,w.context,stop='dialogue')\nAttributeError: module 'scripts.batch.harbor_instruction_lease_renewal' has no attribute 'prefix'. Did you mean: '_prefix'?\n")
 command=[sys.executable,'-I','-B',old['helper']['path'],'authorize-child','--plan',plan['path'],'--sha256',plan['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
 resource=saved(home,'resource.json',dict(returncode=1,failure=None,intent=intent,log=M.binding(log),command=command))
 outcome=saved(home,'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,result=None,automatic_retry=False));saved(home,'before-lease.json',M.read(old['tracker']))
 p=dict(old,helper=saved(tmp,'new-helper.json',{}),fence=3,output=str(tmp/'new-output'),preauthorization_failure=dict(plan=plan,outcome=outcome))
 p['runtime_profile']=saved(tmp,'new-profile.json',dict(M.read(old['runtime_profile']),helper=p['helper']))
 return p,old,home

def test_exact_pre_authority_api_failure_retained(tmp_path):
 p,old,home=preauthorization_fixture(tmp_path);assert M.preauthorization_failure(p)==old

@pytest.mark.parametrize('change',['progress','tracker','code','fence','error','guard'])
def test_pre_authority_recovery_rejects_drift(tmp_path,change):
 p,old,home=preauthorization_fixture(tmp_path)
 if change=='progress':(home/'mechanical').mkdir()
 elif change=='tracker':p['tracker']=saved(tmp_path,'new-tracker.json',{'changed':True})
 elif change=='code':p['core']=saved(tmp_path,'new-core.json',{})
 elif change=='fence':p['fence']=4
 elif change=='error':(home/'step.log').write_text('different failure')
 else:
  q=home/'resource.json';v=json.loads(q.read_text());v['returncode']=0;q.write_text(json.dumps(v))
 with pytest.raises(ValueError):M.preauthorization_failure(p)

def test_preserved_clause_library_uses_only_otherwise_addition_and_restores(tmp_path):
 import ast,importlib.util
 root=Path(__file__).resolve().parents[1];path=root/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py'
 source=path.read_text();tree=ast.parse(source);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='clause_covered')
 branch=next(n for n in fn.body if isinstance(n,ast.If)and ast.unparse(n.test)=="needle == 'otherwise'")
 lines=source.splitlines(True);old_source=''.join(lines[:branch.lineno-1]+lines[branch.end_lineno:]);old_path=tmp_path/'old.py';old_path.write_text(old_source)
 spec=importlib.util.spec_from_file_location('mechanical_old_library_test',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 validator=SimpleNamespace(clause_covered=old.clause_covered);previous=validator.clause_covered
 text='The amount is 0 unless profit is positive. When it is, use the result.'
 assert not previous('otherwise',text)
 with M.clause_library(validator,M.binding(path),M.binding(old_path)):
  assert validator.clause_covered('otherwise',text)
  assert not validator.clause_covered('otherwise','The amount is 0 unless profit is positive.')
 assert validator.clause_covered is previous
 changed=tmp_path/'changed.py';changed.write_text(source.replace("needle=='plus'", "needle=='extra'"))
 with pytest.raises(ValueError,match='beyond otherwise'):
  with M.clause_library(validator,M.binding(changed),M.binding(old_path)):pytest.fail('unlisted change')


def preserved_guard_fixture(tmp_path):
 p,D,env,w,s,l,intent=stopped_fixture(tmp_path);C,I=env[:2]
 old={'source':p['source'],'workbook':p['workbook'],'fence':1,'output':str(tmp_path/'parent'),'helper':saved(tmp_path,'parent.py',{})}
 parent=saved(tmp_path,'parent.json',old);rows=[];previous=None
 for index in range(2):
  home=tmp_path/'parent/first-step'if index==0 else tmp_path/'ops/000';home.mkdir(parents=True,exist_ok=True)
  args=dict(descriptor=p['descriptor'],checkpoint=previous)
  ib=saved(tmp_path,str(home.relative_to(tmp_path)/'intent.json'),dict(code=M.binding(I.__file__),base_code=M.binding(C.__file__),controls=env[7],arguments=args,action='step',timeout_seconds=3300,memory_limit_bytes=8589934592))
  result=dict(schema_version=I.SCHEMA,status='STEP_COMPLETE'if index==0 else'DIALOGUE_REPAIR_REQUIRED',next_step='dialogue-paraphraser',checkpoint=p['blocked'],descriptor=p['descriptor'],publication_performed=False,evidence=[previous],selection='selected',lease='lease')
  rb=saved(tmp_path,str(home.relative_to(tmp_path)/'result.json'),result)
  gb=saved(tmp_path,str(home.relative_to(tmp_path)/'resource.json'),dict(intent=ib,returncode=0,failure=None))
  rows.append(saved(tmp_path,str(home.relative_to(tmp_path)/'outcome.json'),dict(status='COMPLETED_OPERATION',resource=gb,result=rb)))
  saved(tmp_path,str(home.relative_to(tmp_path)/'preserved-gate-bootstrap.json'),dict(plan=parent,helper=old['helper'],intent=ib,automatic_retry=False))
  previous=rb
 p.update(fresh_plan=None,fence=2,preserved_context=dict(plan=parent,outcomes=rows),outcome=rows[-1],result=previous)
 return p,old,env,w,s,l


def test_preserved_boundary_retains_actual_guarded_prefix(tmp_path):
 p,old,env,w,s,l=preserved_guard_fixture(tmp_path)
 assert M.preserved_boundary(p,old,env,SimpleNamespace(successful=lambda r:(M.read(r)['result'],M.read(M.read(r)['result']))))==(w,s,l)

@pytest.mark.parametrize('fault',['source','fence','first','result','descriptor'])
def test_preserved_boundary_rejects_foreign_chain(tmp_path,fault):
 p,old,env,*_=preserved_guard_fixture(tmp_path)
 if fault=='source':p['source']=saved(tmp_path,'foreign-source',{})
 elif fault=='fence':p['fence']=4
 elif fault=='first':p['preserved_context']['outcomes']=p['preserved_context']['outcomes'][::-1]
 else:p[fault]=saved(tmp_path,'foreign-'+fault,{})
 with pytest.raises(ValueError):M.preserved_boundary(p,old,env,SimpleNamespace(successful=lambda r:(M.read(r)['result'],M.read(M.read(r)['result']))))

def test_preserved_archive_import_is_exact_and_restored(tmp_path):
 oldfile=tmp_path/'old.py';oldfile.write_text("SCHEMA='fleet-disclosure-semantic-archive/v1'\n")
 newfile=tmp_path/'new.py';newfile.write_text("SCHEMA='fleet-disclosure-semantic-archive/v1'\nMARKER=2\n")
 oldrow=M.binding(oldfile);newrow=M.binding(newfile);calls=[]
 def original(name,row):calls.append((name,row));return 'original'
 parent=SimpleNamespace(module=original);old={'semantic_archive':{'helper':oldrow}};spec={'semantic_archive':{'original':oldrow,'selected':newrow}}
 with M.preserved_archive_module(parent,old,spec):
  assert parent.module('history',oldrow).MARKER==2
  assert parent.module('other',{'path':'else','sha256':'x'})=='original'
 assert parent.module is original and len(calls)==1
 oldfile.write_text('changed')
 with pytest.raises(ValueError,match='original archive module changed'):
  with M.preserved_archive_module(parent,old,spec):pass
 assert parent.module is original

@pytest.mark.parametrize('plan,expected',[({},900),({'preserved_context':{}},900),({'preserved_context':{},'authorization_seconds':3300},3300),({'authorization_seconds':3300},None),({'preserved_context':{},'authorization_seconds':True},None),({'preserved_context':{},'authorization_seconds':3301},None),({'preserved_context':{},'authorization_seconds':899},None)])
def test_explicit_preserved_authorization_guard(plan,expected):
 if expected is None:
  with pytest.raises(ValueError):M.authorization_seconds(plan)
 else:assert M.authorization_seconds(plan)==expected

@pytest.mark.parametrize('seconds,recorded,ok',[(900,900,True),(3300,3300,True),(3300,900,False),(900,3300,False)])
def test_authorization_receipt_requires_exact_selected_guard(tmp_path,seconds,recorded,ok):
 home=tmp_path/'authorization';home.mkdir();helper=tmp_path/'helper.py';helper.write_text('pass')
 p=dict(output=str(tmp_path),helper=M.binding(helper),blocked={'path':'/blocked','sha256':'b'},runtime_profile={'path':'/runtime','sha256':'r'},preserved_context={},authorization_seconds=seconds)
 row=saved(tmp_path,'plan.json',p);intent=saved(home,'intent.json',dict(schema_version=M.SCHEMA,plan=row,seconds=recorded,memory_bytes=8589934592,models=False,automatic_retry=False))
 log=home/'step.log';log.write_text('completed');descriptor=saved(home,'descriptor.json',{});certificate=saved(home,'certificate.json',{})
 checkpoint=saved(home,'checkpoint.json',{'mechanical_requalification':certificate})
 result=saved(home,'result.json',dict(status='STEP_COMPLETE',next_step='dialogue-review',descriptor=descriptor,checkpoint=checkpoint,evidence=[p['blocked'],row,checkpoint]))
 resource=saved(home,'resource.json',dict(returncode=0,failure=None,intent=intent,log=M.binding(log),command=[sys.executable,'-I','-B',p['helper']['path'],'authorize-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]))
 outcome=saved(home,'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False));seen=[]
 I=SimpleNamespace(selected=lambda r:seen.append('selected'),_prior=lambda r,d:seen.append('prior'));K=SimpleNamespace(validate=lambda r:dict(runtime_profile=p['runtime_profile'],blocked=p['blocked']))
 if ok:assert M.authorization_outcome(row,outcome,I,K)[0]==result and seen==['selected','prior']
 else:
  with pytest.raises(ValueError,match='guard failed or differs'):M.authorization_outcome(row,outcome,I,K)
  assert seen==[]

def test_real_closure_follows_validated_mechanical_directory(tmp_path):
 scope={'step_directory':lambda *a:'original'}
 def step(*args):return scope['step_directory'](*args)
 W=SimpleNamespace(_step=step,step_directory=scope['step_directory']);prior=scope['step_directory'];calls=[]
 K=SimpleNamespace(install_projection=lambda w,c:(calls.append(c),setattr(w,'step_directory',lambda *a:'mechanical')),validate=lambda c:c)
 with M.preserved_step_directory(W,M.binding(__file__)):
  M.install_mechanical_projection(K,W,{'path':'/certificate','sha256':'c'})
  assert W._step()=='mechanical'
  M.install_mechanical_projection(K,W,{'path':'/certificate','sha256':'c'});assert len(calls)==1
  with pytest.raises(ValueError):M.install_mechanical_projection(K,W,{'path':'/foreign','sha256':'d'})
 assert scope['step_directory']is prior
 W.step_directory=lambda *a:'foreign'
 with pytest.raises(ValueError,match='callback diverged'):
  with M.preserved_step_directory(W,M.binding(__file__)):pass


def test_dispatcher_root_bridge_retains_original_selector_and_restores(tmp_path):
 worker=SimpleNamespace(root=tmp_path/'selected',run=tmp_path/'run');I=SimpleNamespace(selected=lambda d:(worker,None,None))
 source=(None,I,None,None,None,{},tmp_path/'source','controls');current=(*source[:6],worker.root,source[7]);events=[]
 def original(env,value):
  assert env==source;events.append(value);return worker,'original recipe'
 D=SimpleNamespace(selected=original)
 with M.dispatcher_environment(D,source,current,worker):
  assert D.selected(current,{'descriptor':'exact'})==(worker,'original recipe')
  with pytest.raises(ValueError,match='current mechanical'):D.selected(source,{'descriptor':'exact'})
  I.selected=lambda d:(SimpleNamespace(root=worker.root,run=tmp_path/'foreign'),None,None)
  with pytest.raises(ValueError,match='changed worker'):D.selected(current,{'descriptor':'foreign'})
 assert D.selected is original and len(events)==1


def settled_fixture(tmp):
 oldhome=tmp/'old';oldhome.mkdir();(oldhome/'authorization').mkdir();ops=tmp/'old-ops';ops.mkdir()
 helper=saved(tmp,'old-helper.json',{});newhelper=saved(tmp,'new-helper.json',{})
 oldprofile=saved(tmp,'old-profile.json',dict(helper=helper,validator='original'))
 newprofile=saved(tmp,'new-profile.json',dict(helper=newhelper,validator='original'))
 original=saved(tmp,'original-job.json',dict(driver='D',publish=False))
 tracker=saved(tmp,'tracker.json',dict(current='renewed'));after=saved(tmp,'old/authorization/after-lease.json',dict(current='renewed'));assert tracker['sha256']==after['sha256']
 saved(tmp,'old/authorization/mechanical/complete.json',dict(lease=dict(sha256=tracker['sha256'])))
 auth=saved(tmp,'old/authorization/outcome.json',{})
 author=dict(M.read(original),outcome=auth,operations=str(ops));saved(tmp,'old/authoring-job.json',author);saved(tmp,'old-ops/job.json',author)
 old=dict(helper=helper,owner='old',fence=3,output=str(oldhome),tracker=tracker,runtime_profile=oldprofile,preserved_context={},original_job=original,source='same',validator='same')
 oldrow=saved(tmp,'old-plan.json',old);run=tmp/'worker';run.mkdir();log=run/'worker.log';log.write_text('worker,recipe=selected(env,value)\nValueError: dispatcher environment differs from retained context\n')
 job=saved(tmp,'worker-job.json',dict(driver=helper,result=str(oldhome/'result.json'),run=str(run)));intent=saved(tmp,'intent.json',dict(owner='old',job=job));outer=saved(tmp,'outer.json',dict(returncode=1,failure_type=None,result=None,intent=intent))
 p=dict(old,helper=newhelper,owner='new',fence=4,output=str(tmp/'new'),runtime_profile=newprofile,completed_authorization=dict(plan=oldrow,worker_outcome=outer,log=M.binding(log)))
 return p,oldrow,ops


def test_settled_authorization_retains_exact_old_root_and_unused_dispatcher(tmp_path):
 p,oldrow,ops=settled_fixture(tmp_path);assert M.authorization_selection({'new':'selection'},p)==(oldrow,M.read(oldrow))
 (ops/'000').mkdir()
 with pytest.raises(ValueError,match='downstream instruction'):M.authorization_selection({},p)

@pytest.mark.parametrize('key,value',[('source','foreign'),('fence',5),('owner','old'),('validator','foreign')])
def test_settled_authorization_refuses_source_or_custody_drift(tmp_path,key,value):
 p,_,_=settled_fixture(tmp_path);p[key]=value
 with pytest.raises(ValueError,match='changed original work'):M.authorization_selection({},p)

def test_settled_authorization_keeps_completed_lease_historical_after_later_cas(tmp_path):
 p,oldrow,_=settled_fixture(tmp_path)
 Path(p['tracker']['path']).write_text(json.dumps(dict(current='later-original-step')))
 assert M.authorization_selection({},p)==(oldrow,M.read(oldrow))
 # The historical reader does not claim that this is the live owning tracker.
 assert M.binding(p['tracker']['path'])!=p['tracker']


def test_projection_reinstalls_after_original_scope_restores_directory(tmp_path):
 from scripts.batch import harbor_dialogue_mechanical_requalification as K
 initial=tmp_path/'initial.json';initial.write_text('{}');original_dir=tmp_path/'old-paraphraser';original_dir.mkdir();mechanical=tmp_path/'mechanical';mechanical.mkdir();(mechanical/'complete.json').write_text('{}')
 certificate={'path':'/authenticated/certificate','sha256':'c'}
 proof=dict(worker_run=str(tmp_path),initial=K.binding(initial),original_directory=str(original_dir),directory=str(mechanical))
 worker=SimpleNamespace(run=tmp_path)
 scope={'step_directory':lambda w,phase:original_dir if phase=='dialogue-paraphraser'else tmp_path/phase}
 def step(w,phase):return scope['step_directory'](w,phase)
 original=scope['step_directory'];W=SimpleNamespace(_step=step,step_directory=original)
 validate=K.validate;K.validate=lambda c:proof if c==certificate else (_ for _ in ()).throw(ValueError('foreign certificate'))
 try:
  with M.preserved_step_directory(W,M.binding(__file__)):
   M.install_mechanical_projection(K,W,certificate)
   assert W._step(worker,'dialogue-paraphraser')==mechanical
  # The enclosing original instruction scope restores the callback, while the
  # worker module and authentic certificate marker survive into its next scope.
  W.step_directory=original
  assert not (W._step(worker,'dialogue-paraphraser')/'complete.json').exists()
  with M.preserved_step_directory(W,M.binding(__file__)):
   M.install_mechanical_projection(K,W,certificate)
   assert (W._step(worker,'dialogue-paraphraser')/'complete.json').is_file()
   selected=W.step_directory;M.install_mechanical_projection(K,W,certificate)
   assert W.step_directory is selected
 finally:K.validate=validate


def test_optional_completed_aggregate_renewal_preserves_default_and_scope(tmp_path,monkeypatch):
 from contextlib import contextmanager
 env=tuple(range(8));w=object();A=object();D=object();events=[]
 with M.renewal_runtime({},env,w,A,D)as value:assert value is env
 profile=saved(tmp_path,'renewal-profile.json',dict(schema_version='fleet-adoption-command-renewal/v1',helper={'path':'/bound','sha256':'h'}))
 @contextmanager
 def installed(S,row,current,worker,aggregate,dispatcher):
  assert row==profile and current is env and worker is w and aggregate is A and dispatcher is D
  events.append('enter')
  try:yield (*env[:4],'selected-renewal',*env[5:])
  finally:events.append('exit')
 monkeypatch.setattr(M,'module',lambda name,row:SimpleNamespace(installed=installed))
 p=dict(renewal_runtime=profile,preserved_context={},completed_authorization={})
 with pytest.raises(RuntimeError):
  with M.renewal_runtime(p,env,w,A,D)as current:
   assert current[4]=='selected-renewal'and current[:4]==env[:4]and current[5:]==env[5:]
   raise RuntimeError('original caller failure')
 assert events==['enter','exit']
 with pytest.raises(ValueError,match='authentic completed'): 
  with M.renewal_runtime(dict(renewal_runtime=profile),env,w,A,D):pass
