"""Synthetic gates plus the actual subprocess/receipt launch function."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from scripts.fleet import launch_job as L


@pytest.fixture
def gate():
    source='a'*64;prior={'path':'/retained/outcome.json','sha256':'b'*64}
    p={'expected_fence':3,'expected_owner':'hhu-server:old','expected_version':'v1','prior_outcome':prior}
    job={'workbook':'0270','source':{'sha256':source}}
    status={'model_limit':1,'publication_limit':1,'operations':[],
        'workbooks':[{'workbook':'0270','state':'hold','fence':3,'owner':'hhu-server:old',
        'version':'v1','source_sha256':source,'reason':json.dumps(prior)}]}
    return status,p,job


def test_exact_held_owner_passes(gate):
    status,p,job=gate
    assert L.central_gate(status,p,job)==status['workbooks'][0]


@pytest.mark.parametrize('field,value',[('state','ready'),('fence',4),('owner','hhu-server:other'),
    ('version','other'),('source_sha256','c'*64),('reason','{}')])
def test_changed_hold_never_readies(gate,field,value):
    status,p,job=gate;status['workbooks'][0][field]=value
    with pytest.raises(ValueError):L.central_gate(status,p,job)


@pytest.mark.parametrize('kind,state',[('model','active'),('model','hold'),('publication','active'),('publication','hold')])
def test_global_uncertainty_blocks_launch(gate,kind,state):
    status,p,job=gate;status['operations']=[{'kind':kind,'state':state}]
    with pytest.raises(ValueError,match='global operation'):L.central_gate(status,p,job)


@pytest.mark.parametrize('limit',['model_limit','publication_limit'])
def test_global_limits_cannot_relax(gate,limit):
    status,p,job=gate;status[limit]=2
    with pytest.raises(ValueError,match='limits'):L.central_gate(status,p,job)


def spawn_payload(tmp_path):
    return {'receipt':str(tmp_path/'worker.json'),'command':[sys.executable,'-c',"print('synthetic launched',flush=True)"],
        'cwd':str(tmp_path),'job':{'path':'/synthetic/job.json','sha256':'a'*64},
        'root_intent':{'path':'/synthetic/root-intent.json','sha256':'b'*64}}


def test_actual_spawn_records_pid_intent_log_and_rejects_replay(tmp_path):
    namespace={};exec(L.function_source('remote_spawn'),namespace)
    payload=spawn_payload(tmp_path);result=namespace['remote_spawn'](payload)
    assert result['pid']>0 and result['command']==payload['command']
    os.waitpid(result['pid'],0)
    assert json.loads((tmp_path/'worker.json').read_text())==result
    assert json.loads((tmp_path/'worker.intent.json').read_text())['root_intent']==payload['root_intent']
    assert (tmp_path/'worker.log').read_text().strip()=='synthetic launched'
    with pytest.raises(ValueError,match='already attempted'):namespace['remote_spawn'](payload)


def test_direct_cli_import_can_build_remote_code_without_inspect_shadowing():
    script="import sys;sys.path.insert(0,'scripts/fleet');import launch_job;compile(launch_job.function_source('remote_spawn'),'<remote>','exec')"
    subprocess.run([sys.executable,'-c',script],check=True,capture_output=True)


def test_pre_subprocess_failure_is_once_only(tmp_path,monkeypatch):
    payload=spawn_payload(tmp_path);calls=[]
    def failed(*a,**k):calls.append(True);raise OSError('synthetic Popen failure')
    monkeypatch.setattr(subprocess,'Popen',failed)
    with pytest.raises(OSError):L.remote_spawn(payload)
    assert (tmp_path/'worker.intent.json').is_file()
    assert not (tmp_path/'worker.json').exists()
    with pytest.raises(ValueError,match='already attempted'):L.remote_spawn(payload)
    assert calls==[True]


def test_ssh_preflight_uses_exact_isolated_python(monkeypatch):
    seen=[]
    def run(command,**kw):
        seen.append(command)
        return subprocess.CompletedProcess(command,0,b'{"status":"VALIDATED_NOT_STARTED"}',b'')
    monkeypatch.setattr(subprocess,'run',run)
    python='/home/isolated-release/venv/bin/python'
    L.ssh({'host':'hhu-server'},{'command':[python]},'check',{})
    assert seen[0][-1].startswith(python+' -I -B -c ')
    assert 'import json,sys' in seen[0][-1]


def test_lost_reconcile_retains_intent_and_never_launches(tmp_path,monkeypatch,gate):
    status,p,job=gate;p={**p,'root_plan':{'path':'/root-plan','sha256':'q'},'coordinator_receipt':{'path':str(tmp_path/'coordinator.json'),'sha256':'z'}}
    class Client:
        def call(self,method,**kw):
            if method=='status':return status
            if method=='reconcile':raise TimeoutError('synthetic lost response')
            pytest.fail('unexpected call')
    monkeypatch.setattr(L,'load',lambda r:(p,job))
    monkeypatch.setattr(L,'checked',lambda r:{'url':'http://127.0.0.1:8765','private_directory':str(tmp_path)})
    monkeypatch.setattr(L,'Client',lambda *a:Client())
    calls=[]
    def remote(*args):calls.append(args[2]);return {'status':'VALIDATED_NOT_STARTED'}
    monkeypatch.setattr(L,'ssh',remote)
    with pytest.raises(TimeoutError):L.launch({'path':'/plan','sha256':'x'},tmp_path/'journal',execute=True)
    assert calls==['check']
    assert (tmp_path/'journal/intent.json').is_file()
    assert json.loads((tmp_path/'journal/uncertain.json').read_text())['phase']=='reconcile'
    with pytest.raises(ValueError,match='journal exists'):
        L.launch({'path':'/plan','sha256':'x'},tmp_path/'journal',execute=True)
    assert calls==['check','check']
