#!/bin/bash
# Make a recovery VM survive a wrong memory estimate.
#
# These VMs have no swap, so exhausting anonymous memory has no slow phase: the
# OOM killer fires and sshd is an ordinary candidate. Two idempotent steps fix
# that. Re-run this on every fleet start, because a recreated VM loses both.
#
#   1. Take sshd off the OOM killer's menu and protect system.slice from
#      reclaim, so the operator can always log in to repair a wedged host.
#   2. Enable lingering, so user@UID.service survives SSH disconnects and the
#      per-attempt systemd scopes the lanes create are not torn down mid-run.
#   3. Cap kern.log and syslog with a one-minute systemd timer. A MemoryMax
#      scope whose every task is OOM-immune makes the kernel print a full OOM
#      report and retry, forever; that flood filled three root disks in a day.
#      The worker now resets oom_score_adj on every child (see
#      hardened_runner._oom_killable_command), and this is the backstop.
#
# The inheritance trap, so nobody removes the reset: oom_score_adj is copied
# across fork and exec, and every lane is launched from an SSH session, so
# without the reset the worker, the agent, and every xl_*.py child inherit
# sshd's -1000. verify() probes this as the fleet user and reports childoom=.
#
# Usage: harbor_host_protections.sh [apply|verify] [--user NAME]
# Exits non-zero when verification fails, so a launcher can gate on it.
set -uo pipefail

MODE="${1:-apply}"
case "$MODE" in
  # A leading --user means the mode was omitted. Deliberately no shift here,
  # so the flag is left in place for the option loop below.
  --user) MODE="apply" ;;
  apply|verify) shift || true ;;
  *) echo "HOSTFAIL args :: unknown mode $MODE" >&2; exit 1 ;;
esac

FLEET_USER="${SUDO_USER:-$(id -un)}"
USER_EXPLICIT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user)
      if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
        echo "HOSTFAIL args :: --user needs a value" >&2; exit 1
      fi
      FLEET_USER="$2"; USER_EXPLICIT=1; shift 2 ;;
    *) echo "HOSTFAIL args :: unknown argument $1" >&2; exit 1 ;;
  esac
done

# Invoked as root with no SUDO_USER -- cloud-init, a systemd unit -- FLEET_USER
# would silently become root, and the script would linger and verify root while
# the actual fleet user stayed unprotected.
if [ "$FLEET_USER" = "root" ] && [ "$USER_EXPLICIT" -eq 0 ]; then
  echo "HOSTFAIL user :: cannot infer the fleet user as root; pass --user NAME" >&2
  exit 1
fi
if ! id -u "$FLEET_USER" >/dev/null 2>&1; then
  echo "HOSTFAIL user :: no such user $FLEET_USER" >&2
  exit 1
fi
FLEET_UID="$(id -u "$FLEET_USER")"

# Ubuntu names the unit ssh.service; other distributions use sshd.service.
SSH_UNIT="ssh"
if ! systemctl cat ssh.service >/dev/null 2>&1; then
  if systemctl cat sshd.service >/dev/null 2>&1; then
    SSH_UNIT="sshd"
  else
    echo "HOSTFAIL ssh :: neither ssh.service nor sshd.service exists" >&2
    exit 1
  fi
fi

SSH_DROPIN="/etc/systemd/system/${SSH_UNIT}.service.d/10-harbor-oom.conf"
SLICE_DROPIN="/etc/systemd/system/system.slice.d/10-harbor-memory-min.conf"
SSH_MEMORY_MIN="${HARBOR_SSH_MEMORY_MIN:-256M}"
SLICE_MEMORY_MIN="${HARBOR_SLICE_MEMORY_MIN:-1G}"
LOG_CAP_SERVICE="/etc/systemd/system/harbor-log-cap.service"
LOG_CAP_TIMER="/etc/systemd/system/harbor-log-cap.timer"
LOG_CAP_SCRIPT="/usr/local/sbin/harbor-log-cap"
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/10-harbor-cap.conf"
# Observed flood rate was about 2 GB/min per file, so a one-minute timer with
# a 2 GiB cap bounds the worst case to a few GB per file.
LOG_CAP_BYTES="${HARBOR_LOG_CAP_BYTES:-2147483648}"
# Below this the worker refuses to open a row (RECOVERY_MIN_FREE_DISK_BYTES
# defaults to 120 GiB). verify() fails earlier, at launch, so an operator sees
# it in the smoke check rather than in a parked lane.
MIN_FREE_GIB="${HARBOR_MIN_FREE_GIB:-120}"
# The reaper reads this too; it is not applied here, only reported.
DISK_CHECK_PATH="${HARBOR_DISK_CHECK_PATH:-/}"

# Run a command as the fleet user. verify() is reached both directly by the
# fleet user and as root after apply re-execs under sudo; runuser needs root.
as_fleet_user() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u "$FLEET_USER" -- "$@"
  else
    "$@"
  fi
}

install_log_cap() {
  # Truncation in place is safe: rsyslog's omfile opens with O_APPEND, so the
  # kernel repositions to end-of-file before each write and the file simply
  # restarts. rm would orphan rsyslog's descriptor and free nothing;
  # compression needs the space being freed. Not a logrotate stanza: Ubuntu's
  # rsyslog stanza already owns both paths, a duplicate is fatal to the whole
  # logrotate run, and hourly evaluation allows tens of GB between runs.
  cat >"$LOG_CAP_SCRIPT" <<'EOF' || return 1
#!/bin/sh
# Installed by harbor_host_protections.sh. Truncate the two kernel-fed logs
# when they pass HARBOR_LOG_CAP_BYTES.
cap="${HARBOR_LOG_CAP_BYTES:-2147483648}"
for f in /var/log/syslog /var/log/kern.log; do
  [ -f "$f" ] || continue
  sz=$(stat -c %s "$f" 2>/dev/null) || continue
  if [ "$sz" -gt "$cap" ]; then
    truncate -s 0 "$f" && logger -t harbor-log-cap "truncated $f at $sz bytes"
  fi
done
exit 0
EOF
  chmod 0755 "$LOG_CAP_SCRIPT" || return 1
  cat >"$LOG_CAP_SERVICE" <<EOF || return 1
[Unit]
Description=Harbor: cap kern.log and syslog growth
RequiresMountsFor=/var/log

[Service]
Type=oneshot
Environment=HARBOR_LOG_CAP_BYTES=$LOG_CAP_BYTES
ExecStart=$LOG_CAP_SCRIPT
EOF
  cat >"$LOG_CAP_TIMER" <<'EOF' || return 1
[Unit]
Description=Harbor: cap kern.log and syslog growth every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF
  chmod 0644 "$LOG_CAP_SERVICE" "$LOG_CAP_TIMER" || return 1
  # Journald was already bounded (unset means 10% capped at 4G) and was not
  # the culprit; this is hygiene so the two caps read the same.
  install -d -m 0755 "$(dirname "$JOURNALD_DROPIN")" || return 1
  cat >"$JOURNALD_DROPIN" <<EOF || return 1
[Journal]
SystemMaxUse=2G
EOF
  chmod 0644 "$JOURNALD_DROPIN" || return 1
  return 0
}

apply() {
  install_log_cap || return 1
  install -d -m 0755 "$(dirname "$SSH_DROPIN")" || return 1
  # OOMScoreAdjust=-1000 makes the kernel never select sshd. MemoryMin keeps
  # its pages out of reclaim, which is the difference between "survives" and
  # "technically alive but cannot accept a connection".
  cat >"$SSH_DROPIN" <<EOF || return 1
[Service]
OOMScoreAdjust=-1000
MemoryMin=$SSH_MEMORY_MIN
EOF
  chmod 0644 "$SSH_DROPIN" || return 1

  install -d -m 0755 "$(dirname "$SLICE_DROPIN")" || return 1
  cat >"$SLICE_DROPIN" <<EOF || return 1
[Slice]
MemoryMin=$SLICE_MEMORY_MIN
EOF
  chmod 0644 "$SLICE_DROPIN" || return 1

  systemctl daemon-reload || return 1
  systemctl enable --now harbor-log-cap.timer >/dev/null 2>&1 || return 1
  systemctl try-restart systemd-journald.service >/dev/null 2>&1 || true

  # OOMScoreAdjust is a per-process setting, not a cgroup property, so
  # `systemctl set-property` rejects it and cannot make this live. Only a
  # restart re-execs sshd with the new oom_score_adj. Ubuntu's ssh.service
  # uses KillMode=process, so try-restart replaces the listener without
  # killing established sessions -- including the one running this script.
  systemctl try-restart "${SSH_UNIT}.service" >/dev/null 2>&1 || true
  # Socket activation means ssh.service may be inactive with no listener to
  # restart; the socket will start it with the drop-in applied on the next
  # connection.
  systemctl is-active "${SSH_UNIT}.socket" >/dev/null 2>&1 && \
    systemctl try-restart "${SSH_UNIT}.socket" >/dev/null 2>&1 || true

  loginctl enable-linger "$FLEET_USER" || return 1
  # Bring the user manager up now. The lanes cap each agent with a --user
  # scope, and RecoveryWorker latches whether that is available exactly once
  # at startup; a lane that starts before the manager is up runs its whole
  # life on /proc polling with no MemoryMax.
  systemctl start "user@${FLEET_UID}.service" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -r "/sys/fs/cgroup/user.slice/user-${FLEET_UID}.slice/user@${FLEET_UID}.service/cgroup.controllers" ] && break
    sleep 0.5
  done
  return 0
}

verify() {
  local failures=0 adjust linger controllers main_pid runtime_adjust
  local free_gib logcap childoom probe_out probe_rc

  adjust="$(systemctl show "${SSH_UNIT}.service" -p OOMScoreAdjust --value 2>/dev/null)"
  if [ "$adjust" != "-1000" ]; then
    echo "HOSTFAIL oom :: ${SSH_UNIT}.service OOMScoreAdjust is ${adjust:-unset}, want -1000" >&2
    failures=$((failures + 1))
  fi

  # systemctl show reports the LOADED config, which daemon-reload updates
  # immediately. The running sshd keeps its old oom_score_adj until it is
  # re-execed, so config alone would attest to a protection that is not in
  # effect. Check the live process.
  main_pid="$(systemctl show "${SSH_UNIT}.service" -p MainPID --value 2>/dev/null)"
  if [ -n "$main_pid" ] && [ "$main_pid" != "0" ] && [ -r "/proc/$main_pid/oom_score_adj" ]; then
    runtime_adjust="$(cat "/proc/$main_pid/oom_score_adj" 2>/dev/null)"
    if [ "$runtime_adjust" != "-1000" ]; then
      echo "HOSTFAIL oom :: running sshd pid $main_pid has oom_score_adj ${runtime_adjust:-unset}, want -1000" >&2
      failures=$((failures + 1))
    fi
  elif ! systemctl is-active "${SSH_UNIT}.socket" >/dev/null 2>&1; then
    echo "HOSTFAIL oom :: ${SSH_UNIT} has no live listener and ${SSH_UNIT}.socket is inactive" >&2
    failures=$((failures + 1))
  else
    echo "HOSTWARN oom :: ${SSH_UNIT}.service is socket-activated and idle; the drop-in applies on the next connection" >&2
  fi

  if [ ! -f "$SSH_DROPIN" ]; then
    echo "HOSTFAIL oom :: $SSH_DROPIN is missing, protection will not survive a reboot" >&2
    failures=$((failures + 1))
  fi

  linger="$(loginctl show-user "$FLEET_USER" -p Linger --value 2>/dev/null)"
  if [ "$linger" != "yes" ]; then
    echo "HOSTFAIL linger :: Linger for $FLEET_USER is ${linger:-unset}, want yes" >&2
    failures=$((failures + 1))
  fi

  if [ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" != "cgroup2fs" ]; then
    echo "HOSTFAIL cgroup :: /sys/fs/cgroup is not cgroup2fs" >&2
    failures=$((failures + 1))
  fi

  # The lanes cap each agent with a --user scope, which needs the memory
  # controller delegated to the user manager. Without it the worker silently
  # falls back to process-group polling, which misses detached descendants.
  controllers="$(cat "/sys/fs/cgroup/user.slice/user-${FLEET_UID}.slice/user@${FLEET_UID}.service/cgroup.controllers" 2>/dev/null)"
  case " $controllers " in
    *" memory "*) ;;
    *)
      echo "HOSTWARN cgroup :: memory controller is not delegated to user@${FLEET_UID}.service; agents will fall back to /proc polling" >&2
      ;;
  esac

  # Free disk, as the fleet user sees it (df reports f_bavail for non-root).
  # A full root disk takes journald and sshd down, and the worker parks any
  # row below RECOVERY_MIN_FREE_DISK_BYTES, so catch it at launch instead.
  free_gib="$(as_fleet_user df -BG --output=avail "$DISK_CHECK_PATH" 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [ -z "$free_gib" ]; then
    free_gib="$(df -BG --output=avail "$DISK_CHECK_PATH" 2>/dev/null | tail -1 | tr -dc '0-9')"
  fi
  if [ -z "$free_gib" ]; then
    echo "HOSTFAIL disk :: cannot measure free space on $DISK_CHECK_PATH" >&2
    failures=$((failures + 1))
    free_gib="?"
  elif [ "$free_gib" -lt "$MIN_FREE_GIB" ]; then
    echo "HOSTFAIL disk :: ${free_gib}G free on $DISK_CHECK_PATH, want at least ${MIN_FREE_GIB}G (HARBOR_MIN_FREE_GIB)" >&2
    failures=$((failures + 1))
  fi

  # The log cap must exist on disk, be enabled, and be live. An installed but
  # inactive timer is exactly the state a reboot without `enable` leaves.
  logcap=on
  if [ ! -f "$LOG_CAP_SERVICE" ] || [ ! -f "$LOG_CAP_TIMER" ] || [ ! -x "$LOG_CAP_SCRIPT" ]; then
    echo "HOSTFAIL logcap :: harbor-log-cap service, timer, or script is missing" >&2
    failures=$((failures + 1)); logcap=off
  elif ! systemctl is-enabled harbor-log-cap.timer >/dev/null 2>&1; then
    echo "HOSTFAIL logcap :: harbor-log-cap.timer is not enabled" >&2
    failures=$((failures + 1)); logcap=off
  elif ! systemctl is-active harbor-log-cap.timer >/dev/null 2>&1; then
    echo "HOSTFAIL logcap :: harbor-log-cap.timer is not active" >&2
    failures=$((failures + 1)); logcap=off
  fi
  if [ -f /etc/logrotate.d/harbor-kernel-cap ]; then
    echo "HOSTFAIL logcap :: /etc/logrotate.d/harbor-kernel-cap duplicates the rsyslog stanza and breaks rotation; remove it" >&2
    failures=$((failures + 1))
  fi

  # Inheritance probe, as the fleet user. Run as root this would inspect
  # root's shell; apply re-execs under sudo and then falls through to verify.
  # The outer sh raises itself to 500 (always permitted), then the same
  # prologue hardened_runner uses must pull the child back to 0. That tests
  # the reset itself rather than passing vacuously on a host already at 0.
  childoom=skip
  if [ -e /proc/self/oom_score_adj ]; then
    probe_out="$(as_fleet_user /bin/sh -c '
      printf 500 > /proc/self/oom_score_adj 2>/dev/null || exit 97
      exec /bin/sh -c "[ -w /proc/self/oom_score_adj ] && printf %s \"\$1\" > /proc/self/oom_score_adj 2>/dev/null; shift; exec \"\$@\"" harbor-oom-adjust 0 /bin/cat /proc/self/oom_score_adj
    ' 2>/dev/null)"
    probe_rc=$?
    if [ "$probe_rc" -eq 97 ]; then
      childoom=skip
    elif [ "$probe_rc" -ne 0 ] || [ "$probe_out" != "0" ]; then
      echo "HOSTFAIL childoom :: OOM reset probe returned rc=$probe_rc value='${probe_out}', want 0; scoped payloads would be OOM-immune" >&2
      failures=$((failures + 1)); childoom="${probe_out:-?}"
    else
      childoom=0
    fi
  fi

  if [ "$failures" -ne 0 ]; then
    return 1
  fi
  echo "HOSTOK ${SSH_UNIT} oom=-1000 linger=yes cgroup=v2 freegib=${free_gib} logcap=${logcap} childoom=${childoom}"
  return 0
}

if [ "$MODE" = "apply" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
      echo "HOSTFAIL sudo :: apply needs root or passwordless sudo" >&2
      exit 1
    fi
    exec sudo -n "$0" apply --user "$FLEET_USER"
  fi
  if ! apply; then
    echo "HOSTFAIL apply :: could not install host protections" >&2
    exit 1
  fi
fi

verify
