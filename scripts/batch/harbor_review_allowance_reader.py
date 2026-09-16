"""Read an actual cumulative review allowance at final readiness.

The caller supplies a pinned owning provenance validator, never a data-only
PASS flag. Only the original dialogue review ceiling is extended; ordinary
naturalization bounds and all other readiness checks remain unchanged.
"""
import ast,copy,hashlib,inspect
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require


def install(module,*,expected_code,validate):
    require(record(Path(module.__file__))==expected_code and callable(validate),'selected original readiness reader required')
    original=getattr(module._semantic_attempts,'declared_original',module._semantic_attempts);tree=ast.parse(inspect.getsource(original));changed=[]
    class Ceiling(ast.NodeTransformer):
        def visit_Compare(self,node):
            self.generic_visit(node)
            if len(node.ops)==2 and all(isinstance(op,ast.LtE)for op in node.ops) and isinstance(node.comparators[0],ast.Name) and node.comparators[0].id=='count' and isinstance(node.comparators[1],ast.Constant) and node.comparators[1].value==2:
                node.comparators[1]=ast.Call(func=ast.Name(id='_declared_review_ceiling',ctx=ast.Load()),args=[ast.Name(id='lane',ctx=ast.Load())],keywords=[]);changed.append(True)
            return node
    tree=Ceiling().visit(tree);require(changed==[True],'original review bound AST changed');ast.fix_missing_locations(tree)
    def selected(before,selection,proof):
        witness=validate(before,selection,proof)
        require(set(witness)=={'scope','reservation','ordinal','limit','requests','outcomes'},'closed owning review witness required')
        scope=witness['scope'];ordinal=witness['ordinal'];limit=witness['limit']
        require(scope==dict(workbook_id=selection.content.workbook,original_sha256=selection.content.bindings['original_raw_source']['sha256'],lane='harbor-dialogue-author',budget_id='dialogue-review'),'cumulative review is for another source/lane')
        require(type(ordinal)is int and type(limit)is int and 2<ordinal<=limit and proof['checks']['dialogue_review_provenance']['result']['round']==ordinal,'actual review ordinal differs')
        requests=witness['requests'];require(len(requests)==ordinal and len({r['sha256']for r in requests})==ordinal,'actual distinct review requests required')
        for row in requests:require(record(Path(row['path']))==row,'retained review request changed')
        from scripts.batch.harbor_continuation_allowance import validate_review_reservation
        validate_review_reservation(witness['reservation'],scope,ordinal,limit)
        reservation=read(witness['reservation']);require(reservation['prior_records']==witness['outcomes'][:-1] and len(witness['outcomes'])==ordinal,'cumulative consumption differs from original records')
        for row in witness['outcomes']:require(record(Path(row['path']))==row,'retained review outcome changed')
        values=dict(original.__globals__);values['_declared_review_ceiling']=lambda lane:limit if lane=='harbor-dialogue-author' else 2
        exec(compile(tree,__file__,'exec'),values)
        return values[original.__name__](before,selection,proof)
    selected.declared_original=original
    module._semantic_attempts=selected
    return selected
