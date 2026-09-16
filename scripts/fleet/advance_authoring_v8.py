"""Fleet fresh authoring with durable single-partition progress boundaries.

Prospective successor of diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py.
The original driver and every in-flight snapshot remain unchanged.

Never performs fresh admission or migrates historical attempts. Only successful
owning outcomes may continue. Every failure is retained without automatic replay.
"""
from pathlib import Path
from dataclasses import replace
import argparse,contextlib,fcntl,hashlib,json,os,sys,time,importlib.util,shutil,errno,traceback
BASE=Path("/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-resume-20260910")
AUTHOR=BASE/'runner/author_workbook.py'
if hashlib.sha256(AUTHOR.read_bytes()).hexdigest()!='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666':
    raise ValueError('original authoring helper changed')
sys.path.insert(0,str(BASE/"runner"))
from author_workbook import binding,checked,retain,imports,code_snapshot
FLEET=BASE/'root'/'authoring-fleet-slots'
MODEL=BASE/'root'/'model-slots'
QUEUE_LOCK=BASE/'root'/'publication.lock'
VERIFIER={'path':str(BASE/'runner/verify_published.py'),'sha256':'bde9ad5007778a9fab80182ddfc47f7a90dd48bb2b76c683fc547c1f56b1ebb2'}
SCHEMA='harbor-queue-dispatch/v6'
WAIT_SCHEMA='harbor-publication-lock-wait/v1'

def require(value,message):
    if not value:raise ValueError(message)

@contextlib.contextmanager
def fleet_slot(workbook=None):
    FLEET.mkdir(parents=True,exist_ok=True);chosen=None;book=None
    try:
        if workbook is not None:
            require(len(workbook)==4 and workbook.isdigit(),'closed workbook identity required')
            book=os.open(FLEET/(workbook+'.job.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            fcntl.flock(book,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for number in range(2):
            fd=os.open(FLEET/f'{number}.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:os.close(fd);continue
            chosen=fd;break
        require(chosen is not None,'fleet capacity occupied; no job started')
        yield (chosen,book) if book is not None else (chosen,)
    finally:
        if chosen is not None:os.close(chosen)
        if book is not None:os.close(book)

def queue_item(queue,workbook):
    require(queue['schema_version']=='authoring-request-queue/v1','unknown queue')
    items=queue['items'];ids=[x['workbook_id'] for x in items]
    require(len(ids)==len(set(ids)) and len(items)==queue['counts']['total'],'queue identity/count mismatch')
    selected=[x for x in items if x['workbook_id']==workbook]
    require(len(selected)==1,'workbook absent from frozen queue')
    item=selected[0]
    require(item['history_class'] in {'no_history','deterministic_rebuild_only','legacy_model_artifacts'},'unknown queue history class')
    return item

def history_authority(item,intake,inventory,root):
    policy=intake.bootstrap.policy;wb=intake.bootstrap.workbook
    candidate=checked(intake.bootstrap.candidate_manifest)
    require(intake.bootstrap.candidate_manifest in policy.campaign_evidence,'candidate missing from admission policy')
    rows=[row for row in candidate['selected_candidates'] if row['original']['workbook_id']==wb]
    require(len(rows)==1 and rows[0]['original']==item['original_source'],'queue/candidate source mismatch')
    proof=rows[0].get('pre_audit_history')
    if item['history_class']=='no_history':
        require(proof is None,'no-history queue cannot silently acquire migration authority')
        return None
    require(proof is not None and proof in policy.campaign_evidence,'historical admission requires exact typed pre-audit proof in policy')
    from scripts.batch import harbor_pre_audit_history as H
    require(Path(H.__file__).resolve().is_relative_to(root),'foreign history validator')
    original={k:item['original_source'][k] for k in ('path','sha256')}
    value=H.validate(proof,workbook=wb,original=original,history_roots=policy.history_roots,known_ids={r['workbook_id'] for r in inventory['sources']})
    expected=[{k:r[k] for k in ('path','sha256')} for r in item['preserved_curations']]
    require(value['original']==original and value['curations']==expected,'history proof differs from immutable queue source/curations')
    return proof

def setup(job):
    require(set(job)-{'publication_wait'}=={'schema_version','driver','queue','config','outcome','operations','maximum_steps','models','commands','containers','publish'},'closed dispatcher job required')
    require(job['schema_version']==SCHEMA and job['driver']==binding(__file__),'dispatcher bytes changed')
    require(type(job['maximum_steps']) is int and 1<=job['maximum_steps']<=100,'bounded operation count required')
    require(all(type(job[k]) is bool for k in ('models','commands','containers','publish')),'capability flags must be booleans')
    config=checked(job['config']);item=queue_item(checked(job['queue']),config['workbook'])
    # Isolated renewal children do not inherit their parent's import path.
    # Select the hash-bound configuration's root before importing its helpers.
    snapshot=checked(config['code']);root=Path(snapshot['root']);sys.path.insert(0,str(root))
    from scripts.fleet.snapshot_inventory import equivalent
    require(equivalent(code_snapshot(root),snapshot),'frozen code changed')
    base,C,P=imports(root)
    require(Path(C.__file__).resolve().is_relative_to(root) and base.SECONDS==3300 and base.MEMORY==8*1024**3,'current guarded code required')
    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as R
    require(all(Path(m.__file__).resolve().is_relative_to(root) for m in (base,C,P,W,R)),'cached foreign implementation')
    controls=base.decode(config['controls']);intake=base.decode(checked(config['intake']))
    require(intake.campaign.model_slots==1 and intake.campaign.slot_root==MODEL,'shared model capacity differs')
    require(Path(config['queue_lock'])==QUEUE_LOCK,'publication lock differs')
    require(Path(controls.slot_lock)==BASE/'root'/'workbook-slots'/config['workbook']/'workbook.lock','canonical workbook lock differs')
    require(intake.bootstrap.workbook==config['workbook'],'intake workbook differs')
    inventory=base.admission.read_inventory(intake.bootstrap.policy.original_inventory['path'])
    require(next(x for x in inventory['sources'] if x['workbook_id']==config['workbook'])==item['original_source'],'queue source differs from admitted inventory')
    history_authority(item,intake,inventory,root)
    return base,C,P,W,R,config,root,controls

def successful(outcome,env=None):
    value=checked(outcome)
    require(value.get('status')=='COMPLETED_OPERATION' and value.get('automatic_retry',False) is False,'partial or uncertain prior operation requires reconciliation')
    directory=Path(outcome['path']).parent
    require(value['resource']==binding(directory/'resource.json') and value.get('result')==binding(directory/'result.json'),'guard result is not its own immutable sibling')
    resource=checked(value['resource'])
    require(resource['returncode']==0 and resource['failure'] is None,'prior guard failed')
    require(resource['intent']==binding(directory/'intent.json') and resource['log']==binding(directory/'step.log'),'prior guard evidence changed')
    if 'result' in resource:require(resource['result']==value['result'],'resource/result mismatch')
    intent=checked(resource['intent']);result=checked(value['result'])
    if env is not None:
        base,C,P,W,R,config,root,controls=env
        if intent.get('schema_version') in ('harbor-coordinator-step/v1',C.SCHEMA):
            module=C if intent['schema_version']==C.SCHEMA else base
            require(intent['code']==binding(module.__file__) and intent['controls']==base.encode(controls),'guard code or controls differ')
            seconds=C.guard_seconds(intent['action'],intent['arguments']) if module is C and hasattr(C,'guard_seconds') else base.SECONDS
            require(intent['memory_limit_bytes']==base.MEMORY and intent['timeout_seconds']==seconds,'guard resource contract differs')
            args=intent['arguments'];action=intent['action']
            if action=='step':
                require(result['descriptor']==args['descriptor'],'step returned foreign descriptor')
                if module is C:require(result['evidence'][0]==args['checkpoint'],'instruction result detached from prior')
            elif action in ('admit','start','import-retry'):
                require(result['descriptor']==binding(directory/'descriptor.json'),'new descriptor not owned by guard')
                descriptor=checked(result['descriptor'])
                if action=='admit':
                    require(args=={'intake':config['intake']} and result['status']=='ADMITTED','foreign admission')
                    require(descriptor['origin']==result['evidence'] and result['lease']==descriptor['initial_lease'],'admission evidence differs')
                else:
                    require(args['checkpoint'] in descriptor['origin'] and args['checkpoint'] in result['evidence'],'new descriptor lost prior checkpoint')
            else:raise ValueError('unsupported prior coordinator action')
        else:
            require(set(intent)=={'job','checkpoint','seconds','driver'} and intent['driver']==binding(__file__),'unsupported custom outcome authority')
            require(result['descriptor']==binding(directory/'descriptor.json'),'renewal descriptor not owned by guard')
            if result['status']=='INSTRUCTION_READY':
                original=binding(directory/'initial-lease'/'result.json');renewed=checked(original)
                initial_intent=checked(binding(directory/'initial-lease'/'intent.json'))
                require(initial_intent['schema_version']=='fleet-initial-lease-continuation/v1'
                    and initial_intent['authority']==resource['intent'] and initial_intent['held']==intent['checkpoint']
                    and intent['checkpoint']in renewed['evidence'] and result==dict(renewed,descriptor=result['descriptor'],evidence=[*renewed['evidence'],original])
                    and checked(result['descriptor'])==checked(renewed['descriptor']), 'initial renewal result detached')
            else:
                require(result['status']=='RETRY_READY' and result['evidence'][0]==intent['checkpoint'],'renewal result detached')
        if result.get('schema_version')==C.SCHEMA:
            C.selected(result['descriptor'])
            if result['status']=='PUBLICATION_READY':publication_request(env,result)
            elif result['status']!='LEASE_RENEWAL_REQUIRED':C._prior(value['result'],result['descriptor'])
        else:
            base.selected(result['descriptor'])
            normalization_progress(env,result)
    return value['result'],result



def normalization_progress(env,value):
    """Inspect durable progress; the next guarded stage owns full revalidation.

    PENDING_MODEL is the existing coordinator continuation status, never a gate
    pass. A progress receipt does not permit retrying any recorded model call.
    """
    if value['status']!='PENDING_MODEL' or value.get('step')!='normalization_phase1':return
    from scripts.batch import harbor_normalization_routing as routing
    base=env[0];rows=[]
    for item in value['evidence']:
        saved=checked(item)
        if saved.get('schema_version')==routing.PROGRESS_SCHEMA:rows.append(item)
    if not rows:return  # Inspection-only pending request; models may be disabled.
    require(len(rows)==1,'ambiguous normalization progress')
    proof=routing.check_progress(rows[0])
    worker,_,_=base.selected(value['descriptor'])
    prepared=routing.P.selection(proof['prepared'])
    require(prepared.budget==worker.normalization_budget and prepared.workbook==worker.bootstrap.workbook,
            'normalization progress belongs to another original budget')
    require(worker.campaign.model_slots==1,'normalization progress changed global model limit')
    require(base.SECONDS==3300 and base.MEMORY==8*1024**3,'normalization progress changed stage guards')


def publication_request(env,value):
    base,C,P,W,R,config,root,controls=env
    require(value['schema_version']==C.SCHEMA and value['status']=='PUBLICATION_READY' and value['publication_performed'] is False and value['next_step'] is None and value['required_action'] is None,'exact publication terminal state required')
    worker,recipe=selected(env,value)
    for row in value['evidence']:checked(row)
    require(value['checkpoint']==binding(W.step_directory(worker,'enqueue')/'complete.json'),'foreign enqueue checkpoint')
    request=P.Request(C.codec.decode(value['selection']),base.decode(value['lease']),value['checkpoint'],worker.context.tracker_root,Path(config['publication_root']),Path(config['output']),QUEUE_LOCK,controls.slot_lock)
    complete=P._checkpoint(request)
    require(checked(complete['intent'])['initial']==binding(worker.run/'initial.json'),'enqueue initial worker differs')
    return request

def selected(env,value):
    base,C,P,W,R,config,root,controls=env
    worker,expected,recipe=C.selected(value['descriptor'])
    require(worker.root==root and expected==controls and worker.run==Path(config['run_root'])/'instruction','instruction selection differs')
    require(worker.campaign.model_slots==1 and worker.campaign.slot_root==MODEL,'instruction model pool differs')
    return worker,recipe

def renewal_context(env,checkpoint):
    base,C,P,W,R,config,root,controls=env;held=checked(checkpoint)
    require(held['status']=='LEASE_RENEWAL_REQUIRED' and len(held['evidence'])==1,'owning renewal hold required')
    worker,recipe=selected(env,held);prior,s,lease=C._prior(held['evidence'][0],held['descriptor'])
    require(all(prior[k]==held[k] for k in ('checkpoint','selection','lease','next_step')),'renewal requires exact completed boundary')
    if prior['status']=='INSTRUCTION_READY':
        require(held['checkpoint']is None and held['next_step']=='disclosure-build'
            and not worker.run.exists(), 'initial renewal requires an unstarted instruction handoff')
    else:
        require(prior['status']=='STEP_COMPLETE','renewal requires exact completed boundary')
        require(checked(held['checkpoint'])['status']=='completed','no pending-step renewal')
    W.active(worker,s,lease)
    return held,worker,recipe,s,lease

def renewal_child(intent):
    value=checked(intent);job=checked(value['job']);env=setup(job)
    base,C,P,W,R,config,root,controls=env
    require(set(value)=={'job','checkpoint','seconds','driver'} and value['driver']==binding(__file__) and type(value['seconds']) is int and 60<=value['seconds']<=900,'renewal intent differs')
    held,worker,recipe,s,lease=renewal_context(env,value['checkpoint']);directory=Path(intent['path']).parent
    dep=C.dependency(recipe,directory)
    if held['checkpoint']is None:
        from scripts.fleet import initial_lease_renewal
        original=initial_lease_renewal.renew(base,C,W,initial=held['evidence'][0],held=value['checkpoint'],
            controls=controls,directory=directory/'initial-lease',authority=intent,
            custody=lambda:W.active(worker,s,lease))
        renewed=checked(original)
        descriptor=retain(directory/'descriptor.json',checked(renewed['descriptor']))
        result=retain(directory/'result.json',dict(renewed,descriptor=descriptor,evidence=[*renewed['evidence'],original]))
        C.selected(descriptor);C._prior(result,descriptor)
        return
    completion=R.authorize(worker,s,lease,held['checkpoint'],config['code'])
    from scripts.batch.harbor_workbook_bootstrap import Lease
    current=Lease(**checked(completion)['lease']);renewed=replace(worker,lease_renewal=completion)
    permit=checked(checked(completion)['permit'])
    descriptor=retain(directory/'descriptor.json',dict(schema_version=C.SCHEMA,worker=C.worker_encode(renewed),controls=base.encode(controls),dependencies=base.encode(recipe),origin=[held['descriptor'],value['checkpoint'],value['job'],dep,completion],code=binding(C.__file__),initial=dict(selection=C.codec.encode(s),lease=base.encode(current))))
    result=retain(directory/'result.json',C._result('RETRY_READY',descriptor,s,current,checkpoint=completion,next_step=permit['phase'],evidence=[value['checkpoint'],dep,completion]))
    C.selected(descriptor);C._prior(result,descriptor)

def renew(job_binding,env,checkpoint,directory,fleet_fd):
    base,C,P,W,R,config,root,controls=env;_,worker,recipe,s,lease=renewal_context(env,checkpoint)
    fd=os.open(base._controls(controls),os.O_RDWR|os.O_NOFOLLOW)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        seconds=min(900,int((W.active(worker,s,lease)[1]['leases']['task_agent']['expires_at']-time.time_ns())//10**9)-30)
        require(seconds>=60,'insufficient active lease for guarded renewal')
        directory.mkdir();intent=retain(directory/'intent.json',dict(job=job_binding,checkpoint=checkpoint,seconds=seconds,driver=binding(__file__)))
        rc=None;failure=None;metrics={}
        try:
            with (directory/'step.log').open('xb') as log:
                rc=base._run_in_process_group([sys.executable,'-I','-B',__file__,'renew-child',intent['path'],intent['sha256']],stdout=log,timeout_seconds=seconds,grace_seconds=3,memory_limit_bytes=base.MEMORY,metrics=metrics,cwd=root,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fd))
        except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
        resource=retain(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log')))
        result=binding(directory/'result.json') if rc==0 and failure is None and (directory/'result.json').exists() else None
        return retain(directory/'outcome.json',dict(status='COMPLETED_OPERATION' if result else 'RECONCILIATION_REQUIRED',resource=resource,result=result,automatic_retry=False))
    finally:os.close(fd)

def verify(job,env,operations):
    require(binding(VERIFIER['path'])==VERIFIER,'independent verifier changed')
    spec=importlib.util.spec_from_file_location('dispatcher_verifier',VERIFIER['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    old=sys.argv
    try:
        sys.argv=[VERIFIER['path'],'--config',job['config']['path'],'--config-sha256',job['config']['sha256'],'--report',str(operations/'independent-verification.json')];m.main()
    finally:sys.argv=old

def operation_namespace(config,value):
    path=Path(value);base_path=Path(config['run_root'])/'dispatcher-v2'
    require(path.is_absolute() and path.parent==base_path and path.name not in {'.','..'},'dispatcher operations must stay in dedicated workbook namespace')
    require(path.resolve()==path and base_path.resolve()==base_path,'symlink or parent escape in dispatcher namespace')
    require(not path.exists(),'dispatcher namespace exists; no replay')
    return path

def inspect_prior(outcome,env,ops):
    try:return successful(outcome,env)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        retain(ops/'hold.json',dict(outcome=outcome,automatic_retry=False,reason='prior evidence inspection failed',error_type=type(exc).__name__))
        return None

def publication_observation(env,request,directory):
    """Exact data-only state surrounding a failed pre-intent lock acquisition."""
    P=env[2]
    path,_,_=P._paths(request,directory)
    require(not directory.exists() and not directory.is_symlink(),'publication intent may exist; reconciliation required')
    raw,tracker=P._tracker(path)
    require(tracker['revision']==request.lease.revision and hashlib.sha256(raw).hexdigest()==request.lease.sha256,'publication tracker changed')
    require(tracker['leases']['publication'] is None,'publication reservation already exists')
    P._checkpoint(request)
    return dict(request=P.encode(request),tracker=binding(path),output=P._tree(request.output),publication=P._tree(request.publication_root),directory=str(directory))

def publication_lock_failure(exc,P):
    if not isinstance(exc,BlockingIOError) or exc.errno not in {errno.EAGAIN,errno.EWOULDBLOCK}:return False
    code=P.locks.__wrapped__.__code__
    tb=exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is code:return True
        tb=tb.tb_next
    return False

def validate_publication_wait(wait_binding,job,env):
    wait=checked(wait_binding)
    require(set(wait)=={'schema_version','status','driver','job','outcome','checkpoint','before','after','exception','automatic_retry'},'closed publication wait required')
    require(wait['schema_version']==WAIT_SCHEMA and wait['status']=='PUBLICATION_LOCK_WAIT' and wait['automatic_retry'] is False,'unsupported publication wait')
    require(wait['driver']==binding(__file__),'publication wait reader changed')
    old=checked(wait['job'])
    require(old['driver']==binding(__file__) and old['schema_version']==SCHEMA,'foreign wait job')
    require(all(old[k]==job[k] for k in ('queue','config','models','commands','containers','publish')),'wait continuation changed authority')
    require(job['outcome']==wait['outcome'],'wait continuation changed owning outcome')
    checkpoint,value=successful(wait['outcome'],env)
    require(checkpoint==wait['checkpoint'],'wait changed enqueue checkpoint')
    oldops=Path(old['operations'])
    require(oldops.is_absolute() and oldops.parent==Path(env[5]['run_root'])/'dispatcher-v2' and oldops.resolve()==oldops,'foreign prior operations namespace')
    require(oldops/'publication-lock-wait.json'==Path(wait_binding['path']) and binding(oldops/'job.json')==wait['job'],'wait detached from original operation namespace')
    require(wait['exception']==binding(oldops/'publication-lock-exception.json'),'foreign wait exception')
    failure=checked(wait['exception'])
    require(failure['type']=='BlockingIOError' and failure['errno'] in {errno.EAGAIN,errno.EWOULDBLOCK} and failure['pre_intent_lock_failure'] is True,'wait lacks exact lock failure')
    require(wait['before']==wait['after'],'publication state changed during failed acquisition')
    request=publication_request(env,value)
    observed=publication_observation(env,request,Path(wait['before']['directory']))
    require(observed==wait['before'],'publication state changed since wait')
    require(Path(observed['directory']).parent==oldops,'wait publication directory escaped original operations')
    return request

def publish_or_wait(job_binding,outcome,checkpoint,env,request,directory,ops,fleet_fd):
    P=env[2]
    before=publication_observation(env,request,directory)
    try:return P.run(request,directory,guardian_lock_fds=fleet_fd)
    except BlockingIOError as exc:
        if not publication_lock_failure(exc,P):raise
        failure=retain(ops/'publication-lock-exception.json',dict(type=type(exc).__name__,errno=exc.errno,pre_intent_lock_failure=True,traceback=traceback.format_exc()))
        after=publication_observation(env,request,directory)
        require(before==after,'publication state changed during lock failure; retain reconciliation')
        retain(ops/'publication-lock-wait.json',dict(schema_version=WAIT_SCHEMA,status='PUBLICATION_LOCK_WAIT',driver=binding(__file__),job=binding(ops/'job.json'),outcome=outcome,checkpoint=checkpoint,before=before,after=after,exception=failure,automatic_retry=False))
        return None

def run(job_binding):
    job=checked(job_binding);env=setup(job);base,C,P,W,R,config,root,controls=env
    outcome=job['outcome'];ops=operation_namespace(config,job['operations'])
    with fleet_slot(config['workbook']) as fleet_fd:
        if 'publication_wait' in job:validate_publication_wait(job['publication_wait'],job,env)
        ops.mkdir(parents=True);retain(ops/'job.json',job)
        for number in range(job['maximum_steps']):
            inspected=inspect_prior(outcome,env,ops)
            if inspected is None:return
            checkpoint,value=inspected
            status=value['status'];directory=ops/f'{number:03d}'
            instruction=value.get('schema_version')==C.SCHEMA
            if instruction:
                worker,recipe=selected(env,value)
                if status=='LEASE_RENEWAL_REQUIRED':outcome=renew(job_binding,env,checkpoint,directory,fleet_fd);continue
                if status=='PUBLICATION_READY':
                    if not job['publish']:break
                    request=publication_request(env,value)
                    published=publish_or_wait(job_binding,outcome,checkpoint,env,request,directory,ops,fleet_fd)
                    if published is None:return
                    retain(ops/'publication.json',dict(checkpoint=checkpoint,outcome=published))
                    if checked(published)['status']=='PROMOTED':verify(job,env,ops)
                    return
                C._prior(checkpoint,value['descriptor'])
                if status not in {'INSTRUCTION_READY','STEP_COMPLETE','RETRY_READY','MODEL_REQUIRED','COMMANDS_REQUIRED','RUNTIME_REQUIRED'}:break
                if (status=='MODEL_REQUIRED' and not job['models']) or (status=='COMMANDS_REQUIRED' and not job['commands']) or (status=='RUNTIME_REQUIRED' and not job['containers']):break
                outcome=C.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,directory,models=job['models'],commands=job['commands'],containers=job['containers'],guardian_lock_fds=fleet_fd)
            elif status=='ENVIRONMENT_READY':
                outcome=C.run('start',dict(environment_descriptor=value['descriptor'],checkpoint=checkpoint,worker_run=str(Path(config['run_root'])/'instruction')),controls,directory,guardian_lock_fds=fleet_fd)
            elif status=='RESOURCE_WAIT':
                prior_worker,_,_=base.selected(value['descriptor'])
                base._prior(checkpoint,value['descriptor'])
                if shutil.disk_usage(root).free<prior_worker.bootstrap.policy.dependencies.minimum_disk_bytes:break
                outcome=base.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint,captures=None),controls,directory,dispatch=job['models'],guardian_lock_fds=fleet_fd)
            elif status in {'ADMITTED','STEP_COMPLETE','PENDING_MODEL'}:
                if status=='PENDING_MODEL' and not job['models']:break
                outcome=base.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint,captures=None),controls,directory,dispatch=job['models'],guardian_lock_fds=fleet_fd)
            else:break
        retain(ops/'hold.json',dict(outcome=outcome,automatic_retry=False,reason='owning status or bounded limit requires continuation'))

if __name__=='__main__':
    if sys.argv[1]=='renew-child':renewal_child(dict(path=sys.argv[2],sha256=sys.argv[3]))
    elif sys.argv[1] in ('run','check'):
        b=dict(path=str(Path(sys.argv[2]).resolve()),sha256=sys.argv[3])
        if sys.argv[1]=='check':
            job=checked(b);env=setup(job);successful(job['outcome'],env)
            if 'publication_wait' in job:validate_publication_wait(job['publication_wait'],job,env)
            print('CHECK_PASSED')
        else:run(b)
    else:raise ValueError('explicit action required')
