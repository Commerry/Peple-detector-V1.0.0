#!/usr/bin/env bash
# Move detection between the CPU and the on-board GPU, and prove it was worth it.
#
#   ./tools/switch_device.sh GPU     # try the integrated graphics
#   ./tools/switch_device.sh CPU     # go back
#
# The GPU was tried once before on this hardware and the driver hung inside a
# minute -- CL_OUT_OF_RESOURCES, then "GPU HANG: Resetting rcs0", and the
# service restarting every thirty-five seconds. At that time the same chip was
# also drawing two desktop screens and two browser windows. That contention is
# gone on a headless station, but the risk is not, so this measures first,
# watches the kernel log, and puts the CPU back by itself if anything hangs.
set -uo pipefail

TARGET="${1:-}"
case "$TARGET" in
    CPU|GPU) ;;
    *) echo "usage: $0 CPU|GPU" >&2; exit 1 ;;
esac

APP="${APP:-$HOME/people-counter}"
PY="$APP/.venv/bin/python"
API="http://127.0.0.1:8000"
WATCH_SECONDS="${WATCH_SECONDS:-240}"

rate() {   # average detections per second across the cameras
    "$PY" - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/stats", timeout=15) as r:
    cams = json.load(r).get("cameras", [])
rates = [float(c.get("fps") or 0) for c in cams if c.get("online")]
print("%.2f" % (sum(rates) / len(rates)) if rates else "0")
PY
}

set_device() {
    "$PY" - "$1" <<'PY'
import json, sys, urllib.request
api, device = "http://127.0.0.1:8000", sys.argv[1]
cfg = json.load(urllib.request.urlopen(api + "/api/settings", timeout=20))
cfg["model"]["device"] = device
req = urllib.request.Request(api + "/api/settings", data=json.dumps(cfg).encode(),
                             headers={"Content-Type": "application/json"}, method="PUT")
urllib.request.urlopen(req, timeout=180).read()
print("   model.device = " + device)
PY
}

echo "== what the machine offers =="
"$PY" - <<'PY'
try:
    import openvino as ov
    core = ov.Core()
    for name in core.available_devices:
        try:
            full = core.get_property(name, "FULL_DEVICE_NAME")
        except Exception:
            full = "?"
        print("   %-6s %s" % (name, full))
except Exception as exc:
    print("   could not query OpenVINO:", exc)
PY

if [ "$TARGET" = "GPU" ] && ! "$PY" -c "import openvino, sys; sys.exit(0 if 'GPU' in openvino.Core().available_devices else 1)"; then
    echo
    echo "No GPU device is available to OpenVINO. The driver package is usually missing:"
    echo "   sudo apt install -y intel-opencl-icd"
    echo "then run this again."
    exit 1
fi

echo
echo "== before =="
BEFORE_RESTARTS=$(systemctl show peoplecounter -p NRestarts --value)
BEFORE=$(rate)
echo "   $BEFORE detections/s per camera, service restarts so far: $BEFORE_RESTARTS"

echo
echo "== switching to $TARGET =="
set_device "$TARGET"
sudo systemctl restart peoplecounter
echo "   waiting ${WATCH_SECONDS}s, watching for driver trouble"

hang=0
for _ in $(seq 1 "$((WATCH_SECONDS / 10))"); do
    sleep 10
    if sudo dmesg | tail -80 | grep -qiE "GPU HANG|Resetting rcs|GPU hang"; then
        hang=1
        break
    fi
done

AFTER_RESTARTS=$(systemctl show peoplecounter -p NRestarts --value)
CRASHED=$((AFTER_RESTARTS - BEFORE_RESTARTS - 1))   # the restart above is expected
AFTER=$(rate)

echo
echo "== after =="
echo "   $AFTER detections/s per camera"
echo "   unexpected restarts: $CRASHED"
echo "   gpu hang in the kernel log: $hang"

if [ "$TARGET" = "GPU" ] && { [ "$hang" = "1" ] || [ "$CRASHED" -gt 0 ]; }; then
    echo
    echo "!! the GPU is not holding up -- putting detection back on the CPU"
    set_device CPU
    sudo systemctl restart peoplecounter
    sleep 30
    echo "   back on CPU at $(rate) detections/s"
    exit 2
fi

echo
echo "$BEFORE -> $AFTER detections/s on $TARGET"
echo "Anything from about 2 per second counts people reliably; 5 and above is comfortable."
