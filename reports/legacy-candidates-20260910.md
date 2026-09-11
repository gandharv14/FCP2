# Legacy candidate preparation and sparse range projection

The pipeline now has an explicit producer for isolated `.xls` and `.xlsb`
conversion candidates. It does **not** establish binary-to-OOXML financial
equivalence or make a workbook eligible for publication. Candidate records remain
`conversion_unverified`; the existing source inventory adoption gate is unchanged.
No real LibreOffice conversion, Excel execution, or task generation was run for
this change.

## Frozen source identities

The regenerated `diagnostics/reliability-20260910/raw-inventory.json` contains all
700 originals. A read-only check of its 58 binary files found 45 OLE `.xls` and 13
`workbook.bin` `.xlsb` containers, with zero format or hash mismatches. OLE magic
takes precedence over incidental embedded ZIP streams. These counts are source
inventory counts, not verified conversions or promoted tasks.

## Explicit candidate CLI

Use Python 3.12 from the pipeline environment. Select the installed converter's
actual executable directly, its application directory, and exact executable
hashes. The following configuration schema is closed; replace the example paths
and hash placeholders with verified local values:

```json
{
  "schema_version": "legacy-candidate-config/v1",
  "converter": {
    "executable": "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "sha256": "<64 lowercase hex characters>",
    "application_root": "/Applications/LibreOffice.app",
    "sandbox_sha256": "<SHA256 of /usr/bin/sandbox-exec>"
  },
  "timeout_seconds": 120
}
```

The paths must be canonical absolute paths without symlinks. The executable must
belong to the selected application. This example does not assert that the shown
application is installed or qualified on a particular host. Find the actual
executable and hash it before selecting it:

```sh
shasum -a 256 /absolute/path/to/converter
shasum -a 256 /usr/bin/sandbox-exec
shasum -a 256 /absolute/path/to/legacy-config.json
shasum -a 256 /absolute/path/to/raw-inventory.json
```

The inventory pin is the SHA256 of the exact JSON **file bytes**, not the
`inventory_sha256` field inside that file. Every selection must be explicit:

```sh
.venv/bin/python scripts/legacy_candidates.py \
  --inventory /absolute/path/to/raw-inventory.json \
  --inventory-sha256 <inventory-file-sha256> \
  --config /absolute/path/to/legacy-config.json \
  --config-sha256 <config-file-sha256> \
  --workbook-id 0038 \
  --workbook-id 0127 \
  --output-root /absolute/path/to/fresh-candidate-campaign
```

The output root must be fresh and its parent must exist. The example IDs must
actually be legacy originals in the pinned inventory. The CLI rejects ambiguous
identities, native sources, duplicate selections, changed configuration, and
changed inventory bytes. It runs selected workbooks sequentially and retains an
individual failure without classifying the original source as invalid. Exit code
`0` means every selected candidate preparation completed, `1` means an individual
conversion failed, and `2` means the selected configuration or campaign could
not be accepted. None of these codes establishes financial equivalence.

The OS sandbox denies networking, host writes, Mach lookup and child-process
forking. It permits only the selected executable, declared system/application
reads, and writes inside a disposable per-workbook workspace. LibreOffice receives
an isolated user profile with the highest macro security setting. Unsupported
sandbox or application requirements fail closed; hermetic command tests do not
establish successful host execution. Do not relax the sandbox to force a pass.

A completed workbook directory contains its candidate XLSX, `candidate.json`,
the independent candidate snapshot, exact command, macro-profile template,
sandbox profile, raw OOXML calculation provenance and stdout/stderr evidence. The manifest binds their hashes plus
the original path, format, hash and workbook identity. Published candidate files
are read-only. Interrupted or failed attempts retain diagnostics without a
completed manifest. Existing directories are never overwritten or reused;
`xl_legacy_conversion.validate_candidate()` revalidates completed evidence but
does not authorize adoption.

## Financial proof remains separate

The initial neutral observation contract supports scalar cells and formulas,
typed values, number formats, sheet identity/state, merged ranges, date epoch and
financial calculation settings. Neutral semantics and the binary observation
contract are version 2. The calculation settings are effective calculation mode,
reference mode, iteration enabled/count/delta and full precision, with
SpreadsheetML schema defaults applied to missing candidate XML attributes.
The original binary observer must independently read these effective values
before changing calculation controls. It must never infer them from the candidate.
Raw OOXML `calcPr` attributes, including engine/cache/rebuild/thread hints, remain
in `candidate-provenance.json`; they cannot establish original semantics or
replace the receipt's exact output-byte binding. Microsoft documents the
[calculation-property fields](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.calculationproperties?view=openxml-3.0.1).
The contract conservatively rejects features its observation cannot
fully describe, including defined names, array/shared/data-table formula
encodings, dynamic/time/random references, external relationships, macros,
tables, drawings and other unsupported worksheet features. It does not replace
formula expressions with cached calculated answers.

`verify_equivalence()` compares an independently supplied original-binary
observation with a fresh OOXML observation. A real trusted signature must bind
the original and candidate hashes, both observation hashes, the selected observer
implementation hash, observation before SaveAs, and completed isolated
recalculation. Its result explicitly states `inventory_adoption_supported: false`.
The tests use a synthetic observation and a test signing key to exercise the
contract; they do not prove an actual binary workbook equivalent.

The installed `scripts/recalc/excel_runner.py` currently recalculates OOXML and
grafts caches back into the original OOXML package. Its automation does not export
an original binary cell/formula/value/format observation before conversion, and
its receipt does not contain the new conversion-observation claims. Complete
legacy adoption therefore still requires:

1. A separately tested, installed and hash-pinned trusted binary observation
   producer that captures original semantics before SaveAs under the existing
   isolation controls.
2. Signed observations and authoritative recalculation evidence for real binary
   originals and their immutable candidates.
3. A versioned inventory certificate consumer with tamper and feature-coverage
   regressions; the existing v1 self-asserted certificate rejection stays intact.
4. The same segmentation, independent reconstruction, disclosure, leak,
   publication and runtime gates required of native workbooks.

### Concrete trusted producer integration proposal

This is a read-only design, not an installed capability. The inspected host has
the ordinary `excel_runner.py` and `harbor-excel-sandbox` deployment. The separate
table quarantine dispatcher/window exist in the repository but were not present
in that installed runner directory. Neither dispatch can accept binary workbooks
unchanged: ordinary requests require `.xlsx` and cache-only changes; table
quarantine requests require an independently inspected OOXML data table.

The smallest correct addition is a separate observation-only profile, rather
than an exception to either existing request type:

- Add `scripts/recalc/legacy_observer.py`, a root-installed stdlib dispatcher
  with no workspace imports. Reuse the hash-pinned ordinary runner's protected
  configuration, dedicated Excel account/staging, PF enforcement and
  `sign_payload()` helpers. Use a new closed `legacy-observation-request/v1`
  schema binding a UUID, execution interval, original path/hash/format/size,
  workbook ID, protected output path, observer/policy hashes, public-key hash,
  exact permitted Excel version and `scope: observation_only`. It grants neither
  source nor task approval.
- Add a fixed, root-owned native observation script. Before opening a private
  byte-identical binary copy, enforce the existing macro/event/add-in/link
  controls. Read effective original settings before forcing recalculation; then
  capture every supported original cell/formula/value/number-format, sheet and
  merge observation **before SaveAs**. Bound cell/output counts and reject missing
  or unsupported observations rather than truncate. Record formula results
  separately from formula expressions; they must never become supplied inputs.
  Export to a fresh XLSX, close the binary workbook, and independently reopen,
  recalculate and observe the candidate. Recheck original bytes throughout.
- The installed Excel scripting dictionary contains native `formula`/`formula2`,
  `has formula`, `has array`, `value2`, `number format`, `merge area`, `used range`,
  date system, iteration settings, reference style, precision and `has vb project`
  properties. These are concrete candidate APIs, not proof that the automation
  works. The script must explicitly reject unsupported features on the original
  as well as the candidate, including macros/names/arrays/tables/links and
  unsupported sheet or workbook objects. Locale-safe JSON encoding, typed
  errors/booleans/numbers and sparse-cell completeness need host fixtures.
- Independently parse the final candidate's OOXML under the selected neutral v2
  contract. Sign the ordinary Ed25519 receipt envelope with its existing
  request/source/output/isolation/calculation fields plus `conversion_policy`,
  `observation_schema`, both observation hashes, observer implementation hash,
  `observed_original_before_save_as`, `original_recalculation_complete` and
  `candidate_recalculation_complete`. The protected implementation must derive
  these claims itself. Retain original/candidate/result observations and reject
  disagreeing supported calculations; a caller-supplied `equivalent` flag or
  observation document must never cause signing.
- Add `scripts/recalc/legacy_observation_window.py` with the existing shared
  `/var/run/fcp2-recalc-window.lock` protocol, inherited lock validation, process
  group shutdown, interface restoration and private diagnostics. Keep derived
  pipeline validation under the invoking user, using its persistent Python 3.12
  environment after networking is restored. Root execution remains isolated
  system Python. Reserve request IDs durably and publish complete evidence by
  exclusive atomic rename; interruption must not make a request replayable.
- Add `scripts/recalc/maintain_legacy_runner.py` using the existing maintenance
  transaction pattern: exact reviewed source hashes, compare-and-swap deployment
  state, backups, root-owned observer/window/config/journal/artifact directories,
  and unchanged private-key inode/public-key binding. Reuse the existing key
  without rotating it or exposing it to pipeline code. A local reviewed sudo
  installation is necessary; the existing ordinary runner's narrow sudoers
  invocation cannot safely be repurposed as a generic new-command dispatcher.

This requires a new closed request/config profile, but not a new business-approval
flag. Observation remains separate from source adoption. A future certificate
consumer in `xl_source_inventory.py` must validate a versioned conversion proof,
the explicitly selected trusted observer and complete evidence before allowing
`conversion_equivalent`; current v1 rejection remains untouched until that work
and its regression coverage are complete.

Hermetic tests should cover request closure/expiry/format binding, wrong invoker,
changed source/candidate/policy/key, forged or incomplete observations, unsupported
original features, immutable publication conflicts, interrupted reservations,
shared lock validation and failed network restoration. Explicit host-control
tests must exercise tiny independently specified `.xls` and `.xlsb` fixtures,
apostrophes/Unicode/financial formats, all financial settings, altered formulas,
and rejection fixtures containing macros, links, arrays and data tables. They
must demonstrate native original-before-SaveAs capture and independent candidate
agreement without workbook macros, cache seeding, model calls or task promotion.

## Sparse projection

`xl_seg/project.py` now intersects shared ranges with an index of existing cell
IDs instead of enumerating every coordinate in the rectangular area. It retains
the existing A1 parser, exact sheet identity, reversed endpoints, canonical cell
IDs and row-major ordering. Absolute and multi-area coordinate strings remain
unsupported by this particular projection helper, as before. The index is built
only when a range exists. Truncation flags remain unchanged for downstream proof
gates; faster projection does not approve incomplete evidence.

A synthetic maximum-size range, `A1:XFD1048576`, represents 17,179,869,184 possible
coordinates. With five stored cells, the helper returned its three actual members
in approximately 55 microseconds with 1,880 bytes of traced peak allocation for
that call. This is a bounded fixture measurement, not a corpus performance claim.

## Focused validation

```sh
.venv/bin/python -m pytest -q \
  tests/test_legacy_conversion.py \
  tests/test_legacy_candidates_cli.py \
  tests/test_conversion_equivalence_trust.py

.venv/bin/python -m pytest -q \
  tests/test_sparse_project_ranges.py \
  tests/test_disk_seg_model.py \
  tests/test_seg_model.py
```

Results: **64 legacy tests passed** and **65 projection/model tests passed**.
These include synthetic signed-receipt validation, altered or missing evidence,
conversion interruption, explicit configuration pins, preservation of the
700-original denominator, closed reference grammar, sparse maximum-range
projection, and memory/SQLite graph equivalence. Full integrated validation is
reported separately.
