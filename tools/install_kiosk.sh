#!/usr/bin/env bash
# Turn this machine into a two-screen counting station:
#   boot -> log in automatically -> one kiosk window per monitor.
# Run once, on the machine itself or over SSH:
#     bash tools/install_kiosk.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "== 1. browser =="
if command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
    echo "   chromium already installed"
else
    echo "   installing chromium (about 200 MB)"
    sudo snap install chromium
fi

echo
echo "== 2. window tools (fallback for firefox) =="
sudo apt-get install -y -qq xdotool wmctrl >/dev/null 2>&1 || true

echo
echo "== 3. log in automatically at boot =="
if grep -q '^ *AutomaticLoginEnable' /etc/gdm3/custom.conf 2>/dev/null; then
    echo "   already configured"
else
    sudo sed -i "s/^\[daemon\]/[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=$USER_NAME/" \
        /etc/gdm3/custom.conf
    echo "   enabled for $USER_NAME"
fi
grep -A3 '^\[daemon\]' /etc/gdm3/custom.conf | sed 's/^/   /'

echo
echo "== 4. start the kiosk windows at login =="
chmod +x "$DIR/tools/kiosk_displays.sh"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/people-counter-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=People Counter Kiosk
Exec=$DIR/tools/kiosk_displays.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=10
NoDisplay=false
EOF
echo "   wrote ~/.config/autostart/people-counter-kiosk.desktop"

echo
echo "Done."
echo "Test right now without rebooting:  $DIR/tools/kiosk_displays.sh"
echo "Log:                               $DIR/data/kiosk.log"
echo "To undo autologin: remove the AutomaticLogin lines from /etc/gdm3/custom.conf"
