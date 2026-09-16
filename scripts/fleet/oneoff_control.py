"""Status or once-only SIGINT for a hash-bound standalone fleet worker.

Interrupt preserves root and remote intent. It does not release claims, settle
model operations, infer successful cleanup or provide an automatic resume.
"""
from __future__ import annotations
import argparse,ast,json,shlex,subprocess,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe,retain
from scripts.fleet.pool import lock,persist
from scripts.fleet.prepare_queue import HOSTS
SCHEMA='harbor-fleet-oneoff-control/v2'
LEGACY_SCHEMA='harbor-fleet-oneoff-control/v1'
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')


def require(ok,message):
    if not ok:raise ValueError(message)


def prepare(launched,output):
    record=checked(launched);intent=checked(record['intent']);plan=checked(intent['plan']);process=record['remote'];job=checked(plan['job'])
    require(process['root_intent']==record['intent'] and process['job']==plan['job'],'launch provenance differs')
    out=safe(output);require(not out.exists(),'control preparation exists');out.mkdir(parents=True,mode=0o700)
    # remote_spawn's exact JSON serialization is verified against the remote
    # process receipt before any observation or signal; it is not a new receipt.
    copy=retain(out/'process-copy.json',process)
    remote={'path':plan['remote_receipt'],'sha256':copy['sha256']}
    p=dict(schema_version=SCHEMA,host=plan['host'],workbook=job['workbook'],job=plan['job'],worker=plan['worker'],
        process=remote,process_copy=copy,launch_record=launched,owner=plan['new_owner'],
        remote_interrupt_directory=str(Path(plan['remote_receipt']).parent/'interrupts'/remote['sha256']),
        root_interrupt_directory=str(out/'interrupt-001'))
    result=retain(out/'plan.json',p);load(result);return result


def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','host','workbook','job','worker','process','process_copy','launch_record','owner','remote_interrupt_directory','root_interrupt_directory'},'closed oneoff control required')
    require(p['schema_version'] in {SCHEMA,LEGACY_SCHEMA} and p['host'] in HOSTS,'authorized host required')
    process=checked(p['process_copy']);job=checked(p['job']);launched=checked(p['launch_record'])
    require(process==launched['remote'] and process['job']==p['job'] and p['process']['sha256']==p['process_copy']['sha256'],'exact retained launch receipt required')
    intent=checked(launched['intent']);selected=checked(intent['plan'])
    require(selected['job']==p['job'] and selected['host']==p['host'] and selected['worker']==p['worker']
        and selected['remote_receipt']==p['process']['path'] and selected['new_owner']==p['owner'],'selected launch provenance differs')
    require(process['root_intent']==launched['intent'] and type(process['pid']) is int and process['pid']>1
        and str(process['start_ticks']).isdigit(),'exact PID and Linux start ticks required')
    expected=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],
        '--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['owner']]
    require(process['command']==expected and job['workbook']==p['workbook'],'exact standalone worker command required')
    expected_interrupt=(Path(p['process']['path']).parent/'interrupt-001' if p['schema_version']==LEGACY_SCHEMA
                        else Path(p['process']['path']).parent/'interrupts'/p['process']['sha256'])
    require(Path(p['remote_interrupt_directory'])==expected_interrupt
        and Path(p['process']['path']).is_relative_to(REMOTE),'fixed remote interruption namespace required')
    require(Path(p['root_interrupt_directory'])==Path(p['process_copy']['path']).parent/'interrupt-001','fixed root interruption namespace required')
    return p,job,process


def remote_control(payload):
    import hashlib,json,os,signal,time
    from pathlib import Path
    p=payload['plan'];expected=payload['process'];action=payload['action']
    def check(row):
        path=Path(row['path'])
        if not path.is_absolute() or any(x.is_symlink() for x in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('bound remote artifact differs')
        return json.loads(path.read_bytes())
    if check(p['process'])!=expected or check(p['job'])!=payload['job']:raise ValueError('process/job changed')
    path=Path(p['worker']['path'])
    if any(x.is_symlink() for x in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=p['worker']['sha256']:raise ValueError('worker code changed')
    if action not in {'status','interrupt'}:raise ValueError('unsupported process control')
    pid=expected['pid'];proc=Path('/proc')/str(pid)
    def observe():
        try:
            fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            command=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
        except (FileNotFoundError,ProcessLookupError):return dict(state='ABSENT',pid=pid)
        if fields[19]!=str(expected['start_ticks']):return dict(state='IDENTITY_MISMATCH',pid=pid,reason='start_ticks')
        if fields[0]=='Z':return dict(state='EXITED_UNREAPED',pid=pid,start_ticks=fields[19])
        if command!=expected['command']:return dict(state='IDENTITY_MISMATCH',pid=pid,reason='command')
        return dict(state='RUNNING',pid=pid,start_ticks=fields[19],command=command)
    before=observe()
    directory=Path(p['remote_interrupt_directory'])
    legacy=Path(p['process']['path']).parent/'interrupt-001'
    per_process=Path(p['process']['path']).parent/'interrupts'/p['process']['sha256']
    legacy_exists=p.get('schema_version')=='harbor-fleet-oneoff-control/v2' and legacy.exists()
    prior_exists=directory.exists() or per_process.exists() or legacy_exists
    if action=='status':return dict(process=before,interrupt_intent_exists=prior_exists,automatic_resume=False)
    if before['state']!='RUNNING':raise ValueError('recorded worker is not running with exact identity')
    if legacy_exists:raise ValueError('legacy interruption exists; reconcile before selecting another signal namespace')
    if prior_exists:raise ValueError('interruption already attempted; reconcile without another signal')
    # pidfd binds the kernel process object so PID reuse after the final /proc
    # comparison cannot redirect the signal to another process.
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):raise ValueError('pidfd-safe interruption unavailable')
    fd=os.pidfd_open(pid,0)
    try:
        if observe()!=before:raise ValueError('worker changed before interruption')
        if any(x.is_symlink() for x in (directory,*directory.parents)):raise ValueError('unsafe interruption namespace')
        directory.mkdir(parents=True,exist_ok=False,mode=0o700)
        # The per-process v2 namespace adds one parent below the existing
        # receipt directory. Persist both directory entries before signalling.
        for parent in (directory.parent,directory.parent.parent):
            handle=os.open(parent,os.O_RDONLY)
            try:os.fsync(handle)
            finally:os.close(handle)
        def keep(path,value):
            with path.open('x') as stream:
                json.dump(value,stream,indent=2,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            handle=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(handle)
            finally:os.close(handle)
        intent=dict(process=p['process'],job=p['job'],root_intent=payload['root_intent'],observed=before,signal='SIGINT',automatic_retry=False,created_at_ns=time.time_ns())
        keep(directory/'intent.json',intent)
        if observe()!=before:raise ValueError('worker changed after durable intent; reconcile')
        signal.pidfd_send_signal(fd,signal.SIGINT,None,0)
        result=dict(status='SIGINT_SENT_RECONCILIATION_REQUIRED',process=p['process'],job=p['job'],observed_after=observe(),
            worker_outcome_may_be_missing=True,cleanup_confirmed=False,automatic_resume=False,claims_released=False,model_operations_settled=False)
        keep(directory/'signal.json',result)
        return result
    finally:os.close(fd)


def function_source():
    text=Path(__file__).read_text();lines=text.splitlines(keepends=True);node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='remote_control')
    return ''.join(lines[node.lineno-1:node.end_lineno])


def ssh(p,job,process,action,root_intent=None):
    code=function_source()+'\nimport json,sys\nprint(json.dumps(remote_control(json.load(sys.stdin))))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    r=subprocess.run(command,input=json.dumps(dict(plan=p,job=job,process=process,action=action,root_intent=root_intent)).encode(),capture_output=True,timeout=120)
    if r.returncode:raise RuntimeError('remote control failed; reconcile retained intent before retry')
    return json.loads(r.stdout)


def control(row,action):
    p,job,process=load(row)
    if action=='status':return ssh(p,job,process,'status')
    require(action=='interrupt','unsupported control action')
    require(p['schema_version']==SCHEMA,'legacy controls are status-only; prepare a v2 process-specific control before interruption')
    directory=safe(p['root_interrupt_directory'])
    with lock(directory.parent/'control.lock',blocking=False):
        require(not directory.exists(),'interruption already attempted; reconcile')
        before=ssh(p,job,process,'status');require(before['process']['state']=='RUNNING' and not before['interrupt_intent_exists'],'exact running worker without prior interruption required')
        directory.mkdir(mode=0o700)
        import os
        handle=os.open(directory.parent,os.O_RDONLY)
        try:os.fsync(handle)
        finally:os.close(handle)
        intent=persist(directory/'intent.json',dict(plan=row,implementation=binding(Path(__file__).absolute()),before=before,automatic_retry=False),immutable=True)
        try:
            result=ssh(p,job,process,'interrupt',intent)
            return persist(directory/'result.json',result,immutable=True)
        except BaseException as exc:
            persist(directory/'uncertain.json',dict(status='RECONCILIATION_REQUIRED',intent=intent,failure_type=type(exc).__name__,automatic_retry=False,automatic_resume=False),immutable=True);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','status','interrupt'));p.add_argument('--plan');p.add_argument('--plan-sha256');p.add_argument('--launch-record');p.add_argument('--launch-record-sha256');p.add_argument('--output');a=p.parse_args()
    if a.action=='prepare':
        if not all((a.launch_record,a.launch_record_sha256,a.output)):p.error('prepare requires launch-record, launch-record-sha256, output')
        result=prepare(dict(path=str(Path(a.launch_record).absolute()),sha256=a.launch_record_sha256),Path(a.output).absolute())
    else:
        if not a.plan or not a.plan_sha256:p.error('status/interrupt requires bound plan')
        result=control(dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256),a.action)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
