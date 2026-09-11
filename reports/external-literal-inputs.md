# Standalone direct external literal assumption transformer

The `xl_external_literal_inputs.py` command produces candidates with replayable evidence. Source approval and final task qualification remain separate required steps.

The executable entry point is:

```sh
.venv/bin/python xl_external_literal_inputs.py plan \
  --source /absolute/path/original.xlsx \
  --evidence /absolute/path/independently-approved-identity.json \
  --approved-evidence-sha256 APPROVED_SHA256

.venv/bin/python xl_external_literal_inputs.py apply \
  --source /absolute/path/original.xlsx \
  --evidence /absolute/path/independently-approved-identity.json \
  --approved-evidence-sha256 APPROVED_SHA256 \
  --destination /absolute/path/new-immutable-candidate

.venv/bin/python xl_external_literal_inputs.py verify \
  --source /absolute/path/original.xlsx \
  --evidence /absolute/path/independently-approved-identity.json \
  --approved-evidence-sha256 APPROVED_SHA256 \
  --destination /absolute/path/new-immutable-candidate
```

`APPROVED_SHA256` is an independently supplied trust anchor. Computing a hash from unreviewed evidence does not establish identity or approve an assumption; the tool does not create these approvals.

## Closed evidence format

The top-level object has exactly `schema_version` (`external-literal-identity-evidence/v1`), `source_sha256`, nonempty `authority`, and `dependencies`.

Each dependency has exactly:

- `link_index`: the one-based index in the original workbook's externalReferences table, not a corpus ID.
- `workbook_relationship_id`, `relationship_part`, `relationship_id`, `relationship_part_sha256`, and `target_identity_sha256`: exact bindings through the original external-link package to its dependency identity.
- `path` and `sha256`: the actual independently identified original dependency workbook.
- `literal_cells`: complete declarations, each with `sheet`, canonical single-cell `coordinate`, `role` equal to `external_assumption`, and nonempty independent `provenance`.

Evidence is closed-schema and duplicate-key checked. Every external link and every declared assumption must be used, and every source formula occurrence must have exactly one disposition. Dependency file hashes, exact sheet identities and coordinates are revalidated from actual package bytes. There is no cache-value or basename identity fallback.

## Supported boundary

Only regular `.xlsx` files and direct single-cell external identity formulas are supported. Numeric, boolean and simple text literals retain their type. Numeric date literals require matching date systems and matching destination date formatting. Text beginning with `=` remains text. External cached values are never read as assumption data.

Formula source cells are rejected even when their saved values equal a legitimate literal. Shared/array/data-table formulas, external names, arithmetic/ranges/INDIRECT, rich text, blank/error inputs, macros, query/connection-backed packages, charts, validations, opaque consumers, ambiguous cell identities and namespace compatibility metadata are rejected conservatively. Dependency package structure is restricted as well. At most 32 dependencies, 10,000 reference occurrences and 128 MiB of expanded input/dependency bytes are permitted.

Each immutable candidate directory contains `candidate.xlsx` and `manifest.json`, installed atomically under a publication lock. The manifest binds the approved identity evidence, generator and policy, actual dependency files, every original formula occurrence, literal provenance/type, replacement cell, and all before/after package-member hashes. Unchanged members retain their exact bytes. Verification rebuilds the candidate from original source/dependency bytes and requires exact candidate and manifest equality. Interrupted writes never publish a partial candidate; existing outputs cannot be overwritten.

Every output explicitly has `qualification: unqualified_candidate`. The manifest requires fresh source health, trusted recalculation where required, reconstruction without answer seeding, disclosure/leak review, and all Harbor quality/publication gates. It neither publishes a source generation nor approves an inventory or final task. A literal that exposes a curated target must still fail downstream leakage/eligibility checks; direct-reference transformation is not a target exemption.

## Validation and actual-corpus blocker

Run:

```sh
.venv/bin/python -m pytest -q tests/test_external_literal_inputs.py tests/test_source_health.py tests/test_volatile_formula_remediation.py
```

The new suite covers 41 accepted/rejected cases, including poisoned external caches, cached formula answers, malformed/changed identity evidence, all-consumer checks, shared/array/query sources, date mismatch, exact replay, interrupted publication and existing-output conflicts. Combined source/remediation coverage is 86 tests.

No actual corpus candidate was generated: the original evidence scan found no unambiguously available dependency identity. Recovery needs independently identified dependency files and their approved identity/assumption evidence. Production integration must subsequently bind the new manifest into an approved source transformation route and rerun all required gates; this implementation deliberately does not alter frozen source policies or inventory approvals.
