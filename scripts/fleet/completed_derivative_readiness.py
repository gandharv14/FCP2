"""Read a reconciled pure readiness failure after its original enqueue has completed.

The failed checkpoint is historical. The real enqueue guard supplies its next
checkpoint; original publication checks retain authority over the live tracker.
"""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import hashlib

def lease_value(value):
    keys={'owner','token','revision','sha256'}
    if set(value)!={'type','value'}or value['type']!='Lease'or set(value['value'])!=keys:
        raise ValueError('closed original lease encoding required')
    if any(set(x)!={'type','value'}or x['type']!='scalar'for x in value['value'].values()):
        raise ValueError('original scalar lease fields required')
    result={k:v['value']for k,v in value['value'].items()}
    if type(result['revision'])is not int or result['revision']<0 or any(type(result[k])is not str for k in keys-{'revision'}):
        raise ValueError('original lease field types required')
    return result


@contextmanager
def installed(M,p,ready):
    c=p.get('continuation');require=M.require;read=M.read;binding=M.binding
    if c is None:
        require('continuation'not in p and p['schema_version']==M.SCHEMA,
            'closed completed direct derivative required')
        def bind(D,env):
            completed_phases(M,p,ready,D,env)
            selected_aggregate_root(M,p,ready,D,env)
        yield bind
        return
    if 'completed_outcomes'not in c:
        require(set(c)in ({'plan','failed_outcome','output'},{'plan','failed_outcome','output','previous_plan'}) and p['schema_version']==M.SCHEMA,
            'closed completed three-phase derivative required')
        def bind(D,env):
            completed_phases(M,p,ready,D,env)
            selected_aggregate_root(M,p,ready,D,env)
        yield bind
        return
    require('failed_readiness'in c and p['schema_version']==M.SCHEMA,
            'exact completed prose-readiness continuation required')
    outcome=read(ready['outcome']);resource=read(outcome['resource']);intent=read(resource['intent'])
    result=read(outcome['result']);complete=read(result['checkpoint']);task_intent=read(complete['intent'])
    previous_outcome=read(c['completed_outcomes'][-1]);previous=read(previous_outcome['result'])
    failed_resource=read(read(c['failed_outcome'])['resource']);failed_intent=read(failed_resource['intent'])
    require(ready['outcome']==binding(Path(p['operations'])/'000/outcome.json')
        and resource['intent']==binding(Path(p['operations'])/'000/intent.json')
        and outcome['status']=='COMPLETED_OPERATION'and outcome['result']==ready['checkpoint']
        and resource['returncode']==0 and resource['failure']is None
        and resource['log']==binding(Path(p['operations'])/'000/step.log')
        and result['status']=='PUBLICATION_READY'and result['next_step']is None
        and result['publication_performed']is False and result['selection']==previous['selection']
        and result['descriptor']==previous['descriptor']
        and intent['arguments']==dict(descriptor=previous['descriptor'],checkpoint=previous_outcome['result']),
        'actual original enqueue guard/checkpoint required')
    require(all(intent[k]==failed_intent[k]for k in('action','code','base_code','controls','models','commands','containers','memory_limit_bytes','timeout_seconds'))
        and intent['action']=='step'and intent['models']is False
        and intent['commands']is True and intent['containers']is True,
        'completed enqueue changed original guard or capabilities')
    require(result['checkpoint']==binding(Path(c['output'])/'steps/enqueue/complete.json')
        and complete['intent']==binding(Path(c['output'])/'steps/enqueue/intent.json')
        and complete['step']=='enqueue'and complete['status']=='completed'
        and complete['selection']==result['selection']and complete['lease']==lease_value(result['lease'])
        and task_intent['step']=='enqueue'and task_intent['previous']==previous['checkpoint']
        and task_intent['selection']==previous['selection']and task_intent['lease']==lease_value(previous['lease']),
        'enqueue completion lost original committed predecessor')
    before=lease_value(previous['lease']);after=lease_value(result['lease'])
    require(after['revision']==before['revision']+1 and all(after[k]==before[k]for k in('owner','token')),
        'enqueue changed owner or skipped tracker revisions')
    original=M.stopped_readiness;namespace=M.namespace;captured=[]
    def capture(original_module):
        result=namespace(original_module);captured.append((original_module,result));return result
    def bind_dispatcher(D,env):
        originals=[o for o,n in captured if n is D]
        require(len(originals)==1,'completed derivative dispatcher namespace is not original')
        original_D=originals[0];original_selected=original_D.selected
        require(D.selected is original_selected,'derivative selected already has an undeclared adapter')
        original_env=tuple(next((o for o,n in captured if n is item),item)for item in env)
        require(original_env[0]is env[0]and original_env[2]is env[2]and original_env[5:]==env[5:]
            and all(any(o is original_env[i]and n is env[i]for o,n in captured)for i in(1,3,4)),
            'derivative context changed more than declared namespaces')
        def selected(observed,result):
            require(observed==env,'publication derivative environment differs')
            worker,recipe=original_selected(original_env,result)
            require(worker.root==env[6]and worker.run==Path(env[5]['run_root'])/'instruction',
                'original publication worker differs')
            return worker,recipe
        M.assign(D,selected=selected)
    M.namespace=capture
    def historical(observed,task,error,worker,selection,lease,W,root,*,ready=None):
        require(observed==c and root==worker.root and W.codec.encode(selection)==previous['selection']
            and asdict(lease)==before and task_intent['code']==W.code(worker)
            and task_intent['initial']==binding(worker.run/'initial.json'),
            'historical readiness belongs to another worker or source')
        require(observed['failed_readiness']==binding(task/'readiness.json')
            and {x.name for x in task.iterdir()}=={'intent.json','exception.json','readiness.json'},
            'historical failure changed or has unknown effects')
        failed=read(observed['failed_readiness'])
        prose=(error==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'full current readiness did not qualify').hexdigest(),automatic_replay=False)
            and failed['publication_ready']is False and failed['blockers']==['prose_mechanics']
            and failed['checks']['prose_mechanics']==dict(checked=False,error_type='AdmissionError'))
        facade=('ready_root'in p and ready is not None
            and error==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'complete current readiness required').hexdigest(),automatic_replay=False)
            and failed['publication_ready']is True and failed['blockers']==[]
            and W.HarborLaneLifecycle.transition is ready.ReadyTransitions.transition)
        require(prose or facade,'historical failed gate differs')
        current=W.complete_readiness(selection,root=root)
        require(current['publication_ready']is True and current['blockers']==[],
            'current full task readiness still fails')
    M.stopped_readiness=historical
    try:yield bind_dispatcher
    finally:
        M.stopped_readiness=original;M.namespace=namespace


def completed_phases(M,p,ready,D,env):
    """Authenticate all original guarded phases after the retained adoption."""
    require=M.require;read=M.read;binding=M.binding
    _,checkpoint,value=M.authorization(p['continuation']['plan']if 'continuation'in p else ready['plan'],env)
    I=env[1];base=env[0];runner=M.module('_completed_derivative_guard',p['runner']);home=Path(p['operations'])
    expected=[binding(home/('%03d'%i)/'outcome.json')for i in range(3)]
    require(ready['operations']==expected and ready['outcome']==expected[-1],
        'full derivative READY must end its three original phases')
    for ordinal,phase in enumerate(('final-runtime','dialogue-commit','enqueue')):
        directory=home/('%03d'%ordinal);outcome=read(expected[ordinal]);resource=read(outcome['resource']);intent=read(resource['intent'])
        side=read(binding(directory/'declared-runtime-adapter.json'))
        require(value['next_step']==phase and resource['intent']==binding(directory/'intent.json')
            and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint)
            and intent['action']=='step' and intent['models']is False and intent['commands']is True and intent['containers']is True,
            'original derivative phase lost its exact predecessor')
        seconds=runner.contract(I,base,'instruction',resource['intent'],side['original_command'])
        expected_side=dict(schema_version=runner.SCHEMA,adapter=p['runner'],producer_code=binding(I.__file__),base_code=binding(base.__file__),kind='instruction',reader_plan=ready['plan'],child_wrapper=p['helper'],reader_evidence=M.dependencies(p),intent=resource['intent'],original_command=side['original_command'],selected_command=[side['original_command'][0],'-I','-B',p['helper']['path'],'owning-child','--plan',ready['plan']['path'],'--plan-sha256',ready['plan']['sha256'],'--kind','instruction','--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],timeout_seconds=seconds,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False)
        require(side==expected_side,'completed derivative guard reader changed')
        checkpoint,value=D.successful(expected[ordinal])
        if ordinal<2:
            require(value['status']=='STEP_COMPLETE' and value['next_step']==('dialogue-commit','enqueue')[ordinal],
                'completed derivative stages changed order')
            I._prior(checkpoint,value['descriptor'])
    require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY'
        and value['publication_performed']is False,'original full derivative enqueue did not qualify')


def selected_aggregate_root(M,p,ready,D,env):
    """Project only the retained aggregate dispatcher's authenticated code root."""
    import ast,inspect,textwrap,types
    require=M.require
    outer=D.selected
    require(outer.__name__=='selected' and M.binding(outer.__code__.co_filename)==p['helper'],
        'original derivative selected function required')
    values=inspect.getclosurevars(outer).nonlocals
    require(values.get('chosen')==env and 'original_selected'in values,
        'original derivative selected closure differs')
    aggregate=values['original_selected']
    if aggregate.__name__!='dispatch':return
    bindings=M.dependencies(p)
    require(M.binding(aggregate.__code__.co_filename)in bindings or
        aggregate_history_binding(M,p)==M.binding(aggregate.__code__.co_filename),
        'aggregate selected function is outside authenticated history')
    expected="""def dispatch(observed,result):
        require(observed==current_env,'aggregate current environment changed');return original_selected(env,result)
    """
    disclosure="""def dispatch(observed,result):
        require(observed==current_env,'retained disclosure current environment changed')
        return original_selected(env,result)
    """
    require(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(aggregate))))in
        (ast.dump(ast.parse(expected)),ast.dump(ast.parse(disclosure))),
        'aggregate selected function changed')
    captured=inspect.getclosurevars(aggregate).nonlocals
    historical=captured['env'];current=captured['current_env']
    require(values['env']==current and len(historical)==len(current)==8
        and historical[:6]==current[:6] and historical[7]==current[7]
        and current[6]==env[6], 'aggregate context changed more than selected root')
    worker,controls,_=env[1].selected(M.read(ready['checkpoint'])['descriptor'])
    require(worker.root==env[6] and controls==env[7]
        and worker.run==Path(env[5]['run_root'])/'instruction',
        'completed aggregate descriptor changed root or controls')
    if ast.dump(ast.parse(textwrap.dedent(inspect.getsource(aggregate))))==ast.dump(ast.parse(disclosure)):
        # The retained DCC dispatcher already owns the old-to-current root
        # adapter. Preserve its required historical environment unchanged.
        return
    def clone(function,replacements):
        cells=[]
        for name,cell in zip(function.__code__.co_freevars,function.__closure__ or ()):
            cells.append((lambda value:lambda:value)(replacements[name]).__closure__[0] if name in replacements else cell)
        result=types.FunctionType(function.__code__,function.__globals__,function.__name__,function.__defaults__,tuple(cells))
        result.__kwdefaults__=function.__kwdefaults__;return result
    projected=clone(aggregate,{'env':current})
    M.assign(D,selected=clone(outer,{'original_selected':projected}))


def aggregate_history_binding(M,p):
    M.require(all(k in p for k in ('review_plan','source','workbook')),'aggregate source history required')
    current=M.read(p['review_plan'])
    route={
      'fleet-dialogue-source-revision-review/v1':'semantic_context',
      'fleet-retained-completed-review-tail/v1':'context',
      'fleet-ordinary-mechanical-dialogue/v1':'semantic_context',
      'fleet-completed-instruction-continuation/v1':'semantic_context',
      'fleet-instruction-semantic-continuation/v1':'retained_context'}
    for _ in range(6):
        M.require(current['workbook']==p['workbook'] and current['source']==p['source'],
            'aggregate publication history source changed')
        if current['schema_version']in('fleet-retained-completed-aggregate/v1','fleet-retained-completed-disclosure/v1'):return current['helper']
        M.require(current['schema_version']in route,'undeclared aggregate publication history route')
        selected=current[route[current['schema_version']]]
        current=M.read(selected['plan'])
        if 'helper'in selected:M.require(current['helper']==selected['helper'],'aggregate history reader differs')
    raise ValueError('aggregate publication history exceeds declared route')
