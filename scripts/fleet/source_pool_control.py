"""One root-selected fresh pool start; uncertain admission or start never replays."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,fcntl,hashlib,importlib.util,json,os,shlex,subprocess,sys,time


def binding(path):
    p=Path(path).absolute()
    if not p.is_file() or any(x.is_symlink()for x in (p,*p.parents)):raise ValueError('canonical file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    if set(row)!={'path','sha256'}or binding(row['path'])!=row:raise ValueError('selected bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
    return binding(p)


def load(selected):
    p=read(selected)
    continued=p.get('schema_version') in {'fleet-source-pool-start/v2','fleet-source-pool-start/v3','fleet-source-pool-start/v4'}
    queued=p.get('schema_version')=='fleet-source-pool-start/v3'
    unused=p.get('schema_version')=='fleet-source-pool-start/v4'
    redistributed=p.get('schema_version')=='fleet-source-pool-start/v6'
    refused=p.get('schema_version')=='fleet-source-pool-start/v7'
    scoped=p.get('schema_version')in {'fleet-source-pool-start/v5','fleet-source-pool-start/v6','fleet-source-pool-start/v7'}
    fields={'schema_version','helper','prepared','configs','probes','coordinator','authorization','run_root'}|({'prior','previous_start'}if continued else set())|({'admission_policy'}if scoped else set())|({'redistribution','redistribution_helper'}if redistributed else set())|({'preclaim_recovery'}if refused else set())
    if set(p)-{'admission_lock','initial_holds','start_recovery'}!=fields or p['schema_version'] not in {'fleet-source-pool-start/v1','fleet-source-pool-start/v2','fleet-source-pool-start/v3','fleet-source-pool-start/v4','fleet-source-pool-start/v5','fleet-source-pool-start/v6','fleet-source-pool-start/v7'} or p['helper']!=binding(__file__):raise ValueError('closed root pool selection required')
    if 'initial_holds'in p and p['schema_version']!='fleet-source-pool-start/v5':raise ValueError('initial holds require scoped source admission')
    if 'start_recovery'in p and (p['schema_version']!='fleet-source-pool-start/v5' or 'initial_holds'not in p):raise ValueError('start recovery requires initial scoped queue')
    prepared=read(p['prepared']);snapshot=read(prepared['snapshot']);runtime=read(prepared['runtime'])
    if {str(x.relative_to(snapshot['root'])):binding(x)for x in Path(snapshot['root']).rglob('*')if x.is_file()}!=snapshot['bindings']:raise ValueError('implementation inventory differs')
    if runtime['root']!=snapshot['root']or runtime['files']!=snapshot['bindings']:raise ValueError('runtime differs')
    if p['authorization']!=prepared['authorization']:raise ValueError('authorization differs')
    if binding(p['authorization']['path'])!=p['authorization']:raise ValueError('authorization changed')
    sys.path.insert(0,snapshot['root'])
    from scripts.fleet.coordinator import Client
    from scripts.fleet.capacity_policy import validate_occupancy
    receipt=read(p['coordinator']);admission_lock_path(p,receipt)
    client=Client(receipt['url'],str(Path(receipt['private_directory'])/'admin.token'))
    status=client.call('status');validate_occupancy(status)
    admission=selected_admission(p)
    if admission is None and any(op['state']=='hold'for op in status['operations']):raise ValueError('global operation uncertainty')
    hosts=['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)]
    if set(p['configs'])!=set(hosts)or set(p['probes'])!=set(hosts)or set(prepared['assignments'])!=set(hosts):raise ValueError('all eight hosts required')
    selected_ids=[];selected_rows={};current={x['workbook']:x for x in status['workbooks']}
    for host in hosts:
        config=read(p['configs'][host]);probe=read(p['probes'][host])
        if config['host']!=host or config['jobs']!=prepared['assignments'][host]or config['code_root']!=snapshot['root']:raise ValueError('host assignment differs')
        if probe['status']!='PASS'or probe['snapshot']!=prepared['snapshot']or probe['config']!=p['configs'][host]or probe['supervisor']!=prepared['supervisor']or any(probe[k]!=0 for k in ['claims','models_dispatched','workers_started']):raise ValueError('actual native proof differs')
        if scoped and (config['schema_version']!='harbor-fleet-pool/v5' or config['admission_policy']!=p['admission_policy']):raise ValueError('selected pool admission differs')
        for job_binding in config['jobs']:
            job=read(job_binding)
            if job['version']!=snapshot['fingerprint']:raise ValueError('job code differs')
            if admission is not None:task_admission(admission,status,host,job)
            selected_ids.append(job['workbook'])
            selected_rows[job['workbook']]=(host,job_binding,job)
    if len(selected_ids)!=len(set(selected_ids)):raise ValueError('pool IDs duplicated')
    if len(prepared['rows'])!=len(selected_ids) or {r['workbook']for r in prepared['rows']}!=set(selected_ids):raise ValueError('prepared rows differ from assignments')
    for row in prepared['rows']:
        host,job_binding,job=selected_rows[row['workbook']]
        if row['host']!=host or row['job']!=job_binding or {k:row['source'][k]for k in ('path','sha256')}!=job['source']:raise ValueError('prepared job/source/host differs')
    if continued:
        previous=read(p['previous_start']);previous_plan=read(previous['plan']);previous_prepared=read(previous_plan['prepared'])
        if previous['status']!='START_RECORDED'or set(p['prior'])!=set(selected_ids):raise ValueError('previous pool start differs')
        for wb in selected_ids:
            host,_,job=selected_rows[wb];prior=p['prior'][wb]
            if prior['host']!=host or prior['job'] not in previous_prepared['assignments'][host]:raise ValueError('prior host custody differs')
            if unused:unused_authority(prior,job,current.get(wb),previous_plan)
            elif queued:queued_authority(prior,job,current.get(wb),previous_plan)
            else:retained_authority(prior,job,current.get(wb))
            if any(op['workbook']==wb for op in status['operations']):raise ValueError('source-only recovery has model/publication history')
    elif redistributed:
        r=p['redistribution_helper']
        if binding(r['path'])!=r:raise ValueError('redistribution reader changed')
        spec=importlib.util.spec_from_file_location('selected_ready_redistribution',r['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        m.validate(p,prepared,status)
    elif refused:preclaim_authority(p,prepared,status)
    elif 'start_recovery'in p:unstarted_pool_authority(p,prepared,status)
    elif 'initial_holds'in p:initial_hold_authority(p,prepared,status)
    elif set(selected_ids)&set(current):raise ValueError('fresh pool IDs already enrolled')
    return p,prepared,runtime,receipt,client,status


def preclaim_authority(p,prepared,status):
    row=p['preclaim_recovery']['helper']
    if binding(row['path'])!=row:raise ValueError('preclaim reader changed')
    spec=importlib.util.spec_from_file_location('selected_pool_preclaim_refusal',row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    from types import SimpleNamespace
    return m.validate(SimpleNamespace(read=read,binding=binding),p,prepared,status)


def initial_hold_authority(p,prepared,status):
    """Reconcile original unclaimed enrollment without erasing local history.

    Historical source/research eligibility still belongs to each native job's
    original reader. This only distinguishes enrollment from a consumed claim.
    """
    proof=read(p['initial_holds'])
    if set(proof)!={'schema_version','coordinator','status','workbooks','authorization'} or proof['schema_version']!='fleet-initial-unclaimed-holds/v1':raise ValueError('closed initial hold reconciliation required')
    if proof['coordinator']!=p['coordinator']or proof['authorization']!=p['authorization']:raise ValueError('initial hold authority differs')
    previous=read(proof['status']);ids={r['workbook']for r in prepared['rows']}
    if type(proof['workbooks'])is not list or proof['workbooks']!=sorted(ids)or not ids:raise ValueError('initial hold set differs')
    for observed in (previous,status):
        rows={r['workbook']:r for r in observed['workbooks']}
        if len(rows)!=len(observed['workbooks']):raise ValueError('duplicate initial workbook state')
        for item in prepared['rows']:
            wb=item['workbook'];w=rows.get(wb)
            if w is None or w['state']!='hold'or type(w['fence'])is not int or w['fence']!=0 or any(w.get(k)is not None for k in ('owner','claim_id','version','checkpoint')) or w['source_sha256']!=item['source']['sha256']or w.get('reason')!='initial ownership reconciliation required':raise ValueError('initial hold has consumed or changed custody')
        if any(o['workbook']in ids for o in observed['operations'])or any(r['workbook']in ids for r in observed.get('author_reservations',[])):raise ValueError('initial hold has prior operations or reservations')
    before={r['workbook']:r for r in previous['workbooks']};now={r['workbook']:r for r in status['workbooks']}
    if any(before[wb]!=now[wb]for wb in ids):raise ValueError('initial hold changed after selection')
    return proof


def unstarted_pool_authority(p,prepared,status):
    """Resume a queued pool only after every SSH call failed before transport."""
    spec=p['start_recovery']
    if set(spec)!={'result','events','intent'}:raise ValueError('closed start recovery evidence required')
    result=read(spec['result']);previous=read(result['plan']);intent=read(spec['intent'])
    if dict(p,helper=previous['helper'])!={**previous,'start_recovery':spec} or 'start_recovery'in previous:raise ValueError('unstarted pool scope changed')
    directory=Path(spec['result']['path']).parent
    if spec['intent']['path']!=str(directory/'intent.json')or spec['events']['path']!=str(directory/'events.jsonl')or intent['plan']!=result['plan']:raise ValueError('start journal detached')
    if binding(previous['helper']['path'])!=previous['helper']:raise ValueError('previous start helper changed')
    if result['status']!='RECONCILIATION_REQUIRED'or result['automatic_retry']is not False:raise ValueError('previous start not stopped')
    hosts=result['hosts']
    if len(hosts)!=8 or {r['host']for r in hosts}!=set(p['configs']):raise ValueError('all host outcomes required')
    prefix='ERROR: (gcloud.compute.ssh) There was a problem refreshing your current auth tokens: Reauthentication failed. cannot prompt during non-interactive execution.'
    for row in hosts:
        if row.get('returncode')!=1 or row.get('automatic_retry')is not False or 'failure_type'in row:raise ValueError('remote outcome remains uncertain')
        for key in ('stdout','stderr'):
            if binding(row[key]['path'])!=row[key] or Path(row[key]['path'])!=directory/(row['host']+'.'+key):raise ValueError('host evidence changed')
        if Path(row['stdout']['path']).read_bytes() or not Path(row['stderr']['path']).read_text().startswith(prefix):raise ValueError('SSH may have executed')
    if binding(spec['events']['path'])!=spec['events']:raise ValueError('queue event evidence changed')
    events=[json.loads(line)for line in Path(spec['events']['path']).read_text().splitlines()]
    if len(events)!=2*len(prepared['rows']):raise ValueError('unexpected queue actions')
    now={r['workbook']:r for r in status['workbooks']}
    if len(now)!=len(status['workbooks']):raise ValueError('duplicate current workbook')
    ids={r['workbook']for r in prepared['rows']}
    for index,item in enumerate(prepared['rows']):
        wb=item['workbook'];a,b=events[2*index:2*index+2];current=now.get(wb)
        if a!=dict(action='reconcile',workbook=wb,state='INTENT')or set(b)!={'action','workbook','state','result'}or any(b[k]!=v for k,v in dict(action='reconcile',workbook=wb,state='RECORDED').items())or b['result']!=current:raise ValueError('queued state changed after failed start')
        if current['state']!='ready'or type(current['fence'])is not int or current['fence']!=0 or any(current.get(k)is not None for k in ('owner','claim_id','version','checkpoint','expires'))or current['source_sha256']!=item['source']['sha256']:raise ValueError('queued task was consumed')
    if any(o['workbook']in ids for o in status['operations'])or any(r['workbook']in ids for r in status.get('author_reservations',[])):raise ValueError('queued task has operations or reservations')
    original=read(read(previous['initial_holds'])['status'])
    initial_hold_authority(previous,prepared,original)
    return result


def selected_admission(p):
    if p['schema_version']not in {'fleet-source-pool-start/v5','fleet-source-pool-start/v6','fleet-source-pool-start/v7'}:return None
    row=p['admission_policy']
    if binding(row['path'])!=row:raise ValueError('selected admission policy changed')
    spec=importlib.util.spec_from_file_location('root_pool_selected_admission',row['path']);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if module.SCHEMA!='fleet-concurrency-policy/v1' or not callable(module.model_admission_barriers):raise ValueError('unsupported selected admission policy')
    return module


def task_admission(admission,status,host,job):
    admission.validate_occupancy(status)
    if admission.model_admission_barriers(status,job['workbook'],host+'/slot-0/pending'):raise ValueError('model operation uncertainty')
    if any(op['workbook']==job['workbook'] and op['state']in('active','hold')for op in status['operations']):raise ValueError('target operation occupied')


def unused_authority(prior,job,current,previous_plan):
    """Transfer an exact initial job that never acquired a claim or dispatch."""
    if set(prior)!={'host','job'} or previous_plan['schema_version']!='fleet-source-pool-start/v1':raise ValueError('original initial queue required')
    if read(prior['job'])!=job:raise ValueError('unused job changed')
    if current is None or current['state']!='ready' or current['fence']!=0 or current.get('owner') is not None or current.get('version') is not None or current['source_sha256']!=job['source']['sha256']:raise ValueError('initial ready custody changed')
    plan=read(job['inputs'][0])
    if plan['reconstruction']['mode']!='source-first':raise ValueError('unchanged initial source-first job required')
    return plan


def queued_authority(prior,job,current,previous_plan):
    """Move only an unchanged, unclaimed ready job from a drained queue."""
    old=read(prior['job'])
    if old!=job or current is None or current['state']!='ready' or current['fence']!=1 or current.get('owner') is not None or current['version']!=job['version'] or current['source_sha256']!=job['source']['sha256']:raise ValueError('queued job or ready custody changed')
    original=previous_plan['prior'][job['workbook']]
    intent=read(original['records'][3]['copy']);source_job=read(original['job'])
    historical=dict(current,state='hold',owner=intent['owner'],version=source_job['version'])
    retained_authority(original,job,historical)
    if prior.get('records')!=original['records']:raise ValueError('retained queued source evidence differs')
    return True


def retained_authority(prior,job,current):
    """Recover only source-qualified work that never entered author admission."""
    old=read(prior['job']);records=prior['records'];values=[]
    for record in records:
        if record['native']['sha256']!=record['copy']['sha256']:raise ValueError('native evidence projection differs')
        values.append(read(record['copy']))
    result,ledger,outcome,intent=values[:4]
    if current is None or current['state']!='hold' or current['fence']!=1 or current['owner']!=intent['owner'] or current['version']!=old['version'] or current['source_sha256']!=old['source']['sha256']:raise ValueError('prior held source custody changed')
    oldplan=read(old['inputs'][0]);plan=read(job['inputs'][0]);handoff=read(plan['reconstruction']['handoff'])
    if job['workbook']!=old['workbook'] or job['source']!=old['source'] or job['version']!=old['version'] or intent['job']!=prior['job'] or outcome['intent']!=records[3]['native']:raise ValueError('prior worker identity differs')
    if result['status']!='RECONSTRUCTION_VERIFIED' or result['plan']!=oldplan['reconstruction']['plan'] or result['ledger']!=records[1]['native'] or result['models_dispatched']!=0 or outcome['status']!='RECONCILIATION_REQUIRED':raise ValueError('actual verified source and stopped outer required')
    if ledger['workbook_id']!=job['workbook'] or ledger['build']['state']!='VERIFIED' or ledger['build']['attempts']!=1 or ledger.get('build_history'):raise ValueError('source attempt lineage differs')
    if plan['reconstruction']['mode']!='existing' or handoff['workbook']!=job['workbook'] or handoff['state']!='VERIFIED' or any(handoff[k]!=records[i]['native']for k,i in [('ledger',1),('source_manifest',4),('segmentation_manifest',5)]):raise ValueError('existing source handoff differs')
    if plan['reconstruction']['generated_curation']!=(records[6]['native']if len(records)==7 else None):raise ValueError('retained curation proof differs')
    return oldplan


def remote(p,prepared,runtime,host,*,execute=False):
    code='''from pathlib import Path
import sys,json,hashlib,importlib.util,os
p=PLAN;prepared=PREPARED
snapshot=json.loads(Path(prepared['snapshot']['path']).read_bytes())
def b(path):return dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
assert b(prepared['snapshot']['path'])==prepared['snapshot']
assert {str(x.relative_to(snapshot['root'])):b(x)for x in Path(snapshot['root']).rglob('*')if x.is_file()}==snapshot['bindings']
assert b(prepared['supervisor']['path'])==prepared['supervisor']
sys.path.insert(0,snapshot['root'])
spec=importlib.util.spec_from_file_location('selected_pool',prepared['supervisor']['path']);pool=importlib.util.module_from_spec(spec);spec.loader.exec_module(pool)
cfg,jobs=pool.config(p['configs'][HOST]);root=Path(p['run_root'])/HOST
assert not root.exists(),'pool start namespace already consumed'
from scripts.fleet.coordinator import Client
from scripts.fleet.capacity_policy import validate_occupancy
status=Client(cfg['url'],cfg['token_file']).call('status');validate_occupancy(status)
if p['schema_version']in ('fleet-source-pool-start/v5','fleet-source-pool-start/v6','fleet-source-pool-start/v7'):
 assert cfg['schema_version']=='harbor-fleet-pool/v5' and cfg['admission_policy']==p['admission_policy']
 admission=pool.admission_module(p['admission_policy'])
 for _,job in jobs:
  assert not admission.model_admission_barriers(status,job['workbook'],HOST+'/slot-0/pending'),'model operation uncertainty'
  assert not any(op['workbook']==job['workbook'] and op['state']in('active','hold')for op in status['operations']),'target operation occupied'
else:assert not any(op['state']=='hold'for op in status['operations'])
claims={x['workbook']:x for x in pool.author_claims(status)};live=pool.native_workers(HOST)
for wb in live:assert wb in claims and pool.owner_host(claims[wb]['owner'])==HOST,'unreconciled live worker'
assert len(live)<=(pool.capacity_policy(cfg['capacity'])['max_total_workers'] if p['schema_version']in ('fleet-source-pool-start/v5','fleet-source-pool-start/v6','fleet-source-pool-start/v7') else 4 if p['schema_version']=='fleet-source-pool-start/v3' else 2)
for _,job in jobs:assert not Path(job['run']).exists() and not Path(job['result']).parent.exists()
if p['schema_version'] in ('fleet-source-pool-start/v2','fleet-source-pool-start/v3','fleet-source-pool-start/v4'):
 previous=json.loads(Path(p['previous_start']['path']).read_bytes());previous_plan=json.loads(Path(previous['plan']['path']).read_bytes());oldroot=Path(previous_plan['run_root'])/HOST
 assert (oldroot/'stop.json').is_file() and not json.loads((oldroot/'stop.json').read_bytes())['force'],'previous pool not gracefully draining'
 assert all(s['state']!='STARTING_WORKER' for s in pool.status(oldroot)['slots']),'prior dispatch still starting'
 for _,job in jobs:
  prior=p['prior'][job['workbook']]
  if p['schema_version']=='fleet-source-pool-start/v4':
   assert not (oldroot/'dispatches'/(job['workbook']+'.json')).exists(),'initial dispatch already consumed'
   initial=json.loads(Path(job['inputs'][0]['path']).read_bytes());source_plan=json.loads(Path(initial['reconstruction']['plan']['path']).read_bytes())
   assert not Path(initial['home']).exists() and not Path(source_plan['home']).exists(),'initial task namespace already consumed'
   assert job['workbook']not in live,'initial worker remains alive'
   continue
  if p['schema_version']=='fleet-source-pool-start/v3':
   assert not (oldroot/'dispatches'/(job['workbook']+'.json')).exists(),'previous queued dispatch already consumed'
   prior=previous_plan['prior'][job['workbook']]
  for record in prior['records']:assert b(record['native']['path'])==record['native'],'original source/worker evidence changed'
  oldjob=json.loads(Path(prior['job']['path']).read_bytes());oldplan=json.loads(Path(oldjob['inputs'][0]['path']).read_bytes())
  assert job['workbook']not in live,'prior worker remains alive'
  assert not (Path(oldplan['home'])/'authoring/admission-001').exists(),'author admission already consumed'
  assert not list((Path(oldjob['run'])/'model-operations').glob('**/*.json')),'source worker has model history'
if p['schema_version']=='fleet-source-pool-start/v7':
 from types import SimpleNamespace
 row=p['preclaim_recovery']['helper'];assert b(row['path'])==row
 spec=importlib.util.spec_from_file_location('selected_pool_preclaim_native',row['path']);reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
 reader.native(SimpleNamespace(read=pool.read,binding=pool.binding),p,prepared,status,HOST,pool)
# Match the proven generic launcher's lexical selected runtime tool wrappers.
os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',os.defpath)
if EXECUTE:result=pool.start(p['configs'][HOST],root)
else:result=dict(status='PASS',host=HOST,config=p['configs'][HOST],live_workbooks=sorted(live),source_qualified=False,workers_started=0)
print(json.dumps(result))
'''
    code='PLAN='+repr(p)+'\nPREPARED='+repr(prepared)+'\nHOST='+repr(host)+'\nEXECUTE='+repr(execute)+'\n'+code
    command=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([runtime['python'],'-I','-B','-c',code])]
    return subprocess.run(command,capture_output=True,timeout=180)


def admission_lock_path(p,receipt):
    if 'admission_lock'not in p:return Path(p['coordinator']['path']).parent/'job-launch.lock'
    row=p['admission_lock']
    if binding(row['path'])!=row:raise ValueError('selected admission lock helper changed')
    spec=importlib.util.spec_from_file_location('selected_admission_lock',row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m.path(receipt)


def run(selected,journal=None):
    p,prepared,runtime,receipt,client,status=load(selected)
    if p['schema_version']=='fleet-source-pool-start/v6':raise ValueError('redistribution may only append to an existing pool')
    hosts=list(p['configs'])
    def check(host):
        result=remote(p,prepared,runtime,host)
        if result.returncode:raise ValueError('native pool preflight failed on '+host+': '+result.stderr.decode(errors='replace'))
        return host,json.loads(result.stdout)
    checks=dict(ThreadPoolExecutor(max_workers=8).map(check,hosts))
    if journal is None:return dict(status='PASS',plan=selected,hosts=checks,claims=0,workers_started=0)
    journal=Path(journal)
    lock=admission_lock_path(p,receipt);fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Recheck the complete absent-ID scope under the shared admission lock.
        p,prepared,runtime,receipt,client,status=load(selected)
        journal.mkdir(parents=True,exist_ok=False)
        retain(journal/'intent.json',dict(plan=selected,preflight=checks,created_at_ns=time.time_ns(),automatic_retry=False))
        def event(value):
            with (journal/'events.jsonl').open('a')as f:f.write(json.dumps(value,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
        for row in prepared['rows']:
            wb=row['workbook'];evidence=json.dumps(dict(plan=selected,job=row['job'],native=p['probes'][row['host']]),sort_keys=True)
            if 'start_recovery'in p:
                event(dict(action='retain-ready',workbook=wb,state='RECORDED',job=row['job']));continue
            if p['schema_version']in('fleet-source-pool-start/v1','fleet-source-pool-start/v5')and 'initial_holds'not in p:
                event(dict(action='enroll',workbook=wb,state='INTENT'))
                result=client.call('enroll',workbook=wb,source_sha256=row['source']['sha256'],evidence=evidence);event(dict(action='enroll',workbook=wb,state='RECORDED',result=result))
            if p['schema_version'] in {'fleet-source-pool-start/v3','fleet-source-pool-start/v4','fleet-source-pool-start/v7'}:
                event(dict(action='retain-ready',workbook=wb,state='RECORDED',job=row['job']));continue
            event(dict(action='reconcile',workbook=wb,state='INTENT'))
            result=client.call('reconcile',workbook=wb,evidence=evidence,disposition='ready');event(dict(action='reconcile',workbook=wb,state='RECORDED',result=result))
        def start(host):
            retain(journal/(host+'.intent.json'),dict(plan=selected,config=p['configs'][host],automatic_retry=False))
            try:
                result=remote(p,prepared,runtime,host,execute=True)
                stdout=journal/(host+'.stdout');stderr=journal/(host+'.stderr');stdout.write_bytes(result.stdout);stderr.write_bytes(result.stderr)
                return dict(host=host,returncode=result.returncode,stdout=binding(stdout),stderr=binding(stderr),automatic_retry=False)
            except Exception as exc:return dict(host=host,failure_type=type(exc).__name__,automatic_retry=False)
        results=list(ThreadPoolExecutor(max_workers=8).map(start,hosts))
        return read(retain(journal/'result.json',dict(status='START_RECORDED'if all(r.get('returncode')==0 for r in results)else'RECONCILIATION_REQUIRED',plan=selected,hosts=results,automatic_retry=False)))
    finally:os.close(fd)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal');x=a.parse_args();print(json.dumps(run(dict(path=str(Path(x.plan).absolute()),sha256=x.sha256),x.journal),indent=2))
