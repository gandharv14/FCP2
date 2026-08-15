"""Canonical continuous and discrete grading for finance cell-value tasks."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Mapping

Mode = Literal["continuous", "discrete"]

DEFAULT_ABS_TOL = 1e-6
DEFAULT_REL_TOL = 1e-6


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _normalise_ref(ref: Any) -> str:
    return str(ref).replace("'", "").replace("$", "").strip()


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_nonnegative(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0.0 else default


def _matches(got: float, expected: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(got - expected) <= max(abs_tol, rel_tol * abs(expected))


def _continuous_numeric_score(
    got: float,
    expected: float,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[float, float]:
    """Return (score, normalized error) for one finite numeric answer.

    Exact-tolerance answers receive full credit. Outside that tolerance, error
    is normalized symmetrically by the larger submitted/expected magnitude.
    A 100% or larger normalized error receives zero credit.
    """
    error = abs(got - expected)
    denominator = max(abs(got), abs(expected), abs_tol)
    normalized_error = error / denominator
    if _matches(got, expected, abs_tol, rel_tol):
        return 1.0, normalized_error
    return _clamp01(1.0 - normalized_error), normalized_error


@dataclass(frozen=True)
class Grade:
    """Canonical grader result consumed by Harbor and offline reports."""

    score: float
    subscores: Mapping[str, float]
    weights: Mapping[str, float]
    scoring_mode: Literal["weighted", "binary"]
    metadata: Mapping[str, Any]
    cells: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        score = _clamp01(self.score)
        subscores = {key: _clamp01(value) for key, value in self.subscores.items()}
        weights = {key: float(value) for key, value in self.weights.items()}
        structured = []
        for cell in self.cells:
            ref = str(cell["ref"])
            structured.append(
                {
                    "name": ref,
                    "label": ref,
                    "id": ref,
                    "criterion_id": ref,
                    "description": f"Accuracy of the submitted value for {ref}",
                    "score": subscores.get(ref, 0.0),
                    "max_score": 1.0,
                    "weight": weights.get(ref, 0.0),
                    "reasoning": str(cell.get("reasoning", "")),
                    "grading_criteria": (
                        "Continuous normalized numerical closeness"
                        if self.scoring_mode == "weighted"
                        else "Exact match within configured tolerance"
                    ),
                }
            )

        metadata = dict(self.metadata)
        metadata.update(
            {
                "scoring_mode": self.scoring_mode,
                "reported_final_score": score,
                "headline_score": score,
                "weighted_total": sum(
                    subscores[key] * weights.get(key, 0.0) for key in subscores
                ),
                "return_shape": "finance_grade",
            }
        )
        return {
            "score": score,
            "subscores": subscores or {"score": score},
            "weights": weights or {"score": 1.0},
            "structured_subscores": structured,
            "scoring_mode": self.scoring_mode,
            "penalties": None,
            "metadata": metadata,
        }

    def score_details(self) -> dict[str, Any]:
        """Return the backwards-compatible assessment payload."""
        return {**dict(self.metadata), "score": _clamp01(self.score), "cells": list(self.cells)}


def _group_assignments(
    answer_key: Mapping[str, Any],
    targets: Mapping[str, Any],
) -> dict[str, str]:
    """Map each target ref to the curated output (band) it belongs to.

    A multi-period output band is one piece of logic filled across its row, so
    it must count once in the headline score no matter how many cells it spans.
    Target refs absent from ``groups`` fall back to singleton groups, which
    reproduces the historical uniform per-cell weighting exactly.
    """
    groups_raw = answer_key.get("groups")
    by_norm: dict[str, str] = {}
    if isinstance(groups_raw, Mapping):
        for group_key, refs in groups_raw.items():
            if not isinstance(refs, (list, tuple)):
                continue
            for ref in refs:
                by_norm[_normalise_ref(ref)] = str(group_key)
    return {
        str(ref): by_norm.get(_normalise_ref(str(ref)), str(ref))
        for ref in targets
    }


def _grade(
    answers: Any,
    answer_key: Mapping[str, Any],
    *,
    mode: Mode,
) -> Grade:
    targets = answer_key.get("targets")
    if not isinstance(targets, Mapping):
        targets = {}

    tolerance = answer_key.get("tolerance")
    if not isinstance(tolerance, Mapping):
        tolerance = {}
    abs_tol = _finite_nonnegative(tolerance.get("numeric_abs"), DEFAULT_ABS_TOL)
    rel_tol = _finite_nonnegative(tolerance.get("numeric_rel"), DEFAULT_REL_TOL)

    valid_answers = isinstance(answers, Mapping)
    answer_items = answers.items() if valid_answers else ()
    got_map = {_normalise_ref(ref): value for ref, value in answer_items}
    if len(targets) == 1 and valid_answers and "answer" in answers:
        only_ref = next(iter(targets))
        got_map.setdefault(_normalise_ref(only_ref), answers["answer"])

    # Each curated output gets an equal share of the headline score, split
    # evenly among the cells of its band. Without a groups table every cell is
    # its own group and the weights collapse to the historical 1/n_targets.
    group_of = _group_assignments(answer_key, targets)
    group_sizes = Counter(group_of.values())
    n_groups = len(group_sizes)
    weights = {
        str(ref): (
            1.0 / (n_groups * group_sizes[group_of[str(ref)]])
            if n_groups
            else 0.0
        )
        for ref in targets
    }

    cells: list[dict[str, Any]] = []
    subscores: dict[str, float] = {}
    n_answered = 0
    n_exact = 0
    n_close = 0

    for raw_ref, expected in targets.items():
        ref = str(raw_ref)
        normalized_ref = _normalise_ref(ref)
        got = got_map.get(normalized_ref)
        answered = got is not None
        expected_num = _as_number(expected)
        got_num = _as_number(got)
        normalized_error: float | None = None

        if expected_num is not None:
            exact = (
                answered
                and got_num is not None
                and _matches(got_num, expected_num, abs_tol, rel_tol)
            )
            close = (
                answered
                and got_num is not None
                and _matches(got_num, expected_num, abs_tol, 0.01)
            )
            if answered and got_num is not None:
                continuous, normalized_error = _continuous_numeric_score(
                    got_num,
                    expected_num,
                    abs_tol=abs_tol,
                    rel_tol=rel_tol,
                )
            else:
                continuous = 0.0
        else:
            exact = answered and str(got).strip() == str(expected).strip()
            close = exact
            continuous = 1.0 if exact else 0.0

        cell_score = continuous if mode == "continuous" else float(exact)
        subscores[ref] = cell_score
        n_answered += int(answered)
        n_exact += int(exact)
        n_close += int(close)
        if not answered:
            reasoning = "No answer was submitted."
        elif got_num is None and expected_num is not None:
            reasoning = "The submitted value is not a finite number."
        elif exact:
            reasoning = "The submitted value matches within tolerance."
        elif expected_num is not None:
            reasoning = (
                f"Normalized numerical error is {normalized_error:.6g}; "
                f"continuous credit is {continuous:.6g}."
            )
        else:
            reasoning = "The submitted text does not match the expected value."
        cells.append(
            {
                "ref": ref,
                "expected": expected,
                "got": got,
                "answered": answered,
                "exact": bool(exact),
                "close_1pct": bool(close),
                "normalized_error": normalized_error,
                "continuous_score": continuous,
                "score": cell_score,
                "weight": weights.get(ref, 0.0),
                "group": group_of.get(ref, ref),
                "reasoning": reasoning,
            }
        )

    n_targets = len(targets)
    passed = n_targets > 0 and n_exact == n_targets
    if mode == "continuous":
        headline = sum(
            subscores[ref] * weights.get(ref, 0.0) for ref in subscores
        )
        scoring_mode: Literal["weighted", "binary"] = "weighted"
    else:
        headline = 1.0 if passed else 0.0
        scoring_mode = "binary"

    metadata = {
        "kind": str(answer_key.get("kind", "cell_value")),
        "grader_mode": mode,
        "curve": (
            "normalized_linear"
            if mode == "continuous"
            else "all_targets_exact_within_tolerance"
        ),
        "valid_answers_json": valid_answers,
        "n_targets": n_targets,
        "n_groups": n_groups,
        "weighting": (
            "band_grouped" if n_groups != n_targets else "uniform"
        ),
        "n_answered": n_answered,
        "n_exact": n_exact,
        "n_close_1pct": n_close,
        "coverage": n_answered / n_targets if n_targets else 0.0,
        "accuracy_exact": n_exact / n_targets if n_targets else 0.0,
        "accuracy_close_1pct": n_close / n_targets if n_targets else 0.0,
        "passed": passed,
        "tolerance": {"numeric_abs": abs_tol, "numeric_rel": rel_tol},
    }
    return Grade(
        score=_clamp01(headline),
        subscores=subscores,
        weights=weights,
        scoring_mode=scoring_mode,
        metadata=metadata,
        cells=tuple(cells),
    )


def grade_continuous(answers: Any, answer_key: Mapping[str, Any]) -> Grade:
    """Grade all targets with normalized linear closeness and equal weights."""
    return _grade(answers, answer_key, mode="continuous")


def grade_discrete(answers: Any, answer_key: Mapping[str, Any]) -> Grade:
    """Return one only when every target matches within exact tolerance."""
    return _grade(answers, answer_key, mode="discrete")


def grade(
    answers: Any,
    answer_key: Mapping[str, Any],
    *,
    mode: Mode = "continuous",
) -> Grade:
    """Grade finance answers using the selected public API."""
    if mode == "continuous":
        return grade_continuous(answers, answer_key)
    if mode == "discrete":
        return grade_discrete(answers, answer_key)
    raise ValueError(f"unknown grader mode: {mode!r}")
