# FCP reliability and private evidence custody — 10 September 2026

This report records the earlier verification-package pass. The subsequent disk
backend, legacy candidate, archive and current validation work is documented in
[the follow-up report](compact-graph-reliability-20260910.md). Test counts below
belong to that earlier source version.

Task production remains stopped. This repair pass generated no production RL
tasks, ran no model calls or solve rollouts, and performed no corpus cleanup.
The former `.corpus-runs/20260908` directory was absent at the start of this
pass. Its old private evidence is not available for current requalification.
All 700 original workbook files remain present in the three sibling raw roots:
100 in RawWorkbooks1, 300 in RawWorkbooks2, and 300 in RawWorkbooks3. The new
inventory has 700 unique identities, comprising 565 XLSX, 77 XLSM, 45 XLS and
13 XLSB files; its inventory digest is
`d93348c5caebf41e411db485efd68e66e54f0b64ed16d61b80bcc2d18a2c6083`.

## Implemented in this pass

- Private verification capture requires native strict promotion qualification
  and the canonical complete-readiness proof. It retains selected dependency
  trees, exact publication journals, signed receipts, original paths, empty
  directories, current verifier code, and configuration.
- A self-contained object store saves identical bytes once. Its externally
  pinned manifest maps original logical paths to those objects. Private
  financial proof stays outside the agent task and Docker build context.
- Isolated replay uses a pinned local Python 3.12 Docker image, read-only
  namespace mounts, no networking, no Docker socket, and current owning
  financial/publication validators. It never rewrites signed evidence or
  substitutes an answer-key smoke for independent reconstruction.
- Historical runtime observation identifies its producing Python and Docker
  explicitly. Portable replay checks those original command identities without
  impersonating their executables or weakening native runtime verification.
- Process-local cleanup capabilities are issued only after isolated replay.
  Current release/tracker/task, source, trust anchor, protected evidence and
  verifier changes revoke them. Serialized success flags cannot issue one.
- Finalization holds the publication and workbook locks, retains its private
  association and verification receipt, and journals exact file deletions.
  No-follow walks, hashes, identity checks, quarantine, and final revalidation
  preserve changed files. Cooperative writers must respect those locks; this
  does not claim protection from a hostile same-user process racing syscalls.
- An interruption between package rename and association publication is
  recoverable by fresh native recapture and exact comparison. Interrupted
  deletion resumes only from its validated journal and a fresh isolated proof.
- The publication coordinator automatically invokes finalization when its
  pinned verification image is configured. Failed private verification reports
  cleanup pending while preserving the already-published outcome and evidence.
- The corpus report accepts an inventory-bound, explicitly selected package
  manifest per workbook and keeps every original in its denominator. It does
  not mistake deleted scratch for an invalid privately verified release.
- Promotion qualification accepts an explicitly selected private package after
  scratch removal. Native validation remains the default; it does not silently
  fall back to historical archive claims.
- Duplicate AST node IDs now cause integrity failure instead of silently
  replacing financial definitions. AST generation releases the loaded workbook
  before graph construction and the edge-deduplication index before SCC work.
  Focused tests preserve exact CSV rows, names, references, and cycle behavior.
- The supported test driver now enforces Python 3.12 explicitly.

## Validation

The complete supported command passed:

```bash
.venv/bin/python scripts/test_pipeline.py
```

**5,187 tests and 101 subtests passed**, followed by **all 158 dialogue
assertions**. There were two skips, eight deselections, seven warnings, and no
failures. The pytest phase took 852.59 seconds. Private-corpus, real Excel, and
host-control checks were excluded from this hermetic run.

Production source and configuration remained unchanged. One test was appended
shortly after the 459-file freeze; its prior file content is byte-identical to
the frozen prefix. Final collection confirms the same 5,189 selected cases
(5,187 passes plus two skips), and the complete affected 30-test file passed
again separately. This is recorded explicitly rather than claiming every test
file stayed unchanged. `git diff --check` passed.

The earlier integrated run was deliberately interrupted for the corpus-report
integration repair. Its log remains separately labeled as aborted.

Two real Docker isolation checks passed against the final code. They establish
read-only original-path reads after synthetic originals are removed, no host
sentinel/socket/network access, and rejection of an intentionally invalid
release by the actual financial validator. They do **not** demonstrate a
successful production release or authorize corpus deletion. The tested existing
image is `sha256:5e704aaa91c18cd69970c7249552af9b3f4b704be01956c934b4af336fcce4de`.

Logs, source hashes, the raw inventory, and `verification-summary.json` are in
`diagnostics/reliability-20260910/`. The temporary code-only import snapshot was
removed after final Docker checks: 253 files / 49,437,189 logical bytes. This
was disposable test scratch; no original or corpus evidence was deleted. Protocol unit
fixtures explicitly mock financial boundaries; their passing results are not
counted as financial-task qualifications.

## Remaining acceptance limits

The full pipeline is not claimed completely repaired. This pass does not
replace the missing corpus evidence or establish a new workbook-to-Harbor yield.

- Large workbook 0266 still needs a compact/disk-backed graph and compatible
  loading, or evidence that a future complete rebuild fits the existing cap.
  Releasing temporary objects reduces overlap; it is not that proof.
- The 58 XLS/XLSB originals still require validated conversion semantics and
  trusted receipts. Source files being present does not establish safe support.
- Workbook 0117 still lacks a completed trusted data-table observation chain.
  Missing external dependencies, historical curation, and non-unique recurrences
  cannot be repaired by substituting cached answers or changing targets.
- The first private package retains unique AST/graph bytes required by current
  validators. Deduplication removes repeated copies, not necessary evidence.
  Shared source/segmentation generations and unselected historical builds stay
  protected until all referring releases have independently verified custody.
- There is no fresh 700-entry terminal ledger or current-code before/after yield
  result after removal of the dated run. Historical 231/700 reconstruction and
  earlier promotion counts are not current accepted yield.

Current documentation and code are retained in the repository, separately from
run artifacts. No claim about measured agent difficulty is made.
