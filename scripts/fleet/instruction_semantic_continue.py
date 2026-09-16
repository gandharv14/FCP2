"""Resume a completed first dialogue review through its original second round.

The selected pipeline remains immutable. Its original semantic retry transaction,
instruction guards, model identities and dispatcher own all task transitions.
This driver selects explicit fleet custody and either a same-owner renewal or
a declared completed takeover with its retained historical reader.
"""
from pathlib import Path
import inspect
import argparse, fcntl, hashlib, importlib.util, json, os, sys, time

SCHEMA = 'fleet-instruction-semantic-continuation/v1'
AUTHORIZATION_SHA = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
FIELDS = {'schema_version','workbook','helper','fresh_plan','original_job','outcome','result',
          'descriptor','blocked','tracker','source','owner','fence','output',
          'renewal_module','authorization','capacity_policy'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'canonical evidence required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row, 'continuation evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def keep(path, value):
    path = Path(path)
    require(not any(x.is_symlink() for x in (path,*path.parents)), 'unsafe continuation journal')
    with path.open('x') as f:
        json.dump(value,f,sort_keys=True,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return binding(path)


def module(name,row):
    require(binding(row['path'])==row, 'selected implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path'])
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m)
    return m



def operations_for(dispatcher,config,output):
    """Use and preflight the original dispatcher's dedicated workbook namespace."""
    path=Path(config['run_root'])/'dispatcher-v2'/Path(output).name
    return dispatcher.operation_namespace(config,str(path))


def completed_failure(p, I, controls):
    """A real completed owning guard, not an uncertain or fabricated checkpoint."""
    out=read(p['outcome']); directory=Path(p['outcome']['path']).parent
    require(out['status']=='COMPLETED_OPERATION' and out.get('automatic_retry',False) is False
            and out['result']==p['result'] and out['resource']==binding(directory/'resource.json'), 'completed original review guard required')
    g=read(out['resource']); require(g['returncode']==0 and g['failure'] is None
            and g['intent']==binding(directory/'intent.json') and g['log']==binding(directory/'step.log'), 'uncertain review guard cannot continue')
    intent=read(g['intent']); value=read(p['result']); failed=read(p['blocked'])
    require(intent['schema_version']==I.SCHEMA and intent['code']==binding(I.__file__)
            and intent['controls']==I.base.encode(controls) and intent['action']=='step'
            and intent['arguments']['descriptor']==p['descriptor']
            and value['evidence'][0]==intent['arguments']['checkpoint'], 'original guarded review identity differs')
    seconds=I.guard_seconds('step',intent['arguments']) if hasattr(I,'guard_seconds') else I.base.SECONDS
    require(seconds==3300 and intent['timeout_seconds']==seconds
            and intent['memory_limit_bytes']==I.base.MEMORY, 'original review resource contract differs')
    require(value['status']==failed['status']=='DIALOGUE_REPAIR_REQUIRED'
            and value['next_step']==failed['step']=='dialogue-review'
            and value['publication_performed'] is False and value['checkpoint']==p['blocked']
            and value['descriptor']==p['descriptor'] and value['selection']==failed['selection'], 'exact completed first semantic rejection required')
    selection=I.codec.decode(value['selection']); lease=I.base.decode(value['lease'])
    from dataclasses import asdict
    require(asdict(lease)==failed['lease'], 'rejected checkpoint lease differs')
    return value, selection, lease


def physical_authoring(p,fresh,job):
    """Read one actually completed physical review and its original dispatcher."""
    q=read(p['physical_context']);home=Path(q['output']);original=read(q['original_job'])
    require(q['schema_version']=='fleet-disclosure-payload-continuation/v1'
        and q['workbook']==p['workbook'] and q['source']==p['source']
        and q['fresh_plan']==p['fresh_plan'] and original['driver']==fresh['dispatcher'],
        'physical review detached from original Fresh source')
    before=read(original['config']);selected=read(q['config']);snapshot=read(q['snapshot']);root=Path(snapshot['root'])
    require(before['code']==q['old_snapshot'] and selected==dict(before,code=q['snapshot']),
        'physical continuation changed original author policy')
    intent=read(binding(home/'intent.json'))
    require(intent==dict(plan=p['physical_context'],helper=q['helper'],automatic_retry=False)
        and binding(q['helper']['path'])==q['helper'], 'physical continuation original intent changed')
    guard=read(binding(home/'permit-resource.json'))
    expected=[sys.executable,'-I','-B',q['helper']['path'],'permit-child','--plan',p['physical_context']['path'],'--sha256',p['physical_context']['sha256']]
    require(guard['intent']==binding(home/'intent.json') and guard['command']==expected
        and guard['returncode']==0 and guard['failure']is None and guard['seconds']==3300
        and guard['memory_bytes']==8589934592 and guard['models']==0 and guard['automatic_retry']is False
        and guard['log']==binding(home/'permit.log'), 'original physical permit guard incomplete')
    first=binding(home/'first-review/outcome.json');operations=str(Path(selected['run_root'])/'dispatcher-v2'/home.name)
    require(p['original_job']==binding(home/'authoring-job.json')
        and job==dict(original,config=q['config'],outcome=first,operations=operations),
        'physical authoring handoff differs from completed review')
    legacy=fresh.get('legacy_research_history')
    if legacy is not None:
        value=read(legacy);require(value['workbook']==p['workbook']and value['original']==p['source'], 'physical source history detached')
        for k,v in [('HARBOR_FLEET_LEGACY_RESEARCH_PATH',legacy['path']),('HARBOR_FLEET_LEGACY_RESEARCH_SHA256',legacy['sha256'])]:
            require(os.environ.get(k)in(None,v),'foreign physical source history');os.environ[k]=v
    return fresh,job,root,q['config'],None


def authoring_context(p):
    """Fresh source or exact completed terminal-normalization handoff."""
    fresh=read(p['fresh_plan']); job=read(p['original_job'])
    require(fresh['schema_version']=='harbor-fleet-fresh-plan/v1' and p['workbook']==fresh['workbook']
        and job['publish'] is False and all(job[k] is True for k in ('models','commands','containers')),
        'unchanged native authoring policy required')
    if 'physical_context' in p:
        require('normalization_context'not in p and 'retained_context'not in p,'physical context cannot mix alternate authoring histories')
        return physical_authoring(p,fresh,job)
    if 'normalization_context' not in p:
        require(job['driver']==fresh['dispatcher'], 'original fresh dispatcher differs')
        return fresh,job,Path(fresh['code_root']),binding(Path(fresh['home'])/'authoring/config.json'),None
    context=p['normalization_context']
    require(set(context)=={'helper','plan'}, 'closed retained normalization context required')
    M=module('semantic_retained_normalization',context['helper']); n=read(context['plan'])
    require(n['schema_version']==M.SCHEMA and n['workbook']==p['workbook'] and 'terminal_rejection'in n,
        'declared terminal normalization source required')
    home=Path(n['home']); intent=read(binding(home/'intent.json')); stopped=read(binding(home/'result.json'))
    require(intent['plan']==context['plan'] and intent['driver']==context['helper']
        and stopped['plan']==context['plan'] and stopped['status']in('RECONCILIATION_REQUIRED','PUBLICATION_READY')
        and stopped['publication_performed']is False and stopped['automatic_retry']is False,
        'actual stopped normalization continuation differs')
    original=read(n['original_job'])
    require(original['driver']==fresh['dispatcher'] and original['config']==binding(Path(fresh['home'])/'authoring/config.json'),
        'normalization predecessor differs from actual fresh source')
    selected=M.successor(n,original); config=read(selected['config'])
    bridge=binding(home/'bridge-step/outcome.json'); refresh=binding(home/'refresh/outcome.json')
    expected={**selected,'outcome':bridge,'operations':str(Path(config['run_root'])/'dispatcher-v2'/home.name)}
    require(p['original_job']==binding(home/'authoring-job.json') and job==expected,
        'retained authoring job changed beyond completed refresh handoff')
    if stopped['status']=='PUBLICATION_READY':
        held=read(binding(Path(job['operations'])/'hold.json'))
        outcome=read(held['outcome']);result=read(outcome['result'])
        require(stopped['workbook']==p['workbook'] and stopped['job']==p['original_job']
            and stopped['config']==job['config'] and stopped['outcome']==held['outcome']
            and stopped['checkpoint']==outcome['result'] and outcome['status']=='COMPLETED_OPERATION'
            and result['status']=='PUBLICATION_READY' and result['publication_performed']is False,
            'completed normalization READY handoff changed')
    return fresh,job,Path(n['code_root']),selected['config'],(refresh,bridge)


def completed_refresh(p,D,env,outcome):
    """Read the original guarded refresh without treating it as a step."""
    C,_,_,_,_,_,_,controls=env
    checkpoint,value=D.successful(outcome)
    n=read(p['normalization_context']['plan']);home=Path(n['home'])
    directory=Path(outcome['path']).parent
    require(directory==home/'refresh','foreign refresh namespace')
    intent=read(binding(directory/'intent.json'))
    expected=dict(legacy=binding(home/'legacy.json'),checkpoint=n['research'],
        current=binding(home/'current.json'),lease=binding(home/'lease.json'))
    require(intent['schema_version']=='harbor-coordinator-step/v1'
        and intent['action']=='refresh' and intent['arguments']==expected
        and intent['dispatch']is False and intent['code']==binding(C.__file__)
        and intent['controls']==C.encode(controls)
        and intent['timeout_seconds']==C.SECONDS and intent['memory_limit_bytes']==C.MEMORY,
        'original refresh command or resource contract differs')
    require(binding(intent['executable']['path'])==intent['executable'],'refresh executable changed')
    descriptor=binding(directory/'descriptor.json');saved=read(descriptor)
    evidence=value['evidence'];arguments=list(intent['arguments'].values())
    require(value['status']=='IMPORTED' and value['publication_ready']is False
        and value['descriptor']==descriptor and len(evidence)==6 and evidence[2:]==arguments
        and saved['schema_version']=='harbor-environment-coordinator-refresh/v1'
        and saved['refresh']==evidence[0] and saved['origin']==arguments+evidence[:2]
        and saved['initial_lease']==value['lease'],'refresh result lost original evidence')
    C.selected(descriptor);C._prior(checkpoint,descriptor)
    return value


def validated_authoring(p):
    if 'retained_context' in p:
        return retained_authoring(p)
    fresh,job,expected_root,expected_config,handoff=authoring_context(p)
    if 'physical_context'in p:
        require('scripts'not in sys.modules,'physical context requires cold selected code')
        sys.path.insert(0,str(expected_root))
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    D=module('semantic_original_dispatcher',job['driver']);env=D.setup(job)
    C,I,P,W,renewal,config,root,controls=env
    require(root==expected_root and job['config']==expected_config and config['workbook']==p['workbook']
        and C.SECONDS==3300 and C.MEMORY==8589934592,'original configured pipeline and guards differ')
    if 'physical_context'in p:
        q=read(p['physical_context']);home=Path(q['output']);_,first=D.successful(binding(home/'first-review/outcome.json'),env)
        require(first['status']=='STEP_COMPLETE'and first['next_step']=='disclosure-commit','physical review did not complete')
        w,observed,_=I.selected(first['descriptor']);permit=binding(home/'permit/permit.json')
        require(w.disclosure_repair==permit and observed==controls,'physical handoff lost original permit')
        from scripts.batch import harbor_disclosure_review_resume as Q
        current=read(p['tracker']);Q.validate(w,current)
    if handoff is not None:
        refreshed=completed_refresh(p,D,env,handoff[0]); _,bridged=D.successful(handoff[1],env)
        require(refreshed['status']=='IMPORTED' and bridged['status']=='STEP_COMPLETE'
            and bridged['publication_ready']is False,'original normalization/source-profile handoff not complete')
    return fresh,job,D,env


def retained_authoring(p):
    """Select an authenticated historical loader's original dispatcher context.

    Tuple positions are explicit data, not inferred from a workbook identifier.
    The selected loader retains responsibility for its full prior history.
    """
    selected=p['retained_context']
    require(set(selected) in ({'helper','plan','projection','config','snapshot'},
            {'helper','plan','projection','config','snapshot','kind'})
            and ('kind' not in selected or selected['kind']in {'completed_disclosure','completed_pre_dispatch'}),
            'closed retained authoring context required')
    projection=selected['projection']
    require(set(projection)=={'environment','dispatcher','job'}
            and all(type(x)is int and 0<=x<16 for x in projection.values())
            and len(set(projection.values()))==3,'explicit distinct context positions required')
    original=module('semantic_retained_authoring',selected['helper'])
    loaded=original.context(selected['plan'],initial=False)
    require(isinstance(loaded,tuple) and max(projection.values())<len(loaded),
            'retained context result shape changed')
    env=loaded[projection['environment']];D=loaded[projection['dispatcher']]
    job=loaded[projection['job']]
    require(isinstance(env,tuple) and len(env)==8 and job==read(p['original_job'])
            and job['config']==selected['config'] and job['driver']==binding(D.__file__),
            'retained dispatcher or original job changed')
    C,I,P,W,R,config,root,controls=env
    snapshot=read(selected['snapshot'])
    if selected.get('kind')in {'completed_disclosure','completed_pre_dispatch'}:
        original.validate_inventory(selected,env,p)
    else:
        require(config==read(selected['config']) and config['code']==selected['snapshot']
                and config['workbook']==p['workbook'] and Path(snapshot['root'])==root
                and C.SECONDS==3300 and C.MEMORY==8589934592
                and all(Path(m.__file__).is_relative_to(root)for m in (C,I,P,W,R)),
                'retained owning modules, snapshot or resource contract changed')
    require(all(job[k]is True for k in ('models','commands','containers'))
            and job['publish']is False and p['fresh_plan']==selected['plan'],
            'retained continuation changed original capabilities')
    return read(selected['plan']),job,D,env


def load(value):
    """Read the original configured fresh context without checking old custody."""
    if set(value)=={'path','sha256'}:value=read(value)
    require(set(value)=={'workbook','fresh_plan','original_job'},'closed fresh lease context required')
    return validated_authoring(value)


def lease_context(p):
    if 'retained_context'in p:
        return p['retained_context']['helper'],retained_invocation(p)
    require('normalization_context'not in p and 'retained_read_call'not in p,'ordinary original fresh context required')
    require(p['helper']==binding(__file__),'fresh lease loader differs from selected helper')
    selected={key:p[key]for key in ('workbook','fresh_plan','original_job')}
    fresh=read(p['fresh_plan']);job=read(p['original_job'])
    require(fresh['schema_version']=='harbor-fleet-fresh-plan/v1'and fresh['workbook']==p['workbook']
            and job['driver']==fresh['dispatcher']and job['config']==binding(Path(fresh['home'])/'authoring/config.json'),
            'fresh lease context differs from original configured job')
    return p['helper'],dict(function='load',args=[selected],kwargs={})


def retained_invocation(p):
    read_call=p.get('retained_read_call',dict(function='context',args=[p['retained_context']['plan']],kwargs=dict(initial=False)))
    require(set(read_call)=={'function','args','kwargs'}
            and read_call['function']in {'context','cold_context','tail_context','load'}
            and read_call['args']==[p['retained_context']['plan']]
            and isinstance(read_call['kwargs'],dict), 'closed retained context invocation required')
    return read_call


def retain_lease_dependencies(profile):
    """Retain declared old dependencies even when the context imported no T."""
    import scripts.batch as package
    for name in ('harbor_tracker_lifecycle','harbor_lease_takeover'):
        canonical='scripts.batch.'+name
        rows=[row for row in profile['dependencies'] if row['module_name']==canonical]
        require(len(rows)==1,'exact historical lease dependency required')
        original=rows[0]['original']
        if canonical not in sys.modules:
            selected=module(canonical,original)
            setattr(package,name,selected)
        selected=sys.modules[canonical]
        require(binding(selected.__file__)==original,'historical lease dependency changed')
        sys.modules['_semantic_retained_'+name]=selected


def retained_takeover_origin(selection,result,takeover):
    """Follow authenticated expired custody replacements to the original guard."""
    current=selection;seen=set()
    while current['checkpoint']!=result:
        prior=current.get('prior_completion')
        require(prior is not None and current['checkpoint']==prior,
                'takeover checkpoint is not the declared prior completion')
        require(prior['path']not in seen and len(seen)<32,'takeover ancestry cycle or excessive depth')
        seen.add(prior['path'])
        takeover.validate_completion(prior)
        previous=read(read(prior)['stopped_owner_proof'])['selection']
        require(all(previous[k]==selection[k]for k in ('campaign_root','workbook','worker_kind','guard','config'))
                and previous['tracker']['path']==selection['tracker']['path'],
                'takeover ancestry detached from original stopped worker')
        current=previous
    return current


def retained_lease(p):
    """Validate completed custody, never reapply expired-owner eligibility."""
    from types import SimpleNamespace
    selected=p['completed_takeover']
    require(set(selected)=={'runtime','plan','completion','dependency_runtime','dependency_profile'},
            'closed completed takeover context required')
    tp=read(selected['plan'])
    require(tp['schema_version']=='harbor-allowance-takeover-direct/v1'
            and tp['selection']['workbook']==p['workbook']
            and tp['selection']['guard']==read(p['outcome'])['resource'],
            'takeover detached from actual completed first review')
    import scripts.batch as package
    dependencies=read(selected['dependency_profile'])
    retain_lease_dependencies(dependencies)
    A=module('scripts.batch.harbor_continuation_allowance',tp['shared']['allowance'])
    package.harbor_continuation_allowance=A
    runtime=module('semantic_dependency_runtime',selected['dependency_runtime'])
    entrypoint,read_call=lease_context(p)
    require(dependencies['runtime']==selected['dependency_runtime']
            and dependencies['entrypoint']==entrypoint
            and dependencies['read_call']==read_call
            and not dependencies['patches'] and not dependencies['guard_modules']
            and not dependencies['child_plans'] and not dependencies['journal_roots'],
            'only declared historical dependency selection is permitted')
    manager=runtime.installed(selected['dependency_profile']);manager.__enter__()
    try:
        T=sys.modules['scripts.batch.harbor_lease_takeover']
        require(binding(T.__file__)==tp['shared']['takeover']
                and binding(T.life.__file__)==tp['shared']['lifecycle'],
                'selected custody dependencies differ')
        loader=module('semantic_takeover_layout',selected['runtime'])
        if 'layout' in tp:loader.select_layout(T,tp['layout'])
        before,after,lease=T.validate_completion(selected['completion'])
        retained_takeover_origin(tp['selection'],p['result'],T)
        complete=read(selected['completion']);stop=read(complete['stopped_owner_proof'])
        require(p['tracker']==dict(path=stop['selection']['tracker']['path'],sha256=complete['after']['sha256']),
                'semantic tracker detached from exact completed takeover')
        require(stop['selection']==T._selection_value(T._selection(tp['selection'])) and stop['before']['sha256']==tp['selection']['tracker']['sha256']
                and lease.owner==native_takeover_owner(p,tp) and after['workbook_id']==p['workbook']
                and after['bindings']['original_raw_source']==p['source'],
                'completed takeover source, owner or prior state differs')
        return lease,SimpleNamespace(manager=manager,takeover=selected['completion'])
    except BaseException:
        manager.__exit__(*sys.exc_info());raise


def native_takeover_owner(p,takeover):
    if 'native_owner'not in p:return p['owner']
    owner=p['native_owner']
    require(completed_disclosure_context(p) and 'completed_takeover'in p
            and isinstance(owner,str) and owner.strip() and owner==takeover['selection']['owner'],
            'explicit native custody must match the completed takeover')
    return owner


def completed_disclosure_context(p):
    return p.get('retained_context',{}).get('kind')in {'completed_disclosure','completed_pre_dispatch'}


def retained_manager(p,D,L):
    if 'completed_takeover'in p:return L.manager
    require(completed_disclosure_context(p),'retained context manager required')
    return D._retained_disclosure_manager


def context(row, *, child=False, continued=False):
    p=read(row)
    require(set(p)-{'normalization_context','physical_context','preparation_failure','retained_context','completed_takeover','native_owner','semantic_preparation'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__), 'closed semantic continuation selection required')
    require(('retained_context'not in p or 'completed_takeover'in p or completed_disclosure_context(p))
            and not ('completed_takeover'in p and ('normalization_context'in p or 'preparation_failure'in p)),
            'retained and fresh custody mechanisms cannot be mixed')
    require(p['authorization']['sha256']==AUTHORIZATION_SHA and binding(p['authorization']['path'])==p['authorization'], 'explicit user authorization required')
    require('native_owner'not in p or completed_disclosure_context(p) and 'completed_takeover'in p,
            'separate native identity requires completed retained custody')
    fresh,job,D,env=validated_authoring(p)
    C,I,P,W,renewal,config,root,controls=env
    if not continued:operations_for(D,config,p['output'])
    value, selection, lease=completed_failure(p,I,controls)
    worker, observed, recipe=I.selected(p['descriptor'])
    require(observed==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction', 'original worker differs')
    require(selection.content.workbook==fresh['workbook'] and selection.content.bindings['original_raw_source']==p['source'], 'original source identity differs')
    tracker=worker.context.tracker_root/(fresh['workbook']+'.json')
    require(binding(p['source']['path'])==p['source'] and p['tracker']['path']==str(tracker), 'source/tracker path changed')
    if not continued:require(p['tracker']==binding(tracker),'source/tracker bytes changed')
    else:require('completed_takeover'in p or completed_disclosure_context(p),'only a declared completed-custody child may load completed history')
    if 'preparation_failure' in p:stopped_renewal_preparation(p)
    require(type(p['fence']) is int and p['fence']>0 and isinstance(p['owner'],str) and p['owner'], 'explicit fleet custody required')
    output=Path(p['output']); require(output.is_absolute() and not output.is_relative_to(root), 'isolated execution directory required')
    if continued:require(output.is_dir() and (output/'permit/result.json').is_file(),'actual semantic permit required for a retained child')
    else:require((output.is_dir() and not (output/'permit').exists()) if child else not output.exists(), 'continuation already attempted; reconcile')
    from scripts.batch import harbor_dialogue_semantic_retry as S
    if 'completed_takeover'in p:
        require(p['renewal_module']is None,'completed takeover cannot trigger another renewal')
        lease,L=retained_lease(p)
    else:
        L=module('semantic_selected_renewal',p['renewal_module'])
        require(L.DURATION_NS==7200*10**9 and L.SCHEMA=='fleet-validated-continuation-lease/v1', 'bounded original same-owner renewal required')
    return p,fresh,job,D,env,worker,selection,lease,recipe,S,L


def stopped_renewal_preparation(p):
    """A sealed failed pre-CAS renewal can select one new execution namespace."""
    proof=read(p['preparation_failure'])
    require(set(proof)=={'schema_version','plan','files','tracker','process'}
        and proof['schema_version']=='fleet-semantic-renewal-preparation-failure/v1',
        'closed stopped preparation proof required')
    old=read(proof['plan']);home=Path(old['output'])
    changed={'helper','renewal_module','output','owner','fence','preparation_failure'}
    require({k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
        and p['fence']==old['fence']+1 and p['output']!=old['output']
        and proof['tracker']==p['tracker']==binding(p['tracker']['path']),
        'preparation continuation changed prior task authority')
    expected={'intent.json','permit.log','permit-resource.json','renewal/intent.json'}
    require({str(x.relative_to(home))for x in home.rglob('*')if x.is_file()}==expected
        and set(proof['files'])==expected,'failed preparation has additional owning evidence')
    for name,r in proof['files'].items():require(r==binding(home/name),'failed preparation changed')
    guard=read(proof['files']['permit-resource.json']);intent=read(proof['files']['intent.json'])
    require(intent==dict(plan=proof['plan'],helper=old['helper'],automatic_retry=False)
        and guard['intent']==proof['files']['intent.json'] and guard['log']==proof['files']['permit.log']
        and guard['returncode']==1 and guard['failure']is None and guard['models_dispatched']==0
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592
        and guard['automatic_retry']is False
        and guard['command']==[sys.executable,'-I','-B',old['helper']['path'],'permit-child','--plan',proof['plan']['path'],'--sha256',proof['plan']['sha256']],
        'uncertain or foreign preparation guard')
    renewal=read(proof['files']['renewal/intent.json'])
    require(renewal['schema_version']=='fleet-validated-continuation-lease/v1'
        and renewal['authority']==proof['plan'] and renewal['tracker']==p['tracker']
        and renewal['duration_ns']==7200*10**9,'failed renewal intent differs')
    process=read(proof['process']);pid=process['pid']
    require(type(pid)is int and pid>1 and not Path('/proc',str(pid)).exists(),
        'prior preparation worker must be stopped')


def custody(p,root):
    from scripts.fleet.coordinator import Client
    capacity=module('semantic_capacity_policy',p['capacity_policy'])
    require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'] and os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'], 'owning fleet environment required')
    status=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('status')
    capacity.validate_occupancy(status); now=time.time(); require(abs(now-status['at'])<=60, 'fresh coordinator observation required')
    wb=p['workbook']; rows=[w for w in status['workbooks'] if w['workbook']==wb]
    require(len(rows)==1 and rows[0]['state']=='active' and rows[0]['owner']==p['owner'] and rows[0]['fence']==p['fence'] and rows[0]['version']==root.name and rows[0]['source_sha256']==p['source']['sha256'] and rows[0]['expires']>now, 'exclusive live workbook claim required')
    if hasattr(capacity,'model_admission_barriers'):
        require(not capacity.model_admission_barriers(status,wb,p['owner']),'unreconciled model effect or quarantined custody')
        require(not any(o['workbook']==wb and o['state']in('active','hold')for o in status['operations']),
                'target operation requires reconciliation')
    else:
        require(not any(o['state']=='hold' or (o['workbook']==wb and o['state']=='active') for o in status['operations']), 'active or uncertain task operation requires reconciliation')


def permit_child(row):
    p,fresh,job,D,env,w,selection,lease,recipe,S,L=context(row,child=True)
    try:
        C,I,P,W,_,_,root,controls=env; output=Path(p['output']); custody(p,root)
        if 'completed_takeover'in p:
            custody(p,root)
            if 'semantic_preparation'in p:
                q=p['semantic_preparation'];require(set(q)=={'helper','profile'},'closed intent recovery reader required')
                Q=module('selected_semantic_intent_recovery',q['helper'])
                w,selection,lease,completion=Q.authorize(q['profile'],S,w,selection,lease,blocked=p['blocked'],takeover=L.takeover)
            else:w,selection,lease,completion=S.authorize(w,selection,lease,blocked=p['blocked'],takeover=L.takeover)
            renewed=L.takeover
        else:
            lease,renewed=renew_semantic(row,p,root,output,w,selection,lease,S,L,I)
            custody(p,root)
            w,selection,lease,completion=S.authorize(w,selection,lease,blocked=p['blocked'])
        return semantic_handoff(row,p,I,C,w,selection,lease,renewed,completion,output)
    finally:
        if 'completed_takeover'in p or completed_disclosure_context(p):retained_manager(p,D,L).__exit__(*sys.exc_info())

def renew_semantic(row,p,root,output,w,selection,lease,S,L,I):
    def validate(before,old):
        proof=S._failure(w,selection,old,p['blocked'])
        require(proof[0]==before, 'semantic failure validation tracker differs')
        return proof[3]
    # Both operations use the same original worker lock. Release between them:
    # S.authorize acquires that lock itself and validates the full renewal chain.
    with (w.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        lease,renewed=L.renew(life=S.life,read=I.read,record=binding,keep=keep,
            path=p['tracker']['path'],lease=lease,authority_record=row,validation=validate,
            custody=lambda:custody(p,root),directory=output/'renewal',retain_sufficient=True)
    return lease,renewed


def semantic_handoff(row,p,I,C,w,selection,lease,renewed,completion,output):
    directory=output/'permit'; directory.mkdir()
    old=read(p['descriptor'])
    descriptor=keep(directory/'descriptor.json',{**old,'worker':I.worker_encode(w),
        'initial':dict(selection=I.codec.encode(selection),lease=C.encode(lease)),
        'origin':[*old['origin'],p['descriptor'],p['result'],row,renewed,completion]})
    result=keep(directory/'result.json',I._result('RETRY_READY',descriptor,selection,lease,
        checkpoint=completion,next_step='dialogue-writer',evidence=[p['result'],row,renewed,completion]))
    I.selected(descriptor); I._prior(result,descriptor)
    return result


def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1', 'read-only cannot execute')
    p,fresh,job,D,env,w,selection,lease,recipe,S,L=context(row)
    try:
        C,I,P,W,_,config,root,controls=env; custody(p,root); output=Path(p['output']); output.mkdir(parents=True)
        if 'completed_takeover'in p or completed_disclosure_context(p):install_retained_commands(row,p,D,env)
        intent=keep(output/'intent.json',dict(plan=row,helper=binding(__file__),automatic_retry=False))
        command=[sys.executable,'-I','-B',__file__,'permit-child','--plan',row['path'],'--sha256',row['sha256']]
        from scripts.batch.hardened_runner import _run_in_process_group
        metrics={};rc=None;failure=None;start=time.monotonic()
        try:
            with (output/'permit.log').open('xb') as log:
                rc=_run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=root,env=os.environ.copy())
        except BaseException as exc: failure=type(exc).__name__
        guard=keep(output/'permit-resource.json',dict(intent=intent,command=command,returncode=rc,failure=failure,elapsed_seconds=time.monotonic()-start,metrics=metrics,seconds=3300,memory_bytes=8589934592,log=binding(output/'permit.log'),models_dispatched=0,automatic_retry=False))
        require(rc==0 and failure is None, 'semantic permit requires reconciliation; never replay')
        checkpoint=binding(output/'permit/result.json');value=read(checkpoint);I._prior(checkpoint,value['descriptor']);custody(p,root)
        outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,output/'first-step',models=True,commands=True,containers=True)
        D.successful(outcome,env)
        current={**job,'outcome':outcome,'operations':str(operations_for(D,config,output))};bound=keep(output/'authoring-job.json',current)
        D.run(bound);held=read(binding(Path(current['operations'])/'hold.json'));last,result=D.successful(held['outcome'],env)
        require(result['status']=='PUBLICATION_READY', 'remaining authoring reached a diagnosed hold')
        return keep(output/'result.json',dict(status='PUBLICATION_READY',workbook=fresh['workbook'],plan=p['fresh_plan'],job=bound,config=job['config'],outcome=held['outcome'],checkpoint=last,continuation=row,permit_guard=guard,publication_performed=False,automatic_retry=False))
    finally:
        if 'completed_takeover'in p or completed_disclosure_context(p):retained_manager(p,D,L).__exit__(*sys.exc_info())

def retained_command(command,p,D,env):
    """Recognize the original guarded instruction or renewal child exactly."""
    C,I=env[:2]
    if len(command)==6 and command[1:4]==['-B',str(Path(I.__file__).resolve()),'_child']:
        kind='instruction';intent=dict(path=command[4],sha256=command[5]);value=read(intent)
        require(value['schema_version']==I.SCHEMA and value['action']=='step'
                and type(value['models'])is bool and value['code']==binding(I.__file__)
                and value['base_code']==binding(C.__file__) and value['controls']==C.encode(env[7]),
                'original instruction child identity changed')
    elif ((len(command)==6 and command[1:4]==['-B',str(Path(D.__file__).resolve()),'renew-child'])
          or(len(command)==7 and command[1:5]==['-I','-B',str(Path(D.__file__).resolve()),'renew-child'])):
        kind='renewal';intent=dict(path=command[-2],sha256=command[-1]);value=read(intent)
        require(set(value)=={'job','checkpoint','seconds','driver'} and value['driver']==binding(D.__file__)
                and type(value['seconds'])is int and 60<=value['seconds']<=900,
                'original renewal intent changed')
    else:raise ValueError('unselected retained child command')
    path=Path(intent['path']);operations=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
    require(path.parent==Path(p['output'])/'first-step' or path.is_relative_to(operations),
            'child intent outside the declared semantic continuation')
    return kind,intent


def install_retained_commands(row,p,D,env):
    C,I=env[:2];original=C._run_in_process_group;job=read(p['original_job'])
    if 'interrupted_commit'in p:
        original_run=I.run
        def no_model_run(action,arguments,*args,**options):
            prior=read(arguments['checkpoint'])
            require(action=='step'and prior['next_step']in {'dialogue-commit','enqueue'},
                    'stopped commit continuation cannot author or review')
            return original_run(action,arguments,*args,**dict(options,models=False))
        I.run=no_model_run
        D._semantic_tail_manager.callback(setattr,I,'run',original_run)
    if 'initial_renewal'in p:job=dict(job,maximum_steps=2)
    operations=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
    def setup(current):
        require(current==dict(job,outcome=current['outcome'],operations=str(operations)),
                'retained dispatcher job changed beyond the exact completed handoff')
        return env
    D.setup=setup
    def guarded(command,*args,**options):
        kind,intent=retained_command(command,p,D,env)
        seconds=retained_seconds(kind,intent,env)
        require(options['timeout_seconds']==seconds and options['memory_limit_bytes']==8589934592,
                'original retained child resource contract changed')
        side=keep(Path(intent['path']).parent/'semantic-bootstrap.json',dict(
            schema_version='fleet-retained-semantic-child/v1',plan=row,helper=p['helper'],kind=kind,
            intent=intent,original_command=command,seconds=seconds,memory_bytes=8589934592,automatic_retry=False))
        replacement=[command[0],'-I','-B',p['helper']['path'],'retained-child','--plan',row['path'],
            '--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],
            '--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']]
        return original(replacement,*args,**options)
    C._run_in_process_group=guarded


def retained_seconds(kind,intent,env):
    value=read(intent)
    if kind=='renewal':return value['seconds']
    C,I=env[:2]
    seconds=I.guard_seconds(value['action'],value['arguments']) if hasattr(I,'guard_seconds')else C.SECONDS
    require(type(seconds)is int and 60<=seconds<=3300 and value['timeout_seconds']==seconds,
            'original instruction duration differs')
    return seconds


def retained_child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated retained semantic child required')
    if read(row)['schema_version']==TAIL_SCHEMA:
        p,fresh,job,D,env,_,_=tail_context(row,continued=True)
        manager=D._initial_tail_manager if 'initial_renewal'in p else D._semantic_tail_manager
    else:
        p,fresh,job,D,env,w,selection,lease,recipe,S,L=context(row,continued=True)
        manager=retained_manager(p,D,L)
    try:
        custody(p,env[6]);side=read(sidecar)
        kind,actual=retained_command(side['original_command'],p,D,env)
        require(actual==intent and side==dict(schema_version='fleet-retained-semantic-child/v1',
            plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],
            seconds=retained_seconds(kind,intent,env),memory_bytes=8589934592,automatic_retry=False)
            and Path(sidecar['path'])==Path(intent['path']).parent/'semantic-bootstrap.json',
            'retained semantic child detached from original guarded intent')
        install_retained_commands(row,p,D,env)
        return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)
    finally:manager.__exit__(*sys.exc_info())


def probe(row):
    p,fresh,job,D,env,w,selection,lease,recipe,S,L=context(row)
    try:
        proof=S._failure(w,selection,lease,p['blocked'],**(dict(takeover=L.takeover)if 'completed_takeover'in p else {}))
        if 'semantic_preparation'in p:
            q=p['semantic_preparation'];require(set(q)=={'helper','profile'}and 'completed_takeover'in p,'closed completed custody recovery required')
            Q=module('probe_semantic_intent_recovery',q['helper']);Q.validate(q['profile'],S,w,blocked=p['blocked'],takeover=L.takeover)
        return dict(status='PASS',result=dict(plan=row,workbook=p['workbook'],findings=proof[2],history_bindings=len(proof[3]),next_round=2,consumed_rounds=1),models_dispatched=0,task_mutations=0)
    finally:
        if 'completed_takeover'in p or completed_disclosure_context(p):retained_manager(p,D,L).__exit__(*sys.exc_info())

TAIL_SCHEMA='fleet-completed-instruction-continuation/v1'
TAIL_FIELDS={'schema_version','workbook','helper','fresh_plan','original_job','outcome',
             'tracker','source','owner','fence','output','operations','next_step',
             'authorization','capacity_policy'}


def semantic_tail_context(row, *, continued=False):
    """Resume a completed review after an adapter refused before child spawn."""
    p=read(row)
    require(set(p)==TAIL_FIELDS|{'semantic_context','failed_guard'} and p['schema_version']==TAIL_SCHEMA
            and p['helper']==binding(__file__), 'closed completed semantic tail required')
    selected=p['semantic_context']
    require(set(selected)=={'helper','plan'},'closed prior semantic reader required')
    old=read(selected['plan'])
    require(old['schema_version']==SCHEMA and old['helper']==selected['helper']
            and 'completed_takeover'in old and p['fresh_plan']==old['fresh_plan']
            and p['original_job']==old['original_job'] and p['source']==old['source']
            and p['workbook']==old['workbook'] and p['authorization']==old['authorization']
            and p['authorization']['sha256']==AUTHORIZATION_SHA
            and p['output']!=old['output'] and p['fence']==old['fence']+1,
            'semantic tail changed its completed source authority')
    M=module('completed_semantic_tail_parent',selected['helper'])
    _,fresh,job,D,env,_,_,_,_,_,L=M.context(selected['plan'],continued=True)
    try:
        C,I,_,W,_,config,root,controls=env
        checkpoint,value=D.successful(p['outcome'])
        require(value['status']=='STEP_COMPLETE' and value['next_step']==p['next_step']=='final-runtime'
                and value['publication_performed']is False,'completed original second review required')
        worker,observed,recipe=I.selected(value['descriptor'])
        if not continued:I._prior(checkpoint,value['descriptor'])
        selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
        require(observed==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction'
                and selection.content.workbook==p['workbook']
                and selection.content.bindings['original_raw_source']==p['source'],
                'completed review worker differs')
        failed=read(p['failed_guard']);directory=Path(p['failed_guard']['path']).parent
        require(directory.parent==Path(config['run_root'])/'dispatcher-v2'/Path(old['output']).name
                and set(x.name for x in directory.iterdir())=={'intent.json','resource.json','outcome.json','step.log'}
                and failed['status']=='RECONCILIATION_REQUIRED' and failed.get('result')is None
                and failed['automatic_retry']is False and failed['resource']==binding(directory/'resource.json'),
                'failed adapter namespace contains owning evidence')
        resource=read(failed['resource']);intent=read(resource['intent'])
        require(resource['returncode']is None and resource['metrics']=={}
                and resource['failure']==dict(type='ValueError',message_sha256=hashlib.sha256(b'original retained child resource contract changed').hexdigest())
                and resource['intent']==binding(directory/'intent.json') and resource['log']==binding(directory/'step.log')
                and Path(resource['log']['path']).read_bytes()==b''
                and intent['action']=='step' and intent['schema_version']==I.SCHEMA
                and intent['code']==binding(I.__file__) and intent['base_code']==binding(C.__file__)
                and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
                and intent['controls']==C.encode(controls) and intent['memory_limit_bytes']==C.MEMORY,
                'failure was not the exact pre-child resource adapter refusal')
        seconds=retained_seconds('instruction',resource['intent'],env)
        require(seconds!=3300 and p['tracker']['path']==str(worker.context.tracker_root/(p['workbook']+'.json')),
                'original step-specific guard or tracker differs')
        output=Path(p['output'])
        require(output.is_absolute() and not output.is_relative_to(root),'isolated semantic tail required')
        operations=Path(config['run_root'])/'dispatcher-v2'/output.name
        require(p['operations']==str(operations),'semantic tail dispatcher namespace changed')
        if continued:
            require(read(binding(output/'intent.json'))==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),
                    'actual semantic tail owning intent required')
        else:
            require(not output.exists() and binding(p['tracker']['path'])==p['tracker']
                    and not W.step_directory(worker,'final-runtime').exists(),
                    'runtime already started or tail tracker changed')
            W.active(worker,selection,lease);D.operation_namespace(config,p['operations'])
        D._semantic_tail_manager=L.manager
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:
        L.manager.__exit__(*sys.exc_info());raise


def sealed_semantic_child(p, old, env, checkpoint, value):
    """Reconcile one failed pre-stage historical validator, never a model call."""
    C,I,_,W,_,config,root,controls=env
    home=Path(old['output']);directory=home/'first-step'
    failed=read(p['failed_guard']);resource=read(failed['resource'])
    require(p['failed_guard']==binding(directory/'outcome.json')
        and failed['status']=='RECONCILIATION_REQUIRED' and failed.get('result')is None
        and failed['automatic_retry']is False and failed['resource']==binding(directory/'resource.json')
        and set(x.name for x in directory.iterdir())=={'intent.json','resource.json','outcome.json','step.log','semantic-bootstrap.json','dependency.json'},
        'failed semantic child contains unknown owning evidence')
    dependency=read(binding(directory/'dependency.json'))
    require(dependency.get('schema_version')=='harbor-dependency-observation/v2',
        'failed child dependency observation differs')
    require(resource['returncode']==1 and resource['failure']is None
        and resource['intent']==binding(directory/'intent.json')
        and resource['log']==binding(directory/'step.log'), 'uncertain semantic child cannot resume')
    intent=read(resource['intent'])
    require(intent['action']=='step' and intent['schema_version']==I.SCHEMA
        and intent['code']==binding(I.__file__) and intent['base_code']==binding(C.__file__)
        and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
        and intent['controls']==C.encode(controls) and intent['memory_limit_bytes']==C.MEMORY
        and all(intent[k]is True for k in ('models','commands','containers')),
        'failed semantic child lost original intent')
    retained_seconds('instruction',resource['intent'],env)
    side=read(binding(directory/'semantic-bootstrap.json'))
    command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',
        resource['intent']['path'],resource['intent']['sha256']]
    require(side==dict(schema_version='fleet-retained-semantic-child/v1',
        plan=p['semantic_context']['plan'],helper=old['helper'],kind='instruction',
        intent=resource['intent'],original_command=command,
        seconds=retained_seconds('instruction',resource['intent'],env),
        memory_bytes=8589934592,automatic_retry=False), 'original failed bootstrap differs')
    trace=Path(resource['log']['path']).read_text()
    if p['unstarted_semantic'].get('kind')=='aggregate-history':
        require(trace.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: prior owning continuations differ')
            and 'validated=A.validate(selected,before)'in trace and 'H.adoption_history(A,R,w,before,'in trace,
            'unrecognized aggregate semantic predecessor failure')
    else:
        require(trace.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: foreign historical instruction repair')
            and 'repair.validate_historical_integrity(worker, before)'in trace,
            'unrecognized semantic child failure')
    process=read(p['unstarted_semantic']['process'])
    require(type(process['pid'])is int and process['pid']>1
        and not Path('/proc',str(process['pid'])).exists(),'prior semantic worker still active')
    # The original phase creates this directory before any producer operation.
    # Its absence plus the unchanged CAS tracker rules out a consumed writer.
    worker,_,_=I.selected(value['descriptor'])
    require(not W.step_directory(worker,'dialogue-writer').exists(),
        'owning semantic writer already started; model reconciliation required')


def unstarted_history_runtime(p,old,env,RT,profile):
    """Select one exact historical function; owning S/I/W stay original."""
    proof=p['unstarted_semantic']
    require(profile['schema_version']==RT.SCHEMA and profile['runtime']==proof['runtime'] and not profile['patches']
        and not profile['dependencies'] and len(profile['symbols'])==1,'only one historical symbol may change')
    symbol=profile['symbols'][0]
    if proof.get('kind')=='aggregate-history':
        import ast
        q=read(old['retained_context']['plan'])
        require(q['schema_version']=='fleet-retained-completed-aggregate/v1'
            and symbol['module_name']=='completed_aggregate_history' and symbol['names']==['adoption_history']
            and symbol['original']==q['aggregate_history']['helper']
            and all(profile[k]==[]for k in ('guard_modules','child_plans','journal_roots')),
            'only exact completed aggregate history may change')
        def other(row):
            require(binding(row['path'])==row,'aggregate history code changed')
            tree=ast.parse(Path(row['path']).read_text())
            require(sum(isinstance(n,ast.FunctionDef)and n.name=='adoption_history'for n in tree.body)==1,'unique adoption history function required')
            return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef)or n.name!='adoption_history'],type_ignores=[]),include_attributes=False)
        require(other(symbol['original'])==other(symbol['selected']),'unrelated aggregate history code changed')
    else:
        require(symbol['module_name']=='scripts.batch.harbor_instruction_repair'
            and symbol['names']==['validate_historical_integrity']
            and symbol['original']==binding(env[6]/'scripts/batch/harbor_instruction_repair.py'),
            'only the declared historical repair validator may change')


def unstarted_aggregate_consumer(old,worker,profile):
    """Carry the same proven aggregate history into original W's resume reader."""
    from contextlib import contextmanager
    from scripts.batch import harbor_disclosure_round_adoption as A,harbor_disclosure_review_resume as D
    from scripts.batch import harbor_instruction_lease_renewal as R
    q=read(old['retained_context']['plan']);adoption=read(q['context']['plan'])
    H=sys.modules['completed_aggregate_history'];original=D.validate
    require(binding(A.__file__)==adoption['adoption'] and binding(H.__file__)==q['aggregate_history']['helper']
        and original.__name__=='validate_selected' and original.__globals__ is vars(A)
        and original.__code__.co_filename==A.__file__
        and binding(inspect.getsourcefile(H.adoption_history))==profile['symbols'][0]['selected'],
        'original installed aggregate consumer differs')
    G=module('unstarted_aggregate_archive',q['aggregate_history']['archive'])
    @contextmanager
    def installed():
        def checked(observed,before):
            if not A.selected(observed):return original(observed,before)
            require(observed.root==worker.root and observed.run==worker.run and observed.context==worker.context
                and observed.disclosure_repair==worker.disclosure_repair,'foreign aggregate instruction consumer')
            return H.adoption_history(A,R,observed,before,historical_evidence=G.historical_evidence)[0]
        D.validate=checked
        try:yield
        finally:D.validate=original
    return installed()


def unstarted_semantic_context(row, *, continued=False):
    """Continue an already consumed semantic permit after a sealed pre-stage failure."""
    from contextlib import ExitStack
    p=read(row);extra={'semantic_context','failed_guard','unstarted_semantic'}
    require(set(p)-{'checkpoint_lease'}==TAIL_FIELDS|extra and p['schema_version']==TAIL_SCHEMA
        and p['helper']==binding(__file__), 'closed unstarted semantic tail required')
    selected=p['semantic_context'];proof=p['unstarted_semantic']
    require(set(selected)=={'helper','plan'} and set(proof)-{'kind'}=={'permit','process','runtime','profile'} and proof.get('kind')in(None,'aggregate-history'),
        'closed consumed semantic permission evidence required')
    old=read(selected['plan'])
    require(old['schema_version']==SCHEMA and old['helper']==selected['helper']
        and old.get('retained_context',{}).get('kind')=='completed_disclosure'
        and ('completed_takeover'in old or proof.get('kind')=='aggregate-history') and p['fence']==old['fence']+1
        and p['output']!=old['output'] and p['outcome']==p['failed_guard']
        and all(p[k]==old[k]for k in ('fresh_plan','original_job','source','workbook','authorization'))
        and p['authorization']['sha256']==AUTHORIZATION_SHA, 'consumed semantic source authority differs')
    M=module('unstarted_semantic_parent',selected['helper'])
    _,fresh,job,D,env,_,_,_,_,S,L=M.context(selected['plan'],continued=True)
    stack=ExitStack();stack.callback(M.retained_manager(old,D,L).__exit__,None,None,None)
    try:
        RT=module('unstarted_semantic_runtime',proof['runtime'])
        profile=RT.profile(proof['profile'])
        unstarted_history_runtime(p,old,env,RT,profile)

        stack.enter_context(RT.installed(proof['profile']))
        C,I,_,W,_,config,root,controls=env
        checkpoint=proof['permit'];value=read(checkpoint);home=Path(old['output'])
        guard=read(binding(home/'permit-resource.json'))
        require(checkpoint==binding(home/'permit/result.json') and guard['returncode']==0
            and guard['failure']is None and guard['models_dispatched']==0
            and guard['intent']==binding(home/'intent.json') and guard['log']==binding(home/'permit.log')
            and read(guard['intent'])==dict(plan=selected['plan'],helper=selected['helper'],automatic_retry=False)
            and value['status']=='RETRY_READY' and value['next_step']==p['next_step']=='dialogue-writer'
            and value['publication_performed']is False, 'completed original semantic permission required')
        worker,observed,_=I.selected(value['descriptor'])
        require(observed==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction'
            and value['checkpoint']==worker.dialogue_semantic_retry
            and p['tracker']['path']==str(worker.context.tracker_root/(p['workbook']+'.json')),
            'semantic worker or tracker differs')
        if proof.get('kind')=='aggregate-history':stack.enter_context(unstarted_aggregate_consumer(old,worker,profile))
        output=Path(p['output']);operations=Path(config['run_root'])/'dispatcher-v2'/output.name
        require(output.is_absolute() and not output.is_relative_to(root) and p['operations']==str(operations),
            'isolated continuation namespace required')
        if continued:
            require(read(binding(output/'intent.json'))==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),
                'actual unstarted tail intent required')
        else:
            I._prior(checkpoint,value['descriptor'])
            require(binding(p['tracker']['path'])==p['tracker'] and not output.exists(),
                'semantic tracker changed or continuation already attempted')
            sealed_semantic_child(p,old,env,checkpoint,value)
            selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
            _,before,_=W.active(worker,selection,lease)
            from scripts.batch.harbor_instruction_repair import validate_historical_integrity
            validate_historical_integrity(worker,before);S.validate(worker,before)
            D.operation_namespace(config,p['operations'])
        if 'checkpoint_lease'in p:
            checkpoint,value=checkpoint_lease_context(row,p,env,stack,checkpoint,value,continued=continued)
        D._semantic_tail_manager=stack
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:stack.__exit__(*sys.exc_info());raise


def checkpoint_lease_modules(p):
    q=p['checkpoint_lease']
    require(set(q)=={'helper','renewal_module','bridge_module'},'closed completed checkpoint lease runtime required')
    K=module('completed_checkpoint_lease',q['helper'])
    L=module('completed_checkpoint_lease_policy',q['renewal_module'])
    B=module('completed_checkpoint_lease_bridge',q['bridge_module'])
    require(K.SCHEMA=='fleet-completed-checkpoint-lease/v1' and L.SCHEMA=='fleet-validated-continuation-lease/v1'
        and L.DURATION_NS==7200*10**9 and B.SCHEMA=='fleet-completed-checkpoint-takeover/v1',
        'completed checkpoint lease policy differs')
    return K,L,B


def checkpoint_lease_context(row,p,env,stack,checkpoint,value,*,continued):
    K,L,B=checkpoint_lease_modules(p);I=env[1];W=env[3]
    from scripts.batch import harbor_dialogue_semantic_retry as S
    path=Path(p['output'])/'checkpoint-lease/handoff.json'
    if not continued:
        require(not path.parent.exists(),'checkpoint renewal namespace already used')
        return checkpoint,value
    handoff=binding(path);raw=read(handoff)
    require(raw['authority']==row and raw['original_result']==checkpoint
        and raw['original_descriptor']==value['descriptor'],'checkpoint renewal detached from selected tail')
    stack.enter_context(K.installed(handoff,I=I,W=W,L=L,B=B,life=S.life))
    selected=read(raw['result']);I._prior(raw['result'],selected['descriptor'])
    return raw['result'],selected


def renew_tail_checkpoint(row,p,env,checkpoint,value,stack):
    K,L,B=checkpoint_lease_modules(p);I=env[1];W=env[3]
    from scripts.batch import harbor_dialogue_semantic_retry as S
    worker,_,_=I.selected(value['descriptor'])
    def validation(w,selection,lease,before):
        require(w.dialogue_semantic_retry is not None,'completed semantic authority required')
        S.validate(w,before)
        return [checkpoint,value['descriptor'],w.dialogue_semantic_retry]
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        handoff=K.renew(checkpoint,value['descriptor'],I=I,W=W,L=L,B=B,life=S.life,authority=row,
            directory=Path(p['output'])/'checkpoint-lease',keep=keep,validation=validation,custody=lambda:custody(p,env[6]))
    stack.enter_context(K.installed(handoff,I=I,W=W,L=L,B=B,life=S.life))
    raw=read(handoff);return raw['result'],read(raw['result'])


def initial_tail_context(row, *, continued=False):
    """Retain a guarded initial renewal hold and stop before its first review."""
    import ast,contextlib
    p=read(row)
    require(set(p)==TAIL_FIELDS|{'initial_renewal'} and p['schema_version']==TAIL_SCHEMA
        and p['helper']==binding(__file__) and p['next_step']=='disclosure-build',
        'closed unstarted instruction continuation required')
    require(p['authorization']['sha256']==AUTHORIZATION_SHA and binding(p['authorization']['path'])==p['authorization'],
        'explicit initial continuation authorization required')
    original_fresh=read(p['fresh_plan']);legacy=original_fresh.get('legacy_research_history')
    if legacy is not None:
        require(binding(legacy['path'])==legacy and original_fresh['workbook']==p['workbook']
            and not any(os.environ.get(k)for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH')),
            'initial legacy history differs from original fresh selection')
        for name,value in {'HARBOR_FLEET_LEGACY_RESEARCH_PATH':legacy['path'],
                           'HARBOR_FLEET_LEGACY_RESEARCH_SHA256':legacy['sha256']}.items():
            require(os.environ.get(name)in (None,value),'foreign legacy history is already selected')
            os.environ[name]=value
    fresh,job,D,env=validated_authoring(p);C,I,_,W,_,config,root,controls=env
    selection=p['initial_renewal']
    require(set(selection)=={'dispatcher','initial_reader','functions'}
        and selection['initial_reader']==binding(root/'scripts/fleet/initial_lease_renewal.py')
        and set(selection['functions'])=={'successful','renewal_context','renewal_child'},
        'only declared initial renewal functions may change')
    source=Path(selection['dispatcher']['path']).read_text()
    require(binding(selection['dispatcher']['path'])==selection['dispatcher'],'selected initial dispatcher changed')
    nodes={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    originals={name:getattr(D,name) for name in selection['functions']}
    def digest(function):
        import textwrap
        return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(function))).body[0],include_attributes=False).encode()).hexdigest()
    for name,expected in selection['functions'].items():
        require(set(expected)=={'original','selected'} and digest(originals[name])==expected['original']
            and hashlib.sha256(ast.dump(nodes[name],include_attributes=False).encode()).hexdigest()==expected['selected'],
            'initial dispatcher function witness changed')
    @contextlib.contextmanager
    def installed():
        try:
            for name in originals:
                exec(compile(ast.Module(body=[nodes[name]],type_ignores=[]),selection['dispatcher']['path'],'exec'),D.__dict__)
            yield
        finally:
            for name,function in originals.items():setattr(D,name,function)
    manager=installed();manager.__enter__()
    try:
        checkpoint,value=D.successful(p['outcome'],env)
        require(value['schema_version']==I.SCHEMA and value['status']=='LEASE_RENEWAL_REQUIRED'
            and value['checkpoint']is None and value['next_step']=='disclosure-build' and len(value['evidence'])==1,
            'exact guarded unstarted renewal hold required')
        first=value['evidence'][0];prior,initial=D.successful(binding(Path(first['path']).parent/'outcome.json'),env)
        require(prior==first and initial['status']=='INSTRUCTION_READY'
            and all(initial[k]==value[k] for k in ('descriptor','checkpoint','selection','lease','next_step')),
            'initial owning guard differs from retained hold')
        w,observed,recipe=I.selected(value['descriptor']);s=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
        require(observed==controls and w.root==root and w.run==Path(config['run_root'])/'instruction'
            and s.content.workbook==p['workbook'] and s.content.bindings['original_raw_source']==p['source']
            and binding(p['source']['path'])==p['source'],'initial source or controls differ')
        if not continued:
            require(not w.run.exists() and p['tracker']==binding(w.context.tracker_root/(p['workbook']+'.json')),
                'initial worker started or tracker changed')
            W.active(w,s,lease)
            require(not Path(p['output']).exists(),'initial continuation already started')
            D.operation_namespace(config,p['operations'])
        else:
            outer=read(binding(Path(p['output'])/'intent.json'))
            require(outer==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),
                'initial cold child detached from actual outer intent')
        require(Path(p['output']).is_absolute() and not Path(p['output']).is_relative_to(root)
            and Path(p['operations'])==Path(config['run_root'])/'dispatcher-v2'/Path(p['output']).name,
            'initial continuation namespace differs')
        D._initial_tail_manager=manager
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:
        manager.__exit__(*sys.exc_info());raise


def tail_context(row, *, continued=False):
    if 'interrupted_commit'in read(row):
        M=module('interrupted_commit',read(row)['interrupted_commit']['reader'])
        return M.context(sys.modules[__name__],row,continued=continued)
    if 'completed_enqueue'in read(row):
        M=module('completed_enqueue',read(row)['completed_enqueue']['reader'])
        return M.completed_context(sys.modules[__name__],row,continued=continued)
    if 'interrupted_enqueue'in read(row):
        M=module('interrupted_enqueue',read(row)['interrupted_enqueue']['reader'])
        return M.context(sys.modules[__name__],row,continued=continued)
    """Only an original completed owning guard can hand off to the next step."""
    p=read(row)
    if 'unprepared_review'in p:
        M=module('unprepared_dialogue_review',p['unprepared_review']['reader'])
        return M.context(sys.modules[__name__],row,continued=continued)
    if 'unstarted_semantic'in p:return unstarted_semantic_context(row,continued=continued)
    if 'initial_renewal'in p:return initial_tail_context(row,continued=continued)
    if 'semantic_context'in p:return semantic_tail_context(row,continued=continued)
    require(not continued,'ordinary tail has no retained child context')
    require(set(p)-{'normalization_context'}==TAIL_FIELDS and p['schema_version']==TAIL_SCHEMA
            and p['helper']==binding(__file__), 'closed completed instruction continuation required')
    require(p['authorization']['sha256']==AUTHORIZATION_SHA
            and binding(p['authorization']['path'])==p['authorization'], 'explicit continuation authorization required')
    fresh,job,D,env=validated_authoring(p)
    C,I,P,W,R,config,root,controls=env
    checkpoint,value=D.successful(p['outcome'],env)
    require(value['schema_version']==I.SCHEMA and value['status']=='STEP_COMPLETE'
            and value['next_step']==p['next_step'] and value['publication_performed'] is False,
            'actual completed instruction step required')
    w,observed,recipe=I.selected(value['descriptor']);I._prior(checkpoint,value['descriptor'])
    selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
    require(observed==controls and w.root==root and w.run==Path(config['run_root'])/'instruction'
            and selection.content.workbook==p['workbook'] and selection.content.bindings['original_raw_source']==p['source']
            and binding(p['source']['path'])==p['source'], 'completed instruction source or controls changed')
    require(p['tracker']==binding(w.context.tracker_root/(p['workbook']+'.json')), 'completed instruction tracker changed')
    W.active(w,selection,lease)
    output=Path(p['output']);require(output.is_absolute() and not output.exists() and not output.is_relative_to(root),
            'fresh continuation output required')
    D.operation_namespace(config,p['operations'])
    return p,fresh,job,D,env,checkpoint,value


def tail_probe(row):
    p,fresh,job,D,env,checkpoint,value=tail_context(row)
    try:
        return dict(status='PASS',result=dict(plan=row,workbook=p['workbook'],checkpoint=checkpoint,next_step=value['next_step']),
                    models_dispatched=0,task_mutations=0)
    finally:
        if 'semantic_context'in p:D._semantic_tail_manager.__exit__(*sys.exc_info())
        if 'initial_renewal'in p:D._initial_tail_manager.__exit__(*sys.exc_info())


def tail_run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1', 'read-only cannot execute')
    p,fresh,job,D,env,checkpoint,value=tail_context(row)
    try:
        custody(p,env[6]);output=Path(p['output']);output.mkdir(parents=True)
        intent=keep(output/'intent.json',dict(plan=row,prior_completed=p['outcome'],automatic_retry=False))
        if 'semantic_context'in p or 'initial_renewal'in p:install_retained_commands(row,p,D,env)
        current={**job,'outcome':p['outcome'],'operations':p['operations']}
        if 'interrupted_commit'in p and 'checkpoint_takeover'in p:
            reader=module('interrupted_commit_handoff',p['interrupted_commit']['reader'])
            checkpoint,value=reader.handoff(sys.modules[__name__],p,env,D)
        if 'checkpoint_lease'in p:
            checkpoint,value=renew_tail_checkpoint(row,p,env,checkpoint,value,D._semantic_tail_manager)
        if 'unstarted_semantic'in p or 'checkpoint_lease'in p or 'completed_enqueue'in p or ('interrupted_commit'in p and 'checkpoint_takeover'in p):
            outcome=env[1].run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),env[7],
                output/'first-step',models=True,commands=True,containers=True)
            D.successful(outcome,env);current['outcome']=outcome
        if 'initial_renewal'in p:current['maximum_steps']=2
        bound=keep(output/'authoring-job.json',current)
        D.run(bound)
        held=read(binding(Path(current['operations'])/'hold.json'));last,result=D.successful(held['outcome'],env)
        status='PUBLICATION_READY' if result['status']=='PUBLICATION_READY' else 'RECONCILIATION_REQUIRED'
        if 'initial_renewal'in p and result['status']=='STEP_COMPLETE' and result['next_step']=='disclosure-review':status='DISCLOSURE_BUILD_COMPLETE'
        return keep(output/'result.json',dict(status=status,
            workbook=p['workbook'],plan=p['fresh_plan'],job=bound,config=job['config'],outcome=held['outcome'],checkpoint=last,
            continuation=row,intent=intent,publication_performed=False,automatic_retry=False))
    finally:
        if 'semantic_context'in p:D._semantic_tail_manager.__exit__(*sys.exc_info())
        if 'initial_renewal'in p:D._initial_tail_manager.__exit__(*sys.exc_info())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe','run','permit-child','tail-probe','tail-run','retained-child']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True)
    for name in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):parser.add_argument('--'+name)
    args=parser.parse_args()
    row=dict(path=str(Path(args.plan).absolute()),sha256=args.sha256)
    if args.action=='retained-child':value=retained_child(row,dict(path=args.intent,sha256=args.intent_sha256),dict(path=args.bootstrap,sha256=args.bootstrap_sha256))
    else:value={'probe':probe,'run':run,'permit-child':permit_child,'tail-probe':tail_probe,'tail-run':tail_run}[args.action](row)
    print(json.dumps(value))
