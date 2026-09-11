# Large-graph and compact-custody implementation — 10 September 2026

This implementation task performs no production RL task generation. This pass changes pipeline code
and runs synthetic, hermetic, and explicitly bounded read-only source checks.
It does not report a new workbook-to-Harbor yield or agent difficulty.

## Implemented

- AST construction automatically uses disposable SQLite storage at 100,000
  formulas. Ordered nodes and edges, exact deduplication and memory-mapped SCC
  work arrays preserve CSV evidence and complete cycle membership. Mutable
  iteration preserves pending updates; cycle previews use bounded heaps.
- Segmentation automatically indexes CSV graphs of at least 128 MiB. It retains
  bounded node caches, ordered adjacency and exact integrity diagnostics.
  Records are read-only; immutable CSV remains the evidence. Production success,
  exceptions and interruption close temporary indexes.
- Cell projection streams relevant node kinds and literal values. Sparse range
  intersections preserve the existing reference grammar and row-major order
  without enumerating blank cells. Truncated-range proof restrictions remain.
- Source-generation fingerprints include the disk builder implementation, and
  segmentation fingerprints include the new loader. Storage changes do not
  change curated targets, tolerances or quality gates.
- Private package copying uses verified independent APFS clones when supported,
  with a byte-copy fallback and exact before/after checks. Captured paths under
  selected `/Volumes` roots can be replayed read-only.
- Private packages support deterministic bounded gzip/tar export and import,
  pinned by both archive and original manifest hashes. Import rejects unexpected
  members, links, traversal, altered contents and oversized expansion. Imported
  objects, manifests and directories are synced before atomic exclusive
  publication. Archive transport neither grants cleanup authority nor deletes
  the raw package.
- Historical audit reuse accepts only the explicit forward addition of the
  installed disk-storage module. It still compares all financial evidence bytes
  and rejects arbitrary module changes. The reviewed reader pins are updated;
  no historical evidence or approval is rewritten.
- Explicit hash-pinned XLS/XLSB candidate preparation is implemented. Candidates
  remain unverified until independent original/candidate observations and
  trusted receipts establish equivalence. Neutral semantics v2 compare effective
  financial calculation settings; XML serialization hints remain separate
  hash-bound candidate provenance. The source inventory adoption gate
  remains closed. See [legacy candidate instructions](legacy-candidates-20260910.md).

## Measured synthetic performance

The fixture has 100,000 formulas, 800,000 AST nodes and 900,000 AST edges. Both
construction modes emitted byte-identical CSV. Both downstream modes produced
300,000 cell records and 200,000 projected dependencies with identical complete
record/adjacency digests.

| Operation | Memory backend peak RSS | Disk backend peak RSS | Memory / disk elapsed |
| --- | ---: | ---: | ---: |
| AST construction and CSV output | 1,637,154,816 bytes | 466,468,864 bytes | 12.835 / 48.996 s |
| CSV load and cell projection, including digest checks | 1,390,018,560 bytes | 568,623,104 bytes | 12.591 / 21.755 s |

Each subprocess had an 8 GiB memory and 900-second time limit. All synthetic
scratch was removed. AST timing preceded the final dirty-iteration and bounded
preview fixes; it measures that explicitly recorded implementation, not a fresh
post-fix timing run. The downstream record reflects its separately recorded
implementation. These results do not establish full workbook reconstruction.
Compact benchmark records are under `diagnostics/compact-graph-benchmarks-20260910/`.

## Completed validation

The supported Python 3.12 command, `.venv/bin/python scripts/test_pipeline.py`,
passed against an isolated, frozen 498-file source/configuration/test snapshot:
**5,508 passed, 31 subtests passed, four skipped, eight deselected, seven warnings**
in 1,023.98 seconds. The separate supported dialogue entry point passed all
**158 assertions**. Private-corpus and host-control checks are not silently counted
as hermetic coverage. The log is
`diagnostics/compact-graph-next/hermetic-final.log`; the source manifest hash is
`74a70383ee89a58c96f1a50df097c4e65e188d871f2d81b76c14ab6cfb46f861`.

The snapshot includes the parallel grading, transport-lifecycle and input-mask
repairs. A subsequent, separately authorized authoring task changed general-cohort
admission and its README documentation. Those edits were preserved; the current
admission and general-cohort tests passed **74 cases** in 15.92 seconds, recorded
in `general-cohort-integration.log`. They are additional focused coverage, not
part of the earlier frozen full-suite result.

An earlier full-suite attempt found six historical audit-refresh failures. They
were repaired by binding the complete storage builder closure and explicitly
validating the two supported historical/current inventory profiles. That failed
log and the deliberately stopped pre-review attempt remain labeled separately;
neither is presented as a successful final run.

Focused graph/model/projection tests passed (125 cases). A separate read-only
SQLite compatibility selection passed all 53 cases: 35 cycle-value cases,
11 Excel compatibility cases and seven range-array cases. Every selected case
actually loaded disk-backed records. Its preceding exploratory sweep is retained
separately: 208 fixtures attempted forbidden post-load mutations and 251 passing
cases constructed memory graphs directly. Those are not passing disk evidence.

Two actual local Docker isolation checks passed against the final snapshot in
3.39 seconds with the pinned installed Python 3.12 image
`sha256:5e704aaa91c18cd69970c7249552af9b3f4b704be01956c934b4af336fcce4de`.
They verify isolated exact-byte namespace reads and
actual rejection of invalid financial evidence. They do not establish a
successful production proof or authorize corpus deletion.

The final test snapshot was compressed into `final-source.tar.gz`, then every
archive member was checked against the source manifest before its disposable
directory and generated test caches were removed. The archive hash and exact
logical byte counts are in `final-source-retention.json`. These are reproducible
test-source records, not a production financial verification package. Logs,
manifests and focused evidence remain in `diagnostics/compact-graph-next/`.

## Remaining acceptance limits

All 700 originals remain the yield denominator. The absent historical run's
private evidence has not been reconstructed. No new 700-entry terminal ledger,
full-cohort rebuild, production qualification or before/after yield is claimed.
The installed runner still lacks original-before-conversion binary observations
and the corresponding accepted signed receipt chain; all 58 binary originals
remain unsupported for adoption. Workbook 0117 still needs its trusted
data-table observation chain. Missing external dependencies, missing historical
curation and non-unique recurrences remain explicit blockers.

The original 0266 AST-only diagnostic constructed 21,410,104 nodes and
35,083,523 edges and completed SCC analysis. The guarded process peaked at
3,168,649,216 bytes RSS (2.95 GiB), below its 8 GiB limit. It hit the fixed
1,800-second wall-time limit during node CSV export; a recorded partial file
had reached 2,078,815,938 bytes. There was no disk-reserve stop. Original source
and all four selected implementation hashes remained unchanged, and the entire
owned temporary database/export directory was removed.

This establishes a real-source memory improvement, not successful production
conversion. No complete CSV hashes, independent reconstruction, source approval
or task publication resulted. The default 900-second corpus-build allowance is
also insufficient. A larger explicitly configured budget and complete downstream
revalidation would be required before claiming recovery; no limit was raised
during the diagnostic. Evidence is under
`diagnostics/compact-graph-benchmarks-20260910/0266-ast/`.
