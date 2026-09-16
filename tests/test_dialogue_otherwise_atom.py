from pathlib import Path
import importlib.util
import pytest
P=Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py'
spec=importlib.util.spec_from_file_location('otherwise_atom_lib',P);A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)

@pytest.mark.parametrize('text',[
 'The row uses 0 unless this period is at least the lower bound. When it is, use the result divided by 12 and 1,000.',
 'The expense is 0 unless profit is greater than 0. When it is, multiply that profit by the locked tax rate.',
 'Return zero unless the condition holds. When it is, use the result.',
])
def test_explicit_default_and_positive_branch_cover_otherwise_atom(text):
 assert A.clause_covered('otherwise',text)

@pytest.mark.parametrize('text',[
 'The row uses 0 unless this period is at least the lower bound.',
 'The row uses 1 unless the condition holds. When it is, use the result.',
 'The row uses 0 unless the condition holds. When it is,',
 'The row never uses 0 unless the condition holds. When it is, use the result.',
 'The row does not use 0 unless the condition holds. When it is, use the result.',
 'Unless the condition holds, use the result.',
])
def test_incomplete_or_negated_default_does_not_cover_atom(text):
 assert not A.clause_covered('otherwise',text)

def test_other_atoms_and_original_otherwise_stay_required():
 assert A.clause_covered('otherwise','Otherwise return 0.')
 assert not A.clause_covered('locked input','The row uses 0 unless it is positive. When it is, use the result.')
