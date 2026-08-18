#!/usr/bin/env bash
# Update the counting station from git, on the machine that runs it.
#
#   cd ~/people-counter && ./tools/update.sh
#
# Safe to run on a station that was installed by copying files: it wires up the
# remote the first time and takes the code from there afterwards. Only tracked
# files are touched -- data/config.json, the database, the snapshots and the
# built frontend are ignored by git and stay exactly as they are, so the
# cameras, the counting lines, the api key and today's counts all survive.
set -euo pipefail

REPO="${REPO:-}"
BRANCH="${BRANCH:-main}"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="${SERVICE:-peoplecounter}"
cd "$APP"

echo "== updating $APP =="

if [ ! -d .git ]; then
    if [ -z "$REPO" ]; then
        echo "This is not a git checkout yet. Run it once with the repository:" >&2
        echo "  REPO=https://github.com/USER/REPO.git ./tools/update.sh" >&2
        exit 1
    fi
    git init -q
    git remote add origin "$REPO"
fi

if [ -n "$REPO" ]; then
    git remote set-url origin "$REPO"
fi

echo "-- fetching"
git fetch --quiet origin "$BRANCH"

echo "-- what is about to change"
git --no-pager diff --stat "HEAD..origin/$BRANCH" 2>/dev/null || echo "   (first update from this remote)"

echo "-- applying"
git checkout -q -B "$BRANCH" "origin/$BRANCH"

VENV="$APP/.venv/bin"
if [ -x "$VENV/pip" ]; then
    echo "-- dependencies"
    "$VENV/pip" install -q -r backend/requirements-runtime.txt || {
        echo "   dependency install failed; the service is being left alone" >&2
        exit 1
    }
fi

echo "-- does it compile"
"$VENV/python" -m py_compile backend/*.py

echo "-- tests"
"$VENV/python" tools/tracker_rate_test.py 2>&1 | tail -4
"$VENV/python" tools/tracker_edge_cases.py 2>&1 | tail -2

echo "-- restarting $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 20
systemctl is-active "$SERVICE"

echo "-- how it is doing"
"$VENV/python" - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/stats", timeout=15) as r:
        stats = json.load(r)
except Exception as exc:
    raise SystemExit(f"the web service is not answering yet: {exc}")
print("site:", json.dumps(stats.get("site")))
for cam in stats.get("cameras", []):
    print("cam %s  %.1f detections/s  in=%s out=%s online=%s" % (
        cam["camera_id"], float(cam.get("fps") or 0),
        cam.get("in"), cam.get("out"), cam.get("online")))
PY

echo
echo "now on $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"
