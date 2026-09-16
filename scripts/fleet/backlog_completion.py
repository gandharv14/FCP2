"""Finish only unappended hosts after a sealed empty-batch import failure.

Authenticates original enrollment and committed heads. Never enrolls, claims,
restarts a supervisor, or repeats an attempted nonempty append.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,fcntl,hashlib,importlib.util,json,os,shlex,subprocess,time

SCHEMA='fleet-backlog-enrollment-completion/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file()and not any(q.is_symlink()for q in(p,*p.parents)),'canonical completion evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'completion evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def module(row):
    require(binding(row['path'])==row,'original controller changed')
    s=importlib.util.spec_from_file_location('selected_completion_'+row['sha256'],row['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def inventory(home):return {str(p.relative_to(home)):binding(p)for p in Path(home).rglob('*')if p.is_file()}

def evidence(row):
    p=read(row);require(set(p)=={'schema_version','helper','result','journal_inventory'}and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completion selection required')
    result=read(p['result']);home=Path(p['result']['path']).parent
    require(inventory(home)==read(p['journal_inventory']),'original journal changed')
    require(result['status']=='RECONCILIATION_REQUIRED'and result['automatic_retry']is False,'sealed stopped enrollment required')
    old=read(result['plan']);E=module(old['helper']);S=module(old['source_controller']);a=read(old['admission']);prepared=read(a['prepared']);runtime=read(prepared['runtime']);receipt=read(a['coordinator'])
    require(old['schema_version']==E.SCHEMA and a['helper']==old['source_controller'],'original enrollment controller differs')
    intent=read(binding(home/'intent.json'));require(intent['plan']==result['plan']and intent['automatic_retry']is False,'original intent differs')
    hosts=set(old['batches']);require(hosts==set(old['heads'])==set(a['configs'])==set(intent['checks']),'complete original host proof required')
    start=read(old['started']);prior=read(start['plan']);prior_prepared=read(prior['prepared'])
    require(start['status']=='START_RECORDED'and prior_prepared['snapshot']==prepared['snapshot']and prior_prepared['supervisor']==prepared['supervisor'],'original pool generation differs')
    snapshot=read(prepared['snapshot']);require(inventory(snapshot['root'])==snapshot['bindings']and runtime['files']==snapshot['bindings']and runtime['root']==snapshot['root'],'original code inventory changed')
    jobs=[];selections={}
    for host in sorted(hosts):
        cfg=read(a['configs'][host]);oldcfg=read(prior['configs'][host]);batch=read(old['batches'][host]);check=intent['checks'][host]
        require({k:v for k,v in cfg.items()if k!='jobs'}=={k:v for k,v in oldcfg.items()if k!='jobs'}and batch==cfg['jobs']==prepared['assignments'][host],'original batch/config differs')
        probe=read(a['probes'][host]);require(probe['status']=='PASS'and probe['config']==a['configs'][host]and probe['snapshot']==prepared['snapshot']and probe['supervisor']==prepared['supervisor'],'original native qualification differs')
        require(check['original']['status']=='PASS'and check['append']==dict(status='APPEND_VALIDATED_NOT_STARTED',head=old['heads'][host],jobs=len(batch),claims=0,workers_started=0),'original preflight differs')
        selections[host]=dict(config=prior['configs'][host],policy=oldcfg['backlog'],batch=old['batches'][host],expected=old['heads'][host],root=str(Path(prior['run_root'])/host))
        jobs.extend((host,j,read(j))for j in batch)
    require(len({j['workbook']for _,_,j in jobs})==len(jobs),'duplicate enrolled workbook')
    rows=prepared['rows'];require(len(rows)==len(jobs)and {r['workbook']for r in rows}=={j['workbook']for _,_,j in jobs},'enrolled rows differ')
    events=[json.loads(x)for x in(home/'events.jsonl').read_text().splitlines()];require(len(events)==4*len(rows),'complete enrollment event journal required')
    for i,item in enumerate(rows):
        selected=[(h,b,j)for h,b,j in jobs if j['workbook']==item['workbook']];require(len(selected)==1,'enrollment row absent');host,jb,job=selected[0]
        require(item['host']==host and item['job']==jb and {k:item['source'][k]for k in ('path','sha256')}==job['source'],'enrollment source/job differs')
        for n,action in enumerate(('enroll','reconcile')):
            before,after=events[4*i+2*n:4*i+2*n+2]
            require(before==dict(action=action,workbook=job['workbook'],state='INTENT')and after['action']==action and after['workbook']==job['workbook']and after['state']=='RECORDED','enrollment sequence differs')
            actual=after['result']
            if action=='enroll':require(actual==dict(workbook=job['workbook'],state='hold'),'original enrollment response differs')
            else:
                require(actual['workbook']==job['workbook']and actual['source_sha256']==job['source']['sha256']and actual['fence']==0 and actual.get('claim_id')is None and actual['state']=='ready','original enrollment authority differs')
                require(json.loads(actual['reason'])==dict(plan=result['plan'],job=jb,native=a['probes'][host]),'recorded enrollment evidence differs')
    sealed={};failed=None
    for index,entry in enumerate(result['hosts']):
        host=entry['host'];require(host in hosts and host not in sealed,'duplicate or foreign attempted host')
        require(read(binding(home/(host+'-result.json')))==entry,'original host result differs')
        hi=read(binding(home/(host+'-intent.json')));require(hi==dict(plan=result['plan'],selection=selections[host],automatic_retry=False),'host intent differs')
        transport=read(binding(home/(host+'-transport/transport.json')));stdout=(home/(host+'-transport/stdout')).read_bytes();stderr=(home/(host+'-transport/stderr')).read_bytes()
        require(transport['uncertain']is False and transport['automatic_retry']is False,'uncertain append cannot be completed automatically')
        if 'result'in entry:
            require(failed is None and transport['returncode']==0 and json.loads(stdout)==entry['result'],'successful append response differs')
            value=entry['result'];require(value['status']=='BACKLOG_APPENDED'and value['claims']==value['workers_started']==0 and read(old['batches'][host]),'nonempty committed append required');sealed[host]=value['head']
        else:
            require(index==len(result['hosts'])-1 and not read(old['batches'][host])and transport['returncode']==1 and not stdout and stderr.decode().rstrip().endswith("ModuleNotFoundError: No module named 'scripts'")and entry['error_type']=='ValueError','only sealed empty-batch import failure is supported');failed=host
    require(failed is not None,'exact stopped empty-batch boundary required')
    pending=hosts-set(sealed)
    for host in pending-{failed}:require(not (home/(host+'-intent.json')).exists()and not(home/(host+'-transport')).exists(),'unattempted append has prior intent')
    return p,E,S,a,prepared,runtime,receipt,selections,sealed,pending,jobs

HEAD_CODE='''from pathlib import Path
import sys,json,hashlib,importlib.util
v=json.load(sys.stdin)
def read(b):
 p=Path(b['path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==b['sha256'];return json.loads(p.read_bytes())
cfg=read(v['selection']['config']);sup=v['supervisor'];assert hashlib.sha256(Path(sup['path']).read_bytes()).hexdigest()==sup['sha256'];s=importlib.util.spec_from_file_location('pool',sup['path']);P=importlib.util.module_from_spec(s);s.loader.exec_module(P);B=P.backlog_module(cfg['backlog']);jobs,head=B.load(cfg['backlog']);assert v['head']['path']==str(Path(read(cfg['backlog'])['directory'])/'head.json') and head==read(v['head']),'committed head changed';batch=read(v['selection']['batch']);assert jobs[-len(batch):]==batch,'committed batch differs';print(json.dumps(dict(status='COMMITTED_HEAD_PRESERVED',head=head)))
'''

def current(data):
    p,E,S,a,prepared,runtime,receipt,selections,sealed,pending,jobs=data
    import sys
    sys.path.insert(0,runtime['root'])
    from scripts.fleet.coordinator import Client
    from scripts.fleet.capacity_policy import validate_occupancy
    client=Client(receipt['url'],str(Path(receipt['private_directory'])/'admin.token'));status=client.call('status');validate_occupancy(status)
    require(status['author_capacity_policy']['authors_per_host']==2,'atomic capacity must remain active');live={w['workbook']:w for w in status['workbooks']};admission=S.selected_admission(a)
    for host,jb,job in jobs:
        w=live.get(job['workbook']);require(w and w['source_sha256']==job['source']['sha256'],'enrolled source changed')
        if host in pending:
            require(w['state']=='ready'and w['fence']==0 and w.get('claim_id')is None and w.get('owner')is None and w.get('version')is None,'unappended job no longer original ready');S.task_admission(admission,status,host,job)
    return status

def proof(data,home):
    p,E,S,a,prepared,runtime,receipt,selections,sealed,pending,jobs=data;current(data);home=Path(home);home.mkdir(mode=0o700)
    def host_check(host):
        if host in sealed:
            command=['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([runtime['python'],'-I','-B','-c',HEAD_CODE])]
            q=subprocess.run(command,input=json.dumps(dict(selection=selections[host],supervisor=prepared['supervisor'],head=sealed[host])).encode(),capture_output=True,timeout=90)
            for name,raw in [('stdout',q.stdout),('stderr',q.stderr)]:path=home/(host+'.'+name);path.write_bytes(raw);path.chmod(0o600)
            require(q.returncode==0,'committed head proof failed');return host,json.loads(q.stdout)
        return host,E.native(selections[host],prepared['supervisor'],runtime,host,evidence_directory=home/(host+'-transport'))
    checks=dict(ThreadPoolExecutor(max_workers=8).map(host_check,selections));S.retain(home/'result.json',checks);return checks

def run(row,directory,*,execute=False):
    data=evidence(row);p,E,S,a,prepared,runtime,receipt,selections,sealed,pending,jobs=data;home=Path(directory).absolute();require(not home.exists(),'completion namespace consumed; reconcile without replay');home.mkdir(mode=0o700);checks=proof(data,home/'preflight')
    if not execute:return read(S.retain(home/'result.json',dict(status='BACKLOG_COMPLETION_VALIDATED_NOT_STARTED',plan=row,checks=checks)))
    fd=os.open(Path(receipt['database']).parent/'job-launch.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);data=evidence(row);current(data);S.retain(home/'intent.json',dict(plan=row,checks=checks,automatic_retry=False));results=[]
        for host in sorted(pending):
            S.retain(home/(host+'-intent.json'),dict(plan=row,selection=selections[host],automatic_retry=False))
            try:
                result=E.native(selections[host],prepared['supervisor'],runtime,host,True,evidence_directory=home/(host+'-transport'))if read(selections[host]['batch'])else dict(status='EMPTY_BATCH',head=None,claims=0,workers_started=0)
                value=dict(host=host,result=result)
            except Exception as error:
                value=dict(host=host,status='RECONCILIATION_REQUIRED',error_type=type(error).__name__,message=str(error));results.append(value);S.retain(home/(host+'-result.json'),value);break
            results.append(value);S.retain(home/(host+'-result.json'),value)
        ok=len(results)==len(pending)and all('result'in v for v in results)
        return read(S.retain(home/'result.json',dict(status='BACKLOG_COMPLETION_RECORDED'if ok else'RECONCILIATION_REQUIRED',plan=row,hosts=results,preserved_heads=sealed,enrollments=0,claims=0,automatic_retry=False)))
    finally:os.close(fd)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--directory',required=True);a.add_argument('--execute',action='store_true');v=a.parse_args();print(json.dumps(run(dict(path=str(Path(v.plan).absolute()),sha256=v.sha256),v.directory,execute=v.execute),indent=2))
