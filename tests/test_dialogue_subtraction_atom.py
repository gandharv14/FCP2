"""Regression for retained0200 valid subtraction blocked by literal 'minus'."""
import importlib.util
from pathlib import Path
import pytest

PATH = Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py'
spec = importlib.util.spec_from_file_location('subtraction_atom_lib', PATH)
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

ACTUAL_0200 = (
    "Otherwise, if that first locked input is below next period's row-fixed figure, "
    "subtract it from next period's figure, divide by 30, and multiply by the "
    "column-fixed second locked input; otherwise return 0."
)


def test_retained_subtraction_sentence_covers_operator_atom():
    assert lib.clause_covered('minus', ACTUAL_0200)


@pytest.mark.parametrize('text', [
    'Subtract the first locked input from the next period figure.',
    'Compute this by subtracting the first locked input from the next period figure.',
    'The first locked input is subtracted from the next period figure.',
])
def test_positive_subtraction_forms(text):
    assert lib.clause_covered('minus', text)


@pytest.mark.parametrize('text', [
    'Do not subtract the input.', 'Never subtract the input.',
    'Without subtracting anything, multiply the inputs.',
    'Use the subtraction label as a row name.', 'Add the inputs.',
    'Multiply both inputs.', 'Use the result only.',
])
def test_other_operations_and_negated_subtraction_are_not_coverage(text):
    assert not lib.clause_covered('minus', text)


def test_literal_and_longer_clause_requirements_are_not_relaxed():
    assert not lib.clause_covered('the text literal "minus"', ACTUAL_0200)
    assert not lib.clause_covered('next period minus locked input', 'Subtract something.')
