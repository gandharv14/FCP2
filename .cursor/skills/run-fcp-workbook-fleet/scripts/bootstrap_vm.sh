#!/bin/bash
# Bootstrap an EXISTING blank VM so deploy_remote.sh can run on it. Does not
# create GCP instances. Safe to rerun; every step is idempotent.
#
# usage: bootstrap_vm.sh HOST --env-file PATH [--with-libreoffice] [--skip-env]
#   --env-file PATH   local .env to copy to $HOME/GDM_FCP/.env on the VM (chmod 600).
#                     Default: <repo parent>/.env. The agent never reads this file.
#   --with-libreoffice  also apt-get install libreoffice (only when a reference host has it)
#   --skip-env        do not copy .env (it is already on the VM)
#
# Refuses when the host is busy (a recovery worker is running). Ends by running
# harbor_host_protections.sh apply + verify and requiring HOSTOK. The one manual
# step it cannot do: `agent login` on the VM (interactive browser flow).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

HOST=""; ENV_FILE="$REPO_DIR/../.env"; LIBRE=0; SKIP_ENV=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2;;
    --with-libreoffice) LIBRE=1; shift;;
    --skip-env) SKIP_ENV=1; shift;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    *) HOST="$1"; shift;;
  esac
done
[ -n "$HOST" ] || die "HOST is required"
if [ "$SKIP_ENV" -eq 0 ]; then
  [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (pass --env-file or --skip-env)"
fi

echo "== $HOST: refusing if busy"
busy=$(gssh "$HOST" --command "pgrep -fc '[s]cripts.batch.recovery_worker' || true" 2>/dev/null | strip_noise | tail -1)
[ "${busy:-0}" = "0" ] || die "$HOST has $busy live recovery worker(s); will not bootstrap a busy host"

echo "== $HOST: packages, agent CLI, host protections"
PROT_SHA=$(sha256_of "$REPO_DIR/scripts/batch/harbor_host_protections.sh")
gscp "$REPO_DIR/scripts/batch/harbor_host_protections.sh" "$HOST:/tmp/harbor_host_protections.sh" || die "scp of host protections failed"

gssh "$HOST" --command "LIBRE=$LIBRE PROT_SHA=$PROT_SHA bash -s" <<'REMOTE' 2>&1 | strip_noise
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "user=$(id -un) home=$HOME"
sudo -n true 2>/dev/null || { echo "BOOTSTRAP_FAIL sudo -n does not work (OS Login needs roles/compute.osAdminLogin)"; exit 2; }
test "$(sha256sum /tmp/harbor_host_protections.sh | awk '{print $1}')" = "$PROT_SHA" || { echo "BOOTSTRAP_FAIL protections script hash"; exit 2; }

pkgs="python3-venv python3-pip tmux docker.io git util-linux"
[ "$LIBRE" = "1" ] && pkgs="$pkgs libreoffice-calc"
sudo -n apt-get update -qq >/dev/null 2>&1 || true
sudo -n apt-get install -y -qq $pkgs >/dev/null 2>&1 || { echo "BOOTSTRAP_FAIL apt-get install $pkgs"; exit 2; }
echo "packages_ok python3=$(python3 --version | awk '{print $2}') docker=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,)"

sudo -n systemctl enable --now docker >/dev/null 2>&1 || true
if ! id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker; then
  sudo -n usermod -aG docker "$(id -un)" && echo "docker_group_added (takes effect on the next SSH session)"
fi

if [ ! -x "$HOME/.local/bin/agent" ]; then
  curl -fsS https://cursor.com/install | bash >/dev/null 2>&1 || { echo "BOOTSTRAP_FAIL cursor agent install"; exit 2; }
fi
test -x "$HOME/.local/bin/agent" && echo "agent_cli_ok $("$HOME/.local/bin/agent" --version 2>/dev/null | head -1)"

mkdir -p "$HOME/GDM_FCP" && chmod 700 "$HOME/GDM_FCP"
sudo -n loginctl enable-linger "$(id -un)" 2>/dev/null || true

sudo -n bash /tmp/harbor_host_protections.sh apply --user "$(id -un)" >/dev/null 2>&1 || { echo "BOOTSTRAP_FAIL host protections apply"; exit 2; }
sudo -n bash /tmp/harbor_host_protections.sh verify --user "$(id -un)" | tail -1
test ! -e /etc/logrotate.d/harbor-kernel-cap || { echo "BOOTSTRAP_FAIL stale /etc/logrotate.d/harbor-kernel-cap present"; exit 2; }
rm -f /tmp/harbor_host_protections.sh
echo "BOOTSTRAP_PACKAGES_DONE"
REMOTE
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || die "$HOST bootstrap failed (exit $rc)"

if [ "$SKIP_ENV" -eq 0 ]; then
  echo "== $HOST: copying .env to \$HOME/GDM_FCP/.env (chmod 600)"
  gscp "$ENV_FILE" "$HOST:GDM_FCP/.env" || die ".env scp failed"
  gssh "$HOST" --command 'chmod 600 "$HOME/GDM_FCP/.env" && echo env_ok' 2>/dev/null | strip_noise | tail -1
fi

echo "== $HOST: agent login state"
state=$(gssh "$HOST" --command '"$HOME/.local/bin/agent" status --format text 2>&1 | grep -q "Logged in" && echo logged_in || echo not_logged_in' 2>/dev/null | strip_noise | tail -1)
if [ "$state" != "logged_in" ]; then
  cat <<EOF
MANUAL STEP for $HOST: the Cursor agent is installed but not logged in. Run
  gcloud compute ssh $HOST --zone $FLEET_ZONE --project $FLEET_PROJECT --tunnel-through-iap
  ~/.local/bin/agent login
finish the browser flow, then re-run fleet_hosts.sh; the host must read 'agent=logged_in'.
EOF
  exit 3
fi
echo "BOOTSTRAP_OK $HOST"
