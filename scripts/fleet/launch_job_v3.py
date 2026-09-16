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

SCHEMA='harbor-fleet-job-launch/v3'
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')


def require(ok,message):
    if not ok: raise ValueError(message)


def load(row):
    p=checked(row)
    from scripts.fleet.offline_bindings import readers
    check,bind=readers(p.get('offline_bindings'))
    require(set(p)-{'native_lease_runway','stopped_model_reconciliation','unclaimed_start','admission_lock','preclaim_ready_selection'}=={'schema_version','host','job','root_plan','probe','prior_outcome','prior_process',
        'expected_fence','expected_owner','expected_version','new_owner','remote_receipt','coordinator_receipt','worker','prior_process_copy','runtime','prior_kind','offline_bindings'},
        'closed root launch plan required')
    require(p['schema_version']==SCHEMA and p['host'] in HOSTS,'exact authorized VM required')
    job=check(p['job']);root=check(p['root_plan']);probe=check(p['probe'])
    prior=check(p['prior_outcome']);process=check(p['prior_process_copy'])
    require(p['prior_process_copy']['sha256']==p['prior_process']['sha256'],'remote process receipt differs from retained copy')
    require(job['inputs'] and job['inputs'][0]==p['root_plan'],'job first input must be root-selected plan')
    require(root['workbook']==job['workbook'],'root plan workbook differs')
    require('--plan' in job['command'] and job['command'][job['command'].index('--plan')+1]==p['root_plan']['path']
        and sum(x in ('--plan-sha256','--sha256') for x in job['command'])==1 and job['command'][next(i for i,x in enumerate(job['command']) if x in ('--plan-sha256','--sha256'))+1]==p['root_plan']['sha256'],
        'command does not select exact root plan')
    require(probe.get('status')=='PASS' and probe.get('models_dispatched')==0
        and probe.get('result',{}).get('plan')==p['root_plan'],'root-selected successful no-model probe required')
    require(prior.get('schema_version')=='harbor-fleet-worker-outcome/v1'
        and prior.get('status')=='RECONCILIATION_REQUIRED' and prior.get('automatic_retry') is False,
        'prior retained worker hold required')
    prior_intent=check(prior['intent']);old_job=check(prior_intent['job'])
    require(old_job['source']==job['source'] and old_job['workbook']==job['workbook']
        and old_job['version']==p['expected_version'] and prior_intent['owner']==p['expected_owner'],
        'prior source, version or owner differs')
    require(type(p['expected_fence']) is int and p['expected_fence']>0,'positive previous fence required')
    require(p['prior_kind'] in ('process','pool-completion'),'supported stopped custody kind required')
    if p['prior_kind']=='process':
        require(type(process.get('pid')) is int and process['pid']>0 and prior_intent['job']['path'] in process['command'],
            'prior worker process does not select its retained job')
    else:
        require(process.get('job')==prior_intent['job'] and process.get('outcome')==p['prior_outcome']
            and process.get('returncode')==0 and process.get('automatic_retry') is False
            and process.get('failure_type') is None,'original pool completion changed')
    require(isinstance(p['new_owner'],str) and p['new_owner'].startswith(p['host']+':') and len(p['new_owner'])<256,
        'explicit host-bound new owner required')
    runtime=check(p['runtime'])
    require(set(runtime)=={'schema_version','root','python','files'} and runtime['schema_version']=='fleet-worker-runtime/v1','closed runtime manifest required')
    require(job['command'][0]==runtime['python'] and p['worker']==runtime['files']['scripts/fleet/worker.py'],'explicit isolated runtime worker required')
    require(all(bind(r['path'])==r for r in runtime['files'].values()),'runtime dependency changed')
    # /home is a macOS system symlink, but these are Linux destination paths.
    # Verify their filesystem ancestry on the VM, not on the root's Mac.
    receipt=Path(p['remote_receipt']);require(receipt.is_absolute() and '..' not in receipt.parts
        and receipt.is_relative_to(REMOTE),'isolated remote process receipt required')
    for r in job['inputs']: require(bind(r['path'])==r,'bound input changed')
    if 'native_lease_runway' in p:
        selected=check(p['native_lease_runway'])
        require(job['inputs'].count(p['native_lease_runway'])==1
            and selected['workbook']==job['workbook'] and selected['source_sha256']==job['source']['sha256'],
            'native runway must be a source-bound job input')
    require(not(p.get('unclaimed_start')and p.get('preclaim_ready_selection')),'select one preclaim recovery')
    if p.get('unclaimed_start') is not None:unclaimed_start(p,job,check=check)
    if p.get('preclaim_ready_selection') is not None:preclaim_ready_selection(p,job,check=check)
    return p,job


def central_gate(status,p,job):
    from scripts.fleet.capacity_policy import validate_occupancy, model_admission_barriers
    validate_occupancy(status)
    require(not model_admission_barriers(status,job['workbook'],p['new_owner']),'held model or target model needs reconciliation')
    # Workbook admission does not acquire publication authority. An unrelated
    # publication hold still occupies the coordinator's serialized publication
    # slot; it need not stop useful authoring on another exclusively held source.
    require(not any(x['state'] in ('active','hold') and x.get('workbook')==job['workbook'] for x in status['operations']),'target operation needs reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']]
    require(len(rows)==1,'exact workbook ownership required');w=rows[0]
    author_capacity_gate(status,p,job,w)
    if p.get('unclaimed_start') is not None:
        unclaimed_start(p,job,live=w,abandoned_claims=status.get('abandoned_claims',[]));return w
    if p.get('preclaim_ready_selection') is not None:
        preclaim_ready_selection(p,job,live=w);return w
    if p.get('stopped_model_reconciliation') is not None:
        from scripts.fleet.offline_bindings import readers
        check,_=readers(p.get('offline_bindings'))
        settlement=check(p['stopped_model_reconciliation'])
        if settlement.get('evidence_alias') is not None:
            stopped_model_reconciliation(status,p,job,w);return w
    require(w['state']=='hold' and w['fence']==p['expected_fence'] and w['owner']==p['expected_owner']
        and w['source_sha256']==job['source']['sha256'] and w['version']==p['expected_version'],
        'held source, fence, owner or version changed')
    if p.get('stopped_model_reconciliation') is not None:
        stopped_model_reconciliation(status,p,job,w)
    elif w['reason']=='lease expired; reconcile remote process and artifacts':
        # An expired coordinator claim can outlive a stopped worker's final
        # heartbeat. Preserve that reason and authenticate its retained intent;
        # the original fresh remote preflight still precedes every root intent.
        import math,time
        from scripts.fleet.offline_bindings import readers
        check,_=readers(p.get('offline_bindings'))
        prior=check(p['prior_outcome']);intent=check(prior['intent']);old=check(intent['job'])
        require(prior.get('schema_version')=='harbor-fleet-worker-outcome/v1' and prior.get('status')=='RECONCILIATION_REQUIRED' and prior.get('automatic_retry') is False,'expired claim requires an actual stopped outcome')
        require(intent['owner']==w['owner'] and old['workbook']==job['workbook'] and old['source']==job['source'] and old['version']==w['version'],'expired claim worker identity differs')
        expires=w.get('expires')
        require(type(expires) in (int,float) and math.isfinite(expires) and expires<time.time(),'expired claim timestamp invalid or still live')
        checkpoint=json.loads(w['checkpoint'])
        require(checkpoint.get('intent')==prior['intent'] and type(checkpoint.get('pid')) is int and checkpoint['pid']>0,'expired claim checkpoint differs from stopped worker intent')
    else:
        require(json.loads(w['reason'])==p['prior_outcome'],'hold reason differs from prior worker outcome')
    return w


def author_capacity_gate(status,p,job,current):
    """Reject a visibly full host before a once-only launch consumes its path.

    This is a read-only hint, never a reservation. The coordinator still checks
    capacity atomically at claim. Only the exact held predecessor which this
    launch will reconcile may be subtracted; unrelated stale holds stay occupied.
    """
    import math
    from scripts.fleet.coordinator import Coordinator,digest
    policy=status.get('author_capacity_policy')
    if policy is None:return
    require(policy.get('schema_version')=='fleet-author-capacity/v1'
        and type(policy.get('authors_per_host'))is int and policy['authors_per_host']==2,
        'supported author capacity policy required')
    host=Coordinator.author_host(job['workbook'],p['new_owner'])
    if host is None:return  # Publication has its own serialized operation gate.
    limit=policy['authors_per_host'];now=status.get('at')
    require(type(now)in(int,float)and math.isfinite(now),'finite author capacity observation required')
    for row in status.get('author_capacity_extensions',[]):
        require(row.get('schema_version')=='fleet-author-capacity-extension/v1'
            and type(row.get('authors_per_host'))is int and row['authors_per_host']==3
            and row.get('base_policy_changed')is False and row.get('held_reservations_retained')is True
            and row.get('physical_models_per_host')==2 and row.get('publication_limit')==1
            and all(type(row.get(k))in(int,float)and math.isfinite(row[k])for k in('created_at','expires_at'))
            and 0<row['expires_at']-row['created_at']<=14400,
            'supported bounded author extension required')
        if row['host']==host and row['author_policy_sha256']==digest(policy)and row['created_at']<=now<row['expires_at']:
            limit=row['authors_per_host']
    reservations=status.get('author_reservations')
    require(isinstance(reservations,list),'durable author reservations required')
    occupied=[];seen=set()
    for row in reservations:
        require(row.get('state')in('active','hold','released')and row.get('claim_id')not in seen,
            'invalid author reservation inventory')
        seen.add(row['claim_id'])
        if row['host']!=host or row['state']=='released':continue
        replacing=(not p.get('unclaimed_start')and not p.get('preclaim_ready_selection')
            and current['state']=='hold'and current.get('claim_id')==row['claim_id']
            and all(current.get(k)==row.get(k)for k in('workbook','owner','fence'))
            and current['owner']==p['expected_owner']and current['fence']==p['expected_fence'])
        if not replacing:occupied.append(row)
    require(len(occupied)<limit,'host author capacity occupied; await finish or exact stopped reconciliation')


def stopped_model_reconciliation(status,p,job,held):
    """Authenticate an abandoned no-HTTP operation without rewriting its hold."""
    from scripts.fleet.offline_bindings import readers
    from scripts.fleet.coordinator import digest
    check,_=readers(p.get('offline_bindings'))
    receipt=check(p['stopped_model_reconciliation']);proof=check(receipt['proof'])
    alias=receipt.get('evidence_alias')
    original_held=held
    evidence=receipt['proof']
    if alias is not None:
        from types import SimpleNamespace
        selected=receipt['recovery_reader'];require(binding(selected['path'])==selected,'settlement reader changed')
        tree=ast.parse(Path(selected['path']).read_text())
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef)and node.name=='reconciliation_evidence']
        schemas=[node.value.value for node in tree.body if isinstance(node,ast.Assign)and len(node.targets)==1
            and isinstance(node.targets[0],ast.Name)and node.targets[0].id=='SCHEMA'and isinstance(node.value,ast.Constant)]
        require(len(functions)==1 and schemas==['harbor-model-pretransport-recovery/v1'],'supported original pretransport reader required')
        scope=dict(E=SimpleNamespace(require=require),read=check,json=json)
        exec(compile(ast.Module(body=functions,type_ignores=[]),selected['path'],'exec'),scope)
        evidence=scope['reconciliation_evidence'](proof,receipt)
        rows=[x for x in check(alias['before'])['workbooks']if x['workbook']==job['workbook']]
        require(len(rows)==1,'one original held author required');original_held=rows[0]
        require(held in receipt['after']['workbooks']and held['state']=='hold'and held['owner']is None
            and held['fence']==p['expected_fence']and held['source_sha256']==job['source']['sha256']
            and held['version']==p['expected_version']and original_held['owner']==p['expected_owner'],'settled current custody differs')
    require(receipt['result']==dict(state='abandoned',operation_id=proof['operation'])
        and receipt['workbook_unchanged'] is (alias is None) and receipt['model_replay'] is False
        and receipt['original_attempt_consumed'] is True,'explicit abandoned operation receipt required')
    require(proof['schema_version']=='fleet-pretransport-stop-proof/v1' and proof['no_transport'] is True
        and proof['pid_exists'] is False and proof['matching_processes']==[]
        and proof['worker_outcome']==p['prior_outcome'] and proof['process']==p['prior_process'],
        'stopped pretransport custody differs')
    previous=[x for x in receipt['after']['workbooks']if x['workbook']==job['workbook']]
    require(previous==[held] and held['state']=='hold' and original_held['reason']=='uncertain model; reconcile without replay',
        'held row changed after abandoned operation reconciliation')
    operations=[x for x in receipt['after']['operations']if x['kind']=='model' and x['operation_id']==proof['operation']]
    current=[x for x in status['operations']if x['kind']=='model' and x['operation_id']==proof['operation']]
    require(len(operations)==1 and current==operations,'reconciled operation changed')
    operation=operations[0]
    request=[x for x in proof['executor_inventory']if x['path']==str(Path(proof['executor'])/'request.json')]
    require(len(request)==1 and operation['state']=='abandoned' and operation['workbook']==job['workbook']
        and operation['owner']==original_held['owner'] and operation['fence']==original_held['fence']
        and operation['identity']==digest(dict(workbook=job['workbook'],request_sha256=request[0]['sha256']))
        and json.loads(operation['evidence'])==evidence,'abandoned request identity differs')
    prior=check(p['prior_outcome']);intent=check(prior['intent']);old=check(intent['job']);process=check(p['prior_process_copy'])
    require(prior['schema_version']=='harbor-fleet-worker-outcome/v1' and prior['status']=='RECONCILIATION_REQUIRED'
        and prior['returncode']==1 and prior['failure_type'] is None and prior.get('result') is None
        and prior['automatic_retry'] is False and prior['intent']==proof['worker_intent'],
        'original stopped worker outcome differs')
    require(intent['owner']==original_held['owner'] and old['workbook']==job['workbook'] and old['source']==job['source']
        and old['version']==held['version'] and process['job']==intent['job'] and process['pid']==proof['pid']
        and process['start_ticks']==proof['start_ticks'],'original worker/process identity differs')
    return operation

def unclaimed_start(p,job,*,check=None,live=None,native=False,abandoned_claims=None):
    """A stopped intent-only worker after an authenticated ready transition."""
    import base64,hashlib,json
    from pathlib import Path
    def read(row):
        if check is not None:return check(row)
        path=Path(row['path']);raw=path.read_bytes()
        if any(x.is_symlink()for x in(path,*path.parents))or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('unclaimed start evidence changed')
        return json.loads(raw)
    def must(ok,message):
        if not ok:raise ValueError(message)
    proof=read(p['unclaimed_start']);inventory=read(proof['native_inventory']);launch=read(proof['prior_launch']);root_intent=read(launch['intent']);old=read(root_intent['plan']);old_job=read(old['job'])
    must(set(proof)-{'successor','abandoned_claim'}=={'schema_version','status','at','database','claim_id','claims','events','event_high_water','workbook','native_inventory','worker_intent','prior_launch','automatic_retry','claims_changed','models_dispatched'},'closed unclaimed-start proof required')
    must(proof['schema_version']=='fleet-unclaimed-start-absence/v1'and proof['status']=='UNCLAIMED_START_ABSENCE_VERIFIED'
        and proof['claims']==proof['events']==[]and proof['automatic_retry']is False and proof['claims_changed']==proof['models_dispatched']==0,'explicit claim absence required')
    permitted={'job','remote_receipt','offline_bindings'};authority_job=old_job
    if 'successor'in proof:
        authority_job=preclaim_archive_successor(p,old,old_job,proof,read);permitted.update({'root_plan','probe'})
    expected_job=dict(authority_job,run=job['run'])
    if p.get('native_lease_runway')!=old.get('native_lease_runway'):
        previous=read(old['native_lease_runway']);selected=read(p['native_lease_runway'])
        must(set(selected)==set(previous)|{'maximum_seconds'} and 'maximum_seconds'not in previous
            and all(selected[k]==v for k,v in previous.items()if k!='required_seconds')
            and type(selected['required_seconds'])is int and type(selected['maximum_seconds'])is int
            and 600<=selected['required_seconds']<selected['maximum_seconds']<=previous['required_seconds'],
            'unclaimed renewal admission must retain exact source/tracker and a bounded smaller window')
        must(old_job['inputs'].count(old['native_lease_runway'])==1,'original runway input is not unique')
        expected_job['inputs']=[p['native_lease_runway']if row==old['native_lease_runway']else row for row in authority_job['inputs']]
        permitted.add('native_lease_runway')
    must(set(p)-{'unclaimed_start'}==set(old)and all(p[k]==v for k,v in old.items()if k not in permitted),
        'unclaimed recovery changed original selected authority')
    must(job==expected_job and job['run']!=old_job['run']and Path(job['run']).parent==Path(old_job['run']).parent
        and p['remote_receipt']!=old['remote_receipt'],'only a fresh worker journal and process receipt may differ')
    must(inventory['processes']==[]and inventory['boundary']==dict(worker_files=['intent.json'],author_output_exists=False,pid_exists=False),'unclaimed worker has effects or process')
    rows={r['path']:r for r in inventory['files']}
    def captured(path):
        row=rows[path];raw=base64.b64decode(row['base64'],validate=True)
        must(hashlib.sha256(raw).hexdigest()==row['sha256'],'retained native bytes differ')
        if native:
            actual=Path(path);must(not any(x.is_symlink()for x in(actual,*actual.parents))and actual.read_bytes()==raw,'native unclaimed boundary changed')
        return raw,dict(path=path,sha256=row['sha256'])
    raw,ib=captured(old_job['run']+'/intent.json');intent=json.loads(raw);raw,pb=captured(old['remote_receipt']);process=json.loads(raw)
    log,_=captured(str(Path(old['remote_receipt']).with_suffix('.log')))
    must(ib==proof['worker_intent']and intent['claim_id']==proof['claim_id']and intent['job']==old['job']
        and intent['owner']==p['new_owner']and intent['automatic_retry']is False,'unclaimed intent detached from source job')
    must(process==launch['remote']and process['job']==old['job']and process['root_intent']==launch['intent']
        and process['command'][-2:]==['--owner',p['new_owner']]and old['job']['path']in process['command'],'stopped process detached from original root launch')
    claim_failures=(b'TimeoutError: timed out\n',
        b'RuntimeError: coordinator HTTP 500: coordinator error; reconcile uncertain mutations before retry\n',
        b'scripts.fleet.coordinator.Conflict: host author capacity occupied; await finish or exact stopped reconciliation\n')
    must(log.endswith(claim_failures) and b'claim = client.call("claim"' in log,
        'only exact stopped first-claim transport failure or author-capacity denial is supported')
    timed_out=log.endswith(b'TimeoutError: timed out\n')
    if timed_out or 'abandoned_claim' in proof:
        import math
        must('abandoned_claim' in proof,'timed-out claim requires durable coordinator retirement')
        retired=read(proof['abandoned_claim'])
        must(set(retired)=={'schema_version','claim_id','workbook','owner','version','evidence','abandoned_at'}
            and retired['schema_version']=='fleet-abandoned-claim/v1'
            and retired['claim_id']==intent['claim_id'] and retired['workbook']==old_job['workbook']
            and retired['owner']==intent['owner'] and retired['version']==old_job['version']
            and retired['evidence']==proof['native_inventory']
            and type(retired['abandoned_at'])in(int,float) and math.isfinite(retired['abandoned_at'])
            and retired['abandoned_at']>=inventory['at'],'claim retirement differs from stopped original request')
        if live is not None:
            must(abandoned_claims is not None and [r for r in abandoned_claims if r.get('claim_id')==intent['claim_id']]==[retired],
                'current coordinator must retain exact abandoned claim')
    state=proof['workbook']
    must(state['state']=='ready'and state['owner']is state['claim_id']is state['expires']is None
        and state['fence']==p['expected_fence']and state['workbook']==job['workbook']and state['source_sha256']==job['source']['sha256']
        and state['version']==p['expected_version']and json.loads(state['reason'])==launch['intent']
        and json.loads(state['checkpoint'])=={'continuation':old['root_plan']},'ready custody differs from actual prior root reconciliation')
    must(type(proof['at'])in(int,float)and proof['at']>=inventory['at']and intent['created_at_ns']/1e9<=inventory['at'], 'claim absence chronology differs')
    if live is not None:
        must(all(live.get(k)==v for k,v in state.items())and live.get('token')is None,'ready source changed since claim-absence proof')
    if native:
        worker=Path(old_job['run']);must(sorted(str(x.relative_to(worker))for x in worker.rglob('*')if x.is_file())==['intent.json']
            and not Path(old_job['result']).parent.exists(),'original unclaimed worker gained effects')
    return old_job,process


def preclaim_archive_successor(p,old,oldjob,proof,read):
    """Authenticate the sole optional archive-reader successor before any claim."""
    import ast,hashlib,json
    from pathlib import Path
    def must(ok,msg):
        if not ok:raise ValueError(msg)
    c=read(proof['successor']);must(set(c)=={'schema_version','original_task','selected_task','native_validation','authorization'}and c['schema_version']=='fleet-preclaim-semantic-archive-successor/v1','closed preclaim archive successor required')
    must(c['original_task']==old['root_plan']and c['selected_task']==p['root_plan'],'preclaim successor task differs')
    prior=read(c['original_task']);selected=read(c['selected_task'])
    must(prior['schema_version']==selected['schema_version']=='fleet-instruction-unacquired-model-continuation/v1'
        and 'semantic_archive'not in prior and set(selected)==set(prior)|{'semantic_archive'}
        and all(selected[k]==v for k,v in prior.items()if k!='helper'),'preclaim archive successor changed owning task authority')
    def tree(row):
        path=Path(row['path']);raw=path.read_bytes();must(hashlib.sha256(raw).hexdigest()==row['sha256'],'successor code changed');return ast.parse(raw)
    before=tree(prior['helper']);after=tree(selected['helper']);changes=[]
    # Remove only the declared optional archive import and its scoped dispatch.
    class Original(ast.NodeTransformer):
        def visit_Set(self,node):
            if any(isinstance(x,ast.Constant)and x.value=='semantic_archive'for x in node.elts):
                must('renewal_before_work'not in selected,'archive successor cannot select another new mode')
                node.elts=[x for x in node.elts if not(isinstance(x,ast.Constant)and x.value=='renewal_before_work')] 
                node.elts=[x for x in node.elts if not(isinstance(x,ast.Constant)and x.value=='semantic_archive')];changes.append('field')
            return self.generic_visit(node)
        def visit_Assign(self,node):
            if len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                name=node.targets[0].id
                if name=='semantic'and isinstance(node.value,ast.Constant)and node.value.value is None:changes.append('init');return None
                if name=='value'and isinstance(node.value,ast.IfExp)and ast.unparse(node.value)=='semantic if semantic is not None and row == p[\'semantic_archive\'][\'original\'] else original_module(name, row)':
                    changes.append('module');node.value=ast.parse('original_module(name,row)',mode='eval').body
            return self.generic_visit(node)
        def visit_If(self,node):
            test=ast.unparse(node.test)
            if test in ("'semantic_archive' in p","row == source['helper'] and semantic is not None"):
                expected={"'semantic_archive' in p": '96e822649a6026d08e5792c33f6d82e64aa87532c24a108eefb86689f71bc057', "row == source['helper'] and semantic is not None": '318838171bd71c63ef55be0f88ab34e00c45f24b4b2fdd4572969cda1fc63c02'}
                must(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()==expected[test],'archive substitution body differs from reviewed import scope')
                changes.append('scope'if test.startswith('row')else'selection');return None
            return self.generic_visit(node)
    after=Original().visit(after)
    must(sorted(changes)==sorted(['field','init','module','scope','selection'])and ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False),'successor changed code beyond the exact optional archive selection')
    archive=selected['semantic_archive'];parent=read(prior['previous'])
    must(set(archive)=={'original','selected'}and archive['original']==parent['semantic_archive']['helper'],'successor archive is not original selected historical reader')
    tree(archive['original']);tree(archive['selected'])
    auth=Path(c['authorization']['path']);must(hashlib.sha256(auth.read_bytes()).hexdigest()==c['authorization']['sha256']and c['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0','explicit source continuation authorization required')
    validation=read(c['native_validation']);must(validation['status']=='ACTUAL_COLD_ORIGINAL_RENEWAL_FIRST_WRITE_PASS'and validation['owning_plan']==p['root_plan']and validation['models_dispatched']==validation['task_mutations']==0,'actual successor cold renewal validation required')
    checked_probe=read(p['probe']);must(checked_probe['status']=='PASS'and checked_probe['models_dispatched']==0 and checked_probe['result']['plan']==p['root_plan']and checked_probe['result']['native_validation']==c['native_validation'],'root successor probe differs')
    command=[selected['helper']['path']if x==prior['helper']['path']else p['root_plan']['path']if x==old['root_plan']['path']else p['root_plan']['sha256']if x==old['root_plan']['sha256']else x for x in oldjob['command']]
    inputs=[p['root_plan']if x==old['root_plan']else selected['helper']if x==prior['helper']else x for x in oldjob['inputs']]
    for row in archive.values():
        if row not in inputs:inputs.append(row)
    return dict(oldjob,command=command,driver=selected['helper'],inputs=inputs)


def preclaim_ready_selection(p,job,*,check=None,live=None,native=False):
    """A new bounded admission after a proved pre-spawn ready transition."""
    import hashlib,json,time
    from pathlib import Path
    def read(row):
        if check is not None:return check(row)
        path=Path(row['path']);raw=path.read_bytes()
        if set(row)!={'path','sha256'}or any(x.is_symlink()for x in(path,*path.parents))or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('preclaim evidence changed')
        return json.loads(raw)
    def must(ok,msg):
        if not ok:raise ValueError(msg)
    proof=read(p['preclaim_ready_selection'])
    must(set(proof)=={'schema_version','prior_intent','prior_ready','prior_uncertain','native_absence','claims','events','event_high_water','at','database','workbook'},'closed preclaim ready proof required')
    must(proof['schema_version']=='fleet-preclaim-ready-selection/v1'and proof['claims']==proof['events']==[],'actual preclaim absence required')
    intent=read(proof['prior_intent']);ready=read(proof['prior_ready']);uncertain=read(proof['prior_uncertain']);old=read(intent['plan']);oldjob=read(old['job']);absence=read(proof['native_absence'])
    journal=Path(proof['prior_intent']['path']).parent
    must(proof['prior_ready']['path']==str(journal/'ready.json')and proof['prior_uncertain']['path']==str(journal/'uncertain.json')
        and uncertain==dict(intent=proof['prior_intent'],phase='launch',status='RECONCILIATION_REQUIRED',failure_type='RuntimeError',automatic_retry=False)
        and intent['automatic_retry']is False,'original pre-spawn launch failure differs')
    must(set(p)-{'preclaim_ready_selection'}==set(old)and all(p[k]==v for k,v in old.items()if k not in{'job','remote_receipt','native_lease_runway','offline_bindings'}),'preclaim selection changed task authority')
    previous=read(old['native_lease_runway']);selected=read(p['native_lease_runway'])
    must(set(previous)==set(selected)and all(selected[k]==v for k,v in previous.items()if k!='required_seconds'),'preclaim runway identity changed')
    must(type(selected['required_seconds'])is int and 60<=selected['required_seconds']<=previous['required_seconds'],'bounded smaller renewal-first runway required')
    inputs=[p['native_lease_runway']if x==old['native_lease_runway']else x for x in oldjob['inputs']]
    must(oldjob['inputs'].count(old['native_lease_runway'])==1 and job==dict(oldjob,run=job['run'],inputs=inputs)
        and job['run']!=oldjob['run']and Path(job['run']).parent==Path(oldjob['run']).parent and p['remote_receipt']!=old['remote_receipt'],'only fresh worker/process and runway input may change')
    expected={oldjob['run']:False,str(Path(oldjob['result']).parent):False,old['remote_receipt']:False,str(Path(old['remote_receipt']).with_suffix('.log')):False}
    must(absence['status']=='ORIGINAL_NATIVE_PRESPAWN_BOUNDARY_UNCONSUMED'and absence['absent']==expected and absence['processes']==[]
        and absence['models']==absence['claims']==0 and absence['tracker']==previous['tracker'],'native pre-spawn boundary differs')
    state=proof['workbook']
    must(all(state.get(k)==v for k,v in ready.items()if k!='token')and ready['state']=='ready'and ready['owner']is ready['claim_id']is ready['expires']is None
        and ready['fence']==p['expected_fence']and ready['workbook']==job['workbook']and ready['source_sha256']==job['source']['sha256']and ready['version']==p['expected_version']
        and json.loads(ready['reason'])==proof['prior_intent']and json.loads(ready['checkpoint'])=={'continuation':p['root_plan']},'ready source custody changed')
    must(proof['at']>=absence['at_ns']/1e9>=ready['updated'],'preclaim absence chronology differs')
    if live is not None:must(all(live.get(k)==v for k,v in state.items())and live.get('token')is None,'live preclaim ready custody changed')
    if native:
        must(all(not Path(path).exists()for path in expected),'old preclaim boundary gained an effect')
        read(previous['tracker'])
    return oldjob


def native_runway(selected,job_binding):
    """Exact current custody and bounded time, without renewing or replaying."""
    import hashlib,json,time
    from pathlib import Path
    def read(row):
        if not isinstance(row,dict) or set(row)!={'path','sha256'}:raise ValueError('closed runway binding required')
        path=Path(row['path'])
        if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('canonical runway evidence required')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('runway evidence changed')
        return json.loads(raw)
    profile=read(selected);job=read(job_binding)
    if set(profile)-{'maximum_seconds'}!={'schema_version','workbook','source_sha256','tracker','required_seconds'} or profile['schema_version']!='fleet-native-job-lease-runway/v1':raise ValueError('closed native runway profile required')
    seconds=profile['required_seconds']
    if type(seconds)is not int or not 60<=seconds<=14400:raise ValueError('bounded explicit native runway required')
    maximum=profile.get('maximum_seconds')
    if 'maximum_seconds' in profile and (type(maximum)is not int or not seconds<maximum<=14400):raise ValueError('bounded explicit maximum runway required')
    if (profile['workbook']!=job['workbook'] or profile['source_sha256']!=job['source']['sha256']
            or job['inputs'].count(selected)!=1):raise ValueError('runway detached from source job')
    tracker=read(profile['tracker']);lease=tracker['leases']['task_agent'];now=time.time_ns()
    source=tracker['raw_source']
    if 'original_raw_source' in tracker.get('bindings',{}):
        source=tracker['bindings']['original_raw_source']
        if not isinstance(source,dict) or set(source)!={'path','sha256'}:raise ValueError('closed original source binding required')
        original=Path(source['path'])
        if (not original.is_absolute() or any(x.is_symlink() for x in (original,*original.parents))
                or hashlib.sha256(original.read_bytes()).hexdigest()!=source['sha256']):raise ValueError('original source bytes changed')
    if (tracker['workbook_id']!=profile['workbook'] or source['sha256']!=profile['source_sha256']
            or not isinstance(lease,dict) or type(lease.get('expires_at'))is not int
            or lease['expires_at']-now<=seconds*10**9):raise ValueError('native lease has insufficient runway')
    if maximum is not None and lease['expires_at']-now>maximum*10**9:raise ValueError('native lease exceeds selected renewal-first window')
    if read(profile['tracker'])!=tracker:raise ValueError('native tracker changed during runway check')
    return dict(status='PASS',tracker=profile['tracker'],observed_at_ns=now,
        remaining_ns=lease['expires_at']-now,required_seconds=seconds,lease_mutations=0)


def remote_preflight(payload):
    import json,os,sys,shutil
    from pathlib import Path
    p=payload['plan'];job=payload['job']
    import hashlib
    def raw_checked(row):
        path=Path(row['path'])
        if any(x.is_symlink() for x in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('runtime bytes changed')
        return json.loads(path.read_bytes())
    runtime=raw_checked(p['runtime']);root=Path(runtime['root'])
    actual={str(x.relative_to(root)) for x in root.rglob('*') if x.is_file()}
    if actual!=set(runtime['files']):raise ValueError('runtime file inventory changed')
    for name,row in runtime['files'].items():
        if row['path']!=str(root/name):raise ValueError('runtime path differs')
        path=Path(row['path'])
        if any(x.is_symlink() for x in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('runtime dependency changed')
    sys.path.insert(0,str(root))
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
    if p['prior_kind']=='pool-completion':
        if process.get('job')!=intent['job'] or process.get('outcome')!=p['prior_outcome'] or process.get('returncode')!=0:raise ValueError('pool stopped custody changed')
        dispatch=checked(process['dispatch'])
        if dispatch.get('job')!=intent['job']:raise ValueError('pool dispatch job differs')
    needles=[intent['job']['path'],prior_job['run'],prior_job['result'],p['job']['path'],job['run'],job['result']]
    if p.get('unclaimed_start') is not None:
        unclaimed,stopped=unclaimed_start(p,job,native=True)
        needles.extend([unclaimed['run'],str(stopped['job']['path'])])
    if p.get('preclaim_ready_selection') is not None:
        unclaimed=preclaim_ready_selection(p,job,native=True);needles.extend([unclaimed['run'],unclaimed['result']])
    active=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            args=(entry/'cmdline').read_bytes().split(b'\0')
            command=' '.join(x.decode(errors='replace') for x in args)
            if (p['prior_kind']=='process' and int(entry.name)==process['pid']) or any(n in command for n in needles):active.append(int(entry.name))
        except (FileNotFoundError,ProcessLookupError):continue
    if active:raise ValueError('prior or new worker process still active; reconcile')
    runway={}
    if 'native_lease_runway'in p:runway['native_lease_runway']=native_runway(p['native_lease_runway'],p['job'])
    return {'status':'VALIDATED_NOT_STARTED','job':p['job'],'root_plan':p['root_plan'],'worker':p['worker'],
        'prior_outcome':p['prior_outcome'],'prior_process':p['prior_process'],'active_pids':[],'models_dispatched':0,**runway}


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
    if 'native_lease_runway'in payload:native_runway(payload['native_lease_runway'],payload['job'])
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
    code=function_source('preclaim_archive_successor')+'\n'+function_source('preclaim_ready_selection')+'\n'+function_source('unclaimed_start')+'\n'+function_source('native_runway')+'\n'+function_source('remote_preflight')+'\n'+function_source('remote_spawn')+'\n'
    code+='import json,sys\np=json.load(sys.stdin)\n'
    if function=='launch':code+="remote_preflight(p)\nprint(json.dumps(remote_spawn(p['launch'])))\n"
    else:code+='print(json.dumps(remote_preflight(p)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet',
        '--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:
        # Preserve the actual remote cause, including failures before a task
        # starts. Do not print stdout: it can contain a partial custody reply.
        import hashlib
        stderr=result.stderr
        raise RuntimeError('remote '+function+' failed (exit '+str(result.returncode)+
            '; stderr sha256 '+hashlib.sha256(stderr).hexdigest()+
            '); reconcile retained evidence before retry\n'+
            (stderr.decode('utf-8',errors='replace') if stderr else '[remote stderr was empty]'))
    return json.loads(result.stdout)


def admission_lock_path(p,receipt):
    if 'admission_lock'not in p:return Path(p['coordinator_receipt']['path']).parent/'job-launch.lock'
    import importlib.util
    row=p['admission_lock'];require(binding(row['path'])==row,'selected admission lock helper changed')
    spec=importlib.util.spec_from_file_location('selected_admission_lock',row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m.path(receipt)


def launch(row,journal,*,execute=False):
    p,job=load(row);receipt=checked(p['coordinator_receipt'])
    client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(admission_lock_path(p,receipt),blocking=False):
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
            ready=before if p.get('unclaimed_start') is not None or p.get('preclaim_ready_selection') is not None else client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'continuation':p['root_plan']})
            require(ready['state']=='ready' and ready['fence']==p['expected_fence'],'unexpected ready response; reconcile')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],
                '--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            payload['launch']={'receipt':p['remote_receipt'],'command':command,'job':p['job'],'root_intent':intent,'cwd':job['cwd']}
            if 'native_lease_runway'in p:payload['launch']['native_lease_runway']=p['native_lease_runway']
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
