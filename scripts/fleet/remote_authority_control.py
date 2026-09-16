"""Root connection/mutex reader and native dispatch for a migrated authority.

Root API users retain their original loopback URL and local token directory.
SQLite maintenance runs on the hub through a selected original control; a
retired local path is never replaced with a writable local database copy.
"""
from pathlib import Path
import importlib.util,json,os,subprocess,sys
from scripts.fleet.remote_coordinator_preparation import binding,checked,require
from scripts.fleet.remote_authority_fence import validate_fence

SCHEMA='fleet-remote-authority-connection/v1'
ROLES={'quarantine':'quarantine_renewal_control.py','quarantine_host':'quarantine_host_renewal_control.py','physical_capacity':'physical_capacity_renewal_control.py','quarantine_physical':'quarantine_physical_renewal_control.py','stopped_author':'stopped_author_control.py'}

def connection(receipt):
    selected=receipt.get('remote_authority');require(type(selected)is dict,'explicit remote authority connection required')
    require(set(selected)=={'schema_version','original_receipt','migration','native_receipt','hub','remote_runner'}and selected['schema_version']==SCHEMA,'closed remote authority connection required')
    old=checked(selected['original_receipt']);migration=checked(selected['migration']);native=checked(selected['native_receipt'])
    require(migration['status']=='REMOTE_AUTHORITY_ACTIVE_VERIFIED'and migration['original_receipt']==selected['original_receipt']and migration['native_receipt']==selected['native_receipt']and migration['hub']==selected['hub'],'exact completed migration required')
    fence=validate_fence(old['database'])
    require(migration['local_fence']==binding(Path(old['database'])/'complete.json')and migration['ledger']==fence['ledger'],'foreign retired authority')
    require(receipt==dict(old,remote_authority=selected),'root connection must preserve original URL/token/lock identity')
    require(native['url']==old['url']=='http://127.0.0.1:8765'and native['database']!=old['database']and native['code_sha256']==old['code_sha256'],'single unchanged remote loopback authority required')
    require(binding(selected['remote_runner']['path'])==selected['remote_runner'],'remote runner changed')
    return selected,old,native,migration

def path(receipt):
    _,old,_,_=connection(receipt)
    return Path(old['database']).parent/'job-launch.lock'

def authority_command(command,server,database):
    """Decode actual Python argv or the exact systemd singleton exec wrapper."""
    require(type(command)is list and all(type(x)is str for x in command),'actual authority argv required')
    argv=list(command)
    if argv and argv[0]=='/usr/bin/flock':
        require(argv[1:4]==['--exclusive','--nonblock','--no-fork']and len(argv)>5 and argv[4]==str(Path(database).parent/'authority.lock'),'foreign singleton wrapper')
        argv=argv[5:]
    require(argv and Path(argv[0]).is_absolute()and argv.count(server)==1,'single selected Python authority required')
    position=argv.index(server)
    require(argv[1:position]in (['-B'],['-I','-B'])and argv[position+1:position+2]==['serve'],'foreign authority executable mode')
    tail=argv[position+2:]
    require(len(tail)==8 and tail[::2]==['--database','--port','--worker-token-file','--admin-token-file']and tail[1]==database and tail[3]=='8765','closed original authority service arguments required')
    require(tail[5]==str(Path(database).parent/'private/worker.token')and tail[7]==str(Path(database).parent/'private/admin.token'),'native token custody differs')
    return argv

def native_run(selection):
    """Called on the hub. Original control still validates policy and executes CAS.

    The root caller holds its canonical admission mutex while this call runs;
    the original control also acquires the native database's owning locks.
    """
    p=checked(selection)
    require(set(p)=={'schema_version','role','controller','plan','native_receipt','journal','execute','dependencies'}and p['schema_version']=='fleet-remote-admin-invocation/v1','closed remote maintenance invocation required')
    require(p['role']in ROLES and Path(p['controller']['path']).name==ROLES[p['role']],'selected original maintenance controller required')
    require(type(p['execute'])is bool and type(p['dependencies'])is list,'explicit owning mode and dependency closure required')
    for row in [p['controller'],*p['dependencies']]:require(binding(row['path'])==row,'remote maintenance dependency changed')
    native=checked(p['native_receipt']);plan=checked(p['plan'])
    require(plan.get('coordinator')==p['native_receipt'],'maintenance must bind actual native authority')
    server=str(Path(native['database']).parent/'coordinator.py');authority_command(native['command'],server,native['database'])
    require(binding(server)['sha256']==native['code_sha256'],'native authority code changed')
    require(Path(native['database']).is_file()and not Path(native['database']).is_symlink(),'actual native SQLite authority required')
    observed=subprocess.run(['/bin/ps','-p',str(native['pid']),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=5)
    require(observed.returncode==0 and observed.stdout.strip()==plan['process_identity']and server in observed.stdout,'native authority process identity differs')
    journal=Path(p['journal']);require(journal.is_absolute()and not journal.exists()and not any(x.is_symlink()for x in journal.parents),'fresh native maintenance journal required')
    name='scripts.fleet.selected_remote_'+p['role'];spec=importlib.util.spec_from_file_location(name,p['controller']['path']);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module.run(p['plan'],p['journal'],execute=p['execute'])


def remote_argv(native,selected,invocation,runtime):
    """Authenticated IAP only for root administration; native workers need none."""
    import shlex
    require(type(runtime)is dict and set(runtime)=={'package_root','dependencies'}and type(runtime['dependencies'])is list,'closed remote root-admin runtime required')
    require(type(runtime['package_root'])is str and Path(runtime['package_root']).is_absolute(),'selected remote package root required')
    helper=selected['remote_runner'];require(helper in runtime['dependencies'],'remote dispatcher missing from runtime closure')
    for row in runtime['dependencies']:require(binding(row['path'])==row,'selected remote administration code changed')
    # Verify the complete selected package before importing any of its modules.
    payload=dict(invocation=invocation,helper=helper,runtime=runtime)
    code="""import sys,json,hashlib,importlib.util
from pathlib import Path
p=json.load(sys.stdin)
for r in p['runtime']['dependencies']:
 q=Path(r['path']);assert q.is_absolute()and not any(x.is_symlink()for x in(q,*q.parents))and hashlib.sha256(q.read_bytes()).hexdigest()==r['sha256']
sys.path.insert(0,p['runtime']['package_root'])
s=importlib.util.spec_from_file_location('scripts.fleet.selected_remote_authority_dispatch',p['helper']['path']);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
print(json.dumps(m.native_run(p['invocation'])))
"""
    interpreter=native['command'][0]
    require(Path(interpreter).is_absolute()and interpreter!='/usr/bin/flock','native Python process receipt required')
    argv=['gcloud','compute','ssh',selected['hub'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([interpreter,'-I','-B','-c',code])]
    return argv,json.dumps(payload).encode()


def dispatch(receipt,invocation,runtime,directory):
    """Own the original root mutex and preserve an uncertain remote result once."""
    import fcntl
    from scripts.fleet.remote_authority_fence import retain
    selected,old,native,_=connection(receipt);call=checked(invocation)
    require(call['native_receipt']==selected['native_receipt'],'remote operation targets a foreign migrated authority')
    argv,payload=remote_argv(native,selected,invocation,runtime)
    home=Path(directory);require(home.is_absolute()and not home.exists()and not any(x.is_symlink()for x in home.parents),'fresh root remote-admin journal required')
    lockpath=path(receipt);fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);connection(receipt)
        home.mkdir(mode=0o700);intent=retain(home/'intent.json',dict(invocation=invocation,remote_authority=selected,runtime=runtime,automatic_retry=False))
        try:
            result=subprocess.run(argv,input=payload,capture_output=True,timeout=900)
            # Structured receipts contain no bearer token material. Preserve
            # exceptions separately; no automatic repeat after an uncertain RPC.
            stdout=home/'stdout';stderr=home/'stderr'
            for target,raw in ((stdout,result.stdout),(stderr,result.stderr)):
                with target.open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
                target.chmod(0o600)
            require(result.returncode==0,'remote maintenance returned nonzero; inspect retained outcome without replay')
            value=json.loads(result.stdout)
            return retain(home/'complete.json',dict(status='REMOTE_ORIGINAL_MAINTENANCE_COMPLETE',intent=intent,result=value,stdout=binding(stdout),stderr=binding(stderr),automatic_retry=False))
        except BaseException as exc:
            retain(home/'uncertain.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',exception_type=type(exc).__name__,automatic_retry=False));raise
    finally:os.close(fd)
