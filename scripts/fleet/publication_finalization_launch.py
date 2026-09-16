"""Once-only root admission of a fully revalidated retained promotion."""
from pathlib import Path
import argparse, ast, copy, hashlib, json, shlex, subprocess, sys, time

SCHEMA='fleet-root-publication-finalization/v1'
FIELDS={'schema_version','helper','runtime','coordinator_receipt','capacity_policy','plan','publisher',
        'implementation','qualification','native_context','probe','host','python','expected_owner',
        'expected_fence','owner','prior_reason','reconciliation'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and not any(x.is_symlink() for x in (p,*p.parents)),'unsafe bound path')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'selected finalization input changed')
    return json.loads(Path(row['path']).read_bytes())

def native_result(row):
    outer=read(row);require(outer['returncode']==0,'native validation transport failed')
    guard=json.loads(outer['stdout']);require(guard['returncode']==0 and guard['failure'] is None
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592,'native validation original guard failed')
    return json.loads(guard['log'])

SOURCE_SCHEMA='fleet-retained-source-publication-finalization/v1'
SOURCE_FIELDS={'schema_version','original_plan','original_publisher','intent','failure','finalizer','lifecycle',
    'authorization','resource_policy','context_reader','qualification_plan','qualification_implementation'}
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'

def source_proofs(p,plan,selected,q):
    """Bind a pure qualification to the retained source and original owning intent."""
    require(set(selected)==SOURCE_FIELDS and selected['schema_version']==SOURCE_SCHEMA,
        'closed completed-source finalization required')
    auth=selected['authorization']
    require(auth['sha256']==AUTH and binding(auth['path'])==auth,'explicit finalization authorization required')
    old=read(selected['original_plan']);expected=dict(old);expected.pop('publication_wait',None)
    expected.update(journal=plan['journal'],finalization=plan['finalization'])
    require(plan==expected and plan['journal']!=old['journal']
        and old.get('source_revision',{}).get('completed_reader')is not None,
        'source finalization changed original qualified task')
    qualified=read(selected['qualification_plan'])
    if qualified!=old:
        require('publication_resume' in qualified and qualified==dict(old,journal=qualified['journal'],
            publication_resume=qualified['publication_resume']),'qualified context changed original task')
        resume=read(qualified['publication_resume'])
        require(resume['prior_plan']==selected['original_plan'] and resume['intent']==selected['intent'],
            'qualified context lost original owning intent')
    qi=read(selected['qualification_implementation']);implementation=read(p['implementation'])
    require(implementation['publisher']==p['publisher']
        and set(implementation['dependencies'])==set(qi['dependencies'])|{'source_finalization'}
        and qi['dependencies']=={k:v for k,v in implementation['dependencies'].items() if k!='source_finalization'},
        'finalization changed validated qualification dependencies')
    for item in [qi['publisher'],implementation['publisher'],*implementation['dependencies'].values()]:
        require(binding(item['path'])==item,'qualified publication implementation changed')
    require(read(selected['resource_policy'])==dict(schema_version='fleet-source-publication-finalization-policy/v1',
        authorization=auth,wall_seconds=3300,memory_bytes=8589934592,disk_reserve_bytes=8589934592,
        maximum_wait_seconds=120,maximum_finalizations=1),'bounded source finalization policy differs')
    for key in ('original_publisher','finalizer','lifecycle','context_reader'):
        require(binding(selected[key]['path'])==selected[key],'source finalization code changed')
    require(Path(selected['intent']['path']).parent==Path(old['journal'])/'owning-publication',
        'source finalization lost original owning intent')
    require(set(q)-{'at'}=={'status','plan','publisher','tracker','qualification','models_dispatched','publication_mutations'}
        and q['plan']==selected['qualification_plan'] and q['publisher']==qi['publisher'],
        'pure qualification belongs to another selected context')
    require(q['qualification']['state']=='PROMOTED' and q['qualification']['prior_promotion']is True
        and q['qualification']['failure']is None and q['qualification']['tracker']==q['tracker']['path']
        and q['qualification']['tracker_sha256']==q['tracker']['sha256'],
        'qualified promotion tracker differs')


def proofs(p,plan):
    q=native_result(p['qualification']);context=native_result(p['native_context']);selected=read(plan['finalization'])
    require(q['status']=='CURRENT_QUALIFICATION_VERIFIED_READ_ONLY' and q['models_dispatched']==q['publication_mutations']==0
        and q['qualification']['verified_promoted'] is True and q['qualification']['revalidation_required'] is False,
        'actual current native qualification required')
    require(context['status']=='RETAINED_FINALIZATION_CONTEXT_VALIDATED' and context['plan']==p['plan']
        and context['publisher']==p['publisher'] and context['tracker']==q['tracker']
        and context['models_dispatched']==context['publication_mutations']==0,'actual stopped native context differs')
    if selected.get('schema_version')==SOURCE_SCHEMA:
        source_proofs(p,plan,selected,q)
    else:
        require(q['plan']==selected['original_plan'] and q['publisher']==selected['original_publisher'],
            'qualification belongs to another original publication')
        require(q['cache']['sha256']==selected['digest_cache']['sha256']
            and q['performance']['harbor_completed_lanes.py']['sha256']==selected['pair_lanes']['sha256']
            and q['performance']['harbor_ready_transitions.py']['sha256']==selected['pair_ready']['sha256'],
            'finalization changed native-tested performance code')
    return q,context

def load(row):
    p=read(row);require(set(p)-{'preclaim_recovery','dependency_continuation'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),
        'closed selected finalization launcher required')
    runtime=read(p['runtime']);root=Path(runtime['root'])
    require({str(x) for x in root.rglob('*') if x.is_file()}=={x['path'] for x in runtime['files']},'runtime inventory changed')
    for item in runtime['files']:require(binding(item['path'])==item,'runtime code changed')
    require(not any(n=='scripts' or n.startswith('scripts.') for n in sys.modules),'isolated root control required')
    sys.path.insert(0,str(root));from scripts.fleet import publication_launch as L
    plan=read(p['plan']);manifest=read(p['implementation'])
    require(p['host'] in L.HOSTS and manifest['publisher']==p['publisher'] and binding(p['publisher']['path'])==p['publisher'],
        'selected owning publisher changed')
    for item in manifest['dependencies'].values():require(binding(item['path'])==item,'publisher dependency changed')
    require(p['owner']!=p['expected_owner'] and p['expected_fence']>0,'new finalization identity required')
    require(binding(p['probe']['path'])==p['probe'],'native context probe changed')
    proofs(p,plan)
    reconciliation=read(p['reconciliation']);require(reconciliation['result']['state']=='abandoned'
        and reconciliation['workbook_remains_held'] is True,'prior publication operation not reconciled')
    if 'preclaim_recovery' in p:preclaim(p,plan)
    return p,plan,L,reconciliation

def dependency_continuation(p,old):
    """Permit only completing the status reader's already pinned dependency.

    The old snapshot remains untouched. All owning functions and selected work
    remain identical; neither an arbitrary publisher upgrade nor a new policy
    is authorized by this narrowly typed continuation.
    """
    v=read(p['dependency_continuation'])
    require(set(v)=={'schema_version','prior_implementation','implementation'}
        and v['schema_version']=='publication-status-dependency-continuation/v1'
        and v['prior_implementation']==old['implementation']
        and v['implementation']==p['implementation'],'dependency continuation differs')
    before=read(v['prior_implementation']);after=read(v['implementation'])
    require(before['publisher']==old['publisher'] and after['publisher']==p['publisher'],
        'publisher closure detached')
    bd=before['dependencies'];ad=after['dependencies']
    require(set(ad)==set(bd)|{'capacity_policy'},'dependency inventory changed')
    require({k:r for k,r in bd.items() if k not in {'publisher','publication_status'}}==
        {k:r for k,r in ad.items() if k not in {'publisher','publication_status','capacity_policy'}},
        'unrelated publication dependency changed')
    require({k:r for k,r in before.items() if k not in {'publisher','dependencies'}}==
        {k:r for k,r in after.items() if k not in {'publisher','dependencies'}},'implementation semantics changed')
    for item in [*bd.values(),*ad.values()]:
        require(binding(item['path'])==item,'dependency bytes changed')
    def tree(row):return ast.parse(Path(row['path']).read_text())
    def equal(a,b):return ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)
    old_status=tree(bd['publication_status']);new_status=tree(ad['publication_status'])
    count=0;expected_sha=None
    for node in old_status.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            if node.targets[0].id=='CAPACITY_POLICY_SHA':expected_sha=ast.literal_eval(node.value)
            if node.targets[0].id=='CAPACITY_POLICY':
                require(ast.literal_eval(node.value)is None,'prior reader already selected dependency')
                node.value=ast.parse(repr(ad['capacity_policy']),mode='eval').body;count+=1
    require(count==1 and ad['capacity_policy']['sha256']==expected_sha and equal(old_status,new_status),
        'status continuation changed more than its existing pinned dependency')
    old_pub=tree(old['publisher']);new_pub=tree(p['publisher']);count=0
    for node in ast.walk(old_pub):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='load' and len(node.args)==2:
            if isinstance(node.args[0],ast.Constant) and node.args[0].value=='publication_status':
                require(ast.literal_eval(node.args[1])==bd['publication_status'],'prior status binding differs')
                node.args[1]=ast.parse(repr(ad['publication_status']),mode='eval').body;count+=1
    require(count==1 and equal(old_pub,new_pub),'owning publisher code changed')
    return True


def preclaim(p,plan):
    v=read(p['preclaim_recovery'])
    require(set(v)=={'schema_version','prior_profile','root_intent','ready','response','remote_intent'}
        and v['schema_version']=='fleet-publication-preclaim-recovery/v1','closed preclaim recovery required')
    old=read(v['prior_profile']);intent=read(v['root_intent']);ready=read(v['ready'])
    require(binding(v['response']['path'])==v['response'],'prior response changed')
    remote=read(v['remote_intent']);old_plan=read(old['plan'])
    require(old['expected_owner']==p['expected_owner'] and old['expected_fence']==p['expected_fence']
        and old['owner']!=p['owner'] and (old['publisher']==p['publisher'] or dependency_continuation(p,old))
        and old['reconciliation']==p['reconciliation'] and old['host']==p['host']
        and intent['profile']==v['prior_profile'],'preclaim predecessor differs')
    require({k:x for k,x in old_plan.items() if k!='journal'}==
        {k:x for k,x in plan.items() if k!='journal'} and old_plan['journal']!=plan['journal'],
        'preclaim recovery changed work or reused journal')
    require(remote['action']=='remote' and remote['automatic_retry']is False and remote['owner']==old['owner']
        and remote['plan']==old['plan'] and remote['driver']==old['publisher']
        and remote['version']==Path(plan['code_root']).name,'retained remote preclaim identity differs')
    require(ready['state']=='ready' and ready['owner']is None and ready['claim_id']is None
        and ready['fence']==p['expected_fence'] and ready['workbook']==plan['workbook']
        and ready['source_sha256']==plan['source_sha256']
        and ready['reason']==json.dumps(v['root_intent'],sort_keys=True)
        and json.loads(ready['checkpoint'])=={'publication':old['plan']},'prior ready custody differs')
    return v,old,remote,ready

def native_preclaim(payload):
    from pathlib import Path
    import hashlib,json,os
    home=Path(payload['journal']);expected=payload['intent']
    files=[x for x in home.rglob('*') if x.is_file()]
    if files!=[home/'intent.json'] or any(x.is_symlink()for x in home.rglob('*')):
        raise ValueError('prior remote execution progressed beyond preclaim')
    if hashlib.sha256(files[0].read_bytes()).hexdigest()!=expected['sha256']:
        raise ValueError('prior remote intent changed')
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name)==os.getpid():continue
        try:
            stat=(path/'stat').read_text().rsplit(')',1)[1].split()
            if stat[0]=='Z':continue
            args=[x.decode(errors='replace')for x in(path/'cmdline').read_bytes().split(b'\0')if x]
            if any(a in (payload['publisher'],payload['plan'],str(home)) or a.startswith(str(home)+'/')for a in args):
                raise ValueError('prior publication process remains')
        except(FileNotFoundError,ProcessLookupError):continue
    return dict(status='STOPPED_BEFORE_CLAIM',intent=expected)

def central(status,p,plan,capacity):
    capacity.validate_occupancy(status)
    require(abs(time.time()-status['at'])<=60,'stale central status')
    rows=[w for w in status['workbooks'] if w['workbook']==plan['workbook']]
    require(len(rows)==1,'unique retained publication workbook required');w=rows[0]
    if 'preclaim_recovery' in p:
        _,_,remote,ready=preclaim(p,plan)
        require(w==ready and not any(o['operation_id']==remote['operation_id'] or
            (o['workbook']==plan['workbook'] and o['fence']>p['expected_fence'])
            for o in status['operations']),'preclaim custody or operation changed')
    else:
        require(w['state']=='hold' and w['owner']==p['expected_owner'] and w['fence']==p['expected_fence']
            and w['version']==Path(plan['code_root']).name and w['source_sha256']==plan['source_sha256']
            and w['reason']==p['prior_reason'],'exact reconciled held custody changed')
    for op in status['operations']:
        if op['state']=='hold':
            owners=[x for x in status['workbooks'] if x['workbook']==op['workbook']]
            require(op['kind']=='model' and op['workbook']!=plan['workbook'] and len(owners)==1
                and owners[0]['state']=='hold' and owners[0]['owner']==op.get('owner')
                and owners[0]['fence']==op.get('fence'),'uncertain publication, target or unknown owner remains')
            continue
        if op['state']=='active':
            require(op['kind']=='model' and op['workbook']!=plan['workbook'],'publication or target operation active')
            owners=[x for x in status['workbooks'] if x['workbook']==op['workbook']]
            require(len(owners)==1 and owners[0]['state']=='active' and owners[0]['owner']==op['owner']
                and owners[0]['fence']==op['fence'] and owners[0]['expires']>status['at'],'unrecognized active model owner')
    return w


def launch(row,journal,*,execute=False):
    p,plan,L,reconciled=load(row);q,context=proofs(p,plan)
    cap=L.module('finalization_capacity',p['capacity_policy']);r=read(p['coordinator_receipt'])
    client=L.Client(r['url'],Path(r['private_directory'])/'admin.token')
    out=Path(journal).absolute();require(not out.exists(),'root namespace consumed; reconcile without replay')
    intent=None
    try:
        with L.lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
            status=client.call('status');before=central(status,p,plan,cap)
            old=[o for o in status['operations'] if o['operation_id']==reconciled['result']['operation_id']]
            require(len(old)==1 and old[0]['state']=='abandoned' and old[0]['workbook']==plan['workbook']
                and old[0]['owner']==p['expected_owner'] and old[0]['fence']==p['expected_fence'],'prior operation changed after reconciliation')
            command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([p['python'],'-I','-B',p['probe']['path']])]
            native=subprocess.run(command,capture_output=True)
            require(native.returncode==0,'fresh stopped native context failed: '+native.stderr.decode(errors='replace'))
            require(json.loads(native.stdout)==context,'native promotion or selected context changed')
            if 'preclaim_recovery' in p:
                import inspect
                v,previous,_,_=preclaim(p,plan)
                payload=dict(journal=read(previous['plan'])['journal'],intent=v['remote_intent'],
                    publisher=previous['publisher']['path'],plan=previous['plan']['path'])
                observed=L.ssh(p,inspect.getsource(native_preclaim)+'\nprint(__import__("json").dumps(native_preclaim('+repr(payload)+')))')
                require(observed==dict(status='STOPPED_BEFORE_CLAIM',intent=v['remote_intent']),
                    'fresh preclaim observation differs')
            central(L.connected_status(p),p,plan,cap)
            if not execute:return dict(status='FINALIZATION_VALIDATED_NOT_STARTED',profile=row,tracker=q['tracker'],models_dispatched=0,claims_changed=0)
            out.mkdir(parents=True,mode=0o700)
            intent=L.persist(out/'intent.json',dict(profile=row,prior_owner=before,native=context,automatic_retry=False),immutable=True)
            central(client.call('status'),p,plan,cap)
            ready=before if 'preclaim_recovery' in p else client.call('reconcile',workbook=plan['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'publication':p['plan']})
            require(ready['state']=='ready' and ready['fence']==p['expected_fence'],'reconciliation response changed')
            L.persist(out/'ready.json',ready,immutable=True)
            child=[p['python'],'-I','-B',p['publisher']['path'],'remote','--plan',p['plan']['path'],'--plan-sha256',p['plan']['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['owner'],'--version',Path(plan['code_root']).name]
            L.persist(out/'command.json',child,immutable=True)
        remote=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(child)]
        with (out/'response.log').open('xb') as log:result=subprocess.run(remote,stdout=log,stderr=subprocess.STDOUT)
        require(result.returncode==0,'finalization response requires reconciliation')
        return L.persist(out/'completed.json',dict(intent=intent,status='RESPONSE_RECEIVED_REQUIRES_VERIFIED_RESULT_FETCH',automatic_retry=False),immutable=True)
    except BaseException as exc:
        if intent is not None:L.persist(out/'uncertain.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True)
        raise

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--journal',required=True);a.add_argument('--execute',action='store_true');x=a.parse_args()
    print(json.dumps(launch(dict(path=x.profile,sha256=x.sha256),x.journal,execute=x.execute)))
