"""Read a completed DCC dialogue rejection for its remaining semantic round.

Historical configuration and module files remain bound to their original root.
Only the runtime root in the typed dispatcher environment selects the already
proved successor. The original DCC context stays alive until the caller closes
it; this reader has no authoring, model, custody or publication entry point.
"""
from pathlib import Path
import ast, hashlib, importlib.util, json, sys
from contextlib import ExitStack, contextmanager

FIELDS={'schema_version','helper','workbook','source','context','original_job','config',
        'original_snapshot','snapshot','outcome','result','descriptor','blocked'}
SCHEMA='fleet-retained-completed-disclosure/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in(p,*p.parents)),
            'canonical retained disclosure evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'retained disclosure evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'retained disclosure reader changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value;spec.loader.exec_module(value);return value

def validate_inventory(selected,env,p):
    q=read(selected['plan']);config=read(q['config']);old=Path(read(q['original_snapshot'])['root'])
    new=Path(read(q['snapshot'])['root']);C,I,P,W,R,actual,root,controls=env
    require(set(selected)=={'helper','plan','projection','config','snapshot','kind'}
        and selected['kind']=='completed_disclosure'
        and all(p[k]==q[k] for k in ('original_job','outcome','result','descriptor','blocked'))
        and selected['helper']==q['helper']==binding(__file__)
        and selected['config']==q['config'] and selected['snapshot']==q['snapshot']
        and selected['projection']==dict(environment=3,dispatcher=2,job=1)
        and q['workbook']==p['workbook'] and q['source']==p['source']
        and actual==config and config['code']==q['original_snapshot']
        and config['workbook']==q['workbook'] and root==new
        and all(Path(m.__file__).is_relative_to(old)for m in(C,I,P))
        and all(Path(m.__file__).is_relative_to(new)for m in(W,R))
        and C.SECONDS==3300 and C.MEMORY==8589934592,
        'retained disclosure original or selected inventory differs')
    return q

def history_runtime(p,env):
    profile=read(p['runtime_profile'])
    require(set(profile)=={'schema_version','runtime','entrypoint','read_call','patches','guard_modules','child_plans','journal_roots','dependencies','symbols'}
        and profile['schema_version']=='fleet-retained-runtime/v1'and profile['entrypoint']==p['helper']
        and profile['read_call']==dict(function='context',args=[],kwargs=dict(initial=False))
        and all(profile[k]==[]for k in('patches','guard_modules','child_plans','journal_roots','dependencies')),
        'only declared disclosure semantic history symbols permitted')
    expected=[('harbor_dialogue_semantic_retry','_history',Path(read(p['original_snapshot'])['root'])),
              ('harbor_instruction_repair','validate_historical_integrity',Path(read(p['snapshot'])['root']))]
    require(len(profile['symbols'])==len(expected),'exact two historical readers required')
    for row,(name,function,root)in zip(profile['symbols'],expected):
        require(set(row)=={'module_name','names','original','selected'}and row['module_name']=='scripts.batch.'+name
            and row['names']==[function]and row['original']==binding(root/'scripts/batch'/(name+'.py')),
            'foreign disclosure historical function')
        old=ast.parse(Path(row['original']['path']).read_text());require(binding(row['selected']['path'])==row['selected'],'selected historical reader changed')
        new=ast.parse(Path(row['selected']['path']).read_text())
        def others(tree):return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef)or n.name!=function],type_ignores=[]),include_attributes=False)
        require(others(old)==others(new)and sum(isinstance(n,ast.FunctionDef)and n.name==function for n in new.body)==1,
            'historical selection changed unrelated code')
        __import__(row['module_name'])
    runtime=module('disclosure_semantic_history_runtime',profile['runtime'])
    return runtime.installed(p['runtime_profile'])


def context(row,*,initial=False):
    require(initial is False,'completed disclosure is read-only history')
    p=read(row);require(set(p)-{'runtime_profile','semantic_successor'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),
                        'closed completed disclosure context required')
    c=p['context'];require(set(c)=={'helper','plan'},'closed original DCC reader required')
    original=read(c['plan'])
    if original.get('completed_review')is True:return completed_review_context(row,p,c,original)
    require(original['schema_version']=='fleet-disclosure-command-continuation/v1'
        and original['helper']==c['helper'] and original['workbook']==p['workbook']
        and original['source']==p['source'] and original['original_job']==p['original_job'],
        'completed disclosure original task differs')
    profile=read(original['profile']);require(profile['snapshot']==p['snapshot']
        and profile['old_snapshot']==p['original_snapshot'],'completed disclosure snapshots differ')
    M=module('retained_completed_disclosure',c['helper']);manager=ExitStack()
    loaded=manager.enter_context(M.context(c['plan'],continued=True))
    try:
        selected,U,D,env,worker,selection,lease,K,raw,job=loaded
        require(selected==original and job==read(p['original_job']) and job['config']==p['config']
            and job['driver']==binding(D.__file__) and job['publish']is False
            and all(job[k]is True for k in('models','commands','containers')),
            'completed disclosure original job changed')
        value,chosen,current=U.completed_failure(p,env[1],env[7])
        active,controls,_=env[1].selected(p['descriptor'])
        require(active.root==worker.root and active.run==worker.run and controls==env[7]
            and chosen.content.workbook==p['workbook']
            and chosen.content.bindings['original_raw_source']==p['source']
            and active.dialogue_semantic_retry is None,
            'completed disclosure rejection is not its unused first semantic round')
        current_env=(*env[:6],worker.root,env[7])
        selected_binding=dict(helper=p['helper'],plan=row,projection=dict(environment=3,dispatcher=2,job=1),
                              config=p['config'],snapshot=p['snapshot'],kind='completed_disclosure')
        validate_inventory(selected_binding,current_env,p)
        if 'runtime_profile'in p:manager.enter_context(history_runtime(p,env))
        original_selected=D.selected
        def dispatch(observed,result):
            require(observed==current_env,'retained disclosure current environment changed')
            return original_selected(env,result)
        D.selected=dispatch
        D._retained_disclosure_manager=manager
        D._retained_disclosure_original_env=env
        return p,job,D,current_env
    except BaseException:
        manager.__exit__(*sys.exc_info());raise


def completed_review_context(row,p,c,original):
    """Read an actual first dialogue rejection after a prepared-review tail."""
    require(original['schema_version']=='fleet-completed-instruction-continuation/v1'
        and original['helper']==c['helper']and original['workbook']==p['workbook']
        and original['source']==p['source']and p['snapshot']==p['original_snapshot'],
        'completed prepared-review context changed original source/root')
    M=module('retained_prepared_review_tail',c['helper']);manager=ExitStack()
    manager.enter_context(completed_rejection_scope(M,p,original))
    loaded=manager.enter_context(M.context(c['plan']))
    try:
        actual,derived,S,U,D,env,w,selection,lease,K,raw,job,B,T,successful,current=loaded
        require(actual==original and job==read(p['original_job'])and job['config']==p['config']
            and job['driver']==binding(D.__file__)and job['publish']is False
            and all(job[k]is True for k in('models','commands','containers')),
            'completed prepared-review original job changed')
        first,_=B.completed(binding(Path(original['output'])/'handoff/handoff.json'),env[1])
        last=M.completed_review_prefix(c['plan'],original,U,D,env,first,job)
        ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(original['output']).name
        directories=sorted(x for x in ops.iterdir()if x.is_dir())
        require(directories and p['outcome']==binding(directories[-1]/'outcome.json')
            and p['result']==current==last,'dialogue failure detached from completed tail')
        value,chosen,current_lease=U.completed_failure(p,env[1],env[7])
        active,controls,_=env[1].selected(p['descriptor'])
        require(active.root==w.root and active.run==w.run and controls==env[7]
            and chosen.content.workbook==p['workbook']
            and chosen.content.bindings['original_raw_source']==p['source']
            and active.dialogue_semantic_retry is None,
            'completed prepared review is not original first dialogue rejection')
        selected=dict(helper=p['helper'],plan=row,projection=dict(environment=3,dispatcher=2,job=1),
            config=p['config'],snapshot=p['snapshot'],kind='completed_disclosure')
        validate_inventory(selected,env,p)
        if 'runtime_profile'in p:manager.enter_context(history_runtime(p,env))
        D._retained_disclosure_manager=manager;D._retained_disclosure_original_env=env
        return p,job,D,env
    except BaseException:
        manager.__exit__(*sys.exc_info());raise


@contextmanager
def completed_rejection_scope(M,p,original):
    """Decode only the actual sealed rejection as history, never as resumable."""
    import inspect,textwrap
    prior=read(original['previous']);U=module('completed_rejection_validator',prior['utilities'])
    functions={name:getattr(M,name)for name in('completed_review_prefix','completed_review_context')}
    def completed(D,outcome,env):
        if outcome!=p['outcome']:return D.successful(outcome,env)
        value,_,_=U.completed_failure(p,env[1],env[7]);return p['result'],value
    def current(I,bound,descriptor,env):
        if bound!=p['result']:return I._prior(bound,descriptor)
        require(descriptor==p['descriptor'],'historical rejection descriptor changed')
        return U.completed_failure(p,I,env[7])
    expressions={
        'completed_review_prefix':('D.successful(outcome,env)','_completed_rejection(D,outcome,env)'),
        'completed_review_context':('I._prior(current,value["descriptor"])','_rejected_current(I,current,value["descriptor"],env)')}
    try:
        for name,(old,new)in expressions.items():
            function=functions[name];tree=ast.parse(textwrap.dedent(inspect.getsource(function)))
            expected=ast.dump(ast.parse(old,mode='eval').body,include_attributes=False);count=[0]
            class Change(ast.NodeTransformer):
                def visit_Call(self,node):
                    if ast.dump(node,include_attributes=False)==expected:
                        count[0]+=1;return ast.copy_location(ast.parse(new,mode='eval').body,node)
                    return self.generic_visit(node)
            tree=Change().visit(tree);require(count==[1],'original completed history expression changed')
            if name=='completed_review_context':
                target=ast.dump(ast.parse('W.active(selected,s,l)',mode='eval').body,include_attributes=False);hits=[0]
                class Active(ast.NodeTransformer):
                    def visit_Call(self,node):
                        if ast.dump(node,include_attributes=False)==target:
                            hits[0]+=1;return ast.copy_location(ast.parse('_historical_active(W,selected,s,l,env,D)',mode='eval').body,node)
                        return self.generic_visit(node)
                tree=Active().visit(tree);require(hits==[1],'original completed history custody expression changed')
            scope=dict(M.__dict__);scope.update(_completed_rejection=completed,_rejected_current=current,_historical_active=lambda W,w,s,l,env,D:semantic_current(p,env=env,D=D,W=W,worker=w,selection=s,lease=l))
            # The context must use the already scoped original prefix function.
            exec(compile(ast.fix_missing_locations(tree),function.__code__.co_filename,'exec'),scope)
            setattr(M,name,scope[name])
        yield
    finally:
        for name,value in functions.items():setattr(M,name,value)


def semantic_current(p,*,env,D,W,worker,selection,lease):
    """A later real semantic checkpoint owns current custody, never old history."""
    selected=p.get('semantic_successor')
    if selected is None:return W.active(worker,selection,lease)
    require(set(selected)=={'helper','output'}and Path(selected['output']).is_absolute(),
        'closed semantic successor namespace required')
    home=Path(selected['output']);permit_result=home/'permit/result.json'
    if not permit_result.exists():
        require(not(worker.run/'dialogue-semantic-retry-02/complete.json').exists(),
            'semantic completion lacks its original handoff')
        return W.active(worker,selection,lease)
    U=module('completed_review_semantic_successor',selected['helper']);outer=read(binding(home/'intent.json'));plan=outer['plan'];q=read(plan)
    require(outer==dict(plan=plan,helper=selected['helper'],automatic_retry=False)
        and q['helper']==selected['helper']and q['output']==str(home)
        and q['retained_context']['plan']==q['fresh_plan']and read(q['fresh_plan'])==p
        and all(q[k]==p[k]for k in('source','workbook','outcome','result','descriptor','blocked','original_job')),
        'semantic successor detached from exact original rejection')
    guard=read(binding(home/'permit-resource.json'))
    require(guard['intent']==binding(home/'intent.json')and guard['returncode']==0 and guard['failure']is None
        and guard['log']==binding(home/'permit.log')and guard['models_dispatched']==0
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592 and guard['automatic_retry']is False
        and guard['command']==[sys.executable,'-I','-B',selected['helper']['path'],'permit-child','--plan',plan['path'],'--sha256',plan['sha256']],
        'semantic authorization did not complete its original guard')
    I=env[1];current=binding(permit_result);value=read(current)
    actual,controls,_=I.selected(value['descriptor']);I._prior(current,value['descriptor'])
    require(value['status']=='RETRY_READY'and value['next_step']=='dialogue-writer'
        and value['descriptor']==binding(home/'permit/descriptor.json')and q['result']in value['evidence']
        and plan in value['evidence']and actual.run==worker.run and actual.root==worker.root
        and controls==env[7]and actual.dialogue_semantic_retry==binding(worker.run/'dialogue-semantic-retry-02/complete.json'),
        'semantic handoff changed original worker or consumed round')
    semantic=read(read(actual.dialogue_semantic_retry)['permit'])
    require(semantic['blocked']==p['blocked']and semantic['round']==2 and semantic['consumed_rounds']==1
        and semantic['semantic_limit']==2 and semantic['semantic_budget_reset']is False,
        'semantic successor detached from original first rejected review')
    ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
    directories=sorted(x for x in ops.iterdir()if x.is_dir())if ops.exists()else []
    require(len(directories)<=40 and [x.name for x in directories]==['%03d'%i for i in range(len(directories))],
        'semantic successor tail is not contiguous')
    terminal=None
    for directory in [home/'first-step',*directories]:
        if not directory.exists():
            require(directory==home/'first-step'and not directories,'missing first semantic child');break
        intent=binding(directory/'intent.json');iv=read(intent);side=read(binding(directory/'semantic-bootstrap.json'))
        kind,bound=U.retained_command(side['original_command'],q,D,env)
        require(kind=='instruction'and bound==intent and iv['action']=='step'
            and iv['arguments']['checkpoint']==current and iv['arguments']['descriptor']==value['descriptor']
            and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=plan,helper=q['helper'],kind=kind,intent=intent,
                original_command=side['original_command'],seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592,automatic_retry=False),
            'semantic child changed predecessor or original guard')
        outcome=directory/'outcome.json'
        if not outcome.exists():
            require('--intent'in sys.argv and sys.argv[sys.argv.index('--intent')+1]==intent['path']
                and '--intent-sha256'in sys.argv and sys.argv[sys.argv.index('--intent-sha256')+1]==intent['sha256']
                and (not directories or directory==directories[-1]),'unsealed semantic successor is not current child')
            break
        outcome_binding=binding(outcome);outcome_value=read(outcome_binding)
        if outcome_value.get('status')=='COMPLETED_OPERATION'and read(outcome_value['result']).get('status')=='DIALOGUE_REPAIR_REQUIRED':
            current,value,current_selection,current_lease=semantic_terminal(U,I,env[7],directory,directories,value,outcome_binding)
            terminal=(current_selection,current_lease)
        else:current,value=D.successful(outcome_binding,env)
    value=read(current);actual,_,_=I.selected(value['descriptor'])
    if terminal is None:_,current_selection,current_lease=I._prior(current,value['descriptor'])
    else:current_selection,current_lease=terminal
    if value['status']=='PUBLICATION_READY':
        env[2]._checkpoint(D.publication_request(env,value))
        before=(None,read(binding(actual.context.tracker_root/(p['workbook']+'.json'))))
    else:before=W.active(actual,current_selection,current_lease)
    from scripts.batch import harbor_dialogue_semantic_retry as S
    S.validate(actual,before[1]);return before



def semantic_terminal(U,I,controls,directory,directories,previous,outcome):
    """Read the actual second rejection only after its two completed authors."""
    require(len(directories)==2 and directory==directories[-1]and directory.name=='001'
        and previous['status']=='STEP_COMPLETE'and previous['next_step']=='dialogue-review',
        'rejected semantic review is not the exact final second-round child')
    result=read(outcome)['result'];value=read(result)
    require(result==binding(directory/'result.json')and value['descriptor']==previous['descriptor'],
        'second rejected review detached from actual child descriptor')
    selected=dict(outcome=outcome,result=result,descriptor=value['descriptor'],blocked=value['checkpoint'])
    checked,selection,lease=U.completed_failure(selected,I,controls)
    require(checked==value,'original rejected review decoder changed result')
    return result,value,selection,lease
