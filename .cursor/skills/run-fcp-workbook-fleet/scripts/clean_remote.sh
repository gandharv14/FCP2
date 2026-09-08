#!/bin/bash
# Runs ON a VM (fed over stdin by clean_vm.sh). Two phases:
#   MODE=plan   list deletable cache/stale paths with sizes, print TOKEN. Deletes nothing.
#   MODE=apply  recompute the list, refuse unless its token equals TOKEN, then delete.
# Env: MODE, TOKEN, RETIRE (space-separated run dir names the operator declared retired).
#
# Never listed: output-*/, state-*/ of any run, verification_manifests/, .env,
# any BASE/VENV bound by a non-retired run, /tmp/fleet-deploy-*.lock, $RUN/logs.
set -uo pipefail
MODE="${MODE:-plan}"; TOKEN="${TOKEN:-}"; RETIRE="${RETIRE:-}"
R="$HOME/GDM_FCP"
echo "CLEAN_BEGIN host=$(hostname -s) user=$(id -un) mode=$MODE"

workers=$(pgrep -fc '[s]cripts.batch.recovery_worker' || true)
if [ "${workers:-0}" != "0" ]; then echo "CLEAN_REFUSED busy workers=$workers"; exit 97; fi

# --- classify runs: protected (non-retired) vs retired -------------------------
protected=""; retired_runs=""
for run in "$R"/fcp-*; do
  [ -d "$run" ] || continue
  name=$(basename "$run")
  declared=0
  for t in $RETIRE; do [ "$t" = "$name" ] && declared=1; done
  unfinished=0; total=0; locks=0
  for st in "$run"/state-*; do
    [ -d "$st" ] || continue
    if [ -f "$st/summary.json" ]; then
      read -r u t <<<"$(python3 -c "
import json,sys
c=json.load(open(sys.argv[1])).get('counts',{}) or {}
t=sum(c.values()); print(t-c.get('generated',0), t)" "$st/summary.json" 2>/dev/null || echo "1 1")"
      unfinished=$((unfinished + u)); total=$((total + t))
    else
      unfinished=$((unfinished + 1))
    fi
    if [ -f "$st/worker.lock" ]; then
      python3 - "$st/worker.lock" <<'PY' >/dev/null 2>&1 || locks=$((locks + 1))
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY
    fi
  done
  base=$(grep -h -m1 -o 'RECOVERY_BASELINE_REPO="[^"]*"' "$run"/run-lane-*.sh 2>/dev/null | head -1 | cut -d'"' -f2)
  venv=$(grep -h -m1 -o 'VIRTUAL_ENV="[^"]*"' "$run"/run-lane-*.sh 2>/dev/null | head -1 | cut -d'"' -f2)
  if [ "$locks" -gt 0 ]; then
    echo "RUN $name LIVE_LOCK unfinished=$unfinished/$total -> protected"; protected="$protected $run $base $venv"
  elif [ "$declared" -eq 1 ] || { [ "$total" -gt 0 ] && [ "$unfinished" -eq 0 ]; }; then
    echo "RUN $name retired unfinished=$unfinished/$total declared=$declared"; retired_runs="$retired_runs $run"
  else
    echo "RUN $name unfinished=$unfinished/$total -> protected (name it with --retire to clean its work/baseline/venv)"
    protected="$protected $run $base $venv"
  fi
done
is_protected() { for p in $protected; do [ -n "$p" ] && [ "$1" = "$p" ] && return 0; done; return 1; }

# --- candidates ---------------------------------------------------------------
CANDS=$(mktemp)
add() { # kind path
  [ -e "$2" ] || return 0
  local b; b=$(du -sb "$2" 2>/dev/null | cut -f1); echo "CAND|$1|$2|${b:-0}" >> "$CANDS"
}
[ -d /tmp/fleet ] && add upload /tmp/fleet
for d in "$HOME/.cache/pip" "$HOME/.cache/uv" "$HOME/.cache/pypoetry"; do add pipcache "$d"; done
while IFS= read -r d; do
  skip=0; for p in $protected; do [ -n "$p" ] && case "$d" in "$p"/*) skip=1;; esac; done
  [ "$skip" -eq 0 ] && add pycache "$d"
done < <(find "$R" -maxdepth 6 -type d \( -name __pycache__ -o -name .pytest_cache \) 2>/dev/null)
for run in $retired_runs; do
  for w in "$run"/work-*; do [ -d "$w" ] && add retired_work "$w"; done
  for s in "$run"/work-*/task-*/.source-build-* "$run"/work-*/task-*/.source-stage-* "$run"/work-*/task-*/.task-stage-* "$run"/work-*/task-*/.release-stage-*; do [ -d "$s" ] && add staging "$s"; done
done
for d in "$R"/FCP_recursive_v1-* "$R"/venv-*; do
  [ -d "$d" ] || continue
  is_protected "$d" && continue
  used=0; for run in "$R"/fcp-*; do is_protected "$run" && grep -qs -- "$d" "$run"/run-lane-*.sh 2>/dev/null && used=1; done
  [ "$used" -eq 0 ] && add stale_tree "$d"
done
apt_bytes=$(du -sb /var/cache/apt/archives 2>/dev/null | cut -f1); [ "${apt_bytes:-0}" -gt 1048576 ] && echo "CAND|aptcache|/var/cache/apt/archives|$apt_bytes" >> "$CANDS"
jr_bytes=$(du -sb /var/log/journal 2>/dev/null | cut -f1); [ "${jr_bytes:-0}" -gt 314572800 ] && echo "CAND|journal|/var/log/journal (vacuum to 200M)|$jr_bytes" >> "$CANDS"
if command -v docker >/dev/null 2>&1 && [ "$(docker ps -q 2>/dev/null | wc -l)" = "0" ]; then
  dangling=$(docker images -qf dangling=true 2>/dev/null | wc -l); stopped=$(docker ps -aq 2>/dev/null | wc -l)
  [ "$dangling$stopped" != "00" ] && echo "CAND|docker|dangling images=$dangling stopped containers=$stopped|0" >> "$CANDS"
fi

sort -t'|' -k2,3 "$CANDS" -o "$CANDS"
total=$(awk -F'|' '{s+=$4} END{print s+0}' "$CANDS")
cat "$CANDS"
computed=$(cut -d'|' -f2,3 "$CANDS" | sha256sum | awk '{print $1}')
echo "SUMMARY candidates=$(wc -l < "$CANDS" | tr -d ' ') bytes=$total free_before=$(df -B1 --output=avail / | tail -1 | tr -d ' ')"
echo "TOKEN=$computed"

if [ "$MODE" != "apply" ]; then echo "CLEAN_PLAN_DONE"; rm -f "$CANDS"; exit 0; fi
if [ -z "$TOKEN" ] || [ "$TOKEN" != "$computed" ]; then
  echo "CLEAN_REFUSED token mismatch (list changed since the plan was shown; re-run plan)"; rm -f "$CANDS"; exit 96
fi

# --- apply --------------------------------------------------------------------
while IFS='|' read -r _ kind path _; do
  case "$kind" in
    aptcache) sudo -n apt-get clean >/dev/null 2>&1 && echo "DELETED aptcache";;
    journal)  sudo -n journalctl --vacuum-size=200M >/dev/null 2>&1 && echo "DELETED journal";;
    docker)   docker container prune -f >/dev/null 2>&1; docker image prune -f >/dev/null 2>&1; echo "DELETED docker dangling";;
    *)
      case "$path" in
        */output-*|*/state-*|*/verification_manifests*|*/.env|*/logs) echo "SKIP never-delete $path"; continue;;
      esac
      chmod -R u+rwX "$path" 2>/dev/null || true
      rm -rf -- "$path" && echo "DELETED $kind $path";;
  esac
done < "$CANDS"
rm -f "$CANDS"
echo "SUMMARY free_after=$(df -B1 --output=avail / | tail -1 | tr -d ' ')"
echo "CLEAN_APPLY_DONE"
