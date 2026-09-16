# Financial workbooks to Harbor reinforcement-learning tasks

Generate financial RL tasks from raw Excel workbooks, with independent output
reconstruction, faithful instructions, research environments, and exact grading.
The walkthrough below explains the segmentation stages and the required Harbor
quality gates.

---

## Private verification and automatic cleanup

A published task has two separate deliverables. The runnable task contains its
instructions, environment, and private grading assets. A sibling verification
package retains the evidence needed to establish that the task is sound:
original financial definitions, reconstruction evidence, semantic reviews,
trusted receipts, and publication history. Never put that package inside the
agent-visible task or its Docker build context.

The strict publication coordinator supports automatic custody and cleanup:

```bash
# Supply this alongside the existing scripts/harbor_publish.py arguments:
--verification-image sha256:<installed-verifier-image-id> \
--verification-bundle /absolute/private/path/0001-verification
```

`FCP_VERIFICATION_IMAGE` supplies the same image setting for CLI invocations.
Without a configured image, publication retains its intermediates. With it,
the coordinator automatically performs these steps after strict promotion:

1. Capture the current complete financial and publication proof, preserving all
   signed bytes. Store each distinct file once in a hash-addressed object store.
2. Restore its original logical paths in a new namespace and rerun the owning
   validators in the pinned local Python 3.12 image. The container has no network,
   Docker socket, or host-original mounts; evidence mounts are read-only. The
   image needs the pipeline dependencies and `/usr/local/bin/python`. No image is
   pulled automatically and no agent solve is run.
3. Bind the private package to the exact tracker, release, final task and current
   verifier code. Acquire the publication and workbook locks and delete only
   explicitly selected scratch files whose identical bytes are in that package.
   Save a verification receipt and resumable cleanup journal beside it.

A failed isolated proof leaves cleanup pending. Changed sources, keys, code,
release pointers, task files, or package bytes invalidate cleanup authorization.
Publication success is reported separately from cleanup success. The callable
`harbor_finalize.FinalizeRequest` / `finalize()` API resumes custody and cleanup
without republishing the task. After scratch removal,
`harbor_verification_package.qualify_retained_release()` explicitly qualifies the
release using its pinned private package; a native validator pointed at deleted
scratch correctly fails instead of trusting an old pass flag.

The corpus report supports explicit retained-package qualification through
`--private-verifications /absolute/selection.json` and
`--private-verifications-sha256 <sha256>`. The selected JSON uses schema
`harbor-report-private-verifications/v1`, binds `inventory_sha256`, and lists
`workbooks` with `workbook_id`, `original_sha256`, `harbor_root`, pinned `tracker`,
`bundle`, `manifest_sha256`, and `verifier_image`. Each Harbor root must also be
explicitly selected by the report. No sidecar is discovered or trusted
implicitly; a failed private proof stays failed. All original inventory entries
remain in the denominator.

The initial storage format compacts evidence by exact-byte deduplication. It
still retains unique AST/graph evidence required by current validators. It does
not delete shared source/segmentation generations, original workbooks, curation,
unknown files, or older builds outside the selected task's scratch roots. Those
need a separate cross-release retention decision. Logical deleted bytes and
filesystem free-space change are separate measurements because APFS may share
physical blocks.

On APFS, package construction and replay first try independently writable
copy-on-write clones, then fall back to byte copies. Both paths recheck exact
hashes and file identities; shared hard links to original evidence are rejected.
Captured original paths on explicitly selected `/Volumes` roots are supported.

For a compact transport copy, export the private package without changing its
manifest or signed evidence:

```bash
.venv/bin/python -m scripts.harbor_package_archive export \
  /absolute/private/package /absolute/private/package.tar.gz \
  --manifest-sha256 <pinned-manifest-sha256>
.venv/bin/python -m scripts.harbor_package_archive import \
  /absolute/private/package.tar.gz /absolute/private/restored-package \
  --archive-sha256 <exported-archive-sha256> \
  --manifest-sha256 <pinned-manifest-sha256>
```

Import verifies bounded, exact contents before exclusive publication. Archive
transport does not grant cleanup authority or delete the raw verification
package; retained-release qualification still requires isolated financial replay.

Task production remains stopped. The previous `.corpus-runs/20260908` directory
was absent when this feature was implemented; its old private proof and yield
logs cannot be used as current validation. Code changes are in this repository,
not in that deleted run directory. See [current reliability status](reports/compact-graph-reliability-20260910.md).

## Large graphs and legacy conversion candidates

AST construction accepts `--graph-storage auto|memory|disk` and
`--graph-storage-dir /absolute/scratch`. Auto mode uses a disposable SQLite graph
at 100,000 formulas. Segmentation accepts the same options and automatically
indexes CSV graphs of at least 128 MiB. CSV remains the immutable evidence;
databases and memory-mapped cycle work arrays are disposable and are closed on
success or failure. Storage selection preserves targets, dependency edges,
cycle checks and numeric tolerances. Sparse range projection examines existing
cells without enumerating the blank area of a large range.

`python scripts/legacy_candidates.py --help` exposes explicit isolated XLS/XLSB
candidate preparation. It requires hash-pinned inventory and converter
configuration. Candidates remain `conversion_unverified` until independently
observed financial equivalence and trusted receipts are available. See
[legacy candidate instructions](reports/legacy-candidates-20260910.md) for the
configuration, command and remaining trusted-runner requirements.

## Runtime, validation, and corpus recovery

The pipeline uses Python 3.12. Keep a persistent environment for subprocesses
and delayed recalculation requests:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
uv pip install --python .venv/bin/python -r requirements-harbor.txt
.venv/bin/python scripts/test_pipeline.py
```

`requirements-harbor.txt` installs the MCP runtime required for smoke and live
retrieval checks. The generated sidecar uses the same FastMCP version.
`requirements-lock.txt` records the complete Python 3.12 environment used in
this rebuild; use `uv pip sync --python .venv/bin/python requirements-lock.txt`
to reproduce it. The installed trusted Excel runner continues using its
separate system Python and standard-library-only implementation.

The test command includes ordinary tests and the hidden disclosure, source-profile,
and dialogue regressions through their supported entry points. Add
`--corpus-roots ../RawWorkbooks1 ../RawWorkbooks2 ../RawWorkbooks3` for private
workbook checks, and `--host-controls` to exercise actual macOS sandbox controls.
The root-owned trusted Excel runner intentionally remains compatible with macOS
system Python; it does not import pipeline dependencies. Hermetic receipt tests
use the declared `cryptography` development dependency to generate test signatures,
so they do not silently skip when the host OpenSSL lacks Ed25519 support.

Private root-driver regression fixtures are opt-in through
`HARBOR_PRETRANSPORT_DRIVER_FIXTURE=/absolute/path/to/prepared-driver-fixture`.
Without that configured private directory, the portable suite skips that adapter
fixture; all synthetic pretransport, budget, interrupted-CAS and codec tests run.
A configured but incomplete fixture fails immediately.

Run qualification against a fixed checkout: editing code during a test or
workbook run intentionally invalidates captured evidence. Hermetic workflow
fixtures supply disk-capacity observations and test both sides of the 8 GiB
boundary; actual workbook dispatch still checks the host's free-space reserve.

The evaluator supports scalar `SQRT` and `SLN`, bounded static `COLUMN`
references, and proved single-area `INDEX(...):INDEX(...)` ranges. Text and
logical constants retain their types. Unsupported reference constructors remain
unresolved even inside `IFERROR`. After a parser/evaluator change, regenerate
the source AST and affected segmentation proofs; revalidate transformations and
all dependent gates. Retained historical passes do not certify the new code.

Fresh model calls allow at most three transport retries shared across completed
transient HTTP errors and typed transient connection/timeout failures before
response reading starts. Retries keep the same request and logical attempt;
they do not prove exactly-once provider execution. Opaque errors, certificate
failures, partial responses and sealed historical attempts are not automatically
replayed. No semantic-review budget is reset.

Retained normalization partitions and the remaining structured dialogue writer
round have narrowly versioned code-applicability checks for the reviewed executor
upgrade. These checks bind archived code, current code and unchanged request
evidence; an arbitrary code change still invalidates reuse. They permit the
prospective retry policy on an unsubmitted call and never reopen a sealed call.

Freeze the complete denominator before running intake:

```bash
.venv/bin/python -m scripts.corpus inventory \
  ../RawWorkbooks1 ../RawWorkbooks2 ../RawWorkbooks3 \
  --expected-count 700 --output .corpus-runs/rebuild/inventory.json
.venv/bin/python -m scripts.corpus screen \
  --inventory .corpus-runs/rebuild/inventory.json \
  --run-root .corpus-runs/rebuild --workers 4
.venv/bin/python -m scripts.corpus_build \
  --inventory .corpus-runs/rebuild/inventory.json \
  --run-root .corpus-runs/rebuild --curation-root seg_out --workers 4
```

Each original identity is counted once, including uppercase extensions, macro
workbooks, and legacy formats. Duplicate identities fail intake. Workbooks and
private reports remain outside Git. `scripts.corpus_build` rebuilds source and
segmentation proofs; its reconstruction count is **not Harbor yield**. Retained
curation is copied verbatim. Restricted sources additionally require the exact
versioned inventory and approved batch ID through `--source-inventory` and
`--inventory-batch-id`. Existing approvals remain in the v1 registry; the current
v2 registry retains them and adds observed restricted cohorts. For signed
recalculation, approvals bind the original source; the trusted receipt binds
the recalculated candidate separately. Explicit request/result pairs under
`--recalc-root/<workbook-id>` are also consumed for otherwise ready, unrestricted
sources. Missing pairs, changed evidence, and previously ignored receipts cannot
silently fall back to the original caches. Signature, source identity, semantic
comparison and current source-health gates still apply. This route does not
authorize composing recalculation with a separate source transformation; such
chains require their own complete provenance and approval.

Workers execute an immutable code snapshot. A frozen checkout uses its validated
file manifest directly, including when Git is unavailable; empty or changed
snapshots fail before workbook dispatch. `worker-cap.json` in the run root
can lower `reconstruction_workers` from four to two while model work is active;
healthy in-flight workers finish before replacement work starts. SIGTERM/SIGINT
stops new launches, preserves active results, and leaves unstarted work resumable.
An independent guardian is armed before each command executes. If its controller
dies unexpectedly, the guardian terminates that worker process group without
affecting siblings. It retains the workbook lock through cleanup so an immediate
resume cannot race the previous worker’s termination grace period. Interrupted controllers cannot provide final resource metrics;
resume retains that uncertainty and restarts from validated checkpoints.

Source and segmentation generations are validated by their IDs. Preflight
accepts the same paired generation IDs or release arguments as packaging, so
an inactive candidate does not need compatibility links. `build_one.sh` accepts
`PIPELINE_PYTHON`, `RUNS_ROOT`, `INPUTS_ROOT`, `MCP_INPUTS_ROOT`, `STAGING_ROOT`,
`SOURCE_GENERATION_ROOT`, `SEGMENTATION_ROOT`, and `RELEASE_ROOT`.

Sensitivity-table results remain calculated cells during inspection. A
presentation-table candidate requires retained targets, complete reconstruction,
exclusion of every table result from their dependency cones, and a numeric
answer-leak scan. In-cone tables remain blocked pending deterministic semantics
and trusted Excel agreement. Every transformed candidate still passes source
publication and fresh reconstruction. A versioned restricted-source inventory
can bind a presentation-table candidate to its actual original workbook with
`source-transformation-lineage/v1`; inventory validation rechecks the original,
candidate, curation, exclusion proof, and leak scan. Policy
`presentation-data-tables/v2` also binds the current AST builder and complete
verification implementation. Historical receipts remain preserved; stale
receipts require fresh proofs and a new approval inventory. Valid current
candidates can resume after inventory updates without duplicate inspection.
Candidate approval never
replaces the original hash in the corpus denominator or provenance ledger.
Legacy conversion alone, a passing flag,
a cached answer, and an answer-key grader smoke do not establish solvability.

Selected mirror bands retain their exact curated cell identity through partitioning.
Primitive numeric text remains text, and genuine booleans retain their logical
values during reconstruction. Bounded same-sheet endpoints such as
`A1:OFFSET(C2,-1,0)` retain the OFFSET operation and independently prove the
full range; only signed integer literal displacements are supported. Explicit
empty OOXML array followers retain their validated anchor and position.
HLOOKUP and VLOOKUP read the complete search vector and selected result for
proved rectangular references, preserving selector inputs and genuine cycles.
Truncated, oversized, ambiguous and dynamic tables keep their existing gates.
IRR and XIRR pass an explicitly supplied starting guess to the rate solver;
XIRR rejects unequal cash-flow and date vectors before pairing their entries.
Numeric handlers unwrap a range only when its declared dimensions and actual
length are exactly one cell. This preserves dates and other scalar values
returned by INDEX without guessing intersections for multi-cell ranges.
During workbook iteration, the evaluator can reuse a reconstructed scalar only
after proving its complete static dependency closure ends in fixed supplied
inputs. Dynamic references, lookups, arrays, unknown operations and any cycle
dependency decline reuse. Original iteration order, limits, tolerances and
cycle certificates remain unchanged; original formula caches never seed reuse.
Parser changes require fresh source/AST artifacts; verifier changes invalidate
dependent segmentation and task evidence.
Completed source checkpoints can retain a current AST after independent checks
of its builder, source health, original and transformed bytes, approvals, and
trusted recalculation evidence. Fresh segmentation still runs after a verifier change.
Historical releases remain immutable during that refresh.
An unrelated test, reporting or scheduler change can retain completed
reconstruction after current source authorization, exact live parser/verifier
fingerprints and fresh preflight all pass. The ledger retains the previous
snapshot and the new revalidation evidence. Financial implementation changes
still restart reconstruction. Embedded-assumption ZIP timestamps are fixed so
unchanged supplied-input workbooks have stable hashes across rebuild times.

Audits require an explicit disposition for every inventory row, including
grounded exclusions. The saved coverage artifact retains exact raw batch tables
and reconstructs all resolved final rows. Missing inventory rows cannot become a
valid audit or plain-task eligibility decision. Qualified publication revalidates
the bound audit before changing release state and retains its coverage artifact.
Audit reuse binds that coverage,
the complete Markdown text, inventory, generator version, and model configuration. Evidence is published atomically under a workbook lock;
transport retries are bounded separately from semantic review. Naturalization,
disclosure, dialogue claim coverage, research eligibility, retrieval checks,
container startup, and grader smoke remain required before release. No solve
rollouts are part of this rebuild.

Naturalization rejects frozen-span transport markers in submitted instructions.
For fresh marked writer responses, use `naturalize_recovery.py submit-frozen`
with the exact preamble and input bodies. The command regenerates the frozen
authoring inputs from the immutable source, retains the marked and restored
documents, and requires zero errors and zero span drift before full validation
and a fresh independent semantic review. Interrupted submissions resume only
with identical bodies, authoring inputs and submission mode; they consume the
same bounded attempt.

For an already retained, independently reviewed submission affected by the old
assembly defect, `span_restoration.restore` can select a separate immutable
candidate only when the supported freezer proves exact wrapper removal with
zero span drift and the current full validator passes. The operation preserves
all original bodies, responses, hashes and consumed attempts. A separately
hash-bound review-applicability record must establish that the original review
covers every unchanged prose clause before `accept_review` and ordinary apply.
This recovery makes no model call and grants no additional attempt. Altered
prose, missing reviews, changed evidence and noncanonical spans are rejected.

`scripts.corpus_report` reports stage attempts only. For the rebuild's isolated
Harbor release namespace, run `scripts.corpus_qualification --inventory PATH
--run-root ROOT --bind-ledgers` with the same Python environment. It revalidates
original hashes, qualification evidence, required lane outcomes, immutable
release tuples, and published task bytes before counting a promotion. Stopped
tasks require matching diagnostic notes and evidence. Its
`qualified-yield-report.json` always retains the complete original denominator;
unfinished or corrupted evidence cannot become successful yield. Prior immutable
promotions whose parser or verifier evidence is stale are reported separately
and do not count as currently verified yield. Requalification publishes with an
explicit prior-release compare-and-swap and preserves prior evidence inventories.

For an independently sealed historical stop, the reporter optionally accepts
`--historical-retirements PATH --historical-retirements-sha256 SHA256`. Both are
required. The root-selected `harbor-report-historical-retirements/v1` manifest
binds the original inventory and each exact workbook, namespace, source, tracker,
seal, contract and activation. The reporter never discovers seals automatically.
A validated selection records a historical `STOPPED` outcome with
`current_qualification=false` and `verified_promoted=false`; invalid evidence
still fails. This mode is read-only and cannot be combined with `--bind-ledgers`.

The predecessor requires intact historical bytes and identity; the replacement
requires current live reconstruction proof. Transformed promotions additionally
require independently replayed original-to-candidate lineage. Use
`--trusted-public-key PATH` with the qualifier when the configured trusted Excel
runner has a different public-key path.

After qualification, disposable staging files may be removed only when no
retained proof or release refers to them. Source/AST and segmentation evidence
referenced by validators must remain readable. On APFS,
`.venv/bin/python -m scripts.compact_artifacts --roots ROOT... --report NEW_REPORT --apply` reclaims
duplicate artifact storage in completed generations with independent copy-on-write
clones. It checks exact hashes before replacement, synchronizes replacement files,
and records each operation;
logical files and evidence hashes stay unchanged.

For unique AST and lineage text, macOS native compression preserves the exact
readable files while reducing allocated storage:

```bash
.venv/bin/python -m scripts.compress_artifacts \
  --roots ARTIFACT_ROOT --report NEW_REPORT.json --max-files 1 --apply
```

Omit `--apply` to inspect; omit `--max-files 1` after a successful canary to
process all eligible completed artifacts. Only `.json`, `.csv`, `.md` and
`.txt` files of at least 1 MiB are eligible. Each run probes native filesystem
support and requires matching logical bytes, hashes, metadata, and compression
flags before atomic replacement. Exact copies that native tools leave
uncompressed are recorded and discarded without replacing the original.
Incomplete generations and invalid historical
evidence are excluded. Failed native copies or changed evidence stop the run;
immutable journal events preserve progress. Run from a native local terminal
where filesystem compression is supported: a restricted sandbox may report a
successful copy while leaving unusable compressed metadata, which the checks
reject. Per-file allocated blocks are reported separately from physical free
space because APFS extents may be shared.

Completed audits can be revalidated after a verifier refresh through
`xl_audit_equivalence.py propose --historical-bindings OLD.json
--current-bindings CURRENT.json --receipt NEW.json`. This separate proof permits
changed segmentation generation identifiers. The v2 contract also permits a
source-generation identifier change only when builder code provenance is the
sole change: source workbook, AST CSVs, health, request/result evidence, builder
arguments and policy must remain byte-identical or exactly equal as applicable.
It requires fresh live reconstruction,
unchanged curated targets, baseline contents, financial inventory, model and
complete historical audit. It preserves every original request, response and
consumed attempt; it does not create a new model response. Validate it with the
same command using `validate` and omitting `--historical-bindings`. Publication
independently revalidates a hash-bound `audit_equivalence` receipt against its
actual generation IDs. Packaging consumes the same receipt with
`xl_output_task.py --audit-equivalence-receipt RECEIPT.json`, the original
`--runs-root` audit directory, and pinned release or paired generation IDs.
Use `--normalization-root ROOT` when newly reviewed normalization artifacts live
in a separate directory. This strict single-workbook route revalidates the
original complete audit and current workbook evidence, makes no audit request,
and copies the receipt into the task. It cannot be combined with audit refresh
or audit omission. All downstream quality reviews remain mandatory.

For external direct-literal assumption candidates, see the
[identity evidence format and commands](reports/external-literal-inputs.md).
Complete independently approved dependency evidence is required; cached external
answers cannot substitute for missing files. Legacy conversion remains blocked until a
[trusted binary-to-OOXML proof producer](reports/legacy-conversion-capability.md)
is implemented; self-asserted equivalence certificates are rejected.

Workbooks containing data tables use a separate trusted observation path when
ordinary cache-only recalculation rejects them. The protected table producer
retains the complete original, Excel staging file, table observations, signed
receipt and recalculated intermediate. `xl_table_quarantine_lineage.py` checks
that first edge and the subsequent presentation-table materialization against
the original curation, complete reconstruction, dependency exclusion and leak
scan. `xl_table_quarantine_inventory.py` then requires an explicit versioned
inventory approval, including for candidates whose final health route is
`pass`. The corpus controller revalidates this complete chain on checkpoint
reuse. A signed intermediate alone is never an approved source or task.

The [maintained-runner procedure](reports/table-maintained-installer-proposal.md)
updates an existing protected deployment without reading or replacing its private
key. It binds exact current and proposed file hashes, retains protected backups,
and supports recovery after interrupted replacements. Root execution uses the
separate system Python; pipeline inspection and evidence assembly use persistent
Python 3.12. Completed ordinary recalculation evidence is rechecked against its
original execution window, source bytes, concrete engine/version constraints and
signed controls; expiry after successful execution does not invalidate later
read-only inspection of the immutable result.

Historical segmentation diagnostics can be isolated with
`replay_segmentation_verification.py --execute --workbooks 0330 --manifest PATH
--root ROOT --shadow-root SHADOW`. The report retains all 53 historical rows;
unselected cases are explicitly marked. Each selected case uses the retained
curation and three deterministic runs, with automatic model adjudication
disabled. Missing historical inputs remain missing; a new target selection
cannot stand in for the original failure cohort.

See [the rebuild report](reports/yield-rebuild-20260908.md) for measured stage
counts and remaining blockers, and [the runner instructions](scripts/recalc/README.md)
for temporary offline execution.

## What is in this repository

The pipeline source, and this report describing it. The workbooks it was run
against are client financial models, so neither they nor the ~800 MB of graphs
and segment artifacts derived from them are published here. Paths such as
`ast_out/0248/` and `seg_out/0248/` appear throughout the report as a record of
the run; you will not find those directories in a fresh clone.

| Path | What it does |
| --- | --- |
| `xl_ast_graph.py` | Stage 0. Parses `.xlsx` into the AST graph (`nodes.csv`, `edges.csv`). |
| `xl_seg/` | Stages 1-11. Projection, banding, condensation, scoring, evaluation, lineage. |
| `xl_segment.py` | The CLI that runs the stages and verifies the result. |
| `xl_input_mask.py` | Turns the segmentation into an inputs-only workbook (section 17). |
| `xl_level_split.py` | Writes one workbook per dependency level; `xl_input_mask.py` reuses its XML rewriter. |
| `/harbor-orchestrator` | Autonomous Cursor fleet: launches one isolated agent per workbook, routes bounded repairs, records promotion failures, and promotes each qualified task independently (section 21). |
| `/profile-mcp-sources` | GPT 5.6 Sol subagent skill: bounded public-source reads → reviewed terminology, dataset, cadence, and excerpt profiles; auth/blocked pages are skipped. |
| `/custom-formula-gate` | Pre-package GPT 5.6 Terra skill: identifies key formula variables, matches them against a closed textbook catalog, and emits audited custom-method hints (section 22). |
| `/naturalize-finance-task-instruction` | Final GPT 5.6 Sol skill: losslessly rewrites only the opening and input prose, then fail-closed validates and atomically applies the complete instruction. |
| `xl_variable_source_audit.py` | GPT 5.6 Sol audit stage: inputs workbook → variable/source Markdown, deterministic inventory, and generation metadata. |
| `xl_variable_mcp.py` + `mcp_env/` | Variable-source MCP environments: externally-sourced inputs masked from the workbook and served through a mock research service (section 23). |
| `xl_mcp_oracle.py` | Generic live-sidecar oracle: exact retrieval, provenance, conflicts, masking, leak isolation, and profile-attribution checks. |
| `requirements.txt` | Runtime dependencies: `openpyxl` and `PyYAML`. |

Normalization checks each referenced cell before deciding a grouped audit row's
disposition. Retained maskable cells require an independent
`normalization-review.json` bound to the draft, source workbook, and baseline
file hashes. The review supplies research eligibility, evidence references,
entity, period, scenario, and basis; passing duplicate checks alone is
insufficient. The generated script preserves audited source descriptions and
URLs, records excluded cells even within partially included rows, and checks
its evidence hashes on every execution. See the
[normalization lane contract](.cursor/skills/harbor-normalization-engineer/SKILL.md)
for the complete review schema. Keep failed attempts and first-normalization
snapshots when rebuilding after a code fix.

The delivered Embedded Assumptions sheet preserves promoted input literals and
their locators. Formula derivations belong in private provenance artifacts;
the mask verifier rejects formula text accidentally copied into that sheet.

```bash
pip install -r requirements.txt

# Stage 0: a folder of .xlsx -> ast_out/<id>/{nodes.csv, edges.csv, ...}
python3.12 xl_ast_graph.py "4-10 100" -o ast_out

# Stages 1-11: ast_out/<id> -> seg_out/<id>, one id per workbook
python3.12 xl_segment.py 0248 0262 0449 0450 --source "4-10 100" -o seg_out

# Optional: seg_out/<id> -> a copy of the workbook holding only its inputs
python3.12 xl_input_mask.py 0248 0262 0449 0450 -o inputs_out

# Optional: mask externally-sourced inputs and serve them through a mock MCP
# research sidecar instead (full walkthrough in section 23)
python3.12 xl_variable_mcp.py validate-spec \
    runs/0233-variable-sources/normalized.json
python3.12 xl_variable_mcp.py build runs/0233-variable-sources/normalized.json \
    runs/0233-variable-sources/mcp --workbook 0233 --source "4-10 100"
python3.12 xl_input_mask.py 0233 --source "4-10 100" -o inputs_out_mcp \
    --mask-cells runs/0233-variable-sources/mcp/mask_cells.json
# Package in staging; the audit always reads the unredacted inputs_out copy.
python3.12 xl_output_task.py 0233 --source "4-10 100" --inputs-root inputs_out_mcp \
    --variable-source-audit-inputs-root inputs_out \
    --mcp runs/0233-variable-sources/mcp -o tasks_outputs_mcp
```

`xl_segment.py` takes workbook ids rather than paths: it reads the graph from
`--ast-dir/<id>/` (default `ast_out`) and the original `<id>.xlsx` from
`--source`, which it needs in order to check its own arithmetic. Add `--llm` to
have the output shortlist named and ranked by a model instead of by the scoring
heuristic, or edit the `curation.toml` that each run writes and re-run to apply
the edits by hand.

The run is self-checking: `xl_segment.py` recomputes every output from the
inputs it selected and compares against the values Excel cached in the file. If
the numbers disagree, or if any cell inside the output cone had to be seeded
rather than derived, it exits non-zero. Section 14 explains why that check is
the thing that makes the segmentation trustworthy.

---

## Table of contents

1. [The problem in one page](#1-the-problem-in-one-page)
2. [The big picture](#2-the-big-picture)
3. [Stage 0 — Reading the raw workbook](#3-stage-0--reading-the-raw-workbook)
4. [What the graph actually looks like](#4-what-the-graph-actually-looks-like)
5. [Why the obvious idea fails](#5-why-the-obvious-idea-fails)
6. [Stage 1 — Collapsing the AST](#6-stage-1--collapsing-the-ast)
7. [Stage 2 — Typing every cell](#7-stage-2--typing-every-cell)
8. [Stage 3 — Bands: the key idea](#8-stage-3--bands-the-key-idea)
9. [Stage 4 — Bypassing the mirrors](#9-stage-4--bypassing-the-mirrors)
10. [Stage 5 — Contracting the loops](#10-stage-5--contracting-the-loops)
11. [Stage 6 — Islands](#11-stage-6--islands)
12. [Stage 7 — Choosing the outputs](#12-stage-7--choosing-the-outputs)
13. [Stage 8 — Cutting the four buckets](#13-stage-8--cutting-the-four-buckets)
14. [Stage 9 — Proving it, by rebuilding the workbook](#14-stage-9--proving-it-by-rebuilding-the-workbook)
15. [Stage 10 — Lineage](#15-stage-10--lineage)
16. [Stage 11 — The files that come out](#16-stage-11--the-files-that-come-out)
17. [Handing back an inputs-only workbook](#17-handing-back-an-inputs-only-workbook)
18. [Results](#18-results)
19. [Bugs found along the way](#19-bugs-found-along-the-way)
20. [Glossary](#20-glossary)
21. [`/harbor-orchestrator`](#21-harbor-orchestrator)
22. [`/custom-formula-gate`](#22-custom-formula-gate)
23. [Variable-source MCP environments](#23-variable-source-mcp-environments)

---

## 1. The problem in one page

A financial model is an Excel file. Someone builds it to answer a question like
*"if we buy this company, what return do we make?"*

Inside, it looks like a mess of thousands of numbers. But every financial model,
no matter what it is about, has the same three-part shape:

```
    THINGS YOU              THINGS THE MODEL           THINGS THE MODEL
    HAVE TO SUPPLY   ──►    WORKS OUT ALONG    ──►     IS TRYING TO
                            THE WAY                    TELL YOU

    "Revenue grew           "Gross profit"             "The investment
     8% last year"          "EBITDA"                    returns 28% a year"
    "We pay 21% tax"        "Free cash flow"           "The company is
    "Exit multiple 9x"      "Discount factor"           worth $115m"
```

The left column is the **inputs**. The right column is the **outputs**. The
middle is the **middle**.

Our job: given the Excel file, work out automatically which cells belong in
which column. Then, for every output, write down the complete chain of
calculation that produced it.

That sounds easy. It is not, and section 5 explains exactly why.

**A note on what "cell" means.** A spreadsheet is a grid. Each box in the grid is
a cell, named by its column letter and row number. `B12` is column B, row 12.
Because a workbook has several sheets (tabs), we write the full name as
`Valuation!C30` — sheet `Valuation`, column C, row 30.

---

## 2. The big picture

The core pipeline uses two programs. The first reads Excel. The second decides
what each part of the workbook does.

The main idea is simple:

1. Turn the workbook into a map of cells and formulas.
2. Follow that map to separate supplied inputs from calculated values.
3. Choose the headline outputs the model is trying to produce.
4. Prove that those outputs can be rebuilt from the selected inputs.
5. Save both a human-readable explanation and machine-readable files.

The pipeline does not decide that a cell is an input just because it is blue,
unlocked, or on a sheet called "Assumptions." Formatting and sheet names are
often inconsistent. Instead, it uses the formulas themselves. If one cell is
used to calculate another, the graph records an arrow between them.

```
        4-10 100/0248.xlsx                      the raw Excel file
                │
                │   xl_ast_graph.py             STAGE 0  (pre-existing)
                │   "read the file and turn every
                │    formula into a wiring diagram"
                ▼
        ast_out/0248/                           9,179 nodes
          nodes.csv                             9,895 edges
          edges.csv
                │
                │   xl_segment.py               STAGES 1-11  (new)
                ▼
    ┌───────────────────────────────────────────────────────────┐
    │  1  collapse the AST         9,179 nodes → 5,844 cells    │
    │  2  type every cell          number? text? date? unit?    │
    │  3  group cells into bands   5,844 cells → 2,112 bands    │
    │  3½ promote formula literals 2,112 → 2,120 bands          │
    │  4  bypass the mirrors       991 display bands removed    │
    │  5  contract the loops       circular blocks become one   │
    │  6  find the islands         separate connected groups    │
    │  7  score the outputs        15 headline outputs chosen   │
    │  8  cut the four buckets     266 / 184 / 15 / 664 bands   │
    │  9  rebuild and check        20 of 20 outputs correct     │
    │ 10  trace the lineage        454-step derivation          │
    │ 11  write the files                                       │
    └───────────────────────────────────────────────────────────┘
                │
                ▼
        seg_out/0248/
          segments.json          the four buckets
          bands.csv              every band, labelled
          output_candidates.csv  the ranked shortlist
          curation.toml          the bit a human can edit
          lineage/*.md           one derivation per output
          lineage.json           the same, machine-readable
```

Here is what those steps mean in plain language:

- **Collapse the AST.** Stage 0 records operators such as `+`, `SUM`, and `IF`
  as separate graph nodes. Stage 1 folds those details back into the Excel cell
  that owns the formula. The graph becomes a cell-to-cell map.
- **Type the cells.** The pipeline records whether a cell holds text, a number,
  a date, a percentage, a formula, or another kind of value. This helps it avoid
  treating a heading or year label as a financial result.
- **Build bands.** A financial model usually repeats one line item across
  several months or years. Cells with the same repeated formula pattern are
  grouped into one band, such as a six-year revenue forecast.
- **Expose hidden assumptions.** A number typed directly inside a formula can
  act like an input even though it has no separate cell. Stage 3½ gives each
  important hardcoded number its own source band.
- **Remove display-only copies.** Summary sheets often contain formulas that
  simply point to a result calculated somewhere else. The pipeline follows the
  pointer back to the real calculation instead of counting both as separate
  results.
- **Handle circular calculations.** Interest, debt, and cash schedules can
  refer back to themselves. A whole circular block is treated as one component
  so the rest of the dependency map remains usable.
- **Separate islands.** Not every sheet is connected to the main model. Notes,
  scratch work, and abandoned calculations often form small disconnected
  groups. Keeping them separate prevents them from distorting output selection.
- **Choose outputs.** Candidate results are scored using signals such as labels,
  position, downstream use, and whether they look like final valuation or return
  measures. A human can review the shortlist in `curation.toml`.
- **Create four buckets.** `input` means supplied information; `middle` means an
  intermediate calculation; `output` means a requested result; `scaffolding`
  means content outside the selected calculation path.
- **Rebuild the model.** The evaluator starts with only the selected inputs,
  recalculates the formulas in dependency order, and compares its answers with
  Excel's saved values. A task cannot pass if an output must be copied from the
  original workbook instead of calculated.
- **Write lineage.** For each output, the pipeline records every input and
  intermediate step that leads to it. This is the calculation's audit trail.

The numbers in the diagram come from the current run of workbook `0248`. It
takes only a few seconds. Much larger workbooks, especially those with tens of
thousands of formulas, take longer.

### What happens after segmentation

Segmentation proves that the workbook can be rebuilt, but it is not yet a
finished task. The packaging workflow then:

1. Writes an inputs-only workbook with calculated cells removed.
2. Checks whether important formulas use standard finance methods or
   workbook-specific logic. Safe method guidance is added when needed.
3. Identifies externally sourced assumptions and moves selected values into a
   mock research service, while keeping the task fully reproducible offline.
4. Packages the workbook, research server, instructions, and grader in a staged
   Harbor task.
5. Rewrites only the opening and input prose in a more natural finance style.
   Tables, cell references, rules, hints, and output requirements stay exactly
   the same.
6. Runs the live research-service oracle and an exact-answer grader.
7. Promotes the staged task only if every check passes.

The original workbook is used as a build-time answer key, but it is never placed
inside the task environment.

---

## 3. Stage 0 — Reading the raw workbook

This is `xl_ast_graph.py`. It was already written. Understanding it matters,
because everything later depends on the shape of what it produces.

### 3.1 What is inside an `.xlsx` file

An `.xlsx` file is secretly a ZIP archive full of XML. Unzip it and you find one
XML file per sheet. Each cell that has anything in it appears as a little tag:

```xml
<c r="L8">              the cell L8
  <f>K8+1</f>           its formula
  <v>2025</v>           the answer Excel last calculated  ("cached value")
</c>
```

Two things to notice, because both matter later:

- **Excel stores the answer as well as the formula.** That saved answer is
  called the *cached value*. It is what you see on screen. We use it as the
  correct answer to check our own work against.
- **Empty cells are simply absent.** There is no tag for them at all. This one
  fact causes three separate bugs later on. Keep it in mind.

### 3.2 Sorting cells into three kinds

Every cell that exists gets sorted into one of three kinds:

```
   Does the cell have a formula?
            │
      ┌─────┴─────┐
     YES          NO
      │            │
   formula     Is the value text?
                    │
              ┌─────┴─────┐
             YES          NO
              │            │
            label        input
```

- **`formula`** — the cell computes something. `=K8+1`
- **`input`** — someone typed a number in by hand. `2024`
- **`label`** — someone typed words in. `"Revenues"`

That last distinction is the seed of the whole thing. An **`input` cell is, by
definition, something the model cannot work out for itself.** Nothing feeds it.
If you want the model to run, you have to supply it.

### 3.3 Turning a formula into a tree

This is the clever part of stage 0. A formula is not stored as a tree, it is
stored as a line of text. To understand it, you have to parse it.

Take `=K8+1`. As text it is just four characters. As a *structure*, it is:

```
                 ( + )
                /     \
              K8       1
```

That shape is called an **AST** — an *Abstract Syntax Tree*. "Abstract" because
it throws away things that do not matter, like brackets and spaces. "Syntax
tree" because it shows how the parts of the formula fit together.

A bigger example. `=SUM(T215:T231)*C214` becomes:

```
                    ( * )
                   /     \
              ( SUM )     C214
                 |
        ┌────┬───┴───┬────┐
      T215  T216 ... T230  T231
```

You read it from the bottom up: add up the seventeen cells `T215` to `T231`,
then multiply that total by whatever is in `C214`.

The parser uses an algorithm called **shunting-yard**, invented by Edsger
Dijkstra. It reads the formula left to right and uses the normal rules of
precedence — multiply before add, brackets first — to decide the shape of the
tree. Excel has its own quirks the parser has to respect, for example that
`-2^2` is `4` in Excel and not `-4`.

### 3.4 From tree to graph

Now the trees for all 3,887 formulas get glued into one big **graph**.

A graph is just dots and arrows. The dots are called **nodes**, the arrows are
called **edges**. An arrow from A to B means *"A is used to work out B"*.

Every piece of every tree becomes a node:

| node kind | what it is | example id |
|---|---|---|
| `formula` | a cell with a formula | `Dashboard!L8` |
| `input` | a hand-typed number | `Dashboard!K8` |
| `label` | a text cell | `Historicals!B6` |
| `op` | one operator or function inside a formula | `Dashboard!L8#1:+` |
| `const` | a number written inside a formula | `Dashboard!L8#0:const` |
| `range` | a whole block like `C6:C125`, kept in one piece | `SOFR Curve!C6:C125` |

The odd-looking ids like `Dashboard!L8#1:+` mean "the operator numbered 1 inside
cell `Dashboard!L8`, which is a `+`". Every `op` and `const` node records which
cell it belongs to, in a field called `owner`.

So the single cell `L8` containing `=K8+1` produces **three** nodes and **three**
edges:

```
   Dashboard!K8 ────────lhs──────►┐
   (the input, 2024)              │
                                  ├──► Dashboard!L8#1:+ ──result──► Dashboard!L8
   Dashboard!L8#0:const ──rhs────►┘         (the operator)          (the cell, 2025)
   (the number 1)
```

This is why the file has 8,783 nodes for only 5,448 cells. The extra 3,335 are
the innards of the formulas.

### 3.5 Edges carry meaning, not just direction

Each edge records **which slot** of the operator it feeds. This matters more
than it sounds.

Consider `=A1-B1` and `=B1-A1`. Both have the same two cells and the same
operator. Without slot information, both would look identical as a graph, and
one of them would be wrong. So each edge stores:

- **`role`** — a name for the slot: `lhs`, `rhs`, `condition`, `then`, `else`,
  `summand`, `criteria`, `value`, `fallback`, and so on.
- **`arg_index`** — the slot number: 0, 1, 2...

For `IF(K8=1, "Historical", "Projection")`:

```
   (K8=1)      ──condition──► ┐
   "Historical"──then───────► ├──► IF ──result──► the cell
   "Projection"──else───────► ┘
      role         arg_index
```

### 3.6 Ranges get spread out

When a formula says `SUM(K28:T28)`, the parser does not create one arrow from a
blob called "K28:T28". It creates **ten separate arrows**, one from each cell in
the range, all pointing at the same `SUM` node, all sharing `arg_index = 0`, and
all tagged with `via_range = "K28:T28"` so we know they came from one range.

```
   K28 ─┐
   L28 ─┤
   M28 ─┤
   N28 ─┤
   O28 ─┼──all with arg_index=0, via_range="K28:T28"──► SUM ──► Valuation!C30
   P28 ─┤
   ...  │
   T28 ─┘
```

**Except** — remember that empty cells do not exist in the file. If `M28` were
empty, there would be only nine arrows, not ten. Section 19 explains the damage
this caused.

### 3.7 Guessing what each cell is called

A cell like `Valuation!C30` holds the number `115597226.3`. On its own that is
meaningless. But a human reading the spreadsheet knows what it is, because they
can see the words nearby.

Stage 0 copies that trick. For each cell it looks:

```
                      column header, searching upwards
                              │
                              ▼
              ┌──────────────────────────────┐
     row  ──► │ Revenues │ AED │ ... │ 43,200 │
   label      └──────────────────────────────┘
      ▲              ▲                   ▲
      │              │                   │
  leftmost text   nearest text        our cell
  in the row      to the left
```

It takes the **leftmost** text in the row (the line-item name, `"Revenues"`),
the **nearest** text to the left (a qualifier, usually a currency: `"AED"`), and
the **nearest** text above in the same column (the column header,
`"Projection"`). It joins them with slashes:

> `Revenues / AED / Projection`

The leftmost cell is preferred over the nearest one because in these models the
far-left column holds the real name and the nearer columns hold units.

This label guessing works extremely well. In workbook `0248`, **8,782 of 8,783
nodes** get a label. It turns out to be the single most useful piece of
information in the whole dataset, and stages 7 and 10 both lean on it heavily.

### 3.8 What comes out

Two CSV files (plus JSON, GraphML and an HTML viewer we do not use).

`nodes.csv`, one row per node:

```
id, kind, sheet, coordinate, row, col, owner, op, op_kind, arity,
expr, label, formula, value, array_formula, in_degree, out_degree, in_cycle
```

`edges.csv`, one row per arrow:

```
source, target, role, arg_index, op, cell, ref, via_range, cross_sheet, in_cycle
```

### 3.9 References the parser cannot see: `INDIRECT`

Everything so far assumes a formula names its precedents in plain text, so the
parser can read them straight off. One function breaks that assumption, and it
breaks it badly: **`INDIRECT`** builds its target out of *text assembled at run
time*.

```
   Summary!AV72  =VLOOKUP($D72, INDIRECT("'" & AV$4 & "'!$A:$I"), 9, 0) / 1000
```

`AV4` holds the word `"AGI"`. At calculation time Excel glues it into the string
`'AGI'!$A:$I` and reads column I of the `AGI` sheet — but a *static* parser sees
only the pieces of the string, never the sheet they point at. So the natural
graph records a dependency on `AV4` (the word) and nothing at all on the sheet
the number actually comes from. The trail goes cold.

This is not a corner case. The workbook `0251` has **744** such formulas, one
for every division tab (`AGI`, `LRL`, `CB1`, …). Left unresolved, every one of
those tabs looked like disconnected junk, and stage 17 wiped their hand-typed
inputs out of the rebuild file.

The fix: when an `INDIRECT` argument is made only of things we already know —
string constants, `&` concatenations, and single-cell references whose cached
values are in hand — assemble the string ourselves, parse it as a reference, and
draw the real edges. These arrows carry the role `resolved` and a `via_range` so
they are traceable back to the dynamic formula that produced them. On `0251` all
744 resolve, reconnecting the division tabs to the summary sheet that reads them.

When the string is *not* statically knowable (its target depends on a value only
known mid-calculation), the formula is flagged rather than silently dropped, so a
genuine hole is visible instead of masquerading as a clean parse.

### 3.10 Cells no formula mentions

The walk in section 3.4 only makes a node for a cell some formula refers to. That
is fine for the maths, but it means a **hand-typed number nothing references
never enters the graph at all** — and a cell the graph has never heard of cannot
be classified, so stage 17 cannot know to keep it. Pasted subtotals like
`AGI!J13` are exactly this: a person typed the number in, but every formula reads
the monthly columns beside it rather than the total.

So after the walk, every populated non-formula cell that still has no node gets
one, as an `input`. An unreferenced typed value is a supplied number whether or
not the model happens to read it here; making it a node lets the later stages
decide honestly whether it is needed, instead of losing it by omission.

### 3.11 Whole-column ranges

A reference like `$A:$I` nominally spans a million rows. Expanding that literally
would be absurd, so a range that resolves to more populated cells than
`--max-range-expand` collapses to a single `range` node — but its coordinates are
now clamped to the **populated bounding box** (`AGI!A3:I449`, not `AGI!A1:I1048576`).
Downstream stages expand range nodes cell by cell, and the tight box keeps that
from ballooning.

---

## 4. What the graph actually looks like

Four workbooks were used throughout. Here is what stage 0 produced for each.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| what kind of model | DCF valuation | acquisition model | fund / DCF | LBO |
| nodes | 8,783 | 8,289 | 30,769 | 5,440 |
| edges | 9,895 | 9,584 | 43,646 | 7,205 |
| real cells | 5,448 | 4,897 | 11,502 | 2,501 |
| `formula` cells | 3,887 | 4,614 | 9,594 | 1,976 |
| `input` cells | 1,380 | 149 | 1,902 | 506 |
| `label` cells | 181 | 134 | 6 | 19 |
| `op` nodes | 2,684 | 2,643 | 15,204 | 2,262 |

Something worth noticing: **the maths in these files is simple.** Across all
four workbooks there are only **24 different functions** and **14 different
operators**. The twelve most common functions cover 98.4% of all use:

```
   SUM      3237  ████████████████████████████████████████  43.1%  (running total)
   IF        870  ███████████                               54.6%
   MIN       621  ████████                                  62.9%
   OFFSET    612  ████████                                  71.0%
   MAX       605  ███████                                   79.1%
   COUNTIF   540  ███████                                   86.2%
   IFERROR   386  █████                                     91.4%
   EOMONTH   290  ████                                      95.2%
   AVERAGE   108  █                                         96.7%
   YEAR       60                                            97.5%
   CHOOSE     38                                            98.0%
   ROUND      29                                            98.4%
```

There is no statistics, no matrix algebra, no simulation. It is addition,
multiplication and `IF`. This is what makes stage 9 — rebuilding the entire
workbook from scratch — a realistic thing to attempt.

---

## 5. Why the obvious idea fails

Here is the idea everybody has first.

> In a graph, some dots have no arrows coming in. Nothing feeds them, so they
> must be the inputs. Some dots have no arrows going out. Nothing uses them, so
> they must be the outputs. Everything else is the middle. Done.

The vocabulary: a dot with no arrows in is a **source**, a dot with no arrows
out is a **sink**.

It is a good idea. It is also completely wrong in practice. I measured it:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| sources found ("inputs") | 1,845 | 1,029 | 1,908 | 532 |
| sinks found ("outputs") | 1,189 | 1,104 | 959 | 371 |

A model does not have 1,189 answers. It has about fifteen. Something is badly
wrong. There are three separate causes.

### Cause 1 — the same idea repeated across ten columns

A financial model is laid out as a grid of *line items* (rows) against *time
periods* (columns).

```
                2020    2021    2022    2023    2024    2025    2026
   Revenue      1,000   1,100   1,210   1,331   1,464   1,611   1,772
   Costs         (600)   (650)   (705)   (764)   (829)   (899)   (975)
   Profit         400     450     505     567     635     712     797
```

The row "Profit" is **one idea**. But it is twenty-one separate cells in the
graph, each with its own arrows. Counting them separately is like counting the
word "Profit" as twenty-one different words because it appears twenty-one times.

I checked how similar the cells in a row really are, by rewriting each formula
in a form that ignores its position (see section 8.1). Between **79% and 95%**
of rows contain at most two distinct formulas. In other words, a row really is
one or two ideas, copied sideways.

### Cause 2 — the dashboard lies about the topology

Modellers build a summary tab. Its cells do no work; they just point at a real
cell somewhere else:

```
   Dashboard!V10  =  Valuation!C$30
```

That is a **mirror**: a formula whose entire content is "go and fetch that other
cell".

There are a lot of them. In `0248`, **2,091 of 5,448 cells** are mirrors. And
they wreck the topology in the worst possible way:

```
   BEFORE                                     what we want to conclude
   ──────                                     ────────────────────────
   ...  ──►  Valuation!C30  ──►  Dashboard!V10
             ▲                   ▲
             │                   │
   this is the real answer.      this computes nothing at all.
   but it has an arrow OUT,      but it has no arrows out,
   so it is NOT a sink,          so it IS a sink,
   so we would not call it       so we WOULD call it
   an output.                    an output.
```

Exactly backwards, on both counts, for the single most important cell in the
model.

### Cause 3 — junk that looks structural

Spreadsheets are full of cells that exist for the reader, not the maths:

- unit stamps: a cell whose value is just `"AED"` or `"000$"`
- titles: `"LBO"`, `"($ in Thousands)"`
- year axes: `=K8+1` repeated sideways to draw `2024, 2025, 2026...`
- reconciliation checks: `"Check - Balance Sheet"`, which should always be zero

To the graph these look exactly like real values. `"Check - Balance Sheet"` is a
`SUM` on a summary sheet with nothing downstream — a textbook output by every
structural measure. It is not an output. It exists to prove the model adds up.

### The plan

Fix all three, in order:

```
   Cause 1  →  stop looking at cells, look at BANDS         (stage 3)
   Cause 2  →  lift the mirrors out of the graph            (stage 4)
   Cause 3  →  type the cells, and score rather than assume (stages 2 and 7)
```

---

## 6. Stage 1 — Collapsing the AST

The first job is to get rid of the formula innards. We do not need to know that
`L8` contains a `+` node; we need to know that `L8` depends on `K8`.

Every `op` and `const` node has an `owner` field pointing at its cell. So: take
every arrow, replace each end with its owner, throw away arrows that now point
from a cell to itself.

```
   BEFORE (AST level)                       AFTER (cell level)

   K8 ──lhs──►┐                             K8 ──► L8
              ├──► L8#1:+ ──result──► L8
   const(1) ──┘                             (the const vanishes — it lives
                                             inside L8, so its arrow became
                                             L8 → L8 and was dropped)
```

The self-pointing arrows are not errors. Two `op` nodes inside the same cell are
both owned by that cell, so the arrow between them collapses to a loop and is
correctly discarded. In `0248` this discards 1,223 `*` arrows, 536 `IF` arrows
and so on — all of them internal plumbing.

Result: **8,783 nodes become 5,448 cells.**

### One thing that had to be repaired here

`range` nodes are the exception. A `range` node stands for a whole block like
`SOFR Curve!C6:C125`, and — unlike everything else — it has **no arrows coming
in from its own member cells**. It also has no `owner`.

So collapsing would silently delete it, and with it the dependency:

```
   BROKEN                                   FIXED

   SOFR Curve!C6 ... C125                   SOFR Curve!C6 ─┐
        (no arrows to the range node)                 C7 ─┤
                                                     ...  ├──► LBO!G144
   [SOFR Curve!C6:C125] ──► XLOOKUP ──► LBO!G144     C125 ─┘
        ▲
        └── owner is empty, so this whole
            arrow gets dropped, and LBO!G144
            appears to depend on nothing
```

The fix: whenever an arrow starts at a `range` node, work out which cells the
range covers and draw an arrow from each of them instead.

This was not a cosmetic bug. It meant an entire dependency was missing from the
graph, which broke the ordering in stage 9 and stopped `0450`'s circular block
from ever settling down.

---

## 7. Stage 2 — Typing every cell

Now label every cell with what sort of thing it holds. Five types:

```
   ┌──────────┬────────────────────────────────┬──────────────────────┐
   │ NUMERIC  │ a real number                  │ 115597226.3          │
   │ AXIS     │ a year or a date               │ 2025, 2024-12-31     │
   │ UNIT     │ a currency or unit stamp       │ "AED", "000$", "x"   │
   │ TEXT     │ any other words                │ "Base Case"          │
   │ BLANK    │ nothing                        │                      │
   └──────────┴────────────────────────────────┴──────────────────────┘
```

The rules, applied in order:

1. Is it a `label` cell? → **TEXT**
2. Is the value empty? → **BLANK**
3. Does the value look like `2024-12-31T00:00:00`? → **AXIS** (a date)
4. Is the value not a number at all? → **UNIT** if it is a known unit word,
   otherwise **TEXT**
5. Does the label mention "year", "date", "period", "month" or "quarter"?
   → **AXIS**
6. Is it a whole number between 1900 and 2200 whose formula is `=<cell>+1`?
   → **AXIS** (a year counter)
7. Otherwise → **NUMERIC**

Rule 3 is there because Excel stores dates as text like `2024-12-31T00:00:00` in
this export. Without it, every date in workbook `0450` would have been filed as
meaningless text.

Only **NUMERIC** cells can be outputs. That single restriction removes an
enormous amount of noise — including, as it turns out, the highest-scoring
candidate in two of the four workbooks, which was a cell containing the word
`"AED"` that 559 dashboard cells happened to point at.

Two more flags get worked out here, both of which matter later:

- **`is_mirror`** — the cell has exactly one incoming arrow and its role is
  `identity`. That means the formula is nothing but a reference: `=Valuation!C30`.
- **`is_literal`** — the cell's formula is just a written-in number, like `=5`.
  Nothing feeds it, so it is really an input in disguise.

---

## 8. Stage 3 — Bands: the key idea

This is the heart of the pipeline.

### 8.1 Making formulas comparable

In `0248`, `Calculations!K238` contains `=K234` and `L238` contains `=L234`. As
text these are different. As *ideas* they are identical: "take the cell four
rows above me".

To see that, rewrite every formula in **R1C1 form**, where references are
described by how far away they are rather than by their name:

```
   cell   formula      R1C1 form      meaning
   ────   ───────      ─────────      ───────
   K238   =K234        =R[-4]C[0]     four rows up, same column
   L238   =L234        =R[-4]C[0]     four rows up, same column   ← identical
   M238   =M234        =R[-4]C[0]     four rows up, same column   ← identical
```

`R[-4]` means "four rows up from me". `C[0]` means "my own column". A dollar
sign in the original means the reference is locked, so it is written as a plain
absolute number instead. Further along the same row, `O238` holds
`=IF($C$214, SUM(O235:O237), O234)`, which becomes:

```
   =IF( R214C3, SUM(R[-3]C[0]:R[-1]C[0]), R[-4]C[0] )
```

`C214` was locked in both directions, so it survives rewriting as the outright
`R214C3` rather than as an offset.

Two cells with the same R1C1 form are doing the same job.

### 8.2 What a band is

> A **band** is a run of side-by-side cells in one row that have the same kind
> and the same R1C1 formula.

Every numeric cell in row 238 is a formula, and they all sit in one row, yet
they do not make one band:

```
   Calculations row 238 — "Admin & general expenses"

            D238    K238    L238    M238    N238    O238   P238  ...  T238
            ─────   ─────   ─────   ─────   ─────   ─────  ─────      ─────
   kind     form    form    form    form    form    form   form       form
   R1C1     R[-1]   R[-4]   R[-4]   R[-4]   R[-4]   IF(…   IF(…       IF(…
            C[0]    C[0]    C[0]    C[0]    C[0]
            └───┘   └───────────────────────────┘   └─────────────────────┘
            band 1            band 2                        band 3
            unit         copies of row 234            projection formula
```

Instead of eleven separate dots in the graph, that row contributes three.

The effect is large:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| cells | 5,448 | 4,897 | 11,502 | 2,501 |
| bands | 1,739 | 1,242 | 1,438 | 845 |
| shrink | 3.1× | 3.9× | 8.0× | 3.0× |

### 8.3 Why bands and not just whole rows

Grouping by whole rows would be simpler. It would also destroy the answer.

Look at `Valuation` row 23 of `0248`, the cost-of-equity discount factor:

```
   Valuation row 23:

      B23         D23    O23     P23      Q23      R23      S23      T23
      ──────────  ───    ───    ──────   ──────   ──────   ──────   ──────
     "Cost of      "x"    1     0.9050   0.8190   0.7412   0.6707   0.6070
      equity -            │       └─────────── formulas ────────────┘
      discount            │        =O23/(1+$C$21), carried rightwards
      factor"          typed in
                     (kind = input)
```

`O23` is the base year, where someone simply typed a 1. Everything to its right
is that number discounted one more year at a time.

**That boundary, in the middle of the row, is the input frontier.** It is the
exact line between "supplied" and "worked out", and the pipeline does put `O23`
in the input bucket and `P23:T23` in the middle bucket.

Banding finds it for free, because the two halves have different kinds and
different formulas, so they land in different bands:

```
   whole-row grouping:   [ O23 P23 Q23 R23 S23 T23 ]     ← boundary destroyed
   band grouping:        [O23] [P23 Q23 R23 S23 T23]     ← boundary preserved
                         input            middle
```

This is not a rare case: in `0248`, **252 of 657 rows** mix supplied and
computed cells. They do not all split the way row 23 does, though, and the
commonest shape is in fact the reverse of it.

| shape of the row, left to right | rows |
|---|---|
| formula, then inputs | 223 |
| formula, then inputs, then formula | 23 |
| inputs, then formulas | 5 |
| alternating | 1 |

Those 223 are mostly rows like `Historicals` row 5, where the unit stamp on the
left is computed — `D5` is `=BC`, which resolves to `"AED"` — while the ten
year-columns `K5:T5` beside it are all hand-entered revenue figures. The split
falls in a different place, but the lesson is the same: take the row whole and
you fold a presentation cell into a band of raw inputs.

### 8.4 Naming and splitting

A band is named after the cells it covers: `Historicals!K5:T5`, or just
`Valuation!C30` when it is a single cell. If two matching groups in the same row
are not touching, they become separate bands, because a gap usually means a
genuine break.

### 8.5 Stage 3½ — Constants hiding inside formulas

`Calculations!N538` in `0248` holds `=1134847*$C$519`. The flag it references
makes the cell a non-source, so by topology alone it would land in **middle** —
and the `1134847`, the number someone actually typed, would vanish from the
input set entirely. A rebuilder handed "the inputs" could never reproduce the
cell.

So after banding, every non-mirror formula band is scanned for **notable
literals**, and each one is promoted to a synthetic source band of its own:

```
   Calculations!N538#lit=1134847          a band with no cells of its own,
        │                                 kind "literal", one edge out
        ▼
   Calculations!N538   =1134847*$C$519    the host, still a formula band
```

Nothing downstream is special-cased. The synthetic band is a source, so the
ordinary frontier rule (stage 8) files it as an **input** whenever its host
sits in the output cone; the host cell itself stays **middle**, because
multiplying by the flag genuinely is work the model does. The self-closing-cone
proof survives untouched, since literals are sources by construction.

*Notable* means the constant reads as an assumption rather than plumbing: any
non-integer (rates, multiples), or an integer of 100 or more (balances,
hardcoded line items), excluding a small boring set — `0`, `1`, `2`, `12`,
`100`, `365`, `1000`, `10000` and friends — that is arithmetic, not data.

In `0248` this catches 8 constants, 6 of them inside the cone: three hardcoded
working-capital balances on `Calculations` (`N532`, `N538`, `N549`), the two
components of `Historicals!N38`'s `=-16800000-4026`, and the terminal-value
anchor in `Valuation!V10`. The other three workbooks come up clean — their
modellers kept assumptions in cells, where they belong.

---

## 9. Stage 4 — Bypassing the mirrors

Now deal with cause 2 from section 5.

A **mirror band** is a band where every cell is a pure reference. We take those
bands out of the working graph and reconnect around them.

Projected revenue in `0248` travels exactly this way. It is worked out once on
`Calculations`, then relayed across two sheets before anything uses it again:

```
   BEFORE

   Calculations!O38             =IF($C$32,SUM(O35:O37),O33)    52,997,175   ← real work
        │
        ▼ mirror
   'Financial Statements'!O6    =Calculations!O$38             52,997,175
        │
        ▼ mirror
   Dashboard!K12                ='Financial Statements'!O$6    52,997,175
        │
        ▼
   Dashboard!V23                =(V22/K12)*365                      41.46   ← real work


   AFTER

   Calculations!O38 ──────────────────────────────────────────► Dashboard!V23

   recorded separately:
   Calculations!O38 was copied onto 3 presentation bands
```

The same number, 52,997,175, sits in the first three cells; only the last one
does any arithmetic. After the bypass `Calculations!O38` is correctly the cell
that feeds `Dashboard!V23`, and the two relay cells that compute nothing are
correctly out of the running.

The same thing happens at the other end of the model, where `Valuation!C30`
("Equity value (DDM)", one of the chosen outputs) is copied onto
`Dashboard!V10` and `Valuation!C87` and then used by nothing at all. Bypassing
those two makes `C30` correctly a sink.

### The mirrors are not thrown away — they are evidence

Here is the nice part. When a modeller copies a number onto the dashboard, they
are telling you something: *this number matters*. It is their own annotation of
what the model is for.

So each mirror pays a compliment back to the real cell it reflects. We count
those compliments as **`mirror_fanin`**, and stage 7 uses it as a scoring
signal. Roughly: *how many times did the author think this was worth showing?*

Mirrors are re-labelled `presentation` and reported separately.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| bands (incl. stage 3½ literals) | 1,747 | 1,242 | 1,438 | 845 |
| of which mirrors | 991 | 788 | 330 | 148 |
| working bands left | **756** | **454** | **1,108** | **697** |

In `0262`, 63% of all bands were presentation. The real model is much smaller
than it looks.

---

## 10. Stage 5 — Contracting the loops

So far everything has been a **DAG** — a *Directed Acyclic Graph*, meaning you
can never follow the arrows in a circle. That property is what lets you compute
things in a sensible order.

Financial models break it on purpose.

### Why a spreadsheet would contain a circle

In a leveraged buyout model:

```
        ┌──────────────────────────────────────────────┐
        │                                              │
        ▼                                              │
   cash at start of year                               │
        │                                              │
        ▼                                              │
   average cash balance                                │
        │                                              │
        ▼                                              │
   interest earned on cash                             │
        │                                              │
        ▼                                              │
   profit ──► cash at end of year ─────────────────────┘
```

Interest depends on your cash. Your cash depends on your interest. There is no
starting point. This is called a **circular reference**, and Excel handles it
with a setting called *iterative calculation*: guess, recalculate, repeat until
the numbers stop moving.

Workbook `0450` has exactly this. The loop is **186 cells** long.

### Handling it

Use **Tarjan's algorithm** to find every group of nodes that can all reach each
other — a *strongly connected component*, or SCC. Squash each group into one
node:

```
   BEFORE                          AFTER

   A ──► B ──► C                   A ──► [ B C D ] ──► E
         ▲     │                         a single node,
         │     ▼                         which we treat as
         └──── D ──► E                   one indivisible block
```

Now the graph is a DAG again and can be ordered.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| working bands | 756 | 454 | 1,108 | 697 |
| after contracting | 756 | 429 | 973 | 664 |
| biggest loop | none | 18 bands | small | **30 bands (186 cells)** |

### Depth

With a DAG we can measure **depth**: the length of the longest chain of arrows
from any starting point down to this node. Depth is a rough measure of "how far
into the calculation are we", and it feeds the output scoring.

`0450` reaches depth **133**, which reflects how long an LBO debt schedule is.

---

## 11. Stage 6 — Islands

Not all of a workbook is connected. Split the graph into **islands** — groups
where you can get from any node to any other, ignoring arrow direction.

```
        ┌───────────────────────────────┐   ┌───────┐   ┌───┐
        │                               │   │       │   │   │
        │      the main model           │   │ small │   │ 2 │  ...
        │      (555 of 756 bands)       │   │ side  │   │   │
        │                               │   │ calc  │   │   │
        └───────────────────────────────┘   └───────┘   └───┘
             island 0                        island 1    island 2
```

Every workbook has one dominant island and a long tail of little ones — leftover
scratch calculations, unused tables, orphaned notes.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| islands | 145 | 141 | 14 | 29 |
| main island (bands) | 555 | 310 | 1,056 | 598 |
| share of working bands | 73% | 68% | 95% | 86% |

Being on the main island is a small positive signal for being an output.

---

## 12. Stage 7 — Choosing the outputs

Inputs can be found by pure logic (stage 8). Outputs cannot. There is no
structural fact that separates "the answer" from "a subtotal near the end". It
is a judgement about meaning.

So instead of a rule, we use a **score** built from eight signals, and then let
a human confirm it.

### The eight signals

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │ SIGNAL                WHY IT SUGGESTS "OUTPUT"                  POINTS │
  ├────────────────────────────────────────────────────────────────────────┤
  │ is a sink             nothing uses it, so it is the end          +3.0  │
  │ mirror_fanin          the author copied it onto summaries     +0.6 each│
  │                                                              (max +3.0)│
  │ on a summary sheet    the copies live on a Dashboard             +2.0  │
  │ strong keyword        "IRR", "MOIC", "NPV", "equity value"       +2.5  │
  │ weak keyword          "EBITDA", "margin", "cash flow"            +0.8  │
  │ output-ish sheet      sheet is Dashboard / Valuation / DCF       +1.5  │
  │ depth                 far along the chain of calculation    up to +1.5 │
  │ collapses a series    one number summarising a whole row         +1.5  │
  │ on the main island                                               +0.5  │
  ├────────────────────────────────────────────────────────────────────────┤
  │ looks like a check    "Check", "tie out", "error", "control"     −6.0  │
  │ has no label                                                     −1.5  │
  └────────────────────────────────────────────────────────────────────────┘
```

Two of these are worth explaining properly.

**"Collapses a series"** turned out to be the sharpest structural signal in the
whole pipeline. A band that is one cell wide, fed by a band that is many cells
wide, is squeezing a whole timeline into a single figure:

```
   K28   L28   M28   N28   O28   P28   Q28   R28   S28   T28     ten years of
    │     │     │     │     │     │     │     │     │     │      discounted
    └─────┴─────┴─────┴──┬──┴─────┴─────┴─────┴─────┴─────┘      cash flow
                         ▼
                 Valuation!C30  =SUM(K28:T28)                    ONE number:
                                                                 the valuation
```

Nobody collapses ten years into one number unless that number is the point.

**The check penalty** exists because reconciliation cells are perfect
counterfeits. `"Check - Balance Sheet"` in `0248` scored 11.09 — second highest
in the workbook — on being a terminal, named, series-collapsing cell on a
summary sheet. It is not an output. The −6.0 removes it.

### Two hard exclusions

Before scoring, two kinds of band are removed entirely:

1. **Not NUMERIC** — a unit stamp is never an answer.
2. **No arrows coming in** — *an output is something the model works out.* A
   band nothing feeds is a given, no matter how prominently it is displayed.

Rule 2 alone removed a batch of false positives in `0248`: historical revenue
rows that scored well only because the dashboard displays them. They are inputs
being shown off, not outputs.

Literal bands — `=5` wearing formula clothes, and the synthetic literal sources
from stage 3½ — are barred the same way. A hardcoded constant is a given by
definition.

### The result

For `0248`, 528 bands scored above zero. The top of the list:

```
   rank  score  band              label                          why
   ────  ─────  ────              ─────                          ───
    1    13.90  Valuation!C103    Equity IRR                     sink, keyword, collapse, depth 22
    2    11.09  Valuation!C30     Equity value (DDM)             copied ×2, collapse, depth 16
    3     9.66  Valuation!C65     Equity value (FCFEE)           copied ×2, keyword
    4     8.91  Valuation!C82     Equity value (EV/EBITDA)       copied ×2, keyword
    5     8.39  Valuation!I10     Terminal value                 sink, keyword
    6     8.32  Valuation!I14     Net cash flows to equity       sink, collapse
    7     8.12  Valuation!I8      Free Cash Flow to Equity       sink, collapse
    8     7.89  Valuation!C62     Enterprise value               keyword, collapse
    9     7.64  Valuation!C43     Cost of debt                   sink, collapse
   10     7.14  Valuation!C79     Enterprise value               keyword, collapse
```

That is a valuation model's answer sheet, in the right order.

### The human in the loop

Anything scoring 6.0 or more is auto-included, and the top 40 are written to an
editable file, `curation.toml`:

```toml
[[output]]
band = "Investment!C63"
sheet = "Investment"
label = "Equity IRR"
score = 11.47
include = true
name = "Equity IRR"
# sink=True mirror_fanin=1 strong_term=True scalar_collapse=True depth=21
```

Change `include` to `false`, re-run, and everything downstream rebuilds from
your choice. The scores stay visible as comments so you can see what you
overrode. An optional `--llm` flag hands the same shortlist to a language model,
which rewrites the same file — so the automatic and manual paths are literally
the same interface, and nothing later needs to know which one ran.

---

## 13. Stage 8 — Cutting the four buckets

Now the actual segmentation, and it is short, because all the hard work is done.

### Two ideas

**Ancestors** of a node: everything that can reach it by following arrows
forwards. Everything it depends on, however indirectly.

**Descendants** of a node: everything it can reach. Everything that depends on
it.

### The cone

Take all the chosen outputs. Collect all their ancestors. Call it the **cone**.

```
                 ┌─────────────────────────────────────┐
                 │              THE CONE               │
                 │   everything the outputs depend on  │
                 │                                     │
    ○ ─────────► ●─────►●──────►●──────►●              │
    ○ ─────────► ●─────►●──────►●───┐   │              │
    ○ ─────────► ●─────►●──────►●───┴──►★  ◄── OUTPUT  │
    ▲            │                      │              │
    │            └──────────────────────┘              │
  sources                                              │
  INSIDE the                                           │
  cone = INPUTS                                        │
                 └─────────────────────────────────────┘

    ○ ─────────► ●──────►●        outside the cone entirely
    ▲            ▲
    │            └── DEAD: fed by inputs, reaches no output
    └── UNUSED INPUT: a given that no chosen output needs
```

### The definitions

```
   INPUT   =  nodes in the cone with no arrows coming in
   OUTPUT  =  the chosen outputs
   MIDDLE  =  everything else in the cone
   SCAFFOLDING = everything not in the cone
```

One consequence of stage 3½ falls out here for free: a constant hardcoded
inside a formula is a source, so when its host sits in the cone the constant
lands in **input** while the host cell stays **middle** — which is exactly the
split the formula deserves. A promoted constant no chosen output needs becomes
`unused_input`, like any other ignored assumption.

### Why this is self-closing

Here is the neat part, and it is worth being precise about.

*Could an output depend on something that is not in our input set?*

No, and you can prove it. Suppose some node **X** helps produce an output. Then
X is an ancestor of that output, so X is in the cone. Now walk backwards from X.
Every step stays in the cone, because an ancestor of an ancestor is an ancestor.
The graph is finite and acyclic (we contracted the loops in stage 5), so the
walk must stop. It stops at a node with no arrows in — which is in the cone,
which is therefore in our input set.

So **the input frontier cannot have holes**. It is not a heuristic; it follows
from how it is defined.

The code still checks, because a proof about the design is not a proof about the
implementation. The check is called `unfed_components` and it came out **zero**
for all four workbooks.

### Scaffolding is not a bin — it is a diagnostic

The leftovers get split into three, because each means something different:

| class | what it is | what it tells you |
|---|---|---|
| `unused_input` | a source no chosen output needs | an assumption nobody uses, or an output you did not select |
| `dead` | fed by inputs, reaches no output | an abandoned branch of the model |
| `detached` | touches neither end | notes, scratch work, formatting |
| `presentation` | the mirrors from stage 4 | the summary layer |

If `dead` were huge, it would mean the output selection had missed something.
Reporting it separately turns the leftovers into a quality measure instead of a
dumping ground.

### The result

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| **input** | 266 | 51 | 86 | 129 |
| **middle** | 184 | 145 | 392 | 264 |
| **output** | 15 | 16 | 19 | 40 |
| **scaffolding** | 291 | 217 | 476 | 231 |
| — unused input | 173 | 151 | 182 | 48 |
| — dead | 60 | 51 | 134 | 117 |
| — detached | 58 | 15 | 160 | 66 |
| presentation (from stage 4) | 991 | 788 | 330 | 148 |
| **unfed (must be 0)** | **0** | **0** | **0** | **0** |

> **Since sections 3.9–3.11.** These counts describe the run before typed-cell
> adoption. Giving every unreferenced hand-typed cell a node (section 3.10) adds
> real sources, so the current run has more `input` and `unused_input` cells —
> `0248`'s scaffolding, for instance, rises to 664 (546 of it `unused_input`) as
> the pasted numbers on `Calculations` finally get counted, and `0450`'s `input`
> frontier grows from 129 to 248. `output`, `middle` and the unfed-count guarantee
> are unchanged. `INDIRECT`-heavy workbooks like `0251`, invisible before, now
> segment too.

The input sheets it identifies are the right ones without being told: `InputH`
and `Scenario` for `0262`, `Input & Assumptions` and `CoE` for `0449`,
`Assumptions` and `Historical Financials & KPIs` for `0450`.

`0248` is the odd one: 235 of its 266 inputs sit on a sheet called
`Calculations`. That looked wrong, so I checked individual cells. They are
genuinely hand-typed numbers — the modeller hardcoded forecast line items like
"Other income: 200,000 rising to 380,000" directly into the calculation tab
instead of an assumptions sheet. The segmentation is right; the workbook is just
built untidily. **That is a finding, not a bug** — and it is exactly the kind of
thing this pipeline should surface. The six promoted literals of stage 3½ are
the same habit in its most extreme form: assumptions typed not just on the
wrong sheet, but inside the formulas themselves.

---

## 14. Stage 9 — Proving it, by rebuilding the workbook

Everything so far is an argument. This stage is a test that can fail.

### The test

```
   1.  Take ONLY the input cells. Fill in their saved values.
   2.  Delete every other value. Pretend we never saw them.
   3.  Recompute the entire workbook from its formulas.
   4.  Compare our answers to Excel's saved answers.
```

If the outputs come back right, the input set is **provably sufficient** — you
really can rebuild the model from it. If they come back wrong, either the
frontier has a hole or the evaluator is broken. Either way we find out.

```
        input cells                    everything else
        ───────────                    ───────────────
      ┌─────────────┐                ┌─────────────────┐
      │ 43,200      │                │      ?          │
      │ 8.0%        │   ────────►    │      ?          │  ───► recompute
      │ 9.0x        │                │      ?          │        in order
      └─────────────┘                └─────────────────┘
       seeded from                    blanked out
       saved values                                            │
                                                               ▼
                                                    ┌──────────────────┐
                                                    │  our answer      │
                                                    │  115,597,226.30  │
                                                    └────────┬─────────┘
                                                             │ compare
                                                    ┌────────▼─────────┐
                                                    │  Excel's answer  │
                                                    │  115,597,226.30  │
                                                    └──────────────────┘
```

### Building an Excel

This means writing a small Excel. It has to handle 14 operators and 24
functions, and it works straight off the AST from stage 0.

For each cell: find the one incoming arrow, which points at the top of its
formula tree. Walk down the tree. A `const` node gives a number. A cell node
gives that cell's value. An `op` node gathers its arguments by `arg_index` and
applies the operation.

Some parts needed real care:

**Order.** Compute a cell only after everything it depends on. Find the cell-level
SCCs, sort them topologically, and work through them.

**Circles.** For a contracted loop, do what Excel does: set everything to zero
and go round and round until the numbers stop moving. `0450`'s 186-cell loop
settles after **67 passes**, to a relative change below 1e-12. The cells are
relaxed in sorted order, so that count is the same on every run — left to its
natural order it wandered between 300 and 600 passes from one run to the next.

**`OFFSET`.** Used 612 times. `OFFSET(G36, Case, 0)` means "start at G36 and move
down by however many rows the scenario selector says". The target depends on a
*value*, so it cannot be known in advance. We resolve it at run time, and fall
back to reading the original `.xlsx` if the target cell is not in the graph.

**Dates.** Excel counts days since 30 December 1899. `EOMONTH` and `YEAR` need
that, and so does `XIRR`.

**`IRR` and `XIRR`.** These have no formula; you have to solve for the rate
numerically. Newton's method starts from the supplied guess (0.1 when omitted),
with bisection as a backstop. XIRR requires equally sized cash-flow and date
vectors; no trailing entries are silently dropped.

**Nothing is guessed.** If the evaluator meets something it does not understand,
it returns a marker called `Unresolved` which spreads to everything downstream
and gets reported. It never quietly substitutes zero.

### What actually happened

Seeding **nothing at all** except the hand-typed leaves, the evaluator rebuilds:

| workbook | formulas rebuilt | correct |
|---|---|---|
| 0248 | 3,887 | **3,887 (100%)** |
| 0262 | 4,614 | **4,614 (100%)** |
| 0449 | 9,594 | **9,594 (100%)** |
| 0450 | 1,976 | **1,976 (100%)** |
| **total** | **20,071** | **20,071 (100%)** |

Every formula in all four workbooks, to within one part in a million, with zero
unresolved and zero unsupported functions.

Then the real test, seeding only the **input frontier**:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| input cells seeded | 1,348 | 123 | 117 | 431 |
| output cells checked | 20 | 272 | 23 | 58 |
| output cells correct | **20** | **272** | **23** | **58** |
| middle sample checked | 400 | 400 | 400 | 400 |
| middle sample correct | **400** | **400** | **400** | **400** |
| verdict | **PASS** | **PASS** | **PASS** | **PASS** |

### The anti-cheating check

There is one way this test could fool itself. Some cells cannot be computed by
anybody — hand-typed numbers, labels, formulas that are just `=5`. Those have to
be seeded. But if such a cell sat *inside* the output cone, an output would have
been handed a saved answer instead of recomputing it, and the test would pass
without meaning anything.

So the pipeline computes the ancestors of every output cell and checks that none
of the extra seeded cells are in there.

**It failed the first time it ran.** 49 cells in `0248`, 234 in `0262`.

The culprits were formulas like `=O604` where `O604` is empty. Empty cells do
not exist in the file (section 3.1), so no arrow was drawn, so the cell looked
uncomputable and got seeded.

But Excel has a clear answer for a reference to an empty cell: **zero**. I
checked all 736 such cells across the corpus — every single one has a saved
value of 0 or blank, confirming it. So they are now computed as 0 rather than
seeded, and the leak disappears honestly rather than by relaxing the check.

---

## 15. Stage 10 — Lineage

For each output, write down the full chain of calculation behind it. Two
resolutions.

### Line-item level — the readable one

Take the output, collect its ancestors, sort them into dependency order, and
print them. `Equity IRR` in `0248` comes out as **454 steps**, of which 266 are
inputs and 178 are intermediate. The last few:

```
 428 | middle | WACC                          | Valuation!C50      | =R[-4]C*R[-3]C+R[-2]C*R[-6]C*(1-R[-1]C)
 430 | middle | Free Cash Flow to Equity      | Valuation!O8:T8    | =SUM(R[-2]C:R[-1]C)
 432 | middle | WACC - discount factor        | Valuation!P52:T52  | =R[0]C[-1]/(1+R50C3)
 445 | middle | Net cash flows disc.          | Valuation!K28:T28  | =R[-2]C*R[-1]C
 447 | output | Equity value (DDM)            | Valuation!C30      | =SUM(R[-2]C[8]:R[-2]C[17])
 449 | output | Equity value                  | Valuation!C90      | =CHOOSE(R[-4]C,R[-3]C,R[-2]C,R[-1]C)
 452 | middle | Cash flow                     | Valuation!O102:T102| =SUM(R[-3]C:R[-1]C)
 453 | output | Equity IRR                    | Valuation!C103     | =IRR(R[-1]C[12]:R[-1]C[17])
```

Read bottom-up and it is a textbook DCF: work out the cost of capital, discount
the cash flows, sum them to a value, then solve for the return. This is a
derivation a human can actually follow, and it is **complete** — nothing is cut.

### Cell level — the exact one

The same thing without the band abstraction: every individual cell, its formula,
its recomputed value, its saved value, and its precedents. `Valuation!C103` has
**2,495 ancestor cells**. The Markdown shows the first 150 for readability;
`lineage.json` holds all of them.

### Why two levels

The band trace answers *"how does this model work?"* — and 454 readable lines
can answer that. The cell trace answers *"where exactly did this number come
from?"* — and for that you need all 2,495.

---

## 16. Stage 11 — The files that come out

Everything lands in `seg_out/<workbook>/`:

```
   seg_out/0248/
   │
   ├── segments.json           the segmentation itself: which bands are input,
   │                           middle, output, scaffolding; islands; embedded
   │                           constants; the full verification report
   │
   ├── bands.csv               one row per band, with bucket, sheet, span,
   │                           depth, island, mirror_fanin, R1C1 pattern;
   │                           stage 3½ literals appear as kind "literal"
   │
   ├── output_candidates.csv   every scored candidate, ranked, with each
   │                           scoring signal in its own column
   │
   ├── curation.toml           THE EDITABLE ONE. include flags and names.
   │                           everything downstream reads only from here
   │
   ├── lineage/                one Markdown derivation per output
   │   ├── equity-irr-valuation.md
   │   ├── equity-value-ddm-valuation.md
   │   └── ...
   │
   └── lineage.json            the same, complete and machine-readable
```

And the code:

```
   xl_segment.py          the command-line entry point, orchestrating everything
   xl_seg/
     model.py             load nodes.csv and edges.csv, index them
     project.py           stage 1-2: collapse the AST, type the cells
     bands.py             stage 3-3½: the band quotient, literal promotion
     condense.py          stage 4-6: mirrors, loops, islands
     frontier.py          stage 7-8: score outputs, find the input frontier
     partition.py         stage 8: cut the four buckets
     evaluate.py          stage 9: the Excel evaluator
     lineage.py           stage 10: derivation traces
     adjudicate.py        the optional LLM pass
     emit.py              stage 11: write everything out
   xl_input_mask.py       section 17: the inputs-only workbook
```

Running it:

```bash
python3.12 xl_segment.py 0248 0262 0449 0450     # all four
python3.12 xl_segment.py 0248 --llm              # let an LLM pick the outputs
python3.12 xl_segment.py 0248 --recurate         # discard hand edits, re-score
python3.12 xl_segment.py 0248 --threshold 8.0    # be pickier about outputs
```

---

## 17. Handing back an inputs-only workbook

Everything so far has been description: files that say what the model is made of.
This turns the verdict back into a spreadsheet. `xl_input_mask.py` writes a copy of
the workbook in which every hand-typed value is present and every cell the model is
meant to *work out* is empty — the file you would hand to someone told to rebuild
the model.

```bash
python3.12 xl_input_mask.py 0248 0262 0449 0450       # -> inputs_out/<id>-inputs.xlsx
```

The point of doing this from the segmentation rather than by hand is that the input
set has already been proved sufficient. Stage 9 recomputed every output from the
frontier alone and got the workbook's own numbers back, so the masked file is known
to contain enough to rebuild everything that was removed. Nothing else offers that
guarantee — *when the proof ran.* Not every workbook gets one: a file whose formulas
use a function the evaluator does not implement (`INDIRECT`, `HLOOKUP`, `MATCH`, …)
is segmented with verification skipped, and then the proof of sufficiency simply is
not there. The rule below is what keeps the mask safe with or without it.

### The golden rule: never blank a hand-typed cell

The mask used to blank any number outside the input frontier. That rested entirely
on the frontier being complete — and section 3.9 is a live example of it *not* being
complete: an `INDIRECT` hole meant a whole sheet of typed inputs looked unneeded, so
the old mask deleted them, and the model could no longer be rebuilt.

The safe rule follows from one fact: **a hand-typed value can never be recomputed.**
A formula cell that is blanked will be rebuilt from the inputs; a typed cell that is
blanked is gone for good. Deleting one is only ever safe under a completeness
guarantee the graph cannot always give. So the mask now blanks **only formula
cells**. Every static, hand-entered value survives, whether or not the graph
believes an output needs it. Minimality is traded away for a guarantee that matters
more: the file can always be rebuilt.

There is exactly one exception. A typed cell whose value is a full-precision
duplicate of a chosen output is a *pasted answer* — keeping it would hand the
rebuild its target. Those are redacted and reported (trivial round numbers are left
alone; they collide by accident, not by paste). In `0449` this catches 49 cells and
in `0450` 98, all of them cells in a sensitivity grid that were pasted copies of an
IRR the model is supposed to compute.

### Two rules that matter

**Surviving formulas are replaced by their cached value, so the output holds no
formulas at all.** This is not tidiness. A kept formula's precedents have just been
blanked, so Excel would recalculate it to zero on open — it would not merely be
wrong, it would look confidently wrong. Freezing the value is the only safe move,
and it makes the file completely static, so `xl/calcChain.xml` is dropped with it.

**Blanked cells keep their style, and the rewrite happens on the sheet XML rather
than through openpyxl.** Round-tripping a workbook through openpyxl silently drops
charts; editing the XML in place means column widths, number formats, conditional
formatting and charts all survive. This borrows the machinery `xl_level_split.py`
already used for its dependency-level snapshots.

### Constants that live inside formulas

The promoted literals of stage 3½ own no cell, so the mask has nothing to keep —
and yet without them the workbook cannot be rebuilt. They get carried across two
ways:

1. **The host cell is kept and frozen** to its cached value, the same treatment
   as any other formula-shaped input, so `Calculations!N538` shows `1,134,847`
   instead of sitting empty. Those cells join the inputs-intact check.
2. **An `Embedded Assumptions` sheet is appended** — sheet, cells, line item,
   exact constant, original formula — because the frozen value alone cannot
   always separate the constants. `Historicals!N38` is `=-16800000-4026`: one
   cell, two distinct hardcodes, and only the sheet records both.

The sheet is injected at the XML level like everything else, so it costs no
charts. Only `0248` gets one; the other three workbooks have nothing to report.

### How much survives

Every hand-typed cell survives now regardless of `--keep` (the golden rule above),
so the setting no longer governs whether *typed* numbers or labels live — they
always do. What it controls is how much **formula-derived** presentation content is
frozen back in:

| Mode | What derived content lives (on top of every typed cell) |
| --- | --- |
| `inputs` | Only the input frontier's frozen values. Derived labels and headers go. |
| `labels` | Also formula-*computed* text — a unit stamp like `=BC` resolving to `"AED"`. Typed labels survived anyway. |
| `headers` | **(default)** Also computed period headers: axis formula cells that read as a year or carry a date format. |

The default needs explaining, because it is the one place where this tool
deliberately overrides the segmentation.

A year row is typically `2020` typed once with `=K1+1` dragged across. Nothing in
the model ever computes off those year *values* — the sheets align by column
position — so the row has no path to any output and hangs off the model as a
satellite. In 0248 the segmenter puts `Historicals!K1` in `unused_input` and
`L1:T1` in `detached`, which is topologically correct and, used as a masking rule,
useless: it strips the period axis off every sheet. A period header is not a value
anyone is meant to derive, so it stays.

What it cannot do is trust the `axis` class wholesale. `axis` is assigned from a
cell's shape, not its role, so it also holds real results — in 0248, keeping all of
it would leak `middle` values like `490,000` from `Calculations` row 450. Only cells
that read as a year (an integer in 1990-2100) or carry a date number format get
through. A sweep of all four workbooks found no period header left behind by that
filter, and no non-period value let in by it.

The honest cost of the default: a handful of *computed* cells labelled `middle` are
frozen back in — 15 in 0262 and 163 in 0450. Every one is a year (`2016`-`2027`) or
a month-end date (`2025-06-30`), computed as `previous + 1`. **No output cell
survives in any of the four workbooks.** Use `--keep labels` to drop those computed
headers; it does not, and cannot, drop hand-typed numbers, which the golden rule
keeps in every mode.

### Checked, not asserted

Every run re-opens what it just wrote and compares it against the original, in the
same spirit as stage 9. Four things would be fatal: a formula surviving and giving
the answer away, a formula-derived number surviving outside the keep set, a
redacted pasted answer surviving, or **any typed cell changing or going missing** so
the workbook can no longer be rebuilt. A run that fails any of them exits non-zero.
Each run also prints the stage 9 verdict (`PASS` / `SKIPPED`) so a missing
sufficiency proof is stated openly rather than assumed away.

```
0248  kept 1957  frozen 945  blanked 2942  redacted 0  ->  0248-inputs.xlsx (144.4KB)
      inputs 1357 cells over 5 sheets; every typed cell preserved
      frontier sufficiency proof (stage 9): PASS
      6 hardcoded formula constants -> sheet 'Embedded Assumptions'
      period headers 56 kept though not inputs
      verified: no formulas, no derived numbers, typed cells intact
```

Across the four workbooks, with the default setting:

| Workbook | Input cells | Embedded constants | Period headers kept | Cells blanked | Redacted | Formulas left |
| --- | --- | --- | --- | --- | --- | --- |
| 0248 | 1,357 | 6 | 56 | 2,942 | 0 | 0 |
| 0262 | 123 | 0 | 87 | 3,883 | 0 | 0 |
| 0449 | 145 | 0 | 136 | 9,458 | 49 | 0 |
| 0450 | 551 | 0 | 253 | 1,733 | 98 | 0 |

The *input* count is still just the frontier — what actually feeds the chosen
outputs — and it varies far more than the workbook sizes do. But the file now keeps
every typed cell besides, so an assumption the frontier happens to ignore is still
present to be found, and the mask no longer stakes the file's rebuildability on the
frontier being complete. On `0251`, whose `INDIRECT` formulas leave verification
skipped, that is the whole ballgame: the earlier frontier-only mask had silently
dropped the hand-typed division tabs (`AGI`, `LRL`, …), and keeping every typed cell
is what puts them back.

---

## 18. Results

### Every stage, every workbook

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| AST nodes | 8,783 | 8,289 | 30,769 | 5,440 |
| AST edges | 9,895 | 9,584 | 43,646 | 7,205 |
| → cells (stage 1) | 5,448 | 4,897 | 11,502 | 2,501 |
| → bands (stage 3) | 1,739 | 1,242 | 1,438 | 845 |
| → after literal promotion (stage 3½) | 1,747 | 1,242 | 1,438 | 845 |
| → working bands (stage 4) | 756 | 454 | 1,108 | 697 |
| → components (stage 5) | 756 | 429 | 973 | 664 |
| max depth | 22 | 23 | 21 | 133 |
| islands / main island bands | 145 / 555 | 141 / 310 | 14 / 1,056 | 29 / 598 |
| **input** | 266 | 51 | 86 | 129 |
| **middle** | 184 | 145 | 392 | 264 |
| **output** | 15 | 16 | 19 | 40 |
| **scaffolding** | 291 | 217 | 476 | 231 |
| verification | PASS | PASS | PASS | PASS |
| full rebuild accuracy | 100% | 100% | 100% | 100% |

The figures above are the original four-workbook run. Sections 3.9–3.11 change the
inputs (typed-cell adoption raises the source counts; `INDIRECT` resolution
reconnects hidden sheets) — see the note under section 13 — but every workbook still
verifies `PASS` and rebuilds at 100%.

Overall shrink from raw graph to decision graph: **8,783 → 756**, about 12×.

### The outputs it found

```
   0248  (DCF valuation)      Equity IRR · Equity value (DDM) · Equity value
                              (FCFEE) · Equity value (EV/EBITDA) · Enterprise
                              value · Terminal value · Free Cash Flow to Equity
                              · Cost of debt

   0262  (acquisition)        Equity IRR · MOIC · Exit equity value · Equity
                              cash flows · Sources of funds · Uses of funds ·
                              EBITDA margin

   0449  (fund / DCF)         Present Enterprise Value (perpetuity method) ·
                              Present Enterprise Value (multiple method) ·
                              Terminal Value · Terminal Value as % of EV ·
                              comparable-company means and medians

   0450  (LBO)                IRR · MOIC · Term · Net Debt / EBITDA · FCCR ·
                              the full IRR and MOIC sensitivity grid
```

These are, in each case, the numbers the model exists to produce.

---

## 19. Bugs found along the way

Building the evaluator flushed out six real defects. All of them affected the
*segmentation* too, not just the arithmetic — a missing dependency is a missing
dependency whichever stage reads it.

**1. Range nodes had no incoming arrows.** `XLOOKUP` against the SOFR curve had
no recorded dependency on it. The topological order was therefore wrong, and
`0450`'s circular block never converged. This was a hole in the graph itself.

**2. Empty operands shifted argument positions.** Because empty cells produce no
arrow, `=J3+1` arrived as a one-argument addition when `J3` was empty. Arguments
are now indexed by slot number rather than packed in order.

```
   BROKEN                              FIXED
   arg list = [ 1 ]                    arg list = [ None, 1 ]
                ▲                                    ▲
        the const slid into                  the empty slot is
        the lhs position                     kept, and reads as 0
```

**3. Paired ranges lost their alignment the same way.**
`XIRR(F235:K235, F229:K229)` with blank cash flows in the middle paired each
amount with the wrong date:

```
   values:  -4396   (blank) (blank) (blank) (blank)  15138
   dates:   2024    2025    2026    2027    2028     2029

   BROKEN pairing:  (-4396, 2024), (15138, 2025)   →  one year   →  244%
   CORRECT pairing: (-4396, 2024), (15138, 2029)   →  five years →   28%
```

Range arguments are now rebuilt from their A1 span rather than from the arrows.

**4. Excel's cancellation rule.** When a `SUM` of values that cancel comes out to
zero, Excel gives exactly `0`. Plain computer arithmetic gives about `5.8e-15`.
Tiny — except it was feeding `IF(x > 0)`, which flipped the branch and changed
three cells in `0449` from 0 to 100.17. The evaluator now snaps a sum to zero
when it is negligible against its own operands, which is what Excel does.

**5. `XIRR` needs Newton's method, not bisection.** A cash-flow series can cross
zero more than once. Bisecting a wide range finds a mathematically valid rate
that Excel would never report. Excel starts from a guess and takes the nearest
root, so now we do too.

**6. Blank dates in `XIRR` must be kept, not dropped.** A blank date is serial 0,
which is 30 December 1899. Discounting a cash flow over 120 years makes it
almost nothing — but once every other flow is discounted that far too, the tiny
stub becomes decisive. `0262` gave 26.6% instead of 20.9% until blanks were kept
as zeros on both sides.

And one defect found after the pipeline was running: **lineage files were
overwriting each other**, because the filename came from the label and a model
can hold several distinct lines called "Enterprise value". `0450` was writing 31
files for 40 outputs. Filenames now fall back to the band reference on a clash.

**7. `INDIRECT` hid whole sheets from the graph.** Found in `0251`, and the worst
of the lot because it was silent. `INDIRECT` names its target as text built at run
time, so a static parse recorded no dependency on the sheet the text points at. The
summary tab reads twelve division sheets through `INDIRECT("'"&AV$4&"'!$A:$I")`,
and every one of those 744 references was invisible — so the division tabs looked
like disconnected scaffolding and their hand-typed inputs (`AGI!J10`, the pasted
totals, all of column J) were dropped from the rebuild file. Two things came out of
fixing it. The graph now resolves an `INDIRECT` whenever cached values pin its
string down (section 3.9), reconnecting the sheets. And the input mask stopped
trusting the graph to be complete at all: it now keeps **every** hand-typed cell
(section 17), so even a dependency the graph still cannot see can no longer cost a
supplied number. The same change surfaced that unreferenced typed cells were never
in the graph to begin with (section 3.10).

---

## 20. Glossary

| term | meaning |
|---|---|
| **AST** | Abstract Syntax Tree. A formula written as a tree instead of a line of text. |
| **band** | A run of side-by-side cells in one row with the same kind and the same R1C1 formula. One line item for one stretch of time. |
| **cached value** | The answer Excel saved in the file. What you see on screen. Our correct answer to check against. |
| **cone** | Every node the chosen outputs depend on, directly or indirectly. |
| **DAG** | Directed Acyclic Graph. Arrows, no circles. |
| **depth** | Longest chain of arrows from any starting point down to this node. |
| **edge** | An arrow. "A is used to work out B". |
| **embedded literal** | A constant hardcoded inside a formula, like the `1134847` in `=1134847*$C$519`. Stage 3½ promotes each notable one to its own source band so the frontier can catch it. |
| **INDIRECT** | An Excel function that builds a cell reference out of text at run time. Its target is invisible to a static parse; stage 0 resolves it from cached values when it can (section 3.9). |
| **resolved edge** | An arrow drawn from an `INDIRECT` target the parser worked out from cached values, rather than one read directly off the formula. |
| **pasted answer** | A hand-typed cell whose value duplicates a chosen output. The mask redacts it so the inputs file cannot hand the rebuild its target. |
| **island** | A group of nodes connected to each other but not to the rest. |
| **input frontier** | The boundary between what must be supplied and what can be worked out. |
| **mirror** | A formula that is nothing but a reference to another cell. `=Valuation!C30` |
| **node** | A dot in the graph: a cell, or a piece of a formula. |
| **R1C1** | A way of writing references by distance instead of by name. `R[-4]C[0]` = four rows up. |
| **SCC** | Strongly Connected Component. A group of nodes that can all reach each other — a circular reference. |
| **shunting-yard** | Dijkstra's algorithm for turning a line of text into a tree, respecting precedence. |
| **sink** | A node with no arrows out. |
| **source** | A node with no arrows in. |
| **topological order** | An ordering where everything comes after the things it depends on. |

---

## 21. `/harbor-orchestrator`

The autonomous entry point for Harbor task creation is the Cursor skill at
`.cursor/skills/harbor-orchestrator/SKILL.md`. Give it a batch name and one or
more workbook IDs. It launches one isolated task agent per workbook, runs those
agents in bounded parallel, and gives every workbook its own final verdict:
`PROMOTED` or `STOPPED`. One failed workbook never cancels or blocks healthy
siblings.

Each task agent owns only its workbook's tracker, immutable generations,
diagnostics, staged bundle, repair budget, and publication state. The default
fleet limits are four concurrent workbook agents and two concurrent
model-heavy operations; the orchestrator lowers those limits when host or
service capacity is constrained.

For a campaign, the root dispatcher sets `HARBOR_MODEL_SLOT_ROOT` to the same
absolute directory in every model worker and `HARBOR_MODEL_SLOTS` to `1` or
`2`. `xl_transport.read_response` holds a shared process lock across the full
request and its bounded transport retries. Exceptions and process death release
the lock. The first caller pins the capacity; inconsistent worker settings fail.
Do not remove or replace that directory or its lock files while the campaign is
running. Custom model clients must use `xl_transport.model_slot()` around their
complete operation. Unconfigured standalone calls have no shared fleet limit.

The current rebuild uses `scripts/harbor_parallel_policy.py` to activate four
fixed workbook lanes. The root retains an exact contract and activation receipt,
proves the old singleton lock and all selected lanes are idle, and pins each
persistent lock inode. A fresh coordinator descriptor or explicitly reviewed
continuation selects its workbook's lane; the existing process guardian keeps
that lock until child cleanup completes. Different workbooks can build and
validate concurrently, while the same workbook cannot run twice. Publication
uses the shared queue lock plus the selected workbook lock. Historical singleton
drivers are retained as evidence and are no longer admitted after activation.

This rebuild keeps the already-pinned model capacity at one. Parallel workbook
execution does not change prepared request identities, semantic attempts, or the
shared model slot directory. Changing those fields to increase throughput would
invalidate the retained request evidence. The fixed-lane contract also does not
renew an expired lease or waive any quality gate.

### Tracker control APIs

`scripts.batch.harbor_tracker` provides closed-schema validation and owned
compare-and-swap updates. `scripts.batch.harbor_tracker_lifecycle` adds
`create_tracker(...)` and `lease_operation(...)` for trusted root code. Both
use the same per-workbook lock, revision and byte-hash checks, and durable
atomic writes. Creation records the original source and requested cohort,
with every financial lane pending.

The root supplies `RootAuthority` and independent intake, source-lineage,
stopped-owner, and failure-note validators as applicable. These are trusted
code callbacks, never model-provided permissions. Lease operations are limited
to acquire, renew, release, and one explicit expired task-agent replacement
after proof that its prior owner stopped. A separately versioned
`harbor_pause_recovery` operation permits one additional recovery when a
coordinator pause outlasted that replacement lease. It requires a root-selected
authorization bound to the exact tracker and continuation request, retained
process-exit evidence, and the workbook's activated lock. It records a new token
and the complete prior history by compare-and-swap; ordinary expired renewal and
second replacement remain rejected. It does not reset any model attempt or
qualify a financial lane. Releasing a `STOPPED` tracker also
requires independently validated matching failure evidence and notes.

Historical pause validation checks the recorded recovery event without imposing
an earlier directory-absence condition on later instruction work. Starting a
new environment recovery still requires the instruction worker to be absent;
this distinction does not relax lease, process-exit or evidence checks.

`scripts.harbor_retirement_history` validates an explicitly selected immutable
retirement seal after registered authoring code changes. It checks archived
bytes at the point of use and retains exact source, notes and released-tracker
identities. This establishes a historical stopped outcome, not current task
qualification. `scripts.harbor_slot_turnover` preserves the existing physical
workbook lock and requires complete predecessor transactions when recovering
an interrupted assignment. Both adapters currently support the original
activated lane's exhausted-dialogue stop only; promoted tasks and successor
retirement require separate supported transitions.

These APIs have no CLI and do not dispatch agents. The installed
`scripts.batch.harbor_lane_lifecycle.HarborLaneLifecycle` facade runs the fixed
owning validators in canonical order for lane completion, failure recording,
archival before mutable repairs, and final readiness transitions. It preserves
consumed budgets and checks exact revisions, leases, notes, and artifact hashes.
Clearing a failure requires all affected gates and complete current readiness.
Explicit migration of historical tracker shapes remains separate work; existing
trackers are not rewritten automatically.

Run actual model and container operations before the final readiness transaction.
`harbor_runtime_readiness.run_runtime` captures immutable inputs, exact Docker
images and commands, live retrieval, startup, and grader evidence; its read-only
validator checks that observation again before publication. The runtime grader
smoke checks wiring. Independent source reconstruction establishes solvability.
`harbor_model_executor` retains exact requests, raw responses and text, completion
journals, and three bounded transport retries for definitive transient HTTP
errors. Uncertain completion remains unresolved and cannot be replayed by
changing an output directory or attempt UUID. The root dispatcher must validate
the owning lane's actual remaining semantic budget before preparing each call.
None of these operations publishes a task; immutable publication and release
compare-and-swap remain serial and separate.

The installed producer APIs prepare concrete evidence before those transitions:
`harbor_normalization_producer` imports the complete audit and freezes review
candidates; `harbor_profile_producer` binds accepted profiles to exact sources;
`harbor_environment_producer` completes normalization and builds the MCP bundle
twice before packaging; and `harbor_instruction_producer` runs disclosure,
naturalization and dialogue with their existing review and attempt limits.
They require root-selected generation, policy and budget records. Producer
completion alone is not a passed lane: the lifecycle reruns the owning validators.

`harbor_workbook_bootstrap` validates a frozen new-task candidate against the
original inventory, current source and segmentation proofs, retained curation,
and prior task history. Its `prepare` and `admit` operations precede acquisition
of the root-owned workbook lease. `harbor_environment_worker.advance` then runs
one fixed step through research, normalization, profiling, and packaging. A
nonempty public-source worklist pauses for independently captured evidence.
`harbor_public_capture` retains complete HTTP attempt journals and exact raw
bytes, reconstructs the public text projection, and removes forbidden values
before profiling. Failed or incomplete captures cannot be hidden by selecting
only successful pages.

Pass the environment worker's exact `ReadinessSelection` and `Lease` to
`harbor_instruction_worker.step`. Enable local commands, model calls, and
containers explicitly through its separate execution arguments. Retain each
returned checkpoint hash and supply it on the next invocation. The worker
preserves the complete checkpoint chain and stops for reconciliation after an
uncertain side effect. It performs independent runtime checks before dialogue
and after the final task mutation, then enqueues only a task that passes full
current readiness. Root serialization and immutable release publication remain
required. These workers must run under the process guardian and the configured
workbook and model concurrency limits.

For maintained environment execution, `scripts/harbor_campaign.py` accepts one
root-selected `admit`, `import`, or `step` operation. Supply the exact descriptor
and checkpoint hashes, a fresh `--journal` directory, the shared `--slot-lock`,
and the configured `--env-file`. Model dispatch requires `--dispatch-models`.
Each operation journals its intent, response, resource measurements and outcome
under a 900-second/8-GiB process-group guard. An interrupted or failed operation
requires explicit reconciliation; it cannot silently replay a model call. An
import verifies every retained owning checkpoint before allowing continuation.

`scripts/harbor_instruction_campaign.py` continues an exact maintained
`ENVIRONMENT_READY` handoff with `start`, then runs one canonical `step` at a
time. Supply each returned descriptor and result hash. `--models`, `--commands`,
and `--containers` enable those capabilities explicitly; the coordinator never
publishes. `import-retry` consumes only an already authorized NAT retry receipt.
Insufficient remaining lease time returns `LEASE_RENEWAL_REQUIRED` before any
instruction work; do not substitute a new lease or rewrite a checkpoint.

A completed normalization response with an invalid schema consumes its semantic
attempt. The producer supplies the full `normalization-review/v1` contract and
validates it before emission. `harbor_normalization_recovery` supports only the
reviewed first-attempt field-name defect: it binds the original complete model
journal and failure, then permits a distinct second attempt in a fresh directory.
It never repairs or accepts the malformed response as a successful review.

Large unsubmitted normalization reviews can explicitly select
`harbor_normalization_partitioned.prepare`. It retains the complete original
context and divides it into disjoint requests with at most 128 candidate cells
and 4 MiB each. Shared evidence is lossless, and every original reference must
appear exactly once. The immutable plan blocks the obsolete single-call scope.
All first-round requests must complete before only failed partitions can use
their second round; uncertain calls cannot be replayed. `aggregate` verifies
complete raw journals and merges the decisions, then `emit_phase1` runs the
unchanged generator and financial checks. Select its explicit
`normalization_partitioned_review` binding; never fabricate a single model Call
for the aggregate or silently replace an existing completed review.

The maintained environment coordinator has explicit `refresh` and
`recover-refresh` actions for completed prefixes affected by an audit-reader
upgrade or an unused review scope moved to partitioned normalization. Select
the original worker, completed predecessor, current proof and exact active lease
by path and hash. The refresh rechecks every completed owning gate before one
tracker update. Original checkpoints and model journals remain immutable;
subsequent steps use the separate refresh continuation. Generator-managed
phase1 outputs receive exact historical witnesses before phase2, and their
replacement requires a complete current phase2 proof.

For the first mechanically rejected frozen naturalization writer,
`harbor_nat_retry.authorize` revalidates the completed financial and disclosure
stages, archives the original response and proof, and records one spent attempt
with a compare-and-swap update. Its sealed continuation allows only the remaining
second writer attempt; independent review and all later gates remain mandatory.
Two separate invocations returning identical text count as two attempts. Replaying
the exact retained invocation is idempotent.

Fresh naturalization calls use versioned `frozen-boundary-v1` assembly. It
canonicalizes only scanner-owned outer blank lines, then runs the unchanged
protected-span and complete instruction validators. Exact legacy calls retain
their original mode. `harbor_nat_boundary_recovery` can derive a candidate from
the demonstrated completed second-writer assembly failure while preserving both
consumed attempts; `harbor_nat_boundary_resume` separately validates the current
financial prefix and authorizes only a fresh independent review.

Dialogue writers and paraphrasers supply exact slot JSON. The owning assembler
retains scaffold bytes and junior-slot text; independent semantic review still
checks the completed dialogue. `harbor_dialogue_retry` supports one reproduced
first-round structural correction shared across writer and paraphraser. It
preserves the original call, failure and semantic round, requires current runtime
and financial proofs, and records an explicit continuation transaction. A second
structural correction or an uncertain transport cannot use this route.

`harbor_dialogue_semantic_retry.authorize` permits the existing second semantic
round only after a completed failed first review and fresh current runtime
validation. It preserves the original writer, paraphraser, reviewer and earlier
continuation transactions. The second round still needs its own independent
review; there is no third round or additional structural correction. Every
numeric target collision, including zero and short integers, needs its own
independently justified occurrence. Source-proved formula operands are supplied
to the reviewer with their signed identity and collision status; this evidence
does not override the review or expose the answer key to the writer.

Source-link grouping supports an explicit `dialogue-source-link-scope/v3`
certificate for relative project years such as `Year 0` and `Year 1`. The
original literal headers must form consecutive axes, and every grouped claim
must retain its exact source formula, matching period and claim identity.
Unproved links remain individual; numerical equality or column position alone
does not establish a shared period. Applying a new grouping after a submitted
review additionally requires explicit applicability of that retained history.

The sparse audit inventory reader parses each selected worksheet once and
preserves native types and exact sheet identities. Older completed audits require
an explicit current applicability proof. `xl_audit_inventory_lineage` validates
previous renderer and locator receipts without executing historical code;
`harbor_normalization_inventory_applicability` additionally preserves both actual
normalization calls in the supported schema-recovery chain. These receipts do
not approve downstream gates or reset review budgets.

For reports spanning a separate campaign, select its Harbor namespace explicitly:

```bash
.venv/bin/python -B -m scripts.corpus_qualification \
  --inventory "$RUN/inventory.json" --run-root "$RUN" \
  --harbor-root harbor/new50-20260909
```

`--harbor-root` is repeatable and adds to the default `$RUN/harbor` namespace.
Every original remains in the denominator. Duplicate trackers for one original
fail qualification rather than selecting whichever result looks best. Stage
reports accept the same option but do not claim a verified promotion numerator.
Historical promotions require the explicit `harbor_legacy_requalification`
transaction when their original tracker predates the strict lifecycle. This
preserves the original tracker, notes, release, and attempt history and reruns
current owning checks before one compare-and-swap update. Subsequent code-only
changes use the separately selected `scripts.harbor_legacy_refresh` transaction
chain. Every prior qualification, task byte, release identity and consumed review
attempt remains bound. Current runtime validation determines whether an earlier
observation is still applicable; code drift cannot substitute for execution.

Publish a newly qualified task through `scripts/harbor_publish.py`, selecting
the exact completed enqueue checkpoint with `--checkpoint` and
`--checkpoint-sha256`, plus the tracker, publication, compatibility-output,
queue-lock, workbook-slot-lock and fresh journal paths. Publication holds both
locks, revalidates complete readiness, uses the strict publication lease and
release compare-and-swap, and independently qualifies the result before and
after releasing leases. A retained interrupted intent can be selected with
`--resume-intent` and `--resume-sha256`; conflicts or incomplete side effects
require explicit reconciliation. Publication never runs an agent solve rollout.

An exact completed audit response rejected by the earlier multiline-label
renderer can be recovered through `xl_audit_renderer_recovery`. Root must bind
the original generator, incomplete audit metadata, every completed transport
attempt, and the unchanged native source and segmentation pair. Recovery emits
separate immutable artifacts and an explicit applicability receipt. It never
reruns the model or imports historical code; incomplete or altered transport
evidence fails recovery.

The final disclosure check can use an explicit pre-dialogue witness only when
its instruction is the exact reviewed naturalization result and every verifier
input matches the current task. The delivered task's faithfulness and dialogue
checks still run independently. An empty dialogue is permitted only after a
fresh, complete empty-claim extraction, unchanged task bytes, and proof that no
prior dialogue round was spent. Directory evidence is accepted only in the
registered typed bundle fields, validated against its complete tree hash, and
expanded into checked file records.

Offline packaging uses `xl_output_task.py --require-cached-variable-source-audit`
with `--no-naturalize`. It accepts only a complete current audit cache and fails
on a cache miss without reading credentials or requesting another audit. Use a
fresh packaging work directory and stage; interrupted command evidence is
retained and cannot be silently replayed. Normalization phase-two recovery
accepts only the exact retained old or expected new bytes.

A fresh normalization with no eligible candidate needs no invented
model review. The consumer independently reconstructs the complete candidate
analysis, verifies its original zero-variable snapshot, and enforces all plain
eligibility rules. Any surviving candidate requires the ordinary semantic review.
Completed historical audits can use only an explicitly supported validator
upgrade receipt; the current validator checks original response completion,
coverage, hashes and consumed budget. It never executes historical audit code
or grants an additional semantic attempt.

`scripts.batch.harbor_admission` supplies the root admission validators: current
Python 3.12 and dependency probes, Docker availability, frozen original identity,
approved source lineage, prior-task namespace scans, and inherited audit budgets.
Its dependency observation expires after five minutes and is independently
rechecked before admission. Admission starts all lanes pending and does not
establish task quality.
Run their combined regression suite with:

```bash
.venv/bin/python -m pytest -q tests/test_harbor_tracker_schema.py tests/test_harbor_tracker_lifecycle.py
```

### Kickoff

Root-selected batches of other sizes use the versioned candidate manifest
`harbor-qualified-task-candidate-proposal/v1`. Its ordered
`requested_workbook_ids` must match `selected_candidates` exactly, and
`target_new_qualified_tasks` must equal their nonzero count. The
`denominator_for_original_rebuild` binds the complete selected inventory size;
it is not the number of passing tasks. Original identities, the full history
survey, retained curation and targets, immutable reconstruction evidence, and
inherited review budgets still pass the existing owning validators. Existing
task or tracker identities require preserved-history resume. The older
`new50-qualified-task-candidate-proposal/v1` contract retains its exact-50
requirements. Neither manifest grants a task-quality pass.

Naturalization reviews with incomplete source or candidate clause coverage now
receive up to three reviews of the same candidate: the original review plus
two automatic corrections containing the exact missing clauses and prior
response. The writer attempt count does not change. If all three reviews are
semantically positive but coverage remains incomplete, the configured policy
continues with a hash-bound `coverage-exception.json`; publication rechecks all
three responses. Semantic findings, invented quotations, and incomplete model
transports remain blocking. Retained failures require the explicit root repair
adapter; they are never erased or treated as fresh attempts.

Dialogue extraction preserves an unused-row disclosure's exact row identity
without treating it as a calculation cell. Calculation methods and source
selections continue to require explicit cell identities.

Invoke the skill explicitly in Cursor:

```text
Use @.cursor/skills/harbor-orchestrator/SKILL.md.

Start Harbor batch raw-workbooks-2026-08-31.
Requested workbook IDs: 0256, 0262, 0449, 0450
Raw sources: 4-10 100/<id>.xlsx
max_parallel_tasks: 4
max_model_heavy_tasks: 2

Run autonomously. Promote each qualified workbook independently, stop only
the workbook whose closed repair path is exhausted, and do not run rollouts.
```

The orchestrator performs dependency provisioning once, then keeps a stable
queue of requested IDs. As a task agent finishes or stops, the next queued
workbook takes its slot. Model-heavy work uses a separate fleet semaphore, and
publication uses a singleton queue so release compare-and-swap operations
remain serial.

Before tracker creation, source health may route volatile-only failures through
`/harbor-volatile-formula-fixer`. Its deterministic OOXML tool can remove
provably dead volatile defined-name graphs, freeze an unreferenced `TODAY()`
display to its cached as-of date, resolve static internal `INDIRECT` targets,
and replace scalar blank-column `OFFSET` calls with `INDEX`. It writes a
candidate under the batch run rather than overwriting the discovered source.
Every cached cell value and untouched OOXML member must remain byte-identical,
all plan/manifest hashes must verify, and fresh source health must pass. Any
unresolved construct remains terminal; no source-policy gate is weakened.

### Per-workbook specialist lane

Every task agent invokes the specialist skills in this order:

1. **Source warden** — source health, trusted recalculation routing, immutable
   source generation, and production AST.
2. **Segmentation analyst** — preserve curation, require strict live-evidence
   `PASS`, and require healthy preflight.
3. **Research auditor** — build baseline inputs and run the pinned
   `openai/gpt-5.6-sol` variable/source audit.
4. **Normalization engineer, phase 1** — import every audit row, write the
   atomic normalizer, resolve every disposition, and choose plain or MCP mode.
5. **Source profiler** — independently profile and review canonical public
   sources for an MCP task; plain tasks record a validated no-op.
6. **Normalization engineer, phase 2** — bind accepted profiles, validate the
   final specification, and pass maskability and leakscan.
7. **Environment packager** — deterministic double MCP build, smoke, separate
   masking, and staging under `tasks_outputs_mcp/`.
8. **Disclosure writer** — select, detect, render, faithcheck, and verify the
   complete workbook disclosure.
9. **Instruction naturalizer** — two-attempt, frozen-span, semantically
   reviewed rewrite with journaled apply.
10. **Independent verifier** — fresh disclosure review, generalized live HTTP
    oracle (MCP mode), and exact-answer grader score `1.0`.
11. **Dialogue author** — produce and validate the colleagues Q&A file, apply
    it transactionally, and smoke the final main image.

Normalization is one lane invoked in two phases, so this list contains ten
specialist skills across eleven ordered invocations. The golden `.xlsx` remains
build-time evidence and never enters the task environment.

### Repairs, failure classes, and agent notes

Lane agents do not weaken gates or repair their own output. The orchestrator
classifies each failure and may route one bounded repair to:

- `harbor-spec-fixer` for workbook-specific normalization, masking, and leak
  defects;
- `harbor-prose-fixer` for an existing disclosure, naturalization, or dialogue
  retry slot;
- `harbor-infra-fixer` for approved infrastructure and routing faults.

After a repair, the workbook re-enters at the earliest invalidated gate and
reruns every hash-dependent downstream gate. Unsupported sources, uncertified
circular calculations, post-semantic oracle failures, exhausted retry budgets,
and unresolved shared-code defects stop only that workbook.

Every non-promoted task has:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json
runs/harbor-fleet/<batch>/workbooks/<id>-agent-notes.md
```

The tracker stores the current `promotion_failure` plus append-only failure and
repair history. Agent notes explain why promotion did not happen, identify the
common failure class and subclass, link the exact evidence and hashes, list
repair attempts, record confidence impact and next action, and end with the
workbook's promote/stop decision. The batch completion report aggregates the
same closed failure classes across every requested workbook.

### Promotion

As soon as one workbook passes every applicable gate with high or qualified
medium confidence, it enters the serial publication queue. The publication
worker freezes its staged task generation, performs the workbook-local release
CAS, verifies `tasks_outputs/<id>-outputs`, and marks that workbook `PROMOTED`.
A publication failure marks only that workbook `STOPPED`; other task agents
continue.

Batch results are written to
`runs/harbor-fleet/<batch>/completion.json` and may contain a mix of promoted
and stopped workbooks. Harbor rollout is a separate operation and is never
started by this skill.

Variable/source audits require `lbx_labelbox_api_key` in `.env`. Audit generation is
pinned to `openai/gpt-5.6-sol`; public-source profiling, disclosure arbitration,
naturalization, independent review, and dialogue generation use pinned
`gpt-5.6-sol-high` agents.

**Automatic invocation boundary.** Running `xl_output_task.py` or an individual
Python stage directly does not launch Cursor task agents, specialist skills,
fixers, model reviewers, or the publication controller. Start task creation
through `/harbor-orchestrator` (or attach its `SKILL.md` explicitly) so every
gate, repair, note, and publication verdict is tracked.

---

## 22. `/custom-formula-gate`

Some golden formulas are standard textbook finance methods and others contain
workbook-specific logic a task-solving model is unlikely to reconstruct.
`/custom-formula-gate` now runs during task creation, after segmentation has
passed and before masking or packaging. It has no Harbor rollout dependency.

The semantic analysis is pinned to one `generalPurpose` subagent using
`gpt-5.6-terra-high`. The raw workbook remains a build-time answer key: Terra
may inspect it through the extracted context, but neither the workbook, context,
nor full report enters the task environment. Only validated method guidance may
be included in the task instruction.

### Inputs

- raw workbook, normally `4-10 100/<workbook>.xlsx`
- verified segmentation and curation under `seg_out/<workbook>/`
- closed textbook catalog at
  `.cursor/skills/custom-formula-gate/CATALOG.md`

### Identify key variables, then match textbook values

```bash
python3.12 .cursor/skills/custom-formula-gate/scripts/extract_gate_context.py \
  0256 --source "4-10 100" --seg-dir seg_out/0256 \
  --output runs/0256-custom-formula-gate/context.json
```

The deterministic extractor starts from every curated output and retains the
complete reverse closure of load-bearing formula series. It ranks those
`key_variables` by output coverage, dependency depth, and downstream fan-out,
then records their labels, direct drivers and assumptions, golden formulas,
normalized patterns, cached period values, neighbors, references, and
custom-logic signals. Ranking changes review order only; no output-dependent
formula is discarded.

Terra first names each key variable and maps its labeled drivers to textbook
slots such as revenue, rate, beginning balance, draw, life, or period. It then
tests only catalog variants for that role against every usable cached period at
`max(1e-6, 1e-6 * abs(expected))` tolerance. A match is standard only when all
required parameters come from labeled assumptions. Missing cached values require
an exact symbolic match; otherwise the row is `unclassified`.

The stage writes:

- `runs/<workbook>-custom-formula-gate/context.json`
- `runs/<workbook>-custom-formula-gate/report.json`
- `runs/<workbook>-custom-formula-gate/hints.json`

```bash
python3.12 .cursor/skills/custom-formula-gate/scripts/validate_gate_outputs.py \
  runs/0256-custom-formula-gate/context.json \
  runs/0256-custom-formula-gate/report.json \
  runs/0256-custom-formula-gate/hints.json
```

| Verdict | Meaning |
| --- | --- |
| `FLAG` | At least one `custom_logic` or `literal_embedded` key variable exists. Validated method-only hints are packaged and the build continues. |
| `REVIEW` | At least one key variable is `unclassified`; packaging stops for manual review. |
| `PASS` | Every domain method is `standard` or `standard_variant`; documented `definitional` and `structural` rows do not fail the gate. |

Before packaging,
`.cursor/skills/custom-formula-gate/scripts/validate_gate_outputs.py` rechecks
the exact Terra model, prompt/context/catalog hashes, complete key-variable
coverage, closed classes and catalog IDs, all-period agreement evidence,
verdict/count consistency, hint coverage, and formula/answer leakage. The
package receives only `hints.json` and its prose; golden formulas and cached
values stay under `runs/`.

---

## 23. Variable-source MCP environments

A rebuild task normally hands the agent every input. This stage removes the
*externally-sourced* inputs — market rates, tax rates, macro assumptions,
contractual terms, opening balances — and serves them through a mock MCP
research service running as a docker-compose sidecar next to the agent
container. The agent has to research the missing assumptions with generic
retrieval tools before it can rebuild the model. Broad queries return
plausible distractors; resolving a value takes a query that pins down every
dimension (entity, metric, period, scenario, basis, unit, status) *and*, as
of the v2 hardening below, picking the unsuperseded release from a provenance
chain of otherwise identical records, five paginated rows at a time.

The design is adapted from the `build-variable-source-mcp` skill and follows
the [Harbor MCP server task pattern](https://www.harborframework.com/docs/tutorials/mcp-server-task).

The Markdown is a model-generated audit candidate, not evidence that a named
source supplied an exact workbook value. Its warning, deterministic inventory,
inventory SHA-256, model id, endpoint, prompt version, timestamp, API-call
count, and finish reasons are retained so a reviewer can reproduce and audit
the generation. `xl_output_task.py` runs or cache-reuses this stage by default;
pass `--refresh-variable-source-audit` to force a new GPT response. Before a
response is accepted, the generator mechanically rejects qualified cell
references or workbook-value numbers that do not occur in that inventory
batch; one corrective retry is allowed.

### Pipeline

```bash
# 0. Generate the review artifacts from the inputs-only workbook.
#    The deterministic inventory is sent to GPT 5.6 Sol through Labelbox's
#    LiteLLM proxy; lbx_labelbox_api_key is read from .env and never written to disk.
python3.12 xl_variable_source_audit.py 0233 \
    --inputs-root inputs_out --seg-root seg_out --audit-root runs
#
#    Emits:
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.md
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.inventory.json
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.metadata.json

# 1. Preserve every table row as a draft review queue
python3.12 xl_variable_mcp.py import \
    runs/0233-variable-sources/0233-inputs-variable-sources.md \
    runs/0233-variable-sources/draft.json

# 2. Normalize into atomic variables (one per entity, metric, period, scenario,
#    basis, unit, status tuple), with workbook cell refs. gen_normalizer.py
#    writes the authoring script to runs/0233-variable-sources/normalize_0233.py;
#    running that script emits normalized.json. Exclusions are documented in
#    exclusions.json.

# 3. Profile deduplicated public source URLs with /profile-mcp-sources.
#    GPT 5.6 Sol receives only a redacted URL/name/kind worklist. Each source
#    is capped at three public reads. Authenticated, paywalled, challenged,
#    unreachable, and unsupported pages are skipped and retain generic source
#    rendering. The normalization script embeds only reviewed profiles.

# 4. Validate the atomic spec and reviewed source-profile references.
python3.12 xl_variable_mcp.py validate-spec \
    runs/0233-variable-sources/normalized.json

# 5. Build offline and deterministically. Every variable's raw cell value is
#    verified against the golden workbook; any mismatch aborts.
python3.12 xl_variable_mcp.py build \
    runs/0233-variable-sources/normalized.json \
    runs/0233-variable-sources/mcp --workbook 0233 --source "4-10 100"

# 6. Exercise the generated server with a real MCP client.
uv run --python 3.12 --with fastmcp --with openpyxl \
    python xl_variable_mcp.py smoke runs/0233-variable-sources/mcp

# 7. Mask served variables into a separate inputs workbook.
python3.12 xl_input_mask.py 0233 --source "4-10 100" -o inputs_out_mcp \
    --mask-cells runs/0233-variable-sources/mcp/mask_cells.json

# 8. Package into staging; audit the unredacted baseline inputs.
python3.12 xl_output_task.py 0233 --source "4-10 100" \
    --inputs-root inputs_out_mcp --mcp runs/0233-variable-sources/mcp \
    --variable-source-audit-inputs-root inputs_out \
    -o tasks_outputs_mcp

# 9. Oracle-check the shipped bundle against the live sidecar container.
docker build -t mcp-0233-oracle tasks_outputs_mcp/0233-outputs/environment/mcp-server
docker run -d --name mcp-0233-oracle-run -p 18233:8000 mcp-0233-oracle
uv run --python 3.12 --with fastmcp --with openpyxl \
    python xl_mcp_oracle.py \
    --bundle tasks_outputs_mcp/0233-outputs \
    --mcp runs/0233-variable-sources/mcp --url http://127.0.0.1:18233/mcp
```

### What `build` emits

```
runs/<wb>-variable-sources/mcp/
  runtime/            served data: sources, documents, datasets, records
  eval/tasks.jsonl    prompts, typed answers, evidence ids — NEVER shipped
  server.py           standalone FastMCP server (streamable HTTP :8000/mcp)
  Dockerfile          sidecar image (python:3.12-slim + fastmcp)
  mask_cells.json     every cell the masker must additionally blank
  masked_inputs.json  audit map: variable -> refs, raw value, MCP evidence
```

Authoring artifacts next to `mcp/` include `draft.json`, `normalized.json`,
`exclusions.json`, `source_profiles.json`, and bounded profile captures. They
never enter `environment/`.

### Public-source realism without live-value substitution

Accepted profiles shape source descriptions, dataset names and aliases, field
labels, document kinds, release cadence/labels, and at most two short
attributed excerpts. They cannot alter record dimensions, workbook values,
distractor logic, or the generic MCP tools. The builder makes no network calls;
identical normalized specs and seeds produce byte-identical runtime artifacts.
Real URLs remain `origin_url`, while served documents use `mock://`.

Only `status: profiled` plus `review.status: accepted` affects rendering.
Skipped, pending, rejected, internal, and unprofiled sources use the prior
generic treatment. Validation rejects broken references, unsafe URLs,
authentication-derived content, source-ID collisions, unattributed or overlong
excerpts, and workbook-value leakage.

Each variable produces one supported record plus two layers of noise, both
seed-controlled:

- **~18 dimension distractors** (`distractors_per_variable`) that each differ
  on at least one meaningful dimension — adjacent period, another scenario
  case, another basis or unit, another status. No *unsuperseded* distractor
  shares every dimension with any supported record; the generator enforces
  this globally, and `alternate_unit` is careful to emit wrong units that do
  not contain the true unit as a token (the server matches dimensions by
  token containment, so "alternate years" would have made queries for
  "years" ambiguous).
- **2 stale provenance releases** (`provenance_releases_per_variable`) that
  share *every* dimension with the supported record — even `status: final` —
  and differ only in `release`, `published_at`, and a `superseded_by` link
  naming the successor. Their values are perturbed, so dimension filters
  alone return several conflicting rows; the deterministic rule is *use the
  release whose* `superseded_by` *is null*.

`validate` re-proves per task, using the live server's matching semantics:
the dimension filter plus the supersession rule select exactly the evidence
record, every chain is acyclic and ends at it, no stale release carries the
current value, and no evaluation key leaks into the runtime. Supported values
are exact raw workbook values (the grader tolerance is 1e-6, so the service
must serve exactly what the masked cells held); the golden-value check makes
a mismatched spec impossible to build.

The server exposes one generic tool surface across all domains —
`list_sources`, `list_datasets`, `search_documents`, `fetch_document`,
`query_records` — with no target-named tools, no truth flags, and `mock://`
document URLs so synthetic documents cannot be mistaken for genuine
publications. Real source URLs survive only as `origin_url` metadata.
`query_records` requires at least two filter dimensions and pages at five
rows per `next_cursor`, so harvesting the corpus wholesale costs many calls.

### Masking

`xl_input_mask.py --mask-cells` merges the listed refs into the pasted-answer
`deny` set (typed cells are blanked) and removes them from the keep/frontier
sets (frozen formula hosts are blanked, and `verify` accepts the intentional
blanks). This is the one sanctioned exception to the golden rule that a typed
cell is never removed: the value stays retrievable, just through the research
tools instead of the sheet.

Normalization has to hunt down duplicate representations, or the mask is
theatre. For 0233 that meant: sensitivity-table centres holding the same 9%
cost of equity (`DDM!BG68` etc.), the active-case debt-term column
(`Control!F61:F63`), and text renderings like `"20 years"` on the Term
facilities sheet (masked via `workbook.extra_cells`, which are blanked and
audited but not value-checked). Variables whose values are exposed on the
Embedded Assumptions sheet (the 85% availability factor, grid/insurance cost
literals, the reserves bridge) are *excluded* from masking rather than half
masked — see `runs/0233-variable-sources/exclusions.json`.

### Harbor integration

With `--mcp`, `xl_output_task.py` adds to the bundle:

- `environment/mcp-server/` — sidecar Dockerfile, `server.py`, and the
  `runtime/` data. `eval/` never enters the bundle; the audit map goes to
  `tests/masked_inputs.json`, which like `answer_key.json` is only mounted at
  verification time.
- `environment/docker-compose.yaml` — the Harbor compose override: `main`
  depends on a healthy `mcp-server`, which exposes port 8000 with a TCP
  healthcheck.
- `task.toml` — `[[environment.mcp_servers]]` with
  `url = "http://mcp-server:8000/mcp"` (streamable HTTP). The openhands-sdk
  agent registers it natively via `MCP_SERVERS_JSON`; the instruction's
  "Research data service" section also documents the URL and tools so any
  shell-capable agent can connect with `fastmcp`.
- agent timeout extended by 15 s per served variable.

The grader is unchanged: the answer key still holds only the curated output
cells, and retrieval quality is graded implicitly — wrong or unretrieved
inputs surface as wrong outputs.

### Verification before rollout

`xl_mcp_oracle.py` runs against any live sidecar exactly as the agent reaches
it — paginating through `next_cursor` and applying the supersession rule — and
asserts: every masked variable resolves to its evidence record; every
provenance chain is visible; every broad metric query conflicts; every masked
cell is blank; no unapproved numeric, string, date, or formatted duplicate
survives; profile excerpts are attributed and value-safe; and neither the
golden workbook, `eval/`, normalized specs, nor profile captures are in
`environment/`. Legitimate duplicate representations require an explicit
cell/variable/reason allowlist; unknown or unused allowlist rows fail. The
historical 0233 oracle reported:
70/70 resolved, 70/70 chains seen, 70/70 broad conflicts, 0 leaks beyond
documented axis-header coincidences and the excluded Group Tax rate rows
(reports in `runs/0233-variable-sources/oracle_report*.json`).

### Source-profiled rebuild results

The strict staged rebuild promoted three MCP tasks after deterministic
double-builds, in-memory smoke tests, live-container oracles, and exact-answer
grader checks:

| workbook | MCP variables | masked cells | runtime records | accepted / skipped public profiles | result |
| --- | ---: | ---: | ---: | ---: | --- |
| 0248 | 48 | 184 | 1,008 | 1 / 3 | promoted; 48/48 exact resolutions, chains, and broad conflicts |
| 0251 | 1 | 108 | 21 | 0 / 0 | promoted; 1/1 exact resolution, chain, and broad conflict |
| 0255 | 15 | 35 | 315 | 0 / 0 | promoted; 15/15 exact resolutions, chains, and broad conflicts |

All three segmentation proofs pass with no cells seeded inside an output cone;
all strict oracles report zero masked cells left populated, zero unapproved
value leaks, and no allowlist. Workbook 0248 also validates the Damodaran
source-shaped terminology and short attributed excerpts in the shipped
runtime. Reports are under `runs/<wb>-variable-sources/oracle-report.json`.

Workbook 0233 was not promoted. General lazy-branch, dynamic-reference, and
iterative-cycle fixes recompute seven of its eight curated outputs, but
`Term facilities!E88` evaluates `IF(F4=B80,E17,E88)` with `2 != 3`, selecting
the recurrence `E88 = E88`. Every value is a fixed point; the saved
`2.156135461674124` is persisted workbook state rather than a result determined
by frontier inputs. Recovering it would require the cached formula seeding that
the strict pipeline forbids, so the existing 0233 task remains unchanged.

### Difficulty hardening (v2): pagination caps and provenance chains

Trajectory analysis of the first rerun showed the retrieval layer was too
cheap: agents dumped whole datasets with one broad `query_records` call
(limit 200-300) and disambiguated the distractors locally by reading the
dimension labels off each row. Two changes close that route:

**Capped, filter-gated, paginated queries.** `query_records` now requires at
least two non-empty filter dimensions and returns at most 5 rows per page
with an opaque `next_cursor`. Bulk dumps stop working; the agent has to issue
one deliberately-filtered query per variable (or page through many screens).

**Provenance release chains.** Each variable's supported record now ships
with stale releases that share *every* dimension - entity, metric, period,
scenario, basis, unit, even status `final` - and differ only in provenance:
`release`, `published_at`, and a `superseded_by` link naming the successor
release. Stale releases carry perturbed values, so a fully-filtered query
legitimately returns several conflicting rows and the dimension labels no
longer decide. The deterministic resolution rule - *use the release whose*
`superseded_by` *is null* - is stated in the server instructions, every
dataset description, the document text ("SUPERSEDED: replaced by release X"),
and the task instruction, so the environment stays fair: exactly one record
survives the rule, and no judgment call is required.

Determinism is enforced mechanically, not hoped for. The validator replays
the *live server's* token-containment matching semantics (a lesson from the
first build of v2: a generic "alternate years" distractor unit substring-
matched queries for "years" and made 18 tasks ambiguous until
`alternate_unit` was taught to emit non-colliding units) and asserts per
task: the dimension filter plus the supersession rule select exactly the
evidence record, every stale chain is acyclic and terminates at it, and no
stale release accidentally carries the current value. The smoke test and
oracle check resolve every variable the same way over real streamable HTTP,
paginating like an agent would.

### 0233 rerun: opus-5, formula hints, 5 attempts

The 0233 task was rebuilt with 70 atomic variables masked (573 cells) and
re-evaluated with the same harness as the baseline: `harbor run` (docker),
openhands-sdk, `anthropic/claude-opus-5`, 5 attempts, formula-hinted bundle,
scored by `xl_passk_score.py`. One harness change was forced by the docker
backend: containers on user-defined compose networks could not reach
`host.docker.internal`, so the Labelbox attribution proxy now runs as a
per-trial compose sidecar (`runs/llm-proxy-compose.yaml`, layered in through
the job config's `environment.extra_docker_compose`) and the agent points at
`http://llm-proxy:8019`.

The task was evaluated three times with the same harness: the baseline with
every input on the sheet, MCP v1 (70 variables masked, unlimited queries),
and MCP v2 (same masking plus pagination caps and provenance chains). All
runs: 5 attempts, no errors, coverage 100%.

| metric (0233-outputs) | baseline | MCP v1 | MCP v2 (hardened) |
| --- | --- | --- | --- |
| continuous score mean | 0.8159 | 0.7964 | 0.8469 |
| continuous score variance | 0.00216 | 0.00316 | 0.00409 |
| pass@5 (all 8 cells exact) | 0% | 0% | 0% |
| mean cells exact | 30.0% | 30.0% | 25.0% |
| best attempt exact | 50% | 50% | 25% |
| mean within 1% | 45.0% | 45.0% | 50.0% |
| agent steps per attempt | 59-90 | 55-91 | 82-123 |
| cost | $37.37 | $32.10 | $43.72 |

Per-cell continuous means and the full attempt detail are in
`runs/opus5-mcp-hinted-0233/` and `runs/opus5-mcp2-hinted-0233/`
(`percell_comparison.json` in the latter holds the three-way table) next to
the baseline in `runs/opus5-hinted-pass5/`.

What the three runs say, from the trajectories:

- **v1 made retrieval too cheap.** Agents dumped whole datasets in 1-5 broad
  `query_records` calls and read the dimension labels locally; zero failed
  queries, zero distractor values used, but attempts skipped 0-13 of the 28
  hard-to-guess values as not worth retrieving (the H2 2021 capex was skipped
  in 4/5 attempts). Mean fell ~2 points to 0.796.
- **v2 forced engagement and got it.** Every attempt wrote a pagination-aware
  helper (looping `next_cursor` with entity+metric filters), paged through
  each metric, and explicitly kept only unsuperseded releases. Zero stale
  release values appear in any attempt's model code. Retrieval got *more*
  thorough - all five attempts recovered 28/28 distinctive masked values -
  at the price of ~30 more steps and ~$2 more per attempt.
- **The continuous mean rose above the baseline (0.847 vs 0.816)** because
  the forced thoroughness fixed v1's skipped-variable losses (DSCR 0.77 ->
  0.88, Implied EV 0.68 -> 0.90 per-cell means), while exact solves narrowed
  (best attempt 25% exact vs 50%): more inputs were right, but small unit
  and convention slips in the longer research pipeline kept cells just
  outside the 1e-6 exact band - within-1% rose to 50%. Variance roughly
  doubled versus baseline (0.0022 -> 0.0041), driven by one 0.75 and one
  0.95 attempt.

Net: the hardened environment measurably raises the cost of research (steps,
tokens, dollars) and defeats bulk-dump shortcuts without making the task
unfair - the supersession rule was applied correctly 5/5 times. On this task
the binding constraint remains the deep calculation chains (DSCR, FCF after
debt service, enterprise value bridge), not retrieval. One residual softness
to tighten next: `search_documents` snippets can expose a document's reported
value without its supersession notice; no agent exploited it, but trimming
the snippet before the value line would close it.

### Final-instruction naturalization A/B rebuild

The fail-closed task workflow was rerun for `0248`, `0251`, and `0255` after
adding the pre-package formula gate and final instruction naturalizer. Workbook
`0233` was not regenerated: strict verification still rebuilds seven of eight
outputs, while `Term facilities!E88` evaluates to the active recurrence
`E88 = E88`. Its saved value is persisted Excel state rather than a result
determined by frontier inputs, so seeding it would violate the no-answer-seeding
gate.

All three accepted workbooks passed segmentation, deterministic double MCP
builds, live HTTP oracles, and exact-answer grader smokes for both instruction
variants. The formula gate classified every key series and packaged only
validated method guidance:

| workbook | key formula series | packaged method hints | MCP variables | oracle |
| --- | ---: | ---: | ---: | --- |
| 0248 | 199 | 9 | 48 | 48/48 exact resolutions, chains, and broad conflicts |
| 0251 | 5,192 | 83 | 1 | 1/1 exact resolution, chain, and broad conflict |
| 0255 | 82 | 28 | 15 | 15/15 exact resolutions, chains, and broad conflicts |

For the A/B test, both variants contained the same workbook, MCP environment,
target table, research instructions, formula hints, conventions, and output
contract. The naturalized variant changed only the opening and `Input` prose;
the deterministic validator required every other section to remain
byte-identical. Each result below is the mean continuous score from five valid
attempts. Agent-exit failures were excluded and replaced one-for-one before
comparison.

| model | workbook | original instruction | naturalized instruction | delta |
| --- | --- | ---: | ---: | ---: |
| Claude Opus 5 | 0248 | 0.906 | 0.848 | -0.058 |
| Claude Opus 5 | 0251 | 0.719 | 0.664 | -0.055 |
| Claude Opus 5 | 0255 | 0.700 | 0.764 | +0.064 |
| Gemini 3.5 Flash | 0248 | 0.681 | 0.653 | -0.028 |
| Gemini 3.5 Flash | 0251 | 0.401 | 0.292 | -0.109 |
| Gemini 3.5 Flash | 0255 | 0.679 | 0.653 | -0.026 |
| **Claude Opus 5, all 15 attempts** | **all** | **0.775** | **0.759** | **-0.016** |
| **Gemini 3.5 Flash, all 15 attempts** | **all** | **0.587** | **0.533** | **-0.054** |

No task/model/variant produced an exact `1.0` solve in its five valid attempts,
so exact pass@5 was zero throughout. The narrow rewrite therefore showed no
aggregate performance gain in this sample; only Opus on `0255` improved.
