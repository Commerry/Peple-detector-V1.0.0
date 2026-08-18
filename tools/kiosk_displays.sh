#!/usr/bin/env bash
# Lay out both screens, put one counter window on each, and keep them there.
#
# Runs inside the desktop session (see tools/install_kiosk.sh, which registers
# it in ~/.config/autostart). GNOME does not remember a layout that was set with
# xrandr, so the layout is re-applied on every login.
#
# One browser, two windows. Two separate browser instances were tried first and
# one of them kept dying a few seconds after login; a single instance also
# halves the memory two windows cost on this box. The windows are placed and
# made fullscreen through the window manager rather than by asking the browser
# for kiosk mode, because a browser that opens its window before the second
# monitor has settled ends up framed on the wrong screen.
#
# The wall displays show the live view exactly as it looks in a browser: header,
# counters, picture with the detection boxes, and the recent-event list. The
# window is made fullscreen by the window manager, so the page keeps its own
# chrome (no kiosk=1) and the whole thing still fills the screen.
#
# The picture is not free: on this 2-core box each stream roughly halves that
# camera's detection rate. That is the trade the displays are meant to make.
LEFT_CAM=1
RIGHT_CAM=2
URL_BASE="http://localhost:8000/?camera="
PROFILE="$HOME/kiosk-profiles/wall"
LOG="$HOME/people-counter/data/kiosk.log"
mkdir -p "$(dirname "$LOG")" "$PROFILE"
exec >>"$LOG" 2>&1
echo "=== kiosk start $(date) ==="

# --- 1. wait for the counting service to answer ---
for _ in $(seq 1 60); do
    if curl -sf -m 2 http://localhost:8000/api/stats >/dev/null 2>&1 ||
       python3 -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/stats',timeout=2)" 2>/dev/null; then
        break
    fi
    sleep 2
done

# --- 2. screen layout: primary on the left, second to its right ---
# A DisplayPort monitor can take a few seconds to appear after login, so poll
# for it instead of reading the connector list once.
PRIMARY=""
SECOND=""
for _ in $(seq 1 10); do
    PRIMARY=$(xrandr | awk '/ connected primary/{print $1; exit}')
    [ -z "$PRIMARY" ] && PRIMARY=$(xrandr | awk '/ connected/{print $1; exit}')
    SECOND=$(xrandr | awk -v p="$PRIMARY" '/ connected/{if ($1 != p) {print $1; exit}}')
    [ -n "$SECOND" ] && break
    sleep 2
done
echo "primary=$PRIMARY second=$SECOND"

# Chrome Remote Desktop runs a second desktop session on a virtual screen, and
# autostart entries fire in that session too. It answered first once and both
# kiosk windows opened on a screen nobody can see, on a box with no spare CPU.
# A session without a real monitor bows out.
case "$PRIMARY" in
    DUMMY* | "")
        echo "no physical monitor here (primary='${PRIMARY:-none}') - leaving the kiosk alone"
        exit 0
        ;;
esac

if [ -n "$SECOND" ]; then
    xrandr --output "$PRIMARY" --primary --auto --pos 0x0
    sleep 1
    # A monitor whose EDID cannot be read (a weak DP cable did this here)
    # advertises no preferred mode, so --auto picks 640x480. Take the widest
    # mode the driver will actually accept instead, largest first.
    placed=0
    for MODE in $(xrandr | awk -v o="$SECOND" '
            $1 == o {f=1; next} /^[A-Za-z]/ && $2 ~ /connected|disconnected/ {f=0}
            f && $1 ~ /^[0-9]+x[0-9]+$/ {print $1}' |
            sort -t x -k1,1n -k2,2n -u | tac); do
        if xrandr --output "$SECOND" --mode "$MODE" --right-of "$PRIMARY" 2>/dev/null; then
            echo "second screen at $MODE"
            placed=1
            break
        fi
    done
    [ "$placed" = 0 ] && xrandr --output "$SECOND" --auto --right-of "$PRIMARY"
    sleep 3
fi
echo "layout: $(xrandr --listmonitors | tail -n +2 | tr '\n' ' ')"

# Geometry per connector, so a window lands on the monitor meant for it.
# Reading the connector list in order got this wrong: xrandr lists HDMI before
# DP, which put the left window on the right screen.
geometry_of() {
    xrandr | awk -v o="$1" '$1 == o && / connected/ {
        if (match($0, /[0-9]+x[0-9]+\+[0-9]+\+[0-9]+/)) print substr($0, RSTART, RLENGTH) }'
}
IFS='x+' read -r LW LH LX LY <<<"$(geometry_of "$PRIMARY")"
IFS='x+' read -r RW RH RX RY <<<"$(geometry_of "$SECOND")"
: "${LW:=1920}" "${LH:=1080}" "${LX:=0}" "${LY:=0}"
echo "left($PRIMARY)=${LW}x${LH}+${LX}+${LY}  right(${SECOND:-none})=${RW:-none}x${RH:-}+${RX:-}+${RY:-}"

# --- 3. never blank a wall display ---
xset s off
xset -dpms
xset s noblank
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null

# --- 4. one window per screen, out of a single browser ---
BROWSER=""
for b in chromium chromium-browser google-chrome google-chrome-stable; do
    command -v "$b" >/dev/null && BROWSER=$b && break
done
if [ -z "$BROWSER" ]; then
    echo "no chromium-like browser found - cannot open the wall windows"
    exit 1
fi

# A window is found by the camera its page reports, which is why the page puts
# the camera in its title.
window_id() {
    wmctrl -l 2>/dev/null | awk -v pat="Camera $1 " '$0 ~ pat {print $1; exit}'
}

# A big screen is usually further away, so scale the page up on one. Chromium
# applies this per instance, not per window, so it is only used when every
# screen is large enough to want it -- otherwise a 1080p panel beside a 4K one
# would end up with half a page on it.
SCALE=""
smallest_width=${LW:-1920}
[ -n "$SECOND" ] && [ "${RW:-0}" -lt "$smallest_width" ] && smallest_width=$RW
if [ "$smallest_width" -ge 3200 ]; then
    SCALE="--force-device-scale-factor=2"
elif [ "$smallest_width" -ge 2400 ]; then
    SCALE="--force-device-scale-factor=1.5"
fi
[ -n "$SCALE" ] && echo "screens are ${smallest_width}px wide or more, scaling the page: $SCALE"

open_window() {
    local cam=$1
    # --password-store=basic: with automatic login nothing can unlock the login
    # keyring, and the browser asking for it put an "Authentication required"
    # dialog on the wall. The counters need no stored passwords.
    "$BROWSER" --user-data-dir="$PROFILE" --class=kiosk-cam \
        --password-store=basic \
        --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
        --check-for-update-interval=31536000 ${SCALE:+"$SCALE"} \
        --app="${URL_BASE}${cam}" >/dev/null 2>&1 &
    for _ in $(seq 1 20); do
        [ -n "$(window_id "$cam")" ] && return 0
        sleep 1
    done
    return 1
}

# Placing before fullscreen: a fullscreen window cannot be moved, so the frame
# goes to the right monitor first and is then blown up in place.
place_window() {
    local id=$1 x=$2 y=$3 w=$4 h=$5
    wmctrl -i -r "$id" -b remove,fullscreen 2>/dev/null
    wmctrl -i -r "$id" -e "0,$x,$y,$w,$h" 2>/dev/null
    sleep 1
    wmctrl -i -r "$id" -b add,fullscreen 2>/dev/null
}

pkill -f "user-data-dir=$PROFILE" 2>/dev/null
pkill -f 'chromium.*kiosk-cam' 2>/dev/null
sleep 2

open_window "$LEFT_CAM" && place_window "$(window_id "$LEFT_CAM")" "$LX" "$LY" "$LW" "$LH"
if [ -n "$SECOND" ]; then
    open_window "$RIGHT_CAM" && place_window "$(window_id "$RIGHT_CAM")" "$RX" "$RY" "$RW" "$RH"
fi
echo "opened with $BROWSER: $(wmctrl -l | grep -c 'Camera ') window(s)"

# --- 5. keep them up ---
# A wall display is unattended for weeks: a browser that dies, or a window that
# loses fullscreen because the monitor blinked, has to come back without anyone
# walking over to it.
while sleep 30; do
    for pair in "$LEFT_CAM $LX $LY $LW $LH" "${SECOND:+$RIGHT_CAM $RX $RY $RW $RH}"; do
        [ -z "$pair" ] && continue
        # shellcheck disable=SC2086 — the fields are ours, split is intended
        set -- $pair
        id=$(window_id "$1")
        if [ -z "$id" ]; then
            echo "$(date +%H:%M:%S) camera $1 window gone - reopening"
            open_window "$1" && place_window "$(window_id "$1")" "$2" "$3" "$4" "$5"
            continue
        fi
        if ! xprop -id "$id" _NET_WM_STATE 2>/dev/null | grep -q _NET_WM_STATE_FULLSCREEN; then
            echo "$(date +%H:%M:%S) camera $1 window not fullscreen - fixing"
            place_window "$id" "$2" "$3" "$4" "$5"
        fi
    done
done
