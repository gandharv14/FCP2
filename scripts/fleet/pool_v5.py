"""Quarantine-aware, root-approved VM supervisor; independent of immutable task snapshots.

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

SCHEMA='harbor-fleet-pool/v5'


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
    expected={'schema_version','host','code_root','python','worker','url','token_file','jobs','capacity','admission_policy'}
    if set(value)-{'backlog'}!=expected or value['schema_version']!=SCHEMA:
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
    capacity_policy(value['capacity']);admission_module(value['admission_policy'])
    if 'backlog'in value:
        module=backlog_module(value['backlog']);p=module.policy(value['backlog'])
        if any(p[k]!=value[k]for k in ('host','code_root','worker')):raise ValueError('backlog belongs to another supervisor')
    return value,validated_jobs(value,value['jobs'])


def validated_jobs(value,selected_jobs):
    jobs=[];seen=set()
    for selected in selected_jobs:
        job=read(selected)
        if job['workbook'] in seen:raise ValueError('duplicate workbook in allowlist')
        seen.add(job['workbook'])
        if job['cwd']!=value['code_root']:raise ValueError('job code differs from pinned runtime')
        sys.path.insert(0,value['code_root'])
        from scripts.fleet.worker import validate
        validate(job)
        jobs.append((selected,job))
    return jobs


def backlog_module(row):
    import importlib.util
    p=read(row);helper=p['helper']
    if binding(helper['path'])!=helper:raise ValueError('backlog reader changed')
    spec=importlib.util.spec_from_file_location('_selected_pool_backlog_'+helper['sha256'],helper['path'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if module.SCHEMA!='fleet-durable-backlog/v1':raise ValueError('unsupported backlog reader')
    return module


def refresh_jobs(cfg,base,previous=None):
    if 'backlog'not in cfg:return base,None
    module=backlog_module(cfg['backlog']);rows,cursor=module.observe(cfg['backlog'],previous=previous)
    return validated_jobs(cfg,[row for row,_ in base]+rows),cursor


def admission_module(row):
    """Explicit immutable policy dependency; no coordinator state projection."""
    import importlib.util
    if set(row)!={'path','sha256'} or binding(row['path'])!=row:
        raise ValueError('selected pool admission policy changed')
    spec=importlib.util.spec_from_file_location('_pool_admission_'+row['sha256'],row['path'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if module.SCHEMA!='fleet-concurrency-policy/v1' or not callable(module.model_admission_barriers):
        raise ValueError('unsupported pool admission policy')
    return module


def reserve(root,jobs,status,slot,host,policy,admission_policy):
    """Only VM-local launch selection; worker still obtains global fenced claim."""
    admission=admission_module(admission_policy);admission.validate_occupancy(status)
    rows={item['workbook']:item for item in status['workbooks']}
    with lock(root/'selection.lock'):
        if (root/'stop.json').exists():return None
        # Includes existing one-off workers. Pending local dispatches occupy a
        # slot before their central claim exists, closing the two-slot race.
        active={item['workbook'] for item in author_claims(status) if owner_host(item['owner'])==host}
        census=native_census(host)
        active.update(census)
        for selected,job in jobs:
            if (root/'dispatches'/(job['workbook']+'.json')).exists() and not (Path(job['run'])/'outcome.json').exists():
                active.add(job['workbook'])
        # The coordinator counts source claims and held reservations against
        # the author limit too. Reserve local pending starts in the same count;
        # a physical model exception does not expand this author allowance.
        if not coordinator_room(status,host,active):return None
        if len(active)>=policy['max_total_workers']:return None
        phases={wb:census[wb]['phase'] if wb in census else 'author' for wb in active}
        for _,j in jobs:
            if j['workbook'] in active and j['workbook'] not in census:phases[j['workbook']]=job_phase(j)
        # A staged pipeline reserves author capacity throughout, and also
        # conservatively reserves source capacity for its whole job as well.
        actual_sources={wb for wb in active if phases[wb]=='source' or
            census.get(wb,{}).get('capacity_reservation')=='author'}
        for _,j in jobs:
            if j['workbook'] in active and j['workbook'] not in census and Path(j['driver']['path']).name=='staged_pipeline.py':actual_sources.add(j['workbook'])
        for selected,job in jobs:
            existing=rows.get(job['workbook'])
            if not existing or existing['state']!='ready':continue
            if existing['source_sha256']!=job['source']['sha256']:continue
            if existing.get('version')!=job['version'] and not (existing.get('version') is None and existing.get('fence',0)==0):continue
            if admission.model_admission_barriers(status,job['workbook'],host+'/slot-'+str(slot)+'/pending'):continue
            if any(o['state']in('active','hold')and o.get('workbook')==job['workbook']for o in status['operations']):continue
            marker=root/'dispatches'/(job['workbook']+'.json')
            if marker.exists() or Path(job['run']).exists():continue
            phase=job_phase(job)
            if sum(p==phase for p in phases.values())>=policy['max_'+phase+'_workers']:continue
            if (phase=='source' or Path(job['driver']['path']).name=='staged_pipeline.py') and len(actual_sources)>=policy['max_source_workers']:continue
            observation=resources(job['cwd'])
            observation['worker_tree_rss_bytes']=sum(r['rss_bytes'] for r in census.values())
            if observation['worker_tree_rss_bytes']+job['memory_limit_bytes']+policy['headroom_bytes']>observation['memory_total_bytes']:return None
            # Two original 8GiB guards remain per-task; leave at least 4GiB free
            # headroom before dispatch. Unavailable measurements fail closed.
            if observation['memory_available_bytes'] is None or observation['memory_available_bytes']<job['memory_limit_bytes']+policy['headroom_bytes']:
                return None
            if observation['disk_free_bytes']<job['minimum_disk_bytes']:return None
            if observation['load_average'][0]>max(1,observation['cpus'])*1.5:return None
            persist(marker,{'job':selected,'slot':slot,'host':host,'at':time.time(),
                            'automatic_retry':False,'resources_before':observation},immutable=True)
            return selected,job,marker
    return None


def coordinator_room(status,host,pending):
    """Admission hint only; the coordinator claim remains the atomic guard."""
    policy=status.get('author_capacity_policy')
    if policy is None:return True
    limit=policy.get('authors_per_host')
    if type(limit)is not int or limit<1:raise ValueError('invalid coordinator author limit')
    rows=status.get('author_reservations')
    if not isinstance(rows,list):raise ValueError('coordinator reservation inventory missing')
    occupied=set(pending)
    for row in rows:
        if row.get('state')not in ('active','hold','released'):raise ValueError('unknown coordinator reservation state')
        if row['host']==host and row['state']in ('active','hold'):occupied.add(row['workbook'])
    # Stay within the base limit until a separately selected pool policy adopts
    # a measured canary. Historical holds are never silently released here.
    return len(occupied)<limit


def owner_host(owner):
    """Both established one-off and pool owner spellings, exact host prefix."""
    for separator in (':','/'):
        prefix=owner.split(separator,1)[0]
        if prefix in ['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)]:return prefix
    raise ValueError('active owner has no registered fleet host')


def author_claims(status):
    result=[]
    for claim in status['workbooks']:
        if claim['state']!='active':continue
        owner=claim['owner']
        parts=owner.split(':')
        native_publication=len(parts)==4 and parts[1]=='publication'
        if owner.startswith(('local:collection:', 'local:publication:')) or native_publication:
            if native_publication and (owner_host(owner)!=parts[0] or parts[2]!=claim['workbook'] or not parts[3]):
                raise ValueError('native publication owner differs from its workbook')
            # The coordinator's operation table binds its current workbook
            # claim by owner/fence; source and version live on that claim, not
            # on the operation row. Do not require invented operation fields.
            matches=[op for op in status['operations'] if op['kind']=='publication' and op['state']=='active'
                and all(op.get(k)==claim.get(k) for k in ('workbook','owner','fence','expires'))
                and all(k not in op or op[k]==claim.get(k) for k in ('source_sha256','version'))]
            if len(matches)!=1:raise ValueError('publication custody is not authenticated')
            continue
        owner_host(owner)
        result.append(claim)
    return result


def native_workers(host, proc_root=Path('/proc')):
    """Retain capacity for live workers even while their claim is entering hold."""
    if not proc_root.is_dir():raise ValueError('native process census unavailable')
    workbooks=set()
    for directory in proc_root.iterdir():
        if not directory.name.isdigit():continue
        try:
            stat=(directory/'stat').read_text()
            if stat[stat.rfind(')')+2:].split()[0]=='Z':continue
            argv=(directory/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        except FileNotFoundError:continue
        if not any(arg.endswith('/worker.py') for arg in argv):continue
        if any(argv.count(flag)!=1 for flag in ('--job','--job-sha256','--owner')):
            raise ValueError('unrecognized live worker invocation')
        selected={'path':argv[argv.index('--job')+1], 'sha256':argv[argv.index('--job-sha256')+1]}
        job=read(selected)
        if owner_host(argv[argv.index('--owner')+1])!=host:raise ValueError('live worker host differs')
        workbooks.add(job['workbook'])
    return workbooks


def capacity_policy(row):
    p=read(row)
    expected={'schema_version','authorization','observation','physical_model_policy','max_total_workers','max_source_workers','max_author_workers','headroom_bytes','expires_at'}
    if set(p)!=expected or p['schema_version']!='fleet-pool-capacity/v1':raise ValueError('closed capacity policy required')
    for key in ('authorization','observation','physical_model_policy'):read(p[key]) if key!='authorization' else safe(p[key]['path'])
    physical=read(p['physical_model_policy'])
    if physical['slots_per_host']!=2 or physical['per_workbook_limit']!=1 or physical['publication_limit']!=1:raise ValueError('physical model limits differ')
    if binding(p['authorization']['path'])!=p['authorization']:raise ValueError('authorization changed')
    if (p['max_total_workers'],p['max_source_workers'],p['max_author_workers'],p['headroom_bytes'])!=(4,2,2,4*1024**3):raise ValueError('capacity outside selected limits')
    if not isinstance(p['expires_at'],(int,float)) or isinstance(p['expires_at'],bool) or p['expires_at']<=time.time():raise ValueError('capacity policy expired')
    return p


def job_phase(job):
    # Source-first switches to author capacity as soon as its original author
    # preparation starts. Other entries conservatively consume author capacity.
    if Path(job['driver']['path']).name=='fresh.py':
        p=read(job['inputs'][0]);mode=p.get('reconstruction',{}).get('mode')
        if mode=='source-first' and not (Path(p['home'])/'authoring').exists():return 'source'
    if Path(job['driver']['path']).name in ('source_first.py','resource_continue.py','reconstruction_continue.py'):return 'source'
    return 'author'



def observed_job_phase(job):
    """Admission reserves an author slot throughout a combined pipeline job."""
    if Path(job['driver']['path']).name!='staged_pipeline.py':return {}
    from scripts.fleet.staged_pipeline import actual_phase
    selected=job['inputs'][0];plan=read(selected)
    if plan['workbook']!=job['workbook'] or read(plan['author_template'])['code_root']!=job['cwd']:
        raise ValueError('pipeline observation detached from worker')
    return {'capacity_reservation':'author','actual_phase':actual_phase(selected)}


def native_census(host,proc_root=Path('/proc')):
    if not proc_root.is_dir():raise ValueError('native process census unavailable')
    records={};workers=[]
    for d in proc_root.iterdir():
        if not d.name.isdigit():continue
        try:
            raw=(d/'stat').read_text();v=raw[raw.rfind(')')+2:].split()
            if v[0]=='Z':continue
            argv=(d/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        except FileNotFoundError:continue
        records[int(d.name)]={'ppid':int(v[1]),'rss_bytes':int(v[21])*os.sysconf('SC_PAGE_SIZE')}
        if not any(arg.endswith('/worker.py') for arg in argv):continue
        if any(argv.count(f)!=1 for f in ('--job','--job-sha256','--owner')):raise ValueError('unrecognized live worker')
        selected={'path':argv[argv.index('--job')+1],'sha256':argv[argv.index('--job-sha256')+1]};job=read(selected)
        if owner_host(argv[argv.index('--owner')+1])!=host:raise ValueError('live worker host differs')
        workers.append((int(d.name),selected,job))
    result={}
    for pid,selected,job in workers:
        if job['workbook'] in result:raise ValueError('duplicate actual workbook workers')
        tree={pid}
        while True:
            new=tree|{p for p,v in records.items() if v['ppid'] in tree}
            if new==tree:break
            tree=new
        result[job['workbook']]={'pid':pid,'job':selected,'phase':job_phase(job),'rss_bytes':sum(records[p]['rss_bytes'] for p in tree if p in records),**observed_job_phase(job)}
    return result


def start(row,root,*,resume=False):
    cfg,jobs=config(row);root=safe(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
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
        for index in range(2 if 'backlog'in cfg else min(2,len(jobs))):
            command=[sys.executable,'-I','-B',own['path'],'slot','--config',row['path'],
                     '--config-sha256',row['sha256'],'--run',str(root),'--generation',generation,'--slot',str(index)]
            with (directory/f'slot-{index}.log').open('xb') as log:
                process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            identity=process_identity(process.pid)
            if identity is None:raise ValueError('supervisor exited during startup; inspect retained log')
            record['slots'].append({'slot':index,'identity':identity,'command':command})
            persist(root/'current.json',record)
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
    cfg,jobs=config(row)
    if type(index) is not int or index not in range(2 if 'backlog'in cfg else min(2,len(jobs))):
        raise ValueError('slot is not assigned to this root allowlist')
    root=safe(root);directory=root/'generations'/generation
    if not directory.is_dir():raise ValueError('root-selected generation missing')
    selected=json.loads((directory/'generation.json').read_text())
    if selected['config']!=row or selected['supervisor']!=binding(Path(__file__).resolve()):
        raise ValueError('slot config or supervisor differs from selected generation')
    sys.path.insert(0,cfg['code_root'])
    from scripts.fleet.coordinator import Client
    from scripts.batch.hardened_runner import _guarded_process
    client=Client(cfg['url'],cfg['token_file'])
    state_file=directory/f'slot-{index}.state.json'
    identity=process_identity(os.getpid());last_sample=0;sample_index=0;backlog_cursor=None
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
            if binding(cfg['worker']['path'])!=cfg['worker']:
                update(state='WORKER_CODE_CHANGED');return
            try:
                selected_jobs,backlog_cursor=refresh_jobs(cfg,jobs,backlog_cursor)
                state.update(backlog_cursor=backlog_cursor,selected_jobs=len(selected_jobs))
                chosen=reserve(root,selected_jobs,central,index,cfg['host'],capacity_policy(cfg['capacity']),cfg['admission_policy'])
            except Exception as exc:
                update(state='ADMISSION_PAUSED',failure_type=type(exc).__name__,worker_identity=None)
                time.sleep(5);continue
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
                        update(state='WORKER_RUNNING',worker_identity=worker_identity)
                        while process.poll() is None:
                            control=json.loads((root/'stop.json').read_text()) if (root/'stop.json').exists() else None
                            if control and control.get('force'):
                                if not forced:
                                    signal_exact(worker_identity,signal.SIGINT);forced=True;force_at=time.monotonic()
                                    update(state='INTERRUPTING_RECONCILIATION_REQUIRED')
                                elif time.monotonic()-force_at>20:
                                    signal_exact(worker_identity,signal.SIGKILL)
                            elif control:update(state='DRAINING')
                            else:update(state='WORKER_RUNNING',**observed_job_phase(job))
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
