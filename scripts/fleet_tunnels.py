#!/usr/bin/env python3
"""Start/inspect/stop loopback reverse SSH tunnels for the eight authorized VMs.

SSH through IAP: remote 127.0.0.1:8765 forwards to coordinator 127.0.0.1:8765.
No credential contents are read or included in records. A failed/stale tunnel
is reported; restarting a tunnel never restarts a workbook or model request.
"""
import argparse
import datetime
import fcntl
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import time

HOSTS=['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)]
SUPERVISOR_SCHEMA='fleet-transport-supervisor/v1'


def live(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False


def command(host,port):
    return ['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a',
            '--tunnel-through-iap','--quiet','--','-N','-T','-o','ExitOnForwardFailure=yes',
            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
            '-R',f'127.0.0.1:{port}:127.0.0.1:{port}']


def start_one(host,root,port):
    record=root/(host+'.json')
    if record.exists():
        old=json.loads(record.read_text())
        if live(old['pid']):return {**old,'running':True,'reused':True}
        raise RuntimeError(f'{host}: stale tunnel record; inspect logs and use new run directory')
    cmd=command(host,port)
    with (root/(host+'.log')).open('xb') as log:
        process=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    value=dict(host=host,pid=process.pid,pgid=process.pid,port=port,command=cmd,started_at=time.time(),log=str(root/(host+'.log')))
    with record.open('x') as stream:json.dump(value,stream,sort_keys=True,indent=2)
    return value


def stop_one(value):
    pid=value['pid']
    if not live(pid):return {**value,'running':False}
    # Refuse PID reuse: stop only the recorded gcloud command/group.
    observed=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
    if 'gcloud' not in observed or value['host'] not in observed or 'compute ssh' not in observed:
        raise RuntimeError('PID command differs; manual process reconciliation required')
    os.killpg(value['pgid'],signal.SIGTERM)
    return {**value,'stop_requested':True}


def process_state(value):
    """A dead parent with remaining group members is not safe to duplicate."""
    rows=subprocess.run(['ps','-axo','pid=,pgid=,lstart=,command='],capture_output=True,text=True,check=True)
    members=[];parent=None
    for line in rows.stdout.splitlines():
        pieces=line.split(None,7)
        if len(pieces)!=8:continue
        pid,pgid=int(pieces[0]),int(pieces[1])
        if pgid!=value['pgid'] and pid!=value['pid']:continue
        members.append(pid)
        if pid==value['pid']:parent=(pgid,' '.join(pieces[2:7]),pieces[7])
    if parent is None:return dict(state='orphaned'if members else 'absent',members=members)
    pgid,started,observed=parent
    try:
        at=datetime.datetime.strptime(started,'%a %b %d %H:%M:%S %Y').timestamp()
        argv=shlex.split(observed);expected=command(value['host'],value['port'])[1:]
        matches=pgid==value['pid']==value['pgid'] and abs(at-value['started_at'])<=5
        matches=matches and len(argv)>len(expected) and argv[-len(expected):]==expected
        matches=matches and any('gcloud' in x for x in argv[:-len(expected)])
    except (ValueError,TypeError):matches=False
    return dict(state='running'if matches else 'identity_mismatch',members=members)


def authenticated_status(host,port,timeout=60):
    """Only an authenticated read-only coordinator status request crosses -R."""
    if host not in HOSTS or type(port)is not int or not 1024<=port<=65535:raise ValueError('authorized host/port required')
    code="""import json,time,urllib.request
from pathlib import Path
token=Path('/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token').read_text().strip()
request=urllib.request.Request('http://127.0.0.1:__PORT__/v1',data=b'{"action":"status","arguments":{}}',headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
with urllib.request.urlopen(request,timeout=15)as response:value=json.load(response)
assert value['schema_version']=='fleet-coordinator/v1'and abs(time.time()-value['at'])<60 and value['publication_limit']==1
print(json.dumps(dict(status='AUTHENTICATED_COORDINATOR_STATUS',at=value['at'],workbooks=len(value['workbooks']),events=value['events'],model_limit=value['model_limit'],publication_limit=1,mutations=0)))
""".replace('__PORT__',str(port))
    cmd=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet',
         '--command',shlex.join(['python3','-I','-B','-c',code])]
    try:
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise RuntimeError('status SSH failed: '+result.stderr[-800:])
        value=json.loads(result.stdout)
        if value.get('status')!='AUTHENTICATED_COORDINATOR_STATUS' or value.get('mutations')!=0:raise ValueError('invalid authenticated status')
        return dict(host=host,healthy=True,observation=value,at=time.time())
    except (subprocess.SubprocessError,OSError,ValueError,RuntimeError)as exc:
        return dict(host=host,healthy=False,error_type=type(exc).__name__,error=str(exc)[-1000:],at=time.time())


def remote_listener(host,port,timeout=60):
    """A remote leftover listener blocks replacement until independently absent."""
    if host not in HOSTS or type(port)is not int or not 1024<=port<=65535:raise ValueError('authorized host/port required')
    command_text=shlex.join(['sudo','-n','ss','-H','-lntp',f'sport = :{port}'])
    cmd=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',command_text]
    try:
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        if result.returncode:return dict(state='unknown',returncode=result.returncode,error=result.stderr[-1000:])
        return dict(state='occupied'if result.stdout.strip()else 'absent',listener=result.stdout.strip(),at=time.time())
    except (subprocess.SubprocessError,OSError)as exc:return dict(state='unknown',error_type=type(exc).__name__)


def restart_decision(state,attempts,now,next_at,hourly_cap):
    """Live, reused or orphaned identities never authorize a new SSH process."""
    if state!='absent':return state
    if len([at for at in attempts if now-3600<at<=now])>=hourly_cap:return 'hourly_cap'
    return 'backoff'if now<next_at else 'restart'


def durable(path,value,*,replace=False):
    path=Path(path);target=path.with_suffix(path.suffix+'.tmp')if replace else path
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as stream:json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    if replace:os.replace(target,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def supervise(root,adopt,port,*,interval=15,health_interval=120,hourly_cap=4,duration=86400):
    """Bounded transport-only recovery; journals are never automatically replayed."""
    root=Path(root).resolve();adopt=Path(adopt).resolve()
    if interval<5 or health_interval<30 or not 1<=hourly_cap<=8 or not 60<=duration<=86400:raise ValueError('bounded supervisor policy required')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    common=root.parent/'tunnel-supervisor.lock'
    with common.open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        records={}
        for host in HOSTS:
            path=adopt/(host+'.json');raw=path.read_bytes();value=json.loads(raw)
            if value['host']!=host or value['port']!=port or value['command']!=command(host,port):raise ValueError('adopted tunnel identity differs')
            observed=process_state(value)
            if observed['state']not in('running','absent'):raise ValueError('adopted tunnel requires process reconciliation')
            records[host]=dict(record=value,record_binding=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()),attempts=[],failures=0,next_restart=time.time()+interval,observed=observed)
        intent=dict(schema_version=SUPERVISOR_SCHEMA,implementation=dict(path=str(Path(__file__).resolve()),sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
            started_at=time.time(),pid=os.getpid(),port=port,adopt=str(adopt),hosts=HOSTS,interval=interval,health_interval=health_interval,
            hourly_restart_cap_per_host=hourly_cap,maximum_duration_seconds=duration,maximum_backoff_seconds=300,workbook_actions_allowed=False,model_actions_allowed=False)
        durable(root/'intent.json',intent)
        sequence=0;health_at=0;end=time.monotonic()+duration
        with (root/'events.jsonl').open('x')as events:
            def event(value):
                events.write(json.dumps({'at':time.time(),**value},sort_keys=True)+'\n');events.flush();os.fsync(events.fileno())
            while time.monotonic()<end:
                now=time.time()
                for host,row in records.items():
                    observed=process_state(row['record']);decision=restart_decision(observed['state'],row['attempts'],now,row['next_restart'],hourly_cap)
                    if decision=='restart':
                        remote=remote_listener(host,port)
                        event(dict(kind='remote_listener',host=host,observation=remote))
                        if remote['state']!='absent':
                            row['next_restart']=now+min(300,health_interval)
                            row['decision']='remote_'+remote['state'];row['observed']=observed
                            continue
                        # Recheck local identity after the authenticated remote probe.
                        observed=process_state(row['record'])
                        if observed['state']!='absent':
                            event(dict(kind='restart_cancelled',host=host,observation=observed))
                            row['observed']=observed;continue
                        sequence+=1;directory=root/'attempts'/('%04d-%s'%(sequence,host));directory.mkdir(parents=True)
                        event(dict(kind='transport_restart_intent',host=host,prior=row['record_binding'],directory=str(directory)))
                        row['attempts'].append(now);row['failures']+=1;row['next_restart']=now+min(300,15*2**min(row['failures']-1,5))
                        try:
                            value=start_one(host,directory,port);path=directory/(host+'.json')
                            row['record']=value;row['record_binding']=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                            event(dict(kind='transport_restart_spawned',host=host,record=row['record_binding']))
                        except Exception as exc:
                            # A partially spawned generation is ambiguous, so stop
                            # the supervisor rather than starting another process.
                            event(dict(kind='transport_restart_uncertain',host=host,error_type=type(exc).__name__))
                            raise
                    if row['observed']!=observed:event(dict(kind='transport_state',host=host,observation=observed,decision=decision))
                    row['observed']=observed;row['decision']=decision
                if now>=health_at:
                    with ThreadPoolExecutor(max_workers=8)as pool:health=list(pool.map(lambda host:authenticated_status(host,port),HOSTS))
                    for value in health:
                        row=records[value['host']];row['health']=value
                        if value['healthy']:row['failures']=0
                        event(dict(kind='authenticated_status',**value))
                    health_at=time.time()+health_interval
                durable(root/'state.json',dict(schema_version=SUPERVISOR_SCHEMA,at=time.time(),records=records),replace=True)
                time.sleep(interval)
            event(dict(kind='supervisor_duration_complete',tunnels_left_running=True))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['start','status','stop','check','supervise']);p.add_argument('--run',required=True)
    p.add_argument('--port',type=int,default=8765);p.add_argument('--host',choices=HOSTS,action='append')
    p.add_argument('--adopt');p.add_argument('--interval',type=int,default=15);p.add_argument('--health-interval',type=int,default=120)
    p.add_argument('--hourly-cap',type=int,default=4);p.add_argument('--duration',type=int,default=86400)
    a=p.parse_args();root=Path(a.run).resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    hosts=a.host or HOSTS
    if a.action=='supervise':
        if not a.adopt or a.host:raise ValueError('supervisor requires all eight adopted hosts')
        return supervise(root,a.adopt,a.port,interval=a.interval,health_interval=a.health_interval,hourly_cap=a.hourly_cap,duration=a.duration)
    if a.action=='check':
        with ThreadPoolExecutor(max_workers=8)as pool:results=list(pool.map(lambda host:authenticated_status(host,a.port),hosts))
        durable(root/'authenticated-status.json',dict(at=time.time(),results=results,workbook_mutations=0,model_calls=0))
        print(json.dumps(results,indent=2));return
    if a.action=='start':
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda h:start_one(h,root,a.port),hosts))
    else:
        results=[]
        for host in hosts:
            record=root/(host+'.json')
            if not record.exists():results.append(dict(host=host,running=False,record_missing=True));continue
            value=json.loads(record.read_text())
            results.append(stop_one(value) if a.action=='stop' else {**value,'running':live(value['pid'])})
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
