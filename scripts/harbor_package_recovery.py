"""Explicit recovery of the completed direct-audit packaging integration failure.

No ordinary resume policy changes. Only a fresh current package or its complete,
independently revalidated receipt can advance the existing environment lane.
"""
from dataclasses import replace, asdict
from pathlib import Path
import argparse
import copy
import fcntl
import json
import os
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import harbor_campaign as C
from scripts.batch import harbor_environment_refresh as F
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_environment_producer as P
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_transition_codec import encode,decode

SCHEMA='harbor-direct-audit-package-recovery/v1'
PAUSE_SCHEMA='harbor-direct-audit-package-recovery/v2'
SOURCE_ID_SCHEMA='harbor-source-id-package-recovery/v1'
BEFORE_PRODUCER='623ae14732f6ab0b2c78ede9859cae3d759908ec118264fffda7b0cdd0653f74'
BEFORE_PACKAGER='3bd3a52b849956364275a51c890640648173a7c1737aac0c7b07fc92fbe634cb'
AFTER_PRODUCER='d1e695a9c39d8455e6011bab7553b5a2661414a918cbd4d44ede4fb59c21562f'
AFTER_PACKAGER='41faaa271988fcb4d8634de6ffa8f2a33a86623dafd23d216c24d9aa3b3242c6'
MEMORY=8*1024**3
SECONDS=900
ROOT=C.ROOT


def keep(path,value):return W.keep(path,value)


def source_runtime(integration):
    import importlib.util
    value=read(integration);row=value['helper'];E.checked_bytes(row)
    spec=importlib.util.spec_from_file_location('declared_package_source_id',row['path']);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    require(module.SCHEMA==SOURCE_ID_SCHEMA and record(module.__file__)==row,'declared source-ID integration differs')
    return module


def code(integration=None):
    if integration is not None:return source_runtime(integration).code(sys.modules[__name__],integration)
    require(record(P.__file__)['sha256']==AFTER_PRODUCER and record(ROOT/'xl_output_task.py')['sha256']==AFTER_PACKAGER,'current reviewed packaging repair changed')
    paths=[Path(__file__),Path(P.__file__),ROOT/'xl_output_task.py',Path(F.__file__),Path(C.__file__),Path(W.__file__),Path(notes.__file__)]
    from scripts.batch import harbor_pause_recovery, harbor_tracker_lifecycle
    from scripts import harbor_parallel_policy,harbor_package_current_prefix
    paths.append(Path(harbor_package_current_prefix.__file__))
    paths.extend(Path(m.__file__) for m in (harbor_pause_recovery,harbor_tracker_lifecycle,harbor_parallel_policy))
    return [record(p) for p in paths]


def guard_seconds(integration=None):
    return SECONDS if integration is None else source_runtime(integration).profile(sys.modules[__name__],integration)[0]['guard_seconds']


def checked_tree(directory):
    directory=E.safe(directory);require(directory.is_dir(),'retained packaging directory missing')
    return [record(directory/relative) for relative in W.static.tree(directory)]



def commands(content,work,taxonomy):
    common,package=P.packaging_arguments(content,work,taxonomy)
    values={'package':package}
    if content.mcp_build_a is not None:
        for name,where in [('build-a',content.mcp_build_a),('build-b',content.mcp_build_b)]:
            values[name]=[ROOT/'xl_variable_mcp.py','build',content.bindings['normalized_final']['path'],where,'--workbook',content.workbook,'--source',Path(content.bindings['source_file']['path']).parent]
        seg=Path(content.bindings['segmentation_generation']['path'])
        values['mask']=[ROOT/'xl_input_mask.py',content.workbook,*common,'--seg-dir',seg.parents[2],'-o',work/'inputs','--mask-cells',content.mcp_build_a/'mask_cells.json']
    return {k:list(map(str,v)) for k,v in values.items()}


def failure(descriptor,checkpoint,outcome,s,current,*,integration=None):
    """Only the observed cached-only failure, with exact fixed command identity."""
    if integration is not None:return source_runtime(integration).failure(sys.modules[__name__],integration,descriptor,checkpoint,outcome,s,current)
    declared=read(outcome)
    require(set(declared)=={'status','resource','automatic_retry','source_invalidity_claimed','terminal_verdict'} and declared['status']=='RECONCILIATION_REQUIRED' and declared['automatic_retry'] is False and declared['source_invalidity_claimed'] is False and declared['terminal_verdict'] is None,'completed selected failed guard required')
    resource=read(declared['resource']);intent=read(resource['intent'])
    require(set(resource)=={'intent','returncode','metrics','log','failure'} and resource['returncode']==1 and resource['failure'] is None,'unknown or resource-interrupted old operation is not recoverable here')
    require(intent['schema_version']=='harbor-coordinator-step/v1' and intent['action']=='step' and intent['arguments']==dict(descriptor=descriptor,checkpoint=checkpoint,captures=None) and intent['dispatch'] is False,'old guard selected another operation or model dispatch')
    require(intent['code']==record(C.__file__) and intent['memory_limit_bytes']==MEMORY and intent['timeout_seconds']==SECONDS,'old guarded code/limits differ')
    require(intent['controls']==read(descriptor)['controls'],'failed guard controls differ from admitted coordinator')
    for item in (resource['log'],intent['executable']):E.checked_bytes(item)
    old=s.run/'packaging';saved=read(record(old/'selection.json'));prior=decode(saved['content'])
    mode=W.lanes.normalization_projection(current.content)['mode']
    expected=replace(current.content,mcp_build_a=old/'build-a' if mode=='mcp' else None,mcp_build_b=old/'build-b' if mode=='mcp' else None)
    # The owning closed codec alone admits the historical absent scope receipt
    # as None. All raw bytes remain retained; unknown fields still reject.
    projected={**saved,'content':encode(prior)}
    require(projected==dict(schema_version='harbor-environment-production/v1',content=encode(expected),profiles=encode(current.public_profiles),taxonomy=s.taxonomy,code=dict(path=str(Path(P.__file__).resolve()),sha256=BEFORE_PRODUCER),mode=mode),'failed package changed authorized content/profile/source or taxonomy')
    require(prior==expected and not (old/'complete.json').exists(),'failed package is not the selected unfinished attempt')
    result=read(record(old/'package.result.json'));command=read(record(old/'package.intent.json'))
    require(result==dict(returncode=1,log=record(old/'package.log')),'selected package did not finish with the known failure')
    require(E.checked_bytes(result['log']).rstrip().endswith(b'RuntimeError: no lbx_api_key found; use --no-variable-source-audit to opt out'),'package failure differs from reviewed integration defect')
    _,args=P.packaging_arguments(expected,old,s.taxonomy);args=list(map(str,args))
    for flag in ('--audit-validator-upgrade-receipt','--audit-upgrade-original-code'):
        require(args.count(flag)==1,'current direct-upgrade route required');i=args.index(flag);del args[i:i+2]
    require(command==dict(argv=[command['argv'][0],*args],entry=dict(path=str(ROOT/'xl_output_task.py'),sha256=BEFORE_PACKAGER),model_calls_permitted=False),'historical package command differs beyond the exact missing flags')
    require(Path(command['argv'][0]).resolve()==Path(intent['executable']['path']).resolve(),'historical subprocess interpreter differs from guarded interpreter')
    expected_commands=commands(expected,old,s.taxonomy)
    labels=('build-a','build-b','mask') if mode=='mcp' else ()
    for label in labels:
        value=read(record(old/(label+'.result.json')))
        require(value==dict(returncode=0,log=record(old/(label+'.log'))),'pre-package command incomplete or failed')
        entry=read(record(old/(label+'.intent.json')))
        require(entry==dict(argv=[command['argv'][0],*expected_commands[label]],entry=record(expected_commands[label][0]),model_calls_permitted=False),'earlier package command differs from fixed current command')
    if mode=='mcp':require(W.static.tree(old/'build-a')==W.static.tree(old/'build-b'),'retained builds disagree')
    return notes.evidence_list([descriptor,checkpoint,outcome,declared['resource'],resource['intent'],resource['log'],intent['executable'],*checked_tree(old)])


def context(descriptor,checkpoint,lease,*,pause_completion=None,prefix_receipt=None,integration=None):
    s,_,controls=C.selected(descriptor);require(read(descriptor).get('refresh') is not None,'explicit completed F refresh required')
    prior=read(checkpoint)
    require(prior.get('descriptor')==descriptor and prior.get('status')=='STEP_COMPLETE' and prior.get('step')=='normalization_phase2','exact completed coordinator phase2 checkpoint required')
    original_lease=C.decode(prior['lease'])
    facade,before=W._active(s,lease)
    if integration is not None:
        require(pause_completion is None and prefix_receipt is None,'source-ID takeover cannot mix pause history')
        source_runtime(integration).lease(sys.modules[__name__],integration,descriptor,checkpoint,original_lease,before,lease)
    elif pause_completion is None:
        require(original_lease==lease,'selected lease differs from completed phase2 checkpoint; explicit lease continuation required')
    else:
        from scripts.batch import harbor_pause_recovery as pause
        old,new,issued=pause.validate_completion(pause_completion)
        receipt=read(pause_completion);stop=read(receipt['event']['stopped_owner_proof'])
        require(old['revision']==original_lease.revision and stop['before']['sha256']==original_lease.sha256,
                'phase2 checkpoint does not bind the exact pre-pause tracker')
        pause.continuation_chain(stop['before'],asdict(original_lease),before,lease,
            completion=pause_completion,tracker_path=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'))
    require(before['leases']['task_agent']['expires_at']-time.time_ns()>(guard_seconds(integration)+30)*10**9,'renew lease explicitly before preparing recovery')
    current=decode(prior['selection']);refresh=read(descriptor)['refresh']
    if prefix_receipt is None:
        for item in prior['evidence']:E.checked_bytes(item)
        F.validate(s,refresh,before)
    else:
        from scripts import harbor_package_current_prefix as prefix
        require(pause_completion is not None,'prefix receipt requires the exact pause continuation')
        prefix.validate(prefix_receipt,s,descriptor,checkpoint,before,current)
    path=Path(refresh['path']).parent/'continuation/normalization_phase2.json'
    saved=read(record(path));require(saved['selection']==prior['selection'],'phase2 result differs from immutable owning continuation')
    if prefix_receipt is None:
        for item in saved['evidence']:E.checked_bytes(item)
    F.checks(s,before,current,'normalization_phase2')
    require(not (path.parent/'environment.json').exists(),'existing environment checkpoint requires separate reconciliation')
    lane=dict(W.lanes.STEPS)['environment']
    require(before['lane_state'][lane]['state']=='pending','environment already attempted/committed under another context')
    return s,before,current,refresh,controls


def prepare(descriptor,checkpoint,outcome,lease,directory,*,pause_completion=None,prefix_receipt=None,integration=None):
    current_code=code(integration);s,before,current,refresh,controls=context(descriptor,checkpoint,lease,pause_completion=pause_completion,prefix_receipt=prefix_receipt,integration=integration)
    require(not current.content.stage.exists(),'authorized task stage must be absent before fresh packaging')
    evidence=failure(descriptor,checkpoint,outcome,s,current,integration=integration)
    directory=E.safe(directory)
    if integration is not None:source_runtime(integration).destination(sys.modules[__name__],integration,directory,current)
    protected=[s.run,s.bootstrap.run,s.bootstrap.policy.tracker_root,s.campaign.root,s.campaign.slot_root,current.content.stage,Path(descriptor['path']).parent]
    require(all(directory!=p and directory not in p.parents and p not in directory.parents for p in protected),'recovery namespace overlaps retained or protected evidence')
    require(not directory.exists(),'recovery preparation exists; select exact prepared checkpoint')
    directory.mkdir(parents=True,mode=0o700)
    E.retain(directory/'before.json',(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')).read_bytes())
    extra={} if pause_completion is None else dict(pause_completion=pause_completion)
    if prefix_receipt is not None:extra['prefix_receipt']=prefix_receipt
    if integration is not None:extra['integration']=integration
    return keep(directory/'prepared.json',dict(**extra,schema_version=SOURCE_ID_SCHEMA if integration is not None else SCHEMA if pause_completion is None else PAUSE_SCHEMA,descriptor=descriptor,checkpoint=checkpoint,failed_outcome=outcome,lease=C.encode(lease),before=record(directory/'before.json'),selection=encode(current),refresh=refresh,code=current_code,failed_evidence=evidence,model_calls=0,budget_reset=False))


def validate(prepared,*,active=True):
    p=read(prepared)
    extra={'integration'} if p.get('schema_version')==SOURCE_ID_SCHEMA else {'pause_completion'} if p.get('schema_version')==PAUSE_SCHEMA else set()
    if p.get('schema_version')==PAUSE_SCHEMA and 'prefix_receipt' in p:extra.add('prefix_receipt')
    require(set(p)=={'schema_version','descriptor','checkpoint','failed_outcome','lease','before','selection','refresh','code','failed_evidence','model_calls','budget_reset'}|extra and p['schema_version'] in {SCHEMA,PAUSE_SCHEMA,SOURCE_ID_SCHEMA} and p['model_calls']==0 and p['budget_reset'] is False,'invalid recovery descriptor')
    if 'pause_completion'in extra:
        from scripts.batch import harbor_pause_recovery as pause
        pause.validate_completion(p['pause_completion'])
    require(p['code']==code(p.get('integration')),'recovery code changed')
    for item in [p['before'],*p['failed_evidence']]:E.checked_bytes(item)
    s,_,controls=C.selected(p['descriptor']);current=decode(p['selection']);lease=C.decode(p['lease'])
    if active:
        actual=context(p['descriptor'],p['checkpoint'],lease,pause_completion=p.get('pause_completion'),prefix_receipt=p.get('prefix_receipt'),integration=p.get('integration'))
        require(actual[2]==current and actual[3]==p['refresh'],'recovery prefix changed')
        require(record(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'))['sha256']==p['before']['sha256'],'recovery exact tracker CAS changed')
    else:
        if p.get('integration') is not None:
            original_lease=C.decode(read(p['checkpoint'])['lease'])
            source_runtime(p['integration']).lease(sys.modules[__name__],p['integration'],p['descriptor'],p['checkpoint'],original_lease,read(p['before']),lease)
        if p.get('pause_completion') is not None:
            receipt=read(p['pause_completion']);stop=read(receipt['event']['stopped_owner_proof'])
            prior_lease=C.decode(read(p['checkpoint'])['lease'])
            pause.continuation_chain(stop['before'],asdict(prior_lease),read(p['before']),lease,
                completion=p['pause_completion'],tracker_path=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'))
        if p.get('prefix_receipt') is None:F.validate(s,p['refresh'])
        else:
            from scripts import harbor_package_current_prefix as prefix
            prefix.validate(p['prefix_receipt'],s,p['descriptor'],p['checkpoint'],read(p['before']),current,allow_environment=True)
        F.checks(s,read(p['before']),current,'normalization_phase2')
    require(failure(p['descriptor'],p['checkpoint'],p['failed_outcome'],s,current,integration=p.get('integration'))==p['failed_evidence'],'failed evidence closure changed')
    return p,s,current,lease,controls


def completed_package(prepared,*,active=True):
    p,s,selection,lease,_=validate(prepared,active=active)
    root=Path(prepared['path']).parent/'package';receipt=record(root/'complete.json');done=read(receipt);saved=read(record(root/'selection.json'))
    c=decode(saved['content']);mode=W.lanes.normalization_projection(selection.content)['mode']
    expected=replace(selection.content,mcp_build_a=root/'build-a' if mode=='mcp' else None,mcp_build_b=root/'build-b' if mode=='mcp' else None)
    require(c==expected and saved==dict(schema_version='harbor-environment-production/v1',content=encode(c),profiles=encode(selection.public_profiles),taxonomy=s.taxonomy,code=record(P.__file__),mode=mode),'complete package belongs to another selection')
    sha=W.static.publication._tree_hash(c.stage)[0]
    require(done==dict(schema_version='harbor-environment-production-complete/v1',selection=record(root/'selection.json'),stage_sha256=sha,mode=mode,model_calls=0,container_jobs=0,lane_approval=False),'package completion is incomplete or changed')
    expected_commands=commands(c,root,s.taxonomy)
    for label in (('build-a','build-b','mask','package') if mode=='mcp' else ('package',)):
        result=read(record(root/(label+'.result.json')));intent=read(record(root/(label+'.intent.json')))
        require(result==dict(returncode=0,log=record(root/(label+'.log'))) and intent['model_calls_permitted'] is False,'package command did not complete')
        require(intent==dict(argv=[sys.executable,*expected_commands[label]],entry=record(expected_commands[label][0]),model_calls_permitted=False),'completed command identity changed')
    if p.get('integration') is not None:source_runtime(p['integration']).completed_commands(sys.modules[__name__],p['integration'],root)
    c=replace(c,stage_sha256=sha);current=replace(selection,content=c)
    before=read(p['before']);result=W.lanes.check_completed_gate(before,W.lanes.CompletionEvidence('environment',current),W._context(s))
    require(result['passed'] and result.get('confidence','high')=='high','current complete environment proof failed')
    return p,s,current,lease,receipt,result


def execute(prepared,*,dispatch_commands=False,fault=lambda _:None):
    p,s,current,lease,_=validate(prepared);directory=Path(prepared['path']).parent;work=directory/'package'
    require(type(dispatch_commands) is bool,'explicit command dispatch boolean required')
    if not work.exists():
        require(not current.content.stage.exists(),'stage appeared before packaging')
        if not dispatch_commands:return dict(status='PREPARED',prepared=prepared,commands_executed=False)
        keep(directory/'package-launch.json',dict(prepared=prepared,work=str(work),stage=str(current.content.stage),code=code(p.get('integration')),models=False,containers=False))
        if p.get('integration') is None:P.package(current.content,current.public_profiles,work,s.taxonomy)
        else:
            with source_runtime(p['integration']).installed(sys.modules[__name__],p['integration']):
                P.package(current.content,current.public_profiles,work,s.taxonomy)
        fault('after_package')
    else:
        require((directory/'package-launch.json').exists() and (work/'complete.json').is_file(),'partial or unknown package cannot be replayed')
        require(read(record(directory/'package-launch.json'))==dict(prepared=prepared,work=str(work),stage=str(current.content.stage),code=code(p.get('integration')),models=False,containers=False),'package launch identity changed')
    p,s,current,lease,receipt,proof=completed_package(prepared)
    complete=keep(directory/'package-validated.json',dict(prepared=prepared,selection=encode(current),receipt=receipt,evidence=proof['evidence']))
    if (directory/'gate-intent.json').exists():raise ValueError('environment CAS was attempted; explicit reconciliation required')
    keep(directory/'gate-intent.json',dict(prepared=prepared,package=complete,before=p['before']))
    result=W._gate(s,lease,'environment',current)
    if p.get('integration') is not None:
        tracker=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')
        E.retain(directory/'after.json',tracker.read_bytes())
        validate_gate_state(read(p['before']),read(record(directory/'after.json')),proof)
    fault('after_gate')
    require(result['status']=='STEP_COMPLETE','current owning environment lane did not complete')
    result={**result,'status':'ENVIRONMENT_READY','lease':C.encode(result['lease']),'selection':encode(current),'descriptor':p['descriptor'],'evidence':[prepared,complete,*result['evidence']]}
    saved=keep(directory/'result.json',result);fault('after_result')
    continuation=Path(p['refresh']['path']).parent/'continuation/environment.json'
    keep(continuation,dict(selection=result['selection'],evidence=[receipt,prepared,complete]))
    keep(directory/'complete.json',dict(result=saved,checkpoint=record(continuation)))
    return result


def validate_gate_state(before,actual,proof):
    expected=copy.deepcopy(before);lane=proof['lane'];level=proof.get('confidence','high')
    expected['bindings'].update(proof['bindings']);row=expected['lane_state'][lane]
    row.update(state='passed',current_confidence=level,current_gate=proof['gate'],evidence_paths=notes.evidence_list(proof['evidence']))
    if proof.get('attempt') is not None:row['attempt']=proof['attempt']
    if proof.get('phase') is not None:row['phase']=proof['phase']
    if proof.get('disposition') is not None:row['disposition']=proof['disposition']
    row['confidence_history'].append(dict(level=level,reason='current_independent_gate_pass',revision=before['revision']+1))
    expected.update(last_gate=proof['gate'],current_gate=None,execution_state='running')
    levels=[(k,v['current_confidence']) for k,v in expected['lane_state'].items() if v.get('current_confidence')]
    expected['current_confidence']=dict(level=min((v for _,v in levels),key={'low':0,'medium':1,'high':2}.get),reason_codes=['current_independent_gate_pass'],contributors=[k for k,_ in levels],computed_at_revision=before['revision']+1)
    expected['confidence_history'].append(copy.deepcopy(expected['current_confidence']))
    expected['revision']+=1;expected['updated_at']=actual['updated_at'];expected['leases']['task_agent']['heartbeat_at']=actual['updated_at']
    require(actual==expected and actual['updated_at']>=before['updated_at'],'tracker is not the exact single owning environment CAS')


def reconcile(prepared):
    """Complete checkpoint publication only after the exact single owning CAS."""
    p,s,current,oldlease,receipt,proof=completed_package(prepared,active=False)
    directory=Path(prepared['path']).parent
    validated=record(directory/'package-validated.json')
    require(read(validated)==dict(prepared=prepared,selection=encode(current),receipt=receipt,evidence=proof['evidence']),'package validation checkpoint changed')
    require(read(record(directory/'gate-intent.json'))==dict(prepared=prepared,package=validated,before=p['before']),'owning CAS intent absent or changed')
    before=read(p['before']);tracker=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json');actual=read(record(tracker))
    validate_gate_state(before,actual,proof)
    lease=replace(oldlease,revision=actual['revision'],sha256=record(tracker)['sha256']);W._active(s,lease)
    result=dict(status='ENVIRONMENT_READY',step='environment',lease=C.encode(lease),selection=encode(current),descriptor=p['descriptor'],evidence=[prepared,validated,*proof['evidence']],publication_ready=False)
    saved=keep(directory/'result.json',result)
    continuation=Path(p['refresh']['path']).parent/'continuation/environment.json'
    keep(continuation,dict(selection=result['selection'],evidence=[receipt,prepared,validated]))
    keep(directory/'complete.json',dict(result=saved,checkpoint=record(continuation)))
    return result


def child(intent):
    v=read(intent);require(v['schema_version']==SCHEMA and v['code']==code(v.get('integration')),'recovery launch code changed')
    require(v['timeout_seconds']==guard_seconds(v.get('integration')) and v['memory_limit_bytes']==MEMORY and v['executable']==record(Path(sys.executable).resolve()),'recovery runtime/limits changed')
    directory=Path(intent['path']).parent;args=v['arguments']
    selected_integration=args.get('integration') if v['action']=='prepare' else read(args['prepared']).get('integration')
    require(v.get('integration')==selected_integration,'child integration differs from selected arguments')
    if v.get('parallel') is not None:
        descriptor=args['descriptor'] if v['action']=='prepare' else read(args['prepared'])['descriptor']
        parallel_controls(descriptor,v['parallel'])
    if v['action']=='prepare':
        require(set(args) in ({'descriptor','checkpoint','outcome','lease'},{'descriptor','checkpoint','outcome','lease','pause_completion'},{'descriptor','checkpoint','outcome','lease','pause_completion','prefix_receipt'},{'descriptor','checkpoint','outcome','lease','integration'}) and v['dispatch_commands'] is False,'exact preparation inputs required')
        result=prepare(args['descriptor'],args['checkpoint'],args['outcome'],C.decode(read(args['lease'])),directory/'recovery',pause_completion=args.get('pause_completion'),prefix_receipt=args.get('prefix_receipt'),integration=args.get('integration'))
    else:
        require(set(args)=={'prepared'},'exact prepared recovery required')
        if v['action']=='execute':result=execute(args['prepared'],dispatch_commands=v['dispatch_commands'])
        else:require(v['action']=='reconcile' and v['dispatch_commands'] is False,'unsupported action');result=reconcile(args['prepared'])
    return keep(directory/'result.json',result)


def parallel_controls(descriptor,parallel):
    from scripts import harbor_parallel_policy as policy
    require(isinstance(parallel,dict) and set(parallel)=={'contract','activation'},'exact activated parallel policy required')
    s,_,_=C.selected(descriptor)
    p=policy.validate(parallel['contract'],parallel['activation'])
    require(Path(p['campaign_root'])==s.bootstrap.policy.tracker_root.parent,'parallel policy belongs to another campaign')
    require(s.campaign.root==Path(p['model_journal_root']) and s.campaign.slot_root==Path(p['model_slot_root']) and s.campaign.model_slots==p['model_capacity'],'parallel model roots/capacity differ from admitted worker')
    return policy.controls(parallel['contract'],parallel['activation'],s.bootstrap.workbook)


def run(action,arguments,controls,directory,*,dispatch_commands=False,parallel=None):
    require(action in {'prepare','execute','reconcile'} and type(dispatch_commands) is bool,'explicit action required')
    require(not dispatch_commands or action=='execute','only execute may run packaging commands')
    required={'descriptor','checkpoint','outcome','lease'} if action=='prepare' else {'prepared'}
    require(set(arguments)==required or action=='prepare' and set(arguments) in (required|{'pause_completion'},required|{'pause_completion','prefix_receipt'},required|{'integration'}),'exact root-selected argument records required')
    for item in arguments.values():E.checked_bytes(item)
    descriptor=arguments['descriptor'] if action=='prepare' else read(arguments['prepared'])['descriptor']
    integration=arguments.get('integration')if action=='prepare'else read(arguments['prepared']).get('integration')
    seconds=guard_seconds(integration)
    selected_worker,_,selected_controls=C.selected(descriptor)
    if parallel is None:
        require(not (selected_worker.bootstrap.policy.tracker_root.parent/'parallel-policy/activation.json').exists(),'activated parallel policy must be explicitly selected')
        require(selected_controls.slot_lock==controls.slot_lock,'recovery must share admitted actual-workbook slot')
    else:
        require(controls==parallel_controls(descriptor,parallel),'recovery controls differ from activated workbook lane')
    lock=C._controls(C.Controls(controls.slot_lock));directory=E.safe(directory)
    require(not directory.exists(),'guard namespace exists; retain and select explicit prepared evidence')
    protected=[selected_worker.run,selected_worker.bootstrap.run,selected_worker.bootstrap.policy.tracker_root,selected_worker.campaign.root,selected_worker.campaign.slot_root,Path(selected_worker.bootstrap.source_manifest['path']).parent,Path(selected_worker.bootstrap.segmentation_manifest['path']).parent]
    require(all(directory!=p and directory not in p.parents and p not in directory.parents for p in protected),'guard namespace overlaps protected workbook evidence')
    require(shutil.disk_usage(ROOT).free>=MEMORY,'8-GiB free-space reserve required')
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);directory.mkdir(parents=True,mode=0o700)
        extra={} if parallel is None else dict(parallel=parallel)
        if integration is not None:extra['integration']=integration
        intent=keep(directory/'intent.json',dict(**extra,schema_version=SCHEMA,action=action,arguments=arguments,dispatch_commands=dispatch_commands,code=code(integration),executable=record(Path(sys.executable).resolve()),timeout_seconds=seconds,memory_limit_bytes=MEMORY))
        metrics={};rc=None;failure_=None;logpath=directory/'step.log'
        try:
            with logpath.open('xb') as log:
                command=[sys.executable,'-B',str(Path(__file__).resolve()),'_child',intent['path'],intent['sha256']] if integration is None else source_runtime(integration).child_command(sys.modules[__name__],integration,intent)
                rc=C._run_in_process_group(command,stdout=log,timeout_seconds=seconds,grace_seconds=3,memory_limit_bytes=MEMORY,metrics=metrics,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT)},guardian_lock_fds=(fd,))
                log.flush();os.fsync(log.fileno())
        except Exception as exc:failure_=dict(type=type(exc).__name__,message_sha256=E.sha(str(exc).encode()))
        resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,metrics=metrics,log=record(logpath),failure=failure_))
        success=rc==0 and failure_ is None and (directory/'result.json').is_file()
        return keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION' if success else 'RECONCILIATION_REQUIRED',result=record(directory/'result.json') if success else None,resource=resource,automatic_retry=False,source_invalidity_claimed=False,publication_performed=False))
    finally:os.close(fd)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv and argv[0]=='_child':
        require(len(argv)==3,'exact child intent required');child(dict(path=argv[1],sha256=argv[2]));return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','execute','reconcile'))
    for field in ('descriptor','checkpoint','outcome','lease','prepared','pause-completion','prefix-receipt','integration','contract','activation'):
        parser.add_argument('--'+field);parser.add_argument('--'+field+'-sha256')
    parser.add_argument('--journal',required=True);parser.add_argument('--slot-lock',required=True);parser.add_argument('--dispatch-commands',action='store_true')
    a=parser.parse_args(argv);arguments={}
    for name in ('descriptor','checkpoint','outcome','lease','prepared','pause_completion','prefix_receipt','integration','contract','activation'):
        path,sha=getattr(a,name),getattr(a,name+'_sha256');require((path is None)==(sha is None),'path and explicit hash required together')
        if path is not None:arguments[name]=dict(path=path,sha256=sha)
    parallel=None
    if 'contract' in arguments or 'activation' in arguments:
        require('contract' in arguments and 'activation' in arguments,'paired parallel policy bindings required')
        parallel={name:arguments.pop(name) for name in ('contract','activation')}
    result=run(a.action,arguments,C.Controls(Path(a.slot_lock)),Path(a.journal),dispatch_commands=a.dispatch_commands,parallel=parallel)
    print(json.dumps(result),flush=True);return 0

if __name__=='__main__':raise SystemExit(main())
