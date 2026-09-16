import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import harbor_campaign as C, harbor_instruction_campaign as I
from scripts.fleet import undispatched_campaign as A


def saved(path,value):
 path.write_text(json.dumps(value));return A.binding(path)


def test_existing_parent_is_valid_for_cold_child_but_dispatcher_still_refuses_replay(tmp_path):
 from scripts.fleet import advance_authoring_v8 as D
 config={'run_root':str(tmp_path)};path=tmp_path/'dispatcher-v2'/'continued'
 assert A.selected_operation_namespace(config,str(path))==path
 assert D.operation_namespace(config,str(path))==path
 path.mkdir(parents=True);saved(path/'intent.json',{'retained':True})
 assert A.selected_operation_namespace(config,str(path))==path
 with pytest.raises(ValueError,match='exists'):D.operation_namespace(config,str(path))
 assert A.checked(A.binding(path/'intent.json'))=={'retained':True}


@pytest.mark.parametrize('kind',['foreign','nested','relative','symlink','file'])
def test_operation_namespace_location_never_relaxed(tmp_path,kind):
 config={'run_root':str(tmp_path)};base=tmp_path/'dispatcher-v2';base.mkdir();path=base/'continued'
 if kind=='foreign':path=tmp_path/'other'
 elif kind=='nested':path=path/'child'
 elif kind=='relative':path=Path('continued')
 elif kind=='symlink':path.symlink_to(tmp_path,target_is_directory=True)
 else:path.write_text('not a journal directory')
 with pytest.raises(ValueError):A.selected_operation_namespace(config,str(path))


@pytest.fixture
def selected(tmp_path,monkeypatch):
 # Use original campaign run, original child intent validation and a real local
 # subprocess. Only the task operation itself is a labeled fixture.
 plan=saved(tmp_path/'reader.json',{'fixture':True})
 wrapper=tmp_path/'child.py'
 wrapper.write_text('''import sys,json\nfrom pathlib import Path\nsys.path.insert(0,REPO)\nfrom scripts import harbor_campaign as C\nfrom scripts.fleet import undispatched_campaign as A\nargs=sys.argv\narg=lambda x:args[args.index(x)+1]\nplan=dict(path=arg('--plan'),sha256=arg('--plan-sha256'))\nintent=dict(path=arg('--intent'),sha256=arg('--intent-sha256'))\nA.validate_child(C,base=C,kind=arg('--kind'),reader_plan=plan,child_wrapper=A.binding(__file__),intent=intent,reader_evidence=[])\nC._refresh=lambda arguments,controls,directory:dict(status='FIXTURE_CHILD_EXECUTED',models=0)\nC.child(intent)\n'''.replace('REPO',repr(str(Path(C.__file__).parents[1]))))
 controls=C.Controls(tmp_path/'slot.lock')
 args={k:saved(tmp_path/(k+'.json'),{'fixture':k})for k in ('legacy','checkpoint','current','lease')}
 monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=12*1024**3))
 run=A.select_run(C,base=C,kind='environment',reader_plan=plan,child_wrapper=A.binding(wrapper),producer_code=A.binding(C.__file__),reader_evidence=[])
 return run,controls,args,tmp_path,plan,wrapper


def test_real_guarded_child_keeps_original_intent_and_no_replay(selected):
 run,controls,args,root,plan,wrapper=selected
 producer_before=Path(C.__file__).read_bytes();original_run=C.run
 result=A.checked(run('refresh',args,controls,root/'operation'))
 assert result['status']=='COMPLETED_OPERATION'
 assert A.checked(result['result'])==dict(status='FIXTURE_CHILD_EXECUTED',models=0)
 intent=A.checked(A.binding(root/'operation/intent.json'))
 assert intent['code']==A.binding(C.__file__) and intent['timeout_seconds']==3300
 sidecar=A.checked(A.binding(root/'operation/declared-runtime-adapter.json'))
 assert sidecar['original_command'][2]==str(Path(C.__file__).resolve())
 assert sidecar['selected_command'][3]==str(wrapper)
 assert sidecar['semantic_budget_reset']is False and sidecar['automatic_retry']is False
 assert Path(C.__file__).read_bytes()==producer_before and C.run is original_run
 with pytest.raises(ValueError,match='attempt exists'):run('refresh',args,controls,root/'operation')


def test_lost_child_response_preserves_new_intent_and_failure(tmp_path,monkeypatch):
 plan=saved(tmp_path/'reader.json',{});wrapper=saved(tmp_path/'child.json',{})
 args={k:saved(tmp_path/(k+'.json'),{})for k in ('legacy','checkpoint','current','lease')};calls=[]
 def lost(command,**options):
  calls.append(command)
  assert (Path(command[command.index('--intent')+1]).parent/'declared-runtime-adapter.json').is_file()
  raise TimeoutError('fixture response lost')
 monkeypatch.setattr(C,'_run_in_process_group',lost)
 monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=12*1024**3))
 run=A.select_run(C,base=C,kind='environment',reader_plan=plan,child_wrapper=wrapper,producer_code=A.binding(C.__file__),reader_evidence=[])
 outcome=A.checked(run('refresh',args,C.Controls(tmp_path/'slot.lock'),tmp_path/'attempt'))
 assert outcome['status']=='RECONCILIATION_REQUIRED'
 assert A.checked(outcome['resource'])['failure']['type']=='TimeoutError'
 with pytest.raises(ValueError):run('refresh',args,C.Controls(tmp_path/'slot.lock'),tmp_path/'attempt')
 assert len(calls)==1


@pytest.mark.parametrize('change',['source','wrapper','guard','dispatch','foreign-action'])
def test_exact_child_bindings_and_guards_reject_before_spawn(selected,change,monkeypatch):
 run,controls,args,root,plan,wrapper=selected
 command=[sys.executable,'-B',str(Path(C.__file__).absolute()),'_child',str(root/'intent.json'),'']
 value=dict(schema_version='harbor-coordinator-step/v1',action='refresh',arguments=args,
  controls=C.encode(controls),dispatch=False,code=A.binding(C.__file__),executable=A.binding(Path(sys.executable).resolve()),
  memory_limit_bytes=8*1024**3,timeout_seconds=3300)
 if change=='source':value['code']['sha256']='0'*64
 elif change=='guard':value['timeout_seconds']=6600
 elif change=='dispatch':value['dispatch']=True
 elif change=='foreign-action':value['action']='admit'
 else:wrapper.write_text('changed')
 bound=saved(root/'intent.json',value);command[-1]=bound['sha256']
 with pytest.raises(ValueError):run.declared_runner(command,timeout_seconds=3300,memory_limit_bytes=8*1024**3,grace_seconds=3,cwd=C.ROOT)
 assert not (root/'declared-runtime-adapter.json').exists()


def test_actual_instruction_contract_preserves_final_runtime_guard(tmp_path):
 prior=saved(tmp_path/'prior.json',dict(schema_version=I.SCHEMA,status='STEP_COMPLETE',next_step='final-runtime'))
 seconds=I.guard_seconds('step',dict(checkpoint=prior))
 intent=saved(tmp_path/'intent.json',dict(schema_version=I.SCHEMA,action='step',arguments=dict(checkpoint=prior),
  controls={},models=False,commands=False,containers=True,code=A.binding(I.__file__),base_code=A.binding(C.__file__),
  executable=A.binding(Path(sys.executable).resolve()),memory_limit_bytes=8*1024**3,timeout_seconds=seconds))
 command=[sys.executable,'-B',str(Path(I.__file__).absolute()),'_child',intent['path'],intent['sha256']]
 assert A.contract(I,C,'instruction',intent,command)==1350
 changed=A.checked(intent);changed['timeout_seconds']=3300;bad=saved(tmp_path/'changed.json',changed);command[-2:]=[bad['path'],bad['sha256']]
 with pytest.raises(ValueError,match='guard'):A.contract(I,C,'instruction',bad,command)


def test_renewal_keeps_original_bounded_seconds(tmp_path):
 producer=SimpleNamespace(__file__=__file__)
 intent=saved(tmp_path/'renew.json',dict(job={},checkpoint={},seconds=420,driver=A.binding(__file__)))
 command=[sys.executable,'-B',str(Path(__file__).absolute()),'renew-child',intent['path'],intent['sha256']]
 assert A.contract(producer,C,'renewal',intent,command)==420
 changed=A.checked(intent);changed['seconds']=7200;bad=saved(tmp_path/'bad.json',changed);command[-2:]=[bad['path'],bad['sha256']]
 with pytest.raises(ValueError):A.contract(producer,C,'renewal',bad,command)


def test_real_instruction_child_retains_final_runtime_capabilities_and_guard(tmp_path,monkeypatch):
 plan=saved(tmp_path/'reader.json',{})
 wrapper=tmp_path/'instruction-child.py'
 wrapper.write_text('''import sys\nfrom pathlib import Path\nsys.path.insert(0,REPO)\nfrom scripts import harbor_campaign as C,harbor_instruction_campaign as I\nfrom scripts.fleet import undispatched_campaign as A\na=sys.argv;arg=lambda k:a[a.index(k)+1]\nplan=dict(path=arg('--plan'),sha256=arg('--plan-sha256'));intent=dict(path=arg('--intent'),sha256=arg('--intent-sha256'))\nA.validate_child(I,base=C,kind='instruction',reader_plan=plan,child_wrapper=A.binding(__file__),intent=intent,reader_evidence=[])\nI._step=lambda arguments,controls,directory,**caps:dict(status='FIXTURE_RUNTIME_BOUNDARY',capabilities=caps,task_operations=0)\nI.child(intent)\n'''.replace('REPO',repr(str(Path(C.__file__).parents[1]))))
 controls=C.Controls(tmp_path/'slot.lock')
 descriptor=saved(tmp_path/'descriptor.json',{})
 checkpoint=saved(tmp_path/'checkpoint.json',dict(schema_version=I.SCHEMA,status='STEP_COMPLETE',next_step='final-runtime'))
 monkeypatch.setattr(I,'selected',lambda _: (None,controls,None))
 monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=12*1024**3))
 run=A.select_run(I,base=C,kind='instruction',reader_plan=plan,child_wrapper=A.binding(wrapper),producer_code=A.binding(I.__file__),reader_evidence=[])
 result=A.checked(run('step',dict(descriptor=descriptor,checkpoint=checkpoint),controls,tmp_path/'runtime',containers=True))
 assert result['status']=='COMPLETED_OPERATION'
 value=A.checked(result['result']);assert value==dict(status='FIXTURE_RUNTIME_BOUNDARY',capabilities=dict(models=False,commands=False,containers=True),task_operations=0)
 sidecar=A.checked(A.binding(tmp_path/'runtime/declared-runtime-adapter.json'))
 assert sidecar['timeout_seconds']==1350 and sidecar['memory_limit_bytes']==8*1024**3
 assert sidecar['producer_code']==A.binding(I.__file__)


def test_isolated_renewal_argv_retains_exact_original_contract(tmp_path):
 producer=SimpleNamespace(__file__=__file__)
 intent=saved(tmp_path/'renew.json',dict(job={},checkpoint={},seconds=420,driver=A.binding(__file__)))
 command=[sys.executable,'-I','-B',str(Path(__file__).absolute()),'renew-child',intent['path'],intent['sha256']]
 assert A.contract(producer,C,'renewal',intent,command)==420
 for changed in ([command[0],'-E',*command[2:]], command[:-2]+['/foreign',command[-1]]):
  with pytest.raises(ValueError):A.contract(producer,C,'renewal',intent,changed)
