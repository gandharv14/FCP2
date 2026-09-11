# Maintained table-runner installation — isolated proposal

The new standalone `scripts/recalc/maintain_table_runner.py` updates an **existing** protected ordinary Excel deployment and adds the separately reviewed table producer/window/config. It neither installs nor runs anything merely by importing the module. This proposal executed only synthetic layout tests and system-Python `--help`; no installed files, source requests, approvals, Excel, model service or network state were changed.

## Scope and trust

The tool uses only the system Python standard library and loads under `/usr/bin/python3 -I` (macOS system Python3.9). It never imports the pipeline or executes the reviewed producer during installation. `prepare` records the exact observer, producer, window, profile, source-recalculation and source-health module paths/hashes. `plan` requires the independently reviewed bundle file hash and captures the exact current deployment. `apply` requires the independently reviewed plan file hash and macOS root. Hash verification and JSON parsing use the same single byte read; there is no reviewed-file swap gap.

The fixed managed destinations are the existing observer, separate producer/window, existing base config/attestation, and separate table config. The existing base and attestation must agree with the actual installed observer and public key. Both require the original six isolation controls as literal true. The configured dedicated automation account must resolve to the proposed UID. Engine versions must be concrete, unique dotted numbers.

The installer changes only `runner_implementation_sha256` in the existing base config and attestation. Every other field, including private/public key references, public-key hash, isolation settings, automation account and wrapper identity, remains unchanged. It never opens or hashes the private key. Instead it verifies its protected0600 single-link metadata before and after and never targets its inode for any write. Public-key bytes are hash-checked before and after. Key aliases and unexpected filenames reject before private-key access. There is no key-generation, rotation, sudoers, subprocess or network-control implementation.

## Transaction and recovery

Apply holds the same protected offline-window file lock as ordinary/quarantine recalculation. It checks the complete current deployment against the reviewed plan, then creates a fresh root-owned0700 transaction directory under `/var/lib/harbor/table-install/journal`. All existing managed files—including observer, config and attestation—are backed up and fsynced in that journal before any deployment replacement. Key files are never copied into backups.

Each file replacement is an atomic same-directory temporary-file/fsync/replace/directory-fsync operation with a second expected-state check immediately before replacement. Installation order is explicitly observer, producer, window, base config, attestation, table config, independent of JSON key order. The separate table config is enabled last. Existing ordinary validation fails closed during an interrupted implementation/config mismatch. The six-file update is a journalled transaction with atomic individual replacements; it does not claim impossible cross-file atomicity.

The journal retains the exact reviewed plan, old/new hashes/modes, backups and completed stages. Catchable failures retain a bounded diagnostic. A hard crash before the completion list is updated is recoverable: recovery inspects every current file and accepts only its exact pre-install or planned post-install state. It validates **all** backups and destinations before restoring anything, restores in reverse order and leaves absent-before files absent. It refuses unfamiliar later changes instead of overwriting them. Recovery itself may be interrupted and repeated. Transaction IDs and journals are never deleted/reused; created protected empty directories remain. Failure while making backups occurs before any deployment mutation and preserves the reserved preparation directory for diagnosis.

`check --plan ... --plan-sha256 ...` verifies a pre-install plan. `check --transaction-id ...` verifies completed installed bytes, existing key preservation and config/attestation consistency. Neither is a claim of successful Excel execution. Actual offline/isolation enforcement and signed observation remain the producer/window's separate mandatory runtime gates.

## Later parent-owned preparation and Terminal handoff

1. Review/integrate this installer, the producer/window, revised profile, two-edge consumer, inventory adapter and controller. Freeze final live policy bytes, including the reviewed streaming source-snapshot implementation. Do not reuse a stale selected-policy template hash.
2. As the normal user, `prepare` a hash-bound module bundle and `plan` against the existing protected deployment with its independently checked dedicated UID and exact Excel version. Preserve both files and provide their hashes for final review. No0117 request is generated by these commands.
3. The parent should bind the exact installer file SHA in its eventual root bootstrap, copy those reviewed installer bytes into a fresh root-protected staging location, verify that copy and invoke system Python `-I` against it. Do not import or execute mutable pipeline modules as root. `apply` then pins the exact reviewed plan SHA and rechecks every source/deployment dependency; any intervening edit requires a new reviewed plan.
4. Confirm the protected artifact root and its parents are traversable by the dedicated invoker, while the private journal remains0700. The tool creates new artifact directories0755 and journal directories0700 and refuses conflicting existing directory modes. Verify installation with `check --transaction-id`; preserve existing key references/bytes and all rollback evidence.
5. Only after root integration/installation and file checks should the parent prepare a fresh new-domain0117 request and concise `run-local.sh` using the persistent user Python3.12 precheck followed by the protected system-Python offline window. The old ordinary request must not be retried or relabelled. The parent owns the final Terminal handoff; this proposal creates no actual request or user command.
6. The wrapper must still retain full private stdout/stderr, actual staging, observations, signed receipt and independently derived result. A signed quarantine result alone is not source approval: current two-edge reconstruction/materialization/leak proof, explicit versioned inventory approval, final source/segmentation proof and all Harbor gates remain necessary.

## Tests

36 synthetic tests pass. They exercise complete install/rollback, interruptions after each of six atomic replacements, a real child exiting immediately after replacement before its journal progress update, exact-hash CAS rejection, changed reviewed code, altered backups, unknown later bytes, key metadata/byte preservation, a private-key read guard, rehashed arbitrary-destination/plan rejection, single-read hash/parse behavior, concrete engine versions, duplicate transaction rejection and post-install verification. Only synthetic ownership/parent metadata and dedicated-account lookup are substituted; filesystem writes, fsync, flock, real child exit and recovery run on temporary fixture files. The system-Python isolated help check requires no pipeline dependencies.

```
.venv/bin/python -B -m pytest .corpus-runs/20260908/diagnostics/table-maintained-installer-proposal/tests -q
```
