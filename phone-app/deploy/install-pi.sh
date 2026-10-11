#!/usr/bin/env bash
# Install the FarFromHome handheld on a Raspberry Pi 5 (Raspberry Pi OS 64-bit).
#
#   sudo bash deploy/install-pi.sh
#
# Idempotent: safe to re-run after a git pull. It creates the service user, a
# virtualenv, the config file and the systemd units, and enables the four
# processes. Nothing here needs the internet beyond pip.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/farfromhome/phone-app}"
DATA_DIR="${DATA_DIR:-/var/lib/farfromhome}"
CONF_DIR="/etc/farfromhome"
SERVICE_USER="farfromhome"
PYTHON="${PYTHON:-python3}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run me with sudo"

say "1/8 checking this is a Raspberry Pi"
if [[ -r /proc/device-tree/model ]]; then
  tr -d '\0' < /proc/device-tree/model; echo
else
  warn "no /proc/device-tree/model; continuing anyway (development machine?)"
fi

say "2/8 installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# python3-venv to build the env, i2c-tools for the IMU, alsa-utils for the mic and
# speaker, and the Qt runtime libraries PySide6 needs on a headless-ish Pi.
apt-get install -y --no-install-recommends \
  python3-venv python3-dev build-essential \
  i2c-tools alsa-utils \
  libgl1 libegl1 libgles2 libxkbcommon0 libdbus-1-3 \
  fonts-dejavu-core \
  ca-certificates curl

say "3/8 creating the service user and directories"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# Groups the hardware needs. Missing groups are created by the packages above; a
# failure here is not fatal, so the script does not stop.
for group in dialout gpio video audio i2c render input; do
  getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$SERVICE_USER" || true
done
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR"
install -d -m 0755 "$CONF_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$APP_DIR"

say "4/8 copying the application"
# rsync keeps exclusions honest and is much faster than cp on a re-run.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude '.ruff_cache' \
    "$REPO_DIR/" "$APP_DIR/"
else
  cp -r "$REPO_DIR/." "$APP_DIR/"
  rm -rf "$APP_DIR/.git" "$APP_DIR/.venv" "$APP_DIR/data" "$APP_DIR/__pycache__"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

say "5/8 creating the virtualenv"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  sudo -u "$SERVICE_USER" "$PYTHON" -m venv "$APP_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip wheel
# PySide6 is large; if it fails, the handheld still runs headless, so this is a
# warning rather than a stop.
if ! sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"; then
  warn "some dependencies failed to install (often PySide6). The handheld still runs with --headless."
fi

say "6/8 writing the configuration"
if [[ ! -f "$CONF_DIR/device.env" ]]; then
  # Detect the LAN address so the QR codes work on the first try.
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
  LAN_IP="${LAN_IP:-127.0.0.1}"
  cat > "$CONF_DIR/device.env" <<EOF
# FarFromHome handheld configuration. Edit SERVER_URL to the laptop's WiFi IP.
SERVER_URL=http://${LAN_IP}:8888
DEVICE_NAME=$(hostname)
DEVICE_MODE=auto
DATA_DIR=${DATA_DIR}
SPEECH_ENABLED=true
UI_FULLSCREEN=true
EOF
  # The laptop's IP, not this Pi's, is usually what belongs here. Say so loudly.
  warn "wrote $CONF_DIR/device.env with SERVER_URL=http://${LAN_IP}:8888"
  warn "if the server runs on another machine, change it to that machine's WiFi address"
else
  echo "keeping the existing $CONF_DIR/device.env"
fi
chmod 0640 "$CONF_DIR/device.env"
chown root:"$SERVICE_USER" "$CONF_DIR/device.env"

say "7/8 installing the systemd units"
cp "$APP_DIR"/systemd/farfromhome-*.service /etc/systemd/system/
cp "$APP_DIR"/systemd/farfromhome.service /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable farfromhome-agent.service farfromhome-engine.service \
                 farfromhome-ui.service farfromhome-syncd.service

say "8/8 enabling the hardware interfaces"
# The GPS is a serial device and the IMU is on I2C; both need enabling once.
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || warn "could not enable I2C automatically"
  raspi-config nonint do_serial_hw 0 || true
  raspi-config nonint do_serial_cons 1 || warn "could not disable the serial console"
else
  warn "raspi-config not found; enable I2C and disable the serial console yourself"
fi

cat <<EOF

$(say "installed")

  config        $CONF_DIR/device.env      <- set SERVER_URL to the laptop's WiFi IP
  data          $DATA_DIR
  code          $APP_DIR

  start         sudo systemctl start farfromhome-agent farfromhome-engine farfromhome-ui farfromhome-syncd
  logs          journalctl -u farfromhome-engine -f
  check         $APP_DIR/.venv/bin/python $APP_DIR/run.py --self-test

  Pair this handheld:
    sudo -u $SERVICE_USER $APP_DIR/.venv/bin/python $APP_DIR/run.py --pair --server http://<laptop-ip>:8888

  One process instead of four:
    sudo systemctl disable --now farfromhome-agent farfromhome-engine farfromhome-ui farfromhome-syncd
    sudo systemctl enable --now farfromhome

EOF
