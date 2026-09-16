from collections import Counter
import pytest
from scripts.batch import harbor_dialogue_source_warnings as W


@pytest.mark.parametrize('text,expected',[
    ('20,000 plus 11,000.',Counter({20000.0:1,11000.0:1})),
    ('-20,000.50 plus +1,234.25.',Counter({-20000.5:1,1234.25:1})),
    ('1, 2, and 3.',Counter({1.0:1,2.0:1,3.0:1})),
    ('20, 000.',Counter({20.0:1,0.0:1})),
    ('Use "2024,2025 label" plus 50.',Counter({50.0:1})),
])
def test_unambiguous_thousands_and_comma_lists(text,expected):
    assert W.spoken_literals(text)==expected


@pytest.mark.parametrize('text',['20,00','1234,567','1,2,3','1,2345','1,000,00','1.23,456'])
def test_malformed_numeric_grouping_is_not_source_evidence(text):
    with pytest.raises(ValueError,match='comma grouping'):W.spoken_literals(text)


def test_normalization_preserves_tokens_before_explicit_exponent_rejection():
    assert W.grouped_thousands('-1,234.50e-2')=='-1234.50e-2'
    assert W.grouped_thousands('1.25e3, -4.5, 2E-3')=='1.25e3, -4.5, 2E-3'


@pytest.mark.parametrize('text',['1,000e2','1000e2','1 plus 9e9','2E-3','-1.25e+3','.5e2'])
def test_exponents_cannot_be_omitted_or_partially_counted(text):
    with pytest.raises(ValueError,match='exponent numeric prose'):W.spoken_literals(text)


def test_all_source_operands_reject_unexpected_exponent():
    from types import SimpleNamespace
    validator=SimpleNamespace(licensed_formula_literals=lambda claim,task:Counter({1.0:1}))
    with pytest.raises(ValueError,match='exponent numeric prose'):
        W.nongraded_input_literals(validator,{},Counter({1.0:1}),'1 plus 9e9','/tmp')


def role_case():
    extras=['The VP leaks the numeric target literals 20,000 and 11,000.',
            'The Director leaks the numeric target literal 50.',
            'The Managing Director leaks the numeric formula literal 1.']
    claims=[dict(reviewer_only=dict(source='custom_method_detector'))for _ in range(3)]
    claims.append(dict(reviewer_only=dict(source='convention_detector')))
    return extras,claims,{0:'VP',1:'Director',2:'Managing Director',3:'VP'}


def test_role_findings_bind_unique_formula_claims_without_ignoring_reused_roles():
    extras,claims,roles=role_case()
    assert W.accuracy_findings(extras,4,claims,senior_roles=roles)==({0:[20000.0,11000.0],1:[50.0],2:[1.0]},[], 'three_role_formula_operands')
    claims[3]['reviewer_only']['source']='custom_method_detector'
    with pytest.raises(ValueError,match='ambiguous formula role'):W.accuracy_findings(extras,4,claims,senior_roles=roles)


@pytest.mark.parametrize('mutation',['unknown','role','incomplete','malformed'])
def test_role_classification_rejects_unrecognized_or_ambiguous_findings(mutation):
    extras,claims,roles=role_case()
    if mutation=='unknown':extras.append('A source formula has the wrong sign.')
    elif mutation=='role':roles[2]='Director'
    elif mutation=='incomplete':roles.pop(3)
    else:extras[0]=extras[0].replace('20,000','20,00')
    with pytest.raises(ValueError):W.accuracy_findings(extras,4,claims,senior_roles=roles)
