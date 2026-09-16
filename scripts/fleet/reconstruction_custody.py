"""First fleet custody for explicitly reviewed source reconstruction continuations.

Read-only by default. A physical retry, admission to authoring, or publication
requires a separate owning handoff; this controller never declares a task ready.
"""
from __future__ import annotations
import argparse,ast,fcntl,json,math,os,re,shlex,subprocess,sys,time
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
from scripts.fleet import launch_job as L,fleet_control as F
SCHEMA='fleet-source-reconstruction-custody/v1'
SCHEMA_V2='fleet-source-reconstruction-custody/v2'
MEMORY=8*1024**3
PROFILES={'0013': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0013-profile.json', 'sha256': '660137e6919f2dbe237882e24e3d4df399fb23acb7b70b04fa4dac8cb8c251c2'}, '0051': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0051-profile.json', 'sha256': '2fafd7f4acf3f25681309e2a821278d29c8f2ccca2639f2b97a55fb5bf5417c5'}, '0062': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0062-profile.json', 'sha256': 'bb2e161087f892d2158e2f53e23ac04a739626153b1b48f10cf32b45a3b275b9'}, '0064': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0064-profile.json', 'sha256': '6b9c8afa7e38d0f821c8cac256e371c5eb8cac81b37b86d965e64dd99e3ab9ad'}, '0200': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0200-profile.json', 'sha256': '172b21c40f40138f20ec4f8babf7819fc30237647bcae87fc2752fc60ea88428'}, '0298': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/control/source-first-custody-003/0298-profile-002.json', 'sha256': 'a827bccee1e9d0761d843e5643c00d93e007bba19469822e5fe692f0bfc450b9'}}

def require(ok,message):
    if not ok:raise ValueError(message)

def source_authority(p,job):
    selected=PROFILES.get(job['workbook']);require(selected is not None,'source reconstruction profile not reviewed')
    require(p['profile']==selected,'exact reviewed source profile required')
    profile=checked(selected);plan=checked(profile['plan']);native=checked(profile['native_probe']);resource=checked(profile['native_resource'])
    resource_only=profile['validation_kind']=='source_identity'
    schema='fleet-resource-source-reconstruction-profile/v1' if resource_only else 'fleet-source-reconstruction-profile/v1'
    require(profile['schema_version']==schema and profile['workbook']==job['workbook'] and profile['host']==p['host'],'source profile host/workbook differs')
    require(resource_only==(job['workbook'] in {'0298','0051','0062'}),'source validation scope differs')
    current_attempt={'0200':1,'0013':1,'0064':1,'0298':6,'0051':4,'0062':4}[job['workbook']]
    require((profile['current_attempt'],profile['next_attempt'])==(current_attempt,current_attempt+1),'original monotonic attempt transition differs')
    report=checked(profile['staging_report'])
    require(plan['helper']==job['driver'] and job['inputs'][0]==profile['plan'],'job must select exact reconstruction helper/plan')
    require(job['command']==[str(Path(job['cwd']).parent/'venv/bin/python'),'-B',job['driver']['path'],'run','--plan',profile['plan']['path'],'--sha256',profile['plan']['sha256']],'closed source-only command required')
    require(plan['guards']==dict(outer_seconds=1350,inner_seconds=900,memory_bytes=MEMORY,disk_bytes=12*1024**3,models=0),'original reconstruction guards required')
    require(job['timeout_seconds']==1350 and job['memory_limit_bytes']==MEMORY and job['minimum_disk_bytes']==12*1024**3,'worker must retain outer1350s8GiB12start guards')
    require(set(job)=={'schema_version','workbook','source','version','driver','inputs','command','cwd','run','result','memory_limit_bytes','timeout_seconds','minimum_disk_bytes'},'no fresh admission or model history context allowed')
    source={k:plan['source'][k] for k in ('path','sha256')};require(job['source']==source,'source identity differs')
    ledger=checked(plan['ledger']);prior=ledger['build'];snapshot=checked(plan['snapshot']);build=checked(plan['build'])
    require(prior['attempts']==current_attempt and prior['state']=='STOPPED' and prior['source_generation_id']==plan['generation'],'original stopped attempt1 required')
    require(prior['source_checkpoint']['state']=='complete' and prior['source_checkpoint']['generation_id']==plan['generation'],'completed original source generation required')
    require(snapshot['fingerprint']==job['version']==Path(snapshot['root']).name and build['snapshot']==plan['snapshot'],'explicit successor identity differs')
    require(native['status']=='SOURCE_RECONSTRUCTION_PREFLIGHT_PASS' and native['plan']==profile['plan'] and native['snapshot']==plan['snapshot']
        and native['ledger']==plan['ledger'] and native['current_attempt']==current_attempt and native['next_attempt']==current_attempt+1,'current native source preflight required')
    require(native['models_dispatched']==native['tracker_mutations']==0 and native['qualification_claimed'] is False,'native source proof cannot grant model/task admission')
    if resource_only:
        require(plan['schema_version']=='resource-source-reconstruction-continuation/v1' and plan['current_attempt']==current_attempt and plan['next_attempt']==current_attempt+1,'resource reconstruction selection differs')
        require(native['probe_scope']=='source_identity_and_reuse' and native['numerical_segmentation_validated'] is False
            and native['selected_targets']==plan['targets']==profile['output_count'] and 'outputs' not in native,'resource identity cannot imply numerical qualification')
        require(report['plan']==profile['plan'] and report['native_probe']==profile['native_probe'] and report['numerical_segmentation_validated'] is False
            and report['models_dispatched']==report['owning_reconstructions']==report['claims']==0,'resource staging evidence differs')
        provenance=checked(profile['guard_binding']) if 'guard_binding' in profile else report
        require(provenance['native_probe']==profile['native_probe'] and provenance['native_resource']==profile['native_resource'],'resource guard evidence detached')
        for key in ('guard','guard_command'):require(binding(provenance[key]['path'])==provenance[key],'resource guard provenance changed')
    else:
        require(plan['schema_version']==job['workbook']+'-source-reconstruction-continuation/v1','numerical reconstruction selection differs')
        require(native['oracle_reads']==0 and len(native['outputs'])==profile['output_count'] and all(v=='match' for v in native['outputs'].values()),'complete original target outputs required')
        if job['workbook']=='0200':require(native['cycles']==0,'unexpected source cycle')
        else:
            require(len(native['cycles'])==6 and all(c['diagnostic']['certified'] is True and c['equivalent'] is True for c in native['cycles']),'source cycle/endpoint proof incomplete')
            reuse=native['source_reuse']
            require(reuse['generation_id']==plan['generation'] and reuse['reused_from_attempt']==current_attempt and reuse['source_rebuilt'] is False and reuse['owning_attempt_mutated'] is False
                and reuse['actual_next_curation_sha256']==reuse['current_checkpoint_curation_sha256']==plan['curation']['sha256'],'actual restricted source reuse proof required')
    require(native['source_generation']==plan['generation'] and native['original_source_producer_preserved'] is True,'source producer/generation must be preserved')
    require(resource['plan']==profile['plan'] and (resource_only or resource['helper']==plan['helper']) and resource['returncode']==0 and resource['failure'] is None
        and resource['timeout_seconds']==900 and resource['memory_limit_bytes']==MEMORY and resource['log']['sha256']==profile['native_probe']['sha256'],'native source guard/log differs')
    require(resource['metrics']['peak_rss_bytes']<=MEMORY,'native source probe exceeded memory guard')
    receipt=checked(profile['import_receipt']);manifest=checked(profile['transfer_manifest'])
    require(receipt['absolute_paths_preserved'] is True and receipt['historical_bytes_rewritten'] is False and receipt['attempts_reset'] is False
        and receipt['archive_sha256']==manifest['archive_sha256'],'verified source artifact transfer required')
    original=checked(plan['closure']);copied={r['original_path']:r['sha256'] for r in receipt['files']}
    for row in [*original['corpus_files'].values(),*original['external'],plan['snapshot'],plan['helper']]:
        require(copied.get(row['path'])==row['sha256'],'source closure missing from verified transfer')
    require(job['result']==str(Path(plan['home'])/'result.json') and Path(job['run'])!=Path(plan['home']),'separate worker/result journals required')
    for row in job['inputs']:checked(row)
    return profile,plan

def operation_gate(status,target):
    require(status.get('schema_version')=='fleet-coordinator/v1' and status.get('model_limit')==status.get('publication_limit')==1,'global model/publication limits must remain1')
    now=status['at'];require(type(now) in (int,float) and math.isfinite(now),'coordinator clock required')
    active=[];seen=set()
    for op in status['operations']:
        require(op.get('kind') in {'model','publication'} and op.get('state') in {'active','hold','confirmed','abandoned'},'unknown operation requires reconciliation')
        key=(op['kind'],op.get('operation_id'));require(key not in seen and isinstance(key[1],str) and key[1],'unique operation identity required');seen.add(key)
        require(op['state']!='hold','uncertain model/publication requires reconciliation')
        if op['state']=='active':active.append(op)
    require(len(active)<=1,'global operation capacity exceeded')
    for op in active:
        require(op['kind']=='model' and op['workbook']!=target,'publication or target model requires reconciliation before launch')
        rows=[w for w in status['workbooks'] if w['workbook']==op['workbook']];require(len(rows)==1,'active operation owner missing');w=rows[0]
        require(w['state']=='active' and w['fence']==op['fence'] and w['owner']==op['owner'] and w['claim_id'] and w['version'],'active model has uncertain claim')
        require(type(op.get('fence')) is int and op['fence']>0 and isinstance(op.get('owner'),str) and bool(op['owner']),'active model fence/owner incomplete')
        require(re.fullmatch('[0-9a-f]{64}',w.get('source_sha256','')) and re.fullmatch('[0-9a-f]{64}',op.get('identity','')),'active model identity incomplete')
        require(type(op.get('started')) in (int,float) and math.isfinite(op['started']) and op['started']<=now,'invalid operation start')
        require(all(type(x) in (int,float) and math.isfinite(x) and x>now for x in (op.get('expires'),w.get('expires'))),'expired or unknown operation lease')
        require(op.get('ended') is None and op.get('evidence') is None and op['expires']==w['expires'],'expired or terminal active model')
    return active

def selected_smoke(p):
    schema=p.get('schema_version',SCHEMA)
    if schema==SCHEMA:
        require('smoke_workbook' not in p,'legacy source custody cannot select another smoke')
        return '0270'
    require(schema==SCHEMA_V2 and isinstance(p.get('smoke_workbook'),str) and p.get('smoke_workbook') in {'0029','0270'},'closed selected smoke workbook required')
    return p['smoke_workbook']


def smoke_gate(p,status):
    workbook=selected_smoke(p)
    operation_gate(status,checked(p['job'])['workbook'])
    verification=checked(p['smoke_verification']);collection=checked(p['smoke_collection'])
    require(verification.get('schema_version')=='independent-published-task-copy-check/v1' and verification.get('workbook')==workbook
        and all(verification.get(k) is True for k in ('passed','byte_identical_to_immutable_generation','current_qualification_verified'))
        and verification.get('model_calls')==verification.get('runtime_operations')==0,'actual selected smoke independent publication verification required')
    require(collection.get('schema_version')=='harbor-fleet-collection/v1' and collection.get('status')=='COLLECTED' and collection.get('workbook')==workbook
        and collection.get('local_byte_integrity_verified') is True and collection['independent_remote_verification']['sha256']==p['smoke_verification']['sha256'],'actual selected smoke qualified collection required')
    require(F.files(Path(collection['output']))==collection['files'] and collection['files']['generation-manifest.json']['sha256']==verification['task_manifest']['sha256'],'collected selected smoke bytes changed')
    if p.get('schema_version')==SCHEMA_V2:
        manifest=json.loads((Path(collection['output'])/'generation-manifest.json').read_bytes())
        require(manifest.get('identity',{}).get('workbook_id')==workbook,'local selected smoke generation workbook differs')
    rows=[w for w in status['workbooks'] if w['workbook']==workbook];require(len(rows)==1 and rows[0]['state']=='complete','actual selected smoke is incomplete')

def central_gate(status,p,job):
    operation_gate(status,job['workbook'])
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']];require(len(rows)==1,'exact workbook claim required');w=rows[0]
    require(w['state']=='hold' and w['fence']==0 and w['owner'] is None and w['version'] is None and w['claim_id'] is None
        and w['source_sha256']==job['source']['sha256'] and w['reason']==p['expected_reason'],'initial untouched central hold required; never retry a consumed claim')
    require(not any(o['workbook']==job['workbook'] for o in status['operations']),'prior fleet operation requires separate reconciliation')
    return w

def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','host','profile','job','worker','coordinator_receipt','coordinator_process','handoff','smoke_verification','smoke_collection','new_owner','remote_receipt','expected_reason','known_jobs','pool_roots','slot'}|({'smoke_workbook'} if p.get('schema_version')==SCHEMA_V2 else set()),'closed reconstruction launch plan required')
    require(p['schema_version'] in {SCHEMA,SCHEMA_V2} and p['host'] in L.HOSTS and type(p['slot']) is int and p['slot'] in (1,2),'exact fleet host/slot required')
    selected_smoke(p)
    job=checked(p['job']);profile,plan=source_authority(p,job)
    require(p['new_owner']==p['host']+':slot-'+str(p['slot'])+':'+job['workbook']+'-source-reconstruction-001','closed unique source owner required')
    require(p['worker']['path']==str(Path(job['cwd'])/'scripts/fleet/worker.py'),'bound runtime worker required')
    require(Path(p['remote_receipt']).is_relative_to(L.REMOTE) and '..' not in Path(p['remote_receipt']).parts,'canonical launch receipt required')
    require(len(p['known_jobs'])==len({b['path'] for b in p['known_jobs']}),'duplicate known host job')
    for b in p['known_jobs']:checked(b)
    require(len(p['pool_roots'])==len(set(p['pool_roots'])) and all(Path(x).is_absolute() and '..' not in Path(x).parts for x in p['pool_roots']),'exact known pool roots required')
    h=checked(p['handoff']);pause=checked(h['pause']);require(h['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION' and pause['new_dispatch_allowed'] is False,'local original owners must remain paused')
    item=next(x for x in h['workbooks'] if x['workbook_id']==job['workbook']);require({k:item['original_source'][k] for k in ('path','sha256')}==job['source'] and item['status']!='published','original handoff source differs')
    return p,job,profile,plan

def local_custody(p,plan):
    receipt=checked(p['coordinator_receipt']);process=checked(p['coordinator_process'])
    require(process['command']==receipt['command'] and binding(receipt['command'][2])['sha256']==receipt['code_sha256'],'frozen coordinator differs')
    lines=subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout.splitlines()
    original=str(Path(plan['corpus']).parent)
    require(not any(line.strip().split(None,1)[0]!=str(os.getpid()) and original in line for line in lines if line.strip()),'original local task still executing')
    pid=process['pid'];command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,check=True).stdout.strip()
    require(shlex.split(command)==receipt['command'],'coordinator process identity differs')
    observed=subprocess.run(['lsof','-a','-p',str(pid),'-Fn','--',*receipt['legacy_locks']],capture_output=True,text=True,check=True).stdout.splitlines()
    require(set(x[1:] for x in observed if x.startswith('n'))==set(receipt['legacy_locks']),'coordinator must own original legacy locks')
    for name in receipt['legacy_locks']:
        fd=os.open(safe(name),os.O_RDWR|os.O_NOFOLLOW)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:raise ValueError('legacy lock is not held')
        finally:os.close(fd)
    return dict(pid=pid,legacy_locks=receipt['legacy_locks'],local_dispatch_paused=True)

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
    import hashlib,json,os,shutil,sys,time,importlib.util
    from pathlib import Path
    p=payload['plan'];job=payload['job'];sys.path.insert(0,job['cwd'])
    from scripts.fleet.migration import checked,binding,safe
    from scripts.fleet.worker import validate
    validate(job)
    if checked(p['job'])!=job or binding(p['worker']['path'])!=p['worker']:raise ValueError('selected remote worker/job changed')
    profile=checked(p['profile']);plan=checked(profile['plan']);snapshot=checked(plan['snapshot'])
    if plan['helper']!=job['driver'] or job['version']!=snapshot['fingerprint']:raise ValueError('source helper/code differs')
    actual={}
    root=Path(snapshot['root'])
    for path in root.rglob('*'):
        if path.is_symlink():raise ValueError('code symlink')
        if path.is_file() and not {'__pycache__','.pytest_cache'}.intersection(path.relative_to(root).parts) and path.name!='.pipeline-code.json':actual[path.relative_to(root).as_posix()]=binding(path)['sha256']
    if actual!=snapshot['files']:raise ValueError('source code changed')
    closure=checked(plan['closure']);original=Path(plan['corpus']);actual={}
    selected_roots=[original/k/job['workbook'] for k in ('sources','segments','workbooks')] if profile['validation_kind']=='source_identity' else [original]
    for path in (p for base in selected_roots for p in base.rglob('*')):
        if path.is_symlink():raise ValueError('source symlink')
        if path.is_file() and not {'__pycache__','.pytest_cache'}.intersection(path.relative_to(original).parts):actual[path.relative_to(original).as_posix()]=binding(path)
    if actual!=closure['corpus_files']:raise ValueError('source corpus changed')
    for row in closure['external']:
        if binding(row['path'])!=row:raise ValueError('source external bytes changed')
    receipt=safe(p['remote_receipt'])
    for path in [safe(job['run']),safe(plan['home']),receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]:
        if path.exists():raise ValueError('source continuation already attempted; reconcile')
    if shutil.disk_usage(root).free<12*1024**3:raise ValueError('12GiB start reserve unavailable')
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
    return dict(status='SOURCE_CUSTODY_VALIDATED_NOT_STARTED',plan=payload['binding'],job=p['job'],profile=p['profile'],host=observation,
        process_inventory=processes,source_ledger=plan['ledger'],models_dispatched=0,claims_changed=0)

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
    source=Path(__file__).read_text();lines=source.splitlines(keepends=True);node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ''.join(lines[node.lineno-1:node.end_lineno])

def ssh(p,job,payload,*,execute=False):
    code='\n'.join(function_source(n) for n in ('require','owned_argv','reconcile_host','process_cwd','remote_preflight','remote_launch'))
    code+='\nimport json,sys\npayload=json.load(sys.stdin)\nprint(json.dumps('+('remote_launch' if execute else 'remote_preflight')+'(payload)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('remote source custody uncertain; inspect retained evidence, never replay')
    return json.loads(result.stdout)

def launch(row,journal=None,*,execute=False):
    p,job,profile,plan=load(row);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
        status=client.call('status');before=central_gate(status,p,job);smoke_gate(p,status);local=local_custody(p,plan)
        remote=ssh(p,job,dict(plan=p,job=job,binding=row,central=status))
        if not execute:return dict(status='SOURCE_CUSTODY_VALIDATED_NOT_STARTED',central=before,local=local,remote=remote,claims_changed=0,workers_started=0)
        directory=safe(Path(journal).absolute());require(not directory.exists(),'root launch already attempted; reconcile');directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',dict(plan=row,implementation=binding(__file__),before=before,local=local,remote=remote,automatic_retry=False),immutable=True);phase='reconcile'
        try:
            status=client.call('status');central_gate(status,p,job);smoke_gate(p,status);require(local_custody(p,plan)==local,'local custody changed')
            ready=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'source_reconstruction_authority':p['profile']})
            require(ready['state']=='ready' and ready['fence']==0 and ready['owner'] is None,'uncertain first ready result')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            status=client.call('status');operation_gate(status,job['workbook']);smoke_gate(p,status)
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            launched=ssh(p,job,dict(plan=p,job=job,binding=row,central=status,command=command,root_intent=intent),execute=True)
            return persist(directory/'launched.json',dict(intent=intent,remote=launched,automatic_retry=False),immutable=True)
        except Exception as exc:
            persist(directory/'uncertain.json',dict(intent=intent,phase=phase,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True);raise

def reconstruction_handoff(row,worker_outcome,status):
    """Verify returned exact attempt2 evidence; grant no authoring or review budget."""
    p,job,profile,plan=load(row);operation_gate(status,job['workbook'])
    outcome=checked(worker_outcome);intent=checked(outcome['intent'])
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1' and outcome['returncode']==0 and outcome['failure_type'] is None
        and outcome['automatic_retry'] is False and intent['job']==p['job'] and intent['owner']==p['new_owner'],'successful exact source worker required')
    require(outcome['result']==binding(job['result']),'source worker result differs')
    result=checked(outcome['result']);native=checked(binding(Path(plan['home'])/'outcome.json'))
    require(native['status']=='COMPLETED_OPERATION' and native['returncode']==0 and native['failure'] is None and native['result']==outcome['result'] and native['automatic_retry'] is False,'owning outer reconstruction guard failed')
    require(result['status']=='SOURCE_RECONSTRUCTION_VERIFIED' and result['plan']==profile['plan'] and result['prior_ledger']==plan['ledger']
        and result['models_dispatched']==0 and result['publication_performed'] is False and result['automatic_retry'] is False,'source result cannot qualify authoring')
    prior=checked(plan['ledger']);after=checked(result['ledger']);build=after['build']
    require(after['source']==prior['source'] and after['build_history']==prior['build_history']+[prior['build']] and build['attempts']==profile['next_attempt'] and build['state']=='VERIFIED','exact monotonic attempt2 completion required')
    require(build['source_reused'] is True and build['source_generation_id']==plan['generation'] and build['source_checkpoint']['reused_from_attempt']==profile['current_attempt']
        and build['source_manifest_sha256']==prior['build']['source_manifest_sha256'],'original source generation/producer not preserved')
    require(build['bindings']['curation_sha256']==plan['curation']['sha256'] and build['bindings']['code_sha256']==job['version'],'source selector/code differs after reconstruction')
    claims=[w for w in status['workbooks'] if w['workbook']==job['workbook']];require(len(claims)==1,'source claim absent');claim=claims[0]
    require(claim['state']=='hold' and claim['fence']==1 and claim['owner']==p['new_owner'] and claim['version']==job['version']
        and json.loads(claim['reason'])==worker_outcome,'exact stopped reconstruction custody required')
    require(not any(o['workbook']==job['workbook'] for o in status['operations']),'source reconstruction cannot consume model/publication operations')
    return dict(schema_version='fleet-reconstruction-remaining-authoring-handoff/v1',status='RECONSTRUCTION_VERIFIED_AWAITING_EXPLICIT_AUTHORING_CONTINUATION',
        workbook=job['workbook'],authority=p['profile'],source_plan=profile['plan'],worker_outcome=worker_outcome,source_result=outcome['result'],
        prior_ledger=plan['ledger'],current_ledger=result['ledger'],source_generation=plan['generation'],reconstruction_attempts_consumed=profile['next_attempt'],
        new_admission_allowed=False,model_permission_granted=False,review_budget_reset=False,publication_allowed=False,automatic_retry=False)

def main():
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal');a.add_argument('--execute',action='store_true');x=a.parse_args()
    require(not x.execute or x.journal is not None,'fresh root launch journal required')
    print(json.dumps(launch(dict(path=str(Path(x.plan).absolute()),sha256=x.sha256),x.journal,execute=x.execute)))
if __name__=='__main__':main()
