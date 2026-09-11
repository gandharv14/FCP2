"""Execute the real worker branch AST, avoiding unrelated expensive fixtures."""
import ast,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
PATH=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/0184-worker-traversal-development/scripts/batch/harbor_instruction_worker.py'
@pytest.mark.parametrize('fault',[None,'renewal','different_binding','different_body','owning_rejected'])
def test_partition_branch_only_consumes_exact_schema_and_proof(fault):
    tree=ast.parse(PATH.read_text())
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.If) and 'partitions.SCHEMA' in ast.unparse(n.test)]
    assert len(nodes)==1
    branch=copy.deepcopy(nodes[0]);branch.orelse=[]
    module=ast.fix_missing_locations(ast.Module(body=[branch],type_ignores=[]))
    current={'path':'/actual/completion','sha256':'a'*64};previous={'path':'/actual/previous','sha256':'b'*64}
    bound={'schema_version':'disclosure-partition/v1','previous':previous};calls=[]
    if fault=='renewal':bound={'schema_version':'harbor-instruction-lease-renewal/v1'}
    def completion(worker):
        calls.append(worker)
        if fault=='owning_rejected':raise ValueError('owning proof failed')
        return ({} if fault=='different_binding' else current,{} if fault=='different_body' else bound)
    def require(ok,message):
        if not ok:raise ValueError(message)
    env=dict(partition={},bound=bound,current=current,worker=object(),partitions=SimpleNamespace(SCHEMA='disclosure-partition/v1',completion=completion),require=require)
    if fault in ('different_binding','different_body','owning_rejected'):
        with pytest.raises(ValueError):exec(compile(module,str(PATH),'exec'),env)
        assert len(calls)==1 and env['current']==current
    else:
        exec(compile(module,str(PATH),'exec'),env)
        assert len(calls)==(0 if fault=='renewal' else 1)
        assert env['current']==(current if fault=='renewal' else previous)
