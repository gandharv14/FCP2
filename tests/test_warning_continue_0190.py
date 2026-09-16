import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import warning_continue_0190 as M


def fixture(tmp_path):
    helper=tmp_path/'helper.py';helper.write_text('fixture')
    plan=M.keep(tmp_path/'plan.json',{'helper':M.binding(helper)})
    d=tmp_path/'step';d.mkdir()
    intent=M.keep(d/'intent.json',{'fixture':True})
    command=['python','-B','/original/I.py','_child',intent['path'],intent['sha256']]
    value=dict(schema_version='0190-warning-child-bootstrap/v1',plan=plan,helper=M.binding(helper),intent=intent,
               original_command=command,models=False,automatic_retry=False)
    return plan,intent,command,value


@pytest.mark.parametrize('defect',['models','intent','original_command','automatic_retry','helper','foreign_directory'])
def test_bootstrap_rejects_unselected_capabilities_or_identity(tmp_path,defect):
    plan,intent,command,v=fixture(tmp_path)
    if defect=='models':v['models']=True
    elif defect=='automatic_retry':v['automatic_retry']=True
    elif defect=='intent':v['intent']={**intent,'sha256':'0'*64}
    elif defect=='original_command':v['original_command']=['changed']
    elif defect=='helper':v['helper']={**v['helper'],'sha256':'0'*64}
    dest=tmp_path/'warning-bootstrap.json' if defect=='foreign_directory' else Path(intent['path']).parent/'warning-bootstrap.json'
    with pytest.raises(ValueError):M.validate_bootstrap(M.keep(dest,v),plan,intent,command)


def test_guarded_bootstrap_keeps_original_resource_and_lock_contract(tmp_path):
    helper=tmp_path/'helper.py';helper.write_text('fixture')
    base=tmp_path/'C.py';base.write_text('base');instruction=tmp_path/'I.py';instruction.write_text('instruction')
    plan=M.keep(tmp_path/'plan.json',dict(helper=M.binding(helper)))
    seen=[]
    def runner(command,*args,**kwargs):seen.append((command,args,kwargs));return 0
    C=SimpleNamespace(__file__=str(base),_run_in_process_group=runner)
    I=SimpleNamespace(__file__=str(instruction),SCHEMA='original-I')
    d=tmp_path/'step';d.mkdir();intent=M.keep(d/'intent.json',dict(schema_version=I.SCHEMA,action='step',models=False,
        code=M.binding(instruction),base_code=M.binding(base)))
    original=['python','-B',str(instruction),'_child',intent['path'],intent['sha256']]
    M.install_child_bootstrap(plan,C,I)
    kwargs=dict(timeout_seconds=1350,memory_limit_bytes=8589934592,guardian_lock_fds=(17,18),cwd=tmp_path)
    assert C._run_in_process_group(original,**kwargs)==0
    assert seen[0][2]==kwargs and original==['python','-B',str(instruction),'_child',intent['path'],intent['sha256']]
    sidecar=M.binding(d/'warning-bootstrap.json');M.validate_bootstrap(sidecar,plan,intent,original)
    assert seen[0][0][:4]==['python','-I','-B',str(helper)]
    with pytest.raises(FileExistsError):C._run_in_process_group(original,**kwargs)
    assert len(seen)==1


def test_model_enabled_original_intent_rejected_before_subprocess(tmp_path):
    p=tmp_path/'module.py';p.write_text('fixture');row=M.binding(p)
    plan=M.keep(tmp_path/'plan.json',dict(helper=row));intent=M.keep(tmp_path/'intent.json',dict(schema_version='I',action='step',models=True,code=row,base_code=row))
    C=SimpleNamespace(__file__=str(p),_run_in_process_group=lambda *a,**k:pytest.fail('model-enabled child ran'))
    I=SimpleNamespace(__file__=str(p),SCHEMA='I');M.install_child_bootstrap(plan,C,I)
    with pytest.raises(ValueError):C._run_in_process_group(['python','-B',str(p),'_child',intent['path'],intent['sha256']])
