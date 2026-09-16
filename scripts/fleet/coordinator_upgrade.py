"""Once-only coordinator cutover; all remote HTTP callers must be briefly frozen.

The original authority, snapshots and ledger rows are retained. No operation is
replayed, no claim is renewed here, and uncertainty never causes a second start.
"""
from pathlib import Path
import argparse,fcntl,hashlib,math,importlib.util,json,os,signal,socket,sqlite3,subprocess,sys,time

COORDINATOR_SHA='ac4a7ff3fd671f0a770b75addb98bb069a4f45019c0b0beafc695b2b36404913'
AUTH_SHA='4cf7d589371b8b7805b34658f801884ac769ea8bc69009631610975e32c94263'
CHANGE_ID='remove-global-model-limit-20260913-001'
ROOT=Path(__file__).absolute().parents[2]
HOME=ROOT/'diagnostics/vm-fleet-20260912/control/model-concurrency-001'
CONTROL=HOME.parent
MAINTENANCE_SHA='184c2bd3e914ac7b20fc740b05d9a37956a7ff0f214d5e8dfcccf4ff541c06c2'
COHORT={'path':str(CONTROL/'operational-cohort-014/workers.json'),'sha256':'cacc3553f5e77bee344037ac64964a459355b32869b7e16ac395cc8352976154'}


def require(ok,message):
    if not ok: raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and not any(q.is_symlink() for q in (p,*p.parents)), 'canonical artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
    require(binding(row['path'])==row,'bound artifact changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)


def implementation():
    path=HOME/'coordinator-v2.py';require(binding(path)['sha256']==COORDINATOR_SHA,'successor coordinator changed')
    spec=importlib.util.spec_from_file_location('verified_capacity_coordinator',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def identity(pid):
    result=subprocess.run(['/bin/ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(result.returncode==0 and result.stdout.strip(),'original coordinator process absent')
    return result.stdout.strip()


def ledger(database):
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
        require(db.execute('PRAGMA quick_check').fetchone()[0]=='ok','ledger integrity failed')
        values={}
        for name in ('workbooks','claims','operations'):
            rows=db.execute('SELECT * FROM '+name+' ORDER BY 1,2').fetchall()
            values[name]=dict(count=len(rows),sha256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())
        return values


def prepare():
    old_binding=binding(CONTROL/'coordinator-process.json');old=checked(old_binding)
    require(binding(CONTROL/'coordinator-v1.py')['sha256']==old['code_sha256'],'original coordinator code changed')
    require(old['url']=='http://127.0.0.1:8765' and Path(old['database'])==CONTROL/'fleet.sqlite3','authority identity changed')
    require(old['pid']==66715 and len(old['legacy_locks'])==3,'original authority receipt differs')
    observed=identity(old['pid']);require(str(CONTROL/'coordinator-v1.py') in observed,'original PID command differs')
    # Do not expose credential values or copy them into the review artifact.
    private=Path(old['private_directory'])
    require(private.stat().st_mode&0o777==0o700,'credential directory custody differs')
    for name in ('worker','admin'):
        p=private/(name+'.token');require(p.stat().st_mode&0o777==0o600 and not p.is_symlink(),'credential file custody differs')
    module=implementation();status=module.Client(old['url'],private/'worker.token').call('status')
    require(status['model_limit']==status['publication_limit']==1,'old coordinator policy changed')
    require(not any(op['state']=='hold' for op in status['operations']),'uncertain operation must be reconciled before maintenance')
    command=list(old['command']);require(command[2]==str(CONTROL/'coordinator-v1.py'),'original command differs')
    command[2]=str(HOME/'coordinator-v2.py')
    auth=binding(HOME/'authorization.json');require(auth['sha256']==AUTH_SHA,'user authorization differs')
    return dict(old_receipt=old_binding,original_process_identity=observed,successor=binding(HOME/'coordinator-v2.py'),
        authorization=auth,command=command,change_id=CHANGE_ID,driver=binding(Path(__file__).absolute()),
        active_workbooks=sorted(w['workbook'] for w in status['workbooks'] if w['state']=='active'),
        model_operations_active=sum(o['kind']=='model' and o['state']=='active' for o in status['operations']),
        original_ledger=ledger(old['database']),automatic_retry=False)


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def argument(command, flag):
    require(type(command) is list and command.count(flag)==1,'exact process argument required')
    index=command.index(flag);require(index+1<len(command),'process argument value missing')
    return command[index+1]


def target_projection(target, host, current, *, cohort_binding=COHORT):
    cohort=checked(cohort_binding)
    matches=[]
    for row in cohort['workers']:
        p=checked(row['plan'])
        if p['process']==target and p['host']==host:matches.append(p)
    require(len(matches)==1,'maintenance target lacks exact root operational projection')
    p=matches[0];require(p['process_copy']['sha256']==target['sha256'],'native process projection hash differs')
    receipt=checked(p['process_copy']);process=receipt.get('remote',receipt)
    launch=checked(p['launch_record']);require(launch['automatic_retry'] is False and launch['remote']==process and launch['intent']==process['root_intent'],'root launch/process provenance differs')
    intent=checked(process['root_intent']);launch_plan=checked(intent['plan']);job=checked(p['job'])
    require(process['job']==p['job']==launch_plan['job'] and launch_plan['host']==host and launch_plan['new_owner']==p['owner'],'root selected launch job/owner differs')
    require(intent['automatic_retry'] is False and intent['before']['state']=='hold'
            and intent['before']['workbook']==p['workbook'] and intent['before']['fence']==launch_plan['expected_fence'],'root launch predecessor fence differs')
    command=process['command']
    require(argument(command,'--job')==p['job']['path'] and argument(command,'--job-sha256')==p['job']['sha256'] and argument(command,'--owner')==p['owner'],'native selected process command differs')
    require(current['workbook']==p['workbook']==job['workbook'] and current['state']=='active'
            and current['source_sha256']==job['source']['sha256'] and current['version']==job['version']
            and current['owner']==p['owner'] and current['fence']==intent['before']['fence']+1,'frozen worker does not own current source/version/fence')
    return process


def frozen_proofs(rows, active_claims, now, *, cohort_binding=COHORT, maintenance_binding=None):
    require(finite(now) and isinstance(rows,list) and rows,'bounded actual maintenance observations required')
    claims={w['workbook']:w for w in active_claims};require(len(claims)==len(active_claims),'duplicate active claims')
    covered=set();deadlines=[];hosts=set()
    for row in rows:
        v=checked(row)
        require(set(v)=={'schema_version','host','helper','plan','frozen','status','observed_at'} and v['schema_version']=='fleet-maintenance-observation/v1','closed actual maintenance observation required')
        require(v['host'] not in hosts,'duplicate host observation');hosts.add(v['host'])
        expected_helper=maintenance_binding or dict(path=v['helper']['path'],sha256=MAINTENANCE_SHA)
        require(v['helper']==expected_helper and binding(v['helper']['path'])==v['helper'],'unreviewed maintenance producer')
        plan=checked(v['plan']);frozen=v['frozen']['value'];native=v['frozen']['binding'];status=v['status']
        require(set(v['frozen'])=={'binding','value'} and native['path']==str(Path(plan['directory'])/'frozen.json')
                and hashlib.sha256((json.dumps(frozen,sort_keys=True,indent=2)+'\n').encode()).hexdigest()==native['sha256'],'native frozen receipt bytes differ')
        require(plan['schema_version']=='fleet-maintenance-pause/v1' and plan['helper']==v['helper']
                and type(plan['maximum_pause_seconds']) is int and 1<=plan['maximum_pause_seconds']<=45,'maintenance plan/ceiling differs')
        require(frozen['status']==status['status']=='FROZEN' and frozen['plan']==v['plan'] and frozen['wall_clocks_paused'] is False
                and status['watchdog_alive'] is True and status['owner_coverage'] is True,'actual live watchdog and full frozen coverage required')
        require(native in status['files'] and frozen['watchdog'] in status['files'],'fresh status lost native frozen/watchdog evidence')
        require(all(finite(x) for x in (v['observed_at'],status['at'],frozen['frozen_at'],status['deadline_epoch'],frozen['deadline_epoch'],status['remaining_pause_seconds'])),'nonfinite maintenance clock')
        require(0<=now-v['observed_at']<=30 and frozen['frozen_at']<=status['at']<=v['observed_at']<=now,'future or stale maintenance observation')
        require(status['deadline_epoch']==frozen['deadline_epoch'] and status['deadline_epoch']-now>=12
                and 0<status['remaining_pause_seconds']<=plan['maximum_pause_seconds']
                and abs(status['remaining_pause_seconds']-(status['deadline_epoch']-status['at']))<=2,'insufficient or inconsistent watchdog runway')
        for target in plan['targets']:
            candidates=[]
            for wb,current in claims.items():
                try:process=target_projection(target,v['host'],current,cohort_binding=cohort_binding)
                except ValueError:continue
                candidates.append((wb,process))
            require(len(candidates)==1,'frozen target does not match one current central owner')
            wb,process=candidates[0];require(wb not in covered,'duplicate frozen workbook')
            require(any(x['pid']==process['pid'] and str(x['start_ticks'])==str(process['start_ticks']) and x['command']==process['command'] for x in frozen['processes']),'exact selected root missing from frozen process tree')
            covered.add(wb)
        deadlines.append(status['deadline_epoch'])
    require(set(claims)==covered,'active workbook HTTP callers not all frozen')
    return min(deadlines)


def selected_plan(plan):
    require(set(plan)=={'old_receipt','original_process_identity','successor','authorization','command','change_id','driver','active_workbooks','model_operations_active','original_ledger','automatic_retry'},'closed upgrade plan required')
    require(plan['driver']==binding(Path(__file__).absolute()) and plan['automatic_retry'] is False and plan['change_id']==CHANGE_ID,'upgrade driver/retry policy differs')
    require(plan['old_receipt']==binding(CONTROL/'coordinator-process.json'),'original authority receipt differs')
    old=checked(plan['old_receipt'])
    require(old['pid']==66715 and old['url']=='http://127.0.0.1:8765' and Path(old['database'])==CONTROL/'fleet.sqlite3'
            and binding(CONTROL/'coordinator-v1.py')['sha256']==old['code_sha256'] and len(old['legacy_locks'])==3,'original authority identity changed')
    command=list(old['command']);require(command[2]==str(CONTROL/'coordinator-v1.py'),'original server command differs');command[2]=str(HOME/'coordinator-v2.py')
    require(plan['command']==command and plan['successor']==binding(HOME/'coordinator-v2.py') and plan['successor']['sha256']==COORDINATOR_SHA,'successor command or code differs')
    require(plan['authorization']==binding(HOME/'authorization.json') and plan['authorization']['sha256']==AUTH_SHA,'user authorization differs')
    return old


def original_absent(pid):
    result=subprocess.run(['/bin/ps','-p',str(pid),'-o','pid='],capture_output=True,text=True,timeout=1)
    require(result.returncode in (0,1),'process absence inspection failed')
    return result.returncode==1 and not result.stdout.strip()


def runway(deadline, seconds=8):
    require(finite(deadline) and deadline-time.time()>=seconds,'freeze window too short at destructive boundary')


def execute(plan_row, proof_rows, journal):
    plan=checked(plan_row)
    if plan.get('schema_version')in('fleet-author-capacity-upgrade/v1','fleet-preserved-author-policy-upgrade/v1'):
        return author_execute(plan_row,proof_rows,journal)
    old=selected_plan(plan);module=implementation()
    directory=Path(journal);require(directory.is_absolute() and not directory.exists(),'fresh once-only migration journal required')
    locks=[]
    try:
        for name in ('coordinator-recovery.lock','root-job-launch.lock'):
            fd=os.open(CONTROL/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(identity(old['pid'])==plan['original_process_identity'],'original process identity changed')
        private=Path(old['private_directory']);client=module.Client(old['url'],private/'worker.token',timeout=3)
        status=client.call('status');active=sorted(w['workbook'] for w in status['workbooks'] if w['state']=='active')
        require(status['model_limit']==status['publication_limit']==1 and not any(o['state']=='hold' for o in status['operations']), 'authority is not safe for maintenance')
        deadline=frozen_proofs(proof_rows,[w for w in status['workbooks'] if w['state']=='active'],time.time())
        require(all(w['expires']-time.time()>60 for w in status['workbooks'] if w['state']=='active'),'claim lacks maintenance runway')
        directory.mkdir(mode=0o700)
        before=ledger(old['database'])
        # A private backup retains lease tokens securely outside public artifacts.
        backup=private/'before-model-concurrency-001.sqlite3';require(not backup.exists(),'prior private backup exists; reconcile')
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as destination:source.backup(destination)
        require(ledger(backup)==before,'private backup ledger differs')
        intent=retain(directory/'intent.json',dict(plan=plan_row,frozen_proofs=proof_rows,before=before,private_backup=binding(backup),at=time.time(),automatic_retry=False))
        runway(deadline)
        # Frozen remote callers cannot start new HTTP mutations. Check that the
        # original listener has finished accepted client connections as well.
        connections=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(old['pid']),'-iTCP','-sTCP:ESTABLISHED','-F','n'],capture_output=True,timeout=3)
        require(connections.returncode==1 and not connections.stdout,'original authority still has an accepted HTTP connection')
        require(identity(old['pid'])==plan['original_process_identity'],'original process changed before signal')
        retain(directory/'stop-intent.json',dict(pid=old['pid'],identity=plan['original_process_identity'],at=time.time()))
        runway(deadline,16)
        os.kill(old['pid'],signal.SIGTERM)
        until=time.monotonic()+3
        while True:
            with socket.socket() as sock:
                sock.settimeout(.1);open_port=sock.connect_ex(('127.0.0.1',8765))==0
            if not open_port and original_absent(old['pid']):break
            require(time.monotonic()<until,'original listener stop uncertain');time.sleep(.02)
        runway(deadline,12)
        retain(directory/'start-intent.json',dict(intent=intent,command=plan['command'],at=time.time(),automatic_retry=False))
        runway(deadline,12)
        with (directory/'coordinator.log').open('xb') as log:
            child=subprocess.Popen(plan['command'],stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,start_new_session=True)
        launched=retain(directory/'launched.json',dict(intent=intent,pid=child.pid,command=plan['command'],at=time.time(),automatic_retry=False))
        until=time.monotonic()+3
        while True:
            require(child.poll() is None,'successor exited; inspect without replay')
            with socket.socket() as sock:
                sock.settimeout(.1);opened=sock.connect_ex(('127.0.0.1',8765))==0
            if opened:break
            require(time.monotonic()<until,'successor readiness uncertain');time.sleep(.02)
        require(ledger(old['database'])==before,'ledger changed across frozen service cutover')
        new_status=client.call('status');require(new_status['model_policy']['revision']==0,'successor initial policy differs')
        admin=module.Client(old['url'],private/'admin.token',timeout=3)
        retain(directory/'policy-intent.json',dict(authorization=plan['authorization'],change_id=CHANGE_ID,expected_revision=0,at=time.time(),automatic_retry=False))
        runway(deadline,8)
        policy=admin.call('set_model_limit',model_limit=None,authorization=plan['authorization'],change_id=CHANGE_ID,expected_revision=0)
        require(policy['model_limit'] is None and policy['publication_limit']==1 and policy['revision']==1,'policy update uncertain')
        require(ledger(old['database'])==before,'policy migration changed retained claims/operations')
        ready=retain(directory/'ready.json',dict(status='COORDINATOR_POLICY_ACTIVE',launched=launched,policy=policy,at=time.time(),before=before,after=ledger(old['database']),automatic_retry=False))
        subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(child.pid)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return ready
    finally:
        for fd in locks:os.close(fd)


def author_state(status,drain_rows,maintenance_binding,now,*,existing_policy=None):
    """Every mutator is stopped or selected for a bounded watchdog pause."""
    require(status['schema_version']=='fleet-coordinator/v1'and status.get('author_capacity_policy')==existing_policy,
            'author capacity already selected or unknown coordinator')
    require(status['publication_limit']==1 and not any(o['state']=='active'for o in status['operations']),
            'active model/publication operation cannot be paused for this cutover')
    require(len(drain_rows)==8,'all eight native admission censuses required')
    actual={};hosts=set()
    for row in drain_rows:
        value=checked(row)
        require(value['schema_version']=='fleet-admission-drain-census/v1'and value['status']=='ADMISSIONS_DRAINED'
                and value['helper']==maintenance_binding and value['model_calls']==value['processes_signalled']==0
                and finite(value['at'])and 0<=now-value['at']<=60,'stale or foreign admission drain census')
        plan=checked(value['plan'])
        require(plan['helper']==maintenance_binding and plan['host']==value['host']and value['host']not in hosts,
                'admission drain host/producer differs');hosts.add(value['host'])
        selected={w['workbook']:w for w in plan['active_claims']}
        require(len(selected)==len(plan['active_claims'])and len(value['workers'])==len(selected),
                'admission drain worker coverage differs')
        for worker in value['workers']:
            claim=selected[worker['workbook']]
            require(worker['owner']==claim['owner']and worker['workbook']not in actual,
                    'duplicate or foreign drained worker')
            actual[worker['workbook']]=claim
    require(hosts=={'hhu-server',*('hhu-server-'+str(i)for i in range(2,9))},'missing fleet host census')
    expected={w['workbook']:w for w in status['workbooks']if w['state']=='active'}
    require(set(actual)==set(expected),'new or missing active claim after admission drain')
    for wb,row in expected.items():
        require(all(actual[wb][k]==row[k]for k in('workbook','owner','fence','claim_id','source_sha256','version')),
                'active claim changed after admission drain')
    return hashlib.sha256(json.dumps(status['workbooks'],sort_keys=True,separators=(',',':')).encode()).hexdigest()


def author_selection(row):
    p=checked(row);preserve=p.get('schema_version')=='fleet-preserved-author-policy-upgrade/v1'
    require(set(p)-({'existing_author_policy'}if preserve else set())=={'schema_version','driver','old_receipt','successor','authorization','evidence','change_id',
                     'original_process_identity','cohort','maintenance_helper','drain_proofs'}
            and p['schema_version']in('fleet-author-capacity-upgrade/v1','fleet-preserved-author-policy-upgrade/v1')
            and p['driver']==binding(Path(__file__).absolute()),'closed author capacity upgrade required')
    require(p['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
            and binding(p['authorization']['path'])==p['authorization'],'explicit operational authorization differs')
    for key in('successor','maintenance_helper'):require(binding(p[key]['path'])==p[key],'selected upgrade implementation differs')
    checked(p['evidence']);checked(p['cohort'])
    old=checked(p['old_receipt'])
    require(old['url']=='http://127.0.0.1:8765'and binding(old['command'][2])['sha256']==old['code_sha256']
            and old['command'][3]=='serve'and argument(old['command'],'--database')==old['database']
            and argument(old['command'],'--port')=='8765'and old['legacy_locks'], 'original coordinator identity differs')
    require(identity(old['pid'])==p['original_process_identity'],'original coordinator process changed')
    spec=importlib.util.spec_from_file_location('selected_author_capacity_coordinator',p['successor']['path'])
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    require(callable(getattr(m.Coordinator,'set_author_capacity',None)),'successor lacks atomic author capacity')
    if preserve:
        policy=checked(p['existing_author_policy']);require(policy['schema_version']=='fleet-author-capacity/v1'and policy['authors_per_host']==policy['physical_models_per_host']==2,'original two-slot policy required')
        require(callable(getattr(m.Coordinator,'permit_physical_capacity_extension',None)),'successor lacks bounded physical policy primitive')
    return p,old,m


def preserved_policy(before, after):
    for name in ('author_capacity_policy', 'author_reservations', 'workbooks', 'operations',
                 'model_limit', 'publication_limit', 'model_policy', 'quarantine_policy',
                 'quarantines', 'quarantine_host_capacity', 'physical_capacity_extensions'):
        require(after[name] == before[name], 'code-only upgrade changed retained ' + name)


def author_execute(plan_row,proof_rows,journal):
    p,old,m=author_selection(plan_row);directory=Path(journal);preserve=p['schema_version']=='fleet-preserved-author-policy-upgrade/v1'
    state_options=dict(existing_policy=checked(p['existing_author_policy']))if preserve else {}
    require(directory.is_absolute()and not directory.exists(),'fresh once-only author upgrade journal required')
    locks=[]
    try:
        for name in('coordinator-recovery.lock','job-launch.lock'):
            fd=os.open(Path(old['database']).parent/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        private=Path(old['private_directory']);client=m.Client(old['url'],private/'worker.token',timeout=3)
        status=client.call('status');expected=author_state(status,p['drain_proofs'],p['maintenance_helper'],time.time(),**state_options)
        active=[w for w in status['workbooks']if w['state']=='active']
        require(all(w['expires']-time.time()>60 for w in active),'active maintenance claims lack runway')
        if active:
            deadline=frozen_proofs(proof_rows,active,time.time(),cohort_binding=p['cohort'],maintenance_binding=p['maintenance_helper'])
        else:
            require(proof_rows==[],'empty fleet cannot claim a worker freeze')
            deadline=time.time()+45
        before=policy_ledger(old['database'],extended=preserve);directory.mkdir(mode=0o700)
        backup=private/('before-author-capacity-'+hashlib.sha256(str(directory).encode()).hexdigest()[:16]+'.sqlite3')
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as src,sqlite3.connect(backup)as dst:src.backup(dst)
        require(policy_ledger(backup,extended=preserve)==before,'private author capacity backup differs')
        intent=retain(directory/'intent.json',dict(plan=plan_row,frozen_proofs=proof_rows,before=before,backup=binding(backup),automatic_retry=False))
        status=client.call('status')
        require(author_state(status,p['drain_proofs'],p['maintenance_helper'],time.time(),**state_options)==expected
                and policy_ledger(old['database'],extended=preserve)==before,'ledger changed before author capacity cutover')
        connections=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(old['pid']),'-iTCP','-sTCP:ESTABLISHED','-F','n'],capture_output=True,timeout=3)
        require(connections.returncode==1 and not connections.stdout and identity(old['pid'])==p['original_process_identity'],
                'old authority still serving or process changed')
        runway(deadline,18);retain(directory/'stop-intent.json',dict(pid=old['pid'],identity=p['original_process_identity'],automatic_retry=False))
        os.kill(old['pid'],signal.SIGTERM);until=time.monotonic()+3
        while not original_absent(old['pid']):
            require(time.monotonic()<until,'old coordinator stop uncertain');time.sleep(.02)
        with socket.socket()as sock:
            sock.settimeout(.1);require(sock.connect_ex(('127.0.0.1',8765))!=0,'old coordinator listener remains')
        runway(deadline,12);command=list(old['command']);command[2]=p['successor']['path']
        retain(directory/'start-intent.json',dict(intent=intent,command=command,automatic_retry=False))
        with (directory/'coordinator.log').open('xb')as log:
            child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,start_new_session=True)
        launched=retain(directory/'launched.json',dict(pid=child.pid,command=command,intent=intent,automatic_retry=False))
        until=time.monotonic()+3
        while True:
            require(child.poll()is None,'author-capacity successor exited')
            try:after=client.call('status');break
            except Exception:
                require(time.monotonic()<until,'successor readiness uncertain');time.sleep(.03)
        require(policy_ledger(old['database'],extended=preserve)==before and author_state(after,p['drain_proofs'],p['maintenance_helper'],time.time(),**state_options)==expected,
                'old claims, operations or policy changed across restart')
        if preserve:
            final=client.call('status');require(final['author_capacity_policy']==state_options['existing_policy'] and final['workbooks']==after['workbooks'] and final['operations']==after['operations'] and final['author_reservations']==after['author_reservations'],'preserved authority changed across upgrade')
            preserved_policy(status, final)
            require(policy_ledger(old['database'],extended=True)==before,'code-only upgrade changed policy or ledger')
            ready=retain(directory/'ready.json',dict(status='COORDINATOR_POLICY_EXTENSION_READY',launched=launched,before=before,after=before,at=time.time(),automatic_retry=False,policy_changes=0,maintenance_mode='watchdog-paused-authors'if active else'quiescent-admission-drain'))
        else:
            arguments=dict(authors_per_host=2,authorization=p['authorization'],evidence=p['evidence'],change_id=p['change_id'],expected_workbooks_sha256=expected)
            runway(deadline,6);retain(directory/'policy-intent.json',dict(arguments=arguments,automatic_retry=False))
            policy=m.Client(old['url'],private/'admin.token',timeout=3).call('set_author_capacity',**arguments)
            final=client.call('status')
            require(final['author_capacity_policy']==policy and policy['authors_per_host']==2
                    and final['workbooks']==after['workbooks']and final['operations']==after['operations'],
                    'author activation changed original work or returned another policy')
            require(all(final[k]==after[k]for k in ('model_limit','publication_limit','model_policy','quarantine_policy','quarantines','quarantine_host_capacity')),
                    'author activation changed model or quarantine policy')
            require(policy_ledger(old['database'],prefix=before,extended=preserve)==before
                    and policy_ledger(old['database'],extended=preserve)['events']['count']==before['events']['count']+1,
                    'author activation rewrote historical policy or added unrelated events')
            require(len(final['author_reservations'])==len(active),'active claims not all reserved')
            ready=retain(directory/'ready.json',dict(status='AUTHOR_CAPACITY_ACTIVE',launched=launched,policy=policy,
                        before=before,after=policy_ledger(old['database'],extended=preserve),at=time.time(),automatic_retry=False,
                        maintenance_mode='watchdog-paused-authors'if active else'quiescent-admission-drain'))
        retain(directory/'coordinator-process.json',{**old,'pid':child.pid,'command':command,'code_sha256':p['successor']['sha256'],
                                                   'predecessor':p['old_receipt'],'upgrade':ready})
        subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(child.pid)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return ready
    finally:
        for fd in locks:os.close(fd)


def quiescent_effect(status,arguments):
    """Authenticate exact stopped claims and the sole unchanged unknown effect."""
    require(status.get('schema_version')=='fleet-coordinator/v1','unknown current authority')
    require(not any(w['state']=='active' for w in status['workbooks'])
            and not any(o['state']=='active' for o in status['operations']),
            'active claims or operations must drain before quiescent upgrade')
    holds=[o for o in status['operations'] if o['state']=='hold']
    require(len(holds)==1 and holds[0]['kind']=='model' and holds[0]['operation_id']==arguments['operation_id'],
            'exact sole retained unknown model operation required')
    op=holds[0]
    require(all(op[k]==arguments[k] for k in ('workbook','owner','fence')) and hashlib.sha256(json.dumps(op,sort_keys=True,separators=(',',':')).encode()).hexdigest()==arguments['operation_sha256'],
            'held operation bytes differ')
    rows=[w for w in status['workbooks'] if w['workbook']==arguments['workbook']]
    require(len(rows)==1 and rows[0]['state']=='hold' and all(rows[0][k]==arguments[k] for k in ('owner','fence','source_sha256','version')),
            'exact uncertain workbook custody differs')
    require(status['model_limit']is None and status['publication_limit']==1
            and status['model_policy']['unknown_model_hold_barrier']=='global','original default policy changed')
    return status['model_policy']


def quiescent(status,arguments):
    result=quiescent_effect(status,arguments)
    require(status.get('quarantines',[])==[],'prior quarantine changed')
    return result


def host_quiescent(status,arguments):
    require(not any(w['state']=='active'for w in status['workbooks']) and not any(o['state']=='active'for o in status['operations']),
            'productive claims and operations must drain before host exception upgrade')
    rows=status.get('quarantines',[])
    require(len(rows)==1 and rows[0]['change_id']==arguments['quarantine_change_id'] and rows[0]['host']==arguments['host'],
            'exact existing quarantine required')
    q=rows[0]
    require(hashlib.sha256(json.dumps(q,sort_keys=True,separators=(',',':')).encode()).hexdigest()==arguments['quarantine_sha256']
            and not status.get('quarantine_host_capacity',[]) and arguments['physical_model_limit']==2
            and type(arguments['physical_model_limit'])is int and status['at']<arguments['expires_at']<=q['expires_at'],
            'host capacity identity, duration or prior selection differs')
    original={k:q[k]for k in ('operation_id','operation_sha256','workbook','owner','fence','source_sha256','version')}
    return quiescent_effect(status,original)


def policy_ledger(database,prefix=None,*,extended=False):
    values=ledger(database)
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)as db:
        tables={x[0]for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'" if not extended else "SELECT name FROM sqlite_master WHERE type IN('table','view')")}
        for name in ('model_policy','model_policy_changes','model_quarantines','quarantine_host_capacity','events')+(('model_quarantine_originals','model_quarantine_renewals','author_capacity_policy','author_reservations','physical_capacity_extensions')if extended else()):
            rows=db.execute('SELECT * FROM '+name+' ORDER BY 1').fetchall()if name in tables else []
            if prefix is not None:rows=rows[:prefix[name]['count']]
            values[name]=dict(count=len(rows),sha256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())
    return values


def host_policy_transition(database,before):
    after=policy_ledger(database)
    require(policy_ledger(database,prefix=before)==before,'existing ledger or policy history was rewritten')
    for name,row in before.items():
        if name not in ('quarantine_host_capacity','events'):require(after[name]==row,'unrelated ledger table changed')
    require(after['quarantine_host_capacity']['count']==before['quarantine_host_capacity']['count']+1
            and after['events']['count']==before['events']['count']+1,'host authority was not appended exactly once')
    return after


def quarantine_selection(plan_row):
    p=checked(plan_row)
    require(set(p)=={'schema_version','old_receipt','successor','authorization','declaration',
            'arguments','original_process_identity','driver','native_stopped_proof'},'closed quiescent upgrade profile required')
    require(p['schema_version']in{'fleet-quiescent-quarantine-upgrade/v1','fleet-quiescent-host-capacity-upgrade/v1'} and p['driver']==binding(Path(__file__).absolute()),'quiescent upgrade implementation changed')
    host_exception=p['schema_version']=='fleet-quiescent-host-capacity-upgrade/v1'
    old=checked(p['old_receipt']);require(old['url']=='http://127.0.0.1:8765' and binding(old['command'][2])['sha256']==old['code_sha256'],
        'original service/code binding differs')
    require(old['command'][3]=='serve' and argument(old['command'],'--database')==old['database']
        and argument(old['command'],'--port')=='8765' and old['legacy_locks'], 'original database/listener/locks differ')
    require(binding(p['successor']['path'])==p['successor'],'selected coordinator code changed')
    require(binding(p['authorization']['path'])==p['authorization'] and p['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0','explicit user operational authorization required')
    declaration=checked(p['declaration']);a=p['arguments']
    expected=dict(schema_version='fleet-scoped-model-quarantine-authorization/v1',authorization=p['authorization'],
        arguments={k:v for k,v in a.items() if k!='authorization'},provider_completion='unknown',model_replay_allowed=False,
        workbook_and_host_remain_blocked=True,original_effect_remains_consumed=True)
    if host_exception:
        expected.pop('workbook_and_host_remain_blocked');expected.update(schema_version='fleet-scoped-quarantine-host-capacity-authorization/v1',
            workbook_remains_blocked=True,uncertain_effect_occupies_one_of_two_host_slots=True)
    require(declaration==expected and a['authorization']==p['declaration'],'scoped declaration differs from selected operation')
    stopped=checked(p['native_stopped_proof'])
    require(stopped['status']=='ALL_EIGHT_NATIVE_HTTP_MUTATORS_STOPPED' and stopped['active_processes']==[]
        and stopped['model_calls']==0 and set(stopped['hosts'])=={'hhu-server',*('hhu-server-'+str(i) for i in range(2,9))} and len(stopped['hosts'])==8
        and 0<=time.time()-stopped['at']<=90,'fresh all-eight stopped observation required')
    require(len(stopped.get('probes',[]))==8,'all native stopped observations required')
    for host,proof_row in zip(stopped['hosts'],stopped['probes']):
        observation=checked(proof_row)
        require(Path(proof_row['path']).name==host+'.json' and observation['active_processes']==[]
                and observation['model_calls']==observation['processes_signalled']==0
                and 0<=time.time()-observation['at']<=90,'native stopped observation changed or stale')
    require(identity(old['pid'])==p['original_process_identity'],'original process identity changed')
    spec=importlib.util.spec_from_file_location('selected_quiescent_coordinator',p['successor']['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    require(callable(getattr(m.Coordinator,'permit_quarantine_host_capacity'if host_exception else 'quarantine_model',None)),'selected successor lacks declared admin action')
    client=m.Client(old['url'],Path(old['private_directory'])/'worker.token',timeout=3)
    (host_quiescent if host_exception else quiescent)(client.call('status'),a)
    return p,old,m


def quarantine_execute(plan_row,journal):
    p,old,m=quarantine_selection(plan_row)
    host_exception=p['schema_version']=='fleet-quiescent-host-capacity-upgrade/v1'
    observe=host_quiescent if host_exception else quiescent
    snapshot=policy_ledger if host_exception else ledger
    directory=Path(journal);require(directory.is_absolute() and not directory.exists(),'fresh quiescent once-only journal required')
    locks=[]
    try:
        for name in ('coordinator-recovery.lock','job-launch.lock'):
            fd=os.open(Path(p['old_receipt']['path']).parent/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p,old,m=quarantine_selection(plan_row)
        client=m.Client(old['url'],Path(old['private_directory'])/'worker.token',timeout=3)
        model_policy=observe(client.call('status'),p['arguments'])
        directory.mkdir(mode=0o700);before=snapshot(old['database'])
        backup=Path(old['private_directory'])/('before-quarantine-'+hashlib.sha256(str(directory).encode()).hexdigest()[:16]+'.sqlite3');fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as source,sqlite3.connect(backup)as target:source.backup(target)
        require(snapshot(backup)==before,'private backup differs')
        intent=retain(directory/'intent.json',dict(plan=plan_row,before=before,backup=binding(backup),model_policy=model_policy,at=time.time(),automatic_retry=False))
        observe(client.call('status'),p['arguments'])
        require(snapshot(old['database'])==before and identity(old['pid'])==p['original_process_identity'],'authority changed before cutover')
        connections=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(old['pid']),'-iTCP','-sTCP:ESTABLISHED','-F','n'],capture_output=True,timeout=3)
        require(connections.returncode==1 and not connections.stdout,'original authority has an accepted HTTP request')
        retain(directory/'stop-intent.json',dict(pid=old['pid'],identity=p['original_process_identity'],automatic_retry=False))
        os.kill(old['pid'],signal.SIGTERM)
        until=time.monotonic()+5
        while not original_absent(old['pid']):
            require(time.monotonic()<until,'original stop uncertain; never start another authority');time.sleep(.02)
        with socket.socket()as sock:
            sock.settimeout(.1);require(sock.connect_ex(('127.0.0.1',8765))!=0,'listener remains after process stop')
        command=list(old['command']);command[2]=p['successor']['path']
        retain(directory/'start-intent.json',dict(intent=intent,command=command,automatic_retry=False))
        with (directory/'coordinator.log').open('xb')as log:
            child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=Path(old['command'][0]).absolute().parents[2],start_new_session=True)
        launched=retain(directory/'launched.json',dict(pid=child.pid,command=command,intent=intent,automatic_retry=False))
        until=time.monotonic()+5
        while True:
            require(child.poll()is None,'successor exited; inspect without restart')
            try:status=client.call('status');break
            except Exception:
                require(time.monotonic()<until,'successor status unknown; no automatic restart');time.sleep(.05)
        require(snapshot(old['database'])==before and observe(status,p['arguments'])==model_policy,'retained ledger or default policy changed')
        retain(directory/'quarantine-intent.json',dict(arguments=p['arguments'],automatic_retry=False))
        admin=m.Client(old['url'],Path(old['private_directory'])/'admin.token',timeout=3)
        result=admin.call('permit_quarantine_host_capacity'if host_exception else 'quarantine_model',**p['arguments'])
        if host_exception:host_policy_transition(old['database'],before)
        else:require(snapshot(old['database'])==before,'quarantine changed retained claims/effects')
        status=client.call('status')
        require(status['model_policy']==model_policy,'default model policy changed')
        if host_exception:require(status['quarantine_host_capacity']==[result],'selected host capacity result differs')
        else:require(status['quarantines']==[result],'selected quarantine result differs')
        ready=retain(directory/'ready.json',dict(status='QUARANTINE_HOST_CAPACITY_ACTIVE'if host_exception else 'QUARANTINE_POLICY_ACTIVE',launched=launched,quarantine=result,
            before=before,after=snapshot(old['database']),at=time.time(),automatic_retry=False))
        receipt={**old,'pid':child.pid,'command':command,'code_sha256':p['successor']['sha256'],
                 'predecessor':p['old_receipt'],'upgrade':ready}
        retain(directory/'coordinator-process.json',receipt)
        subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(child.pid)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return ready
    finally:
        for fd in locks:os.close(fd)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','quarantine-preflight','quarantine-execute']);p.add_argument('--plan');p.add_argument('--sha256');p.add_argument('--proofs');p.add_argument('--journal');a=p.parse_args()
    if a.action=='prepare':result=prepare()
    elif a.action=='quarantine-preflight':
        p,old,m=quarantine_selection(dict(path=a.plan,sha256=a.sha256));result=dict(status='QUIESCENT_QUARANTINE_VALIDATED_NOT_STARTED',plan=dict(path=a.plan,sha256=a.sha256))
    elif a.action=='quarantine-execute':result=quarantine_execute(dict(path=a.plan,sha256=a.sha256),a.journal)
    else:result=execute(dict(path=a.plan,sha256=a.sha256),json.loads(Path(a.proofs).read_bytes()),a.journal)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
