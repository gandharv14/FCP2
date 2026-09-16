"""Actual guarded renewal launch must not import fleet/inspect as stdlib inspect."""
import ast,hashlib,json,os,sys,time,subprocess
from pathlib import Path
from types import SimpleNamespace
from scripts.fleet import advance_authoring_v8 as D

def test_real_renewal_child_imports_with_adjacent_shadow_and_keeps_guard(tmp_path,monkeypatch):
    child=tmp_path/'renew.py'
    child.write_text("import inspect,openpyxl,json,sys\nfrom pathlib import Path\nassert hasattr(inspect,'isgenerator')\nPath(sys.argv[2]).with_name('result.json').write_text(json.dumps({'status':'FIXTURE_RENEWAL_IMPORT_VALIDATED','isolated':sys.flags.isolated}))\n")
    (tmp_path/'inspect.py').write_text("raise RuntimeError('unsafe script-directory import')\n")
    lock=tmp_path/'slot.lock';lock.touch();calls=[]
    def run(command,**kw):
        calls.append((command,kw))
        return subprocess.run(command,stdout=kw['stdout'],stderr=subprocess.STDOUT,cwd=kw['cwd'],env=kw['env'],timeout=kw['timeout_seconds'],pass_fds=kw['guardian_lock_fds']).returncode
    base=SimpleNamespace(_controls=lambda _:lock,_run_in_process_group=run,MEMORY=8*1024**3)
    W=SimpleNamespace(active=lambda *a:(None,{'leases':{'task_agent':{'expires_at':time.time_ns()+500*10**9}}}))
    env=(base,None,None,W,None,{},tmp_path,None)
    monkeypatch.setattr(D,'renewal_context',lambda *a:(None,None,None,None,None))
    monkeypatch.setattr(D,'__file__',str(child))
    result=D.checked(D.renew({},env,{},tmp_path/'operation',()))
    assert result['status']=='COMPLETED_OPERATION'
    actual=D.checked(result['result']);assert actual=={'status':'FIXTURE_RENEWAL_IMPORT_VALIDATED','isolated':1}
    command,options=calls[0]
    assert options['memory_limit_bytes']==8*1024**3 and 60<=options['timeout_seconds']<=900
    assert options['grace_seconds']==3 and len(options['guardian_lock_fds'])==1
    assert D.checked(D.checked(result['resource'])['intent'])['seconds']==options['timeout_seconds']
    assert not list(tmp_path.rglob('__pycache__'))
