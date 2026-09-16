"""Continue a completed instruction through an explicit root lease handoff.

The retained source context and dispatcher remain the owning implementations.
Only completed custody, a named failed enqueue directory, and the selected
current facade are supplied by this continuation.
"""
import argparse
import json
import sys
import hashlib
import importlib.util
from contextlib import contextmanager, ExitStack
from pathlib import Path

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),
                        'canonical continuation artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'continuation bytes changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'continuation implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

SCHEMA='fleet-completed-instruction-continuation/v1'
FIELDS={'schema_version','helper','previous','history','bridge','bridge_profile','takeover_runtime',
        'facade','failure','failure_proof','worker_outcome','owner','fence','output','tracker','source','workbook',
        'capacity_policy','utilities'}


def retained_prefix(p,old,source,U,D,env,first):
    """Validate the actual later completed guards and their stopped successor."""
    job=read(binding(Path(old['output'])/'authoring-job.json'));ops=Path(job['operations'])
    dirs=sorted(x for x in ops.iterdir()if x.is_dir())
    require(1<len(dirs)<=40 and [x.name for x in dirs]==['%03d'%n for n in range(len(dirs))],
            'closed contiguous completed tail required')
    bp=read(p['bridge_profile']);previous=first
    derived=dict(source,helper=old['helper'],output=old['output'],owner=old['owner'],fence=old['fence'])
    for directory in dirs[:-1]:
        outcome=binding(directory/'outcome.json');saved=read(outcome)
        cp,value=D.successful(outcome,env);guard=read(saved['resource']);intent=read(guard['intent'])
        side=read(binding(directory/'disclosure-bootstrap.json'))
        kind,actual=U.retained_command(side['original_command'],derived,D,env)
        require(kind=='instruction'and actual==guard['intent']and side['plan']==p['previous']
                and side==dict(schema_version=source['schema_version'],plan=p['previous'],helper=old['helper'],
                    kind='instruction',intent=guard['intent'],original_command=side['original_command'],
                    seconds=intent['timeout_seconds'],memory_bytes=8589934592)
                and intent['action']=='step'
                and intent['code']==binding(env[1].__file__)
                and intent['controls']==env[0].encode(env[7])
                and intent['timeout_seconds']==env[1].guard_seconds('step',intent['arguments']),
                'completed tail guard differs')
        require(intent['arguments']['checkpoint']==previous,'tail skipped a checkpoint')
        previous=cp
    require(outcome==bp['outcome']and cp==bp['result']and value['descriptor']==bp['descriptor']
            and value['next_step']=='enqueue'and value['status']=='STEP_COMPLETE',
            'takeover omitted the actual completed tail')
    failed=read(p['failure']);guard=read(failed['resource']);intent=read(guard['intent'])
    require(p['failure']==binding(dirs[-1]/'outcome.json')and failed['resource']==bp['guard']
            and intent['arguments']==dict(descriptor=bp['descriptor'],checkpoint=bp['result']),
            'failed next guard differs from completed takeover boundary')
    outer=read(p['worker_outcome']);wi=read(outer['intent']);workerjob=read(wi['job'])
    require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
            and wi['owner']==old['owner']and workerjob['driver']==old['helper']
            and workerjob['result']==str(Path(old['output'])/'result.json'), 'different stopped tail worker')


@contextmanager
def context(row,*,invocation=None):
    p=read(row)
    if p.get('completed_pre_dispatch') is True:
        with pre_dispatch_context(row,invocation=invocation) as loaded:yield loaded
        return
    if p.get('completed_review') is True:
        with completed_review_context(row,invocation=invocation) as loaded:yield loaded
        return
    if p.get('prepared_review') is True:
        with prepared_context(row,invocation=invocation) as loaded:yield loaded
        return
    require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),
            'closed completed instruction continuation required')
    old=read(p['previous']);require(old['schema_version']=='fleet-disclosure-completed-tail/v1'
        and p['workbook']==old['workbook']and p['source']==old['source']
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'continuation changed source or repeated ownership')
    H=module('completed_instruction_history',p['history']);require(binding(H.__file__)==old['helper'],
        'history is not the actual previous tail implementation')
    source=read(old['previous']);M=module('completed_instruction_source',source['helper'])
    S=module('completed_instruction_guard',old['shell'])
    import ast,inspect,textwrap
    for name in ('install_commands','child'):
        original=textwrap.dedent(inspect.getsource(getattr(M,name)))
        expected=original.replace("3300 if kind=='instruction'else read(intent)['seconds']","U.retained_seconds(kind,intent,env)")
        require(ast.dump(ast.parse(expected))==ast.dump(ast.parse(textwrap.dedent(inspect.getsource(getattr(S,name))))),
                'selected dispatcher shell changed beyond its original duration correction')
    U=module('completed_instruction_utilities',p['utilities'])
    B=module('completed_instruction_takeover',p['bridge']);bp=read(p['bridge_profile'])
    require(bp['implementation']==p['bridge']and bp['source']==p['source']and bp['workbook']==p['workbook']
        and bp['tracker']==p['tracker']and bp['directory']==str(Path(p['output'])/'handoff'),
        'completed takeover profile detached from continuation')
    with M.context(old['previous'],continued=True)as(_,oldU,D,env,worker,selection,lease,K,raw,job):
        first,_=H.prefix(old,source,oldU,D,env);retained_prefix(p,old,source,oldU,D,env,first)
        tp=read(bp['takeover_plan']);T=module('completed_instruction_T',bp['takeover_module'])
        T.life=module('completed_instruction_lifecycle',tp['shared']['lifecycle'])
        loader=module('completed_instruction_T_layout',p['takeover_runtime'])
        if 'layout'in tp:loader.select_layout(T,tp['layout'])
        I,W=env[1],env[3]
        facade=p['facade'];require(set(facade)=={'helper','original','selected'}and facade['selected']==str(worker.root),
                                  'closed current facade selection required')
        L=module('completed_instruction_current_facade',facade['helper'])
        original_successful=D.successful
        successful=lambda bound:original_successful(bound,env)
        with ExitStack()as stack:
            stack.enter_context(L.installed(W,original=facade['original'],selected=facade['selected']))
            complete=Path(bp['directory'])/'handoff.json'
            def successor():
                own=Path(p['output'])/'authoring-job.json'
                if not own.exists():return None
                ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
                require(complete.exists()and read(binding(own))==dict(job,outcome=binding(complete),operations=str(ops)),
                        'completed handoff child job changed')
                observed=ops/'000/outcome.json'
                if not observed.exists():return None
                row=binding(observed)
                return row if read(row)['status']=='COMPLETED_OPERATION'else None
            completed_successor=successor()
            # Full historical proof may be revisited after enqueue changes the
            # tracker. Only the selected current operation must retain live CAS.
            proof,value,w,s,previous,current=B.validate(p['bridge_profile'],I,W,T,successful,
                live=invocation is None and completed_successor is None)
            require(current.owner==p['owner'],'takeover selected a different native owner')
            if complete.exists():
                done=binding(complete)
                stack.enter_context(B.prior_scope(done,p['bridge_profile'],I,W,T,successful,completed_successor=successor))
            elif invocation is not None:raise ValueError('cold child lacks completed root handoff')
            else:done=None
            directory=stack.enter_context(B.enqueue_directory_scope(p['failure_proof'],p['bridge_profile'],I,W,T,successful))
            stack.enter_context(B.step_scope(W,B.lease_predicate(p['bridge_profile'],I,W,T,successful),directory=directory))
            if completed_successor is not None:B.completed(done,I)
            derived=dict(source,helper=p['helper'],output=p['output'],owner=p['owner'],fence=p['fence'],capacity_policy=p['capacity_policy'])
            if invocation is not None:
                intent,sidecar=invocation;side=read(sidecar)
                kind,actual=U.retained_command(side['original_command'],derived,D,env)
                require(kind=='instruction'and actual==intent and side['plan']==row and side['helper']==p['helper'],
                        'cold checkpoint detached from its exact parent')
                args=read(intent)['arguments'];w,controls,_=I.selected(args['descriptor']);_,s,current=I._prior(args['checkpoint'],args['descriptor'])
                require(controls==env[7]and w.run==worker.run and w.root==worker.root,'cold selected worker changed')
                W.active(w,s,current)
            if done is not None:
                def completed(bound,observed=None):
                    if bound==done:return B.completed(done,I)
                    return original_successful(bound,observed)
                D.successful=completed
                stack.callback(setattr,D,'successful',original_successful)
            yield p,derived,S,U,D,env,w,s,current,K,raw,job,B,T,successful,done


PRE_DISPATCH_FIELDS={'schema_version','helper','completed_pre_dispatch','history','previous','bridge','bridge_profile',
    'takeover_runtime','shell','utilities','original_job','owner','fence','output','tracker','source',
    'workbook','capacity_policy','lifecycle_runtime'}


@contextmanager
def pre_dispatch_context(row,*,invocation=None):
    """Continue a completed instruction after a stopped cold renewal import."""
    p=read(row);require(set(p)==PRE_DISPATCH_FIELDS and p['schema_version']==SCHEMA
        and p['helper']==binding(__file__)and p['completed_pre_dispatch']is True,
        'closed completed pre-dispatch continuation required')
    history=read(p['previous']);M=module('completed_pre_dispatch_history',p['history'])
    require(history['schema_version']=='fleet-completed-pre-dispatch-history/v1'
        and history['helper']==p['history']and history['workbook']==p['workbook']and history['source']==p['source'],
        'completed instruction source history differs')
    h,D,env,job,checkpoint=M.context(p['previous'])
    prior=read(history['previous']);I,W=env[1],env[3]
    require(h==history and p['original_job']==history['original_job']
        and p['owner']!=prior['owner']and p['fence']==prior['fence']+1
        and job==dict(read(p['original_job']),driver=history['dispatcher']),
        'completed instruction changed authoring ancestry')
    S=module('completed_pre_dispatch_shell',p['shell']);U=module('completed_pre_dispatch_utilities',p['utilities'])
    B=module('completed_pre_dispatch_bridge',p['bridge']);bp=read(p['bridge_profile']);tp=read(bp['takeover_plan'])
    require(bp['implementation']==p['bridge']and bp['workbook']==p['workbook']and bp['source']==p['source']
        and bp['result']==checkpoint and bp['outcome']==history['last_step']and bp['tracker']==p['tracker']
        and bp['directory']==str(Path(p['output'])/'handoff'),'completed instruction handoff detached from source')
    T=module('completed_pre_dispatch_T',bp['takeover_module']);T.life=module('completed_pre_dispatch_lifecycle',tp['shared']['lifecycle'])
    if 'layout'in tp:module('completed_pre_dispatch_T_layout',p['takeover_runtime']).select_layout(T,tp['layout'])
    original_successful=D.successful;successful=lambda bound:original_successful(bound,env)
    value=read(checkpoint);worker,_,_=I.selected(value['descriptor']);raw=env[0]._run_in_process_group
    with ExitStack()as stack:
        stack.enter_context(takeover_allowance(T,tp['shared']['allowance']))
        stack.enter_context(prepared_lifecycle_runtime(p,worker,T))
        complete=Path(bp['directory'])/'handoff.json'
        def successor():
            own=Path(p['output'])/'authoring-job.json'
            if not own.exists():return None
            ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
            require(complete.exists()and read(binding(own))==dict(job,outcome=binding(complete),operations=str(ops)),
                'completed handoff job changed')
            observed=ops/'000/outcome.json'
            if not observed.exists():return None
            bound=binding(observed)
            return bound if read(bound)['status']=='COMPLETED_OPERATION'else None
        completed_successor=successor()
        checked,old,w,s,previous,current=B.validate(p['bridge_profile'],I,W,T,successful,live=completed_successor is None)
        require(current.owner==p['owner'],'completed native handoff selected another owner')
        done=binding(complete)if complete.exists()else None
        if done is not None:stack.enter_context(B.prior_scope(done,p['bridge_profile'],I,W,T,successful,completed_successor=successor))
        elif invocation is not None:raise ValueError('completed child lacks actual handoff')
        stack.enter_context(B.step_scope(W,B.lease_predicate(p['bridge_profile'],I,W,T,successful)))
        if invocation is not None:
            intent,sidecar=invocation;side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
            require(kind=='instruction'and actual==intent and side['plan']==row and side['helper']==p['helper'],
                'completed child detached from its original owning guard')
            args=read(intent)['arguments'];selected,controls,_=I.selected(args['descriptor']);_,selection,lease=I._prior(args['checkpoint'],args['descriptor'])
            require(selected.run==w.run and selected.root==w.root and controls==env[7],'completed child changed worker')
            W.active(selected,selection,lease)
        if done is not None:
            def completed(bound,observed=None):
                if bound==done:return B.completed(done,I)
                return original_successful(bound,observed)
            D.successful=completed;stack.callback(setattr,D,'successful',original_successful)
        yield p,p,S,U,D,env,w,s,current,None,raw,job,B,T,successful,done


def run(row):
    if read(row).get('completed_review') is True:return run_completed_review(row)
    if read(row).get('prepared_review') is True:return run_prepared(row)
    with context(row)as(p,derived,M,U,D,env,w,s,l,K,raw,job,B,T,successful,done):
        require(done is None and not Path(p['output']).exists(),'completed continuation already started')
        U.custody(derived,w.root);Path(p['output']).mkdir()
        done=B.handoff(p['bridge_profile'],env[1],env[3],T,successful,U.keep)
    # Reopen the same authenticated history with the actual new handoff.
    with context(row)as(p,derived,M,U,D,env,w,s,l,K,raw,job,B,T,successful,done):
        U.custody(derived,w.root);ops=U.operations_for(D,env[5],Path(p['output']))
        M.install_commands(row,derived,U,D,env,raw,job)
        bound=U.keep(Path(p['output'])/'authoring-job.json',dict(job,outcome=done,operations=str(ops)))
        D.run(bound);held=read(binding(ops/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
        return U.keep(Path(p['output'])/'result.json',dict(status=value['status'],workbook=p['workbook'],
            continuation=row,job=bound,outcome=held['outcome'],checkpoint=checkpoint,
            publication_performed=False,automatic_retry=False))


def child(row,intent,sidecar):
    require(sys.flags.isolated and sys.dont_write_bytecode,'isolated completed child required')
    with context(row,invocation=(intent,sidecar))as(p,derived,M,U,D,env,w,s,l,K,raw,job,B,T,successful,done):
        side=read(sidecar);kind,actual=U.retained_command(side['original_command'],derived,D,env)
        require(kind=='instruction'and actual==intent and side==dict(schema_version=M.SCHEMA,plan=row,
            helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],
            seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592)
            and Path(sidecar['path'])==Path(intent['path']).parent/'disclosure-bootstrap.json',
            'original child guard or sidecar changed')
        U.custody(derived,w.root);M.install_commands(row,derived,U,D,env,raw,job)
        return env[1].child(intent)


@contextmanager
def takeover_allowance(T,row):
    """Supply only the exact declared allowance import to the selected T reader."""
    import scripts.batch as package
    name='scripts.batch.harbor_continuation_allowance';prior=sys.modules.get(name)
    attribute=getattr(package,'harbor_continuation_allowance',None)
    A=module(name,row);A.life=T.life;package.harbor_continuation_allowance=A
    try:yield A
    finally:
        if prior is None:sys.modules.pop(name,None)
        else:sys.modules[name]=prior
        if attribute is None:
            if hasattr(package,'harbor_continuation_allowance'):delattr(package,'harbor_continuation_allowance')
        else:package.harbor_continuation_allowance=attribute


@contextmanager
def prepared_model_runtime(p,executor):
    """Reuse the proved shared physical transport while retaining the old E plan."""
    import scripts.fleet as fleet
    selected=p['model_runtime']
    require(set(selected)=={'helper','gate','capacity','transport','physical_module','physical_policy'}
        and selected['capacity']==p['capacity_policy'],'closed prepared transport dependencies required')
    R=module('prepared_existing_transport_runtime',selected['helper'])
    originals={n:(sys.modules.get('scripts.fleet.'+n),getattr(fleet,n,None))for n in ('model_gate','capacity_policy')}
    try:
        for name,key in [('model_gate','gate'),('capacity_policy','capacity')]:
            loaded=module('scripts.fleet.'+name,selected[key]);setattr(fleet,name,loaded)
        with R.transport_runtime(selected,executor):
            R.transport_checked(selected,executor)
            yield
    finally:
        for name,(prior,attribute)in originals.items():
            if prior is None:sys.modules.pop('scripts.fleet.'+name,None)
            else:sys.modules['scripts.fleet.'+name]=prior
            if attribute is None:
                if hasattr(fleet,name):delattr(fleet,name)
            else:setattr(fleet,name,attribute)


@contextmanager
def prepared_lifecycle_runtime(p,worker,T):
    """Read current allowance-backed lifecycle events with their declared reader."""
    selected=p['lifecycle_runtime']
    require(set(selected)=={'helper','profile'},'closed current lifecycle runtime required')
    RT=module('prepared_current_lifecycle_runtime',selected['helper']);profile=read(selected['profile'])
    expected=dict(schema_version=RT.SCHEMA,runtime=selected['helper'],entrypoint=p['history'],
        read_call=dict(function='context',args=[p['previous']],kwargs={}),patches=[],guard_modules=[],
        child_plans=[],journal_roots=[],symbols=[dict(
            module_name='scripts.batch.harbor_tracker_lifecycle',
            original=binding(worker.root/'scripts/batch/harbor_tracker_lifecycle.py'),
            selected=binding(T.life.__file__),names=['validate_lifecycle'])],dependencies=[dict(
            module_name='scripts.batch.harbor_tracker_lifecycle',
            original=binding(worker.root/'scripts/batch/harbor_tracker_lifecycle.py'),
            selected=binding(T.life.__file__))])
    require(profile==expected,'only the actual takeover lifecycle dependency may change')
    with RT.installed(selected['profile']):yield


PENDING_FIELDS={'schema_version','helper','prepared_review','history','previous','bridge','bridge_profile',
    'takeover_runtime','shell','utilities','original_job','owner','fence','output','tracker','source',
    'workbook','capacity_policy','model_runtime','lifecycle_runtime'}


@contextmanager
def prepared_context(row,*,invocation=None):
    """One existing prepared review, through the original guarded instruction API."""
    p=read(row)
    require(set(p)==PENDING_FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and p['prepared_review']is True,'closed prepared review continuation required')
    M=module('completed_prepared_context',p['history']);history=read(p['previous'])
    require(history['helper']==p['history']and history['workbook']==p['workbook']and history['source']==p['source'],
        'prepared continuation changed source history')
    B=module('completed_prepared_bridge',p['bridge']);bp=read(p['bridge_profile'])
    require(bp['implementation']==p['bridge']and bp['pending_reader']==p['history']
        and bp['pending_history']==p['previous']and bp['tracker']==p['tracker']
        and bp['directory']==str(Path(p['output'])/'handoff'),'pending takeover profile detached')
    U=module('completed_prepared_utilities',p['utilities']);S=module('completed_prepared_commands',p['shell'])
    with M.context(p['previous'])as(h,old,loaded,w,s,previous,request):
        _,_,_,env,A,D,job,_,_,_,_,_=loaded
        require(read(p['original_job'])==job and p['owner']!=read(history['original']['plan'])['owner'] and type(p['fence'])is int and p['fence']==read(history['original']['plan'])['previous_fence']+2,
                'prepared review must retain original job and select a fresh owner')
        I,W=env[1],env[3];tp=read(bp['takeover_plan'])
        T=module('completed_prepared_takeover',bp['takeover_module'])
        T.life=module('completed_prepared_lifecycle',tp['shared']['lifecycle'])
        if 'layout'in tp:
            loader=module('completed_prepared_T_layout',p['takeover_runtime']);loader.select_layout(T,tp['layout'])
        successful=lambda bound:D.successful(bound,env)
        with ExitStack()as stack:
            stack.enter_context(takeover_allowance(T,tp['shared']['allowance']))
            stack.enter_context(prepared_lifecycle_runtime(p,w,T))
            stack.enter_context(prepared_model_runtime(p,env[3].executor))
            checked,value,worker,selection,prior,current=B.validate(p['bridge_profile'],I,W,T,successful)
            require(current.owner==p['owner']and worker==w and selection==s,'pending native owner/source differs')
            complete=Path(bp['directory'])/'handoff.json';done=binding(complete)if complete.exists()else None
            if done is not None:stack.enter_context(B.prior_scope(done,p['bridge_profile'],I,W,T,successful))
            elif invocation is not None:raise ValueError('pending cold child lacks completed handoff')
            stack.enter_context(B.pending_step_scope(p['bridge_profile'],I,W,T,successful,
                wrapper=read(history['original']['plan'])['pending_reader']))
            raw=env[0]._run_in_process_group
            if invocation is not None:
                intent,sidecar=invocation;side=read(sidecar)
                kind,actual=U.retained_command(side['original_command'],p,D,env)
                require(kind=='instruction'and actual==intent and side['plan']==row
                    and side['helper']==p['helper']and read(intent)['arguments']==dict(
                        descriptor=read(done)['descriptor'],checkpoint=read(done)['result']),
                    'pending child detached from actual handoff')
                W.active(worker,selection,current)
            yield p,p,S,U,D,env,worker,selection,current,None,raw,job,B,T,successful,done


def run_prepared(row):
    with context(row)as(p,derived,S,U,D,env,w,s,l,K,raw,job,B,T,successful,done):
        require(done is None and not Path(p['output']).exists(),'prepared review already started')
        U.custody(p,w.root);Path(p['output']).mkdir()
        B.handoff(p['bridge_profile'],env[1],env[3],T,successful,U.keep)
    with context(row)as(p,derived,S,U,D,env,w,s,l,K,raw,job,B,T,successful,done):
        U.custody(p,w.root);handoff=read(done);S.install_commands(row,p,U,D,env,raw,job)
        with D.fleet_slot(p['workbook'])as fds:
            outcome=env[1].run('step',dict(descriptor=handoff['descriptor'],checkpoint=handoff['result']),
                env[7],Path(p['output'])/'first-step',models=True,commands=True,containers=True,guardian_lock_fds=fds)
        checkpoint,value=D.successful(outcome,env)
        return U.keep(Path(p['output'])/'result.json',dict(status=value['status'],workbook=p['workbook'],
            continuation=row,outcome=outcome,checkpoint=checkpoint,prepared_request_preserved=True,
            publication_performed=False,automatic_retry=False))


COMPLETED_REVIEW_FIELDS={'schema_version','helper','completed_review','previous','history','history_profile',
    'outcome','owner','fence','output','tracker','source','workbook','capacity_policy'}


def completed_review_handoff(old,history,B,T,I,W,D,env,outcome):
    """Authenticate the actual consumed pending handoff without selecting it again."""
    from dataclasses import asdict,replace
    p,value,w,s,previous,issued=B.validate(old['bridge_profile'],I,W,T,lambda r:D.successful(r,env),live=False)
    done=binding(Path(p['directory'])/'handoff.json');h=read(done);descriptor=binding(Path(p['directory'])/'descriptor.json')
    result=binding(Path(p['directory'])/'result.json')
    require(read(descriptor)==dict(read(p['descriptor']),origin=[*read(p['descriptor'])['origin'],old['bridge_profile'],p['completion']],
        initial=dict(selection=value['selection'],lease=I.base.encode(issued)))
        and read(result)==I._result(value['status'],descriptor,s,issued,checkpoint=value['checkpoint'],next_step=value['next_step'],
            evidence=[old['bridge_profile'],p['completion'],p['outcome'],p['result']])
        and h==dict(schema_version=B.SCHEMA,profile=old['bridge_profile'],result=result,descriptor=descriptor,
            original_result=p['result'],checkpoint=value['checkpoint'],selection=value['selection'],lease=asdict(issued),
            next_step=value['next_step'],completion=p['completion'],models_dispatched=0,semantic_budget_reset=False),
        'completed pending handoff changed')
    cp,observed=D.successful(outcome,env);raw=read(outcome);guard=read(raw['resource']);intent=read(guard['intent'])
    selected,controls,_=I.selected(descriptor);decoded,new,current=I._prior(cp,descriptor)
    require(raw['result']==cp and observed==decoded and controls==env[7]and selected==w
        and intent['arguments']==dict(descriptor=descriptor,checkpoint=result)and intent['action']=='step'
        and intent['code']==binding(I.__file__)and intent['controls']==env[0].encode(env[7])
        and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments'])
        and intent['models']is True and intent['commands']is True and intent['containers']is True
        and observed['status']=='STEP_COMPLETE'and observed['next_step']=='disclosure-commit'
        and observed['evidence'][0]==result and asdict(current)==asdict(issued),
        'completed review lost original owning guard or takeover lease')
    directory=Path(history['directory']);complete=read(observed['checkpoint'])
    request=W.request_saved(directory/'request.json');call=W.executor.inspect(request.prepared,w.campaign).call
    semantic=W.SemanticSelection(call,read(binding(directory/'manifest.json')),binding(directory/'instruction.snapshot.md'),None,None,())
    require(call is not None and new==replace(s,semantic=semantic)
        and complete['intent']==read(history['pending'])['intent']and complete['lease']==asdict(issued),
        'completed review changed more than its original semantic evidence')
    return cp,observed,w,new,current


def completed_review_prefix(row,p,U,D,env,first,job,*,invocation=None):
    """Every later original child must consume the actual preceding checkpoint."""
    ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
    current=first
    if not ops.exists():return current
    directories=sorted(x for x in ops.iterdir()if x.is_dir())
    require(len(directories)<=40 and [x.name for x in directories]==['%03d'%i for i in range(len(directories))],
        'closed contiguous original review tail required')
    for directory in directories:
        intent=binding(directory/'intent.json');value=read(intent)
        require(value['action']=='step'and value['arguments']['checkpoint']==current,
            'remaining child skipped its actual predecessor')
        if not(directory/'outcome.json').exists():
            require(invocation is not None and invocation[0]==intent and directory==directories[-1],
                'unsealed remaining child cannot be replayed')
            break
        outcome=binding(directory/'outcome.json');raw=read(outcome);guard=read(raw['resource'])
        require(guard['intent']==intent and guard['returncode']==0 and guard['failure']is None,
            'remaining child did not complete successfully')
        side=read(binding(directory/'disclosure-bootstrap.json'));kind,actual=U.retained_command(side['original_command'],p,D,env)
        require(kind=='instruction'and actual==intent and side==dict(schema_version='fleet-disclosure-command-continuation/v1',
            plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],
            seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592),
            'remaining child lost its original cold command')
        current,_=D.successful(outcome,env)
    return current


@contextmanager
def completed_review_context(row,*,invocation=None):
    p=read(row);require(set(p) in (COMPLETED_REVIEW_FIELDS,COMPLETED_REVIEW_FIELDS|{'bridge','bridge_profile'}) and p['schema_version']==SCHEMA
        and p['helper']==binding(__file__)and p['completed_review']is True,'closed completed prepared-review tail required')
    old=read(p['previous']);require(old['prepared_review']is True and old['workbook']==p['workbook']
        and old['source']==p['source']and p['fence']==old['fence']+1 and p['owner']!=old['owner']
        and p['output']!=old['output']and p['capacity_policy']==old['capacity_policy'],'completed review tail changed source or reused owner')
    O=module('completed_review_previous',old['helper']);M=module('completed_review_history',p['history'])
    h=read(p['history_profile']);require(h==dict(read(old['previous']),helper=p['history']),
        'completed history differs from actual prepared history')
    with M.context(p['history_profile'],completed=p['outcome'])as(history,_,loaded,w,s,lease,request):
        _,_,_,env,A,D,job,_,_,_,_,_=loaded;I,W=env[1],env[3]
        B=module('completed_review_original_bridge',old['bridge']);bp=read(old['bridge_profile']);tp=read(bp['takeover_plan'])
        T=module('completed_review_original_T',bp['takeover_module']);T.life=module('completed_review_original_lifecycle',tp['shared']['lifecycle'])
        U=module('completed_review_tail_utilities',old['utilities']);S=module('completed_review_tail_commands',old['shell'])
        with ExitStack()as stack:
            stack.enter_context(O.takeover_allowance(T,tp['shared']['allowance']))
            stack.enter_context(O.prepared_lifecycle_runtime(old,w,T))
            stack.enter_context(O.prepared_model_runtime(old,W.executor))
            first,_,w,s,l=completed_review_handoff(old,history,B,T,I,W,D,env,p['outcome'])
            successful=lambda bound:D.successful(bound,env)
            if 'bridge'in p:
                B=module('completed_review_current_bridge',p['bridge']);bp=read(p['bridge_profile'])
                require(bp['implementation']==p['bridge']and bp['outcome']==p['outcome']and bp['result']==first
                    and bp['tracker']==p['tracker']and bp['directory']==str(Path(p['output'])/'handoff'),
                    'review successor handoff detached from actual completed review')
                _,_,selected,s,_,l=B.validate(p['bridge_profile'],I,W,T,successful,live=False)
                complete=Path(bp['directory'])/'handoff.json';ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
                original_successful=D.successful
                successful=lambda bound:original_successful(bound,env)
                def successor():
                    path=ops/'000/outcome.json'
                    return binding(path)if path.exists()and read(binding(path))['status']=='COMPLETED_OPERATION'else None
                if complete.exists():
                    done=binding(complete)
                    stack.enter_context(B.prior_scope(done,p['bridge_profile'],I,W,T,successful,completed_successor=successor))
                    first,_=B.completed(done,I)
                    def completed(bound,observed=None):
                        if bound==done:return B.completed(done,I)
                        return original_successful(bound,observed)
                    D.successful=completed;stack.callback(setattr,D,'successful',original_successful)
                else:
                    require(invocation is None and not ops.exists(),'missing current takeover handoff')
                    B.validate(p['bridge_profile'],I,W,T,successful)
                    first=None
                stack.enter_context(B.declared_step_scope(W,B.lease_predicate(p['bridge_profile'],I,W,T,successful),wrapper=read(history['original']['plan'])['pending_reader']))
            if first is not None:
                current=completed_review_prefix(row,p,U,D,env,first,job,invocation=invocation)
                value=read(current);selected,_,_=I.selected(value['descriptor']);_,s,l=I._prior(current,value['descriptor'])
                require(selected.run==w.run and selected.root==w.root,'remaining worker source changed')
                W.active(selected,s,l)
            else:current=None
            raw=env[0]._run_in_process_group
            yield p,p,S,U,D,env,selected if 'bridge'in p else w,s,l,None,raw,job,B,T,successful,current


def run_completed_review(row):
    p=read(row)
    if 'bridge'in p:
        with context(row)as(p,derived,S,U,D,env,w,s,l,K,raw,job,B,T,successful,current):
            require(current is None,'remaining review continuation already started')
            U.custody(p,w.root)
            B.handoff(p['bridge_profile'],env[1],env[3],T,successful,U.keep)
    with context(row)as(p,derived,S,U,D,env,w,s,l,K,raw,job,B,T,successful,current):
        require(not(Path(p['output'])/'authoring-job.json').exists(),'remaining review continuation already started')
        U.custody(p,w.root);Path(p['output']).mkdir(exist_ok='bridge'in p)
        S.install_commands(row,p,U,D,env,raw,job)
        ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
        bound=U.keep(Path(p['output'])/'authoring-job.json',dict(job,outcome=binding(Path(p['output'])/'handoff/handoff.json')if 'bridge'in p else p['outcome'],operations=str(ops)))
        D.run(bound);held=read(binding(ops/'hold.json'));cp,value=D.successful(held['outcome'],env)
        return U.keep(Path(p['output'])/'result.json',dict(status=value['status'],workbook=p['workbook'],
            continuation=row,job=bound,outcome=held['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run','child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True)
    a.add_argument('--intent');a.add_argument('--intent-sha256');a.add_argument('--bootstrap');a.add_argument('--bootstrap-sha256');q=a.parse_args()
    row=dict(path=q.plan,sha256=q.sha256)
    if q.action=='run':print(json.dumps(run(row)))
    else:child(row,dict(path=q.intent,sha256=q.intent_sha256),dict(path=q.bootstrap,sha256=q.bootstrap_sha256))
