# Signed macOS Excel runner

This directory contains the maintained deployment source for
`xl_source_recalc.py`'s `MacOSExcelEngine`. It does not import pipeline code.
The production copy is root-owned and hash-bound by its isolation attestation.

The deployment has three trust boundaries:

- The normal automation user owns only the private candidate and request.
- A root-owned wrapper uses a narrow sudo rule to start the maintained runner.
  The runner drops Excel automation back to the invoking console user.
- Only the root process can read the Ed25519 private key. Linux VMs receive
  only `excel-runner-public.pem`.

The runner refuses non-macOS hosts, a missing Excel app, an already-running
Excel session, enabled add-ins, expired or incorrectly bound requests, unsafe
paths, partial OOXML saves, unapproved Excel versions, and altered originals.
Excel opens the private candidate with alerts, events, link updates, and
Auto_Open execution disabled. It forces a full dependency rebuild, saves that
same candidate, closes it, and quits the dedicated Excel process.

## Preflight and root install

Use a root-owned OpenSSL 3 binary. The default below is the MacPorts path.
Homebrew under a user-writable prefix does not satisfy the runner's trust
checks.

```bash
cd /path/to/FCP2

python3 scripts/recalc/install_runner.py plan \
  --automation-user "$USER" \
  --openssl /opt/local/bin/openssl

python3 scripts/recalc/install_runner.py preflight \
  --automation-user "$USER" \
  --openssl /opt/local/bin/openssl
```

After the dedicated VM has no default network route, uses a dedicated login
session/profile, and has no Excel add-ins, the remaining root installation is:

```bash
sudo python3 scripts/recalc/install_runner.py install --apply \
  --automation-user "$USER" \
  --openssl /opt/local/bin/openssl \
  --confirm-dedicated-session \
  --confirm-network-disabled \
  --confirm-macros-disabled \
  --confirm-add-ins-disabled \
  --confirm-link-updates-disabled \
  --confirm-prompts-suppressed
```

Then check the installed ownership, modes, and hashes:

```bash
sudo python3 scripts/recalc/install_runner.py check \
  --automation-user "$USER" \
  --openssl /opt/local/bin/openssl
```

The installer prints these paths:

- sandbox runner:
  `/Library/Application Support/FCP2/recalc/harbor-excel-sandbox`
- isolation attestation:
  `/Library/Application Support/FCP2/recalc/excel-isolation-attestation.json`
- VM-safe public key: `/etc/harbor/excel-runner-public.pem`
- root-only private key: `/etc/harbor/excel-runner-private.pem`

Copy only the public key to each Linux VM under a root-owned, non-writable
directory. Never copy the private key or runner configuration.

## Local execution

An installed runner may enter a temporary offline window on the configured
Mac. `install_runner.py preflight` reports `ready_for_offline_window` separately
from the live `ready` verdict; execution still requires actual loopback-only
interfaces and the signed runner's independent network enforcement.

Prepare requests from a frozen corpus inventory using the persistent project
Python environment (temporary `uv run --with` environments must not be embedded
in delayed execution scripts):

```bash
.venv/bin/python -m scripts.recalc.prepare_offline_batch \
  --inventory .corpus-runs/rebuild/inventory.json \
  --output /path/to/recalc-recovery/new-request-batch \
  --workbooks 0266 0332
bash /path/to/recalc-recovery/new-request-batch/run-local.sh
```

The local command prompts for sudo to change interface state. It validates the
Python runtime before changing the network, serializes Excel execution, and
restores previously active interfaces on completion or interruption. Requests
expire after seven days; expired or incomplete results cannot be published.
Existing request/result artifacts are never overwritten. A failed readiness
check or unavailable sudo is an infrastructure blocker, not source invalidity.

Create a request on the Linux VM with the installed public key hash. Transfer
the request and source as one bundle:

```bash
python3 scripts/recalc/transfer_bundle.py export-request \
  --request run/request.json \
  --source source.xlsx \
  --output request-bundle.zip
```

On the macOS VM:

```bash
python3 scripts/recalc/transfer_bundle.py import-request \
  --bundle request-bundle.zip \
  --destination run/incoming

python3 xl_source_recalc.py execute run/incoming/request.json \
  --source run/incoming/source.xlsx \
  --allowed-root run/recalculated \
  --isolation-attestation \
    "/Library/Application Support/FCP2/recalc/excel-isolation-attestation.json" \
  --sandbox-runner \
    "/Library/Application Support/FCP2/recalc/harbor-excel-sandbox" \
  -o run/result.json
```

Export the result:

```bash
python3 scripts/recalc/transfer_bundle.py export-result \
  --request run/incoming/request.json \
  --result run/result.json \
  --workbook run/recalculated/source.xlsx \
  --output result-bundle.zip
```

On Linux:

```bash
python3 scripts/recalc/transfer_bundle.py import-result \
  --bundle result-bundle.zip \
  --destination run/returned
```

Bundle import verifies every member hash and refuses existing destinations.
`xl_source_recalc.py` still verifies the Ed25519 receipt, request binding,
output hash, completion time, Excel version, semantic equivalence, and original
source hash before publishing the candidate.

## Data-table observations and maintained deployments

Ordinary recalculation does not accept workbooks with data tables. The separate
`table_quarantine_runner.py` records complete table observations and a signed
intermediate under the dedicated offline session. `table_quarantine_window.py`
uses system Python for protected controls and checks the persistent Python 3.12
environment before disabling interfaces. Pipeline-derived semantic evidence is
assembled as the normal user after networking is restored. Full private output
and failure diagnostics are retained; the terminal receives a short result.

For an existing deployment, `maintain_table_runner.py` provides `prepare`,
`plan`, `check`, `apply` and `recover`. The coordinator reviews exact bundle and
deployment-plan hashes, then uses a hash-checked bootstrap to copy reviewed code
into protected staging before invoking it as root. Installation changes the
observer implementation binding and adds the separate producer/window/config;
existing key references and bytes remain intact. Each replacement has a
compare-and-swap precondition and protected rollback evidence. New requests are
created only after the installed transaction passes its postcheck. See the
[full maintenance and recovery contract](../../reports/table-maintained-installer-proposal.md).

Successful observation is an intermediate result. The consuming pipeline still
requires independent original-to-intermediate and intermediate-to-candidate
proof, unchanged curation, complete reconstruction, certified exclusion of
presentation tables, leak scanning, explicit inventory approval and all normal
source, segmentation and Harbor gates.
