from pathlib import Path
import ast,hashlib
import pytest
from scripts.fleet import package_recovery_continue as M,retained_runtime as RT

@pytest.fixture
def selected(tmp_path):
    from scripts.batch.harbor_instruction_producer import put
    root=Path(__file__).resolve().parents[1]
    # The two immutable implementations are the equality-tested original and
    # candidate used by the native package continuation.
    old=root/'diagnostics/vm-fleet-20260912/control/source-zero-column-001/implementation-001/code/58791f3734239a8d375b36bc8f34c9a84624c6267f7f40ac3c118203afc118d4/xl_mcp_oracle.py'
    if not old.is_file():pytest.skip('retained production original is not included in checkout')
    oldcopy=tmp_path/'old/xl_mcp_oracle.py';oldcopy.parent.mkdir();oldcopy.write_bytes(old.read_bytes())
    tree=ast.parse(old.read_text());source=ast.parse((root/'xl_mcp_oracle.py').read_text());newfn=next(n for n in source.body if isinstance(n,ast.FunctionDef)and n.name=='check_workbook')
    tree.body=[newfn if isinstance(n,ast.FunctionDef)and n.name=='check_workbook'else n for n in tree.body]
    new=tmp_path/'new.py';new.write_text(ast.unparse(tree)+'\n')
    integration=put(tmp_path/'integration.json',dict(snapshot=put(tmp_path/'snapshot.json',dict(root=str(oldcopy.parent)))))
    oldplan=put(tmp_path/'oldplan.json',dict(integration=integration))
    profile=dict(schema_version=RT.SCHEMA,runtime=M.binding(Path(RT.__file__)),entrypoint=M.binding(Path(M.__file__)),read_call=dict(function='context',args=[],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],dependencies=[],symbols=[dict(module_name='xl_mcp_oracle',original=M.binding(oldcopy),selected=M.binding(new),names=['check_workbook'])])
    p=dict(helper=profile['entrypoint'],previous=oldplan,oracle_runtime=put(tmp_path/'runtime.json',profile))
    return p,profile,oldcopy,new,tmp_path,put


def test_exact_scan_only_import_scope_restores(selected):
    p,profile,old,new,tmp,put=selected
    with M.oracle_runtime(p):
        oracle=M.module('original_package_oracle',M.binding(old))
        assert Path(oracle.check_workbook.__code__.co_filename)==new
    assert Path(oracle.check_workbook.__code__.co_filename)==old

@pytest.mark.parametrize('change',['scan','other-function','wrong-root','wrong-symbol','guard'])
def test_any_undeclared_oracle_change_rejected(selected,change):
    p,profile,old,new,tmp,put=selected
    if change=='scan':new.write_text(new.read_text().replace('def check_workbook(', 'def check_workbook_changed('));profile['symbols'][0]['selected']=M.binding(new)
    elif change=='other-function':new.write_text(new.read_text()+'\ndef arbitrary():return True\n');profile['symbols'][0]['selected']=M.binding(new)
    elif change=='wrong-root':profile['symbols'][0]['original']=M.binding(new)
    elif change=='wrong-symbol':profile['symbols'][0]['names']=['scan_value_leaks']
    else:profile['guard_modules']=[M.binding(old)]
    p['oracle_runtime']=put(tmp/'changed.json',profile)
    with pytest.raises(ValueError):
        with M.oracle_runtime(p):pytest.fail('unproved implementation selected')
