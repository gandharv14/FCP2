#!/bin/bash
# Runs ON a VM (fed over stdin by fleet_hosts.sh). Read-only. Prints KEY=VALUE
# lines that fleet_hosts.sh turns into fleet.json. Never modifies anything.
set -u
R="$HOME/GDM_FCP"
echo "PROBE_BEGIN"
echo "hostname=$(hostname -s)"
echo "user=$(id -un)"
echo "home=$HOME"
echo "mem_total_bytes=$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
echo "mem_avail_bytes=$(awk '/MemAvailable/ {print $2 * 1024}' /proc/meminfo)"
echo "disk_avail_bytes=$(df -B1 --output=avail / | tail -1 | tr -d ' ')"
echo "disk_size_bytes=$(df -B1 --output=size / | tail -1 | tr -d ' ')"
echo "loadavg=$(cut -d' ' -f1 /proc/loadavg)"
echo "uptime_s=$(cut -d. -f1 /proc/uptime)"

# Live recovery workers, any user. The bracket trick keeps this script's own
# command line from matching.
workers=$(pgrep -f '[s]cripts.batch.recovery_worker' || true)
echo "worker_count=$(echo "$workers" | grep -c . || true)"
owners=""
for p in $workers; do owners="$owners $(ps -o user= -p "$p" 2>/dev/null | tr -d ' ')"; done
echo "worker_owners=$(echo $owners | tr ' ' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')"
echo "agent_count=$(pgrep -fc '[.]local/bin/agent' || true)"
echo "tmux_sessions=$(tmux ls -F '#S' 2>/dev/null | tr '\n' ',' | sed 's/,$//')"
# Other users' tmux servers show up as socket dirs.
echo "tmux_other_uids=$(ls -d /tmp/tmux-* 2>/dev/null | sed 's#/tmp/tmux-##' | grep -v "^$(id -u)$" | tr '\n' ',' | sed 's/,$//')"
echo "harbor_scopes=$(systemctl --user list-units 'harbor-*' --no-legend --plain 2>/dev/null | wc -l)"
echo "docker_containers=$(docker ps -q 2>/dev/null | wc -l)"

# Tool parity.
echo "python3=$(python3 --version 2>&1 | awk '{print $2}')"
echo "has_venv=$(python3 -c 'import venv, ensurepip' 2>/dev/null && echo yes || echo no)"
for t in docker libreoffice soffice git tmux systemd-run sha256sum flock; do
  echo "tool_$t=$(command -v $t >/dev/null 2>&1 && echo yes || echo no)"
done
echo "sudo_n=$(sudo -n true 2>/dev/null && echo yes || echo no)"
echo "cgroup_v2=$([ -f /sys/fs/cgroup/cgroup.controllers ] && echo yes || echo no)"
echo "linger=$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo unknown)"
echo "sshd_oom=$(cat /proc/$(pgrep -o -x sshd 2>/dev/null || echo self)/oom_score_adj 2>/dev/null)"
echo "logcap_timer=$(systemctl is-active harbor-log-cap.timer 2>/dev/null || echo inactive)"
if [ -x "$HOME/.local/bin/agent" ]; then
  if "$HOME/.local/bin/agent" status --format text 2>&1 | grep -q 'Logged in'; then echo "agent=logged_in"; else echo "agent=installed_not_logged_in"; fi
else
  echo "agent=missing"
fi
echo "env_file=$([ -f "$R/.env" ] && echo yes || echo no)"
echo "budget_dir_owner=$(stat -c %U /var/tmp/harbor-memory 2>/dev/null || echo none)"
echo "budget_files=$(ls /var/tmp/harbor-memory/*.json 2>/dev/null | wc -l)"
echo "kern_log_bytes=$(stat -c %s /var/log/kern.log 2>/dev/null || echo 0)"
echo "syslog_bytes=$(stat -c %s /var/log/syslog 2>/dev/null || echo 0)"

# Existing runs under $HOME/GDM_FCP: tag, per-lane counts, lock state, bound BASE/VENV.
for run in "$R"/fcp-*; do
  [ -d "$run" ] || continue
  name=$(basename "$run")
  unfinished=0; total=0; locks_held=0; lanes=""
  for st in "$run"/state-*; do
    [ -d "$st" ] || continue
    lane=${st##*/state-}
    lanes="$lanes$lane"
    if [ -f "$st/summary.json" ]; then
      read -r u t <<<"$(python3 -c "
import json,sys
c=json.load(open(sys.argv[1])).get('counts',{}) or {}
t=sum(c.values()); print(t-c.get('generated',0), t)" "$st/summary.json" 2>/dev/null || echo "0 0")"
      unfinished=$((unfinished + u)); total=$((total + t))
    fi
    if [ -f "$st/worker.lock" ]; then
      python3 - "$st/worker.lock" <<'PY' >/dev/null 2>&1 || locks_held=$((locks_held + 1))
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
PY
    fi
  done
  base=$(grep -h -m1 -o 'RECOVERY_BASELINE_REPO="[^"]*"' "$run"/run-lane-*.sh 2>/dev/null | head -1 | cut -d'"' -f2)
  venv=$(grep -h -m1 -o 'VIRTUAL_ENV="[^"]*"' "$run"/run-lane-*.sh 2>/dev/null | head -1 | cut -d'"' -f2)
  bytes=$(du -sb "$run" 2>/dev/null | cut -f1)
  echo "run=$name|lanes=$lanes|total=$total|unfinished=$unfinished|locks_held=$locks_held|base=$base|venv=$venv|bytes=$bytes"
done
for d in "$R"/FCP_recursive_v1-* "$R"/venv-*; do
  [ -d "$d" ] || continue
  echo "tree=$(basename "$d")|bytes=$(du -sb "$d" 2>/dev/null | cut -f1)"
done
echo "tmp_fleet_bytes=$(du -sb /tmp/fleet 2>/dev/null | cut -f1 || echo 0)"
echo "PROBE_END"
