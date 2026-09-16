"""Root-bound, once-only launch from an explicitly reconciled fleet hold.

Default is read-only. A failed or lost response retains intent and never retries.
The root launch lock serializes this helper; coordinator claims remain atomic.
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import shlex
import subprocess
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding, checked, safe
from scripts.fleet.pool import persist, lock
from scripts.fleet.coordinator import Client
from scripts.fleet.prepare_queue import HOSTS

SCHEMA='harbor-fleet-job-launch/v1'
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')


def require(ok,message):
    if not ok: raise ValueError(message)


def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','host','job','root_plan','probe','prior_outcome','prior_process',
        'expected_fence','expected_owner','expected_version','new_owner','remote_receipt','coordinator_receipt','worker','prior_process_copy'},
        'closed root launch plan required')
    require(p['schema_version']==SCHEMA and p['host'] in HOSTS,'exact authorized VM required')
    job=checked(p['job']);root=checked(p['root_plan']);probe=checked(p['probe'])
    prior=checked(p['prior_outcome']);process=checked(p['prior_process_copy'])
    require(p['prior_process_copy']['sha256']==p['prior_process']['sha256'],'remote process receipt differs from retained copy')
    require(job['inputs'] and job['inputs'][0]==p['root_plan'],'job first input must be root-selected plan')
    require(root['workbook']==job['workbook'],'root plan workbook differs')
    require('--plan' in job['command'] and job['command'][job['command'].index('--plan')+1]==p['root_plan']['path']
        and '--plan-sha256' in job['command'] and job['command'][job['command'].index('--plan-sha256')+1]==p['root_plan']['sha256'],
        'command does not select exact root plan')
    require(probe.get('status')=='PASS' and probe.get('models_dispatched')==0
        and probe.get('result',{}).get('plan')==p['root_plan'],'root-selected successful no-model probe required')
    require(prior.get('schema_version')=='harbor-fleet-worker-outcome/v1'
        and prior.get('status')=='RECONCILIATION_REQUIRED' and prior.get('automatic_retry') is False,
        'prior retained worker hold required')
    prior_intent=checked(prior['intent']);old_job=checked(prior_intent['job'])
    require(old_job['source']==job['source'] and old_job['workbook']==job['workbook']
        and old_job['version']==p['expected_version'] and prior_intent['owner']==p['expected_owner'],
        'prior source, version or owner differs')
    require(type(p['expected_fence']) is int and p['expected_fence']>0,'positive previous fence required')
    require(type(process.get('pid')) is int and process['pid']>0 and prior_intent['job']['path'] in process['command'],
        'prior worker process does not select its retained job')
    require(isinstance(p['new_owner'],str) and p['new_owner'].startswith(p['host']+':') and len(p['new_owner'])<256,
        'explicit host-bound new owner required')
    require(job['command'][0]==str(Path(job['cwd']).parent/'venv/bin/python'),'job isolated interpreter required')
    require(p['worker']['path']==str(Path(job['cwd'])/'scripts/fleet/worker.py'),'selected runtime worker required')
    # /home is a macOS system symlink, but these are Linux destination paths.
    # Verify their filesystem ancestry on the VM, not on the root's Mac.
    receipt=Path(p['remote_receipt']);require(receipt.is_absolute() and '..' not in receipt.parts
        and receipt.is_relative_to(REMOTE),'isolated remote process receipt required')
    for r in job['inputs']: checked(r)
    return p,job


def central_gate(status,p,job):
    require(status.get('model_limit')==status.get('publication_limit')==1,'global limits must remain one')
    require(not any(x['state'] in {'active','hold'} for x in status['operations']),'global operation needs reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']]
    require(len(rows)==1,'exact workbook ownership required');w=rows[0]
    require(w['state']=='hold' and w['fence']==p['expected_fence'] and w['owner']==p['expected_owner']
        and w['source_sha256']==job['source']['sha256'] and w['version']==p['expected_version'],
        'held source, fence, owner or version changed')
    require(json.loads(w['reason'])==p['prior_outcome'],'hold reason differs from prior worker outcome')
    return w


def remote_preflight(payload):
    import json,os,sys,shutil
    from pathlib import Path
    p=payload['plan'];job=payload['job'];sys.path.insert(0,job['cwd'])
    from scripts.fleet.migration import checked,binding,safe
    from scripts.fleet.worker import validate
    if checked(p['job'])!=job: raise ValueError('remote job changed')
    validate(job)
    if binding(p['worker']['path'])!=p['worker']:raise ValueError('runtime worker changed')
    root=checked(p['root_plan'])
    if root.get('tracker') and binding(root['tracker']['path'])!=root['tracker']:raise ValueError('native tracker changed')
    if shutil.disk_usage(job['cwd']).free<job['minimum_disk_bytes']:raise ValueError('disk reserve unavailable')
    receipt=safe(p['remote_receipt'])
    for path in [safe(job['run']),safe(job['result']).parent,receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]:
        if path.exists():raise ValueError('new launch path already consumed; reconcile')
    old=checked(p['prior_outcome']);intent=checked(old['intent']);prior_job=checked(intent['job']);process=checked(p['prior_process'])
    needles=[intent['job']['path'],prior_job['run'],prior_job['result'],p['job']['path'],job['run'],job['result']]
    active=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            args=(entry/'cmdline').read_bytes().split(b'\0')
            command=' '.join(x.decode(errors='replace') for x in args)
            if int(entry.name)==process['pid'] or any(n in command for n in needles):active.append(int(entry.name))
        except (FileNotFoundError,ProcessLookupError):continue
    if active:raise ValueError('prior or new worker process still active; reconcile')
    return {'status':'VALIDATED_NOT_STARTED','job':p['job'],'root_plan':p['root_plan'],'worker':p['worker'],
        'prior_outcome':p['prior_outcome'],'prior_process':p['prior_process'],'active_pids':[],'models_dispatched':0}


def remote_spawn(payload):
    """Self-contained subprocess launch; same function is exercised locally."""
    import hashlib,json,os,subprocess,time
    from pathlib import Path
    receipt=Path(payload['receipt']);command=payload['command']
    if not receipt.is_absolute() or any(p.is_symlink() for p in (receipt,*receipt.parents)):
        raise ValueError('canonical receipt required')
    def keep(path,value):
        with path.open('x') as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    paths=[receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]
    if any(p.exists() for p in paths):raise ValueError('launch already attempted; reconcile')
    receipt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    intent={'command':command,'job':payload['job'],'root_intent':payload['root_intent'],'automatic_retry':False,'created_at_ns':time.time_ns()}
    keep(paths[2],intent)
    with paths[1].open('xb') as log:
        environment={**os.environ,'PATH':str(Path(command[0]).parent)+':'+os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'}
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
            start_new_session=True,cwd=payload['cwd'],env=environment)
    # A failure after Popen must preserve the launch intent and live process.
    start=None;stat=Path('/proc')/str(process.pid)/'stat'
    if stat.exists():start=stat.read_text().rsplit(')',1)[1].split()[19]
    value={'pid':process.pid,'start_ticks':start,'command':command,'job':payload['job'],
        'root_intent':payload['root_intent'],'automatic_retry':False,'created_at_ns':time.time_ns()}
    keep(receipt,value)
    return value


def function_source(name):
    # Avoid importing stdlib inspect by bare name: this directory also contains
    # the fleet's inspect.py, which shadows it when this CLI is run directly.
    source=Path(__file__).read_text();lines=source.splitlines(keepends=True)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ''.join(lines[node.lineno-1:node.end_lineno])


def ssh(p,job,function,payload):
    code=function_source('remote_preflight')+'\n'+function_source('remote_spawn')+'\n'
    code+='import json,sys\np=json.load(sys.stdin)\n'
    if function=='launch':code+="remote_preflight(p)\nprint(json.dumps(remote_spawn(p['launch'])))\n"
    else:code+='print(json.dumps(remote_preflight(p)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet',
        '--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('remote '+function+' failed; reconcile retained evidence before retry')
    return json.loads(result.stdout)


def launch(row,journal,*,execute=False):
    p,job=load(row);receipt=checked(p['coordinator_receipt'])
    client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    # The one root coordinator's directory is common to all helper invocations.
    with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
        before=central_gate(client.call('status'),p,job)
        payload={'plan':p,'job':job};remote=ssh(p,job,'check',payload)
        if not execute:return {'status':'VALIDATED_NOT_STARTED','plan':row,'remote':remote,'central':before,'claims_changed':0,'workers_started':0}
        directory=safe(Path(journal).absolute());require(not directory.exists(),'launch journal exists; reconcile without replay')
        directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',{'plan':row,'driver':binding(Path(__file__).resolve()),'remote':remote,
            'before':before,'automatic_retry':False},immutable=True)
        phase='reconcile'
        try:
            central_gate(client.call('status'),p,job)
            ready=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'continuation':p['root_plan']})
            require(ready['state']=='ready' and ready['fence']==p['expected_fence'],'unexpected ready response; reconcile')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],
                '--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            payload['launch']={'receipt':p['remote_receipt'],'command':command,'job':p['job'],'root_intent':intent,'cwd':job['cwd']}
            result=ssh(p,job,'launch',payload)
            return persist(directory/'launched.json',{'intent':intent,'remote':result,'automatic_retry':False},immutable=True)
        except Exception as exc:
            persist(directory/'uncertain.json',{'intent':intent,'phase':phase,'status':'RECONCILIATION_REQUIRED',
                'failure_type':type(exc).__name__,'automatic_retry':False},immutable=True)
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--journal');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if args.execute and not args.journal:parser.error('--execute requires fresh --journal')
    print(json.dumps(launch({'path':str(Path(args.plan).absolute()),'sha256':args.plan_sha256},args.journal,execute=args.execute),indent=2))

if __name__=='__main__':main()
