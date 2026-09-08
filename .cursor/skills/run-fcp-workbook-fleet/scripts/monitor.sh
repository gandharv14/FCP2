#!/bin/bash
# Local 10-minute monitor loop for one run. Writes DIR/reports/<UTC>.md each tick.
#
# usage: monitor.sh start  --run-dir DIR [--interval 600] [--run-prefix fcp-run]
#        monitor.sh stop   --run-dir DIR
#        monitor.sh status --run-dir DIR
#        monitor.sh latest --run-dir DIR       # print the newest report
#
# One loop per run directory (DIR/monitor.pid); a second start is refused.
# Runs under nohup, wrapped in caffeinate on macOS so the Mac does not sleep
# through a tick. No tmux needed locally.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ACTION="${1:-}"; shift || true
RUN_DIR=""; INTERVAL=600; RUN_PREFIX="${RUN_PREFIX:-fcp-run}"
while [ $# -gt 0 ]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    --run-prefix) RUN_PREFIX="$2"; shift 2;;
    *) die "unknown arg $1";;
  esac
done
[ -n "$RUN_DIR" ] || die "--run-dir is required"
RUN_DIR=$(cd "$RUN_DIR" && pwd)
[ -f "$RUN_DIR/RUN_TAG" ] || die "$RUN_DIR has no RUN_TAG (deploy first)"
PIDFILE="$RUN_DIR/monitor.pid"

alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; }

case "$ACTION" in
  start)
    if alive; then echo "monitor already running (pid $(cat "$PIDFILE")); reports in $RUN_DIR/reports/"; exit 0; fi
    mkdir -p "$RUN_DIR/reports"
    LOOP="cd '$RUN_DIR' && while true; do RUN_PREFIX='$RUN_PREFIX' FLEET_PROJECT='$FLEET_PROJECT' FLEET_ZONE='$FLEET_ZONE' FLEET_INNER_BUDGET_SECONDS='${FLEET_INNER_BUDGET_SECONDS:-0}' python3 '$SCRIPTS_DIR/report.py' --run-dir '$RUN_DIR' --out \"$RUN_DIR/reports/\$(date -u +%Y%m%dT%H%M%SZ).md\" > '$RUN_DIR/reports/latest.log' 2>&1; sleep $INTERVAL; done"
    # Detach into a new session (double fork + setsid) so an agent or IDE shell
    # that exits cannot reap the loop. macOS has no setsid binary; use Python's.
    rm -f "$PIDFILE"
    LOOP="$LOOP" PIDFILE="$PIDFILE" python3 - <<'PY'
import os, shutil, sys
loop, pidfile = os.environ["LOOP"], os.environ["PIDFILE"]
cmd = ["bash", "-c", loop]
if shutil.which("caffeinate"):
    cmd = ["caffeinate", "-i"] + cmd
if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    os._exit(0)
os.chdir("/")
devnull = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(devnull, fd)
with open(pidfile, "w") as handle:
    handle.write(str(os.getpid()))
os.execvp(cmd[0], cmd)
PY
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$PIDFILE" ] && break; sleep 0.3; done
    [ -s "$PIDFILE" ] || die "monitor did not start"
    echo "monitor started (pid $(cat "$PIDFILE")), every $((INTERVAL / 60)) min, reports in $RUN_DIR/reports/"
    echo "first report is running now; read it with: monitor.sh latest --run-dir $RUN_DIR";;
  stop)
    if alive; then
      pid=$(cat "$PIDFILE"); pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null
      sleep 1; pkill -f "report.py --run-dir '$RUN_DIR'" 2>/dev/null
      rm -f "$PIDFILE"; echo "monitor stopped"
    else rm -f "$PIDFILE"; echo "no monitor running for $RUN_DIR"; fi;;
  status)
    if alive; then echo "running (pid $(cat "$PIDFILE"))"; else echo "not running"; fi
    ls -1t "$RUN_DIR/reports"/*.md 2>/dev/null | head -3;;
  latest)
    f=$(ls -1t "$RUN_DIR/reports"/*.md 2>/dev/null | head -1)
    [ -n "$f" ] || die "no reports yet"
    echo "# $(basename "$f")"; cat "$f";;
  *) sed -n '2,11p' "$0"; exit 1;;
esac
