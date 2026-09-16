"""Once-only first fleet custody for reviewed historical continuations.

No prior fleet worker is invented. Original tracker custody is replaced only by
its owning continuation helper after a centrally fenced claim. A lost response
retains root/remote intent and requires reconciliation, never automatic replay.
"""
from __future__ import annotations
import argparse,ast,fcntl,json,os,shlex,subprocess,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
from scripts.fleet import launch_job as L,fleet_control as F
SCHEMA='harbor-fleet-first-historical-custody/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def authority(p,job):
    a=checked(p['authority'])
    if a.get('kind')=='resource-reconstruction':
        from scripts.fleet.first_resource_authority import authority as selected
        return selected(p,job)
    if a.get('kind')=='rejected-reader-review':
        from scripts.fleet.first_rejected_review_authority import authority as selected
        return selected(p,job)
    inspection=checked(a['inspection'])
    require(set(a)=={'schema_version','kind','workbook','job','source','tracker','checkpoint','descriptor','inspection','history'},'closed historical authority required')
    require(a['schema_version']=='harbor-fleet-retained-native-authority/v1' and a['job']==p['job']
        and a['workbook']==job['workbook'] and a['source']==job['source'],'native job/source identity differs')
    tracker=checked(a['tracker']);checkpoint=checked(a['checkpoint']);checked(a['descriptor'])
    require(tracker['workbook_id']==job['workbook'] and tracker['bindings']['original_raw_source']==a['source']
        and checkpoint['descriptor']==a['descriptor'],'native tracker/checkpoint/source differs')
    require(inspection['model_calls']==0 and inspection['publication_ready'] is False,'successful non-model native probe required')
    command=job['command'];require('--output' in command and command[command.index('--output')+1]==str(Path(job['result']).parent),'exact original output namespace required')
    h=checked(a['history'])
    if a['kind']=='source-only':
        require(job['workbook']=='0006' and inspection['status']=='SOURCE_ONLY_CONTINUATION_VALIDATED'
            and inspection['source']==a['source'] and inspection['new_admission'] is False,'source-only native probe differs')
        require(a['tracker']==inspection['predecessor']['trackers/workbooks/0006.json']
            and a['checkpoint']==inspection['predecessor']['dispatcher-v2/first/000/result.json']
            and a['descriptor']==inspection['predecessor']['admission-001/descriptor.json'],'source-only predecessor differs')
        require(h['schema_version']=='fleet-source-continuation-history/v1' and h['fresh_admission_allowed'] is False
            and h['tracker']==a['tracker'] and h['checkpoint']==a['checkpoint'] and h['descriptor']==a['descriptor']
            and h['source']==a['source'] and h['central_histories'].get(job['workbook'])
            and job.get('continuation_history')==a['history'],'retained source history differs')
        require('migrate' in command and '--continuation-history' in command
            and command[command.index('--continuation-history')+1]==a['history']['path']
            and command[command.index('--continuation-history-sha256')+1]==a['history']['sha256'],'migration history command differs')
    elif a['kind']=='existing-review':
        require(job['workbook']=='0190' and inspection['status']=='EXISTING_REVIEW_VALIDATED'
            and inspection['same_review_must_not_replay'] is True and inspection['source_sha256']==a['source']['sha256']
            and inspection['tracker']==a['tracker'] and inspection['takeover_selection']['checkpoint']==a['checkpoint'],'existing-call native probe differs')
        require(h['schema_version']=='0190-fleet-existing-review-refresh-plan/v1' and h['workbook']==job['workbook']
            and h['models'] is False and h['publication'] is False and h['automatic_retry'] is False
            and h['source']==a['source'] and h['original_tracker']==a['tracker'] and h['original_checkpoint']==a['checkpoint']
            and h['original_descriptor']==a['descriptor'] and h['command']==command and h['helper']==job['driver']
            and h['existing_review']==inspection['receipt'] and h['snapshot']==inspection['snapshot'],'reviewed existing-response history differs')
        for row in h['original_completed_call'].values():checked(row)
    else:raise ValueError('unsupported historical authority')
    return a


def resource_runtime(p,job):
    runtime=checked(p['runtime'])
    require(set(runtime)=={'schema_version','root','python','files'} and runtime['schema_version']=='fleet-worker-runtime/v1'
        and job['command'][0]==runtime['python'] and p['worker']==runtime['files']['scripts/fleet/worker.py'],
        'explicit isolated resource runtime required')
    require({str(x.relative_to(runtime['root'])) for x in Path(runtime['root']).rglob('*') if x.is_file()}==set(runtime['files'])
        and all(binding(x['path'])==x and x['path']==str(Path(runtime['root'])/name) for name,x in runtime['files'].items()),'resource runtime closure changed')
    return runtime


def load(row):
    p=checked(row)
    require(set(p)-{'runtime'}=={'schema_version','host','job','authority','handoff','coordinator_receipt','coordinator_process',
        'smoke_verification','smoke_collection','worker','new_owner','remote_receipt','expected_reason'},'closed first-custody plan required')
    require(p['schema_version']==SCHEMA and p['host'] in L.HOSTS,'authorized fleet host required')
    job=checked(p['job']);a=authority(p,job)
    resource=a.get('kind')=='resource-reconstruction'
    if resource or a.get('kind')=='rejected-reader-review':
        resource_runtime(p,job)
    else:
        require('runtime'not in p and job['command'][0]==str(Path(job['cwd']).parent/'venv/bin/python')
            and p['worker']['path']==str(Path(job['cwd'])/'scripts/fleet/worker.py'),'isolated interpreter/runtime worker required')
    require(job['memory_limit_bytes']==8*1024**3 and job['minimum_disk_bytes']>=12*1024**3
        and 0<job['timeout_seconds']<=(7500 if resource else 3300),'original resource guards required')
    require(p['new_owner'].startswith(p['host']+':') and len(p['new_owner'])<256,'explicit host owner required')
    path=Path(p['remote_receipt']);require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(L.REMOTE),'isolated launch receipt required')
    h=checked(p['handoff']);pause=checked(h['pause']);require(h['schema_version']=='local-to-vm-custody-handoff/v1'
        and h['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION' and pause['new_dispatch_allowed'] is False,'stopped local handoff required')
    item=next(x for x in h['workbooks'] if x['workbook_id']==job['workbook'])
    require({k:item['original_source'][k] for k in ('path','sha256')}==job['source'] and item['status']!='published','handoff source not eligible for continuation')
    for r in job['inputs']:checked(r)
    return p,job


def central_gate(status,p,job):
    if p.get('runtime') is not None:
        from scripts.fleet.first_resource_authority import central
        return central(status,p,job)
    require(status['model_limit']==status['publication_limit']==1,'global limits must remain one')
    require(not any(o['state'] in {'active','hold'} for o in status['operations']),'global operation requires reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']]
    require(len(rows)==1,'exact central workbook required');w=rows[0]
    require(w['state']=='hold' and w['fence']==0 and w['owner'] is None and w['version'] is None
        and w['source_sha256']==job['source']['sha256'] and w['reason']==p['expected_reason'],'first custody changed; do not retry')
    return w


def local_custody(p):
    receipt=checked(p['coordinator_receipt']);process=checked(p['coordinator_process'])
    require(process['command']==receipt['command'],'coordinator process command differs')
    require(binding(receipt['command'][2])['sha256']==receipt['code_sha256'],'frozen coordinator changed')
    native=checked(p['authority'])
    original_home=str(Path(checked(native['plan'])['corpus']).parent) if native.get('kind')=='resource-reconstruction' else native['original_home'] if native.get('kind')=='rejected-reader-review' else str(Path(native['tracker']['path']).parents[2])
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


def remote_preflight(payload):
    import os,sys,shutil,json
    from pathlib import Path
    p=payload['plan'];job=payload['job']
    # The root already authenticated this closed runtime. Read it again before
    # loading its worker; task source code has a separate immutable snapshot.
    if 'runtime'in p:
        import hashlib
        row=p['runtime'];raw=Path(row['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('resource runtime manifest changed')
        runtime=json.loads(raw)
        for item in runtime['files'].values():
            path=Path(item['path'])
            if any(x.is_symlink()for x in(path,*path.parents))or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('resource runtime code changed')
        sys.path.insert(0,runtime['root'])
    else:sys.path.insert(0,job['cwd'])
    from scripts.fleet.migration import checked,binding,safe
    from scripts.fleet.worker import validate
    if checked(p['job'])!=job:raise ValueError('remote job changed')
    validate(job)
    if binding(p['worker']['path'])!=p['worker']:raise ValueError('runtime worker changed')
    a=checked(p['authority'])
    if a.get('kind')=='resource-reconstruction':
        from scripts.fleet.first_resource_authority import authority
        authority(p,job)
    elif a.get('kind')=='rejected-reader-review':
        from scripts.fleet.first_rejected_review_authority import authority
        for key in ('inspection','resource'):checked(a[key])
        authority(p,job)
    else:
        for key in ('tracker','checkpoint','descriptor','history','inspection'):checked(a[key])
    if a['job']!=p['job'] or a['source']!=job['source']:raise ValueError('native remote identity changed')
    if shutil.disk_usage(job['cwd']).free<job['minimum_disk_bytes']:raise ValueError('disk reserve unavailable')
    receipt=safe(p['remote_receipt'])
    for path in [safe(job['run']),safe(job['result']).parent,receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]:
        if path.exists():raise ValueError('attempt already exists; reconcile')
    # Original tracker/config directory catches historical children as well as
    # the selected output paths; /proc identity is read immediately pre-Popen.
    original_home=Path(checked(a['plan'])['corpus']).parent if a.get('kind')=='resource-reconstruction' else Path(a['original_home']) if a.get('kind')=='rejected-reader-review' else Path(a['tracker']['path']).parents[2]
    needles=[p['job']['path'],job['run'],job['result'],str(original_home)]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if any(n in cmd for n in needles):raise ValueError('original or selected task process active')
        except (FileNotFoundError,ProcessLookupError):continue
    return dict(status='VALIDATED_NOT_STARTED',job=p['job'],authority=p['authority'],active_pids=[],model_calls=0)


def function_source(name):
    source=Path(__file__).read_text();lines=source.splitlines(keepends=True)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ''.join(lines[node.lineno-1:node.end_lineno])


def ssh(p,job,action,payload):
    code=function_source('remote_preflight')+'\n'+L.function_source('remote_spawn')+'\nimport json,sys\np=json.load(sys.stdin)\n'
    code+="remote_preflight(p)\nprint(json.dumps(remote_spawn(p['launch'])))\n" if action=='launch' else 'print(json.dumps(remote_preflight(p)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('remote first-custody check/launch failed; reconcile before retry')
    return json.loads(result.stdout)


def smoke_gate(p,status):
    if p.get('runtime') is not None:
        from scripts.fleet.first_resource_authority import smoke
        return smoke(p,status)
    return F.smoke_gate(p,status)


def launch(row,journal=None,*,execute=False):
    p,job=load(row);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
        status=client.call('status');before=central_gate(status,p,job);smoke_gate(p,status)
        local=local_custody(p);remote=ssh(p,job,'check',dict(plan=p,job=job))
        if not execute:return dict(status='VALIDATED_NOT_STARTED',central=before,local=local,remote=remote,claims_changed=0,workers_started=0)
        directory=safe(Path(journal).absolute());require(not directory.exists(),'launch journal exists; reconcile without replay');directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',dict(plan=row,implementation=binding(__file__),before=before,local=local,remote=remote,automatic_retry=False),immutable=True);phase='reconcile'
        try:
            status=client.call('status');central_gate(status,p,job);smoke_gate(p,status)
            require(local_custody(p)==local,'local custody changed during preflight')
            ready=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'historical_first_custody':p['authority']})
            require(ready['state']=='ready' and ready['fence']==0 and ready['owner'] is None,'uncertain ready result')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            result=ssh(p,job,'launch',dict(plan=p,job=job,launch=dict(receipt=p['remote_receipt'],command=command,job=p['job'],root_intent=intent,cwd=job['cwd'])))
            return persist(directory/'launched.json',dict(intent=intent,remote=result,automatic_retry=False),immutable=True)
        except Exception as exc:
            persist(directory/'uncertain.json',dict(intent=intent,phase=phase,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--journal');p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute and not a.journal:p.error('--execute requires fresh journal')
    print(json.dumps(launch(dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256),a.journal,execute=a.execute),indent=2))
if __name__=='__main__':main()
