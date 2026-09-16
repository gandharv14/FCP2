import importlib.util
from pathlib import Path
import pytest
p=Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py'
s=importlib.util.spec_from_file_location('hold_flat_library',p);M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
@pytest.mark.parametrize('text',[
 'Hold the row labelled "Accounts Payable" flat through the forecast at its last known level.',
 'Use the current balance. Hold the row labelled "Accounts Payable" flat.',
])
def test_explicit_positive_row_hold(text):assert M.clause_covered('held flat',text)
@pytest.mark.parametrize('text',[
 'Do not hold the row labelled "Accounts Payable" flat.',
 'Never hold the row labelled "Accounts Payable" flat.',
 'Hold the row labelled "Accounts Payable" flat only in the first year.',
 'Hold the row labelled "Accounts Payable" flat unless the driver changes.',
 'Hold growth for the row labelled "Accounts Payable".',
 'Hold the row labelled "Accounts Payable" at its growth rate.',
 'Could we hold the row labelled "Accounts Payable" flat?',
])
def test_new_equivalence_does_not_admit_conditional_or_different_rule(text):assert not M.clause_covered('held flat',text)
def test_original_wording_unchanged():assert M.clause_covered('held flat','The row labelled "Accounts Payable" is held flat across the forecast.')

def test_runtime_only_installs_declared_hold_flat_atom_and_restores(tmp_path):
    import ast
    from types import SimpleNamespace
    from scripts.fleet import mechanical_dialogue_continue as R
    tree=ast.parse(p.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='clause_covered')
    branch=next(n for n in fn.body if isinstance(n,ast.If)and ast.unparse(n.test)=="needle == 'held flat'")
    fn.body.remove(branch);old=tmp_path/'original.py';old.write_text(ast.unparse(tree))
    previous=R.module('original_hold_flat',R.binding(old));v=SimpleNamespace(clause_covered=previous.clause_covered)
    text='Hold the row labelled "Accounts Payable" flat through the forecast at its last known level.'
    assert not v.clause_covered('held flat',text)
    with R.clause_library(v,R.binding(p),R.binding(old),atom='held flat'):
        assert v.clause_covered('held flat',text)
    assert v.clause_covered is previous.clause_covered
    changed=tmp_path/'changed.py';changed.write_text(p.read_text().replace('blob = normalize(haystack)','blob = "held flat"'))
    with pytest.raises(ValueError):
        with R.clause_library(v,R.binding(changed),R.binding(old),atom='held flat'):pytest.fail('unlisted change')
