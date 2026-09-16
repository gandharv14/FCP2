"""Explicit, hash-bound central history projection for fresh fleet admission.

The coordinator captures the real full namespaces while legacy dispatch locks
are held. Remote admission consumes the complete survey, not empty substitute
directories. Historical tasks and source/checkpoint bytes are never migrated by
this adapter. The selected worker must hold the coordinator workbook claim.
"""
from pathlib import Path
import hashlib
import json
import os
import time

SCHEMA = "fleet-central-history/v1"

def binding(path):
    p = Path(path).absolute()
    return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

def capture(roots, known_ids, selected_ids, pause, destination, *, supplemental_roots=()):
    from scripts.batch.harbor_admission import survey_namespaces
    if os.environ.get("HARBOR_FLEET_HISTORY_PATH"):
        raise ValueError("Cannot capture a projection from another projection")
    pause_value = json.loads(Path(pause).read_text())
    if pause_value.get("new_dispatch_allowed") is not False:
        raise ValueError("Owning local dispatch pause required")
    scan_roots=list(dict.fromkeys([*map(str,roots),*map(str,supplemental_roots)]))
    excluded, histories = survey_namespaces(tuple(map(Path, scan_roots)), set(known_ids))
    if not selected_ids or not set(selected_ids) <= set(known_ids):
        raise ValueError("Explicit known fresh selection required")
    if any(w in excluded or histories.get(w) for w in selected_ids):
        raise ValueError("Selected task has history; supported continuation required")
    value = dict(schema_version=SCHEMA, roots=list(map(str, roots)), scan_roots=scan_roots,
        known_ids=sorted(known_ids), fresh_ids=sorted(selected_ids),
        excluded=excluded, histories=histories, pause=binding(pause),
        captured_at_ns=time.time_ns(),
        constraint="Only valid while the owning coordinator holds legacy dispatch locks and this workbook claim")
    raw=(json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
    p=Path(destination)
    with p.open("xb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)

def inspect_projection(roots, known_ids, workbook, selected):
    """Validate evidence only; this does not acquire or attest live custody."""
    path=selected["path"]
    expected=selected["sha256"]
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError("Central history projection changed")
    value=json.loads(raw)
    if value.get("schema_version")!=SCHEMA:
        raise ValueError("Unsupported central history projection")
    if list(map(str,roots))!=value["roots"] or sorted(known_ids)!=value["known_ids"]:
        raise ValueError("Central history namespace or denominator changed")
    if not set(value['roots']) <= set(value['scan_roots']):
        raise ValueError('Central history omitted a required namespace')
    if workbook not in value["fresh_ids"] or workbook in value["excluded"] or value["histories"].get(workbook):
        raise ValueError("Central history does not authorize fresh admission")
    return value["excluded"],value["histories"]


def projected_survey(roots, known_ids):
    """Admission still requires a real owning heartbeat after evidence checks."""
    workbook=os.environ["HARBOR_FLEET_WORKBOOK"]
    evidence=inspect_projection(roots,known_ids,workbook,
        {"path":os.environ["HARBOR_FLEET_HISTORY_PATH"],"sha256":os.environ["HARBOR_FLEET_HISTORY_SHA256"]})
    # The hash-selected file is evidence; live custody is still required.
    from scripts.fleet.coordinator import Client
    client=Client(os.environ["HARBOR_FLEET_URL"],os.environ["HARBOR_FLEET_TOKEN_FILE"])
    client.call("heartbeat", workbook=workbook,
        owner=os.environ["HARBOR_FLEET_OWNER"],
        token=os.environ["HARBOR_FLEET_CLAIM_TOKEN"],
        fence=int(os.environ["HARBOR_FLEET_FENCE"]), lease_seconds=180)
    return evidence
