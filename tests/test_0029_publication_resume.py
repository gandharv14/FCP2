import importlib.util,inspect,json,os,sys,types
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];H=R/'diagnostics/vm-fleet-20260912/control/0029-publication-003'
s=importlib.util.spec_from_file_location('pubresume',H/'publish.py');P=importlib.util.module_from_spec(s);s.loader.exec_module(P)
PROOF={'path':str(H/'resume-proof.json'),'sha256':'f331afd71e6a9d0dcd002c2a768fe690f1adbcb6fdd45669b7decd2670931ecb'}
INTENT={'path':'/home/gmahajan_labelbox_com/synthetic-data-fleet/0029-publication-002/owning-publication/intent.json','sha256':'b5703876cbbe4b8919a16d011252632fd59be6ba49441e0dcfe5180c42cc4f95'}
def test_exact_owning_resume_request_and_code(monkeypatch):
    proof=P.checked(PROOF);resume=dict(journal=str(Path(INTENT['path']).parent),intent=INTENT,proof=PROOF)
    request=object();code=[{'old':'code'}];encoded={'exact':'old-request'}
    checked=P.checked
    def read(row):
        if row==PROOF:return proof
        if row==INTENT:return {'request':encoded,'code':code}
        if row==proof['readiness']:return {'publication_ready':True}
        return checked(row)
    monkeypatch.setattr(P,'checked',read);monkeypatch.setattr(P,'binding',lambda path:next(proof[k] for k in ('intent','queued_tracker','current_tracker','failed_resource','failed_log','runtime_command','readiness') if proof[k]['path']==str(path)))
    module=types.SimpleNamespace(encode=lambda r:encoded,_codes=lambda:code)
    plan=dict(journal='/new/outer',owning_resume=resume)
    assert P.retained_owning_resume(plan,request,[None,None,module])==(Path(resume['journal']),INTENT)
    module.encode=lambda r:{'changed':'request'}
    with pytest.raises(ValueError):P.retained_owning_resume(plan,request,[None,None,module])

def test_fresh_fallback_impossible():
    with pytest.raises(ValueError):P.retained_owning_resume({'journal':'/new'})
    source=inspect.getsource(P.remote_child)
    assert 'resume=owning_intent' in source
    assert 'run(request, Path(plan["journal"]) / "owning-publication")' not in source

def test_runtime_path_precedes_inherited_system_path(monkeypatch):
    monkeypatch.setattr(sys,'executable','/runtime/venv/bin/python');monkeypatch.setenv('PATH','/system/bin')
    auth=dict(workbook='0029',owner='pub',token='secret',fence=5)
    env=P.publication_environment({'workbook':'0029','code_root':'/task'},auth,action='collect',url='http://127.0.0.1:1',token_file='/token')
    assert env['PATH']=='/runtime/venv/bin'+os.pathsep+'/system/bin'
    assert 'HARBOR_FLEET_MODEL_JOURNAL' not in env

def test_native_proof_all_gates_and_no_mutation():
    proof=P.checked(PROOF);full=P.checked(proof['readiness'])
    assert proof['tracker_unchanged'] and not proof['promotion_started']
    assert proof['old_docker']!=proof['required_docker']==proof['corrected_docker']
    assert full['publication_ready'] and not full['blockers'] and all(v['checked'] for v in full['checks'].values())
