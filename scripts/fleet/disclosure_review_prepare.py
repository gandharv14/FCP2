"""Prepare a source-complete bounded first review from an actual completed build.

Preparation creates only separate review inputs. It does not reserve a round,
renew custody, send a model request, or adopt a semantic result.
"""
from contextlib import contextmanager,ExitStack
from pathlib import Path
from dataclasses import asdict
import importlib.util,sys,json,hashlib,ast

SCHEMA='fleet-disclosure-review-preparation/v1'
FIELDS={'schema_version','helper','runner','loader','modules','build','workbook','source','authorization','output','max_bytes','max_sentences','max_batches'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical preparation binding required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):require(binding(row['path'])==row,'preparation bytes changed');return json.loads(Path(row['path']).read_bytes())

def load(name,row):
    require(binding(row['path'])==row,'preparation dependency changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

@contextmanager
def context(row,*,continued=True):
    p=read(row);require(set(p)-{'completed_custody','prior_rejections','next_round','wire_encoding','completed_environment_runtime','source_derivative','rejection_history','empty_review_helper'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed preparation selector required')
    require(('prior_rejections'in p)==('next_round'in p),'explicit round and complete prior history required together')
    require(continued is True,'preparation opens historical completed context only')
    c=p['loader'];require(set(c)=={'helper','plan','function','kwargs','mode','environment','lease','dispatcher','close_attribute'}
        and c['function']in('context','cold_context','tail_context','completed_review_context')and c['kwargs']=={'continued':True}
        and c['mode']in('contextmanager','tuple'),'closed retained loader required')
    manager=None;env=None;history=ExitStack()
    try:
        if 'completed_environment_runtime'in p:history.enter_context(completed_environment_runtime(p,c))
        original=load('prepared_disclosure_original',c['helper'])
        if c['mode']=='contextmanager':
            require(type(c['environment'])is int and c['environment']>=0
                and (c['lease']is None or type(c['lease'])is int and c['lease']>=0)
                and c['dispatcher']is None and c['close_attribute']is None,'context tuple positions required')
            manager=getattr(original,c['function'])(c['plan'],**c['kwargs']);value=manager.__enter__();env=value[c['environment']]
            # An explicit None selects the original completed-build lease below,
            # as tuple mode already does, rather than an earlier loader lease.
            lease=None if c['lease']is None else value[c['lease']]
        else:
            require(c['function']=='tail_context'and type(c['environment'])is int and c['environment']>=0 and c['lease'] is None
                and type(c['dispatcher'])is int and c['dispatcher']>=0 and c['close_attribute']=='_initial_tail_manager','original plain tail tuple required')
            value=original.tail_context(c['plan'],**c['kwargs']);env=value[c['environment']];lease=None
        require(isinstance(env,tuple)and len(env)==8,'original complete instruction environment required')
        runner=load('prepared_disclosure_runner',p['runner']);require(set(p['modules'])==set(runner.MODULES),'complete selected review runtime required')
        prior={};attributes={};modules={};reader_manager=ExitStack()
        try:
            for key,suffix in runner.MODULES.items():
                package='scripts.fleet'if key=='gate'else'scripts.batch';parent=__import__(package,fromlist=[suffix]);name=package+'.'+suffix
                prior[name]=sys.modules.get(name);attributes[(package,suffix)]=getattr(parent,suffix,None)
                modules[key]=load(name,p['modules'][key]);setattr(parent,suffix,modules[key])
            if 'rejection_history'in p:
                reader_manager.enter_context(completed_rejection_history(p,modules['rounds']))
            w,s,buildlease=modules['rounds'].completed_build(p['build'])
            require(s.content.bindings['original_raw_source']==p['source']and s.content.workbook==p['workbook'],'source-scoped original completed worker required')
            lease=buildlease if lease is None else lease
            if 'completed_custody'in p:
                with completed_custody(row,p,env,w,s,lease)as current:yield env,current
            else:yield env,lease
        finally:
            reader_manager.close()
            for name,old in reversed(list(prior.items())):
                if old is None:sys.modules.pop(name,None)
                else:sys.modules[name]=old
            for (package,name),old in attributes.items():
                parent=sys.modules[package]
                if old is None:delattr(parent,name)
                else:setattr(parent,name,old)
    finally:
        try:
            if manager is not None:manager.__exit__(*sys.exc_info())
            elif env is not None and c['close_attribute']is not None:
                D=value[c['dispatcher']]
                manager=getattr(D,c['close_attribute'],None)
                if manager is not None:manager.__exit__(*sys.exc_info())
        finally:history.close()


@contextmanager
def completed_rejection_history(p,rounds):
    """Reuse only explicitly listed, independently proven sealed rejections."""
    selected=p['rejection_history']
    require(set(selected)=={'helper','profile'}and p.get('prior_rejections')
        and p.get('next_round',1)>1,'closed completed rejection history selection required')
    reader=load('prepared_completed_rejection_cache',selected['helper'])
    with reader.installed(rounds,selected['profile']):yield


def completed_environment_runtime(p,loader):
    """Select only the completed environment's historical takeover reader."""
    selected=p['completed_environment_runtime']
    require(set(selected)=={'helper','profile'}and 'completed_custody'in p,
        'historical environment reader requires current completed custody')
    profile=read(selected['profile']);runtime=load('prepared_completed_environment_runtime',selected['helper'])
    require(profile['runtime']==selected['helper']and profile['schema_version']=='fleet-retained-runtime/v1'
        and profile['entrypoint']==loader['helper']
        and profile['read_call']==dict(function=loader['function'],args=[loader['plan']],kwargs=loader['kwargs'])
        and all(profile[k]==[]for k in ('patches','dependencies','guard_modules','child_plans','journal_roots'))
        and len(profile['symbols'])==1,'only one declared historical environment function permitted')
    symbol=profile['symbols'][0]
    original=read(loader['plan'])['context'];environment=read(original['plan'])
    require(original['function']=='completed_context'and original['kwargs']==dict(probe=True)
        and original['projection']==dict(dispatcher=1,environment=2)
        and environment['schema_version']=='fleet-completed-environment-continuation/v1'
        and environment['takeover_completion']is not None
        and symbol['original']==environment['entry']and symbol['names']==['takeover_context']
        and Path(symbol['original']['path']).name=='environment_checkpoint_continue.py',
        'historical reader detached from actual completed environment')
    require(binding(symbol['selected']['path'])==symbol['selected'],'selected environment reader changed')
    def remainder(row):
        tree=ast.parse(Path(row['path']).read_text())
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='takeover_context']
        require(len(functions)==1,'unique historical takeover reader required')
        return ast.dump(ast.Module(body=[n for n in tree.body if n not in functions],type_ignores=[]),include_attributes=False)
    require(remainder(symbol['original'])==remainder(symbol['selected']),
        'historical environment selection changed unrelated implementation')
    return runtime.installed(selected['profile'])


@contextmanager
def completed_custody(row,p,env,worker,selection,previous):
    """Select a sealed replacement over the exact original completed build."""
    c=p['completed_custody']
    require(set(c)-{'stopped_outcome','stopped_pre_dispatch'}=={'utilities','takeover','tracker','owner'}
        and not('stopped_outcome'in c and 'stopped_pre_dispatch'in c),'closed completed review custody required')
    U=load('prepared_review_completed_custody',c['utilities'])
    outcome=read(p['build']['outcome']);guard=read(outcome['resource']);intent=read(guard['intent'])
    require(intent['action']=='step'and set(intent['arguments'])=={'descriptor','checkpoint'}
        and intent['arguments']['descriptor']==p['build']['descriptor'],'completed build guard detached from descriptor')
    tp=read(c['takeover']['plan'])
    require(tp['selection']['tracker']['path']==str(worker.context.tracker_root/(p['workbook']+'.json'))
        and c['tracker']['path']==tp['selection']['tracker']['path'],
        'completed custody belongs to another worker')
    origin_outcome,origin_checkpoint=completed_custody_origin(p,env,intent)
    selected=dict(completed_takeover=c['takeover'],workbook=p['workbook'],source=p['source'],owner=c['owner'],tracker=c['tracker'],
        result=origin_checkpoint,outcome=origin_outcome,
        retained_context=dict(helper=p['loader']['helper'],plan=p['loader']['plan']),
        retained_read_call=dict(function=p['loader']['function'],args=[p['loader']['plan']],kwargs=p['loader']['kwargs']))
    current,installed=U.retained_lease(selected)
    try:
        T=__import__('scripts.batch.harbor_lease_takeover',fromlist=['validate_completion'])
        completed_custody_chain(T,c['takeover']['completion'],c['tracker']['path'],previous,current)
        # Current custody remains an owning worker check, never an archive view.
        env[3].active(worker,selection,current)
        yield current
    finally:installed.manager.__exit__(*sys.exc_info())


def completed_custody_chain(T,completion,tracker_path,previous,current):
    """Authenticate every replacement and its original completed-build lease."""
    row=completion;seen=set();latest=None;origin=None
    while row is not None:
        require(row['path']not in seen,'cyclic completed custody chain')
        seen.add(row['path']);before,after,lease=T.validate_completion(row)
        if latest is None:
            latest=after
            require(lease==current,'latest custody lease differs')
        stopped=read(read(row)['stopped_owner_proof']);selected=stopped['selection']
        require(selected['tracker']['path']==tracker_path,'replacement belongs to another tracker')
        prior=selected.get('prior_completion')
        original=before['leases']['task_agent']
        if (original['owner'],original['token'],before['revision'],stopped['before']['sha256'])==(
                previous.owner,previous.token,previous.revision,previous.sha256):
            require(origin is None,'ambiguous completed build custody in replacement chain')
            origin=stopped['before']
        row=prior
    require(origin is not None,'replacement detached from completed build custody')
    T.continuation_chain(origin,asdict(previous),latest,current,
        takeover=completion,tracker_path=Path(tracker_path))


def completed_custody_origin(p,env,build_intent):
    """Retain a later sealed review guard without inventing a build replay."""
    c=p['completed_custody']
    if 'stopped_pre_dispatch'in c:return pre_dispatch_custody_origin(p,env)
    if 'stopped_outcome'not in c:return p['build']['outcome'],build_intent['arguments']['checkpoint']
    from scripts.batch import harbor_disclosure_review_round as Q
    D=Q.bound_module(p['build']['dispatcher'])
    # The original guard decoder is valid for this sealed failure, while its
    # optional resumable-checkpoint decoder deliberately rejects this status.
    row=c['stopped_outcome'];result,value=D.successful(row)
    built=read(p['build']['result']);outcome=read(row);guard=read(outcome['resource']);intent=read(guard['intent'])
    blocked=read(value['checkpoint'])
    require(outcome['status']=='COMPLETED_OPERATION'and outcome['result']==result
        and guard['returncode']==0 and guard['failure']is None
        and value['status']==blocked['status']=='MODEL_RECONCILIATION_REQUIRED'
        and value['next_step']==blocked['step']=='disclosure-review'
        and value['publication_performed']is False
        and value['descriptor']==p['build']['descriptor']==built['descriptor']
        and value['selection']==built['selection']==blocked['selection']
        and value['lease']==built['lease'] and value['evidence'][0]==p['build']['result']
        and intent['action']=='step'and intent['arguments']==dict(descriptor=p['build']['descriptor'],checkpoint=p['build']['result'])
        and intent['code']==p['build']['instruction']==binding(env[1].__file__)
        and intent['controls']==env[0].encode(env[7])
        and intent['timeout_seconds']==env[1].guard_seconds('step',intent['arguments'])
        and intent['memory_limit_bytes']==env[0].MEMORY,
        'stopped review custody detached from actual completed build')
    worker,_,_=env[1].selected(p['build']['descriptor'])
    require(value['checkpoint']==binding(worker.run/'steps/disclosure-review/blocked.json'),
        'stopped review checkpoint moved')
    return row,p['build']['result']


def pre_dispatch_custody_origin(p,env):
    """The executed cold loader binds a build followed by renewal and sealed HTTP."""
    loader=p['loader'];selected=p['completed_custody']['stopped_pre_dispatch'];q=read(selected)
    require(selected==loader['plan'] and loader['function']=='cold_context'
        and loader['kwargs']=={'continued':True}and loader['mode']=='contextmanager'
        and loader['environment']==3 and loader['lease']==4
        and q['schema_version']=='fleet-retained-pre-dispatch-review/v1'
        and q['helper']==loader['helper']and q['workbook']==p['workbook']and q['source']==p['source']
        and q['terminal_http']in p['prior_rejections'], 'completed pre-dispatch custody differs from executed loader')
    # This exact helper has already executed source_context and completed_prefix
    # on entry. Verify that the selected stopped boundary is the same one,
    # while keeping the original completed build and its earlier lease intact.
    original=read(read(q['pre_dispatch'])['original_job']);outcome=read(q['outcome']);value=read(q['result'])
    guard=read(outcome['resource']);intent=read(guard['intent']);blocked=read(q['blocked'])
    require(original['driver']==p['build']['dispatcher']and original['config']==q['config']
        and env[5]==read(q['config'])and str(env[6])==read(q['snapshot'])['root']
        and Path(p['build']['outcome']['path']).parent.parent==Path(original['operations'])
        and outcome['status']=='COMPLETED_OPERATION'and outcome['result']==q['result']
        and guard['returncode']==0 and guard['failure']is None
        and value['status']==blocked['status']=='MODEL_RECONCILIATION_REQUIRED'
        and value['next_step']==blocked['step']=='disclosure-review'
        and value['descriptor']==q['descriptor']and value['checkpoint']==q['blocked']
        and intent['action']=='step'and intent['arguments']['descriptor']==q['descriptor']
        and intent['code']==p['build']['instruction']==binding(env[1].__file__),
        'completed pre-dispatch stopped guard detached from original source build')
    return q['outcome'],q['result']


def review_history(p,Q,worker,scope):
    """Select a distinct round only after authenticating every sealed rejection."""
    prior=p.get('prior_rejections',[]);ordinal=p.get('next_round',1)
    require(type(ordinal)is int and ordinal>=1 and isinstance(prior,list)
            and len(prior)==ordinal-1,'explicit next round must preserve every consumed review')
    for number,rejected in enumerate(prior,1):Q.closed_rejection(rejected,scope,number)
    Q._primary_history(worker.campaign,scope,ordinal,prior)
    return ordinal,prior


def selected_wire_encoding(p,wire):
    value=p.get('wire_encoding',wire.SCHEMA)
    allowed={getattr(wire,name,None)for name in ('SCHEMA','COLUMNAR_SCHEMA','RUN_SCHEMA')}
    require(type(value)is str and value in allowed,'selected lossless wire encoding is unsupported by pinned module')
    return value


def unused_round_directory(worker,ordinal,prior):
    parent=worker.run/'disclosure-review-rounds'
    expected={'%04d'%i for i,row in enumerate(prior,1)
              if read(row).get('schema_version')=='harbor-completed-disclosure-partition-rejection/v1'}
    actual={p.name for p in parent.iterdir()}if parent.exists()else set()
    require(actual==expected and '%04d'%ordinal not in actual,
            'unlisted or already reserved partition round remains')


def prepare(row):
    p=read(row);output=Path(p['output']);require(output.is_absolute()and not output.exists(),'fresh separate preparation namespace required')
    with context(row)as(env,lease):
        from scripts.batch import harbor_disclosure_review_round as Q,harbor_disclosure_partition_review as R,harbor_disclosure_partition_evidence as V,harbor_disclosure_partition_wire as wire
        from scripts.batch import harbor_instruction_worker as W
        from scripts.batch.harbor_runtime_readiness import delivered_workbook
        encoding=selected_wire_encoding(p,wire)
        E=R.E;w,s,_=Q.completed_build(p['build']);_,before,tracker=W.active(w,s,lease)
        require(before['workbook_id']==p['workbook']and before['bindings']['original_raw_source']==p['source'],'actual current source differs')
        scope=Q.semantic_scope(dict(workbook=p['workbook'],original_source=p['source']))
        ordinal,prior=review_history(p,Q,w,scope)
        unused_round_directory(w,ordinal,prior)
        stage=s.content.stage
        if 'source_derivative'in p:
            selected=read(p['source_derivative']);D=Q.bound_module(selected['implementation'])
            require(D.SCHEMA=='harbor-disclosure-partition-source-derivative/v1','closed source derivative reader required')
            _,_,stage=D.validate(p['source_derivative'],worker=w,selection=s,build=p['build'],prior_rejections=prior)
        source=s.content.bindings['source_file'];golden={k:source[k]for k in('path','sha256')};delivered=E.record(delivered_workbook(stage,p['workbook']))
        from scripts.batch.harbor_semantic_readiness import disclosure_manifest
        instruction=E.record(stage/'instruction.md');disclosure=E.record(stage/'tests/disclosure.json')
        manifest=disclosure_manifest(instruction,disclosure,golden,delivered,root=w.root)
        output.mkdir(parents=True);E.save(output/'manifest.json',manifest);E.retain(output/'instruction.md',E.checked_bytes(instruction))
        manifest_binding=E.record(output/'manifest.json');instruction_binding=E.record(output/'instruction.md')
        body=R.full_body(manifest_binding,instruction_binding,disclosure,golden,delivered,root=w.root)
        E.save(output/'full-body.json',body)
        contract=None
        if 'empty_review_helper'in p:
            require(not manifest['sentences']and ordinal>1 and len(prior)==ordinal-1,
                'explicit empty review requires an authentic consumed rejection')
            contract=empty_review_contract(p,body,stage,output,E,W,V,instruction_binding,disclosure,golden,delivered)
        if contract:
            require(p['max_batches']==1 and p['max_bytes']<=V.MAX_BYTES and p['max_sentences']<=V.MAX_SENTENCES,
                'empty review requires one batch inside original finite bounds')
            projected=V.plan(body,golden['path'],delivered['path'],root=w.root,max_bytes=p['max_bytes'],max_sentences=p['max_sentences'],empty_review_contract=contract)
        else:
            bounds=dict(schema_version=V.WIRE_BOUNDS_SCHEMA,authorization=p['authorization'],full_body_sha256=V.digest(body),sources=dict(golden=golden,delivered=delivered),max_bytes=p['max_bytes'],max_sentences=p['max_sentences'],max_batches=p['max_batches'],wire_encoding=encoding)
            E.save(output/'bounds.json',bounds)
            projected=V.plan(body,golden['path'],delivered['path'],root=w.root,max_bytes=p['max_bytes'],max_sentences=p['max_sentences'],bounds_policy=E.record(output/'bounds.json'))
        E.save(output/'projection.json',projected)
        inputs=dict(schema_version=R.PLAN,full_body=E.record(output/'full-body.json'),projection=E.record(output/'projection.json'),manifest=manifest_binding,instruction=instruction_binding,disclosure=disclosure,golden=golden,delivered=delivered,workbook=p['workbook'],original_source=p['source'],policy=asdict(s.content.model_policy),worker_run=str(w.run),initial=E.record(w.run/'initial.json'),producer_run=str(W.task(w,s,tracker).run))
        E.save(output/'inputs.json',inputs)
        require(E.record(tracker['path'])==tracker,'tracker changed during input derivation')
        result=dict(status='PREPARED_NOT_RESERVED',plan=row,inputs=E.record(output/'inputs.json'),build=p['build'],tracker=tracker,batches=len(projected['batches']),models_dispatched=0,lease_mutations=0,task_mutations=0)
        E.save(output/'result.json',result);return E.record(output/'result.json')


def empty_review_contract(p,body,stage,output,E,W,V,instruction,disclosure,golden,delivered):
    """Retain immutable stage inputs; never put numeric answer keys in a request."""
    helper=load('prepared_empty_disclosure_review',p['empty_review_helper'])
    require(helper.CONTRACT=='harbor-empty-disclosure-review-contract/v1',
        'closed source-backed empty review helper required')
    E.retain(output/'disclosure.json',E.checked_bytes(disclosure))
    answer=E.record(stage/'tests/answer_key.json')
    E.retain(output/'answer_key.json',E.checked_bytes(answer))
    _,inventory=W.static.publication._tree_hash(stage)
    E.save(output/'stage-manifest.json',inventory)
    targets=sorted(helper.normalized_target(k)for k in read(answer)['targets'])
    value=dict(schema_version=helper.CONTRACT,implementation=p['empty_review_helper'],authorization=p['authorization'],
        build_result=p['build']['result'],instruction=instruction,disclosure=E.record(output/'disclosure.json'),
        answer_key=E.record(output/'answer_key.json'),stage_manifest=E.record(output/'stage-manifest.json'),
        golden=golden,delivered=delivered,targets=targets)
    E.save(output/'empty-review-contract.json',value)
    return E.record(output/'empty-review-contract.json')
