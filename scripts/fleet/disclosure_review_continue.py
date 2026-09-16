"""Guarded, data-selected disclosure review rounds over retained completed work.

No author command or completed model call is replayed. This runner stops at an
actual aggregate and never adopts, publishes, or manufactures a successful Call.
"""
from contextlib import contextmanager,ExitStack
from dataclasses import asdict
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time

SCHEMA='fleet-disclosure-review-continuation/v1'
FIELDS={'schema_version','helper','retained','modules','utilities','capacity_policy','workbook','source','owner','fence',
        'build','inputs','allowance','prior_rejections','round','limit','max_calls','code_snapshots','output','env_file','renewal_module',
        'transport','physical_module','physical_policy','oracle'}
MODULES={'wire':'harbor_disclosure_partition_wire','evidence':'harbor_disclosure_partition_evidence',
         'allowance':'harbor_continuation_allowance','rounds':'harbor_disclosure_review_round','review':'harbor_disclosure_partition_review',
         'cache':'harbor_disclosure_partition_cache','gate':'model_gate'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'selected bytes changed');return json.loads(Path(row['path']).read_bytes())

def initial_renewal_seconds(p):
    """Admission runway is separate from the renewed lease and HTTP timeout."""
    if 'initial_renewal_policy' not in p:return 930
    policy=read(p['initial_renewal_policy'])
    require(set(policy)=={'schema_version','authorization','workbook','source','measured_context','required_seconds'}
        and policy['schema_version']=='fleet-disclosure-initial-renewal-policy/v1'
        and policy['workbook']==p['workbook'] and policy['source']==p['source'],
        'closed source-scoped initial renewal policy required')
    require(binding(policy['authorization']['path'])==policy['authorization'],'renewal authorization changed')
    proof=read(policy['measured_context']);seconds=policy['required_seconds']
    observed=json.loads(proof.get('tail','null'))
    require(type(observed)is dict and type(observed.get('value'))is dict,
            'actual completed source preparation measurement required')
    completed=observed['value'];prepared=read(p['retained']['plan'])
    require(completed.get('status')=='PREPARED_NOT_RESERVED'
        and all(completed.get(k)==0 for k in ('models_dispatched','task_mutations','lease_mutations'))
        and completed.get('plan')==p['retained']['plan']
        and completed.get('build')==p['build'] and completed.get('inputs')==p['inputs']
        and prepared.get('schema_version')=='fleet-disclosure-review-preparation/v1'
        and prepared.get('source')==p['source'] and prepared.get('workbook')==p['workbook']
        and prepared.get('build')==p['build'] and prepared.get('modules')==p['modules'],
        'measured preparation differs from selected source, build or review runtime')
    require(proof.get('returncode')==0 and type(proof.get('seconds')) in (int,float)
        and 0<proof['seconds']<600 and type(seconds)is int and 300<=seconds<=930
        and seconds>=proof['seconds']*3+120,'insufficient measured renewal runway')
    return seconds

def module(name,row):
    require(binding(row['path'])==row,'selected module changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def keep(path,value):
    from scripts.batch import harbor_model_executor as E
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    E.save(path,value)
    return binding(path)

@contextmanager
def transport_runtime(p,executor):
    """Select the declared host gate while retaining the old campaign evidence."""
    import scripts.fleet as fleet
    prior_transport=sys.modules.get('xl_transport');prior_physical=sys.modules.get('scripts.fleet.physical_model_capacity')
    prior_attribute=getattr(fleet,'physical_model_capacity',None);prior_executor=executor.xl_transport
    names={'HARBOR_PHYSICAL_MODEL_POLICY':p['physical_policy']['path'],'HARBOR_PHYSICAL_MODEL_POLICY_SHA256':p['physical_policy']['sha256']}
    previous={k:os.environ.get(k)for k in names}
    try:
        physical=module('scripts.fleet.physical_model_capacity',p['physical_module']);fleet.physical_model_capacity=physical
        os.environ.update(names);physical.validate(p['physical_policy'])
        transport=module('xl_transport',p['transport']);executor.xl_transport=transport
        yield
    finally:
        executor.xl_transport=prior_executor
        for name,old in [('xl_transport',prior_transport),('scripts.fleet.physical_model_capacity',prior_physical)]:
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old
        if prior_attribute is None:
            if hasattr(fleet,'physical_model_capacity'):delattr(fleet,'physical_model_capacity')
        else:fleet.physical_model_capacity=prior_attribute
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value

def transport_checked(p,executor):
    require(binding(executor.xl_transport.__file__)==p['transport'],'selected transport changed')
    from scripts.fleet import physical_model_capacity as physical
    require(binding(physical.__file__)==p['physical_module']and physical.selected()[0]==p['physical_policy'],
            'selected physical capacity changed')

@contextmanager
def oracle_runtime(row):
    require(set(row)=={'original','selected'},'closed oracle dependency required')
    prior=sys.modules.get('xl_mcp_oracle')
    if prior is None:prior=module('xl_mcp_oracle',row['original'])
    require(binding(prior.__file__)==row['original'],'original oracle dependency differs')
    try:
        module('xl_mcp_oracle',row['selected'])
        yield
    finally:sys.modules['xl_mcp_oracle']=prior

@contextmanager
def context(row):
    p=read(row);require(set(p)-{'rejection_history','initial_renewal_policy'} in (FIELDS,FIELDS|{'source_derivative'},FIELDS|{'resume'},FIELDS|{'reserved_round','round_reader'},FIELDS|{'source_derivative','reserved_round','round_reader'},FIELDS|{'completed_prefix','progress_reader'}) and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed selected review continuation required')
    initial_renewal_seconds(p)
    c=p['retained'];require(set(c)=={'helper','plan','function','kwargs','environment','lease'}
        and c['function']=='context'and c['kwargs']=={'continued':True}
        and type(c['environment'])is int and type(c['lease'])is int and c['environment']>=0 and c['lease']>=0,'exact retained context projection required')
    original=module('retained_disclosure_review_context',c['helper'])
    with getattr(original,c['function'])(c['plan'],**c['kwargs'])as value:
        env=value[c['environment']];lease=value[c['lease']];require(isinstance(env,tuple)and len(env)==8,'original instruction environment required')
        require(set(p['modules'])==set(MODULES),'complete review runtime required')
        prior={};attributes={};loaded={};reader_manager=ExitStack()
        try:
            for key,suffix in MODULES.items():
                package='scripts.fleet'if key=='gate'else'scripts.batch';name=package+'.'+suffix
                parent=__import__(package,fromlist=[suffix]);prior[name]=sys.modules.get(name);attributes[(package,suffix)]=getattr(parent,suffix,None)
                loaded[key]=module(name,p['modules'][key]);setattr(parent,suffix,loaded[key])
            Q=loaded['rounds']
            if 'completed_prefix'in p:
                loaded['progress']=module('disclosure_completed_batch_progress',p['progress_reader'])
            if 'rejection_history'in p:
                history=p['rejection_history']
                require(set(history)=={'helper','profile'},'closed completed rejection history selection required')
                reader=module('completed_disclosure_rejection_cache',history['helper'])
                reader_manager.enter_context(reader.installed(Q,history['profile']))
            if 'reserved_round'in p:
                require(set(p['round_reader'])=={'runtime','profile'},'closed reserved reader projection required')
                replacement=module('disclosure_reserved_round',p['round_reader']['runtime'])
                if p['round_reader']['profile'] is None:
                    stopped=read(p['reserved_round'])
                    require(stopped['schema_version'] in ('fleet-disclosure-interrupted-reservation/v1','fleet-disclosure-initial-cache-failure/v1','fleet-disclosure-derivative-request-failure/v1')
                        and read(stopped['plan'])['modules']['rounds']==p['modules']['rounds'],
                        'unmodified reserved reader requires exact interrupted authority')
                else:reader_manager.enter_context(replacement.reader(Q,p['round_reader']['profile']))
                loaded['reservation']=replacement
            inputs=read(p['inputs']);worker,selection,_=Q.completed_build(p['build'])
            require(worker.root in [Path(read(x)['root'])for x in p['code_snapshots']],'original worker snapshot missing')
            require(inputs['workbook']==p['workbook']and inputs['original_source']==p['source']
                and inputs['worker_run']==str(worker.run),'review source or worker changed')
            require(type(p['round'])is int and type(p['limit'])is int and 1<=p['round']<=p['limit'],'explicit finite review round required')
            require(type(p['max_calls'])is int and p['max_calls']>0 and p['max_calls']==len(read(inputs['projection'])['batches']),
                    'explicit physical call ceiling differs from complete projection')
            if 'reserved_round'in p and p['round_reader']['profile']is None:
                stopped=read(p['reserved_round'])
                if 'lease_before'in stopped or 'lease_after'in stopped:
                    lease=loaded['reservation'].completed_initial_lease(p['reserved_round'],p,worker,lease,Q)
            U=module('disclosure_review_custody',p['utilities'])
            if 'resume' in p:
                r=p['resume'];require(set(r)-{'lease_plan'}=={'prior_plan','reserved','lease_completion','lease_before','lease_after','recovery','payload_repair','certificate'},'closed pretransport resume required')
                for key,suffix in [('recovery','harbor_model_pretransport_recovery'),('payload_repair','harbor_disclosure_payload_repair')]:
                    package='scripts.batch';name=package+'.'+suffix;parent=__import__(package,fromlist=[suffix])
                    prior[name]=sys.modules.get(name);attributes[(package,suffix)]=getattr(parent,suffix,None)
                    loaded[key]=module(name,r[key]);setattr(parent,suffix,loaded[key])
                renewal=module('completed_disclosure_round_lease',p['renewal_module'])
                old=read(r['prior_plan']);lease_authority=resume_lease_plan(p,old,r);path=worker.context.tracker_root/(p['workbook']+'.json')
                lease=renewal.validate_completed(life=Q.life,read=read,record=binding,path=path,previous=lease,
                    completion=r['lease_completion'],before_binding=r['lease_before'],after_binding=r['lease_after'],authority_record=lease_authority)
                require(binding(path)==dict(path=str(path),sha256=r['lease_after']['sha256']),'current tracker differs from completed renewal')
                resume_state(p,inputs,loaded,worker)
            with transport_runtime(p,loaded['review'].E),oracle_runtime(p['oracle']):
                yield p,U,env,worker,selection,lease,inputs,loaded
        finally:
            reader_manager.close()
            for name,old in reversed(list(prior.items())):
                if old is None:sys.modules.pop(name,None)
                else:sys.modules[name]=old
            for (package,suffix),old in attributes.items():
                parent=sys.modules[package]
                if old is None:delattr(parent,suffix)
                else:setattr(parent,suffix,old)

def validate_code_snapshots(p,cache):
    # Validate the exact cache inventory contract before any lease or reservation.
    for snapshot in p['code_snapshots']:cache.inventory(snapshot)

def probe(row):
    with context(row)as(p,U,env,w,s,lease,inputs,m):
        validate_code_snapshots(p,m['cache'])
        W=env[3];_,before,tracker=W.active(w,s,lease)
        require(before['leases']['task_agent']['expires_at']>time.time_ns()+initial_renewal_seconds(p)*10**9,'review context cannot cover the explicit initial renewal')
        Q=m['rounds'];scope=Q.semantic_scope(inputs)
        if 'source_derivative'in p:
            Q.reviewed_content(p['source_derivative'],inputs,w,s,p['build'],p['prior_rejections'])
        for i,old in enumerate(p['prior_rejections'],1):Q.closed_rejection(old,scope,i)
        Q._primary_history(w.campaign,scope,p['round'],p['prior_rejections'])
        policy=Q.A.validate(p['allowance'],expected_scope=Q.A.scope(p['workbook'],p['source']['sha256']),at=time.time_ns())
        require(p['limit']<=policy['limits']['review_attempts']and policy['limits']['transport_retries']==0,'review allowance differs')
        body=m['review'].full_body(inputs['manifest'],inputs['instruction'],inputs['disclosure'],inputs['golden'],inputs['delivered'],root=w.root)
        require(body==read(inputs['full_body']),'full source-derived review body differs')
        m['evidence'].validate_plan(read(inputs['projection']),body,inputs['golden']['path'],inputs['delivered']['path'],root=w.root)
        require(W.record(w.context.tracker_root/(p['workbook']+'.json'))==tracker,'tracker changed during read-only proof')
        return dict(status='PASS',models_dispatched=0,result=dict(plan=row,original_build=p['build'],retained_rejections=p['prior_rejections'],
            batches=len(read(inputs['projection'])['batches']),actual_task_mutations=0,lease_mutations=0))

@contextmanager
def fresh_admitted_transport(executor,check):
    """Recheck native custody after waiting and before the executor starts HTTP."""
    transport=executor.xl_transport;original=transport.model_slot
    @contextmanager
    def admitted(**kwargs):
        with original(**kwargs):
            check()
            yield
    transport.model_slot=admitted
    try:yield
    finally:transport.model_slot=original


def admitted_capacity(capacity,status,workbook,owner,operation):
    """Check occupancy after atomic admission without asking for another slot."""
    models,_=capacity.validate_occupancy(status)
    applicable=capacity.validate_quarantines(status)
    isolated={q['operation_id']for q in applicable}
    require(not any(op['state']=='hold'and op['operation_id']not in isolated for op in models),
        'another model effect or quarantined custody blocks transport')
    barriers=capacity.model_admission_barriers(status,workbook,owner)
    if all(value==operation for value in barriers):return
    exceptions=capacity.validate_host_capacities(status,applicable)
    extensions=capacity.validate_physical_extensions(status)
    host=capacity.quarantine_host(owner)
    selected=next((row for row in exceptions if row['host']==host),None)
    require(selected is not None,'another model effect or quarantined custody blocks transport')
    limit=next((row['physical_model_limit']for row in extensions if row['host']==host),selected['physical_model_limit'])
    occupied=[op for op in models if capacity.quarantine_host(op['owner'])==host]
    isolated={q['operation_id']for q in applicable}
    require(operation in occupied and len(occupied)<=limit
        and all(value==operation or (value in occupied and value['workbook']!=workbook
            and (value['state']=='active' or value['state']=='hold'and value['operation_id']in isolated))
            for value in barriers), 'another model effect or quarantined custody blocks transport')


def admitted_custody(p,root,prepared,executor,gate):
    """Validate the exact acquired operation, rather than asking to admit it again."""
    capacity=module('admitted_disclosure_capacity',p['capacity_policy'])
    client,owner,_=gate.context();status=client.call('status');now=time.time()
    capacity.validate_occupancy(status)
    require(abs(now-status['at'])<=60,'fresh admitted coordinator observation required')
    require(owner['owner']==p['owner']and owner['workbook']==p['workbook']and owner['fence']==p['fence'],
            'admitted fleet identity differs')
    rows=[v for v in status['workbooks']if v['workbook']==p['workbook']]
    require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==p['owner']
            and rows[0]['fence']==p['fence']and rows[0]['version']==Path(root).name
            and rows[0]['source_sha256']==p['source']['sha256']and rows[0]['expires']>now,
            'exclusive admitted workbook claim differs')
    operation=prepared.attempt_id+('-physical-repair-01'if prepared.physical_repair is not None else '')
    from scripts.fleet.coordinator import digest as operation_digest
    identity=operation_digest(dict(workbook=p['workbook'],request_sha256=executor.sha(executor.checked_bytes(prepared.request))))
    expected=[v for v in status['operations']if v['operation_id']==operation]
    require(len(expected)==1 and expected[0]['kind']=='model'and expected[0]['state']=='active'
            and expected[0]['workbook']==p['workbook']and expected[0]['owner']==p['owner']
            and expected[0]['fence']==p['fence']
            and expected[0]['identity']==identity,
            'exact acquired model operation required before HTTP')
    admitted_capacity(capacity,status,p['workbook'],p['owner'],expected[0])
    require(not any(v['state']in('active','hold')and v['workbook']==p['workbook']and v!=expected[0]
                    for v in status['operations']),'another target operation blocks transport')
    return expected[0]

def resume_lease_plan(p,old,r):
    """Keep the completed initial renewal when a cache continuation stopped later."""
    if 'lease_plan'not in r:return r['prior_plan']
    require('reserved_round'in old and 'resume'not in old and old['round_reader']['profile']is None,
        'completed cache continuation ancestry required')
    proof=read(old['reserved_round']);base=read(r['lease_plan'])
    require(set(proof)=={'schema_version','plan','worker_outcome','process','authority','retained_lease','lease_before','lease_after'}
        and proof['schema_version']=='fleet-disclosure-initial-cache-failure/v1'
        and proof['plan']==r['lease_plan']and proof['retained_lease']==r['lease_completion']
        and proof['lease_before']==r['lease_before']and proof['lease_after']==r['lease_after'],
        'exact original completed renewal history required')
    changes={'helper','owner','fence','output','reserved_round','round_reader','code_snapshots'}
    require('reserved_round'not in base and 'resume'not in base
        and {k:v for k,v in old.items()if k not in changes}=={k:v for k,v in base.items()if k not in changes}
        and old['fence']==base['fence']+1 and old['owner']!=base['owner'],
        'cache ancestry changed review or source authority')
    home=Path(old['output']);require(read(binding(home/'continued.json'))==old['reserved_round'],
        'actual cache continuation receipt changed')
    require(read(binding(home/'reserved.json'))==read(binding(Path(base['output'])/'reserved.json')),
        'original round reservation changed')
    return r['lease_plan']


def resume_state(p,inputs,m,worker):
    """Reuse only the exact reserved round whose first shard never reached HTTP."""
    r=p['resume'];old=read(r['prior_plan'])
    changed={'helper','modules','owner','fence','output','resume'}
    if 'lease_plan'in r:
        resume_lease_plan(p,old,r);changed|={'reserved_round','round_reader'}
    require({k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed},
            'resume changed source, limits, inputs or original workflow')
    require(p['output']!=old['output'] and p['fence']>old['fence'],'fresh fenced continuation namespace required')
    root=Path(old['output']);require(read(binding(root/'intent.json'))==dict(plan=r['prior_plan'],automatic_retry=False),'original runner intent differs')
    require(r['reserved']==binding(root/'reserved.json'),'original reservation moved')
    reserved=read(r['reserved']);require(set(reserved)=={'plan','automatic_retry'}and reserved['automatic_retry']is False,'original reservation receipt differs')
    plan=reserved['plan'];v=read(plan)
    require({k:x for k,x in v.items()if k!='review_round'}==inputs,'reserved review inputs changed')
    authority=m['rounds'].validate(v['review_round'],inputs)
    require((authority['round'],authority['limit'])==(p['round'],p['limit']),'reserved review allowance changed')
    batches=root/'batches';require(sorted(x.name for x in batches.iterdir())==['0000'],'pretransport resume requires only its exact first untransmitted shard')
    first=batches/'0000';require({x.name for x in first.iterdir()}=={'request.json','admission-refused.json'},'unsealed first shard has additional effects')
    certificate,prepared,campaign=m['recovery'].validate_original(r['certificate'])
    require(campaign==worker.campaign and certificate['proof']==read(certificate['reconciliation'])['proof'],'recovery campaign differs')
    proof=read(certificate['proof'])
    require(proof['prepared']==binding(first/'request.json')and proof['refused']==binding(first/'admission-refused.json'), 'recovery detached from original runner batch')
    lease_root=Path(read(resume_lease_plan(p,old,r))['output'])
    require(read(r['lease_completion'])['intent']==binding(lease_root/'initial-lease/intent.json'),'original completed renewal differs')
    from dataclasses import replace
    return plan,replace(prepared,physical_repair=r['certificate']),read(binding(first/'request.json'))['context']

def run(row):
    with context(row)as(p,U,env,w,s,lease,inputs,m):
        validate_code_snapshots(p,m['cache'])
        W=env[3];Q=m['rounds'];R=m['review'];E=R.E
        if not any(k in p for k in ('resume','reserved_round','completed_prefix')):
            _,before,_=W.active(w,s,lease)
            require(before['leases']['task_agent']['expires_at']>time.time_ns()+initial_renewal_seconds(p)*10**9,
                    'review context cannot cover the explicit initial renewal')
        U.custody(p,w.root);home=Path(p['output']);require(not home.exists(),'review run consumed; reconcile without replay')
        home.mkdir(parents=True);keep(home/'intent.json',dict(plan=row,automatic_retry=False))
        with (w.run/'worker.lock').open('a+b')as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            U.custody(p,w.root)
            resumed_request=None
            if 'completed_prefix'in p:
                plan,_,_=m['progress'].authority(p['completed_prefix'],p,w,s,lease,Q,W)
                keep(home/'reserved.json',dict(plan=plan,automatic_retry=False));keep(home/'continued.json',p['completed_prefix'])
            elif 'reserved_round'in p:
                path,value=m['reservation'].validate(p['reserved_round'],p,w,s,lease,Q)
                plan=binding(path)if path.exists()else keep(path,value)
                keep(home/'reserved.json',dict(plan=plan,automatic_retry=False));keep(home/'continued.json',p['reserved_round'])
            elif 'resume'in p:
                plan,resumed_request,resumed_context=resume_state(p,inputs,m,w)
                keep(home/'reserved.json',dict(plan=plan,automatic_retry=False))
                keep(home/'continued.json',p['resume'])
            else:
                renewal=module('disclosure_round_lease',p['renewal_module'])
                require(renewal.SCHEMA=='fleet-validated-continuation-lease/v1'and renewal.DURATION_NS==7200*10**9,'original bounded continuation renewal required')
                def lease_evidence(before,current):
                    _,actual,tracker=W.active(w,s,current);require(actual==before,'renewal custody differs')
                    Q.completed_build(p['build'],worker=w,selection=s)
                    scope=Q.semantic_scope(inputs)
                    for i,old in enumerate(p['prior_rejections'],1):Q.closed_rejection(old,scope,i)
                    Q._primary_history(w.campaign,scope,p['round'],p['prior_rejections'])
                    return [row,p['inputs'],p['allowance'],*p['build'].values(),*p['prior_rejections']]
                lease,_=renewal.renew(life=Q.life,read=read,record=binding,keep=keep,path=w.context.tracker_root/(p['workbook']+'.json'),
                    lease=lease,authority_record=row,validation=lease_evidence,custody=lambda:U.custody(p,w.root),directory=home/'initial-lease',retain_sufficient=True)
                plan=Q.reserve(inputs,worker=w,selection=s,lease=lease,environment=env,allowance=p['allowance'],build=p['build'],
                    prior_rejections=p['prior_rejections'],ordinal=p['round'],limit=p['limit'],
                    **({'source_derivative':p['source_derivative']}if 'source_derivative'in p else {}))
                keep(home/'reserved.json',dict(plan=plan,automatic_retry=False))
            started=time.monotonic()
            cache=m['cache'].ValidationCache(plan,policy=s.content.model_policy,root=w.root,code_snapshots=p['code_snapshots'])
            keep(home/'validation-metrics.json',dict(full_constructor_seconds=time.monotonic()-started,source_rederivations=1))
            validated=cache.get(policy=s.content.model_policy,root=w.root)
            total=len(validated[1]['batches']);calls=[]
            if 'completed_prefix'in p:
                retained_plan,calls=m['progress'].completed_calls(p['completed_prefix'],p,w,s,lease,Q,W,R,validated)
                require(retained_plan==plan,'completed prefix reservation changed')
                keep(home/'retained-calls.json',dict(proof=p['completed_prefix'],plan=plan,calls=[asdict(call)for call in calls],calls_replayed=0))
            from xl_task_build import read_env_key
            credential=read_env_key(Path(p['env_file']))
            os.environ['HARBOR_MODEL_SLOTS']=str(w.campaign.model_slots);os.environ['HARBOR_MODEL_SLOT_ROOT']=str(w.campaign.slot_root)
            for index in range(len(calls),total):
                U.custody(p,w.root);transport_checked(p,E);_,before,tracker=W.active(w,s,lease)
                require(before['leases']['task_agent']['expires_at']>time.time_ns()+660*10**9,'remaining lease cannot cover next original model timeout')
                started=time.monotonic();validated=cache.get(policy=s.content.model_policy,root=w.root)
                recheck_seconds=time.monotonic()-started
                task=W.task(w,s,tracker)
                if index==0 and resumed_request is not None:
                    from types import SimpleNamespace
                    request=SimpleNamespace(prepared=resumed_request,context=resumed_context)
                    require(E.checked_bytes(request.prepared.request)==E.canonical(R.payload(validated[0],validated[1],index,s.content.model_policy)),
                            'retained bounded request differs from actual current full projection')
                else:request=R.prepare(task,plan,index,_validated=validated)
                out=home/'batches'/('%04d'%index);out.mkdir(parents=True)
                keep(out/'request.json',dict(prepared=asdict(request.prepared),context=request.context))
                execution_plan=E.prepared_plan(request.prepared,w.campaign)[0]
                require(execution_plan['max_transport_retries']==0,'automatic transport retry forbidden')
                require(not E.location(request.prepared,w.campaign).exists(),'prepared new shard already has executor history')
                def admitted():
                    try:
                        admitted_custody(p,w.root,request.prepared,E,m['gate']);transport_checked(p,E);_,actual,current=W.active(w,s,lease)
                        observed=time.time_ns()
                        require(actual['leases']['task_agent']['expires_at']>observed+660*10**9,
                                'native lease cannot cover HTTP after model admission')
                        keep(out/'admission.json',dict(tracker=current,observed_at_ns=observed,required_remaining_seconds=660,
                            physical_policy=p['physical_policy'],transport_started=False))
                    except BaseException as exc:
                        keep(out/'admission-refused.json',dict(failure_type=type(exc).__name__,
                            message_sha256=hashlib.sha256(str(exc).encode()).hexdigest(),transport_started=False,automatic_retry=False))
                        raise
                with fresh_admitted_transport(E,admitted):
                    outcome=E.execute(request.prepared,w.campaign,credential=credential)
                require(outcome.status=='completed'and outcome.call is not None,'review transport not completed; retain consumed request without replay')
                keep(out/'transport-complete.json',dict(index=index,plan=plan,outcome=outcome.outcome,
                    call=asdict(outcome.call),semantic_approval=False,automatic_retry=False))
                validated=cache.get(policy=s.content.model_policy,root=w.root)
                R.validate_batch(outcome.call,plan,index,s.content.model_policy,w.root,_validated=validated)
                calls.append(outcome.call);keep(out/'complete.json',dict(index=index,plan=plan,call=asdict(outcome.call),
                    cached_input_recheck_seconds=recheck_seconds))
            # Reuse the exact fully checked batch projection; aggregate validates
            # every actual Call and independent response, never a synthetic Call.
            aggregate=R.aggregate(plan,calls,s.content.model_policy,w.root,_validated=cache.get(policy=s.content.model_policy,root=w.root))
            aggregate_binding=keep(home/'aggregate.json',aggregate)
            return keep(home/'result.json',dict(status='DISCLOSURE_REVIEW_COMPLETE_NOT_ADOPTED',workbook=p['workbook'],plan=row,
                review_plan=plan,aggregate=aggregate_binding,actual_review_calls=len(calls),publication_performed=False,automatic_retry=False))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('probe','run'));parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);a=parser.parse_args();row=dict(path=a.plan,sha256=a.sha256)
    print(json.dumps(probe(row)if a.action=='probe'else run(row)))
if __name__=='__main__':main()
