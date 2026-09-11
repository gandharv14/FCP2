"""One guarded instruction step from an exact environment/worker checkpoint.

Root selects every checkpoint and capability. This coordinator never publishes,
retries a failure, rewrites a worker checkpoint, or resets a semantic budget.
"""
from dataclasses import asdict,replace
from pathlib import Path
import argparse,json,os,sys,time,fcntl,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import harbor_campaign as base
from scripts.batch import harbor_instruction_worker as worker
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_completed_lanes import CompletionEvidence,check_completed_gate
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_admission import read,record,require

ROOT=base.ROOT
SCHEMA='harbor-instruction-coordinator/v1'
RESUMABLE={'INSTRUCTION_READY','STEP_COMPLETE','COMMANDS_REQUIRED','MODEL_REQUIRED','RUNTIME_REQUIRED','RETRY_READY'}


def _continuations(retry, dialogue_retry, boundary_recovery, dialogue_semantic_retry=None):
    for value in (retry, dialogue_retry, boundary_recovery, dialogue_semantic_retry):
        require(value is None or (type(value) is dict and set(value)=={'path','sha256'}
            and isinstance(value['path'],str) and bool(value['path'])
            and isinstance(value['sha256'],str) and len(value['sha256'])==64
            and all(c in '0123456789abcdef' for c in value['sha256'])),
            'exact continuation receipt record required')
    require(dialogue_retry is None or boundary_recovery is None,
            'independent continuation kinds cannot be combined')


def _warning_history(semantic,repair,warning,revision=None,disclosure=None,renewal=None):
    if warning is None:return
    if semantic is None and repair is None:
        require(revision is not None,'warning requalification requires its semantic history')
        permit=read(warning)
        original_renewal=permit.get('prior_continuations',{}).get('lease_renewal')
        current=renewal;seen=set()
        while current!=original_renewal:
            require(current is not None and current['path'] not in seen and len(seen)<128,'warning detached later renewal history')
            seen.add(current['path']);complete=read(current);item=read(complete['permit'])
            require(complete.get('schema_version')==item.get('schema_version')=='harbor-instruction-lease-renewal/v1'
                    and item.get('prior_continuations',{}).get('warning_requalification')==warning,'warning changed outside owning renewal')
            current=item['prior_continuations'].get('lease_renewal')
        expected={k:v for k,v in dict(disclosure_revision=revision,disclosure_repair=disclosure,lease_renewal=original_renewal).items() if v is not None}
        require(permit.get('schema_version')=='harbor-dialogue-warning-requalification/v1' and permit.get('prior_continuations')==expected,'warning detached revised disclosure history')
    if semantic is not None and repair is not None:
        from scripts.batch.harbor_dialogue_semantic_retry import SCHEMA as SEMANTIC_SCHEMA
        completion=read(semantic);permit=read(completion['permit'])
        require(completion['schema_version']==permit['schema_version']==SEMANTIC_SCHEMA
                and permit['prior_continuations'].get('code_repair')==repair,
                'warning cannot combine detached semantic and repair histories')


def _disclosure_history(receipt,others):
    if receipt is not None:
        _continuations(None,None,None,receipt)
        # Later owning retry adapters bind this predecessor explicitly.
        for binding in others:
            if binding is None:continue
            saved=read(binding);permit=read(saved['permit']) if 'permit' in saved else saved
            require(permit.get('prior_continuations',{}).get('disclosure_repair')==receipt,
                    'disclosure repair cannot combine detached continuations')


def worker_encode(w):
    require(type(w) is worker.Worker,'typed installed instruction worker required')
    _continuations(w.retry,w.dialogue_retry,w.boundary_recovery,w.dialogue_semantic_retry)
    _continuations(None,None,None,w.pretransport_recovery)
    require(w.pretransport_recovery is None or (w.dialogue_semantic_retry is not None and type(w.pretransport_recovery) is dict and set(w.pretransport_recovery)=={'path','sha256'}),'invalid pretransport continuation binding')
    result = dict(context=dict(tracker_root=str(w.context.tracker_root),trusted_public_key=w.context.trusted_public_key),run=str(w.run),campaign=base.encode(w.campaign),root=str(w.root),retry=w.retry,dialogue_retry=w.dialogue_retry,boundary_recovery=w.boundary_recovery,dialogue_semantic_retry=w.dialogue_semantic_retry,pretransport_recovery=w.pretransport_recovery)
    for name in ('code_repair', 'warning_requalification', 'disclosure_repair', 'lease_renewal', 'disclosure_revision', 'dialogue_pre_review_repair'):
        value = getattr(w, name)
        _continuations(None, None, None, value)
        if value is not None:
            result[name] = value
    _disclosure_history(w.disclosure_repair,[w.retry,w.dialogue_retry,w.boundary_recovery,w.dialogue_semantic_retry,w.pretransport_recovery,w.code_repair,w.warning_requalification,w.disclosure_revision])
    _warning_history(w.dialogue_semantic_retry,w.code_repair,w.warning_requalification,w.disclosure_revision,w.disclosure_repair,w.lease_renewal)
    return result


def worker_decode(value):
    legacy={'context','run','campaign','root','retry'}
    fields = set(value) - {'code_repair', 'warning_requalification', 'disclosure_repair', 'lease_renewal', 'disclosure_revision', 'dialogue_pre_review_repair'}
    require(fields in (legacy,legacy|{'dialogue_retry','boundary_recovery'},legacy|{'dialogue_retry','boundary_recovery','dialogue_semantic_retry'},legacy|{'dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery'}) and set(value['context'])=={'tracker_root','trusted_public_key'},'closed instruction worker fields required')
    dialogue=value.get('dialogue_retry');boundary=value.get('boundary_recovery')
    semantic=value.get('dialogue_semantic_retry')
    pretransport=value.get('pretransport_recovery')
    require(pretransport is None or (semantic is not None and type(pretransport) is dict and set(pretransport)=={'path','sha256'}),'invalid pretransport continuation binding')
    _continuations(value['retry'],dialogue,boundary,semantic)
    _continuations(None,None,None,pretransport)
    repair=value.get('code_repair');warning=value.get('warning_requalification')
    for item in (repair, warning):_continuations(None,None,None,item)
    _continuations(None,None,None,value.get('lease_renewal'))
    _continuations(None,None,None,value.get('disclosure_revision'))
    _continuations(None,None,None,value.get('dialogue_pre_review_repair'))
    disclosure=value.get('disclosure_repair')
    _disclosure_history(disclosure,[value['retry'],dialogue,boundary,semantic,pretransport,repair,warning,value.get('disclosure_revision')])
    _warning_history(semantic,repair,warning,value.get('disclosure_revision'),disclosure,value.get('lease_renewal'))
    c=value['context'];return worker.Worker(RootContext(Path(c['tracker_root']),c['trusted_public_key']),Path(value['run']),base.decode(value['campaign']),Path(value['root']),value['retry'],dialogue,boundary,semantic,pretransport,code_repair=repair,warning_requalification=warning,disclosure_repair=disclosure,lease_renewal=value.get('lease_renewal'),disclosure_revision=value.get('disclosure_revision'),dialogue_pre_review_repair=value.get('dialogue_pre_review_repair'))


def selected(binding):
    d=read(binding)
    require(set(d)=={'schema_version','worker','controls','dependencies','origin','code','initial'} and d['schema_version']==SCHEMA,'closed instruction descriptor required')
    require(d['code']==record(__file__),'instruction coordinator code changed; explicit root reconciliation required')
    w=worker_decode(d['worker']);controls=base.decode(d['controls']);recipe=base.decode(d['dependencies'])
    require(type(recipe) is base.admission.DependencyRecipe,'root-selected dependency recipe required')
    base._controls(controls);base._campaign(w.campaign)
    require(w.campaign.model_slots<=recipe.maximum_models,'campaign exceeds dependency model capacity')
    for row in d['origin']:base.executor.checked_bytes(row)
    return w,controls,recipe


def dependency(recipe,directory):
    result=base.keep(directory/'dependency.json',base.admission.dependency_report(recipe,ROOT))
    base.admission.validate_dependency(result,recipe,ROOT)
    return result


def _result(status,descriptor,selection,lease,*,checkpoint=None,next_step=None,evidence=(),required_action=None):
    return dict(schema_version=SCHEMA,status=status,descriptor=descriptor,selection=codec.encode(selection),lease=base.encode(lease),checkpoint=checkpoint,next_step=next_step,evidence=list(evidence),required_action=required_action,publication_performed=False)


def _start(arguments,controls,directory):
    descriptor=arguments['environment_descriptor'];checkpoint=arguments['checkpoint']
    s,_,expected=base.selected(descriptor);require(expected==controls,'environment controls differ')
    prior=read(checkpoint)
    require(prior.get('descriptor')==descriptor and prior.get('status')=='ENVIRONMENT_READY','exact ENVIRONMENT_READY handoff required')
    lease=base.decode(prior['lease']);selection=codec.decode(prior['selection'])
    refresh=read(descriptor).get('refresh');refresh_evidence=[]
    if refresh is None:
        base.worker._setup(s,lease)
        actual=base.worker._read_checkpoint(s,'environment')
    else:
        from scripts.batch import harbor_environment_refresh as refreshed
        _,before=base.worker._active(s,lease)
        proof,initial=refreshed.validate(s,refresh,before)
        for result in proof['results']:
            require(all(before['bindings'].get(k)==val for k,val in result['bindings'].items() if k not in refreshed.MUTABLE),'tracker differs from refreshed prefix')
        root=Path(refresh['path']).parent
        committed=record(root/'committed.json')
        require(read(committed)['prepared']==refresh,'environment refresh was not committed')
        if proof['last']=='environment':actual=initial
        else:
            checkpoint_=record(root/'continuation/environment.json')
            saved=read(checkpoint_);actual=codec.decode(saved['selection'])
            for item in saved['evidence']:base.executor.checked_bytes(item)
            refresh_evidence.append(checkpoint_)
        refresh_evidence.extend([refresh,committed])
    require(codec.encode(actual)==prior['selection'],'environment result differs from owning checkpoint')
    for row in prior['evidence']:base.executor.checked_bytes(row)
    run=base.executor.safe(Path(arguments['worker_run']))
    protected=(s.run,s.bootstrap.run,s.bootstrap.policy.tracker_root,selection.content.stage,s.campaign.root,s.campaign.slot_root)
    require(all(run!=p and run not in p.parents and p not in run.parents for p in protected),'instruction run overlaps protected evidence')
    require(not run.exists(),'instruction work already exists; select exact prior checkpoint')
    dep=base._dependency(s,directory);lease=base._lease(s,lease,directory)
    w=worker.Worker(base.worker._context(s),run,s.campaign)
    _,before,_=worker.active(w,selection,lease)
    check_completed_gate(before,CompletionEvidence('environment',selection),w.context)
    bound=base.keep(directory/'descriptor.json',dict(schema_version=SCHEMA,worker=worker_encode(w),controls=base.encode(controls),dependencies=base.encode(s.bootstrap.policy.dependencies),origin=[descriptor,checkpoint,dep,*refresh_evidence],code=record(__file__),initial=dict(selection=codec.encode(selection),lease=base.encode(lease))))
    return _result('INSTRUCTION_READY',bound,selection,lease,evidence=[descriptor,checkpoint,dep,*refresh_evidence],next_step=worker.STEPS[0])


def _prior(binding,descriptor):
    value=read(binding)
    require(value.get('schema_version')==SCHEMA and value.get('descriptor')==descriptor,'checkpoint belongs to another instruction descriptor')
    require(value.get('status') in RESUMABLE,'instruction result requires explicit root reconciliation, not replay')
    require(value.get('publication_performed') is False,'unexpected publication claim')
    for row in value['evidence']:base.executor.checked_bytes(row)
    selection=codec.decode(value['selection']);lease=base.decode(value['lease'])
    require(type(lease) is base.bootstrap.Lease,'typed exact lease required')
    bound=value['checkpoint']
    if value['status']=='INSTRUCTION_READY':
        require(bound is None,'initial handoff cannot invent worker history')
        require(read(descriptor)['initial']==dict(selection=value['selection'],lease=value['lease']),'initial selection/lease differs from root descriptor')
    elif value['status']=='RETRY_READY':
        completion=read(bound)
        from scripts.batch.harbor_dialogue_warning_requalification import SCHEMA as WARNING_SCHEMA
        from scripts.batch.harbor_disclosure_review_resume import SCHEMA as DISCLOSURE_SCHEMA
        if completion.get('schema_version')=='harbor-dialogue-pre-review-repair/v1':
            configured=worker_decode(read(descriptor)['worker'])
            require(configured.dialogue_pre_review_repair==bound,'pre-review repair checkpoint detached from worker')
            permit=completion
        elif completion.get('schema_version') in {'harbor-disclosure-revision/v1','harbor-disclosure-manifest-revision/v1'}:
            configured=worker_decode(read(descriptor)['worker'])
            require(configured.disclosure_revision==bound,'disclosure revision checkpoint detached from worker')
            permit=completion
        elif completion.get('schema_version') == DISCLOSURE_SCHEMA:
            configured=worker_decode(read(descriptor)['worker'])
            require(configured.disclosure_repair==bound,'disclosure checkpoint detached from worker')
            permit=completion
        elif completion.get('schema_version') == WARNING_SCHEMA:
            configured = worker_decode(read(descriptor)['worker'])
            require(configured.warning_requalification == bound,
                    'warning checkpoint detached from configured worker')
            permit = completion
        else:
            permit=read(completion['permit'])
            if completion.get('schema_version')=='harbor-instruction-lease-renewal/v1':
                require(worker_decode(read(descriptor)['worker']).lease_renewal==bound,'renewal checkpoint detached from worker')
        require(completion['lease']==asdict(lease) and permit['selection']==value['selection'],'retry lease/selection differs')
    else:
        require(bound is not None,'completed/pending worker evidence missing')
        saved=read(bound)
        require(saved['selection']==value['selection'] and saved['lease']==asdict(lease),'checkpoint selection or lease changed')
        require(saved['status']==('completed' if value['status']=='STEP_COMPLETE' else value['status']),'worker status differs from coordinator result')
    return value,selection,lease



def _import_retry(arguments,controls,directory):
    """Consume an already authorized, fully validated retry; never authorize it."""
    descriptor=arguments['descriptor'];w,expected,recipe=selected(descriptor)
    require(expected==controls and w.retry is None,'retry requires unchanged original worker/controls')
    prior=read(arguments['checkpoint'])
    require(prior['descriptor']==descriptor and prior['schema_version']==SCHEMA and prior['status']=='NAT_REPAIR_REQUIRED','exact rejected writer handoff required')
    for row in prior['evidence']:base.executor.checked_bytes(row)
    selected_retry=replace(w,retry=arguments['retry_completion'])
    completion=read(selected_retry.retry);permit=read(completion['permit'])
    require(permit['blocked']==prior['checkpoint'] and permit['selection']==prior['selection'],'retry does not belong to selected rejected writer')
    blocked=read(prior['checkpoint'])
    require(blocked['status']=='NAT_REPAIR_REQUIRED' and blocked['lease']==asdict(base.decode(prior['lease'])) and blocked['selection']==prior['selection'],'rejected coordinator handoff changed')
    require(not worker.step_directory(selected_retry,'nat-writer').exists(),'remaining writer round already attempted; select its exact checkpoint')
    selection=codec.decode(permit['selection']);lease=base.bootstrap.Lease(**completion['lease'])
    _,before,_=worker.active(selected_retry,selection,lease)
    from scripts.batch.harbor_nat_retry import validate
    validated=validate(selected_retry,before)
    require(validated['lease']==asdict(lease) and validated['spent']==1 and validated['remaining']==1 and validated['limit']==2,'retry changed remaining semantic budget')
    current=read(descriptor)
    bound=base.keep(directory/'descriptor.json',{**current,'worker':worker_encode(selected_retry),'initial':dict(selection=codec.encode(selection),lease=base.encode(lease)),'origin':[*current['origin'],descriptor,arguments['checkpoint'],selected_retry.retry]})
    return _result('RETRY_READY',bound,selection,lease,checkpoint=selected_retry.retry,next_step='nat-writer',evidence=[descriptor,arguments['checkpoint'],selected_retry.retry])


def _step(arguments,controls,directory,*,models,commands,containers):
    descriptor=arguments['descriptor'];w,expected,recipe=selected(descriptor)
    require(expected==controls,'instruction controls changed')
    prior,selection,lease=_prior(arguments['checkpoint'],descriptor)
    _,before,_=worker.active(w,selection,lease)
    # The installed worker seals exact predecessor lease bytes. Renewing a CAS
    # here would break that proof chain. Return before side effects instead.
    remaining=before['leases']['task_agent']['expires_at']-time.time_ns()
    if remaining<=(base.SECONDS+30)*10**9:
        return _result('LEASE_RENEWAL_REQUIRED',descriptor,selection,lease,checkpoint=prior['checkpoint'],next_step=prior['next_step'],evidence=[arguments['checkpoint']],required_action='Root must renew the same owner/token through an explicit worker checkpoint reconciliation; no instruction work was consumed.')
    dep=dependency(recipe,directory);credential=None
    if models:
        require(controls.env_file is not None,'explicit root-selected credential file required')
        from xl_task_build import read_env_key
        credential=read_env_key(controls.env_file);require(bool(credential),'model credential unavailable')
    result=worker.step(w,selection,lease,checkpoint=prior['checkpoint'],models=models,commands=commands,containers=containers,credential=credential)
    require(result.publication_performed is False,'instruction worker unexpectedly published')
    return _result(result.status,descriptor,result.selection,result.lease,checkpoint=result.evidence,next_step=result.next_step,evidence=[arguments['checkpoint'],dep,result.evidence])


def child(intent):
    value=read(intent)
    require(set(value)=={'schema_version','action','arguments','controls','models','commands','containers','code','base_code','executable','memory_limit_bytes','timeout_seconds'} and value['schema_version']==SCHEMA,'closed instruction launch intent required')
    require(value['code']==record(__file__) and value['base_code']==record(base.__file__) and value['executable']==record(Path(sys.executable).resolve()),'coordinator/executable changed after intent')
    require(value['memory_limit_bytes']==base.MEMORY and value['timeout_seconds']==base.SECONDS and all(type(value[k]) is bool for k in ('models','commands','containers')),'instruction guard/capabilities changed')
    directory=Path(intent['path']).parent;require(record(directory/'intent.json')==intent,'foreign launch intent')
    controls=base.decode(value['controls']);base._controls(controls)
    if value['action']=='start':
        require(not any(value[k] for k in ('models','commands','containers')),'start cannot execute instruction effects')
        result=_start(value['arguments'],controls,directory)
    elif value['action']=='import-retry':
        require(not any(value[k] for k in ('models','commands','containers')),'retry import cannot dispatch')
        result=_import_retry(value['arguments'],controls,directory)
    else:
        require(value['action']=='step','unknown instruction action')
        result=_step(value['arguments'],controls,directory,**{k:value[k] for k in ('models','commands','containers')})
    return base.keep(directory/'result.json',result)


def run(action,arguments,controls,directory,*,models=False,commands=False,containers=False,guardian_lock_fds=()):
    """A fresh, guarded single-workbook operation with explicit capabilities."""
    guardian_lock_fds=base._guardian_fds(guardian_lock_fds)
    require(action in {'start','step','import-retry'} and all(type(v) is bool for v in (models,commands,containers)),'explicit instruction action/capabilities required')
    require(action=='step' or not any((models,commands,containers)),'start cannot execute models/commands/containers')
    require(set(arguments)=={'start':{'environment_descriptor','checkpoint','worker_run'},'step':{'descriptor','checkpoint'},'import-retry':{'descriptor','checkpoint','retry_completion'}}[action],'exact root arguments required')
    for key,binding in arguments.items():
        if key!='worker_run':base.executor.checked_bytes(binding)
    expected=base.selected(arguments['environment_descriptor'])[2] if action=='start' else selected(arguments['descriptor'])[1]
    require(expected==controls,'root-selected controls changed')
    if models:require(controls.env_file is not None,'explicit credential file required before dispatch')
    lockpath=base._controls(controls);directory=base.executor.safe(directory)
    require(not directory.exists(),'attempt exists; retain and select exact completed checkpoint')
    require(directory!=lockpath.parent and directory not in lockpath.parents,'journal overlaps shared slot')
    require(shutil.disk_usage(ROOT).free>=base.MEMORY,'8-GiB free-space reserve required')
    fd=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);directory.mkdir(parents=True,mode=0o700)
        intent=base.keep(directory/'intent.json',dict(schema_version=SCHEMA,action=action,arguments=arguments,controls=base.encode(controls),models=models,commands=commands,containers=containers,code=record(__file__),base_code=record(base.__file__),executable=record(Path(sys.executable).resolve()),memory_limit_bytes=base.MEMORY,timeout_seconds=base.SECONDS))
        metrics={};rc=None;failure=None;logpath=directory/'step.log'
        try:
            with logpath.open('xb') as log:
                rc=base._run_in_process_group([sys.executable,'-B',str(Path(__file__).resolve()),'_child',intent['path'],intent['sha256']],stdout=log,timeout_seconds=base.SECONDS,grace_seconds=3,memory_limit_bytes=base.MEMORY,metrics=metrics,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT)},guardian_lock_fds=(fd,*guardian_lock_fds))
                log.flush();os.fsync(log.fileno())
        except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=base.executor.sha(str(exc).encode()))
        resource=base.keep(directory/'resource.json',dict(intent=intent,returncode=rc,metrics=metrics,log=record(logpath),failure=failure))
        if rc!=0 or failure or not (directory/'result.json').is_file():
            return base.keep(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,automatic_retry=False,terminal_verdict=None,source_invalidity_claimed=False))
        result=record(directory/'result.json');read(result)
        return base.keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=resource,publication_performed=False))
    finally:os.close(fd)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv and argv[0]=='_child':
        require(len(argv)==3,'exact child record required');child(dict(path=argv[1],sha256=argv[2]));return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('start','step','import-retry'))
    for field in ('environment-descriptor','descriptor','checkpoint','retry-completion'):
        parser.add_argument('--'+field);parser.add_argument('--'+field+'-sha256')
    parser.add_argument('--worker-run');parser.add_argument('--journal',required=True);parser.add_argument('--slot-lock',required=True);parser.add_argument('--env-file')
    for flag in ('models','commands','containers'):parser.add_argument('--dispatch-'+flag,action='store_true')
    args=parser.parse_args(argv);arguments={}
    for field in ('environment_descriptor','descriptor','checkpoint','retry_completion'):
        path,sha=getattr(args,field),getattr(args,field+'_sha256');require((path is None)==(sha is None),'path and explicit hash must be paired')
        if path is not None:arguments[field]=dict(path=path,sha256=sha)
    if args.worker_run is not None:arguments['worker_run']=args.worker_run
    outcome=run(args.action,arguments,base.Controls(Path(args.slot_lock),None if args.env_file is None else Path(args.env_file)),Path(args.journal),models=args.dispatch_models,commands=args.dispatch_commands,containers=args.dispatch_containers)
    print(json.dumps(outcome),flush=True);return 0

if __name__=='__main__':raise SystemExit(main())
