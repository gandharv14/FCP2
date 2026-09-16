"""A product used as an operand must retain its arithmetic grouping."""
import importlib.util
from pathlib import Path


def renderer():
    path = Path(__file__).resolve().parents[1] / '.cursor/skills/additional-assumptions-dialogue/scripts/spoken_formula.py'
    spec = importlib.util.spec_from_file_location('nested_product_spoken', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_beta_denominator_preserves_whole_product():
    # Actual source shape H13/(1+(1-J13)*I13). These are three different
    # source quantities, not repeated occurrences of the same period value.
    text = renderer().flatten_spoken(
        'divide (levered beta) by (add (1) and '
        '(multiply (subtract (tax rate) from (1)) by (debt to equity)))'
    )
    assert text == ('levered beta divided by (1 plus '
                    '(the product of (1 minus tax rate) and debt to equity))')
    assert ';' not in text


def test_subtracted_product_is_not_applied_to_the_difference():
    assert renderer().flatten_spoken('subtract (multiply (a) by (b)) from (c)') == \
        'c minus (the product of a and b)'


def test_sum_factor_stays_inside_divisor():
    assert renderer().flatten_spoken(
        'divide (a) by (multiply (b) by (add (c) and (d)))'
    ) == 'a divided by (the product of b and (c plus d))'


def test_top_level_sequential_calculation_is_unchanged():
    assert renderer().flatten_spoken(
        'multiply (subtract (tax rate) from (1)) by (debt to equity)'
    ) == '1 minus tax rate; multiply by debt to equity'


def test_negative_factor_retains_sign_flip():
    text = renderer().flatten_spoken('divide (a) by (multiply (b) by (-1))')
    assert text == 'a divided by (b; flip the sign)'
