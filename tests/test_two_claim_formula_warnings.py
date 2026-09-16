import pytest
from scripts.batch.harbor_dialogue_source_warnings import accuracy_findings

FINDING='The senior turns leak numeric calculation literals: 12 and 2 in the first claim and 365.25 in the second claim.'

def test_exact_numeric_allegations_select_source_checks_not_a_pass():
    assert accuracy_findings([FINDING],2)==({0:[12.,2.],1:[365.25]},[],'two_claim_formula_operands')

@pytest.mark.parametrize('extras,count', [([FINDING],3),([FINDING,'The sign is wrong.'],2),([FINDING.replace('first claim','third claim')],2),(['There are leaked answers.'],2),([],2)])
def test_unknown_or_substantive_allegations_do_not_become_warnings(extras,count):
    with pytest.raises(ValueError):accuracy_findings(extras,count)


def test_sentence_final_constant_and_decimal_are_counted_exactly():
    from scripts.batch.harbor_dialogue_source_warnings import spoken_literals
    assert spoken_literals('Divide by 12 and then by 2. Multiply by 365.25.') == {12.:1, 2.:1, 365.25:1}
    assert spoken_literals('Use "2025 value 12" and row2, then 12.5 rather than 2.') == {12.5:1, 2.:1}
