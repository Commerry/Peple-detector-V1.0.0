#!/usr/bin/env bash
# Install the People Counter on Ubuntu. Run from the project directory:
#     bash tools/install_ubuntu.sh
# Assumes backend/models/ already contains an exported OpenVINO model, so
# ultralytics/torch (~2 GB) is never installed on the target machine.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "== project: $DIR   user: $USER_NAME =="

echo
echo "== 1. system packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip intel-opencl-icd

# OpenVINO reaches the integrated GPU through /dev/dri/renderD128, which is
# owned by the render group; a systemd service is not in the desktop ACL.
if ! id -nG "$USER_NAME" | grep -qw render; then
    echo "   adding $USER_NAME to the render group (needed for iGPU inference)"
    sudo usermod -aG render "$USER_NAME"
fi

echo
echo "== 2. python environment =="
if [ ! -d "$DIR/.venv" ]; then
    python3 -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/pip" install -q --upgrade pip
if [ -n "$(find "$DIR/backend/models" -name '*.xml' -print -quit 2>/dev/null)" ]; then
    echo "   model already exported - installing runtime dependencies only"
    "$DIR/.venv/bin/pip" install -q -r "$DIR/backend/requirements-runtime.txt"
else
    echo "   no model found - installing the full set (includes torch, slow)"
    "$DIR/.venv/bin/pip" install -q -r "$DIR/backend/requirements.txt"
fi

echo
echo "== 3. folders =="
mkdir -p "$DIR/data/snapshots"

echo
echo "== 4. systemd service =="
sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" \
    "$DIR/tools/peoplecounter.service" | sudo tee /etc/systemd/system/peoplecounter.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable peoplecounter.service
sudo systemctl restart peoplecounter.service
sleep 5
sudo systemctl --no-pager status peoplecounter.service | head -12

echo
echo "== 5. firewall =="
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 8000/tcp
else
    echo "   ufw inactive - nothing to open"
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "Done. Open:  http://$IP:8000"
echo "Entrance display : http://$IP:8000/?camera=1&kiosk=1"
echo "Exit display     : http://$IP:8000/?camera=2&kiosk=1"
echo
echo "Service commands:"
echo "  sudo systemctl status peoplecounter"
echo "  sudo systemctl restart peoplecounter"
echo "  tail -f $DIR/data/server.log"
