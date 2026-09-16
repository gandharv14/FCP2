"""Reopen preserved history with an independently authenticated current checkpoint.

Only the checkpoint-provider and its declared runtime scope are injected. The
original loader, history proofs, source/control comparisons and final W.active
remain the original function, including its normal cleanup.
"""
import ast,hashlib,inspect,textwrap
from contextlib import contextmanager


def require(ok,message):
    if not ok:raise ValueError(message)


def function_sha(fn):
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0],include_attributes=False).encode()).hexdigest()


def projected(original,witness,checkpoint,runtime):
    require(callable(checkpoint)and callable(runtime)and function_sha(original)==witness,'exact retained checkpoint context required')
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)));count=[0,0,0]
    expected=ast.parse('invocation is None',mode='eval').body
    class Change(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.dump(node.test,include_attributes=False)==ast.dump(expected,include_attributes=False):
                count[0]+=1
                return ast.copy_location(ast.parse('w,controls,s,l=_current_checkpoint(env,worker)').body[0],node)
            return self.generic_visit(node)
        def visit_With(self,node):
            node=self.generic_visit(node)
            # Inner current-custody scope: inject after original repair scope,
            # immediately before the independently authenticated checkpoint.
            for i,child in enumerate(node.body):
                if isinstance(child,ast.Assign)and isinstance(child.value,ast.Call)and isinstance(child.value.func,ast.Name)and child.value.func.id=='_current_checkpoint':
                    count[1]+=1;node.body.insert(i,ast.parse('stack.enter_context(_current_runtime(W,I,A))').body[0]);break
            # The owning caller receives the same declared runtime after all
            # historical checks, with original final W.active already passed.
            for i,child in enumerate(node.body):
                if isinstance(child,ast.Expr)and isinstance(child.value,ast.Yield)and isinstance(child.value.value,ast.Tuple):
                    count[2]+=1;node.body.insert(i,ast.parse('stack.enter_context(_current_runtime(env[3],env[1],A))').body[0]);break
            return node
    changed=Change().visit(tree)
    require(count==[1,1,1],'retained context shape differs beyond current checkpoint hooks: '+str(count))
    scope=dict(inspect.unwrap(original).__globals__);scope.update(_current_checkpoint=checkpoint,_current_runtime=runtime)
    exec(compile(ast.fix_missing_locations(changed),inspect.unwrap(original).__code__.co_filename,'exec'),scope)
    return scope[original.__name__]


@contextmanager
def installed(original,row,*,witness,checkpoint,runtime):
    selected=projected(original.context,witness,checkpoint,runtime)
    with selected(row,continued=True)as value:yield value
