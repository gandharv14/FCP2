"""Read-only fleet execution snapshot. Never prints prompts, credentials or bodies.

Reports actual process/resource observations, allowlisted checkpoint fields and
model-gate timing. Missing observations remain unknown, never a qualification.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

HOSTS=['hhu-server']+['hhu-server-'+str(i) for i in range(2,9)]

PROBE=r'''
import hashlib,json,os,re,time,shlex
from pathlib import Path
configuration=json.load(__import__('sys').stdin)
root=Path(configuration['root']);selected=configuration['jobs'];now=time.time()
def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return None
def row(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
def scalar(v):return isinstance(v,(bool,int,float)) or isinstance(v,str) and bool(re.fullmatch(r'[A-Za-z0-9_./: -]{1,120}',v))
def summary(path):
    value=read(path)
    if not isinstance(value,dict):return None
    result={k:value[k] for k in ['status','step','next_step','phase','returncode','failure_type','publication_performed','automatic_retry'] if k in value and scalar(value[k])}
    if not result:return None
    return {**row(path),'modified_at':path.stat().st_mtime,**result}
def processes():
    result={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            raw=(path/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
            argv=(path/'cmdline').read_bytes().split(b'\0');argv=[a.decode(errors='replace') for a in argv if a]
            if fields[0]=='Z':continue
            role=next((Path(a).name for a in argv if a.endswith('.py')),Path(argv[0]).name if argv else 'unknown')
            result[int(path.name)]={'ppid':int(fields[1]),'ticks':int(fields[11])+int(fields[12]),'start_ticks':int(fields[19]),'rss':int(fields[21])*os.sysconf('SC_PAGE_SIZE'),'argv':argv,'role':role}
        except (OSError,ValueError,IndexError):pass
    return result
def descendants(pid,table):
    found={pid}
    while True:
        expanded=found|{p for p,r in table.items() if r['ppid'] in found}
        if expanded==found:return found
        found=expanded
def cpu():return list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
first=processes();ticks1=cpu();start=time.monotonic();time.sleep(2);last=processes();ticks2=cpu();interval=time.monotonic()-start
hz=os.sysconf('SC_CLK_TCK');uptime=float(Path('/proc/uptime').read_text().split()[0]);jobs=[]
for select in selected:
    job_path=Path(select['path']);job=read(job_path)
    if not job:
        jobs.append({'job':select,'observation':'JOB_NOT_TRANSFERRED'});continue
    if row(job_path)!=select:
        jobs.append({'job':select,'observation':'JOB_HASH_MISMATCH'});continue
    run=Path(job['run']);task=Path(job['result']).parent
    intent=read(run/'intent.json')
    if intent and intent.get('job')!=select:
        jobs.append({'job':select,'workbook':job['workbook'],'version':job['version'],
            'run':str(run),'task_root':str(task),'observation':'UNSELECTED_PLAN',
            'selected_job':intent.get('job')})
        continue
    worker_pids=[]
    for pid,p in last.items():
        argv=p['argv']
        if '--job' in argv and argv[argv.index('--job')+1:argv.index('--job')+2]==[str(job_path)] and p['role']=='worker.py':worker_pids.append(pid)
    observed=[];active_journals=[]
    for pid in worker_pids:
        members=descendants(pid,last);cpu_seconds=0
        for member in members:
            args=last[member]['argv']
            candidate=None
            if '_child' in args and args.index('_child')+1<len(args):
                candidate=Path(args[args.index('_child')+1])
            elif 'child' in args and '--plan' in args and args.index('--plan')+1<len(args):
                candidate=Path(args[args.index('--plan')+1])
            # A continuation can retain its original authoring operation root
            # beside its new task home. Inspect that workbook's journals too.
            journal_roots=(task,root/'workbooks'/job['workbook'])
            if candidate is not None and candidate.is_absolute() and any(candidate.is_relative_to(p) for p in journal_roots) and candidate.is_file():
                intent_value=read(candidate) or {}
                active_journals.append({'intent':row(candidate),'journal_directory':str(candidate.parent),
                    'action':intent_value.get('action') if scalar(intent_value.get('action')) else None})
        for member in members:
            before=first.get(member);after=last[member]
            if before and before['start_ticks']==after['start_ticks']:cpu_seconds+=max(0,after['ticks']-before['ticks'])/hz
        observed.append({'pid':pid,'kernel_start_ticks':last[pid]['start_ticks'],
            'elapsed_seconds':uptime-last[pid]['start_ticks']/hz,'tree_process_count':len(members),
            'tree_rss_bytes':sum(last[m]['rss'] for m in members),'tree_cpu_percent':100*cpu_seconds/interval,
            'roles':sorted(set(last[m]['role'] for m in members))})
    gate=run/'model-operations';waits=[];intents=acquired=released=0
    if gate.is_dir():
        for directory in gate.iterdir():
            if not directory.is_dir():continue
            intents+=int((directory/'intent.json').is_file())
            value=read(directory/'acquired.json')
            if value:
                acquired+=1
                if isinstance(value.get('wait_seconds'),(int,float)):waits.append(value['wait_seconds'])
            released+=int((directory/'released.json').is_file())
    progress=[]
    progress_roots=[run,task]
    for journal in active_journals:
        parent=Path(journal['journal_directory']).parent
        if not any(parent.is_relative_to(p) for p in progress_roots):progress_roots.append(parent)
    for directory in progress_roots:
        if not directory.is_dir():continue
        count=0
        for current,dirs,files in os.walk(directory):
            # Inspect stage journals only, never generated source/prompt content.
            dirs[:]=[d for d in dirs if d not in ['.git','node_modules','venv','code','golden','source','sources','segments','tools','mcp']]
            for name in files:
                if name not in ['outcome.json','result.json','checkpoint.json','resource.json','complete.json']:continue
                count+=1
                if count>5000:break
                item=summary(Path(current)/name)
                if item:progress.append(item)
            if count>5000:break
    progress.sort(key=lambda r:r['modified_at'],reverse=True)
    normalization_reviews=[]
    for stage in sorted((task/'stages').glob('partition-*-round-*')):
        result=read(stage/'result.json')
        if not isinstance(result,dict):continue
        selected_review=result.get('value')
        if not isinstance(selected_review,dict) or set(selected_review)!={'path','sha256'}:continue
        review_path=Path(selected_review['path'])
        allowed=root/'workbooks'/job['workbook']
        if not review_path.is_absolute() or '..' in review_path.parts or not review_path.is_relative_to(allowed):continue
        if any(p.is_symlink() for p in (review_path,*review_path.parents)) or not review_path.is_file():continue
        if row(review_path)!=selected_review:continue
        review=read(review_path)
        if not isinstance(review,dict) or type(review.get('valid')) is not bool:continue
        normalization_reviews.append({'stage':stage.name,'review':selected_review,'valid':review['valid'],
                                      'ordinal':review.get('ordinal') if type(review.get('ordinal')) is int else None})
    intent=read(run/'intent.json');claim=read(run/'claim.json');outcome=summary(run/'outcome.json')
    jobs.append({'job':select,'workbook':job['workbook'],'version':job['version'],'run':str(run),
        'task_root':str(task),'observation':'RUNNING' if observed else 'ATTEMPT_RETAINED' if run.exists() else 'NOT_STARTED',
        'elapsed_since_intent_seconds':(now-intent['created_at_ns']/1e9) if intent and 'created_at_ns' in intent else None,
        'processes':observed,'active_journals':active_journals,'claim':{k:claim[k] for k in ['owner','fence','state'] if k in claim} if claim else None,
        'outcome':outcome,'recent_checkpoints':progress[:8],'normalization_reviews':normalization_reviews,
        'model_gate':{'intents':intents,'acquired':acquired,'released':released,'total_wait_seconds':sum(waits),'maximum_wait_seconds':max(waits) if waits else None}})
mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:','MemTotal:'))}
diff=[b-a for a,b in zip(ticks1,ticks2)];total=sum(diff[:8]);idle=diff[3]+diff[4]
pools=[]
pool_files=set(root.glob('pools/**/current.json'))
for configured in configuration.get('pool_roots',[]):
    directory=Path(configured).expanduser()
    if (directory/'current.json').is_file():pool_files.add(directory/'current.json')
    if directory.is_dir():pool_files.update(directory.glob('**/current.json'))
for current in sorted(pool_files):
    value=read(current)
    if not value or value.get('host')!=configuration['host']:continue
    directory=current.parent/'generations'/value['generation'];slots=[]
    for item in value['slots']:
        state=read(directory/('slot-'+str(item['slot'])+'.state.json')) or {}
        pid=item['identity']['pid'];actual=last.get(pid)
        alive=bool(actual and str(actual['start_ticks'])==str(item['identity']['start']))
        slots.append({'slot':item['slot'],'supervisor_pid':pid,'supervisor_alive':alive,
            'state':state.get('state','STARTING'),'workbook':state.get('workbook'),
            'heartbeat_age_seconds':time.time()-state['updated_at'] if 'updated_at' in state else None,
            'completed_dispatches':state.get('completed_dispatches',0)})
    cfg=read(Path(value['config']['path'])) or {}
    base=[cfg.get('python','python3'),value['supervisor']['path']]
    commands={'inspect':shlex.join(base+['status','--run',str(current.parent)]),
              'stop_and_drain':shlex.join(base+['stop','--run',str(current.parent)]),
              'resume_untouched_jobs_after_reconciliation':shlex.join(base+['resume','--config',value['config']['path'],'--config-sha256',value['config']['sha256'],'--run',str(current.parent)])}
    pools.append({'run':str(current.parent),'generation':value['generation'],'slots':slots,'stop_requested':(current.parent/'stop.json').exists(),'commands':commands})
print(json.dumps({'host':configuration['host'],'observed_at':time.time(),'cpu_sample_seconds':interval,
    'host_resources':{'cpus':os.cpu_count(),'cpu_busy_percent':100*(total-idle)/total if total else None,
    'memory_available_bytes':mem['MemAvailable'],'memory_total_bytes':mem['MemTotal'],
    'disk_free_bytes':__import__('shutil').disk_usage(root if root.exists() else Path.home()).free,'load_average':list(os.getloadavg())},
    'pool_status':'OBSERVED' if pools else 'NOT_LAUNCHED','pools':pools,'jobs':jobs,
    'pipeline_qualification_inferred':False}))
'''


def binding(path):
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def launched_job(root, selected, launch_record):
    """Read a native job only through its exact completed-launch custody copy."""
    path=Path(selected['path'])
    native_root=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')
    if not path.is_relative_to(native_root):
        if binding(path)!=selected:raise ValueError('selected job changed')
        return json.loads(path.read_bytes())
    if not path.is_absolute() or '..' in path.parts:raise ValueError('invalid native job path')
    matches=[]
    for receipt in sorted(root.glob('control/native-job-custody/*.json')):
        if any(p.is_symlink() for p in (receipt,*receipt.parents)):continue
        value=json.loads(receipt.read_bytes())
        if set(value)!={'schema_version','native_job','local_copy','launch_record'} or value['schema_version']!='fleet-inspection-native-job-custody/v1':continue
        if value['native_job']!=selected or value['launch_record']!=launch_record:continue
        copy=Path(value['local_copy']['path'])
        if not copy.is_absolute() or not copy.is_relative_to(root.resolve()) or any(p.is_symlink() for p in (copy,*copy.parents)):raise ValueError('unsafe native job custody')
        if value['local_copy']['sha256']!=selected['sha256'] or binding(copy)!=value['local_copy']:raise ValueError('native job copy changed')
        matches.append(json.loads(copy.read_bytes()))
    if len(matches)!=1:raise ValueError('exact single native job custody required')
    return matches[0]


def gather_jobs(root):
    result={h:[] for h in HOSTS}
    candidates=set(root.glob('plans*/**/*job*.json')) | set(root.glob('holds-*/**/*job*.json'))
    for path in sorted(candidates):
        try:value=json.loads(path.read_text())
        except (OSError,ValueError):continue
        if value.get('schema_version')!='harbor-fleet-worker-job/v1':continue
        parts=Path(value['run']).parts
        try:host=parts[parts.index('workers')+1]
        except (ValueError,IndexError):continue
        if host in result:result[host].append(binding(path))
    # Historical continuations can retain a native /home worker directory.
    # Their hash-bound first-custody plan supplies the host, never a guessed path.
    for path in sorted(root.glob('control/first-custody-*/**/*launch*.json')):
        try:
            value=json.loads(path.read_text())
            if value.get('schema_version')!='harbor-fleet-first-historical-custody/v1':continue
            host=value['host'];selected=value['job'];job_path=Path(selected['path'])
            if host not in result or binding(job_path)!=selected:continue
            if json.loads(job_path.read_text()).get('schema_version')!='harbor-fleet-worker-job/v1':continue
            if selected not in result[host]:result[host].append(selected)
        except (OSError,ValueError,KeyError,TypeError):continue
    # A supported successor can keep its job outside the original plans tree.
    # Discover it through the completed launch's hash-bound intent and plan.
    launches=set(root.glob('control/**/launched.json'))
    # Deployment repairs share the sibling diagnostic namespace. Only a full
    # hash-bound completed launch chain can introduce a job from that tree.
    launches.update((root.parent/'bulk-production').glob('**/launched.json'))
    for path in sorted(launches):
        try:
            launched=json.loads(path.read_text())
            intent_binding=launched['intent'];intent_path=Path(intent_binding['path'])
            if binding(intent_path)!=intent_binding:continue
            intent=json.loads(intent_path.read_text())
            plan_binding=intent['plan'];plan_path=Path(plan_binding['path'])
            if binding(plan_path)!=plan_binding:continue
            plan=json.loads(plan_path.read_text())
            if plan.get('schema_version') not in {
                'harbor-fleet-job-launch/v1',
                'harbor-fleet-job-launch/v2',
                'harbor-fleet-job-launch/v3',
                'harbor-fleet-source-initial-launch/v1',
                'harbor-fleet-first-historical-custody/v1',
                'harbor-fleet-first-historical-custody/v2',
                'fleet-source-reconstruction-custody/v2',
            }:continue
            host=plan['host'];selected=plan['job']
            if host not in result:continue
            if launched['remote']['job']!=selected or launched['remote']['root_intent']!=intent_binding:continue
            if launched_job(root,selected,binding(path)).get('schema_version')!='harbor-fleet-worker-job/v1':continue
            if selected not in result[host]:result[host].append(selected)
        except (OSError,ValueError,KeyError,TypeError):continue
    # A started source pool has a single root-selected job catalog. Its slots
    # dispatch jobs later, so there is no per-job launched.json to discover.
    for path in sorted(root.glob('control/source-pool-*/start-*/intent.json')):
        try:
            intent=json.loads(path.read_bytes());selected_plan=intent['plan']
            if binding(Path(selected_plan['path']))!=selected_plan:continue
            plan=json.loads(Path(selected_plan['path']).read_bytes())
            if plan.get('schema_version') not in {'fleet-source-pool-start/v1','fleet-source-pool-start/v2','fleet-source-pool-start/v3','fleet-source-pool-start/v4'}:continue
            selected=plan['prepared']
            if binding(Path(selected['path']))!=selected:continue
            prepared=json.loads(Path(selected['path']).read_bytes())
            additions=[]
            for host,jobs in prepared['assignments'].items():
                if host not in result:raise ValueError('unknown pool host')
                for job in jobs:
                    if binding(Path(job['path']))!=job:raise ValueError('pool job changed')
                    if json.loads(Path(job['path']).read_bytes()).get('schema_version')!='harbor-fleet-worker-job/v1':raise ValueError('invalid pool job')
                    additions.append((host,job))
            for host,job in additions:
                if job not in result[host]:result[host].append(job)
        except (OSError,ValueError,KeyError,TypeError):continue
    return result


def collect(root,hosts,pool_roots=()):
    root=root.resolve();jobs=gather_jobs(root)
    def probe(host):
        config={'root':str(root),'host':host,'jobs':jobs[host],'pool_roots':list(pool_roots)}
        cmd=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command','python3 -c '+shlex.quote(PROBE)]
        value=subprocess.run(cmd,input=json.dumps(config).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        if value.returncode:return {'host':host,'observation':'SSH_PROBE_FAILED','returncode':value.returncode}
        try:return json.loads(value.stdout)
        except ValueError:return {'host':host,'observation':'INVALID_PROBE_RESPONSE'}
    with ThreadPoolExecutor(max_workers=8) as executor:machines=list(executor.map(probe,hosts))
    receipt=json.loads((root/'control/coordinator-process.json').read_text())
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from scripts.fleet.coordinator import Client
    try:
        central=Client(receipt['url'],Path(receipt['private_directory'])/'worker.token').call('status')
        # No arbitrary checkpoint data/reasons. Only explicit operational fields.
        central={'workbooks':[{k:w[k] for k in ['workbook','state','owner','fence','expires','version']} for w in central['workbooks']],
                 'operations':[{k:o[k] for k in ['kind','operation_id','workbook','state','started','ended']} for o in central['operations']],
                 'model_limit':central['model_limit'],'publication_limit':central['publication_limit']}
    except Exception as exc:central={'observation':'UNAVAILABLE','failure_type':type(exc).__name__}
    sources=[]
    for path in sorted((root/'deploy').glob('actual-source-*.stdout.json')):
        try:value=json.loads(path.read_text())
        except ValueError:continue
        sources.append({'evidence':binding(path),**{k:value[k] for k in ['actual_source_validation','source_generation','pipeline_code_hash','model_calls'] if k in value}})
    return {'schema_version':'harbor-fleet-inspection/v1','at':time.time(),'machines':machines,
            'coordinator':central,'actual_source_checks':sources,'pipeline_qualification_inferred':False,
            'limitations':['CPU is a measured two-second sample; process RSS includes shared pages.',
                           'Missing stages or processes are unknown; retained evidence requires independent validation.',
                           'Model-gate counts are operation journals, not inferred semantic review passes.']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--output');p.add_argument('--host',action='append',choices=HOSTS);p.add_argument('--pool-root',action='append',default=[])
    a=p.parse_args();value=collect(Path(a.root),a.host or HOSTS,a.pool_root)
    if a.output:
        path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    from collections import Counter
    central=value['coordinator']
    brief={'snapshot':a.output,'queue_counts':dict(Counter(w['state'] for w in central.get('workbooks',[]))),
           'model_operations':dict(Counter(o['state'] for o in central.get('operations',[]) if o['kind']=='model')),
           'machines':[]}
    for machine in value['machines']:
        resources=machine.get('host_resources',{})
        brief['machines'].append({'host':machine['host'],'pool_status':machine.get('pool_status'),
            'cpu_busy_percent':resources.get('cpu_busy_percent'),
            'memory_available_gib':resources.get('memory_available_bytes',0)/1024**3 if resources else None,
            'disk_free_gib':resources.get('disk_free_bytes',0)/1024**3 if resources else None,
            'jobs':[{'workbook':j.get('workbook'),'observation':j['observation'],'processes':j.get('processes'),
                     'model_gate':j.get('model_gate'),'active_journals':j.get('active_journals'),
                     'worker_status':(j.get('outcome') or {}).get('status'),
                     'normalization_reviews':[{k:r[k] for k in ['stage','valid','ordinal']} for r in j.get('normalization_reviews',[])],
                     'latest_checkpoint_status':j.get('recent_checkpoints',[{}])[0].get('status') if j.get('recent_checkpoints') else None}
                    for j in machine.get('jobs',[]) if j['observation'] not in ['NOT_STARTED','JOB_NOT_TRANSFERRED']]})
    print(json.dumps(brief,indent=2))

if __name__=='__main__':main()
