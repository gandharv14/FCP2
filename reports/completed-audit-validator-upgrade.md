# Completed audit validation across the completion-guard upgrade

This explicit evidence contract preserves an already completed audit generated
by auditor 1.3.0 with SHA
`88338cdb4653e1d0d8f12efc61a07afce540ccbcbe991135121fa66b6bcc14a1`.
It supports exactly the addition of three `finish_reason == "stop"` guards.
The ordinary audit cache still requires the exact current generator hash; this
contract does not change that path, reinterpret an incomplete response, allocate
a model attempt, or approve a source/task.

`xl_audit_validator_upgrade.code_identity` reads the root-selected retained
original module and the installed current module. It requires the original hash
above and byte equality after removing exactly one occurrence of each reviewed
new guard from the current module. No historical module is imported or executed.
Any other code, prompt, batching or rendering change rejects this policy.

The adapter runs the current coverage, request, response, correction/history,
configuration, original scope and budget checks against unchanged files. The
original invocation generator identity is checked separately from the current
validation identity. Metadata, requests, scopes, responses, journals and budgets
are never rewritten. Missing or altered evidence and accepted responses whose
finish reason is not exactly `stop` reject. All inherited and submitted counts
remain consumed, including failed historical semantic attempts.

## Root-selected API

```python
from xl_audit_validator_upgrade import propose_upgrade, validate_upgrade

proposal = propose_upgrade(audit_bindings, original_code=original_code_record)
# Root reviews and atomically retains the proposal outside original audit files.
checked_files = validate_upgrade(
    upgrade_receipt_record,
    bindings=audit_bindings,
    original_code=original_code_record,
)
```

A proposal or validated receipt is evidence only: `source_approval` is false,
`model_reexecuted` is false, and `semantic_attempts_added` is zero. Root remains
responsible for admission, publication, immutable receipt retention and tracker
compare-and-swap operations.

Current source/segmentation applicability is a separate mandatory gate. The
owning publication adapter accepts explicitly selected `audit_validator_upgrade`
and `audit_upgrade_original_code` records together. Without a locator change it
revalidates the current audit context directly. With a locator change, root
prepares a new `xl_audit_equivalence.propose_audit_equivalence` result using
`validator_upgrade=` and `original_auditor=`. The ordinary equivalence path is
unchanged; the new receipt must be revalidated with those same caller-selected
records. Historical receipts remain immutable. The current proof still requires
live source/segmentation evidence, original/source bytes, unchanged curation,
ordered targets, baseline ZIP members, exact full financial inventory and the
existing restricted set of machine-locator changes.

## Exact normalization applicability

If an original normalization request names an earlier locator receipt, changing
that request would falsely change the actual model input. Instead,
`harbor_normalization_audit_applicability` revalidates the current owning audit
and equivalence gates, the original raw request/response/Call, all candidate
coverage and independently observed current primitive/context evidence.

Every non-code receipt field must remain exactly equal, including source and
segmentation bindings, inventory hashes, baseline members, target/curation proof,
locator differences and consumed budgets. Every old non-code checked file must
remain present with its exact hash. The only added evidence files are the new
upgrade receipt, its adapter, and the retained original auditor. The old locator
implementation is hash-bound as provenance and is never executed or used as the
current validation authority.

One additional supported case is a copied `draft.json` between canonical runs
for the same workbook. Both files must be byte-identical. Current
`gen_normalizer.analyse` independently rebuilds the original run and must match
its retained complete analysis. Its output and the current analysis must differ
only in `rows[*].evidence_paths.draft_sha256`. Source/baseline paths, values,
formulas, every row field and draft hashes stay exact. The original model input
is retained unchanged; the projected current context exists only in memory for
comparison and is never represented as a submitted request.

Root prepares `propose_applicability(...)`, then independently selects
`normalization_audit_applicability` and `normalization_original_locator_code`
records. The existing readiness and completed-lane consumers validate that
receipt before continuing their original semantic-review, current emission,
primitive equality, profiling and plain-eligibility checks. This contract grants
no lane PASS and spends/resets no semantic budget. Financial context changes,
dropped proof files, other path changes or stale current proof reject.

## Source identity

The fresh normalizer's `<workbook>.xlsx` requirement matches the production
source-preparation contract: `prepare_source_generation` copies the admitted
source into exactly that basename before immutable publication.
`publish_source_generation` accepts `.xlsx` sources only. No `.xlsm` extension
is guessed or silently added by this adapter; source policy/normalization must
establish support before the normalization lane.
