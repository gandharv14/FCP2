"""Compose an actual mechanical authorization with a separate completed T."""
from contextlib import contextmanager
from pathlib import Path

FIELDS={'helper','profile','takeover_runtime'}

def load(M,p,I,W,K,authrow):
    spec=p['checkpoint_takeover'];M.require(set(spec)==FIELDS and spec['helper']==M.binding(__file__),'closed mechanical takeover integration required')
    q=M.read(spec['profile']);B=M.module('mechanical_actual_checkpoint_bridge',q['implementation']);tp=M.read(q['takeover_plan'])
    T=M.module('mechanical_actual_checkpoint_T',q['takeover_module']);T.life=M.module('mechanical_actual_checkpoint_lifecycle',tp['shared']['lifecycle'])
    loader=M.module('mechanical_actual_checkpoint_layout',spec['takeover_runtime'])
    if 'layout'in tp:loader.select_layout(T,tp['layout'])
    auth=M.read(authrow);outcome=M.binding(Path(auth['output'])/'authorization/outcome.json');result,value=M.authorization_outcome(authrow,outcome,I,K)
    M.require(q['outcome']==outcome and q['result']==result and q['descriptor']==value['descriptor']
        and q['guard']==M.read(outcome)['resource']and q['workbook']==p['workbook']and q['source']==p['source']
        and q['tracker']==p['tracker']and q['directory']==str(Path(p['output'])/'handoff')
        and 'prior_takeover_chain'in q,'mechanical takeover lost actual authorization or selected current authority')
    successful=lambda bound:M.authorization_outcome(authrow,bound,I,K)
    return B,T,successful


def current(M,p,I,W,K,authrow):
    B,T,successful=load(M,p,I,W,K,authrow)
    q,v,w,s,old,new=B.validate(p['checkpoint_takeover']['profile'],I,W,T,successful)
    M.require(new.owner==p['owner'],'mechanical takeover has foreign new owner')
    return w,s,new


@contextmanager
def installed(M,p,I,W,K,authrow):
    B,T,successful=load(M,p,I,W,K,authrow);profile=p['checkpoint_takeover']['profile'];q=M.read(profile)
    complete=Path(q['directory'])/'handoff.json';parent=M.read(p['preserved_context']['plan'])
    from contextlib import ExitStack
    with ExitStack()as stack:
        if complete.exists():stack.enter_context(B.prior_scope(M.binding(complete),profile,I,W,T,successful,completed_successor=lambda:successor(M,p,complete)))
        stack.enter_context(preserved_step_scope(M,B,W,B.lease_predicate(profile,I,W,T,successful),parent['core']))
        yield B,T,successful


def completed(M,p,I):
    q=M.read(p['checkpoint_takeover']['profile']);B=M.module('mechanical_actual_checkpoint_completed',q['implementation']);row=M.binding(Path(q['directory'])/'handoff.json')
    return row,B.completed(row,I)


@contextmanager
def create_handoff(M,p,I,W,K,authrow):
    B,T,successful=load(M,p,I,W,K,authrow);profile=p['checkpoint_takeover']['profile']
    row=B.handoff(profile,I,W,T,successful,M.keep)
    with B.prior_scope(row,profile,I,W,T,successful):yield row


@contextmanager
def preserved_step_scope(M,B,W,allowed,wrapper):
    """Rebuild the original preserved AST and alter only its lease predicate."""
    import ast,inspect,textwrap
    outer=W._step;M.require(M.binding(outer.__code__.co_filename)==wrapper,'foreign preserved worker wrapper')
    closure=inspect.getclosurevars(outer).nonlocals;scope=closure.get('scope');old=closure.get('old_step')
    M.require(isinstance(scope,dict)and callable(old)and old.__name__ in scope,'preserved worker closure missing')
    core=M.module('mechanical_original_preserved_step',wrapper);function=inspect.unwrap(core.instruction_scope)
    tree=ast.parse(textwrap.dedent(inspect.getsource(function)));nodes=[];started=False
    for node in tree.body[0].body:
        names={x.id for x in node.targets if isinstance(x,ast.Name)}if isinstance(node,ast.Assign)else set()
        if 'tree'in names:started=True
        if started and 'scope'in names:break
        if started:nodes.append(node)
    M.require(nodes and any(isinstance(n,ast.ClassDef)and n.name=='Change'for n in nodes),'original preserved AST recipe missing')
    ns=dict(ast=ast,inspect=inspect,textwrap=textwrap,old_step=old,require=M.require)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),wrapper['path']+'#historical-step-ast','exec'),ns)
    original=scope[old.__name__];selected=B.lease_predicate_function(original,ast.unparse(ns['tree']),allowed)
    scope[old.__name__]=selected
    try:yield
    finally:scope[old.__name__]=original


def successor(M,p,complete):
    """Only the actual first child may turn the handoff into historical input."""
    job=M.read(p['original_job']);config=M.read(job['config']);ops=Path(config['run_root'])/'dispatcher-v2'/Path(p['output']).name
    own=Path(p['output'])/'authoring-job.json'
    if not own.exists():return None
    M.require(M.read(M.binding(own))==dict(job,outcome=M.binding(complete),operations=str(ops)),
        'mechanical handoff author job changed')
    path=ops/'000/outcome.json'
    if not path.exists():return None
    row=M.binding(path)
    return row if M.read(row)['status']=='COMPLETED_OPERATION'else None
