"""Continue one stopped, unprepared enqueue without replaying prior task work.

The original author/review/runtime/commit and interrupted journals are retained.
A new physical enqueue namespace is allowed only while the tracker is exactly
its completed commit and no prepared notes transaction exists.
"""
from contextlib import ExitStack,contextmanager
from dataclasses import asdict
from pathlib import Path
import ast,os,sys


@contextmanager
def routed_step_scope(U,fresh,W,K,allowed,directory):
    """Compose the exact retained command projection with completed custody."""
    declared=fresh['context'];route=U.module('completed_commit_command_route',declared['helper'])
    plan=U.read(declared['plan']);selected=plan['command_routing']['worker']
    U.require(U.binding(selected['path'])==selected and W._step.__globals__ is W.__dict__
        and W._step.__code__.co_filename==selected['path'],'completed commit command route detached')
    text=Path(selected['path']).read_text()
    expected=route.routed_source(Path(W.__file__).read_text(),'worker')
    U.require(ast.dump(ast.parse(text))==ast.dump(ast.parse(expected)), 'completed commit command route changed')
    nodes=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='_step']
    U.require(len(nodes)==1,'one declared command step required')
    original=W._step
    W._step=K.lease_predicate_function(original,ast.unparse(nodes[0]),allowed,directory=directory)
    try:yield
    finally:W._step=original


def stopped(U,p,old,env,checkpoint,value,worker,*,live=True):
    C,I,_,W,_,config,root,controls=env
    commit='interrupted_commit'in p
    phase='dialogue-commit'if commit else'enqueue'
    proof=p['interrupted_commit'if commit else'interrupted_enqueue']
    job=U.read(proof['worker_job']);outer=U.read(proof['worker_outcome']);process=U.read(proof['process']);wi=U.read(outer['intent'])
    U.require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['failure_type']==('RuntimeError'if commit else'TimeoutError')and outer['returncode']is None and outer['result']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and outer['metrics']['heartbeat_count']>0 and outer['metrics']['elapsed_seconds']>0,
        'exact stopped heartbeat failure required')
    U.require(job['driver']==old['helper']and job['inputs'][0]==p['semantic_context']['plan']
        and job['workbook']==p['workbook']and job['source']==p['source']and job['result']==str(Path(old['output'])/'result.json')
        and proof['worker_outcome']==U.binding(Path(job['run'])/'outcome.json')
        and outer['intent']==U.binding(Path(job['run'])/'intent.json')and wi['job']==proof['worker_job']
        and wi['owner']==old['owner']and process['job']==proof['worker_job'],
        'heartbeat stop belongs to another task')
    U.require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(),
        'original worker may still be alive')
    for d in Path('/proc').glob('[0-9]*/cmdline'):
        if int(d.parent.name)==os.getpid():continue
        try:argv=d.read_bytes().split(b'\0')
        except (FileNotFoundError,PermissionError):continue
        U.require(not any(x.encode()in argv for x in (proof['worker_job']['path'],old['helper']['path'])),
            'original enqueue child may still be alive')
    directory=Path(proof['failed_intent']['path']).parent
    U.require(directory.parent==Path(config['run_root'])/'dispatcher-v2'/Path(old['output']).name
        and directory.name==('003'if commit else'004')and set(q.name for q in directory.iterdir())=={'intent.json','semantic-bootstrap.json','dependency.json','step.log'}
        and proof['failed_intent']==U.binding(directory/'intent.json')and (directory/'step.log').read_bytes()==b'',
        'interrupted guard has unknown effects')
    intent=U.read(proof['failed_intent']);U.require(intent['action']=='step'and intent['schema_version']==I.SCHEMA
        and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
        and intent['code']==U.binding(I.__file__)and intent['base_code']==U.binding(C.__file__)
        and intent['controls']==C.encode(controls)and intent['memory_limit_bytes']==8589934592
        and all(intent[k]is True for k in('models','commands','containers'))
        and U.retained_seconds('instruction',proof['failed_intent'],env)==3300,'original enqueue guard changed')
    side=U.read(U.binding(directory/'semantic-bootstrap.json'))
    command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',proof['failed_intent']['path'],proof['failed_intent']['sha256']]
    U.require(side==dict(schema_version='fleet-retained-semantic-child/v1',plan=p['semantic_context']['plan'],helper=old['helper'],kind='instruction',intent=proof['failed_intent'],original_command=command,seconds=3300,memory_bytes=8589934592,automatic_retry=False),'interrupted bootstrap changed')
    step=W.step_directory(worker,phase)
    U.require(set(q.name for q in step.iterdir())==({'intent.json'}if commit else{'intent.json','readiness.json'})
        and proof['task_intent']==U.binding(step/'intent.json')and (commit or proof['readiness']==U.binding(step/'readiness.json')),
        'unprepared enqueue has extra effects')
    original=U.read(proof['task_intent']);lease=C.decode(value['lease'])
    U.require(original==dict(code=W.code(worker),initial=U.binding(worker.run/'initial.json'),lease=asdict(lease),previous=value['checkpoint'],selection=value['selection'],step=phase),
        'interrupted task intent changed')
    if not commit:
        report=U.read(proof['readiness']);U.require(report['publication_ready']is True and report['blockers']==[]and report['mutation_performed']is False,'original enqueue did not qualify')
    if not live:return step
    tracker=worker.context.tracker_root/(p['workbook']+'.json');before=U.read(U.binding(tracker))
    U.require(before['revision']==lease.revision and U.binding(tracker)['sha256']==lease.sha256,
        'interrupted enqueue tracker changed; reconcile actual transaction')
    journal=tracker.parent/(tracker.stem+'-notes-transactions')
    U.require(all(int(q.name.split('-')[0])<=lease.revision for q in (journal.iterdir()if journal.exists()else [])),
        'prepared or partially prepared enqueue exists; use original transaction recovery')
    return step


def context(U,row,*,continued=False):
    p=U.read(row);commit='interrupted_commit'in p;key='interrupted_commit'if commit else'interrupted_enqueue'
    phase='dialogue-commit'if commit else'enqueue';extra={'semantic_context',key}
    takeover='checkpoint_takeover'in p
    U.require((not takeover or commit)and set(p)-{'checkpoint_takeover'}==U.TAIL_FIELDS|extra|(set()if commit else{'checkpoint_lease'}) and p['schema_version']==U.TAIL_SCHEMA and p['helper']==U.binding(U.__file__),
        'closed interrupted enqueue continuation required')
    proof=p[key];chosen=p['semantic_context'];old=U.read(chosen['plan'])
    U.require(set(proof)=={'reader','worker_job','worker_outcome','process','failed_intent','task_intent'}|(set()if commit else{'readiness'})
        and proof['reader']==U.binding(__file__)and set(chosen)=={'helper','plan'}
        and old['schema_version']==U.SCHEMA and old['helper']==chosen['helper']
        and old.get('retained_context',{}).get('kind')=='completed_disclosure'
        and p['fence']==old['fence']+1 and p['output']!=old['output']
        and all(p[k]==old[k]for k in('fresh_plan','original_job','source','workbook','authorization'))
        and p['authorization']['sha256']==U.AUTHORIZATION_SHA,'original completed semantic authority differs')
    M=U.module('interrupted_enqueue_parent',chosen['helper']);_,fresh,job,D,env,_,_,_,_,S,L=M.context(chosen['plan'],continued=True)
    stack=ExitStack();stack.callback(M.retained_manager(old,D,L).__exit__,None,None,None)
    try:
        C,I,_,W,_,config,root,controls=env;checkpoint,value=D.successful(p['outcome'])
        U.require(value['status']=='STEP_COMPLETE'and value['next_step']==p['next_step']==phase and value['publication_performed']is False,
            'original completed dialogue commit required')
        worker,seen,_=I.selected(value['descriptor']);selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
        U.require(worker.root==root and worker.run==Path(config['run_root'])/'instruction'and seen==controls
            and selection.content.workbook==p['workbook']and selection.content.bindings['original_raw_source']==p['source'],
            'completed enqueue source differs')
        output=Path(p['output']);U.require(output.is_absolute()and not output.is_relative_to(root)
            and p['operations']==str(Path(config['run_root'])/'dispatcher-v2'/output.name),'isolated enqueue continuation required')
        stopped(U,p,old,env,checkpoint,value,worker,live=not continued and not takeover)
        if takeover:
            chosen=p['checkpoint_takeover'];U.require(set(chosen)=={'helper','profile'},'closed completed commit takeover required')
            K=U.module('interrupted_commit_takeover',chosen['helper']);profile=U.read(chosen['profile'])
            U.require(profile['outcome']==p['outcome']and profile['result']==checkpoint and profile['descriptor']==value['descriptor']
                and profile['tracker']==p['tracker']and profile['source']==p['source']and profile['workbook']==p['workbook']
                and profile['directory']==str(output/'checkpoint-takeover'),'commit takeover detached from actual completed runtime')
            T=sys.modules['scripts.batch.harbor_lease_takeover']
            _,_,_,_,_,current=K.validate(chosen['profile'],I,W,T,D.successful,live=not continued)
            if not continued:
                journal=worker.context.tracker_root/(p['workbook']+'-notes-transactions')
                U.require(all(int(q.name.split('-')[0])<=lease.revision for q in(journal.iterdir()if journal.exists()else[])),
                    'interrupted commit has a prepared transaction')
            lease=current
        if continued:U.require(U.read(U.binding(output/'intent.json'))==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),'enqueue continuation intent differs')
        else:
            I._prior(checkpoint,value['descriptor'])
            U.require(U.binding(p['tracker']['path'])==p['tracker']and not output.exists(),'current enqueue custody changed')
            _,before,_=W.active(worker,selection,lease);S.validate(worker,before)
            if not commit:U.require(W.complete_readiness(selection,root=root)['publication_ready']is True,'current full enqueue readiness failed')
            D.operation_namespace(config,p['operations'])
        old_directory=W.step_directory
        def directory(current,selected_phase):
            if current==worker and selected_phase==phase:return output/(phase+'-step')
            return old_directory(current,selected_phase)
        U.require(W._step.__globals__ is W.__dict__,'original enqueue worker global differs')
        W.step_directory=directory;stack.callback(setattr,W,'step_directory',old_directory)
        if takeover:
            stack.enter_context(routed_step_scope(U,fresh,W,K,K.lease_predicate(chosen['profile'],I,W,T,D.successful),directory))
            if continued:
                handoff=U.binding(output/'checkpoint-takeover/handoff.json')
                successor=output/'first-step/outcome.json'
                completed=U.binding(successor)if successor.exists()and U.read(U.binding(successor))['status']=='COMPLETED_OPERATION'else None
                stack.enter_context(K.prior_scope(handoff,chosen['profile'],I,W,T,D.successful,completed_successor=completed))
                checkpoint,value=K.completed(handoff,I)
        if not commit:checkpoint,value=U.checkpoint_lease_context(row,p,env,stack,checkpoint,value,continued=continued)
        D._semantic_tail_manager=stack
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:stack.__exit__(*sys.exc_info());raise


def handoff(U,p,env,D):
    selected=p['checkpoint_takeover'];K=U.module('interrupted_commit_takeover_owning',selected['helper'])
    I=env[1];W=env[3];T=sys.modules['scripts.batch.harbor_lease_takeover']
    row=K.handoff(selected['profile'],I,W,T,D.successful,U.keep)
    D._semantic_tail_manager.enter_context(K.prior_scope(row,selected['profile'],I,W,T,D.successful))
    return K.completed(row,I)


def completed_context(U,row,*,continued=False):
    """A sealed readiness-root failure after the already completed lease renewal."""
    p=U.read(row);q=p['completed_enqueue'];extra={'semantic_context','completed_enqueue'}
    U.require(set(p)==U.TAIL_FIELDS|extra and p['schema_version']==U.TAIL_SCHEMA and p['helper']==U.binding(U.__file__)
        and set(q)=={'reader','previous','guard','process','outer','worker_job','intent','readiness','exception','root_reader'}
        and q['reader']==U.binding(__file__)and set(q['previous'])=={'helper','plan'},'closed completed enqueue recovery required')
    old=U.read(q['previous']['plan']);M=U.module('completed_enqueue_predecessor',q['previous']['helper'])
    U.require(old['helper']==q['previous']['helper']and old['schema_version']==U.TAIL_SCHEMA
        and 'interrupted_enqueue'in old and 'checkpoint_lease'in old
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output']
        and all(p[k]==old[k]for k in('semantic_context','fresh_plan','original_job','source','workbook','authorization','outcome','next_step')),
        'completed renewal or original semantic authority changed')
    _,fresh,job,D,env,checkpoint,value=M.tail_context(q['previous']['plan'],continued=True)
    stack=ExitStack();stack.callback(D._semantic_tail_manager.__exit__,None,None,None)
    try:
        C,I,_,W,_,config,root,controls=env
        worker,seen,_=I.selected(value['descriptor']);_,selection,lease=I._prior(checkpoint,value['descriptor'])
        U.require(value['status']=='STEP_COMPLETE'and value['next_step']=='enqueue'and worker.root==root and seen==controls,
            'original renewed commit required')
        home=Path(old['output']);guard=U.read(q['guard']);resource=U.read(guard['resource']);intent=U.read(resource['intent'])
        U.require(q['guard']==U.binding(home/'first-step/outcome.json')and guard['status']=='RECONCILIATION_REQUIRED'
            and guard['resource']==U.binding(home/'first-step/resource.json')and resource['returncode']==1 and resource['failure']is None
            and resource['intent']==U.binding(home/'first-step/intent.json')and intent['action']=='step'
            and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
            and intent['code']==U.binding(I.__file__)and intent['base_code']==U.binding(C.__file__)
            and intent['controls']==C.encode(controls)and intent['memory_limit_bytes']==8589934592
            and all(intent[k]is True for k in ('models','commands','containers'))
            and U.retained_seconds('instruction',resource['intent'],env)==3300,'sealed original enqueue guard differs')
        task=home/'enqueue-step'
        U.require({x.name for x in task.iterdir()}=={'intent.json','readiness.json','exception.json'}
            and all(q[k]==U.binding(task/(k+'.json'))for k in('intent','readiness','exception')),
            'enqueue has unknown owning effects')
        attempted=U.read(q['intent']);ready=U.read(q['readiness']);error=U.read(q['exception'])
        U.require(attempted==dict(code=W.code(worker),initial=U.binding(worker.run/'initial.json'),lease=asdict(lease),previous=value['checkpoint'],selection=value['selection'],step='enqueue')
            and ready['publication_ready']is True and ready['blockers']==[]and ready['mutation_performed']is False
            and error==dict(type='AdmissionError',message_sha256=__import__('hashlib').sha256(b'complete current readiness required').hexdigest(),automatic_replay=False),
            'only exact post-worker readiness failure is supported')
        outer=U.read(q['outer']);process=U.read(q['process']);oldjob=U.read(q['worker_job'])
        U.require(oldjob['driver']==old['helper']and oldjob['inputs'][0]==q['previous']['plan']and oldjob['source']==p['source']
            and oldjob['result']==str(home/'result.json')and q['outer']==U.binding(Path(oldjob['run'])/'outcome.json')
            and outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
            and outer['result']is None and outer['automatic_retry']is False
            and process['job']==q['worker_job']and type(process['pid'])is int and process['pid']>1
            and not Path('/proc',str(process['pid'])).exists(),'original enqueue worker is not exactly stopped')
        for proc in Path('/proc').glob('[0-9]*/cmdline'):
            if int(proc.parent.name)==os.getpid():continue
            try:argv=proc.read_bytes().split(b'\0')
            except (FileNotFoundError,PermissionError):continue
            U.require(not any(x.encode()in argv for x in(q['worker_job']['path'],old['helper']['path'])),
                'previous enqueue child may remain alive')
        output=Path(p['output']);U.require(output.is_absolute()and not output.is_relative_to(root)
            and p['operations']==str(Path(config['run_root'])/'dispatcher-v2'/output.name),'isolated corrected enqueue required')
        U.require(set(q['root_reader'])=={'helper','lifecycle'},'closed readiness facade runtime required')
        root_reader=U.module('authenticated_ready_root',q['root_reader']['helper']);lifecycle=U.module('authenticated_lifecycle_root',q['root_reader']['lifecycle'])
        R=stack.enter_context(root_reader.installed(W,root,lifecycle))
        if continued:
            U.require(U.read(U.binding(output/'intent.json'))==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),'corrected enqueue intent differs')
        else:
            U.require(not output.exists()and U.binding(p['tracker']['path'])==p['tracker'],'corrected enqueue tracker changed')
            _,before,tracker=W.active(worker,selection,lease)
            journal=Path(tracker['path']).parent/(p['workbook']+'-notes-transactions')
            U.require(all(int(x.name.split('-')[0])<=lease.revision for x in(journal.iterdir()if journal.exists()else[])),
                'prepared enqueue requires original transaction recovery')
            R._proof(before,selection)
            D.operation_namespace(config,p['operations'])
        old_directory=W.step_directory;scope=W._step.__globals__
        U.require(scope is not W.__dict__ and scope['step_directory']is old_directory,
            'completed renewal step projection differs')
        def directory(current,phase):
            if current==worker and phase=='enqueue':return output/'enqueue-step'
            return old_directory(current,phase)
        W.step_directory=directory;scope['step_directory']=directory
        stack.callback(setattr,W,'step_directory',old_directory);stack.callback(scope.__setitem__,'step_directory',old_directory)
        D._semantic_tail_manager=stack
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:stack.__exit__(*sys.exc_info());raise
