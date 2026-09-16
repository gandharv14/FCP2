"""One-workbook bootstrap smoke custody; no prior smoke is fabricated.

Only 0029's exact reviewed zero-model preparation may start. Its subsequent
review, native runtime, publication and independent collection remain required.
"""
from __future__ import annotations
import argparse,ast,fcntl,json,os,shlex,subprocess,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')
SCHEMA='harbor-fleet-bootstrap-smoke-0029/v1'
PROTECTED_0270={'workbook': '0270', 'state': 'hold', 'fence': 5, 'owner': 'hhu-server:slot-1:0270-oracle-001', 'version': '73b5681a505d6d3ec0865109e1bcfa6b63ac48f9c0f68a47f94ed28df31f3071', 'source_sha256': '9812c0ac11a2462356e3020d8aeaa21f998c8f3a8a9f83a01569ee1cd23b21fb', 'reason': '{"path": "/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/workers/hhu-server/slot-1/0270-oracle-001/outcome.json", "sha256": "e2ec6df7f82a74ae7c03e5883ff169ffdf980a16a002720345e0093ce25afd3c"}'}

PROFILES={'0029': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/driver.py', 'sha256': '52e35506ca06909f04c51b3b45ec13ce8f246d1b9ba66c037b15984dcf8b0cdb'}, 'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/gated-successor-004/source-proof.json', 'sha256': '5c8448deb7e3328cfd0a5eba646965d31c30e9aa1271a2d5b33e358db005d176'}, {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/local-probe/result.json', 'sha256': 'bcb13e8f74580345aa2d15530b3627ccaef9d29bc56de86e057d8af5a5bca623'}], 'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/plan.json', 'sha256': '835ce8237f0c8b1b19d487846b626924911ad004dd3f586799d7597c267e8843'}, 'host': 'hhu-server-3', 'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/transfer-003/probe.json', 'sha256': '9b92788ab23f22bd222c58127e49bf4126cad4810fdfdcc3bdeb55e118184f7f'}, 'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/worker-job-002.json', 'sha256': 'acc2541686f43134e91470c5dd11b48ff15c95b45ff5652862e3eabe7c52c098'}, 'kind': 'dialogue-pre-review-0029', 'stage_memory': 8589934592, 'stage_seconds': 3300, 'version': '2d3e15a8e7b500635e6960eac14872beb98adcae75de8f1f53f174eeb42ec98c'}}

def require(ok,message):
    if not ok:raise ValueError(message)

def native_bindings(p,job):
    a=checked(p['authority'])
    require(set(a)=={'schema_version','kind','workbook','job','source','tracker','checkpoint','descriptor','inspection','history'},'closed historical authority required')
    require(a['schema_version']=='harbor-fleet-retained-native-authority/v1' and a['job']==p['job']
        and a['workbook']==job['workbook'] and a['source']==job['source'],'native job/source identity differs')
    tracker=checked(a['tracker']);checkpoint=checked(a['checkpoint']);checked(a['descriptor'])
    require(tracker['workbook_id']==job['workbook'] and tracker['bindings']['original_raw_source']==a['source']
        and checkpoint['descriptor']==a['descriptor'],'native tracker/checkpoint/source differs')
    profile=PROFILES.get(job['workbook'])
    require(profile is not None and profile['kind']==a['kind'],'unreviewed historical preparation')
    require(p['host']==profile['host'] and p['job']==profile['job'] and checked(p['job'])==job and a['inspection']==profile['inspection']
        and a['history']==profile['history'],'exact reviewed host/job/probe/recipe required')
    require(job['driver']==profile['driver'] and job['version']==profile['version'],'reviewed driver/code differs')
    for row in profile['evidence']:checked(row)
    require(binding(job['driver']['path'])==job['driver'],'reviewed helper changed')
    return a,tracker,checkpoint,checked(a['inspection']),checked(a['history']),profile

def decoded_budget(value):
    if value['type']=='scalar':return value['value']
    if value['type']=='tuple':return [decoded_budget(x) for x in value['value']]
    if value['type']=='dict':return {decoded_budget(k):decoded_budget(v) for k,v in value['value']}
    if value['type']=='Budget':return {k:decoded_budget(v) for k,v in value['value'].items()}
    raise ValueError('unsupported retained budget encoding')

def dialogue_authority(p,job):
    a,tracker,checkpoint,probe,h,profile=native_bindings(p,job)
    require(job['workbook']==h['workbook']=='0029' and h['schema_version']=='harbor-fleet-0029-pre-review-continuation/v1','exact pre-review recipe required')
    require(all(h[k]==a[k] for k in ('tracker','checkpoint','descriptor')) and h['stopped_proof']==p['handoff']
        and h['linux_probe']==a['inspection'],'retained instruction predecessor differs')
    require(h['source']['sha256']==job['source']['sha256'] and binding(h['source']['path'])==h['source'],'byte-identical retained source required')
    proof=checked(probe['source_proof']);require(proof['bindings']['source']==h['source'],'full source proof detached')
    require(probe['status']=='SOURCE_PREFIX_MECHANICS_VERIFIED' and probe['model_calls']==probe['models_dispatched']==0
        and probe['publication_ready'] is False and probe['task_evidence_mutated'] is False
        and probe['snapshot']==h['snapshot'] and probe['tracker']==a['tracker'] and probe['blocked']==h['blocked']
        and probe['claims']==43 and probe['source_formula_instances']==153 and probe['passed_prefix_checks']==10
        and probe['custody']['semantic_budget_reset'] is False and len(probe['legacy_calls'])==8,'complete source/prefix non-model proof required')
    for row in probe['legacy_calls']:
        require(row['status']=='completed','uncertain legacy model response')
        checked(row['outcome']);checked(row['plan'])
    for k in ('blocked','guard','original_config'):checked(h[k])
    snapshot=checked(h['snapshot']);require(Path(snapshot['root']).name==job['version'],'owning code version differs')
    for row in snapshot['files']:
        name=Path(row['relative']);require(not name.is_absolute() and '..' not in name.parts,'snapshot path escape')
        require(binding(Path(snapshot['root'])/name)['sha256']==row['sha256'],'immutable owning snapshot changed')
    require(job['command']==[h['native_python'],'-I','-B',job['driver']['path'],'run','--plan',a['history']['path'],
        '--sha256',a['history']['sha256'],'--output',str(Path(job['result']).parent)]
        and job['timeout_seconds']==3300 and h['owner']==p['new_owner'],'exact pre-review command/owner/guard required')
    return a

def authority(p,job):
    return dialogue_authority(p,job)

def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','host','job','authority','handoff','coordinator_receipt','coordinator_process',
        'worker','new_owner','remote_receipt','expected_reason','slot','known_jobs','pool_roots','protected_0270'},'closed first-custody plan required')
    require(p['schema_version']==SCHEMA and p['host']=='hhu-server-3' and p['slot']==2 and type(p['slot']) is int,'authorized fleet host required')
    job=checked(p['job']);a=authority(p,job)
    require(job['command'][0]==str(Path(job['cwd']).parent/'venv/bin/python')
        and p['worker']['path']==str(Path(job['cwd'])/'scripts/fleet/worker.py'),'isolated interpreter/runtime worker required')
    maximum=86400 if a['kind'] in {'normalization-hold','dialogue-pre-review-0029'} else 3300
    require(job['memory_limit_bytes']==8*1024**3 and job['minimum_disk_bytes']>=12*1024**3
        and 0<job['timeout_seconds']<=maximum,'original resource guards required')
    require(p['new_owner'].startswith(p['host']+':') and len(p['new_owner'])<256,'explicit host owner required')
    path=Path(p['remote_receipt']);require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(REMOTE),'isolated launch receipt required')
    h=checked(p['handoff']);pause=checked(h['pause']);require(h['schema_version']=='local-to-vm-custody-handoff/v1'
        and h['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION' and pause['new_dispatch_allowed'] is False,'stopped local handoff required')
    item=next(x for x in h['workbooks'] if x['workbook_id']==job['workbook'])
    require({k:item['original_source'][k] for k in ('path','sha256')}==job['source'] and item['status']!='published','handoff source not eligible for continuation')
    for r in job['inputs']:checked(r)
    require(p['new_owner']=='hhu-server-3:slot-2:0029-pre-review-001','exact bootstrap owner required')
    require(p['known_jobs']==[] and p['pool_roots']==[], 'bootstrap requires an empty host, no pool allowances')
    require(set(p['protected_0270'])=={'workbook','state','fence','owner','version','source_sha256','reason'} and p['protected_0270']['workbook']=='0270' and p['protected_0270']['state']=='hold' and p['protected_0270']['fence']==5, 'exact retained 0270 hold required')
    require(p['protected_0270']==PROTECTED_0270,'exact reviewed protected hold required')
    checked(json.loads(p['protected_0270']['reason']))
    return p,job

def central_gate(status,p,job):
    require(status['model_limit']==status['publication_limit']==1,'global limits must remain one')
    require(not any(o['state'] in {'active','hold'} for o in status['operations']),'global operation requires reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']]
    require(len(rows)==1,'exact central workbook required');w=rows[0]
    require(w['state']=='hold' and w['fence']==0 and w['owner'] is None and w['version'] is None
        and w['source_sha256']==job['source']['sha256'] and w['reason']==p['expected_reason'],'first custody changed; do not retry')
    require(w['claim_id'] is None and not any(o['workbook']=='0029' for o in status['operations']), 'previous bootstrap custody or operation requires reconciliation')
    protected_gate(status,p)
    return w

def protected_gate(status,p):
    require(status['model_limit']==status['publication_limit']==1,'global limits must remain one')
    require(not any(o['state'] in {'active','hold'} for o in status['operations']),'global operation requires reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']=='0270']
    require(len(rows)==1 and {k:rows[0][k] for k in p['protected_0270']}==p['protected_0270'],'protected 0270 hold changed')

def local_custody(p):
    receipt=checked(p['coordinator_receipt']);process=checked(p['coordinator_process'])
    require(process['command']==receipt['command'],'coordinator process command differs')
    require(binding(receipt['command'][2])['sha256']==receipt['code_sha256'],'frozen coordinator changed')
    native=checked(p['authority']);original_home=str(Path(native['tracker']['path']).parents[2])
    processes=subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout.splitlines()
    require(not any(line.strip().split(None,1)[0]!=str(os.getpid()) and original_home in line for line in processes if line.strip()),'local historical task process still active')
    pid=process['pid'];require(type(pid) is int and pid>0,'live coordinator PID required')
    command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,check=True).stdout.strip()
    require(shlex.split(command)==receipt['command'],'coordinator process identity changed')
    observed=subprocess.run(['lsof','-a','-p',str(pid),'-Fn','--',*receipt['legacy_locks']],capture_output=True,text=True,check=True).stdout.splitlines()
    require(set(x[1:] for x in observed if x.startswith('n'))==set(receipt['legacy_locks']),'coordinator must retain every legacy lock descriptor')
    inodes=[]
    for name in receipt['legacy_locks']:
        path=safe(name);fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:raise ValueError('legacy lock not held by coordinator')
            stat=os.fstat(fd);inodes.append(dict(path=name,device=stat.st_dev,inode=stat.st_ino))
        finally:os.close(fd)
    return dict(pid=pid,locks=inodes,local_dispatch_paused=True)

def owned_argv(arguments,namespaces):
    for argument in arguments:
        value=argument.split('=',1)[1] if argument.startswith('--') and '=' in argument else argument
        if any(value==n or value.startswith(n.rstrip('/')+'/') for n in namespaces):return True
    return False

def reconcile_host(host,status,processes,known_jobs,pool_roots,*,now,slot=None):
    """Pure exact claim/process reconciliation. Descendants do not add slots."""
    require(abs(now-status['at'])<=60,'host observation needs fresh central status')
    claims=[w for w in status['workbooks'] if w['state']=='active' and isinstance(w.get('owner'),str) and w['owner'].startswith((host+':',host+'/'))]
    require(len(claims)<2,'host already has two active claims')
    if slot is not None:
        require(type(slot) is int and slot in (1,2),'closed logical slot required')
        for claim in claims:
            owner=claim['owner']
            require(any(owner.startswith(host+':slot-'+str(n)+':') or owner.startswith(host+'/slot-'+str(n-1)+'/') for n in (1,2)),'unknown host slot owner requires reconciliation')
            require(not (owner.startswith(host+':slot-'+str(slot)+':') or owner.startswith(host+'/slot-'+str(slot-1)+'/')),'selected logical slot already occupied')
    roots={};by_pid={x['pid']:x for x in processes};supervisors=set()
    for proc in processes:
        argv=proc['argv']
        if proc.get('pool'):
            require(proc['pool'] in pool_roots and proc.get('pool_stopped') is True,'pool must be drained before reconstruction')
            supervisors.add(proc['pid']);continue
        if '--job' in argv and any(x.endswith('/scripts/fleet/worker.py') for x in argv):
            def value(flag):
                require(argv.count(flag)==1 and argv.index(flag)+1<len(argv),'worker arguments incomplete');return argv[argv.index(flag)+1]
            path=value('--job');require(path in known_jobs,'unknown worker job requires reconciliation')
            selected,job,claim=known_jobs[path]
            require(value('--job-sha256')==selected['sha256'] and value('--owner')==claim['owner'],'worker args do not bind claim')
            matches=[w for w in claims if w['workbook']==job['workbook']]
            require(len(matches)==1,'worker has no exact active central claim');w=matches[0]
            require(w['owner']==claim['owner'] and w['fence']==claim['fence'] and w['claim_id']==claim['claim_id']
                and w['source_sha256']==job['source']['sha256']==claim['source_sha256'] and w['version']==job['version']==claim['version']
                and w['expires']>now,'native worker claim differs or expired')
            require(w['owner'] not in roots,'duplicate worker for one claim');roots[w['owner']]=proc['pid']
    require(len(roots)==len(claims) and len(roots)<2,'missing worker or full native host requires reconciliation')
    allowed=set(roots.values())|supervisors
    for proc in processes:
        if not proc.get('task_related') or proc['pid'] in allowed:continue
        ancestors=set();current=proc['ppid']
        while current in by_pid and current not in ancestors:
            ancestors.add(current);current=by_pid[current]['ppid']
        require(bool(ancestors.intersection(roots.values())),'unknown or orphan task process requires reconciliation')
    return dict(host=host,workers=roots,active_claims=[{k:w[k] for k in ('workbook','owner','fence','version','source_sha256')} for w in claims],worker_count=len(roots),maximum_workers=2,observed_at=now)

def process_cwd(entry,uid,current_uid):
    from pathlib import Path
    try:return str((entry/'cwd').resolve())
    except PermissionError:
        if uid==current_uid:
            import subprocess
            require(isinstance(entry,Path) and entry.parent==Path('/proc') and entry.name.isdigit(),'cannot inspect a process owned by the pipeline account')
            # Non-dumpable PAM/SSH children share the worker UID. Read their
            # cwd through the already configured noninteractive sudo; do not
            # whitelist an argv label that an orphan process could imitate.
            result=subprocess.run(['sudo','-n','readlink','--',str(entry/'cwd')],capture_output=True,text=True,timeout=5)
            if not entry.exists():raise FileNotFoundError(str(entry))
            require(result.returncode==0 and result.stdout.strip().startswith('/'),'cannot inspect a process owned by the pipeline account')
            return result.stdout.rstrip('\n')
        # Root service cwd is intentionally unreadable to the unprivileged
        # worker. Its public argv, UID and parent identity are still inspected;
        # path-based source matching and descendant checks remain active.
        return None

def remote_preflight(payload):
    import os,sys,shutil,json,time
    from pathlib import Path
    p=payload['plan'];job=payload['job'];sys.path.insert(0,job['cwd'])
    from scripts.fleet.migration import checked,binding,safe
    from scripts.fleet.worker import validate
    if checked(p['job'])!=job:raise ValueError('remote job changed')
    validate(job)
    if binding(p['worker']['path'])!=p['worker']:raise ValueError('runtime worker changed')
    a=checked(p['authority'])
    for key in ('tracker','checkpoint','descriptor','history','inspection'):checked(a[key])
    if a['job']!=p['job'] or a['source']!=job['source']:raise ValueError('native remote identity changed')
    if shutil.disk_usage(job['cwd']).free<job['minimum_disk_bytes']:raise ValueError('disk reserve unavailable')
    receipt=safe(p['remote_receipt'])
    unused=[safe(job['run']),safe(job['result']).parent,receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]
    if a['kind'] in {'normalization-hold','dialogue-pre-review-0029'}:
        history=checked(a['history']);unused.append(safe(history['home']))
        if a['kind']=='dialogue-pre-review-0029':unused.append(safe(history['takeover_directory']))
        else:
            original=checked(history['original_config'])
            unused.append(safe(Path(original['run_root'])/'lease-takeovers'/('fleet-normalization-'+job['workbook']+'-001')))
    for path in unused:
        if path.exists():raise ValueError('attempt already exists; reconcile')
    # Original tracker/config directory catches historical children as well as
    # the selected output paths; /proc identity is read immediately pre-Popen.
    original_home=Path(a['tracker']['path']).parents[2]
    needles=[p['job']['path'],job['run'],job['result'],str(original_home)]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if any(n in cmd for n in needles):raise ValueError('original or selected task process active')
        except (FileNotFoundError,ProcessLookupError):continue
    processes=[];known={}
    for row in p['known_jobs']:
        value=checked(row);claimfile=Path(value['run'])/'claim.json'
        known[row['path']]=(row,value,json.loads(claimfile.read_bytes()) if claimfile.is_file() else {})
    namespaces=['/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2','/home/gmahajan_labelbox_com/synthetic-data-fleet']
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if stat[0]=='Z':continue
            argv=[x.decode(errors='replace') for x in (entry/'cmdline').read_bytes().split(b'\0') if x]
            uid=entry.stat().st_uid;cwd=process_cwd(entry,uid,os.getuid());pool=None
            for poolroot in p['pool_roots']:
                if '--run' in argv and argv[argv.index('--run')+1]==poolroot and any(x.endswith('supervisor.py') or x.endswith('/pool.py') for x in argv):pool=poolroot
            processes.append(dict(pid=int(entry.name),ppid=int(stat[1]),start_ticks=stat[19],argv=argv,
                task_related=owned_argv(argv,namespaces) or bool(cwd and any(cwd==n or cwd.startswith(n+'/') for n in namespaces)) or bool(uid==os.getuid() and argv and Path(argv[0]).name.startswith('python')) or any(x.startswith(('scripts.','xl_')) for x in argv),uid=uid,cwd_visible=cwd is not None,pool=pool,
                pool_stopped=bool(pool and (Path(pool)/'stop.json').is_file())))
        except (FileNotFoundError,ProcessLookupError):continue
    observation=reconcile_host(p['host'],payload['central'],processes,known,p['pool_roots'],now=time.time(),slot=p['slot'])
    require(observation['worker_count']==0, 'bootstrap requires no other worker on the selected host')
    return dict(status='BOOTSTRAP_VALIDATED_NOT_STARTED',job=p['job'],authority=p['authority'],host=observation,process_inventory=processes,model_calls=0,claims_changed=0)

def remote_launch(payload):
    import fcntl,json,os,subprocess,time
    from pathlib import Path
    p=payload['plan'];job=payload['job'];receipt=Path(p['remote_receipt']);lockfds=[]
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    paths=[Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/source-slots')/p['host']/('slot-'+str(p['slot'])+'.lock')]
    paths += [Path(root)/('slot-'+str(p['slot']-1)+'.lock') for root in p['pool_roots'] if Path(root).exists()]
    try:
        for path in paths:
            if any(x.is_symlink() for x in (path,*path.parents)):raise ValueError('slot lock namespace symlink')
            path.parent.mkdir(parents=True,exist_ok=True);fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BaseException:os.close(fd);raise
            lockfds.append(fd)
        observation=remote_preflight(payload)
        if any(x.exists() for x in [receipt,receipt.with_suffix('.intent.json'),receipt.with_suffix('.log')]):raise ValueError('launch namespace consumed')
        command=payload['command'];keep(receipt.with_suffix('.intent.json'),dict(command=command,job=p['job'],root_intent=payload['root_intent'],free_slot=observation,slot_locks=[str(x) for x in paths],automatic_retry=False))
        with receipt.with_suffix('.log').open('xb') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True,cwd=job['cwd'],
                pass_fds=tuple(lockfds),env={**os.environ,'PATH':str(Path(command[0]).parent)+':'+os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'})
        start=(Path('/proc')/str(proc.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
        result=dict(pid=proc.pid,start_ticks=start,command=command,job=p['job'],root_intent=payload['root_intent'],slot=p['slot'],automatic_retry=False)
        keep(receipt,result);return result
    finally:
        for fd in lockfds:os.close(fd)

def function_source(name):
    source=Path(__file__).read_text();lines=source.splitlines(keepends=True)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ''.join(lines[node.lineno-1:node.end_lineno])

def ssh(p,job,payload,*,execute=False):
    code='\n'.join(function_source(n) for n in ('require','owned_argv','reconcile_host','process_cwd','remote_preflight','remote_launch'))
    code+='\nimport json,sys\npayload=json.load(sys.stdin)\nprint(json.dumps('+('remote_launch' if execute else 'remote_preflight')+'(payload)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('remote source custody uncertain; inspect retained evidence, never replay')
    return json.loads(result.stdout)

def launch(row,journal=None,*,execute=False):
    p,job=load(row);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
        status=client.call('status');before=central_gate(status,p,job);protected_gate(status,p)
        local=local_custody(p);remote=ssh(p,job,dict(plan=p,job=job,central=status))
        if not execute:return dict(status='VALIDATED_NOT_STARTED',central=before,local=local,remote=remote,claims_changed=0,workers_started=0)
        directory=safe(Path(journal).absolute());require(not directory.exists(),'launch journal exists; reconcile without replay');directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',dict(plan=row,implementation=binding(__file__),before=before,local=local,remote=remote,automatic_retry=False),immutable=True);phase='reconcile'
        try:
            status=client.call('status');central_gate(status,p,job);protected_gate(status,p)
            require(local_custody(p)==local,'local custody changed during preflight')
            ready=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'historical_first_custody':p['authority']})
            require(ready['state']=='ready' and ready['fence']==0 and ready['owner'] is None,'uncertain ready result')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            status=client.call('status');protected_gate(status,p)
            rows=[w for w in status['workbooks'] if w['workbook']=='0029']
            require(len(rows)==1 and rows[0]['state']=='ready' and rows[0]['fence']==0 and rows[0]['owner'] is None and rows[0]['claim_id'] is None and rows[0]['source_sha256']==job['source']['sha256'], 'ready custody changed before launch')
            result=ssh(p,job,dict(plan=p,job=job,central=status,command=command,root_intent=intent),execute=True)
            return persist(directory/'launched.json',dict(intent=intent,remote=result,automatic_retry=False),immutable=True)
        except Exception as exc:
            persist(directory/'uncertain.json',dict(intent=intent,phase=phase,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True);raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--journal');p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute and not a.journal:p.error('--execute requires fresh journal')
    print(json.dumps(launch(dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256),a.journal,execute=a.execute),indent=2))

if __name__=='__main__':main()
