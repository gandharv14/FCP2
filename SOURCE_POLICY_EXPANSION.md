# Source-policy expansion

This document defines the opt-in path for workbook constructs that
`xl_source_health.py` currently quarantines. It does not authorize a route
change by itself. A construct becomes eligible only after its transformer,
signed evidence, and acceptance fixtures are implemented and reviewed.

## Safety boundary

The normal `pass`, `restricted_pass`, and `recalc_candidate` routes remain
unchanged. No policy option may turn an unsupported source directly into
`pass`. Expanded support must produce a new `.xlsx` candidate, a transformation
manifest, and a signed trusted-Excel receipt. Source health then observes that
candidate from scratch.

The original workbook remains immutable. The transformed candidate and all
evidence are hash-bound and published as an inactive source generation until
the complete task release passes compare-and-swap publication.

## Construct-specific treatment

### Empty or stale formula caches

Use the existing `xl_source_recalc.py request` and `execute` flow. Require the
root-owned isolation attestation, root-owned sandbox runner, trusted public key,
pinned Microsoft Excel version, disabled network/macros/add-ins/link updates,
calculation completion, and signed runner receipt. The recalculated workbook
must receive a fresh healthy source report before AST generation.

### External links and connections

Network access and link updates remain disabled. The transformer must either:

- embed every required source workbook in the signed request and rewrite each
  external reference to an internal sheet reference; or
- materialize the external-reference cell as a typed external input and remove
  the external relationship.

The manifest records every original formula, relationship target hash,
replacement cell, value type, and resulting formula or input. Missing source
workbooks, unresolved names, connection-backed queries, or formulas reaching a
graded output without a complete rewrite remain unsupported.

### Data tables

The trusted Excel transformer may materialize sensitivity-table result grids as
typed values and remove the table definition only when the table is
presentation-only and outside every graded-output dependency cone. Table input
cells and the governing formula remain ordinary model cells. A data table
inside a graded-output cone remains unsupported until the deterministic
evaluator implements its semantics and proves the cached grid.

### Volatile formulas

Deterministic dynamic references (`OFFSET`, A1-mode `INDIRECT`, and the approved
`CELL("filename", ...)` case) continue through `restricted_pass`. Expanding
that list requires an immutable cohort inventory whose workbook IDs and hashes
are reviewed and frozen.

Clock- or randomness-dependent functions (`NOW`, `TODAY`, `RAND`,
`RANDBETWEEN`, and `RANDARRAY`) may only be materialized as typed inputs by the
trusted transformer. The manifest binds the signed completion time or random
seed and removes the volatile formula. They may not remain formulas in the
authoritative candidate.

## Transformation manifest

The future transformer must emit `source-policy-transform/v1` with:

- original and transformed workbook SHA-256 values;
- policy and transformer code versions;
- exact Microsoft Excel engine version and signed receipt hash;
- one disposition for every quarantined construct;
- before/after cell formulas, value types, and relationship hashes;
- dependency-cone classification for every materialized data-table or volatile
  cell;
- a canonical manifest hash.

The source-generation manifest binds this transformation manifest exactly.
Unknown fields, duplicate dispositions, omitted quarantined constructs, hash
drift, or an unsigned receipt fail closed.

## Restricted-cohort updates

Do not edit `verification_manifests/restricted_source_cohort_123.v2.json` in
place. Generate a new versioned inventory, review every added workbook ID/hash
and classification, freeze its cohort hash, and update the code constant in the
same reviewed change. Old source generations remain bound to the previous
inventory version.

## Acceptance tests

Before enabling any expanded route, add fixtures proving:

- original sources are never modified;
- unsupported reports cannot be passed directly to AST publication;
- request, source, output, engine, isolation, and transformation hashes are
  signature-bound;
- network, macros, add-ins, and link updates remain disabled;
- every quarantined construct has exactly one manifest disposition;
- external references are fully internalized or converted to typed inputs;
- data-table materialization is rejected inside graded dependency cones;
- volatile formulas are absent from transformed candidates;
- stale, missing, replayed, or mismatched evidence fails;
- the transformed candidate passes fresh source health, strict segmentation,
  disclosure, oracle, and grader checks.

Until those executable fixtures pass, the current unsupported classifications
remain the production policy.
