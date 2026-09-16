"""Enroll a proven fresh batch, then publish it to an existing durable backlog.

Uses the original source-pool validation and native preflight. No supervisor is
started, no claim is made, and an uncertain enrollment or append never retries.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,fcntl,hashlib,importlib.util,json,os,shlex,subprocess,time

SCHEMA='fleet-backlog-enrollment/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path).absolute()
    require(not any(q.is_symlink()for q in(p,*p.parents)),'canonical backlog enrollment file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'backlog enrollment bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(row):
    require(binding(row['path'])==row,'source controller changed')
    s=importlib.util.spec_from_file_location('selected_backlog_source_controller',row['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def load(row):
    p=read(row)
    require(set(p)=={'schema_version','helper','source_controller','admission','started','heads','batches'}
            and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed backlog enrollment selection required')
    S=module(p['source_controller']);a,prepared,runtime,receipt,client,status=S.load(p['admission'])
    require(a['helper']==p['source_controller']and a['schema_version']in {'fleet-source-pool-start/v5','fleet-source-pool-start/v6'},'original fresh scoped source admission required')
    policy=status.get('author_capacity_policy')
    require(policy and policy['authors_per_host']==2 and policy['schema_version']=='fleet-author-capacity/v1',
            'atomic author capacity must be active before replenishment')
    old_result=read(p['started']);require(old_result['status']=='START_RECORDED'and old_result['automatic_retry']is False,
                                        'existing pool must have an authentic completed start')
    old=read(old_result['plan']);old_prepared=read(old['prepared']);old_receipt=read(old['coordinator'])
    require(old['schema_version']=='fleet-source-pool-start/v5'and old['authorization']==a['authorization']
            and old['admission_policy']==a['admission_policy']and old_prepared['snapshot']==prepared['snapshot']
            and old_prepared['supervisor']==prepared['supervisor']
            and all(old_receipt[k]==receipt[k]for k in('database','private_directory','url')),
            'backlog requires the same selected authority, source policy and worker generation')
    hosts=set(a['configs']);require(set(old['configs'])==set(p['heads'])==set(p['batches'])==hosts,'complete host append selection required')
    require(a['run_root']!=old['run_root'],'fresh native validation namespace required')
    old_ids=set();new_ids=set();selected={}
    for host in hosts:
        cfg=read(a['configs'][host]);prior=read(old['configs'][host]);batch=read(p['batches'][host])
        require('backlog'in prior and {k:v for k,v in cfg.items()if k!='jobs'}=={k:v for k,v in prior.items()if k!='jobs'},
                'running pool configuration changed beyond proposed jobs')
        require(batch==cfg['jobs']==prepared['assignments'][host],'batch differs from original native admission jobs')
        for j in prior['jobs']:old_ids.add(read(j)['workbook'])
        for j in batch:new_ids.add(read(j)['workbook'])
        selected[host]=dict(config=old['configs'][host],policy=prior['backlog'],batch=p['batches'][host],expected=p['heads'][host],root=str(Path(old['run_root'])/host))
    overlap=old_ids&new_ids
    if overlap:
        require(a['schema_version']=='fleet-source-pool-start/v6','initial job is already consumed or selected')
        relocation=read(a['redistribution'])
        require(relocation.get('enrollment_kind')=='initial-start'and relocation['original_admission']==old_result['plan']and overlap<=set(relocation['rows']),'initial job lacks exact original redistribution custody')
        for host in hosts:
            destination={read(j)['workbook']for j in read(old['configs'][host])['jobs']}
            require(not destination&{read(j)['workbook']for j in read(p['batches'][host])},'initial job cannot be appended to its original host')
    return p,S,a,prepared,runtime,receipt,client,status,selected


REMOTE=r'''
from pathlib import Path
import sys,json,hashlib,importlib.util
v=json.load(sys.stdin)
def b(path):return dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
def read(row):
 assert b(row['path'])==row,'native append binding changed'
 return json.loads(Path(row['path']).read_bytes())
s=read(v['selection']['config']);root=Path(v['selection']['root']);current=read(b(root/'current.json'))
assert not (root/'stop.json').exists(),'running pool is draining'
assert current['config']==v['selection']['config'] and current['supervisor']==v['supervisor'],'running pool generation differs'
assert b(v['supervisor']['path'])==v['supervisor']
spec=importlib.util.spec_from_file_location('selected_pool',v['supervisor']['path']);P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
cfg,_=P.config(v['selection']['config']);B=P.backlog_module(v['selection']['policy']);assert cfg['backlog']==v['selection']['policy']
assert current['slots'] and all(P.process_identity(x['identity']['pid'])==x['identity'] for x in current['slots']),'selected supervisor is not running'
prior,head=B.load(v['selection']['policy']);assert head==v['selection']['expected'],'append head changed'
extra=read(v['selection']['batch']);P.validated_jobs(cfg,cfg['jobs']+prior+extra)
for selected in extra:
 job=read(selected)
 assert not Path(job['run']).exists() and not Path(job['result']).parent.exists(),'new job was already dispatched'
 assert not (root/'dispatches'/(job['workbook']+'.json')).exists(),'dispatch intent already exists'
if v['execute']:
 result=None
 if extra:
  from scripts.fleet.coordinator import Client
  status=Client(cfg['url'],cfg['token_file']).call('status');live={w['workbook']:w for w in status['workbooks']}
  for selected in extra:
   job=read(selected);w=live[job['workbook']]
   assert w['state']=='ready'and w['source_sha256']==job['source']['sha256']and w['fence']==0 and w['claim_id']is None,'root enrollment is no longer fresh ready'
  result=B.append(v['selection']['policy'],v['selection']['batch'],expected=head)
 print(json.dumps(dict(status='BACKLOG_APPENDED'if extra else'EMPTY_BATCH',head=result,claims=0,workers_started=0)))
else:print(json.dumps(dict(status='APPEND_VALIDATED_NOT_STARTED',head=head,jobs=len(extra),claims=0,workers_started=0)))
'''


def native(selected,supervisor,runtime,host,execute=False,*,evidence_directory=None):
    command=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([runtime['python'],'-I','-B','-c',REMOTE])]
    started=time.time()
    def retain_response(stdout,stderr,returncode,uncertain):
        if evidence_directory is None:
            require(not execute,'owning append requires private full transport custody');return
        home=Path(evidence_directory);home.mkdir(mode=0o700)
        for name,raw in [('stdout',stdout or b''),('stderr',stderr or b''),('transport.json',json.dumps(dict(started=started,ended=time.time(),returncode=returncode,uncertain=uncertain,automatic_retry=False)).encode())]:
            fd=os.open(home/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    require(not execute or evidence_directory is not None,'owning append requires private transport journal')
    try:q=subprocess.run(command,input=json.dumps(dict(selection=selected,supervisor=supervisor,execute=execute)).encode(),capture_output=True,timeout=90)
    except subprocess.TimeoutExpired as error:
        retain_response(error.stdout,error.stderr,None,True);raise
    retain_response(q.stdout,q.stderr,q.returncode,False)
    require(q.returncode==0,'native append failed; retain response and reconcile without replay: '+q.stderr.decode(errors='replace')[-1200:])
    return json.loads(q.stdout)


def run(row,journal=None):
    p,S,a,prepared,runtime,receipt,client,status,selected=load(row)
    def check(host):
        q=S.remote(a,prepared,runtime,host)
        require(q.returncode==0,'original source native preflight failed: '+q.stderr.decode(errors='replace')[-1200:])
        return host,dict(original=json.loads(q.stdout),append=native(selected[host],prepared['supervisor'],runtime,host))
    checks=dict(ThreadPoolExecutor(max_workers=8).map(check,selected))
    if journal is None:return dict(status='BACKLOG_ENROLLMENT_VALIDATED_NOT_STARTED',plan=row,checks=checks,claims=0,workers_started=0)
    directory=Path(journal).absolute();require(not directory.exists(),'enrollment journal consumed; no automatic replay')
    # Authority identity comes from its database, never the location of a receipt copy.
    lock=Path(receipt['database']).parent/'job-launch.lock';fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p,S,a,prepared,runtime,receipt,client,status,selected=load(row)
        directory.mkdir(mode=0o700);S.retain(directory/'intent.json',dict(plan=row,checks=checks,automatic_retry=False,at=time.time()))
        def event(value):
            with (directory/'events.jsonl').open('a')as f:json.dump(value,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        for item in ([]if a.get('schema_version')=='fleet-source-pool-start/v6'else prepared['rows']):
            evidence=json.dumps(dict(plan=row,job=item['job'],native=a['probes'][item['host']]),sort_keys=True)
            for action,args in [('enroll',dict(source_sha256=item['source']['sha256'])),('reconcile',dict(disposition='ready'))]:
                event(dict(action=action,workbook=item['workbook'],state='INTENT'))
                result=client.call(action,workbook=item['workbook'],evidence=evidence,**args)
                event(dict(action=action,workbook=item['workbook'],state='RECORDED',result=result))
        results=[]
        for host in selected:
            S.retain(directory/(host+'-intent.json'),dict(plan=row,selection=selected[host],automatic_retry=False))
            try:
                result=native(selected[host],prepared['supervisor'],runtime,host,True,evidence_directory=directory/(host+'-transport'))
                result=dict(host=host,result=result)
            except Exception as error:
                result=dict(host=host,status='RECONCILIATION_REQUIRED',error_type=type(error).__name__,message=str(error))
                results.append(result);S.retain(directory/(host+'-result.json'),result);break
            results.append(result);S.retain(directory/(host+'-result.json'),result)
        ok=len(results)==len(selected)and all('result'in v for v in results)
        return read(S.retain(directory/'result.json',dict(status='BACKLOG_APPEND_RECORDED'if ok else'RECONCILIATION_REQUIRED',plan=row,hosts=results,automatic_retry=False)))
    finally:os.close(fd)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal');v=a.parse_args()
    print(json.dumps(run(dict(path=str(Path(v.plan).absolute()),sha256=v.sha256),v.journal),indent=2))
