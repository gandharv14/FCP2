from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import json,pytest
from scripts.fleet import disclosure_command_continue as D,instruction_semantic_continue as U
from scripts.batch import harbor_disclosure_command_repair as Q,harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as I
from test_instruction_worker import case,instruction_setup
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup


def test_actual_retry_handoff_keeps_original_descriptor_code_and_checks_permit(case,monkeypatch):
 worker,s,l,*_=case
 old=W.keep(worker.run/'old-descriptor.json',dict(worker=I.worker_encode(worker),initial={},origin=[],code={'path':'retained-I','sha256':'a'*64},controls={'retained':True},dependencies={'original':True},schema_version=I.SCHEMA))
 initial=W.keep(worker.run/'initial-result.json',{'retained':'initial'})
 profile=W.keep(worker.run/'profile.json',{'retained':'full command history'})
 permit=W.keep(worker.run/'continuation/permit/permit.json',dict(schema_version=Q.SCHEMA,selection=I.codec.encode(s),lease=asdict(l)))
 checked=[]
 monkeypatch.setattr(I,'selected',lambda row:checked.append(row))
 p=dict(output=str(worker.run/'continuation'),descriptor=old,initial_result=initial,profile=profile)
 result=D.handoff(SimpleNamespace(keep=W.keep),p,I,I.base,worker,s,l,permit)
 value=I.read(result);descriptor=I.read(value['descriptor'])
 assert descriptor['code']==I.read(old)['code'] and descriptor['controls']==I.read(old)['controls'] and descriptor['dependencies']==I.read(old)['dependencies']
 assert descriptor['origin']==[old,initial,profile,permit]
 assert I._prior(result,value['descriptor'])[1]==s
 assert checked==[value['descriptor']]
 assert I.worker_decode(descriptor['worker']).code_repair==permit


def fixture(tmp_path,seconds=3300):
 files=[]
 for name in ('C.py','I.py','D.py','helper.py'):
  f=tmp_path/name;f.write_text('# retained source\n');files.append(f)
 C=SimpleNamespace(__file__=str(files[0]),encode=lambda x:x,SECONDS=3300)
 I=SimpleNamespace(__file__=str(files[1]),SCHEMA='original-I',guard_seconds=lambda action,arguments:seconds)
 oldD=SimpleNamespace(__file__=str(files[2]))
 p=dict(output=str(tmp_path/'continuation'),helper=D.binding(files[3]))
 config=dict(run_root=str(tmp_path/'run'));env=(C,I,None,None,None,config,tmp_path,{})
 job={'original':'source job'};directory=Path(p['output'])/'first-step';directory.mkdir(parents=True)
 value=dict(schema_version=I.SCHEMA,action='step',arguments={'checkpoint':'original'},timeout_seconds=seconds,models=True,code=D.binding(files[1]),base_code=D.binding(files[0]),controls={})
 intent=U.keep(directory/'intent.json',value)
 command=['python','-B',str(files[1]),'_child',intent['path'],intent['sha256']]
 return p,oldD,env,job,intent,command


def test_actual_child_router_preserves_guard_and_accepts_existing_dispatcher_namespace(tmp_path):
 p,dispatcher,env,job,intent,command=fixture(tmp_path);operations=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name;operations.mkdir(parents=True)
 calls=[]
 def guard(argv,**kwargs):calls.append((argv,kwargs));return 7
 plan=U.keep(tmp_path/'plan.json',{'selected':'plan'})
 D.install_commands(plan,p,U,dispatcher,env,guard,job)
 assert env[0]._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592)==7
 actual,options=calls[0]
 assert actual[1:5]==['-I','-B',p['helper']['path'],'child']
 assert options==dict(timeout_seconds=3300,memory_limit_bytes=8589934592)
 side=D.read(D.binding(Path(intent['path']).parent/'disclosure-bootstrap.json'))
 assert side['original_command']==command and side['intent']==intent
 assert dispatcher.setup(dict(job,outcome={'actual':'completed'},operations=str(operations)))is env
 with pytest.raises(ValueError):dispatcher.setup(dict(job,outcome={},operations='/foreign'))


@pytest.mark.parametrize('defect',['guard','foreign_namespace','foreign_code'])
def test_child_router_rejects_changed_guard_or_location_before_spawn(tmp_path,defect):
 p,dispatcher,env,job,intent,command=fixture(tmp_path);calls=[]
 if defect=='foreign_namespace':
  elsewhere=tmp_path/'elsewhere';elsewhere.mkdir();intent=U.keep(elsewhere/'intent.json',D.read(intent));command[-2:]=[intent['path'],intent['sha256']]
 if defect=='foreign_code':command[2]=str(tmp_path/'unknown.py')
 D.install_commands(U.keep(tmp_path/'plan.json',{}),p,U,dispatcher,env,lambda *a,**k:calls.append(a),job)
 with pytest.raises(ValueError):env[0]._run_in_process_group(command,timeout_seconds=3301 if defect=='guard'else 3300,memory_limit_bytes=8589934592)
 assert calls==[]


def test_actual_cold_child_reinstalls_context_then_calls_original_instruction(tmp_path,monkeypatch):
 p,dispatcher,env,job,intent,command=fixture(tmp_path);row=U.keep(tmp_path/'plan.json',{})
 D.install_commands(row,p,U,dispatcher,env,lambda *a,**k:None,job)
 env[0]._run_in_process_group(command,timeout_seconds=3300,memory_limit_bytes=8589934592)
 side=D.binding(Path(intent['path']).parent/'disclosure-bootstrap.json');seen=[]
 env[1].child=lambda bound:seen.append(bound)or 'original child'
 utility=SimpleNamespace(retained_command=U.retained_command,retained_seconds=U.retained_seconds,custody=lambda *a:None,keep=U.keep)
 @contextmanager
 def context(selected,**kwargs):
  assert selected==row and kwargs=={'continued':True}
  yield p,utility,dispatcher,env,SimpleNamespace(root=tmp_path),None,None,None,lambda *a,**k:None,job
 monkeypatch.setattr(D,'context',context);monkeypatch.setattr(D,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=1),dont_write_bytecode=True))
 assert D.child(row,intent,side)=='original child'
 assert seen==[intent]


def test_inline_renewal_uses_original_lifecycle_cas_and_preserves_identity(failed,monkeypatch):
 from scripts.fleet import continuation_lease as L
 worker,selection,lease,*_=failed
 home=worker.run/'continuation';home.mkdir()
 tracker=worker.context.tracker_root/(selection.content.workbook+'.json');before=json.loads(tracker.read_bytes())
 profile=W.keep(worker.run/'candidate.json',{'bound':'full suffix proof'})
 p=dict(renewal_module=D.binding(L.__file__),output=str(home),tracker=D.binding(tracker),profile=profile,source=selection.content.bindings['original_raw_source'])
 seen=[];utility=SimpleNamespace(keep=W.keep,custody=lambda *a:seen.append('custody'))
 core=SimpleNamespace(inspect_history=lambda *a:seen.append('history'),W=W)
 env=(None,None,None,W,SimpleNamespace(prefix=lambda *a,**k:seen.append('prefix')or []))
 updated,completion=D.renew_for_permit(profile,p,utility,env,worker,selection,lease,core)
 after=json.loads(tracker.read_bytes())
 assert (updated.owner,updated.token)==(lease.owner,lease.token)
 assert updated.revision==lease.revision+1
 assert after['handoffs'][:-1]==before['handoffs'] and after['handoffs'][-1]['operation']=='renew'
 for key in ('lane_state','bindings','generation_ids','promotion_ids'):assert after[key]==before[key]
 assert seen.count('history')==seen.count('prefix')==1
 assert D.read(completion)['semantic_budget_reset']is False
 with pytest.raises(ValueError,match='already attempted'):D.renew_for_permit(profile,p,utility,env,worker,selection,lease,core)
 assert json.loads(tracker.read_bytes())==after


def test_completed_takeover_uses_exact_initial_guard_and_declared_context(tmp_path):
 snapshot={'path':'/old/snapshot.json','sha256':'a'*64};completion={'path':'/native/complete.json','sha256':'b'*64}
 plan=U.keep(tmp_path/'takeover-plan.json',{'original_snapshot':snapshot})
 profile=U.keep(tmp_path/'profile.json',{'old_snapshot':snapshot,'lease_history':completion})
 context=dict(helper={'selected':'loader'},plan={'selected':'history'},function='cold_context',kwargs={'continued':True})
 p=dict(completed_takeover=dict(plan=plan,completion=completion),profile=profile,context=context,
  renewal_module=None,lease=None,workbook='0077',initial_result={'actual':'initial'},failure_outcome={'actual':'guard'},tracker={'actual':'tracker'},owner='owner',source={'actual':'source'})
 calls=[];lease=object();manager=object()
 def reader(value):calls.append(value);return lease,manager
 assert D.completed_lease(p,SimpleNamespace(retained_lease=reader))==(lease,manager)
 assert calls[0]['result']==p['initial_result']and calls[0]['outcome']==p['failure_outcome']
 assert calls[0]['retained_read_call']==dict(function='cold_context',args=[context['plan']],kwargs=context['kwargs'])
 assert U.retained_invocation(calls[0])==calls[0]['retained_read_call']
 for key,value in [('lease',{'foreign':True}),('renewal_module',{'foreign':True})]:
  with pytest.raises(ValueError):D.completed_lease({**p,key:value},SimpleNamespace(retained_lease=reader))
 foreign=U.keep(tmp_path/'foreign.json',{'old_snapshot':snapshot,'lease_history':{'wrong':'completion'}})
 with pytest.raises(ValueError):D.completed_lease({**p,'profile':foreign},SimpleNamespace(retained_lease=reader))
 assert len(calls)==1


@pytest.mark.parametrize('call',[
 {'function':'run','args':[{'selected':'history'}],'kwargs':{}},
 {'function':'cold_context','args':[{'wrong':'history'}],'kwargs':{}},
 {'function':'cold_context','args':[{'selected':'history'}],'kwargs':None},
 {'function':'cold_context','args':[{'selected':'history'}],'kwargs':{},'extra':True},
])
def test_retained_context_metadata_rejects_unlisted_invocation(call):
 with pytest.raises(ValueError):U.retained_invocation(dict(retained_context={'plan':{'selected':'history'}},retained_read_call=call))


def test_retains_exact_unimported_takeover_dependency(tmp_path,monkeypatch):
 import sys
 import scripts.batch as package
 from types import ModuleType
 rows=[]
 for name in ('harbor_tracker_lifecycle','harbor_lease_takeover'):
  path=tmp_path/(name+'.py');path.write_text('VALUE = 7\n')
  rows.append(dict(module_name='scripts.batch.'+name,original=U.binding(path),selected=U.binding(path)))
  monkeypatch.delitem(sys.modules,'scripts.batch.'+name,raising=False)
  monkeypatch.setattr(package,name,None,raising=False)
  monkeypatch.setitem(sys.modules,'_semantic_retained_'+name,None)
 try:
  U.retain_lease_dependencies(dict(dependencies=rows))
  for row in rows:
   name=row['module_name'].rsplit('.',1)[1];m=sys.modules[row['module_name']]
   assert m.VALUE==7 and getattr(package,name)is m and sys.modules['_semantic_retained_'+name]is m
  original=sys.modules[rows[1]['module_name']]
  U.retain_lease_dependencies(dict(dependencies=rows))
  assert sys.modules[rows[1]['module_name']]is original
  with pytest.raises(ValueError):U.retain_lease_dependencies(dict(dependencies=rows[:1]))
  with pytest.raises(ValueError):U.retain_lease_dependencies(dict(dependencies=rows+[rows[0]]))
  foreign=ModuleType('foreign');foreign.__file__=str(tmp_path/'foreign.py');Path(foreign.__file__).write_text('VALUE=8\n')
  monkeypatch.setitem(sys.modules,rows[1]['module_name'],foreign)
  with pytest.raises(ValueError):U.retain_lease_dependencies(dict(dependencies=rows))
 finally:
  for row in rows:sys.modules.pop(row['module_name'],None)


def test_exact_tracker_correction_rejects_other_changes(tmp_path,monkeypatch):
 fixed=Path(Q.__file__).read_text();old=fixed.replace('_,before,tracker=W.active(worker,selection,lease);permit=validate(worker,before)','_,before,_=W.active(worker,selection,lease);permit=validate(worker,before)').replace('task=W.task(worker,selection,tracker);require(task.run==destination','task=W.task(worker,selection,before);require(task.run==destination')
 assert old!=fixed
 original=tmp_path/'old.py';original.write_text(old);selected=tmp_path/'new.py';selected.write_text(fixed)
 monkeypatch.setattr(Q,'__file__',str(original));function=D.corrected_operation(Q,D.binding(selected));assert function.__globals__ is Q.__dict__
 selected.write_text(fixed.replace('if not commands:','if False:'))
 with pytest.raises(ValueError,match='only the exact'):D.corrected_operation(Q,D.binding(selected))
 selected.write_text(old)
 with pytest.raises(ValueError):D.corrected_operation(Q,D.binding(selected))


def test_settled_projection_scopes_worker_and_restores_on_failure(case,monkeypatch):
 from dataclasses import replace
 worker,selection,lease,*_=case
 permit=W.keep(worker.run/'repair/permit.json',dict(schema_version=Q.SCHEMA,phase='disclosure-build'))
 worker=replace(worker,code_repair=permit);output=worker.run/'continued';old_run=Q.producer_run;old_step=W.step_directory;old_op=Q.remaining_operation
 selected=lambda *a,**k:None
 with pytest.raises(RuntimeError,match='forced'):
  with D.operation_scope(Q,worker,output,selected):
   assert Q.producer_run(worker)==output/'producer' and W.step_directory(worker,'disclosure-build')==output/'steps/disclosure-build'
   assert W.step_directory(worker,'disclosure-review')==old_step(worker,'disclosure-review')
   foreign=replace(worker,code_repair=None)
   assert Q.producer_run(foreign)==old_run(foreign)and W.step_directory(foreign,'disclosure-build')==old_step(foreign,'disclosure-build')
   assert Q.remaining_operation is selected
   raise RuntimeError('forced')
 assert (Q.producer_run,W.step_directory,Q.remaining_operation)==(old_run,old_step,old_op)


def test_original_dispatcher_selection_only_projects_declared_root(tmp_path):
 from scripts.fleet import advance_authoring_v8 as original
 from dataclasses import replace
 original_root=tmp_path/'old';new_root=tmp_path/'new';run=tmp_path/'run/instruction';controls=object()
 worker=SimpleNamespace(root=new_root,run=run,campaign=SimpleNamespace(model_slots=1,slot_root=original.MODEL))
 I=SimpleNamespace(selected=lambda row:(worker,controls,'recipe'))
 env=(None,I,None,None,None,{'run_root':str(run.parent)},original_root,controls)
 dispatcher=SimpleNamespace(selected=original.selected);old=dispatcher.selected
 with pytest.raises(ValueError,match='instruction selection differs'):old(env,{'descriptor':{}})
 with D.dispatcher_selection(dispatcher,env,worker):
  assert dispatcher.selected(env,{'descriptor':{}})==(worker,'recipe')
  assert dispatcher.selected((*env[:6],new_root,controls),{'descriptor':{}})==(worker,'recipe')
  for index,replacement in ((1,object()),(5,{'run_root':'/foreign'}),(6,tmp_path/'foreign'),(7,object())):
   changed=list(env);changed[index]=replacement
   with pytest.raises(ValueError,match='environment differs'):dispatcher.selected(tuple(changed),{'descriptor':{}})
  # A second authenticated continuation composes with the first projection.
  with D.dispatcher_selection(dispatcher,(*env[:6],new_root,controls),worker):
   assert dispatcher.selected((*env[:6],new_root,controls),{'descriptor':{}})==(worker,'recipe')
  worker.campaign.model_slots=2
  with pytest.raises(ValueError,match='model pool differs'):dispatcher.selected(env,{'descriptor':{}})
  worker.campaign.model_slots=1
  I.selected=lambda row:(SimpleNamespace(root=tmp_path/'foreign',run=run),controls,'recipe')
  with pytest.raises(ValueError,match='outside the declared successor'):dispatcher.selected(env,{'descriptor':{}})
 assert dispatcher.selected is old
 with pytest.raises(RuntimeError):
  with D.dispatcher_selection(dispatcher,env,worker):raise RuntimeError('owning failure')
 assert dispatcher.selected is old


def test_original_skill_loader_switches_only_snapshot_bound_cache(tmp_path,monkeypatch):
 import sys
 from scripts.batch import harbor_readiness as readiness
 relative='.cursor/skills/task-disclosure/scripts/disclose.py';snapshots=[];paths=[]
 for name in ('old','new'):
  root=tmp_path/name;p=root/relative;p.parent.mkdir(parents=True);p.write_text('VALUE='+repr(name)+'\n');paths.append(p)
  snapshots.append(U.keep(tmp_path/(name+'.json'),dict(root=str(root),files=[dict(relative=relative,sha256=D.binding(p)['sha256'])])))
 old=D.module('disclose',D.binding(paths[0]));monkeypatch.setitem(sys.modules,'disclose',old)
 loader=readiness.skill_module
 with pytest.raises(Exception,match='foreign cached skill'):loader(paths[1].parents[4],'task-disclosure','disclose')
 with D.skill_modules(*snapshots):
  assert readiness.skill_module(tmp_path/'new','task-disclosure','disclose').VALUE=='new'
  assert readiness.skill_module(tmp_path/'old','task-disclosure','disclose')is not old
  assert readiness.skill_module(tmp_path/'old','task-disclosure','disclose').VALUE=='old'
  foreign=SimpleNamespace(__file__=str(tmp_path/'foreign.py'));sys.modules['disclose']=foreign
  with pytest.raises(ValueError,match='foreign cached skill origin'):readiness.skill_module(tmp_path/'new','task-disclosure','disclose')
 assert readiness.skill_module is loader and sys.modules['disclose']is old
 with pytest.raises(ValueError,match='selected skill snapshot changed'):
  with D.skill_modules(*snapshots):
   paths[1].write_text('VALUE="tampered"\n');readiness.skill_module(tmp_path/'new','task-disclosure','disclose')
 assert readiness.skill_module is loader and sys.modules['disclose']is old


def test_direct_completed_tail_skips_permit_and_first_instruction(tmp_path,monkeypatch):
 old_home=tmp_path/'original';old_home.mkdir();(old_home/'permit').mkdir();new_home=tmp_path/'continued'
 checkpoint=U.keep(old_home/'permit/result.json',dict(descriptor={'actual':'descriptor'}))
 U.keep(old_home/'permit-resource.json',{'actual':'successful original permit guard'})
 old_plan=U.keep(tmp_path/'old-plan.json',dict(output=str(old_home),owner='old'))
 completed=U.keep(tmp_path/'completed.json',dict(plan=old_plan))
 p=dict(output=str(new_home),helper={'selected':'helper'},completed_step=completed)
 row=U.keep(tmp_path/'new-plan.json',p);outcome={'actual':'completed first instruction'};calls=[]
 def forbidden(*a,**k):raise AssertionError('completed command or permit replay')
 I=SimpleNamespace(_prior=lambda *a:None,run=forbidden)
 env=(None,I,None,None,None,{'run_root':str(tmp_path/'run')},tmp_path,{})
 dispatcher=SimpleNamespace(successful=lambda *a:None)
 def run(job):
  assert D.read(job)['outcome']==outcome;calls.append('dispatcher');raise RuntimeError('after original dispatcher admission')
 dispatcher.run=run;utility=SimpleNamespace(keep=U.keep,custody=lambda *a:None,operations_for=lambda *a:tmp_path/'new-operations')
 @contextmanager
 def context(*a,**k):yield p,utility,dispatcher,env,SimpleNamespace(root=tmp_path),None,None,None,forbidden,{'retained':'job'}
 monkeypatch.setattr(D,'context',context);monkeypatch.setattr(D,'completed_step',lambda *a:({},outcome));monkeypatch.setattr(D,'install_commands',lambda *a:None)
 with pytest.raises(RuntimeError,match='dispatcher admission'):D.run(row)
 assert calls==['dispatcher']and not(new_home/'permit').exists()and not(new_home/'first-step').exists()


def test_explicit_disclosure_stop_preserves_successful_boundary(tmp_path):
 p=dict(output=str(tmp_path),workbook='0280');row={'actual':'plan'};out={'actual':'original completed outcome'};checkpoint={'actual':'checkpoint'}
 dispatcher=SimpleNamespace(successful=lambda value,env:(checkpoint,dict(status='STEP_COMPLETE',next_step='disclosure-review')))
 selected=D.stopped_disclosure(U,p,row,{'config':{'original':'config'}},{'actual':'permit guard'},out,dispatcher,())
 v=D.read(selected)
 assert v['status']=='DISCLOSURE_BUILD_COMPLETE'and v['outcome']==out and v['checkpoint']==checkpoint and v['publication_performed']is False
 assert set(x.name for x in tmp_path.iterdir())=={'result.json'}
 for status,next_step in [('MODEL_REQUIRED','disclosure-review'),('STEP_COMPLETE','dialogue-review')]:
  dispatcher.successful=lambda *a:(checkpoint,dict(status=status,next_step=next_step))
  with pytest.raises(ValueError,match='completed disclosure boundary'):D.stopped_disclosure(U,p,row,{},None,out,dispatcher,())


def test_renew_only_stops_after_original_guarded_renewal(tmp_path,monkeypatch):
 from contextlib import nullcontext
 home=tmp_path/'output';operations=tmp_path/'operations';p=dict(output=str(home),helper={'bound':'helper'},preparation_resume={'bound':'failure'},workbook='0280');row=U.keep(tmp_path/'plan.json',p)
 old={'actual':'completed'};checkpoint={'actual':'completed checkpoint'};held={'actual':'early hold'};holdcp={'actual':'hold checkpoint'};renewed={'actual':'renewed'};renewcp={'actual':'renew checkpoint'};worker_cp={'actual':'worker completed checkpoint'};calls=[]
 value=dict(status='STEP_COMPLETE',descriptor={'actual':'descriptor'},checkpoint=worker_cp,next_step='disclosure-review')
 def successful(out,*a):
  if out==old:return checkpoint,value
  if out==held:return holdcp,dict(status='LEASE_RENEWAL_REQUIRED',checkpoint=worker_cp,next_step='disclosure-review')
  assert out==renewed;return renewcp,dict(status='RETRY_READY',next_step='disclosure-review')
 def step(*a,**kw):
  assert kw['models']is kw['commands']is kw['containers']is False;calls.append('original I');return held
 def renew(*a):calls.append('original guarded R');return renewed
 dispatcher=SimpleNamespace(successful=successful,fleet_slot=lambda _:nullcontext((19,)),renew=renew)
 descriptor=U.keep(tmp_path/'descriptor.json',dict(worker={'actual':'worker'}));value['descriptor']=descriptor
 env=(None,SimpleNamespace(run=step,_prior=lambda *a:(None,'current selection','current lease'),worker_decode=lambda _:worker),None,None,None,{},None,{})
 worker=SimpleNamespace(root=tmp_path)
 def active(w,s,l):
  assert(s,l)==('current selection','current lease');return None,dict(leases=dict(task_agent=dict(expires_at=D.time.time_ns()+2000*10**9))),None
 K=SimpleNamespace(W=SimpleNamespace(active=active))
 utility=SimpleNamespace(keep=U.keep,custody=lambda *a:None,operations_for=lambda *a:operations)
 @contextmanager
 def context(*a):yield p,utility,dispatcher,env,worker,None,None,K,None,dict(config={'old':'config'})
 monkeypatch.setattr(D,'context',context);monkeypatch.setattr(D,'completed_step',lambda *a:({},old));monkeypatch.setattr(D,'install_commands',lambda *a:None)
 result=D.read(D.renew_only(row));assert calls==['original I','original guarded R']and result['status']=='LEASE_RENEWED'and result['review_dispatched']is False and result['outcome']==renewed


def test_completed_step_accepts_exact_successful_stop_receipt(tmp_path):
 code=tmp_path/'I.py';code.write_text('# original I\n');oldhome=tmp_path/'original';(oldhome/'permit').mkdir(parents=True);(oldhome/'first-step').mkdir();original_job=U.keep(tmp_path/'original-job.json',dict(config={'original':'config'}))
 old=dict(output=str(oldhome),helper={'original':'helper'},owner='old',fence=1,workbook='0280',original_job=original_job,stop_after_disclosure_build=True)
 plan=U.keep(tmp_path/'old-plan.json',old);prior=U.keep(oldhome/'permit/result.json',dict(descriptor={'original':'descriptor'}));guard=U.keep(oldhome/'permit-resource.json',{'original':'guard'})
 intent=U.keep(oldhome/'first-step/intent.json',dict(action='step',code=D.binding(code),arguments=dict(descriptor={'original':'descriptor'},checkpoint=prior),timeout_seconds=3300,memory_limit_bytes=8589934592,controls={}))
 resource=U.keep(oldhome/'first-step/resource.json',dict(returncode=0,failure=None,intent=intent));cp=U.keep(oldhome/'first-step/result.json',dict(status='STEP_COMPLETE',next_step='disclosure-review',descriptor={'original':'descriptor'}));out=U.keep(oldhome/'first-step/outcome.json',dict(status='COMPLETED_OPERATION',result=cp,resource=resource))
 result=U.keep(oldhome/'result.json',dict(status='DISCLOSURE_BUILD_COMPLETE',workbook='0280',continuation=plan,config={'original':'config'},outcome=out,checkpoint=cp,permit_guard=guard,publication_performed=False,automatic_retry=False))
 job=U.keep(tmp_path/'worker-job.json',dict(result=str(oldhome/'result.json'),driver=old['helper']));wi=U.keep(tmp_path/'worker-intent.json',dict(owner='old',job=job));outer=U.keep(tmp_path/'outer.json',dict(returncode=0,failure_type=None,result=result,intent=wi));proof=U.keep(tmp_path/'completed.json',dict(plan=plan,outcome=out,worker_outcome=outer));p={k:v for k,v in old.items()if k!='stop_after_disclosure_build'};p.update(output=str(tmp_path/'new'),helper={'new':'helper'},owner='new',fence=2,completed_step=proof)
 env=(SimpleNamespace(encode=lambda x:x),SimpleNamespace(__file__=str(code),_prior=lambda *a:None),None,None,None,dict(run_root=str(tmp_path/'run')),None,{})
 assert D.completed_step(p,env)==(old,out)
 ops=tmp_path/'run/dispatcher-v2/original';ops.mkdir(parents=True)
 with pytest.raises(ValueError,match='stopped disclosure outer differs'):D.completed_step(p,env)


def test_unused_renewal_failure_requires_marker_only_namespace(tmp_path):
 home=tmp_path/'failed';home.mkdir();helper={'actual':'old helper'};old=dict(output=str(home),owner='old',fence=6,helper=helper,preparation_resume={'actual':'preparation'})
 prior=U.keep(tmp_path/'prior-plan.json',old);marker=U.keep(home/'intent.json',dict(plan=prior,helper=helper,automatic_retry=False));job=U.keep(tmp_path/'job.json',dict(driver=helper,result=str(home/'result.json')));intent=U.keep(tmp_path/'worker-intent.json',dict(owner='old',job=job));log=tmp_path/'log';log.write_text('scripts.batch.harbor_admission.AdmissionError: selected stage changed\n');outer=U.keep(tmp_path/'outcome.json',dict(returncode=1,failure_type=None,result=None,intent=intent,log=D.binding(log)));proof=U.keep(tmp_path/'failure.json',dict(plan=prior,outcome=outer,marker=marker));p=dict(old,owner='new',fence=7,helper={'actual':'corrected helper'},output=str(tmp_path/'new'),renewal_preparation_failure=proof);env=(None,None,None,None,None,dict(run_root=str(tmp_path/'run')))
 assert D.renewal_preparation_failure(p,env)==old
 (home/'renewal-intent.json').write_text('{}')
 with pytest.raises(ValueError,match='renewal already began'):D.renewal_preparation_failure(p,env)

def test_direct_runtime_inputs_include_custody_even_if_probe_replaces_it(tmp_path):
 files={}
 for key in ('helper','utilities','stdlib','capacity_policy','authorization','profile','core','runtime','runtime_profile'):
  path=tmp_path/(key+'.py');path.write_text('# bound\n');files[key]=D.binding(path)
 p=dict(files,context={'helper':files['utilities']},renewal_module=None)
 assert D.runtime_inputs(p)==list(files.values())
 Path(files['capacity_policy']['path']).unlink()
 with pytest.raises(ValueError):D.runtime_inputs(p)

def test_direct_runtime_inputs_reject_changed_dependency(tmp_path):
 path=tmp_path/'bound.py';path.write_text('# bound\n');row=D.binding(path)
 p={key:row for key in ('helper','utilities','stdlib','capacity_policy','authorization','profile','core','runtime','runtime_profile')};p.update(context={'helper':row},renewal_module=row)
 path.write_text('# changed\n')
 with pytest.raises(ValueError):D.runtime_inputs(p)

@pytest.mark.parametrize('selected_seconds',[1350,3300])
def test_original_step_specific_guard_is_retained_in_cold_sidecar(tmp_path,selected_seconds):
 p,dispatcher,env,job,intent,command=fixture(tmp_path,seconds=selected_seconds);calls=[]
 row=U.keep(tmp_path/'plan.json',{})
 D.install_commands(row,p,U,dispatcher,env,lambda argv,**options:calls.append((argv,options)),job)
 with pytest.raises(ValueError,match='original child guard'):
  env[0]._run_in_process_group(command,timeout_seconds=selected_seconds+1,memory_limit_bytes=8589934592)
 assert not calls and not (Path(intent['path']).parent/'disclosure-bootstrap.json').exists()
 env[0]._run_in_process_group(command,timeout_seconds=selected_seconds,memory_limit_bytes=8589934592)
 side=D.read(D.binding(Path(intent['path']).parent/'disclosure-bootstrap.json'))
 assert side['seconds']==selected_seconds and calls[0][1]['timeout_seconds']==selected_seconds

@pytest.mark.parametrize('fails',[False,True])
def test_retained_context_manager_lives_until_consumer_exits(monkeypatch,fails):
 events=[]
 @contextmanager
 def original(plan,**kw):
  assert plan=='retained'and kw=={'initial':False}
  events.append('enter')
  try:yield ('dispatcher','environment')
  finally:events.append('exit')
 monkeypatch.setattr(D,'module',lambda *a:SimpleNamespace(context=original))
 c=dict(manager='contextmanager',function='context',helper={},plan='retained',kwargs={'initial':False})
 try:
  with D.retained_loader(c)as(_,value):
   assert value==('dispatcher','environment')and events==['enter']
   if fails:raise RuntimeError('later validation fails')
 except RuntimeError:
  assert fails
 assert events==['enter','exit']

@pytest.mark.parametrize('manager',['uncontrolled','contextmanager'])
def test_retained_loader_rejects_untyped_or_unknown_context(monkeypatch,manager):
 monkeypatch.setattr(D,'module',lambda *a:SimpleNamespace(load=lambda *a,**k:()))
 with pytest.raises(ValueError):
  with D.retained_loader(dict(manager=manager,function='load',helper={},plan={},kwargs={})):pass


def test_source_review_checkpoint_requires_original_completed_renewal(monkeypatch,tmp_path):
 from types import SimpleNamespace
 from scripts.fleet import disclosure_command_continue as M
 receipt={'path':'renewal','sha256':'r'};cp={'path':'build','sha256':'b'}
 worker=SimpleNamespace(run=tmp_path,lease_renewal=receipt);calls=[]
 def predecessor(w,b):calls.append((w,b));return cp
 renewal=SimpleNamespace(checkpoint_predecessor=predecessor)
 monkeypatch.setattr(M,'binding',lambda p:cp)
 value=dict(status='RETRY_READY',next_step='disclosure-review',checkpoint=receipt)
 assert M.source_review_checkpoint(value,worker,renewal)and calls==[(worker,receipt)]
 assert not M.source_review_checkpoint(dict(value,checkpoint={'path':'foreign','sha256':'f'}),worker,renewal)
 renewal.checkpoint_predecessor=lambda w,b:{'path':'other','sha256':'o'}
 import pytest
 with pytest.raises(ValueError,match='original completed disclosure build'):M.source_review_checkpoint(value,worker,renewal)


def test_direct_permit_reader_accepts_only_existing_exact_branch(tmp_path,case):
 import ast
 worker,s,l,*_=case
 current=Path(I.__file__).read_text();tree=ast.parse(current)
 fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_prior')
 target=next(n for n in ast.walk(fn)if isinstance(n,ast.If)and ast.unparse(n.test)=="completion.get('schema_version') == 'harbor-disclosure-command-repair/v1'")
 class Remove(ast.NodeTransformer):
  def visit_If(self,node):return node.orelse[0]if node is target else self.generic_visit(node)
 Remove().visit(tree)
 old=tmp_path/'old.py';old.write_text(ast.unparse(tree));new=tmp_path/'selected.py';new.write_text(current)
 assert D.direct_permit_reader(D.binding(old),D.binding(new))==D.binding(new)
 for changed in(current.replace("configured.code_repair==bound","True"),current.replace("'retry lease/selection differs'","'foreign check'")):
  new.write_text(changed)
  with pytest.raises(ValueError):D.direct_permit_reader(D.binding(old),D.binding(new))


def written_permit_fixture(case):
 worker,s,l,*_=case;home=worker.run/'written';home.mkdir(parents=True);helper=W.keep(home/'helper.json',{})
 tracker=W.keep(home/'tracker.json',{});profile=W.keep(home/'profile.json',{'stage_tree':{'f':'a'}})
 p=dict(helper=helper,owner='old-owner',fence=2,output=str(home),runtime_profile=helper,profile=profile,tracker=tracker)
 old=W.keep(home/'plan.json',p)
 original=W.keep(home/'original.json',{})
 descriptor=W.keep(home/'descriptor-original.json',dict(worker=I.worker_encode(worker),initial={},origin=[],code=helper,controls={},dependencies={},schema_version=I.SCHEMA))
 permit=W.keep(home/'permit/permit.json',dict(schema_version=Q.SCHEMA,selection=I.codec.encode(s),lease=asdict(l)))
 configured=__import__('dataclasses').replace(worker,code_repair=permit)
 descriptor=W.keep(home/'permit/descriptor.json',dict(worker=I.worker_encode(configured)))
 checkpoint=W.keep(home/'permit/result.json',I._result('RETRY_READY',descriptor,s,l,checkpoint=permit,next_step='disclosure-build'))
 marker=W.keep(home/'intent.json',dict(plan=old,helper=helper,automatic_retry=False));log=home/'permit.log';log.write_text("Traceback\nKeyError: 'permit'\n")
 command=['python','-I','-B',helper['path'],'permit-child','--plan',old['path'],'--sha256',old['sha256']]
 guard=W.keep(home/'permit-resource.json',dict(returncode=1,failure=None,seconds=3300,memory_bytes=8589934592,models_dispatched=0,intent=marker,log=D.binding(log),command=command))
 job=W.keep(home/'job.json',dict(driver=helper,inputs=[old],result=str(home/'result.json'),run=str(home/'worker'),command=['python']))
 intent=W.keep(home/'worker/intent.json',dict(job=job,owner='old-owner'))
 outcome=W.keep(home/'worker/outcome.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False))
 process=W.keep(home/'process.json',dict(pid=99999999,job=job))
 a=dict(plan=old,checkpoint=checkpoint,guard=guard,outcome=outcome,job=job,process=process,reader=helper)
 bound=W.keep(home/'reconciliation.json',a)
 fresh=dict(p,owner='new-owner',fence=3,output=str(home/'fresh'),completed_permit=bound)
 core=SimpleNamespace(validate=lambda *a:None,tree=lambda *a:{'f':'a'},producer_run=lambda *a:home/'permit/producer',W=SimpleNamespace(step_directory=lambda *a:home/'permit/steps/disclosure-build',refreshed=lambda x:x))
 env=(I.base,I,None,None,None,dict(run_root=str(worker.run)),None,None)
 return fresh,env,worker,s,l,core,home,a


def test_written_permit_reuses_exact_checkpoint_and_preserves_consumed_authorization(case):
 p,env,w,s,l,K,home,a=written_permit_fixture(case)
 actual,selected,lease=D.completed_permit(p,env,w,s,l,K,continued=False)
 assert actual.code_repair==D.read(a['checkpoint'])['checkpoint']and selected==s and lease==l
 assert not(home/'first-step').exists()and not(home/'fresh').exists()


@pytest.mark.parametrize('changed',['source','fence','later_step','log','guard','invalid_worker','detached_checkpoint'])
def test_written_permit_rejects_changed_custody_before_execution(case,changed):
 p,env,w,s,l,K,home,a=written_permit_fixture(case)
 if changed=='source':p['source']={'foreign':'input'}
 elif changed=='fence':p['fence']=4
 elif changed=='later_step':(home/'first-step').mkdir()
 elif changed=='log':(home/'permit.log').write_text('another failure')
 elif changed=='guard':
  g=D.read(a['guard']);g['models_dispatched']=1;a['guard']=W.keep(home/'foreign-guard.json',g)
 elif changed=='invalid_worker':
  import os
  a['process']=W.keep(home/'live.json',dict(pid=1,job=a['job']))
 else:
  cp=D.read(a['checkpoint']);cp['next_step']='dialogue-review';a['checkpoint']=W.keep(home/'foreign-checkpoint.json',cp)
 if changed in('guard','invalid_worker','detached_checkpoint'):p['completed_permit']=W.keep(home/'foreign-reconciliation.json',a)
 with pytest.raises(ValueError):D.completed_permit(p,env,w,s,l,K,continued=False)


def test_command_routing_installs_only_selected_functions_in_original_namespace(tmp_path,monkeypatch):
 from types import ModuleType
 worker=ModuleType('original_worker');worker.__file__=str(Path(W.__file__));worker.__dict__.update(vars(W))
 repair=ModuleType('original_repair');from scripts.batch import harbor_instruction_repair as R
 repair.__dict__.update(vars(R))
 # Reverse only the known branches to produce the real predecessor functions.
 current=Path(W.__file__).read_text()
 current=current.replace('    from scripts.batch.harbor_disclosure_command_repair import producer_run\n','').replace('                         producer_run(worker), selection.content.model_policy',"                         worker.run/'producer', selection.content.model_policy")
 current=current.replace("    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if phase=='disclosure-build' and command_repair.selected(worker):\n        return command_repair.remaining_operation(worker,selection,lease,directory,commands=commands,lock_fd=lock_fd)\n",'')
 current=current.replace("    if worker.code_repair is not None and not pending_now and next_phase=='disclosure-build':\n        from scripts.batch.harbor_disclosure_command_repair import selected as command_repair_selected\n        if command_repair_selected(worker):required=worker.code_repair\n",'')
 old=tmp_path/'old_worker.py';old.write_text(current);worker.__file__=str(old)
 oldr=tmp_path/'old_repair.py';oldr.write_text(Path(R.__file__).read_text().replace('    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if command_repair.selected(worker):return command_repair.validate(worker,before)\n',''));repair.__file__=str(oldr)
 rows={}
 for kind,original in [('worker',worker),('repair',repair)]:
  p=tmp_path/(kind+'.py');p.write_text(D.routed_source(Path(original.__file__).read_text(),kind));rows[kind]=D.binding(p)
 before=(worker.task,worker._operation,worker._step,repair.validate);env=(None,None,None,worker,repair)
 with D.command_routing({'command_routing':rows},env):
  assert worker._step.__globals__ is worker.__dict__ and worker.task.__globals__ is worker.__dict__
  assert repair.validate.__globals__ is repair.__dict__
  assert worker.__file__==str(old)and repair.__file__==str(oldr)
  calls=[];monkeypatch.setattr(Q,'selected',lambda w:True);monkeypatch.setattr(Q,'validate',lambda *a:calls.append(a)or {'passed':'actual core'})
  assert repair.validate('worker','before')=={'passed':'actual core'}and calls==[('worker','before')]
 assert (worker.task,worker._operation,worker._step,repair.validate)==before
 changed=Path(rows['worker']['path']);changed.write_text(changed.read_text().replace("'exact root-selected preceding checkpoint hash required'","'weakened'"));rows['worker']=D.binding(changed)
 with pytest.raises(ValueError,match='unrelated command routing'):
  with D.command_routing({'command_routing':rows},env):pass
 assert (worker.task,worker._operation,worker._step,repair.validate)==before
