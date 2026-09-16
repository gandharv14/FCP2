"""Root-approved, two-slot VM supervisor; independent of immutable task snapshots.

start/resume require a hash-bound allowlist. stop drains active workers by
writing control state, never by deleting claims. --force asks each supervisor to
interrupt its exact recorded worker PID; parent-death guardians retain evidence.
A dispatch intent is never replayed, including after a missing/lost claim reply.
Idle slots and protocol checks are not actual pipeline qualification.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

SCHEMA='harbor-fleet-pool/v1'


def safe(path):
    p=Path(path)
    if not p.is_absolute() or '..' in p.parts or any(x.is_symlink() for x in (p,*p.parents)):
        raise ValueError('canonical absolute path without symlinks required')
    return p


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def binding(path):
    p=safe(path);return {'path':str(p),'sha256':sha(p)}


def read(row):
    if set(row)!={'path','sha256'} or binding(row['path'])!=row:
        raise ValueError('hash-bound supervisor input changed')
    return json.loads(Path(row['path']).read_text())


def persist(path,value,*,immutable=False):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.pool-',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        if immutable:os.link(name,p)
        else:os.replace(name,p)
        directory=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)
    return binding(p)


@contextmanager
def lock(path,*,blocking=True):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(p,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB));yield fd
    finally:os.close(fd)


def process_identity(pid):
    """PID plus kernel start identity; never use PID existence alone for signals."""
    try:
        stat=Path('/proc')/str(pid)/'stat'
        if stat.exists():
            text=stat.read_text();fields=text[text.rfind(')')+2:].split()
            if fields[0]=='Z':return None
            return {'pid':pid,'start':fields[19],'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        result=subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','stat='],capture_output=True,text=True,timeout=5)
        if result.returncode or not result.stdout.strip() or result.stdout.split()[-1].startswith('Z'):return None
        return {'pid':pid,'start':' '.join(result.stdout.split()[:-1])}
    except (OSError,ValueError,subprocess.TimeoutExpired):return None


def signal_exact(identity,kind):
    if identity is None or process_identity(identity['pid'])!=identity:
        raise ValueError('PID start identity changed; do not signal')
    os.kill(identity['pid'],kind)


def resources(root):
    result={'at':time.time(),'cpus':os.cpu_count(),'load_average':list(os.getloadavg()),
            'disk_free_bytes':shutil.disk_usage(root).free}
    proc=Path('/proc/meminfo')
    if proc.exists():
        values={line.split(':')[0]:int(line.split()[1])*1024 for line in proc.read_text().splitlines() if line.startswith(('MemAvailable:','MemTotal:'))}
        result.update(memory_available_bytes=values['MemAvailable'],memory_total_bytes=values['MemTotal'])
        result['cpu_counters_ticks']=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
    else:
        result['memory_available_bytes']=None
    return result


def config(row):
    value=read(row)
    expected={'schema_version','host','code_root','python','worker','url','token_file','jobs'}
    if set(value)!=expected or value['schema_version']!=SCHEMA:
        raise ValueError('closed root-selected pool configuration required')
    if value['host'] not in ['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)]:
        raise ValueError('host outside authorized fleet')
    for key in ['code_root','token_file']:safe(value[key])
    if not Path(value['python']).is_absolute() or not Path(value['python']).is_file():
        raise ValueError('absolute pinned runtime executable required')
    if binding(value['worker']['path'])!=value['worker']:
        raise ValueError('pinned worker changed')
    if not Path(value['worker']['path']).is_relative_to(value['code_root']):
        raise ValueError('worker must belong to selected immutable code')
    if len(value['jobs'])>10000:raise ValueError('bounded explicit allowlist required')
    jobs=[];seen=set()
    for selected in value['jobs']:
        job=read(selected)
        if job['workbook'] in seen:raise ValueError('duplicate workbook in allowlist')
        seen.add(job['workbook'])
        if job['cwd']!=value['code_root']:raise ValueError('job code differs from pinned runtime')
        if job['memory_limit_bytes']!=8*1024**3 or job['minimum_disk_bytes']<12*1024**3:
            raise ValueError('original job resource guards changed')
        jobs.append((selected,job))
    return value,jobs


def reserve(root,jobs,status,slot,host):
    """Only VM-local launch selection; worker still obtains global fenced claim."""
    if any(item['state']=='hold' for item in status.get('operations',[])):
        return None
    rows={item['workbook']:item for item in status['workbooks']}
    with lock(root/'selection.lock'):
        if (root/'stop.json').exists():return None
        for selected,job in jobs:
            existing=rows.get(job['workbook'])
            if not existing or existing['state']!='ready':continue
            if existing['source_sha256']!=job['source']['sha256']:continue
            marker=root/'dispatches'/(job['workbook']+'.json')
            if marker.exists() or Path(job['run']).exists():continue
            observation=resources(job['cwd'])
            # Two original 8GiB guards remain per-task; leave at least 4GiB free
            # headroom before dispatch. Unavailable measurements fail closed.
            if observation['memory_available_bytes'] is None or observation['memory_available_bytes']<12*1024**3:
                return None
            if observation['disk_free_bytes']<job['minimum_disk_bytes']:return None
            if observation['load_average'][0]>max(1,observation['cpus'])*1.5:return None
            persist(marker,{'job':selected,'slot':slot,'host':host,'at':time.time(),
                            'automatic_retry':False,'resources_before':observation},immutable=True)
            return selected,job,marker
    return None


def start(row,root,*,resume=False):
    cfg,_=config(row);root=safe(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with lock(root/'control.lock'):
        old_path=root/'current.json'
        if old_path.exists():
            old=json.loads(old_path.read_text())
            if old['host']!=cfg['host']:raise ValueError('pool host identity changed')
            for item in old['slots']:
                if item.get('identity') and process_identity(item['identity']['pid'])==item['identity']:
                    raise ValueError('existing pool still running; drain and inspect before resume')
            if not resume:raise ValueError('pool has retained generation; explicit resume required')
            # Stale workers cannot be resumed merely because their supervisors
            # exited. Require every recorded active worker to be stopped too.
            for state_file in (root/'generations'/old['generation']).glob('slot-*.state.json'):
                state=json.loads(state_file.read_text());identity=state.get('worker_identity')
                if identity and process_identity(identity['pid'])==identity:
                    raise ValueError('prior worker still active; reconcile before resume')
        elif resume:raise ValueError('no previous pool to resume')
        if (root/'stop.json').exists():
            # Preserve old stop directives in immutable generation evidence.
            stop=json.loads((root/'stop.json').read_text())
            persist(root/'control-history'/('stop-'+str(time.time_ns())+'.json'),stop,immutable=True)
            (root/'stop.json').unlink()
        generation=str(uuid.uuid4());directory=root/'generations'/generation;directory.mkdir(parents=True)
        own=binding(Path(__file__).resolve())
        record={'schema_version':SCHEMA,'generation':generation,'config':row,'supervisor':own,
                'host':cfg['host'],'started_at':time.time(),'slots':[],'resumed':resume}
        persist(directory/'generation.json',record,immutable=True)
        # Each slot retains its own logfile/PID and obtains its own lifetime lock.
        for index in [0,1]:
            command=[sys.executable,'-I','-B',own['path'],'slot','--config',row['path'],
                     '--config-sha256',row['sha256'],'--run',str(root),'--generation',generation,'--slot',str(index)]
            with (directory/f'slot-{index}.log').open('xb') as log:
                process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            identity=process_identity(process.pid)
            if identity is None:raise ValueError('supervisor exited during startup; inspect retained log')
            record['slots'].append({'slot':index,'identity':identity,'command':command})
            persist(root/'current.json',record)
        persist(directory/'launched.json',record,immutable=True)
        return record


def status(root):
    root=safe(root);record=json.loads((root/'current.json').read_text());directory=root/'generations'/record['generation']
    slots=[]
    for item in record['slots']:
        file=directory/f"slot-{item['slot']}.state.json"
        state=json.loads(file.read_text()) if file.exists() else {'state':'STARTING'}
        identity=item['identity'];state.update(slot=item['slot'],supervisor_alive=process_identity(identity['pid'])==identity,
                                             heartbeat_age_seconds=time.time()-state['updated_at'] if 'updated_at' in state else None)
        slots.append(state)
    return {'schema_version':SCHEMA,'host':record['host'],'generation':record['generation'],
            'config':record['config'],'supervisor':record['supervisor'],'stop_requested':(root/'stop.json').exists(),
            'slots':slots,'dispatch_count':len(list((root/'dispatches').glob('*.json'))),
            'pipeline_validation_claimed':False,'at':time.time()}


def stop(root,*,force=False):
    root=safe(root)
    with lock(root/'selection.lock'):
        persist(root/'stop.json',{'requested_at':time.time(),'force':force,'behavior':'interrupt and reconcile' if force else 'drain active; no new dispatch'})
    return status(root)


def run_slot(row,root,generation,index):
    cfg,jobs=config(row);root=safe(root);directory=root/'generations'/generation
    if not directory.is_dir():raise ValueError('root-selected generation missing')
    selected=json.loads((directory/'generation.json').read_text())
    if selected['config']!=row or selected['supervisor']!=binding(Path(__file__).resolve()):
        raise ValueError('slot config or supervisor differs from selected generation')
    sys.path.insert(0,cfg['code_root'])
    from scripts.fleet.coordinator import Client
    from scripts.batch.hardened_runner import _guarded_process
    client=Client(cfg['url'],cfg['token_file'])
    state_file=directory/f'slot-{index}.state.json'
    identity=process_identity(os.getpid());last_sample=0;sample_index=0
    state={'schema_version':SCHEMA,'host':cfg['host'],'slot':index,'generation':generation,
           'supervisor_identity':identity,'state':'STARTING','worker_identity':None,
           'completed_dispatches':0,'pipeline_validation_claimed':False}
    def update(**changes):
        nonlocal last_sample,sample_index
        state.update(changes,updated_at=time.time())
        if time.monotonic()-last_sample>=10:
            state['resources']=resources(cfg['code_root']);last_sample=time.monotonic()
            persist(directory/f'slot-{index}.samples'/f'{sample_index:08d}.json',
                    {'state':state['state'],'workbook':state.get('workbook'),'resources':state['resources']},immutable=True)
            sample_index+=1
        persist(state_file,state)
    # SIGTERM means graceful drain; active worker continues under its guardian.
    def drain(signum,frame):
        persist(root/'stop.json',{'requested_at':time.time(),'force':False,'signal':signum})
    signal.signal(signal.SIGTERM,drain)
    with lock(root/f'slot-{index}.lock',blocking=False) as slot_lock_fd:
        while True:
            if (root/'stop.json').exists():update(state='STOPPED',worker_identity=None);return
            try:central=client.call('status')
            except Exception as exc:
                update(state='COORDINATOR_UNAVAILABLE',failure_type=type(exc).__name__);time.sleep(5);continue
            if any(item['state']=='hold' for item in central.get('operations',[])):
                update(state='GLOBAL_OPERATION_RECONCILIATION_REQUIRED',worker_identity=None);time.sleep(5);continue
            if binding(cfg['worker']['path'])!=cfg['worker']:
                update(state='WORKER_CODE_CHANGED');return
            chosen=reserve(root,jobs,central,index,cfg['host'])
            if chosen is None: update(state='IDLE',workbook=None,worker_identity=None);time.sleep(5);continue
            selected,job,marker=chosen
            worker_owner=f"{cfg['host']}/slot-{index}/{generation}"
            command=[cfg['python'],'-I','-B',cfg['worker']['path'],'--job',selected['path'],'--job-sha256',selected['sha256'],
                     '--url',cfg['url'],'--token-file',cfg['token_file'],'--owner',worker_owner]
            log_path=directory/f"slot-{index}.workbook-{job['workbook']}.log"
            update(state='STARTING_WORKER',workbook=job['workbook'],dispatch=binding(marker))
            failure=None;rc=None;forced=False;force_at=None
            try:
                with log_path.open('xb') as log:
                    with _guarded_process(command,stdout=log,cwd=Path(cfg['code_root']),env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},grace_seconds=5,guardian_lock_fds=(slot_lock_fd,)) as process:
                        worker_identity=process_identity(process.pid)
                        if worker_identity is None:raise ValueError('worker start identity unavailable')
                        update(state='AUTHORING',worker_identity=worker_identity)
                        while process.poll() is None:
                            control=json.loads((root/'stop.json').read_text()) if (root/'stop.json').exists() else None
                            if control and control.get('force'):
                                if not forced:
                                    signal_exact(worker_identity,signal.SIGINT);forced=True;force_at=time.monotonic()
                                    update(state='INTERRUPTING_RECONCILIATION_REQUIRED')
                                elif time.monotonic()-force_at>20:
                                    signal_exact(worker_identity,signal.SIGKILL)
                            elif control:update(state='DRAINING')
                            else:update(state='AUTHORING')
                            time.sleep(1)
                        rc=process.wait()
            except Exception as exc:failure=type(exc).__name__
            outcome_path=Path(job['run'])/'outcome.json'
            outcome=binding(outcome_path) if outcome_path.is_file() else None
            completion={'job':selected,'dispatch':binding(marker),'returncode':rc,'failure_type':failure,
                        'forced':forced,'outcome':outcome,'log':binding(log_path) if log_path.exists() else None,
                        'finished_at':time.time(),'automatic_retry':False,'publication_performed':False}
            persist(directory/f"slot-{index}.workbook-{job['workbook']}.result.json",completion,immutable=True)
            update(state='RECONCILIATION_REQUIRED' if rc!=0 or failure or forced else 'AUTHORING_RETURNED',
                   worker_identity=None,completed_dispatches=state['completed_dispatches']+1,last_result=completion)
            # This task is never selected again; only untouched, centrally ready
            # jobs from the root allowlist can enter the next dispatch.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','resume','status','stop','slot'])
    parser.add_argument('--run',required=True);parser.add_argument('--config');parser.add_argument('--config-sha256')
    parser.add_argument('--force',action='store_true');parser.add_argument('--generation');parser.add_argument('--slot',type=int,choices=[0,1])
    a=parser.parse_args();root=Path(a.run).absolute()
    if a.action in {'start','resume','slot'}:
        if not a.config or not a.config_sha256:parser.error('hash-bound --config and --config-sha256 required')
        row={'path':str(Path(a.config).absolute()),'sha256':a.config_sha256}
        if a.action=='slot':
            if a.generation is None or a.slot is None:parser.error('internal slot generation required')
            run_slot(row,root,a.generation,a.slot);return
        value=start(row,root,resume=a.action=='resume')
    elif a.action=='stop':value=stop(root,force=a.force)
    else:value=status(root)
    print(json.dumps(value,indent=2))

if __name__=='__main__':main()
