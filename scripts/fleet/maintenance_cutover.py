"""Coordinate one bounded maintenance pause and the reviewed authority upgrade.

Native freezes are never replayed. Every selected host is explicitly thawed in
finally, with its independent watchdog as a second path. Thaw changes no claims.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from contextlib import contextmanager, nullcontext
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,shlex,sqlite3,subprocess,time

REMOTE = r"""
import importlib.util,json,hashlib,sys,time
from pathlib import Path
v=json.load(sys.stdin);row=v['plan'];p=Path(row['path'])
if hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('maintenance plan changed')
plan=json.loads(p.read_bytes());h=plan['helper'];p=Path(h['path'])
if hashlib.sha256(p.read_bytes()).hexdigest()!=h['sha256']:raise ValueError('maintenance helper changed')
spec=importlib.util.spec_from_file_location('bound_maintenance_pause',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
if v['action']=='freeze':
 frozen=m.freeze(row);value=m.checked(frozen);state=m.status(Path(plan['directory']))
 result=dict(schema_version='fleet-maintenance-observation/v1',host=v['host'],helper=h,plan=row,frozen=dict(binding=frozen,value=value),status=state,observed_at=time.time())
elif v['action']=='thaw':
 home=Path(plan['directory'])
 if home.exists():
  if m.checked(m.binding(home/'intent.json'))['plan']!=row:raise ValueError('maintenance intent changed')
  result=dict(thawed=m.thaw(home,'root-cutover-finished'),status=m.status(home))
 else:result=dict(status='NO_MAINTENANCE_INTENT')
else:raise ValueError('unsupported action')
print(json.dumps(result,sort_keys=True))
"""


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and not any(q.is_symlink() for q in (p,*p.parents)),'canonical path required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
    require(binding(row['path'])==row,'bound control changed');return json.loads(Path(row['path']).read_bytes())


def keep(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)


def remote(select,action,home):
    start=time.time();row=select['plan'];checked(row)
    command=['gcloud','compute','ssh',select['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command','python3 -I -B -c '+shlex.quote(REMOTE)]
    result=subprocess.run(command,input=json.dumps(dict(host=select['host'],plan=row,action=action)).encode(),capture_output=True,timeout=22)
    keep(home/(select['host']+'-'+action+'.stderr'),result.stderr)
    proof=keep(home/(select['host']+'-'+action+'.json'),result.stdout)
    keep(home/(select['host']+'-'+action+'-transport.json'),json.dumps(dict(started=start,ended=time.time(),returncode=result.returncode)).encode())
    require(result.returncode==0,'maintenance response failed; inspect/watchdog, never replay freeze')
    value=json.loads(result.stdout)
    if action=='freeze':
        require(value['host']==select['host'] and value['plan']==row and value['status']['status']=='FROZEN','selected maintenance freeze not confirmed')
        require(value['status']['watchdog_alive'] is True and value['status']['owner_coverage'] is True,'maintenance watchdog/coverage unconfirmed')
    return proof


@contextmanager
def no_http_barrier(database, directory):
    """Prevent a new original begin transaction between zero-HTTP proof and freeze.

    The write reservation changes no rows. It is released before the original
    upgrade's HTTP checks; a queued permit then makes that upgrade refuse and
    thaw, rather than treating a possibly submitted request as quiescent.
    """
    p=Path(database)
    require(p.is_absolute() and p.is_file() and not any(q.is_symlink() for q in (p,*p.parents)), 'canonical existing coordinator database required')
    db=sqlite3.connect('file:'+str(p)+'?mode=rw',uri=True,timeout=3,isolation_level=None)
    started=time.time()
    try:
        db.execute('BEGIN IMMEDIATE')
        active=db.execute("SELECT COUNT(*) FROM operations WHERE state='active'").fetchone()[0]
        require(active==0,'active HTTP/publication operation forbids maintenance freeze')
        keep(directory/'no-http-barrier.json',json.dumps(dict(schema_version='fleet-no-http-maintenance-barrier/v1',database=str(p),started_at=started,checked_at=time.time(),active_operations=active,method='original-sqlite-write-reservation',task_mutations=0)).encode())
        yield
    finally:
        changed=db.total_changes
        db.rollback();db.close()
        keep(directory/'no-http-barrier-released.json',json.dumps(dict(started_at=started,released_at=time.time(),database_changes=changed)).encode())
        require(changed==0,'maintenance barrier must never change authority rows')


def execute(plan_row, home):
    plan=checked(plan_row);require(set(plan)=={'schema_version','driver','upgrade_driver','upgrade_plan','hosts'},'closed maintenance cutover plan required')
    require(plan['schema_version']=='fleet-maintenance-cutover/v1' and plan['driver']==binding(Path(__file__).absolute()),'cutover implementation differs')
    hosts=plan['hosts'];require(0<len(hosts)<=8 and len({x['host'] for x in hosts})==len(hosts),'explicit unique fleet hosts required')
    for host in hosts:
        require(host['host'] in ['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)],'unauthorized VM')
        checked(host['plan'])
    module_row=plan['upgrade_driver'];require(binding(module_row['path'])==module_row,'upgrade implementation changed')
    checked(plan['upgrade_plan'])
    directory=Path(home);require(directory.is_absolute() and not directory.exists(),'fresh maintenance orchestration journal required')
    directory.mkdir(mode=0o700)
    keep(directory/'intent.json',json.dumps(dict(plan=plan_row,at=time.time(),automatic_retry=False)).encode())
    spec=importlib.util.spec_from_file_location('reviewed_coordinator_upgrade',module_row['path']);upgrade=importlib.util.module_from_spec(spec);spec.loader.exec_module(upgrade)
    proofs=[];freeze_attempted=False
    try:
        selected=checked(plan['upgrade_plan'])
        if selected.get('schema_version')=='fleet-preserved-author-policy-upgrade/v1':
            _,old,_=upgrade.author_selection(plan['upgrade_plan'])
            barrier=no_http_barrier(old['database'],directory)
        else:barrier=nullcontext()
        with barrier:
            freeze_attempted=True
            with ThreadPoolExecutor(max_workers=8) as pool:
                pending={pool.submit(remote,host,'freeze',directory):host for host in hosts}
                failures=[]
                for future in as_completed(pending):
                    try:proofs.append(future.result())
                    except BaseException as error:failures.append(type(error).__name__)
        require(not failures,'one or more freezes uncertain; no coordinator cutover')
        result=upgrade.execute(plan['upgrade_plan'],proofs,directory/'upgrade')
        keep(directory/'policy-active.json',json.dumps(result).encode())
        selected=checked(plan['upgrade_plan'])
        status={'fleet-author-capacity-upgrade/v1':'AUTHOR_CAPACITY_ACTIVE','fleet-preserved-author-policy-upgrade/v1':'COORDINATOR_POLICY_EXTENSION_READY'}.get(selected.get('schema_version'),'MODEL_CAP_REMOVED')
        print(json.dumps(dict(status=status,ready=result)),flush=True)
    finally:
        outcomes=[]
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending={pool.submit(remote,host,'thaw',directory):host for host in hosts} if freeze_attempted else {}
            for future in as_completed(pending):
                host=pending[future]['host']
                try:outcomes.append(dict(host=host,proof=future.result()))
                except BaseException as error:outcomes.append(dict(host=host,failure_type=type(error).__name__,watchdog_required=True))
        keep(directory/'thaw-summary.json',json.dumps(outcomes,indent=2).encode())
        print(json.dumps(dict(thaw_responses=outcomes)),flush=True)


def main():
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);v=a.parse_args()
    execute(dict(path=v.plan,sha256=v.sha256),v.journal)

if __name__=='__main__':main()
