"""Forward optional IRR/XIRR guesses without changing the rate solver."""
import math
from unittest.mock import patch
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph, write_csv
from xl_seg import evaluate, model, project
from xl_segment import stabilize_runtime_proof

FLOWS = [-100., 230., -132.]
DATES = [40000., 40365., 40730.]


def call(op, guess=None, *, supplied=True, flows=None, dates=None):
    args = [FLOWS if flows is None else flows]
    if op == 'xirr':
        args.append(DATES if dates is None else dates)
    if supplied:
        args.append(guess)
    return getattr(evaluate.Evaluator, '_fn_' + op)(None, args)


@pytest.mark.parametrize('op', ['irr', 'xirr'])
@pytest.mark.parametrize('guess', [0.1, 0.2])
def test_exact_closed_form_root_guess_reaches_existing_solver(op, guess):
    # NPV * (1+r)^2 = -(10r-1)(10r-2), with roots .1 and .2.
    assert abs(math.fsum(v/(1+guess)**i for i,v in enumerate(FLOWS))) < 3e-14
    actual = call(op, guess)
    assert actual == pytest.approx(guess, abs=1e-10)
    helper = evaluate._irr(FLOWS, guess) if op == 'irr' else evaluate._xirr(FLOWS, DATES, guess)
    assert actual == helper


@pytest.mark.parametrize('op', ['irr', 'xirr'])
def test_omitted_guess_keeps_exact_existing_default_result(op):
    expected = evaluate._irr(FLOWS) if op == 'irr' else evaluate._xirr(FLOWS, DATES)
    assert call(op, supplied=False) == expected


@pytest.mark.parametrize('op', ['irr', 'xirr'])
@pytest.mark.parametrize('guess', [0.0, -0.5, 0.2])
def test_numeric_guess_forwarded_exactly_including_zero(op, guess):
    with patch.object(evaluate, '_' + op, return_value=123.0) as solver:
        assert call(op, guess) == 123.0
        assert solver.call_args.args[-1] == guess


@pytest.mark.parametrize('flows,dates', [
    ([-100.,110.], DATES), (FLOWS, DATES[:2]),
    ([], []), ([], DATES), (FLOWS, []),
])
def test_xirr_rejects_unequal_or_empty_vectors_before_solver(flows, dates):
    with patch.object(evaluate, '_xirr', side_effect=AssertionError('must reject before solver')):
        result = call('xirr', .2, flows=flows, dates=dates)
    assert isinstance(result, evaluate.ExcelError)
    assert result.code == '#NUM!'


@pytest.mark.parametrize('op', ['irr', 'xirr'])
@pytest.mark.parametrize('flows', [[1.,2.,3.], [-1.,-2.,-3.], [0.,0.,0.]])
def test_missing_sign_change_still_rejected(op, flows):
    result = call(op, .2, flows=flows)
    assert isinstance(result, evaluate.ExcelError)
    assert result.code == '#NUM!'


def run_proof(tmp_path, op, guess_formula, cache):
    tmp_path.mkdir(exist_ok=True)
    w = Workbook();s = w.active;s.title = 'Sheet'
    for i,(flow,date) in enumerate(zip(FLOWS,DATES),1):
        s[f'A{i}']=flow;s[f'B{i}']=date
    s['D1']=guess_formula;s['E1']=.2
    s['C1']='=IRR(A1:A3,D1)' if op=='irr' else '=XIRR(A1:A3,B1:B3,D1)'
    file=tmp_path/'fixture.xlsx';w.save(file);w.close()
    ast=AstGraph(file,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    g=model.load(tmp_path,'fixture');g.nodes['Sheet!C1'].value=cache
    seeds={f'Sheet!{col}{i}' for col in 'AB' for i in range(1,4)} | {'Sheet!E1'}
    result,inputs,_,proof=stabilize_runtime_proof(g,project.build(g),seeds,{'Sheet!C1'},
        calculation=evaluate.CalculationMetadata(available=True,iterate=False))
    return result, inputs, proof


@pytest.mark.parametrize('op', ['irr', 'xirr'])
@pytest.mark.parametrize('cache', ['', '-987654321', '0.1'])
def test_reconstructed_guess_dependency_and_no_cached_target(tmp_path, op, cache):
    result, inputs, proof = run_proof(tmp_path, op, '=E1', cache)
    assert result.values['Sheet!C1'] == pytest.approx(.2, abs=1e-10)
    assert 'Sheet!E1' in inputs
    assert 'Sheet!D1' in proof['runtime_radj']['Sheet!C1']
    assert 'Sheet!C1' not in inputs
    assert not result.oracle_reads


@pytest.mark.parametrize('op', ['irr', 'xirr'])
def test_unknown_guess_cannot_be_ignored(tmp_path, op):
    result, _, _ = run_proof(tmp_path, op, '=UNSUPPORTED(E1)', '0.2')
    assert isinstance(result.values['Sheet!C1'], evaluate.Unresolved)
    assert not result.oracle_reads


@pytest.mark.parametrize('op', ['irr', 'xirr'])
def test_error_guess_propagates(tmp_path, op):
    result, _, _ = run_proof(tmp_path, op, '=1/0', '0.2')
    assert isinstance(result.values['Sheet!C1'], evaluate.ExcelError)
    assert result.values['Sheet!C1'].code == '#DIV/0!'
    assert not result.oracle_reads


@pytest.mark.parametrize('op', ['irr', 'xirr'])
def test_self_dependent_guess_still_rejected(tmp_path, op):
    result, _, _ = run_proof(tmp_path, op, '=C1', '0.2')
    assert isinstance(result.values['Sheet!C1'], evaluate.Unresolved)
    assert not result.oracle_reads
