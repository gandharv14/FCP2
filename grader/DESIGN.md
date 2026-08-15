# Finance Grader API design

## Goals

The grader gives spreadsheet reconstruction tasks useful partial credit without
changing the existing exact-match definition of a full pass. It is:

- deterministic and dependency-free;
- safe for hidden Harbor answer keys;
- reusable by Harbor, local evaluation, and pass@k reporting;
- explicit about continuous versus discrete scoring; and
- compatible with Harbor's canonical reward files.

## Public API

`grader.finance_grader` exports:

```python
grade_continuous(answers, answer_key) -> Grade
grade_discrete(answers, answer_key) -> Grade
grade(answers, answer_key, mode="continuous") -> Grade
```

`answers` is the submitted `answers.json` object. `answer_key` contains:

```json
{
  "kind": "cell_value",
  "tolerance": {
    "numeric_abs": 0.000001,
    "numeric_rel": 0.000001
  },
  "targets": {
    "Sheet!A1": 123.45,
    "Sheet!C1": 10.0,
    "Sheet!D1": 20.0
  },
  "groups": {
    "Sheet!A1": ["Sheet!A1"],
    "Sheet!C1:D1": ["Sheet!C1", "Sheet!D1"]
  }
}
```

`groups` is optional and maps each curated output band to the target refs it
covers; it drives the band-grouped weighting described below.

The returned `Grade` contains the headline score, per-cell subscores and
weights, scoring mode, metadata, and detailed cell results. `Grade.to_dict()`
emits the canonical structure used by Harbor and the trajectory viewer.

## Input normalization

Cell references are normalized by removing apostrophes, `$` markers, and
surrounding whitespace. Numeric strings may contain commas or a percent sign;
the percent sign is removed without rescaling because task instructions require
answers in the workbook's displayed scale.

Booleans, missing values, malformed numerics, NaN, and infinity are not valid
numeric answers. Extra submitted cells are ignored. Missing expected cells
remain in the denominator and receive zero credit.

## Continuous scoring

For an expected value `e`, submitted value `g`, absolute tolerance `a`, and
relative tolerance `r`, an answer is exact when:

```text
|g - e| <= max(a, r * |e|)
```

Exact answers receive `1`. Otherwise, the normalized error and cell score are:

```text
normalized_error = |g - e| / max(|g|, |e|, a)
cell_score       = clamp(1 - normalized_error, 0, 1)
```

This symmetric normalization avoids scale bias across percentages, multiples,
and large currency values. A 100% or larger normalized error receives zero,
while progressively closer answers receive progressively more credit. Near-zero
answers must fall within the absolute tolerance to receive meaningful credit.

Weights are grouped by curated output. The answer key's optional `groups`
table maps each output band to the cell refs it spans; every group receives an
equal share of the headline score, split evenly among its cells:

```text
cell_weight      = 1 / (number_of_groups * cells_in_this_group)
continuous_score = sum(cell_score * cell_weight)
```

A multi-period band — one fill formula copied across seven periods — therefore
counts once, not seven times, which stops wide series from dominating the
score. Target refs absent from `groups` (and keys without a `groups` table at
all) become singleton groups, which reproduces the historical uniform
`1 / number_of_targets` weighting exactly.

Consequently, coverage is part of the reward: unanswered targets contribute
zero rather than being removed from the denominator.

## Discrete scoring

The discrete API preserves the original pass contract:

```text
discrete_score = 1 if every target is exact, otherwise 0
```

Harbor tasks use only continuous mode. The discrete API is used separately when
computing legacy pass@k, where a successful attempt must reconstruct every
target within the configured tolerance.

## Harbor integration

Each generated task packages:

```text
tests/
  test.sh
  run_grader.py
  finance_grader/
  answer_key.json
```

`test.sh` explicitly invokes `run_grader.py --mode continuous`. The runner reads
`/app/answers.json` and the verifier-only answer key, then writes:

- `reward.json`: headline and per-cell rewards;
- `reward.txt`: six-decimal headline fallback;
- `reward-details.json`: canonical `Grade` serialization;
- `score_details.json`: reporting-compatible assessment details; and
- `answers.json`: a verifier-log copy of the submitted artifact when present.

Missing or invalid submissions score zero. A missing or invalid answer key is a
grader failure: the runner still emits a zero reward with diagnostic metadata
and exits nonzero.

`grader/sync_tasks.py` copies the canonical runner and package into existing
generated bundles. `xl_output_task.py` performs the same packaging for future
bundles, preventing generated verifiers from drifting from the shared source.

## Reporting

Chat-level and offline Harbor reports use the same continuous API. Multi-attempt
reports grade each attempt twice:

- continuous mode for per-attempt quality, mean, and population variance;
- discrete mode for pass@k.

Population variance uses denominator `N` because the five retained attempts are
the complete evaluated pass@5 set rather than a sample of those attempts.

## Validation

The standard-library test suite covers tolerance boundaries, normalized error,
negative and zero expected values, malformed and non-finite answers, partial
coverage, reference normalization, discrete all-or-nothing behavior, canonical
serialization, Harbor output files, generated bundle synchronization, and
pass@5 population statistics.
