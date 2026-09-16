from pathlib import Path
import json,os,signal
import pytest
from scripts.fleet import oneoff_control as C
from scripts.fleet.migration import retain,binding,checked


@pytest.fixture
def remote(tmp_path,monkeypatch):
    job={'workbook':'0190'};jb=retain(tmp_path/'job.json',job);worker=tmp_path/'worker.py';worker.write_text('# worker\n')
    process={'pid':999999,'start_ticks':'77','command':['python','worker.py'],'job':jb};pb=retain(tmp_path/'process.json',process)
    p={'process':pb,'job':jb,'worker':binding(worker),'remote_interrupt_directory':str(tmp_path/'interrupt-001')}
    payload={'plan':p,'process':process,'job':job,'action':'status','root_intent':{'path':'/root/intent','sha256':'a'*64}}
    state={'ticks':'77','command':b'python\0worker.py\0','state':'S','absent':False};events=[]
    read_text=Path.read_text;read_bytes=Path.read_bytes
    def text(path,*args,**kw):
        if str(path)=='/proc/999999/stat':
            if state['absent']:raise FileNotFoundError
            fields=[state['state']]+['0']*18+[state['ticks']];return '999999 (python) '+' '.join(fields)
        return read_text(path,*args,**kw)
    def raw(path,*args,**kw):
        if str(path)=='/proc/999999/cmdline':return state['command']
        return read_bytes(path,*args,**kw)
    monkeypatch.setattr(Path,'read_text',text);monkeypatch.setattr(Path,'read_bytes',raw)
    monkeypatch.setattr(os,'pidfd_open',lambda pid,flags:os.open('/dev/null',os.O_RDONLY),raising=False)
    def send(fd,sig,info,flags):
        intent=json.loads((tmp_path/'interrupt-001/intent.json').read_text());assert intent['signal']=='SIGINT'
        assert sig==signal.SIGINT;events.append('SIGINT');state['absent']=True
    monkeypatch.setattr(signal,'pidfd_send_signal',send,raising=False)
    return payload,state,events


def test_status_does_not_signal_or_claim(remote):
    payload,state,events=remote;r=C.remote_control(payload)
    assert r['process']['state']=='RUNNING' and not r['interrupt_intent_exists'] and not events


@pytest.mark.parametrize('field,value',[('ticks','88'),('command',b'unrelated\0'),('absent',True),('state','Z')])
def test_changed_pid_command_or_exit_is_never_signaled(remote,field,value):
    payload,state,events=remote;state[field]=value;payload['action']='interrupt'
    with pytest.raises(ValueError,match='not running'):C.remote_control(payload)
    assert not events and not Path(payload['plan']['remote_interrupt_directory']).exists()


def test_once_only_interrupt_retains_intent_and_does_not_claim_cleanup(remote):
    payload,state,events=remote;payload['action']='interrupt';r=C.remote_control(payload)
    assert events==['SIGINT'] and r['status']=='SIGINT_SENT_RECONCILIATION_REQUIRED'
    assert r['cleanup_confirmed'] is False and r['claims_released'] is False and r['model_operations_settled'] is False
    state['absent']=False
    with pytest.raises(ValueError,match='already attempted'):C.remote_control(payload)
    assert events==['SIGINT']


def test_pid_changes_after_pidfd_open_no_signal(remote,monkeypatch):
    payload,state,events=remote;payload['action']='interrupt'
    def opened(pid,flags):state['ticks']='88';return os.open('/dev/null',os.O_RDONLY)
    monkeypatch.setattr(os,'pidfd_open',opened)
    with pytest.raises(ValueError,match='changed before'):C.remote_control(payload)
    assert not events


def test_lost_signal_reply_retains_remote_intent(remote,monkeypatch):
    payload,state,events=remote;payload['action']='interrupt'
    def uncertain(*args):raise OSError('uncertain signal transport')
    monkeypatch.setattr(signal,'pidfd_send_signal',uncertain)
    with pytest.raises(OSError):C.remote_control(payload)
    assert (Path(payload['plan']['remote_interrupt_directory'])/'intent.json').exists()
    with pytest.raises(ValueError,match='already attempted'):C.remote_control(payload)


def test_root_lost_reply_never_replays(tmp_path,monkeypatch):
    p={'schema_version':C.SCHEMA,'root_interrupt_directory':str(tmp_path/'interrupt-001')};monkeypatch.setattr(C,'load',lambda row:(p,{},{}));calls=[]
    def ssh(p,j,proc,action,root_intent=None):
        calls.append(action)
        if action=='status':return {'process':{'state':'RUNNING'},'interrupt_intent_exists':False}
        assert (tmp_path/'interrupt-001/intent.json').exists();raise TimeoutError('lost')
    monkeypatch.setattr(C,'ssh',ssh)
    with pytest.raises(TimeoutError):C.control({'path':'/plan','sha256':'x'},'interrupt')
    assert calls==['status','interrupt'] and (tmp_path/'interrupt-001/uncertain.json').exists()
    with pytest.raises(ValueError,match='already attempted'):C.control({'path':'/plan','sha256':'x'},'interrupt')
    assert calls==['status','interrupt']


def test_actual0270_prepare_checks_retained_launch(tmp_path):
    root=Path(__file__).resolve().parents[1];p=root/'diagnostics/vm-fleet-20260912/control/0270-normalization-generic-launch-001/launched.json'
    if not p.exists():pytest.skip('optional real launch fixture')
    plan=C.prepare(binding(p),tmp_path/'control');value,job,process=C.load(plan)
    assert value['workbook']=='0270' and process['pid']==342244 and not (tmp_path/'control/interrupt-001').exists()
    copy=Path(value['process_copy']['path']);copy.write_text('{}')
    with pytest.raises(ValueError):C.load(plan)


def test_v2_sibling_worker_interrupts_have_separate_namespaces(remote,monkeypatch):
    payload,state,events=remote;p=payload['plan'];parent=Path(p['process']['path']).parent
    p['schema_version']=C.SCHEMA;p['remote_interrupt_directory']=str(parent/'interrupts'/p['process']['sha256'])
    other=parent/'interrupts'/('b'*64);other.mkdir(parents=True);(other/'intent.json').write_text('{}')
    assert C.remote_control(payload)['interrupt_intent_exists'] is False
    def send(*args):
        assert (Path(p['remote_interrupt_directory'])/'intent.json').exists();events.append('SIGINT')
    monkeypatch.setattr(signal,'pidfd_send_signal',send)
    payload['action']='interrupt';C.remote_control(payload)
    assert events==['SIGINT'] and (other/'intent.json').read_text()=='{}'


def test_v2_does_not_bypass_a_legacy_interruption(remote):
    payload,state,events=remote;p=payload['plan'];parent=Path(p['process']['path']).parent
    p['schema_version']=C.SCHEMA;p['remote_interrupt_directory']=str(parent/'interrupts'/p['process']['sha256'])
    legacy=parent/'interrupt-001';legacy.mkdir();(legacy/'intent.json').write_text('{}')
    assert C.remote_control(payload)['interrupt_intent_exists'] is True
    payload['action']='interrupt'
    with pytest.raises(ValueError,match='legacy interruption'):C.remote_control(payload)
    assert not events and not Path(p['remote_interrupt_directory']).exists()


def test_legacy_plan_does_not_bypass_per_process_interruption(remote):
    payload,state,events=remote;p=payload['plan'];parent=Path(p['process']['path']).parent
    p['schema_version']=C.LEGACY_SCHEMA
    previous=parent/'interrupts'/p['process']['sha256'];previous.mkdir(parents=True)
    (previous/'intent.json').write_text('{}')
    assert C.remote_control(payload)['interrupt_intent_exists'] is True
    payload['action']='interrupt'
    with pytest.raises(ValueError,match='already attempted'):C.remote_control(payload)
    assert not events and not Path(p['remote_interrupt_directory']).exists()


def test_legacy_interrupt_rejected_before_any_remote_action(monkeypatch):
    monkeypatch.setattr(C,'load',lambda row:({'schema_version':C.LEGACY_SCHEMA},{},{}))
    monkeypatch.setattr(C,'ssh',lambda *a:pytest.fail('legacy control attempted remote action'))
    with pytest.raises(ValueError,match='status-only'):C.control({},'interrupt')
