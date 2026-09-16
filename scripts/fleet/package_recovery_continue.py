"""Run selected package recovery, then the original remaining authoring chain.

Completed normalization is historical input. Packaging remains guarded by the
original recovery module and the original environment gate owns its only CAS.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import argparse,ast,hashlib,importlib.util,json,os,sys

SCHEMA='fleet-package-recovery-continuation/v1'
FIELDS={'schema_version','helper','integration','context_helper','context_plan','original_job','descriptor','checkpoint','failed_outcome','workbook','source','owner','fence','home','operations'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'canonical package continuation binding required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'package continuation bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'package continuation code changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def evidence(p):
    if p.get('remaining_start'):
        return [p[k]for k in ('previous','history_helper','start_outcome','stopped','original_job')]+list(p.get('checkpoint_lease',p.get('checkpoint_takeover')).values())
    keys=('previous','takeover_profile','takeover_completion','stopped','original_job')if p.get('completed_start')else ('integration','context_helper','context_plan','original_job','descriptor','checkpoint','failed_outcome')
    return [p[k]for k in keys]+([p['oracle_runtime']]if p.get('oracle_runtime')else [])+([p['docker_runtime']['helper'],p['docker_runtime']['profile']]if p.get('docker_runtime')else [])


@contextmanager
def context(row,*,completed=False,invocation=None):
    p=read(row)
    if p.get('schema_version')=='fleet-failed-package-first-step-context/v1':
        require(not completed and invocation is None,'failed history is not an owning package invocation')
        with failed_start_context(row)as loaded:yield loaded
        return
    if p.get('remaining_start'):
        with remaining_start_context(row,invocation=invocation)as loaded:yield loaded
        return
    if p.get('completed_start'):
        with docker_runtime(p):
            with oracle_runtime(p):
                with completed_start_context(row,invocation=invocation)as loaded:yield loaded
        return
    require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed package continuation required')
    for item in evidence(p):require(binding(item['path'])==item,'package context evidence changed')
    integration=read(p['integration'])
    require(integration['retained_helper']==p['context_helper'] and integration['retained_plan']==p['context_plan'],'package retained context changed')
    M=module('_package_retained_context',p['context_helper'])
    with M.context(p['context_plan'],initial=False)as(old,origin,_,D,env,P,w,previous,A):
        require(old['workbook']==p['workbook'] and old['source']==p['source'] and origin['original_job']==p['original_job'],'original package source/job changed')
        config=env[5];home=Path(p['home']);ops=Path(p['operations'])
        require(home.parent==ops.parent==Path(config['run_root'])/'dispatcher-v2' and home!=ops and home!=Path(old['home'])
            and type(p['fence'])is int and p['fence']>old['fence'] and p['owner'],'fresh package continuation identity required')
        R=module('_selected_package_recovery',integration['recovery']);R.code(p['integration'])
        s,_,controls=R.C.selected(p['descriptor']);cp=read(p['checkpoint'])
        require(controls==env[-1] and s==w and cp.get('descriptor')==p['descriptor'] and cp['status']=='STEP_COMPLETE'and cp['step']=='normalization_phase2','original completed phase2 selection changed')
        current=R.decode(cp['selection']);require(current.content.bindings['original_raw_source']==p['source'],'original package source changed')
        S=R.source_runtime(p['integration']);_,_,cli,tp=S.profile(R,p['integration'])
        require(Path(cli['work'])==home/'prepare/recovery/package' and tp['selection']['owner']==p['owner'],'declared package output/lease owner changed')
        with S.takeover(R,tp)as T:before,after,lease=T.validate_completion(integration['takeover_completion'])
        S.lease(R,p['integration'],p['descriptor'],p['checkpoint'],R.C.decode(cp['lease']),after,lease)
        with S.analysis_scope(R,p['integration'],p['checkpoint']):
            if completed:completed_environment(p,R)
            yield p,M,D,env,A,R,lease


def active(p):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'] and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
        and int(os.environ['HARBOR_FLEET_FENCE'])==p['fence'],'current package recovery claim required')
    result=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
    origin=read(read(p['previous'])['previous'])if p.get('remaining_start')else (read(p['previous'])if p.get('completed_start')else p)
    integration=read(origin['integration']);snapshot=read(integration['snapshot'])
    require(result['source_sha256']==p['source']['sha256'] and result['version']==Path(snapshot['root']).name,'current package claim source/version changed')
    return result


def successful(R,outcome,action,arguments,integration):
    value=read(outcome);directory=Path(outcome['path']).parent;resource=read(value['resource']);intent=read(resource['intent'])
    require(value==dict(status='COMPLETED_OPERATION',result=binding(directory/'result.json'),resource=binding(directory/'resource.json'),automatic_retry=False,source_invalidity_claimed=False,publication_performed=False)
        and resource['returncode']==0 and resource['failure']is None and resource['log']==binding(directory/'step.log') and resource['intent']==binding(directory/'intent.json'), 'original package guard did not complete')
    require(intent==dict(schema_version=R.SCHEMA,action=action,arguments=arguments,integration=integration,dispatch_commands=action=='execute',code=R.code(integration),executable=binding(Path(sys.executable).resolve()),timeout_seconds=R.guard_seconds(integration),memory_limit_bytes=R.MEMORY),'original package guarded action changed')
    return value['result'],read(value['result'])


def completed_environment(p,R):
    home=Path(p['home']);prepared=binding(home/'prepare/recovery/prepared.json')
    result,value=successful(R,binding(home/'execute/outcome.json'),'execute',dict(prepared=prepared),p['integration'])
    checked=R.completed_package(prepared,active=False)
    after=binding(home/'prepare/recovery/after.json');state=read(after)
    R.validate_gate_state(read(checked[0]['before']),state,checked[5])
    issued=R.C.decode(value['lease']);prior=R.C.decode(checked[0]['lease'])
    require((issued.owner,issued.token,issued.revision,issued.sha256)==(prior.owner,prior.token,state['revision'],after['sha256']),'completed environment lease detached from original CAS')
    require(value==read(binding(home/'prepare/recovery/result.json')) and value['status']=='ENVIRONMENT_READY'
        and value['descriptor']==p['descriptor'] and value['selection']==R.encode(checked[2]),'actual package environment checkpoint changed')
    done=read(binding(home/'prepare/recovery/complete.json'))
    require(done==dict(result=binding(home/'prepare/recovery/result.json'),checkpoint=binding(Path(checked[0]['refresh']['path']).parent/'continuation/environment.json')),'original environment commit receipt changed')
    return result,value


def selected_dispatcher(p,D,env,A,row):
    # Use the same generic original-run adapter as normalization continuation;
    # this reader supplies the completed package ancestry for every cold child.
    from types import ModuleType,FunctionType
    base,I=env[:2];common=dict(base=base,reader_plan=row,child_wrapper=p['helper'],reader_evidence=evidence(p))
    b=SimpleNamespace(**base.__dict__);b.run=A.select_run(base,kind='environment',producer_code=binding(base.__file__),**common)
    i=SimpleNamespace(**I.__dict__);i.run=A.select_run(I,kind='instruction',producer_code=binding(I.__file__),**common)
    b._run_in_process_group=A.select_run(D,kind='renewal',producer_code=binding(D.__file__),**common).declared_runner
    clone=ModuleType(D.__name__);clone.__dict__.update(D.__dict__)
    for name,value in D.__dict__.items():
        if isinstance(value,FunctionType)and value.__globals__ is D.__dict__:
            f=FunctionType(value.__code__,clone.__dict__,value.__name__,value.__defaults__,value.__closure__);f.__kwdefaults__=value.__kwdefaults__;clone.__dict__[name]=f
    def setup(job):
        observed=D.setup(job);require(observed[:2]==env[:2],'original dispatcher modules changed');return b,i,*observed[2:]
    clone.setup=setup
    return clone,(b,i,*env[2:])


def owning_child(row,kind,intent):
    with context(row,completed=True,invocation=(kind,intent))as(p,M,D,env,A,R,lease):
        active(p);producer={'environment':env[0],'instruction':env[1],'renewal':D}[kind]
        A.validate_child(producer,base=env[0],kind=kind,reader_plan=row,child_wrapper=p['helper'],intent=intent,reader_evidence=evidence(p))
        return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)


def run(row):
    if read(row).get('remaining_start'):return run_remaining_start(row)
    if read(row).get('completed_start'):return run_completed_start(row)
    with context(row)as(p,M,D,env,A,R,lease):
        active(p);home=Path(p['home']);require(not home.exists() and not Path(p['operations']).exists(),'package continuation already attempted')
        R.context(p['descriptor'],p['checkpoint'],lease,integration=p['integration'])
        D.operation_namespace(env[5],p['operations'])
        home.mkdir(mode=0o700);R.keep(home/'intent.json',dict(plan=row,automatic_retry=False))
        lb=R.keep(home/'lease.json',R.C.encode(lease))
        args=dict(descriptor=p['descriptor'],checkpoint=p['checkpoint'],outcome=p['failed_outcome'],lease=lb,integration=p['integration'])
        outcome=R.run('prepare',args,env[-1],home/'prepare');_,prepared=successful(R,outcome,'prepare',args,p['integration'])
        require(prepared==binding(home/'prepare/recovery/prepared.json'),'package preparation belongs elsewhere')
        outcome=R.run('execute',dict(prepared=prepared),env[-1],home/'execute',dispatch_commands=True)
        checkpoint,value=completed_environment(p,R)
        DD,chosen=selected_dispatcher(p,D,env,A,row)
        outcome=chosen[1].run('start',dict(environment_descriptor=p['descriptor'],checkpoint=checkpoint,worker_run=str(Path(env[5]['run_root'])/'instruction')),env[-1],home/'instruction-start')
        DD.successful(outcome,chosen)
        job=dict(read(p['original_job']),outcome=outcome,operations=p['operations']);jb=R.keep(home/'authoring-job.json',job)
        DD.run(jb);held=read(binding(Path(p['operations'])/'hold.json'));cp,result=DD.successful(held['outcome'],chosen)
        return R.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,job=jb,outcome=held['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


START_FIELDS={'schema_version','helper','completed_start','previous','takeover_profile','takeover_completion',
    'stopped','original_job','workbook','source','owner','fence','home','operations'}


ORACLE_SCAN_AST=(
    '7b1781883a8be0dfc24037699022fe4470bc27e9cba0396b6884efe61b07374a',
    'a5a61a47efff51048b58ba5c330a7737ea8ed8ad07810cb850a73fa4c88c261c')


@contextmanager
def docker_runtime(p):
    selected=p.get('docker_runtime')
    if selected is None:
        yield;return
    require(set(selected)=={'helper','profile'}and p.get('completed_start')is True,'closed completed-package Docker command selection required')
    helper=module('_package_docker_command',selected['helper'])
    require(read(selected['profile'])['helper']==selected['helper'],'Docker runtime helper detached')
    with helper.installed(selected['profile'],entrypoint=p['helper'],workbook=p['workbook'],source=p['source']):yield


@contextmanager
def oracle_runtime(p):
    """Select the proved one-pass mask scan; every original gate still executes."""
    if 'oracle_runtime'not in p:
        yield;return
    profile=read(p['oracle_runtime']);old=read(p['previous']);integration=read(old['integration']);root=Path(read(integration['snapshot'])['root'])
    require(set(profile)=={'schema_version','runtime','entrypoint','read_call','patches','guard_modules','child_plans','journal_roots','dependencies','symbols'}
        and profile['schema_version']=='fleet-retained-runtime/v1'and profile['entrypoint']==p['helper']
        and profile['read_call']==dict(function='context',args=[],kwargs={})
        and all(profile[k]==[]for k in('patches','guard_modules','child_plans','journal_roots','dependencies'))
        and len(profile['symbols'])==1,'only the proved original oracle scan may be selected')
    symbol=profile['symbols'][0]
    require(set(symbol)=={'module_name','original','selected','names'}and symbol['module_name']=='xl_mcp_oracle'
        and symbol['names']==['check_workbook']and symbol['original']==binding(root/'xl_mcp_oracle.py'),
        'oracle runtime detached from selected package snapshot')
    trees=[]
    for row,expected in zip((symbol['original'],symbol['selected']),ORACLE_SCAN_AST):
        require(binding(row['path'])==row,'oracle implementation bytes changed')
        tree=ast.parse(Path(row['path']).read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='check_workbook']
        require(len(functions)==1 and hashlib.sha256(ast.dump(functions[0],include_attributes=False).encode()).hexdigest()==expected,
            'oracle scan differs from proved equality-preserving change')
        trees.append(ast.dump(ast.Module(body=[n for n in tree.body if n is not functions[0]],type_ignores=[]),include_attributes=False))
    require(trees[0]==trees[1],'oracle runtime changed unrelated code')
    runtime=module('_package_oracle_runtime',profile['runtime'])
    with runtime.installed(p['oracle_runtime']):yield


def interrupted_start(p,old,O,D,env,A,checkpoint):
    stop=read(p['stopped']);home=Path(old['home'])/'instruction-start'
    require(set(stop)=={'schema_version','directory','tree','worker_outcome','process','claim'}
        and stop['schema_version']=='fleet-unstarted-package-instruction/v1'and stop['directory']==str(home),
        'closed original interrupted startup required')
    actual={}
    for path in home.rglob('*'):
        require(not path.is_symlink()and path.is_file(),'unexpected interrupted startup entry')
        actual[str(path.relative_to(home))]=binding(path)['sha256']
    require(actual==stop['tree']and set(actual)=={'intent.json','step.log','declared-runtime-adapter.json','dependency.json'}
        and (home/'step.log').read_bytes()==b'', 'interrupted startup has later effects')
    intent=binding(home/'intent.json');iv=read(intent);side=read(binding(home/'declared-runtime-adapter.json'))
    seconds=A.contract(env[1],env[0],'instruction',intent,side['original_command'])
    expected=[sys.executable,'-I','-B',old['helper']['path'],'owning-child','--plan',p['previous']['path'],
        '--plan-sha256',p['previous']['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    require(iv['action']=='start'and iv['arguments']==dict(environment_descriptor=old['descriptor'],checkpoint=checkpoint,
        worker_run=str(Path(env[5]['run_root'])/'instruction'))
        and side==dict(schema_version=A.SCHEMA,adapter=binding(A.__file__),producer_code=binding(env[1].__file__),
            base_code=binding(env[0].__file__),kind='instruction',reader_plan=p['previous'],child_wrapper=old['helper'],
            reader_evidence=O.evidence(old),intent=intent,original_command=side['original_command'],selected_command=expected,
            timeout_seconds=seconds,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,
            semantic_budget_reset=False,automatic_retry=False),'original interrupted command changed')
    read(binding(home/'dependency.json'))
    outer=read(stop['worker_outcome']);wi=read(outer['intent']);job=read(wi['job']);process=read(stop['process'])
    require(set(outer)=={'schema_version','status','intent','returncode','failure_type','log','metrics','result','publication_performed','automatic_retry'}
        and outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and outer['failure_type']=='TimeoutError'and outer['result']is None and outer['returncode']is None
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['result']==str(Path(old['home'])/'result.json')
        and process['pid']>0 and process['job']==wi['job']and process['automatic_retry']is False,'different stopped original worker')
    claim=read(stop['claim'])
    require(set(wi)=={'job','owner','claim_id','automatic_retry','created_at_ns'}and wi['automatic_retry']is False
        and claim['claim_id']==wi['claim_id']and claim['owner']==old['owner']and claim['fence']==old['fence']
        and claim['workbook']==old['workbook']==job['workbook']and claim['source_sha256']==old['source']['sha256']
        and claim['version']==job['version']and job['source']==old['source']
        and stop['claim']==binding(Path(outer['intent']['path']).parent/'claim.json')
        and outer['log']==binding(Path(outer['intent']['path']).parent/'worker.log')
        and job['command']==[sys.executable,'-I','-B',old['helper']['path'],'run','--plan',p['previous']['path'],
            '--plan-sha256',p['previous']['sha256']], 'interrupted original claim/job identity changed')
    stat=Path('/proc')/str(process['pid'])/'stat'
    if stat.exists():
        ticks=stat.read_text().rsplit(')',1)[1].split()[19]
        require(ticks!=process['start_ticks'],'original interrupted worker is still running')
    return stop


def immutable_tree(root):
    root=Path(root);require(root.is_dir()and not root.is_symlink(),'original package receipt tree missing')
    result={}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'package receipt tree contains link')
        if path.is_file():result[str(path.relative_to(root))]=binding(path)['sha256']
        else:require(path.is_dir(),'unexpected package receipt entry')
    return result


def package_history(p,old,R,checkpoint):
    require(binding(p['source']['path'])==p['source'],'original package source bytes changed')
    prepared=binding(Path(old['home'])/'prepare/recovery/prepared.json')
    return dict(schema_version='fleet-completed-package-history/v1',previous=p['previous'],
        checkpoint=checkpoint,integration=old['integration'],prepared=prepared,
        directory=str(Path(prepared['path']).parent),tree=immutable_tree(Path(prepared['path']).parent),
        source=p['source'],code=R.code(old['integration']))


def startup_lease_history(p,C,old_lease,new_lease):
    """Authenticate an original startup renewal without altering old bindings."""
    import copy
    from dataclasses import asdict
    home=Path(p['home'])/'instruction-start';path=home/'lease-renewal.json'
    if old_lease==new_lease:
        require(not path.exists(),'unchanged startup lease has unexpected renewal receipt')
        return None
    receipt=binding(path);value=read(receipt);old=C.decode(old_lease);new=C.decode(new_lease)
    completed=read(p['takeover_completion']);before_row=completed['after'];before=read(before_row)
    tracker=read(p['takeover_profile'])['selection']['tracker']['path'];current=read(binding(tracker))
    require(value==dict(previous=old_lease,lease=new_lease,tracker=dict(path=tracker,sha256=new.sha256))
        and before_row['sha256']==old.sha256 and before['revision']==old.revision
        and new.revision==old.revision+1 and new.owner==old.owner and new.token==old.token
        and before['bindings']['original_raw_source']==current['bindings']['original_raw_source']==p['source'],
        'startup renewal detached from original takeover and source')
    prefix=before['handoffs'];require(current['handoffs'][:len(prefix)]==prefix
        and len(current['handoffs'])>len(prefix),'startup renewal missing from current history')
    event=current['handoffs'][len(prefix)];prior=before['leases']['task_agent'];after=event['after']
    require(event['schema_version']==C.lifecycle.EVENT_SCHEMA and event['operation']=='renew'
        and event['lease_kind']=='task_agent' and event['before']==prior and event['revision']==new.revision
        and event['prior_tracker_sha256']==old.sha256 and prior['owner']==old.owner and prior['token']==old.token
        and after['owner']==new.owner and after['token']==new.token
        and prior['heartbeat_at']<=event['at']<prior['expires_at']
        and after['heartbeat_at']==event['at'] and after['expires_at']-event['at']==C.DURATION,
        'startup renewal is not the original live same-owner operation')
    reconstructed=copy.deepcopy(before);reconstructed['leases']['task_agent']=after
    reconstructed['handoffs']=[*prefix,event];reconstructed['revision']=new.revision;reconstructed['updated_at']=event['at']
    C.lifecycle.validate_lifecycle(reconstructed)
    require(hashlib.sha256(C.lifecycle.canonical(reconstructed)).hexdigest()==new.sha256,
        'startup renewal historical bytes differ from actual original receipt')
    return receipt


def historical_environment(p,old,O,D,env,A,R,row,invocation):
    """Use the actual owning startup as the historical package boundary.

    The package's task stage legitimately changes during later instruction work.
    Its immutable original receipts, inputs and consumed Calls remain checked.
    """
    home=Path(p['home']);checkpoint=binding(Path(old['home'])/'execute/result.json')
    receipt=binding(home/'package-history.json');history=read(receipt)
    require(history==package_history(p,old,R,checkpoint),'completed package immutable history changed')
    prepared=history['prepared'];R.validate(prepared,active=False)
    result,value=O.successful(R,binding(Path(old['home'])/'execute/outcome.json'),'execute',dict(prepared=prepared),old['integration'])
    require(result==checkpoint and value['status']=='ENVIRONMENT_READY'and value['descriptor']==old['descriptor']
        and value==read(binding(Path(prepared['path']).parent/'result.json')),'original environment checkpoint changed')
    R.source_runtime(old['integration']).completed_commands(R,old['integration'],Path(prepared['path']).parent/'package')
    current,start=D.successful(binding(home/'instruction-start/outcome.json'),env)
    handoff=binding(home/'environment-handoff.json');hv=read(handoff)
    intent=binding(home/'instruction-start/intent.json');iv=read(intent)
    require(iv['action']=='start'and iv['arguments']==dict(environment_descriptor=old['descriptor'],checkpoint=handoff,
        worker_run=str(Path(env[5]['run_root'])/'instruction'))and start['status']=='INSTRUCTION_READY'
        and start['selection']==value['selection']
        and receipt in hv['evidence'],'original startup lost completed package history')
    startup_lease_history(p,env[0],hv['lease'],start['lease'])
    historical_adapter(row,p,env,A,intent)
    descriptor=read(start['descriptor'])
    require(descriptor['initial']==dict(selection=start['selection'],lease=start['lease'])
        and handoff in descriptor['origin'],'original startup descriptor changed')
    ops=Path(p['operations']);dirs=sorted(x for x in ops.iterdir()if x.is_dir())if ops.exists()else []
    require(len(dirs)<=40 and [x.name for x in dirs]==['%03d'%i for i in range(len(dirs))],
        'completed package tail is not contiguous')
    for directory in dirs:
        intent=binding(directory/'intent.json');iv=read(intent)
        require(iv['action']=='step'and iv['arguments']['checkpoint']==current
            and iv['arguments']['descriptor']==start['descriptor'],'package tail lost actual predecessor')
        historical_adapter(row,p,env,A,intent)
        if not(directory/'outcome.json').exists():
            require(invocation==('instruction',intent)and directory==dirs[-1],'unsealed package tail cannot replay')
            break
        current,_=D.successful(binding(directory/'outcome.json'),env)
    return checkpoint,value


def historical_adapter(row,p,env,A,intent):
    side=read(binding(Path(intent['path']).parent/'declared-runtime-adapter.json'))
    seconds=A.contract(env[1],env[0],'instruction',intent,side['original_command'])
    expected=[sys.executable,'-I','-B',p['helper']['path'],'owning-child','--plan',row['path'],
        '--plan-sha256',row['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    require(side==dict(schema_version=A.SCHEMA,adapter=binding(A.__file__),producer_code=binding(env[1].__file__),
        base_code=binding(env[0].__file__),kind='instruction',reader_plan=row,child_wrapper=p['helper'],
        reader_evidence=evidence(p),intent=intent,original_command=side['original_command'],selected_command=expected,
        timeout_seconds=seconds,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,
        semantic_budget_reset=False,automatic_retry=False),'completed package child declaration changed')


@contextmanager
def completed_start_context(row,*,invocation=None):
    from dataclasses import asdict
    p=read(row);require(set(p)-{'oracle_runtime','docker_runtime'}==START_FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and p['completed_start']is True,'closed completed package continuation required')
    old=read(p['previous']);O=module('_original_completed_package',old['helper'])
    require(old['workbook']==p['workbook']and old['source']==p['source']and old['original_job']==p['original_job']
        and p['fence']>old['fence']and p['owner']!=old['owner']and p['home']!=old['home']
        and p['operations']!=old['operations'],'completed package continuation reused identity or source')
    with O.context(p['previous'])as(_,M,D,env,A,R,prior):
        checkpoint,value=(historical_environment(p,old,O,D,env,A,R,row,invocation)
            if (Path(p['home'])/'instruction-start/outcome.json').exists()else O.completed_environment(old,R))
        interrupted_start(p,old,O,D,env,A,checkpoint)
        base=Path(env[5]['run_root'])/'dispatcher-v2'
        require(Path(p['home']).parent==Path(p['operations']).parent==base and p['home']!=p['operations'],
            'new completed-package namespace must remain in original run')
        tp=read(p['takeover_profile']);integration=read(old['integration']);S=R.source_runtime(old['integration'])
        require(tp['shared']==read(integration['takeover_profile'])['shared']and tp['original_snapshot']==read(integration['takeover_profile'])['original_snapshot'],
            'completed package takeover implementation changed')
        with S.takeover(R,tp)as T:
            before,after,lease=T.validate_completion(p['takeover_completion']);done=read(p['takeover_completion']);stop=read(done['stopped_owner_proof'])
            previous=env[0].decode(value['lease'])
            require(T._selection(stop['selection'])==T._selection(tp['selection'])and tp['selection']['checkpoint']==checkpoint
                and tp['selection']['prior_completion']==integration['takeover_completion']and tp['selection']['owner']==p['owner']
                and stop['before']['sha256']==previous.sha256 and before['revision']==previous.revision
                and (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(previous.owner,previous.token)
                and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==p['source'],
                'completed package takeover detached from actual environment CAS')
            handoff=Path(p['home'])/'environment-handoff.json'
            extra=[binding(Path(p['home'])/'package-history.json')]if (Path(p['home'])/'package-history.json').exists()else []
            expected=dict(value,lease=env[0].encode(lease),evidence=[*value['evidence'],checkpoint,p['previous'],p['takeover_profile'],p['takeover_completion'],*extra])
            if handoff.exists():require(read(binding(handoff))==expected,'completed package handoff changed')
            yield p,O,D,env,A,R,lease


def run_completed_start(row):
    with context(row)as(p,O,D,env,A,R,lease):
        active(p);old=read(p['previous']);home=Path(p['home']);ops=Path(p['operations'])
        require(not home.exists()and not ops.exists()and not(Path(env[5]['run_root'])/'instruction').exists(),
            'completed instruction startup already attempted')
        tp=read(p['takeover_profile']);done=read(p['takeover_completion'])
        require(binding(tp['selection']['tracker']['path'])['sha256']==done['after']['sha256'],'current takeover tracker changed')
        value=read(binding(Path(old['home'])/'execute/result.json'));checkpoint=binding(Path(old['home'])/'execute/result.json')
        D.operation_namespace(env[5],p['operations']);home.mkdir();R.keep(home/'intent.json',dict(plan=row,automatic_retry=False))
        history=R.keep(home/'package-history.json',package_history(p,old,R,checkpoint))
        handoff=R.keep(home/'environment-handoff.json',dict(value,lease=env[0].encode(lease),
            evidence=[*value['evidence'],checkpoint,p['previous'],p['takeover_profile'],p['takeover_completion'],history]))
        DD,chosen=selected_dispatcher(p,D,env,A,row)
        outcome=chosen[1].run('start',dict(environment_descriptor=old['descriptor'],checkpoint=handoff,
            worker_run=str(Path(env[5]['run_root'])/'instruction')),env[-1],home/'instruction-start')
        DD.successful(outcome,chosen)
        job=R.keep(home/'authoring-job.json',dict(read(p['original_job']),outcome=outcome,operations=p['operations']))
        DD.run(job);held=read(binding(ops/'hold.json'));cp,result=DD.successful(held['outcome'],chosen)
        return R.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,job=job,
            outcome=held['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


REMAINING_FIELDS={'schema_version','helper','remaining_start','previous','history_helper','start_outcome',
    'interrupted','stopped','original_job','workbook','source','owner','fence','home','operations','checkpoint_lease'}


def startup_history_delta(original,selected):
    """Only the reviewed exact startup-renewal history predicate may differ."""
    import inspect,textwrap
    before=ast.parse(Path(original.__file__).read_text());after=ast.parse(Path(selected.__file__).read_text())
    wanted=ast.parse(textwrap.dedent(inspect.getsource(startup_lease_history))).body[0]
    additions=[n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='startup_lease_history']
    require(len(additions)==1 and ast.dump(additions[0])==ast.dump(wanted),'different startup lease history predicate')
    after.body.remove(additions[0])
    old=next(n for n in before.body if isinstance(n,ast.FunctionDef)and n.name=='historical_environment')
    new=next(n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='historical_environment')
    text=ast.unparse(old);anchor=" and (start['lease'] == hv['lease'])"
    require(text.count(anchor)==1,'original startup history assertion changed')
    text=text.replace(anchor,'')
    anchor='historical_adapter(row, p, env, A, intent)'
    require(text.count(anchor)==2,'original history adapter call count changed')
    text=text.replace(anchor,"startup_lease_history(p, env[0], hv['lease'], start['lease'])\n    "+anchor,1)
    require(ast.dump(ast.parse(text).body[0])==ast.dump(new),'startup history changed beyond lease predicate')
    before.body[before.body.index(old)]=new
    require(ast.dump(before)==ast.dump(after),'other completed package history code changed')


@contextmanager
def selected_startup_history(O,H):
    from types import FunctionType
    startup_history_delta(O,H)
    old=O.historical_environment
    require('startup_lease_history'not in O.__dict__,'nested startup history selection')
    O.startup_lease_history=FunctionType(H.startup_lease_history.__code__,O.__dict__)
    O.historical_environment=FunctionType(H.historical_environment.__code__,O.__dict__)
    try:yield
    finally:
        O.historical_environment=old;del O.startup_lease_history


def remaining_setup(row):
    p=read(row)
    require(set(p)in (REMAINING_FIELDS,(REMAINING_FIELDS-{'checkpoint_lease'})|{'checkpoint_takeover'}) and p['schema_version']==SCHEMA and p['remaining_start']is True
        and p['helper']==binding(__file__),'closed completed-start remaining continuation required')
    old=read(p['previous']);require(old.get('completed_start')is True and p['fence']==old['fence']+1
        and (p['owner']==old['owner']if 'checkpoint_lease'in p else isinstance(p['owner'],str)and bool(p['owner'].strip())and p['owner']!=old['owner'])
        and p['source']==old['source'] and p['workbook']==old['workbook']
        and p['original_job']==old['original_job'] and p['home']!=old['home'] and p['operations']!=old['operations'],
        'remaining startup changed source or consumed identity')
    require(binding(p['source']['path'])==p['source'],'original startup source bytes changed')
    O=module('_completed_start_original',old['helper']);H=module('_completed_start_history',p['history_helper'])
    startup_history_delta(O,H)
    original=read(old['previous']);integration=read(original['integration']);snapshot=read(integration['snapshot'])
    job=read(p['original_job']);require(Path(job['driver']['path']).is_relative_to(snapshot['root']),
        'original dispatcher left package snapshot')
    D=module('_completed_start_dispatcher',job['driver']);env=D.setup(job)
    require(str(env[6])==snapshot['root'],'remaining startup snapshot differs')
    retained=read(integration['retained_plan']);A=module('_completed_start_adapter',retained['campaign_adapter'])
    require(Path(p['home']).parent==Path(p['operations']).parent==Path(env[5]['run_root'])/'dispatcher-v2'
        and p['home']!=p['operations'],'remaining startup namespace escaped original run')
    if 'checkpoint_takeover'in p:
        require(set(p['checkpoint_takeover'])=={'helper','profile'},'closed completed initial takeover required')
        K=L=None;B=module('_package_checkpoint_takeover',p['checkpoint_takeover']['helper'])
    else:
        require(set(p['checkpoint_lease'])=={'helper','renewal','bridge'},'closed original checkpoint renewal modules required')
        K,L,B=(module('_package_checkpoint_'+key,p['checkpoint_lease'][key])for key in ('helper','renewal','bridge'))
    return p,old,O,H,D,env,A,K,L,B


@contextmanager
def remaining_takeover(p,old,D,env,B):
    selected=p['checkpoint_takeover'];profile=read(selected['profile']);tp=read(profile['takeover_plan'])
    original=read(old['previous']);integration=read(original['integration']);previous_tp=read(old['takeover_profile'])
    checkpoint,value=D.successful(p['start_outcome'],env)
    require(profile['initial_start']is True and profile['implementation']==selected['helper']
        and profile['outcome']==p['start_outcome'] and profile['result']==checkpoint
        and profile['descriptor']==value['descriptor'] and profile['guard']==read(p['start_outcome'])['resource']
        and profile['source']==p['source'] and profile['workbook']==p['workbook']
        and profile['directory']==str(Path(p['home'])/'checkpoint-takeover')
        and tp['original_snapshot']==previous_tp['original_snapshot'] and tp['shared']==previous_tp['shared']
        and tp['selection']['owner']==p['owner'] and tp['selection']['prior_completion']==old['takeover_completion']
        and tp['selection']['checkpoint']==old['takeover_completion']
        and profile['prior_takeover_chain']['original_tracker']==read(read(profile['completion'])['stopped_owner_proof'])['before']
        and profile['prior_takeover_chain']['original_tracker']['sha256']==read(p['stopped'])['tracker']['sha256'],
        'initial takeover detached from actual startup or original T chain')
    R=module('_remaining_package_recovery',integration['recovery']);S=R.source_runtime(original['integration'])
    with S.takeover(R,tp)as T:yield T


def interrupted_initial_step(p,old,O,D,env,A,*,live):
    stop=read(p['stopped']);directory=Path(p['interrupted']['path']).parent
    require(set(stop)=={'schema_version','previous','worker_outcome','process','claim','tracker','files'}
        and stop['schema_version']=='fleet-interrupted-package-first-step/v1' and stop['previous']==p['previous']
        and directory==Path(old['operations'])/'000','closed original first-step interruption required')
    require({x.name for x in Path(old['operations']).iterdir()}=={'000','job.json'}
        and set(stop['files'])=={'intent.json','declared-runtime-adapter.json','step.log'}
        and {x.name for x in directory.iterdir()}==set(stop['files']),'first instruction step has later effects')
    for name,item in stop['files'].items():require(binding(directory/name)==item,'interrupted instruction bytes changed')
    require(p['interrupted']==stop['files']['intent.json'] and (directory/'step.log').read_bytes()==b'',
        'interrupted first child has task output')
    checkpoint,value=D.successful(p['start_outcome'],env)
    require(p['start_outcome']==binding(Path(old['home'])/'instruction-start/outcome.json')
        and value['status']=='INSTRUCTION_READY' and value['checkpoint']is None
        and read(p['interrupted'])['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
        and read(p['interrupted'])['action']=='step','remaining step detached from actual startup')
    O.historical_adapter(p['previous'],old,env,A,p['interrupted'])
    O.historical_adapter(p['previous'],old,env,A,binding(Path(old['home'])/'instruction-start/intent.json'))
    outer=read(stop['worker_outcome']);wi=read(outer['intent']);job=read(wi['job']);claim=read(stop['claim']);process=read(stop['process'])
    require(set(outer)=={'schema_version','status','intent','returncode','failure_type','log','metrics','result','publication_performed','automatic_retry'}
        and outer['schema_version']=='harbor-fleet-worker-outcome/v1' and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['returncode']is None and outer['result']is None and outer['failure_type']in {'RuntimeError','TimeoutError'}
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and wi['owner']==old['owner'] and wi['automatic_retry']is False
        and claim['claim_id']==wi['claim_id'] and claim['owner']==old['owner'] and claim['fence']==old['fence']
        and claim['source_sha256']==old['source']['sha256'] and claim['version']==job['version']
        and claim['workbook']==job['workbook']==old['workbook'] and job['driver']==old['helper']
        and job['source']==old['source'] and job['result']==str(Path(old['home'])/'result.json')
        and job['command']==[sys.executable,'-I','-B',old['helper']['path'],'run','--plan',p['previous']['path'],'--plan-sha256',p['previous']['sha256']]
        and process['job']==wi['job'] and process['automatic_retry']is False
        and stop['worker_outcome']==binding(Path(job['run'])/'outcome.json')
        and stop['claim']==binding(Path(outer['intent']['path']).parent/'claim.json')
        and outer['log']==binding(Path(outer['intent']['path']).parent/'worker.log'),
        'interrupted worker or claim identity changed')
    stat=Path('/proc')/str(process['pid'])/'stat'
    if stat.exists():require(stat.read_text().rsplit(')',1)[1].split()[19]!=str(process['start_ticks']),'old worker remains active')
    needles=(job['run'],p['previous']['path'],str(directory))
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            args=(entry/'cmdline').read_bytes().decode(errors='replace').split(chr(0))
        except(FileNotFoundError,PermissionError):continue
        require(not any(a==n or a.startswith(n+'/')for n in needles for a in args),'original child remains active')
    worker,_,_=env[1].selected(value['descriptor'])
    if live:
        require(not worker.run.exists() and binding(stop['tracker']['path'])==stop['tracker']
            and stop['tracker']['sha256']==env[0].decode(value['lease']).sha256,'original first-step custody advanced')
        env[3].active(worker,env[1].codec.decode(value['selection']),env[0].decode(value['lease']))
    return checkpoint,value


@contextmanager
def remaining_start_context(row,*,invocation=None):
    from contextlib import ExitStack
    p,old,O,H,D,env,A,K,L,B=remaining_setup(row)
    interrupted_initial_step(p,old,O,D,env,A,live=False)
    with ExitStack()as stack:
        stack.enter_context(selected_startup_history(O,H))
        _,_,D,env,A,R,_=stack.enter_context(O.context(p['previous'],invocation=('instruction',p['interrupted'])))
        handoff=binding(Path(p['home'])/('checkpoint-takeover'if K is None else 'checkpoint-lease')/'handoff.json')
        first=Path(p['home'])/'first-step/outcome.json'
        if K is None:
            T=stack.enter_context(remaining_takeover(p,old,D,env,B))
            stack.enter_context(B.prior_scope(handoff,p['checkpoint_takeover']['profile'],env[1],env[3],T,
                lambda out:D.successful(out,env),completed_successor=lambda:binding(first)if first.exists()else None))
        else:stack.enter_context(K.installed(handoff,I=env[1],W=env[3],L=L,B=B,life=env[0].lifecycle))
        state=read(handoff);current=state['result'];value=read(current)
        dirs=[Path(p['home'])/'first-step']
        ops=Path(p['operations']);tail=sorted(x for x in ops.iterdir()if x.is_dir())if ops.exists()else []
        require(len(tail)<=40 and [x.name for x in tail]==['%03d'%i for i in range(len(tail))],'remaining startup tail is not contiguous')
        for directory in [*dirs,*tail]:
            if not directory.exists():require(not tail and invocation is None,'missing earlier remaining step');break
            intent=binding(directory/'intent.json');iv=read(intent)
            require(iv['action']=='step'and iv['arguments']==dict(descriptor=value['descriptor'],checkpoint=current),
                'remaining startup skipped actual predecessor')
            historical_adapter(row,p,env,A,intent)
            if not(directory/'outcome.json').exists():
                require(invocation==('instruction',intent)and directory==([*dirs,*tail][-1]),'unsealed remaining step cannot replay');break
            current,value=D.successful(binding(directory/'outcome.json'),env)
        lease=(B.validate(p['checkpoint_takeover']['profile'],env[1],env[3],T,lambda out:D.successful(out,env),live=not first.exists())[-1]
            if K is None else K.validate(handoff,I=env[1],W=env[3],L=L,B=B,life=env[0].lifecycle)[-1])
        yield p,O,D,env,A,R,lease


def failed_start_custody(row,p,proof,old,original,D,env,A,*,renewed=False):
    """Keep the sealed failed first step and its actual stopped outer worker."""
    stop=read(p['stopped']);failed=read(p['failure']);directory=Path(old['home'])/'first-step'
    require(set(stop)=={'schema_version','previous','worker_outcome','process','claim','tracker'}
        and stop['schema_version']=='fleet-failed-package-first-step-stop/v1'
        and stop['previous']==p['previous']and failed['outcome']==binding(directory/'outcome.json')
        and failed['handoff']==binding(Path(old['home'])/'checkpoint-takeover/handoff.json')
        and not(Path(old['home'])/'result.json').exists()
        and not Path(old['operations']).exists(),'failed package first step has later effects')
    original.historical_adapter(p['previous'],old,env,A,binding(directory/'intent.json'))
    outer=read(stop['worker_outcome']);wi=read(outer['intent']);job=read(wi['job']);claim=read(stop['claim']);process=read(stop['process'])
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['result']is None and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and wi['owner']==old['owner']and wi['automatic_retry']is False
        and claim['claim_id']==wi['claim_id']and claim['owner']==old['owner']and claim['fence']==old['fence']
        and claim['workbook']==job['workbook']==old['workbook']and claim['source_sha256']==old['source']['sha256']
        and claim['version']==job['version']and job['driver']==old['helper']and job['source']==old['source']
        and job['result']==str(Path(old['home'])/'result.json')
        and job['command']==[sys.executable,'-I','-B',old['helper']['path'],'run','--plan',p['previous']['path'],'--plan-sha256',p['previous']['sha256']]
        and process['job']==wi['job']and process['automatic_retry']is False
        and stop['worker_outcome']==binding(Path(job['run'])/'outcome.json')
        and stop['claim']==binding(Path(job['run'])/'claim.json')and outer['log']==binding(Path(job['run'])/'worker.log'),
        'failed package outer worker or claim identity changed')
    require(stop['tracker']==proof['tracker']and (renewed or binding(stop['tracker']['path'])==stop['tracker']),
        'failed package current takeover custody changed')
    needles=(job['run'],p['previous']['path'],str(directory))
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
        try:
            state=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if state[0]=='Z':continue
            require(not(int(entry.name)==process['pid']and state[19]==str(process['start_ticks'])),
                'failed package worker remains active')
            args=(entry/'cmdline').read_bytes().decode(errors='replace').split(chr(0))
        except(FileNotFoundError,PermissionError):continue
        require(not any(a==n or a.startswith(n+'/')for a in args for n in needles),
            'failed package child remains active')


@contextmanager
def failed_start_context(row):
    """Expose a failed original disclosure to its existing command repair API."""
    from contextlib import ExitStack
    p=read(row)
    require(set(p)-{'pair_scope','lease_renewal'}=={'schema_version','helper','previous','failure','bridge','stopped'}
        and p['schema_version']=='fleet-failed-package-first-step-context/v1'and p['helper']==binding(__file__),
        'closed failed package first-step context required')
    previous=read(p['previous']);original=module('_failed_package_original',previous['helper'])
    old,prior,O,H,D,env,A,K,L,B=original.remaining_setup(p['previous'])
    require(K is None and 'checkpoint_takeover'in old,'failed first-step requires actual completed takeover')
    selected=module('_failed_package_handoff',p['bridge'])
    original.interrupted_initial_step(old,prior,O,D,env,A,live=False)
    with ExitStack()as stack:
        stack.enter_context(failed_refresh_scope(p,env))
        stack.enter_context(original.selected_startup_history(O,H))
        _,_,D,env,A,R,_=stack.enter_context(O.context(old['previous'],invocation=('instruction',old['interrupted'])))
        T=stack.enter_context(original.remaining_takeover(old,prior,D,env,B))
        profile=read(old['checkpoint_takeover']['profile']);handoff=read(p['failure'])['handoff']
        receipt=Path(p['lease_renewal']['directory'])/'complete.json'if 'lease_renewal'in p else None
        renewed=receipt is not None and receipt.exists()
        failed_start_custody(row,p,profile,old,original,D,env,A,renewed=renewed)
        renew=lambda previous:failed_renewal_lease(row,p,profile,previous,env[0].lifecycle)
        stack.enter_context(selected.prior_scope(handoff,old['checkpoint_takeover']['profile'],env[1],env[3],T,
            lambda out:D.successful(out,env),failed_successor=p['failure'],original_reader=B,
            **({'failed_lease':renew}if 'lease_renewal'in p else {})))
        actual=read(handoff);_,_,lease=env[1]._prior(actual['result'],actual['descriptor'])
        # The ordinary DCC retained-loader projection uses D at index2 and env3.
        yield p,original,D,env,A,R,lease


def failed_renewal_lease(row,p,profile,previous,life):
    """Authenticate the original same-owner CAS without rewriting the T handoff."""
    selected=p['lease_renewal']
    require(set(selected)=={'module','directory'},'closed failed-step renewal selection required')
    home=Path(selected['directory']);complete=home/'complete.json'
    require(home.is_absolute()and home.name=='renewal','isolated original renewal directory required')
    L=module('_failed_package_lease',selected['module'])
    require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,
        'original bounded lease authority required')
    if not complete.exists():
        require(not home.exists()and binding(profile['tracker']['path'])==profile['tracker'],
            'unfinished renewal or changed original takeover custody')
        return previous
    value=read(binding(complete));intent=read(value['intent']);authority=intent['authority'];owner=read(authority)
    require(owner['context']['plan']==row and owner['context']['helper']==p['helper']
        and owner['context']['function']=='context'and owner['context']['manager']=='contextmanager'
        and owner['renewal_module']==selected['module']and Path(owner['output'])/'renewal'==home
        and owner['tracker']==profile['tracker']and 'prepermit_failure'in owner
        and read(profile['completion'])['after']['sha256']==profile['tracker']['sha256']
        and owner['source']==profile['source']and owner['workbook']==profile['workbook'],
        'renewal detached from exact failed takeover continuation')
    return L.validate_completed(life=life,read=read,record=binding,path=profile['tracker']['path'],previous=previous,
        completion=binding(complete),before_binding=binding(home/'before.json'),after_binding=binding(home/'after.json'),
        authority_record=authority)


@contextmanager
def failed_refresh_scope(p,env):
    """Reuse only the existing successful pair proof inside each F.checks call."""
    selected=p.get('pair_scope')
    if selected is None:
        yield
        return
    require(set(selected)=={'helper','refresh','lanes'},'closed original refresh pair scope required')
    root=Path(env[6]);F=__import__('importlib').import_module('scripts.batch.harbor_environment_refresh')
    require(selected['refresh']==binding(root/'scripts/batch/harbor_environment_refresh.py')
        and selected['lanes']==binding(root/'scripts/batch/harbor_completed_lanes.py'),
        'pair scope left the original selected package snapshot')
    P=module('_failed_package_refresh_scope',selected['helper'])
    with P.installed(F,refresh=selected['refresh'],lanes=selected['lanes']):yield


def run_remaining_start(row):
    import fcntl
    p,old,O,H,D,env,A,K,L,B=remaining_setup(row);active(p)
    checkpoint,value=interrupted_initial_step(p,old,O,D,env,A,live=K is not None)
    home=Path(p['home']);require(not home.exists()and not Path(p['operations']).exists(),'remaining startup already attempted')
    def validation(worker,selection,lease,before):
        cp,v=interrupted_initial_step(p,old,O,D,env,A,live=True)
        require(cp==checkpoint and v==value,'completed startup changed before renewal')
        H.startup_lease_history(old,env[0],read(binding(Path(old['home'])/'environment-handoff.json'))['lease'],value['lease'])
        return [p['start_outcome'],checkpoint,value['descriptor'],p['previous'],p['stopped']]
    lock=env[0]._controls(env[7]);fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if K is None:
            with remaining_takeover(p,old,D,env,B)as T:
                B.validate(p['checkpoint_takeover']['profile'],env[1],env[3],T,lambda out:D.successful(out,env))
                active(p);home.mkdir();env[0].keep(home/'intent.json',dict(plan=row,original_start=p['start_outcome'],automatic_retry=False))
                B.handoff(p['checkpoint_takeover']['profile'],env[1],env[3],T,lambda out:D.successful(out,env),env[0].keep)
        else:
            home.mkdir();env[0].keep(home/'intent.json',dict(plan=row,original_start=p['start_outcome'],automatic_retry=False))
            K.renew(checkpoint,value['descriptor'],I=env[1],W=env[3],L=L,B=B,life=env[0].lifecycle,authority=row,
                directory=home/'checkpoint-lease',keep=env[0].keep,validation=validation,custody=lambda:active(p))
    finally:os.close(fd)
    with context(row)as(p,O,D,env,A,R,_):
        handoff=read(binding(home/('checkpoint-takeover'if K is None else 'checkpoint-lease')/'handoff.json'));value=read(handoff['result'])
        DD,chosen=selected_dispatcher(p,D,env,A,row);D.operation_namespace(env[5],p['operations'])
        outcome=chosen[1].run('step',dict(descriptor=handoff['descriptor'],checkpoint=handoff['result']),env[-1],home/'first-step',models=True,commands=True,containers=True)
        DD.successful(outcome,chosen)
        job=R.keep(home/'authoring-job.json',dict(read(p['original_job']),outcome=outcome,operations=p['operations']))
        DD.run(job);held=read(binding(Path(p['operations'])/'hold.json'));cp,result=DD.successful(held['outcome'],chosen)
        return R.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,job=job,
            outcome=held['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind',choices=['environment','instruction','renewal']);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=v.plan,sha256=v.plan_sha256)
    print(json.dumps(run(row)if v.action=='run'else owning_child(row,v.kind,dict(path=v.intent,sha256=v.intent_sha256))))
