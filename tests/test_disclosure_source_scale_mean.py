"""Source-equivalent unit constants and explicit trailing ratio calculations."""
import pytest
from test_disclosure_copied_scope import disclose
from xl_ast_graph import parse_formula

@pytest.mark.parametrize('exponent',[3,4,6])
def test_power_of_ten_matches_existing_literal_unit_scale(exponent):
 assert disclose.ast_is_ref_like(parse_formula('=A1/10^'+str(exponent)))
 assert disclose.ast_is_ref_like(parse_formula('=A1/'+str(10**exponent)))

@pytest.mark.parametrize('formula',['=A1/10^5','=A1/2^6','=A1/B1^6','=A1/(10^6+1)','=A1^6','=10^6/A1'])
def test_parameter_or_non_unit_power_is_not_a_reference_scale(formula):
 assert not disclose.ast_is_ref_like(parse_formula(formula))


def profile(formula,refs):
 return dict(ast=parse_formula(formula),cells=['S!M153'],references=refs,label='Prepaid Expenses as % of Total UPB',functions=['AVERAGE'],operators=[])

def test_exact_prior_mean_is_not_a_ratio_assumption():
 p=profile('=AVERAGE(G153:L153)',['S!'+c+'153'for c in 'GHIJKL'])
 assert disclose._prior_row_mean(p)
 assert disclose.structural_reason(p)is None
 assert disclose.confident_custom_reason('method_working_capital',p)=='recursive_working_capital_rule'

@pytest.mark.parametrize('formula,refs',[
 ('=AVERAGE(G153:M153)',['S!'+c+'153'for c in 'GHIJKLM']),
 ('=AVERAGE(G152:L152)',['S!'+c+'152'for c in 'GHIJKL']),
 ('=AVERAGE(G153:K153)',['S!'+c+'153'for c in 'GHIJK']),
 ('=AVERAGE(G153,L153)',['S!G153','S!L153']),
 ('=AVERAGE(G153:L153)+1',['S!'+c+'153'for c in 'GHIJKL']),
])
def test_nontrailing_or_modified_expression_stays_out(formula,refs):
 assert not disclose._prior_row_mean(profile(formula,refs))
