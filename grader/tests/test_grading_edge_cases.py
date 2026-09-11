"""Regressions for grading valid references and adversarial numeric answers."""
import json
import math

import pytest

from grader.finance_grader import grade


@pytest.mark.parametrize("mode", ["continuous", "discrete"])
def test_exact_zero_with_zero_tolerances(mode):
    result = grade({"S!A1": 0}, {
        "targets": {"S!A1": 0},
        "tolerance": {"numeric_abs": 0, "numeric_rel": 0},
    }, mode=mode)
    assert result.score == 1
    assert result.cells[0]["normalized_error"] == 0


@pytest.mark.parametrize("mode", ["continuous", "discrete"])
def test_oversized_json_integer_scores_zero(mode):
    answers = json.loads('{"S!A1": ' + '9' * 400 + '}')
    result = grade(answers, {"targets": {"S!A1": 1}}, mode=mode)
    assert result.score == 0
    assert result.metadata["n_answered"] == 1
    assert not result.metadata["passed"]


def test_oversized_tolerances_use_defaults():
    result = grade({"S!A1": 1}, {
        "targets": {"S!A1": 1},
        "tolerance": {"numeric_abs": 10**400, "numeric_rel": 10**400},
    })
    assert result.score == 1
    assert result.metadata["tolerance"] == {"numeric_abs": 1e-6, "numeric_rel": 1e-6}


@pytest.mark.parametrize("expected,got", [(1e308, -1e308), (-1e308, 1e308)])
def test_finite_extreme_values_produce_finite_error(expected, got):
    result = grade({"S!A1": got}, {"targets": {"S!A1": expected}})
    assert result.score == 0
    assert math.isfinite(result.cells[0]["normalized_error"])
    assert result.cells[0]["normalized_error"] == 2
    json.dumps(result.score_details(), allow_nan=False)


@pytest.mark.parametrize("relative,score", [(1.9, 0), (2.0, 1)])
def test_overflow_does_not_turn_relative_tolerance_into_a_false_pass(relative, score):
    result = grade({"S!A1": -1e308}, {
        "targets": {"S!A1": 1e308},
        "tolerance": {"numeric_abs": 0, "numeric_rel": relative},
    }, mode="discrete")
    assert result.score == score


@pytest.mark.parametrize("target,wrong", [
    ("'A''B'!A1", "AB!A1"),
    ("'US$D'!A1", "USD!A1"),
])
def test_punctuation_in_sheet_names_does_not_alias_another_sheet(target, wrong):
    result = grade({wrong: 10}, {"targets": {target: 10}})
    assert result.score == 0
    assert result.metadata["n_answered"] == 0


@pytest.mark.parametrize("target,submitted", [
    ("'A''B'!$A$1", "A'B!A1"),
    ("'US$D'!$A$1", "US$D!A1"),
    ("'Sales!Net'!$A$1", "Sales!Net!A1"),
    ("'Input & Assumptions'!$A$1", "Input & Assumptions!A1"),
])
def test_quoted_and_absolute_references_still_match(target, submitted):
    assert grade({submitted: 10}, {"targets": {target: 10}}).score == 1


def test_distinct_punctuated_sheet_groups_keep_equal_weights():
    result = grade({"AB!A1": 10}, {
        "targets": {"'A''B'!A1": 10, "'A''B'!B1": 20, "AB!A1": 10},
        "groups": {"band": ["'A''B'!A1", "'A''B'!B1"], "scalar": ["AB!A1"]},
    })
    assert result.score == .5
    assert result.weights == {"'A''B'!A1": .25, "'A''B'!B1": .25, "AB!A1": .5}
