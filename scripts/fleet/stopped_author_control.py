"""Recorded stopped-author reconciliation without stopping the live listener."""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,sqlite3,subprocess,time

AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
SCHEMA='fleet-stopped-author-control/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical control file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'control bytes changed');return json.loads(Path(row['path']).read_bytes())


def keep(path,value):
    raw=value if isinstance(value,bytes)else(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(path)


def module(row,name):
    require(binding(row['path'])==row,'selected implementation changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def stopped_evidence(row,args):
    e=read(row)
    if e.get('schema_version')=='fleet-stopped-author-no-operation-evidence/v2':return no_operation_evidence(e,args)
    require(set(e)=={'schema_version','scope_plan','fresh_processes','worker_intent'}and e['schema_version']=='fleet-stopped-author-evidence/v1','closed stopped author evidence required')
    scope=read(e['scope_plan']);oldargs=scope['arguments'];Q=module(scope['helper'],'stopped_author_original_scope_validator')
    original=Q.scope_evidence(oldargs['evidence'],oldargs)
    require(all(args[k]==oldargs[k]for k in('workbook','owner','fence')),'original scoped owner differs')
    prior=read(original['stopped']);item=e['worker_intent']
    require(item in prior['copies']and binding(item['copy']['path'])==item['copy']and item['native']['sha256']==item['copy']['sha256'],'original stopped intent custody differs')
    intent=read(item['copy']);require(intent['owner']==args['owner']and intent['automatic_retry']is False,'original stopped worker owner differs')
    native=read(e['fresh_processes']);now=time.time()
    require(set(native)=={'schema_version','host','at','boot_id','selection','files','processes','matches'}and native['schema_version']=='fleet-stopped-author-process/v1'
        and native['host']in(oldargs['host'],oldargs['host']+'.c.saleseng.internal')and type(native['at'])in(int,float)and 0<=now-native['at']<=180
        and native['matches']==[],'fresh stopped process census required')
    selected=dict(owner=args['owner'],worker=str(Path(item['native']['path']).parent),intent=item['native'])
    require(selected in native['selection']and item['native']in native['files'],'original worker not covered by fresh census')
    require(isinstance(native['processes'],list)and all(set(p)=={'pid','ppid','start','argv'}for p in native['processes']),'complete process identity census required')
    require(not any(args['owner']in p['argv']or any(selected['worker']in a for a in p['argv'])for p in native['processes']),'original worker still running')
    return e


def no_operation_evidence(e,args):
    """A stopped original worker whose final claim update was not recorded."""
    from scripts.fleet.coordinator import digest
    require(set(e)=={'schema_version','launch','copies','fresh_processes','workbook'}
        and args['quarantines']=={},'closed zero-outstanding-operation evidence required')
    w=read(e['workbook']);require(digest(w)==args['workbook_sha256']
        and w['state']=='hold'and all(w[k]==args[k]for k in('workbook','owner','fence','claim_id')),
        'different held worker custody')
    launch=read(e['launch']);remote=launch['remote'];items={}
    for item in e['copies']:
        require(set(item)=={'native','copy'}and item['native']['path']not in items
            and binding(item['copy']['path'])==item['copy']
            and item['native']['sha256']==item['copy']['sha256'],'stopped worker copies differ')
        items[item['native']['path']]=item
    def copied(row):
        item=items.get(row['path']);require(item is not None and item['native']==row,'original worker inventory incomplete')
        return read(item['copy'])
    job=copied(remote['job']);home=Path(job['run'])
    def at(name):
        item=items.get(str(home/name));require(item is not None,'missing original worker boundary');return item['native'],read(item['copy'])
    ir,intent=at('intent.json');cr,claim=at('claim.json');outcome_row,outcome=at('outcome.json');fr,finish=at('finish-uncertain.json')
    require(launch['automatic_retry']is False and remote['automatic_retry']is False
        and remote['job']==intent['job']and intent['automatic_retry']is False
        and intent['owner']==claim['owner']==args['owner']and claim['claim_id']==intent['claim_id']==args['claim_id']
        and claim['workbook']==job['workbook']==args['workbook']and claim['fence']==args['fence']
        and claim['version']==job['version']==w['version']and claim['source_sha256']==job['source']['sha256']==w['source_sha256']
        and remote['command'][remote['command'].index('--job')+1]==intent['job']['path']
        and remote['command'][remote['command'].index('--job-sha256')+1]==intent['job']['sha256']
        and remote['command'][remote['command'].index('--owner')+1]==args['owner'],
        'original launch, claim, code or source differs')
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1'and outcome['intent']==ir
        and outcome['status']=='RECONCILIATION_REQUIRED'and outcome['automatic_retry']is False
        and outcome['publication_performed']is False and outcome['returncode']is None and outcome['result']is None
        and outcome['failure_type']in('RuntimeError','TimeoutError','URLError')
        and finish==dict(outcome=outcome_row,failure_type=outcome['failure_type'],automatic_retry=False,reconciliation_required=True),
        'original uncertain final claim update required')
    log=items.get(outcome['log']['path']);require(log is not None and log['native']==outcome['log'],'original worker log missing')
    checkpoint=json.loads(w['checkpoint']);require(checkpoint['intent']==ir and type(checkpoint['pid'])is int and checkpoint['pid']>0,'last heartbeat detached from worker')
    native=read(e['fresh_processes']);now=time.time();host=args['owner'].split(':',1)[0]
    require(set(native)=={'schema_version','host','at','boot_id','selection','files','processes','matches'}
        and native['schema_version']=='fleet-stopped-author-process/v2'and native['host']in(host,host+'.c.saleseng.internal')
        and type(native['at'])in(int,float)and 0<=now-native['at']<=180 and native['matches']==[]
        and isinstance(native['boot_id'],str)and bool(native['boot_id'])
        and native['selection']==[dict(owner=args['owner'],worker=str(home),intent=ir)]
        and {r['path']:r for r in native['files']}=={k:v['native']for k,v in items.items()},
        'fresh exact stopped worker inventory required')
    require(isinstance(native['processes'],list)and all(set(p)=={'pid','ppid','pgid','start','argv'}for p in native['processes']),
        'complete native process/group census required')
    needles=[str(home),intent['job']['path'],job['driver']['path'],str(Path(job['result']).parent),args['owner']]
    require(not any(p['pid']==remote['pid']or p['pgid']==checkpoint['pid']
        or any(needle in arg for needle in needles for arg in p['argv'])for p in native['processes']),
        'original worker or child process remains')
    return e


def load(row):
    p=read(row);require(set(p)=={'schema_version','helper','primitive','support_coordinator','coordinator','process_identity','authorization','arguments'}and p['schema_version']==SCHEMA and p['helper']==binding(str(Path(__file__).absolute())),'closed stopped author control required')
    require(p['authorization']['sha256']==AUTH and binding(p['authorization']['path'])==p['authorization']and p['arguments']['authorization']==p['authorization'],'explicit authorization changed')
    old=read(p['coordinator']);require(old['url']=='http://127.0.0.1:8765'and old['command'][3]=='serve'and old['command'][old['command'].index('--database')+1]==old['database'],'original listener identity differs')
    code=dict(path=old['command'][2],sha256=old['code_sha256']);O=module(code,'stopped_author_original_coordinator')
    actual=subprocess.run(['/bin/ps','-p',str(old['pid']),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(actual.returncode==0 and actual.stdout.strip()==p['process_identity']and code['path']in actual.stdout,'live listener process changed')
    import scripts.fleet.coordinator as C
    require(binding(C.__file__)==p['support_coordinator'],'primitive coordinator support changed')
    R=module(p['primitive'],'scripts.fleet.selected_stopped_author_reconciliation')
    stopped_evidence(p['arguments']['evidence'],p['arguments'])
    return p,old,O,R


def copy_proof(data,home):
    p,old,O,R=data;home=Path(home);home.mkdir(mode=0o700);copy=home/'ledger.sqlite3'
    with sqlite3.connect('file:'+old['database']+'?mode=ro',uri=True)as source,sqlite3.connect(copy)as destination:source.backup(destination)
    copy.chmod(0o600);c=O.Coordinator(copy);before=c.status();result=R.reconcile(c,**p['arguments']);after=c.status()
    require(all(after[k]==v for k,v in before.items()if k not in('at','events','author_reservations'))and after['events']==before['events']+1,'copied reconciliation changed original authority')
    keep(home/'before.json',before);keep(home/'after.json',after)
    return keep(home/'proof.json',dict(status='FULL_DATABASE_COPY_STOPPED_AUTHOR_PASS',result=result,models=0,claims=0,listener_restarts=0))


def run(row,directory,*,execute=False):
    data=load(row);p,old,O,R=data;home=Path(directory);require(home.is_absolute()and not home.exists()and not any(x.is_symlink()for x in home.parents),'fresh canonical journal required');home.mkdir(mode=0o700)
    proof=copy_proof(data,home/'copy-proof')
    if not execute:return keep(home/'result.json',dict(status='STOPPED_AUTHOR_VALIDATED_NOT_STARTED',plan=row,proof=proof))
    locks=[]
    try:
        for name in('coordinator-recovery.lock','job-launch.lock'):
            fd=os.open(Path(old['database']).parent/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);locks.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p,old,O,R=load(row);c=O.Coordinator(old['database'])
        keep(home/'intent.json',dict(plan=row,proof=proof,at=time.time(),automatic_retry=False))
        result=R.reconcile(c,**p['arguments']);receipt=keep(home/'reconciliation.json',result)
        after=O.Client(old['url'],Path(old['private_directory'])/'worker.token').call('status');keep(home/'after.json',after)
        require(result['after_reservation']in after['author_reservations'],'original live listener did not observe author release')
        return keep(home/'result.json',dict(status='STOPPED_AUTHOR_RECONCILED',plan=row,receipt=receipt,proof=proof,models=0,claims=0,listener_restarts=0,unknown_operations_released=False))
    finally:
        for fd in locks:os.close(fd)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);a.add_argument('--execute',action='store_true');v=a.parse_args();print(json.dumps(run(dict(path=v.plan,sha256=v.sha256),v.journal,execute=v.execute)))
