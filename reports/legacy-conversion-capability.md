# Legacy binary conversion capability and trust audit

Read-only corpus/runtime audit completed, followed by the separately authorized fail-closed inventory validator repair. No Excel, model, network, converter or installed-runner execution occurred. No source workbook, historical approval or installed runner was modified.

## Evidenced counts

| Observation | Count |
|---|---:|
| Original legacy workbooks | 58 |
| `.xls` originals, verified OLE compound containers | 45 |
| `.xlsb` originals, verified workbook.bin packages | 13 |
| Original hashes matching frozen inventory | 58 |
| Candidate workbooks bound by the current legacy ledgers | 0 |
| Conversion-equivalence evidence bound by those ledgers | 0 |
| Trusted recalculation results belonging to legacy originals | 0 |
| Independently proven binary-to-OOXML conversions | 0 |
| Misnamed OOXML workbooks suitable for byte-identical renaming | 0 |
| XLSB packages with macro parts | 9 |

The 58 figure counts **original inputs requiring conversion and proof**, not completed conversion candidates. The current ledgers retain `conversion_requires_authoritative_evidence`. Searches remained within relevant corpus and pipeline run/source artifacts; no broader user-directory search was performed.

Four OLE originals also return true from Python's `zipfile.is_zipfile` because they contain incidental ZIP payloads for drawing/theme material. They remain OLE workbooks. A ZIP probe alone must not establish that an `.xls` is actually OOXML. No workbook values were inspected.

The private rebuild diagnostic `diagnostics/legacy-conversion-capability/capability.json` binds every legacy original and ledger, inventories container features, and records available runtime/runner metadata. Macro-part observations are capability constraints, not proof that a particular workbook invokes macros or is irreparably invalid.

## Existing capabilities and boundaries

- Pipeline Python 3.12 has openpyxl 3.1.5, but no xlrd, pyxlsb, python-calamine or olefile. The configured bundled Python 3.12.14 environment has the same reader gap; bundled Node has no xlsx, exceljs or @e965/xlsx packages.
- Bundled LibreOffice 26.8.0.0 and its isolated-profile wrapper exist. A profile directory is not proof of network/macro isolation, and LibreOffice conversion does not prove faithful Excel semantics. It was not executed.
- Configured trusted Microsoft Excel 16.112.2 exists. The installed runner implementation hash matches its configuration. Existing source-recalc requests and runner validation accept `.xlsx` only. Their policy permits cache-only changes, and the runner grafts caches back into the original OOXML package after requiring exact formula-cell sets and formula encoding. That mechanism cannot graft an XLSB/BIFF original into a new OOXML representation.
- `semantic_workbook_snapshot` requires XML OOXML workbook parts. Read-only probes rejected original 0038 (`.xls`) and 0127 (`.xlsb`). It captures formulas separately from caches, typed inputs, names, sheets, merges, calculation settings, relationships and other package parts, but has no BIFF/XLSB decoder or neutral cross-format equivalence producer.
- Existing trusted receipts bind recalculation request, `.xlsx` source/output hashes, engine and isolation. None of the 15 available trusted results belongs to the 58 legacy originals. They do not constitute binary conversion receipts.

These are primarily missing producer/decoder/integration capabilities. The originals are available and hash-valid; lack of conversion evidence must not be reported as missing raw data or successful conversion.

## Reproduced and repaired validator defect

Before the repair, `_validate_equivalence_certificate` accepted a `source-conversion-equivalence/v1` document containing correct source/candidate hashes, `equivalent: true`, `verification.deterministic: true`, an arbitrary nonempty method and a self-computed certificate hash. No binary semantics or trusted receipt was required. `reproduction.log` demonstrates that this document was accepted despite the regression expecting rejection.

The repaired validator still checks historical structural bindings, then rejects the unsupported equivalence-proof contract explicitly. Fake receipt fields, equal asserted snapshot hashes, or a method named “trusted Excel” cannot enable it. Inventory building and existing-manifest revalidation both fail closed for these purported equivalent conversions.

The serialized inventory schema remains v3 (with v2 reading retained); native inventories and `conversion_unverified` evidence behavior remain unchanged. The `source-conversion-equivalence/v1` identifier is retained to diagnose historical documents, but it grants no equivalence capability. Historical approvals are untouched. Any existing inventory relying on these self-asserted equivalent documents now fails validation and requires actual new proof, not a renamed method or rehashed assertion.

Changes: `xl_source_inventory.py`, `tests/test_conversion_equivalence_trust.py` (eight new regressions). Focused result: **79 passed, one skipped** across conversion trust, source health, source recalculation and inventory approval tests; `fixed-tests.log` records the result.

## Minimal supported trusted proof producer

A future implementation should introduce a distinct conversion request/receipt and a neutral cross-format semantics contract. Extending the existing `.xlsx` suffix check alone is unsafe.

1. Bind the original binary bytes, detected container type, workbook identity, policy and producer version into a fresh expiring request. Preserve the original; stage a private copy under the existing dedicated-user, macro/add-in-disabled, link-update-disabled, network-isolated execution controls. Serialize Excel execution.
2. Establish the **original binary workbook semantics before Save As**, independently of the converted OOXML. This requires either a complete supported BIFF/XLSB decoder or an explicitly validated authoritative Excel object-model snapshot under a closed feature profile. Do not produce both “before” and “after” snapshots from the converted file. Merely exposing reader APIs for cell values or converting through LibreOffice is insufficient.
3. The first feature profile should reject unsupported or unobservable constructs: VBA/XLM execution, external links/connections, data tables, dynamic arrays/implicit-intersection ambiguities, unsupported named formulas and other opaque calculation state. The snapshot must cover original formula identity and references, literal value types, exact sheet names/order/visibility, names, merges, date system, financial number formats, precision and calculation/iteration settings. A primitive-only workbook still requires proof that no hidden formulas, names, queries or macros supply meaning.
4. Perform Save As to OOXML in trusted Excel, record any repair/conversion warning and fail closed on unreviewed losses. Reopen and snapshot the result through the same supported semantics contract, then independently parse its OOXML. Require complete semantic equality with the original snapshot. Formula result caches are a separate ledger and may never establish semantic equivalence or become replacement assumptions.
5. Recalculate the verified candidate authoritatively where required and require deterministic output reconstruction against its trusted caches. Keep cache refresh separate from format equivalence. Bind original/candidate hashes, original/converted snapshot hashes, feature inventory, comparison result, engine/version, isolation controls, request identity/expiry and producer hash into a verified signed receipt. A hash-bound self-assertion is not a receipt verifier.
6. Add a new executable certificate verifier and supported schema only after accepted/rejected tests pass. Fixtures must reject changed binary bytes, altered snapshots/receipts, omitted formulas or hidden sheets, type/date-system changes, lost names/formatting, duplicate identities, unsupported features, cached-answer substitution, replay/expiry, interrupted conversion and publication conflicts. Independently verify converted OOXML structure and receipt signatures before allowing `conversion_equivalent`.
7. Integrate the validated candidate and its original lineage into a new versioned source inventory and immutable source generation. Rerun source health, segmentation/reconstruction, all quality gates and final Harbor publication checks. Conversion alone never counts as a promoted workbook.

No available legacy case is independently proven by the current tooling. The next actionable work is implementing and validating this closed trusted producer, not rerunning the existing `.xlsx` recalculation runner against renamed binary files.
