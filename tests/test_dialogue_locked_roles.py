"""Roles must not turn a component reference into the whole expression's role."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def renderer():
    path = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts/spoken_formula.py'
    spec = importlib.util.spec_from_file_location('locked_role_renderer', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def atom(value):
    return dict(op='atom', text=value)


@pytest.mark.parametrize('operation', ['sub', 'add', 'mul', 'div', 'pow'])
@pytest.mark.parametrize('role', ['result', 'upper bound', 'lower bound'])
def test_compound_operands_keep_identity(renderer, operation, role):
    node = dict(op=operation, args=[atom('the first locked input'), atom('the second locked input')])
    original = renderer._plain(node)
    assert renderer._locked_role(node, role) == original
    assert 'first locked input' in original and 'second locked input' in original


def test_atomic_role_remains_supported(renderer):
    assert renderer._locked_role(atom('the second locked input on Revenue'), 'result') == 'the result locked input on Revenue'
    assert renderer._locked_role(atom('the first locked input'), 'upper bound') == 'the upper bound locked input'


def test_nested_branch_does_not_relabel_condition(renderer):
    node = dict(op='if', args=[dict(op='lt', args=[atom('the first locked input'), atom('next period')]),
                               atom('the second locked input'), atom('0')])
    assert renderer._locked_role(node, 'result') == renderer._plain(node)
    assert 'if the first locked input is less than' in renderer._locked_role(node, 'result')


def test_partial_period_formula_preserves_each_reference(renderer):
    # Source shape: IF(C<F,D,IF(C<G,(G-C)/30*D,0)). The two locked
    # inputs C and D must stay distinct inside the arithmetic branch.
    c, d = atom('the first locked input'), atom('the second locked input')
    f, g = atom('this period'), atom('next period')
    partial = dict(op='mul', args=[dict(op='div', args=[dict(op='sub', args=[g, c]), atom('30')]), d])
    tree = dict(op='if', args=[dict(op='lt', args=[c, f]), d,
                dict(op='if', args=[dict(op='lt', args=[c, g]), partial, atom('0')])])
    spoken = renderer._plain(tree)
    assert 'minus the first locked input' in spoken
    assert 'minus the result locked input' not in spoken
    assert 'multiply by the second locked input' in spoken
    assert 'use the result locked input' in spoken
    # A source-backed counterexample to treating that role substitution as
    # cosmetic: C=15, D=120, F=0, G=30 gives 60, versus -360 if C becomes D.
    assert (30 - 15) / 30 * 120 == 60
    assert (30 - 120) / 30 * 120 == -360
