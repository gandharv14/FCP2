#!/bin/bash
# Two-phase disk cleanup for one VM. Phase 1 (default) only lists; phase 2 deletes
# exactly the listed items and needs the token phase 1 printed.
#
# usage: clean_vm.sh HOST [--retire RUN_DIR_NAME ...]                 # plan
#        clean_vm.sh HOST --apply --token SHA [--retire RUN_DIR_NAME ...]   # apply
#
# --retire NAME  treat that run directory (e.g. fcp-recursive-b2-20260903-0130-vm3)
#                as finished so its work-*, baseline, and venv become candidates.
#                Its output-* and state-* are never candidates.
#
# The agent must show the phase-1 list to the user and get an explicit yes
# before running --apply. Refuses on busy hosts; refuses on token mismatch.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

HOST=""; MODE=plan; TOKEN=""; RETIRE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) MODE=apply; shift;;
    --token) TOKEN="$2"; shift 2;;
    --retire) RETIRE="$RETIRE $2"; shift 2;;
    -h|--help) sed -n '2,13p' "$0"; exit 0;;
    *) HOST="$1"; shift;;
  esac
done
[ -n "$HOST" ] || die "HOST is required"
[ "$MODE" = "plan" ] || [ -n "$TOKEN" ] || die "--apply needs --token from the plan output"

gssh "$HOST" --command "MODE=$MODE TOKEN='$TOKEN' RETIRE='$RETIRE' bash -s" < "$SCRIPTS_DIR/clean_remote.sh" 2>&1 | strip_noise | python3 -c '
import sys
rows=[]; other=[]
for line in sys.stdin:
    line=line.rstrip("\n")
    if line.startswith("CAND|"):
        _,kind,path,b=line.split("|",3)
        try: b=int(b)
        except ValueError: b=0
        rows.append((kind,path,b))
    else:
        other.append(line)
for l in other:
    if not l.startswith(("SUMMARY","TOKEN","CLEAN_")): print(l)
if rows:
    print("%-13s %7s  path" % ("kind", "GiB"))
    for kind,path,b in rows: print(f"{kind:<13} {b/2**30:>7.2f}  {path}")
for l in other:
    if l.startswith(("SUMMARY","TOKEN","CLEAN_")): print(l)
'
exit "${PIPESTATUS[0]}"
