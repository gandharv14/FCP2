# Standalone protected table quarantine producer — isolated proposal

This proposal adds a separate standalone system-Python dispatcher and offline-window wrapper. It does not edit the ordinary runner, install anything, invoke Excel, alter interfaces, issue a request, approve a source or publish a task. It revises the unpublished quarantine-v1 receipt contract because a standalone producer cannot truthfully attest to pipeline-only health/snapshot computations.

## Protocol revision

The protected producer signs **only facts it independently establishes**: the full exact request hash and ID, profile/domain and quarantine-only scope, original identity and actual source/staging/output paths and byte hashes, complete stdlib table-observation hashes, protected selected policy-code identity, key identity, actual engine version and execution times, completed-calculation flag and all six actual isolation controls/PF identity. Request policy-code identity must exactly equal root-protected configuration. This is a selected-code policy binding, not a claim that the standalone process imported or executed pipeline modules.

The producer never imports the pipeline, openpyxl, evaluator, health module or pipeline-created unsigned claims. It loads only the exact protected ordinary-runner file after root ownership/parent and digest checks. That file supplies stdlib observation/grafting, known Excel automation script, isolation and signing primitives. The ordinary file remains byte-for-byte unchanged by this proposal.

Pipeline health and semantic-snapshot hashes move into `result.derived_evidence`, explicitly labelled `pipeline_derived_not_runner_attested`. `assemble_result` and `validate_result` independently recompute them from actual original/staging/output files. Altering the unsigned evidence and rehashing the outer result still fails. Exact original/staging/output financial semantic equality, complete normal formula/table cache matching, current health, protected signature verification and source/task approval denials remain unchanged. The chain consumer continues to revalidate both edges with the same reconstruction/exclusion/leak gates. No production quarantine receipts had been issued under the earlier v1 proposal; no historical evidence is rewritten.

## Protected dispatcher

`proposal/scripts/recalc/table_quarantine_runner.py` is a separate entry point. `main` requires macOS root, the dedicated sudo invoker, a valid inherited shared offline-window lock FD and protected configuration. There is no ordinary-request fallback branch. Requests may publish only to `<protected artifact root>/<canonical request UUID>/<workbook>.xlsx`; arbitrary request-selected output paths reject. Original files remain untouched. Their original basename is preserved while Excel observes the private copy.

The dispatcher independently checks the request's exact schema, digest, UUID, identity, original ownership/hash/size, nonempty complete original table observation, allowed engine version, expiry, selected policy-code hashes and protected key binding. It rechecks these after admission waits. It requires strict true booleans for all six isolation controls, verifies the live offline condition and applies the existing PF isolation context around actual Excel automation, complete staging observation, graft and signature.

Actual Excel staging is retained before cleanup. The output is grafted only from that observed file; table geometry, formula identities, primitive values and every table result are rechecked. It signs the distinct quarantine domain using the existing protected signer and its signature self-verification. Health and full financial-semantic acceptance remain independently checked by the pipeline afterward; this receipt alone does not pass source health or approve a transformation.

## Durable replay and atomic artifacts

A protected per-request UUID directory is atomically created and fsynced before Excel starts. The reservation binds the request object/file hashes. The ID remains consumed after success, failure, interruption or a process crash; there is no journal deletion, retry reset or automatic new-ID creation. Simultaneous reservations permit exactly one process. A separate protected session lock serializes table requests, and the wrapper's shared `/var/run/fcp2-recalc-window.lock` serializes with existing ordinary offline batches too.

The producer assembles a root-private temporary directory containing `original.xlsx`, `excel-staged.xlsx`, final `<workbook>.xlsx`, full request, full producer receipt and all table observations. Only after signing and final byte checks does it atomically rename the completed bundle under the protected artifact root. The published directory remains root-owned; files become invoker-readable owner-only0400. Subsequent owner modification cannot satisfy the retained signature/hash checks. A crash after bundle publication but before journal completion leaves the complete bundle and a consumed reservation; replay remains rejected. Failed private work stays available for diagnosis and is not counted as a successful result.

`failed.json` retains bounded error text (8KiB), full-tail traceback (32KiB), error type and exact request binding in the protected private journal. It does not print workbook values.

## Offline window, diagnostics and derived result

`table_quarantine_window.py` is separately root-installed/protected and runs with `/usr/bin/python3 -I`. It verifies the dispatcher/deployment and dedicated console invoker, then checks the explicitly provided persistent Python executable as that user before changing any network interface. Python must be3.12 with pipeline dependencies available; it never uses a transient build interpreter implicitly or imports pipeline code as root.

The wrapper acquires the same host lock as ordinary `offline_window.py`, installs INT/TERM/HUP cleanup, records the interfaces it downs and restores those interfaces in `finally`. The dispatcher independently verifies offline status and PF enforcement. It receives the shared lock descriptor rather than reopening a potentially unrelated file. Child process groups are reaped before restoring interfaces. The standalone producer is not a substitute for this serialized offline entry point. Uncatchable host/power failure cannot run a Python finally block; reservations still prevent replay, and deployment recovery must verify network state before another run.

After the network window ends, the pipeline independently assembles/validates the derived result **as the user**. It writes a private temporary file, fsyncs it, atomically publishes through a no-replace hardlink and fsyncs the parent directory. On interruption/failure, temporary evidence remains; no incomplete final result or overwritten previous result is accepted.

Every run creates a fresh invoker-owned0700 diagnostics directory (default `<derived-result>.diagnostics`). Full producer and derived-validation stdout/stderr are saved as0600 files with per-phase manifests binding exact hashes, byte sizes, return code and request-file hash. Both success and failure logs are retained; interrupted phases are captured after child termination. Terminal output is only concise status, result/bundle/diagnostic paths and hashes. A finalizer rejection therefore preserves its exact cause without asking the user to paste large logs. If storage prevents diagnostic persistence, the wrapper reports that failure instead of claiming logs were saved.

## Deployment prerequisites — not performed

`config.template.json` records reviewed candidate hashes and deliberately leaves the dedicated UID unfilled. A reviewed installation must place both new scripts under the root-protected recalculation directory, create the root-owned0700 journal root and root-owned0755 artifact root with invoker-traversable protected parents, install the distinct protected JSON config, confirm the dedicated UID and permitted exact Excel version, and retain the existing protected receipt key. The configured ordinary-runner dependency must have the reviewed observation implementation hash; an older installed dependency fails closed. No existing normal schema/config/key/entry-point is silently modified.

A future local invocation must explicitly supply the persistent pipeline interpreter, pipeline checkout, fresh new-domain request, fresh derived result and optional fresh diagnostics directory. No actual0117 request or ready-to-run user command is created here. The earlier ordinary request is incompatible and must not be relabeled. Root must review/integrate the protocol, producer and source-inventory chain route before preparing a new request or asking for a Terminal retry.

Even a valid quarantined result is not conversion success. The existing exact semantic checks can conservatively reject Excel re-encodings, calculation-setting changes or unsupported observations. The complete original→signed intermediate→materialized candidate chain, current reconstruction with unchanged curation/targets/tolerances, exclusion/leak checks, new approved inventory evidence and all normal Harbor gates remain mandatory. No answer seeding, oracle fallback, target changes or source-health exception is added.

## Tests

Synthetic fixtures use actual stdlib observations/grafting and real Ed25519 signatures, plus actual pipeline health/snapshot/reconstruction/materialization validators. Only fixture ownership metadata and actual engine/network callbacks are substituted; no host controls, Excel, model or corpus job executes. System Python `-I --help` proves both new scripts load without pipeline dependencies. Kernel-level subprocess reservation races, abrupt child exits, shared-flock descriptor identity, interrupted publication, full diagnostic capture, protected config selection, original-basename preservation and old profile/chain rejection cases are covered.

Run from repository root:

```
.venv/bin/python -B -m pytest .corpus-runs/20260908/diagnostics/table-protected-dispatcher-proposal/tests tests/test_presentation_tables.py tests/test_table_recalc_observation.py tests/test_recalc_deployment.py -q -m 'not host_controls and not real_excel and not private_corpus'
```
