import datetime
import json
from types import SimpleNamespace as NS
import pytest
from scripts import fleet_tunnels as T


def record(host='hhu-server',pid=8123,at=1789337500):
    return dict(host=host,pid=pid,pgid=pid,started_at=at,port=8765,command=T.command(host,8765))


@pytest.mark.parametrize('state',['running','orphaned','identity_mismatch'])
def test_live_ambiguous_or_reused_process_never_restarts(state):
    assert T.restart_decision(state,[],500,0,4)==state


def test_restart_backoff_and_hourly_consumption_are_bounded():
    assert T.restart_decision('absent',[],100,115,4)=='backoff'
    assert T.restart_decision('absent',[],115,115,4)=='restart'
    assert T.restart_decision('absent',[1,2,3,4],100,0,4)=='hourly_cap'
    assert T.restart_decision('absent',[1,2,3,4],3604,0,4)=='restart'


@pytest.mark.parametrize('fault',['none','changed-command','pid-reused','orphan','absent'])
def test_native_process_identity_includes_start_time_and_group(monkeypatch,fault):
    v=record();at=v['started_at']-(100 if fault=='pid-reused'else 0)
    stamp=datetime.datetime.fromtimestamp(at).strftime('%a %b %d %H:%M:%S %Y')
    command=' '.join(['/usr/bin/python3','/sdk/gcloud.py',*v['command'][1:]])
    if fault=='changed-command':command=command.replace('-R','-L')
    raw=f"{v['pid']} {v['pgid']} {stamp} {command}\n"
    if fault=='orphan':raw=f"9000 {v['pgid']} {stamp} ssh orphan\n"
    if fault=='absent':raw=''
    monkeypatch.setattr(T.subprocess,'run',lambda *a,**k:NS(stdout=raw))
    expected={'none':'running','changed-command':'identity_mismatch','pid-reused':'identity_mismatch','orphan':'orphaned','absent':'absent'}
    assert T.process_state(v)['state']==expected[fault]


def test_health_probe_only_requests_authenticated_status(monkeypatch):
    def run(command,**options):
        code=command[-1]
        assert '"action":"status"'in code and 'Bearer 'in code
        assert not any(x in code for x in ['"claim"','"begin"','"end"','"heartbeat"'])
        assert '-R'not in command
        return NS(returncode=0,stdout=json.dumps(dict(status='AUTHENTICATED_COORDINATOR_STATUS',mutations=0)),stderr='')
    monkeypatch.setattr(T.subprocess,'run',run)
    assert T.authenticated_status('hhu-server',8765)['healthy']
    with pytest.raises(ValueError):T.authenticated_status('foreign-host',8765)


def test_supervisor_preserves_adopted_records_and_only_restarts_dead_transport(tmp_path,monkeypatch):
    adopt=tmp_path/'adopt';adopt.mkdir();before={}
    for i,host in enumerate(T.HOSTS):
        p=adopt/(host+'.json');p.write_text(json.dumps(record(host,8123+i)));before[p]=p.read_bytes()
    clock=[0];calls=[];observations=[0]
    monkeypatch.setattr(T.time,'time',lambda:clock[0]);monkeypatch.setattr(T.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(T.time,'sleep',lambda delay:clock.__setitem__(0,clock[0]+delay))
    def state(v):
        observations[0]+=1
        absent=observations[0]>8 and v['host']=='hhu-server'and v['pid']==8123
        return dict(state='absent'if absent else 'running',members=[]if absent else[v['pid']])
    monkeypatch.setattr(T,'process_state',state)
    monkeypatch.setattr(T,'remote_listener',lambda host,port:dict(state='absent'))
    monkeypatch.setattr(T,'authenticated_status',lambda host,port:dict(host=host,healthy=True,observation=dict(status='AUTHENTICATED_COORDINATOR_STATUS',mutations=0),at=clock[0]))
    def start(host,root,port):
        calls.append((host,root));v=record(host,9999,clock[0]);T.durable(root/(host+'.json'),v);return v
    monkeypatch.setattr(T,'start_one',start)
    run=tmp_path/'supervisor';T.supervise(run,adopt,8765,interval=15,health_interval=30,duration=60)
    assert len(calls)==1 and calls[0][0]=='hhu-server'
    assert all(p.read_bytes()==raw for p,raw in before.items())
    events=[json.loads(x)for x in(run/'events.jsonl').read_text().splitlines()]
    assert len([x for x in events if x['kind']=='transport_restart_intent'])==1
    assert not json.loads((run/'intent.json').read_text())['workbook_actions_allowed']
    with pytest.raises(FileExistsError):T.supervise(run,adopt,8765,duration=60)
    assert len(calls)==1

@pytest.mark.parametrize('output,rc,expected',[('',0,'absent'),('LISTEN 127.0.0.1:8765 sshd pid=123',0,'occupied'),('',1,'unknown')])
def test_remote_listener_is_read_only_and_blocks_occupied_unknown(monkeypatch,output,rc,expected):
    def run(command,**kw):
        assert command[-1].startswith('sudo -n ss -H -lntp ')
        assert 'kill'not in command[-1] and '-R'not in command
        return NS(returncode=rc,stdout=output,stderr='')
    monkeypatch.setattr(T.subprocess,'run',run)
    assert T.remote_listener('hhu-server',8765)['state']==expected
