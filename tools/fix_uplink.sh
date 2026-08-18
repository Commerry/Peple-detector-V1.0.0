#!/usr/bin/env bash
# Get a station talking to FactoryBox from anywhere.
#
#   bash tools/fix_uplink.sh
#
# Two things stopped it once the station left the server's own network:
#
#   * the upload address was the server's LAN IP, which resolves to nothing
#     anywhere else. It becomes the API domain.
#   * Cloudflare refuses urllib's default "Python-urllib/3.x" with error 1010
#     before the request ever reaches the server, so even the right address
#     failed. Every upload now carries a real product name.
#
# Only those two things change. Cameras, counting lines, the api key, the
# database and today's counts are untouched, and running it twice is harmless.
set -euo pipefail

APP="${APP:-$HOME/people-counter}"
DOMAIN="${DOMAIN:-https://dashboardapi.factoryboxx.com}"
SERVICE="${SERVICE:-peoplecounter}"
PY="$APP/.venv/bin/python"
UPLOADER="$APP/backend/uploader.py"

[ -f "$UPLOADER" ] || { echo "no uploader at $UPLOADER — set APP=/path/to/people-counter" >&2; exit 1; }

echo "== patching $(basename "$UPLOADER") =="
cp -n "$UPLOADER" "$UPLOADER.bak-uplink" 2>/dev/null || true
"$PY" - "$UPLOADER" <<'PY'
import io, sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in src else "\n"

if "USER_AGENT" in src:
    print("   already carries a user agent")
else:
    anchor = "TIMEOUT = 15"
    block = (
        'TIMEOUT = 15\n'
        '\n'
        '# The platform sits behind Cloudflare, which rejects urllib\'s default\n'
        '# "Python-urllib/3.x" outright with error 1010 -- no request ever reaches the\n'
        '# server, from any network. Any honest product name gets through, so the station\n'
        '# says who it is.\n'
        'USER_AGENT = "PeopleCounter/1.0 (+FactoryBox counting station)"\n'
        'HEADERS = {"Content-Type": "application/json", "User-Agent": USER_AGENT}'
    ).replace("\n", nl)
    if anchor.replace("\n", nl) not in src:
        raise SystemExit("   could not find TIMEOUT = 15 to patch after")
    src = src.replace(anchor, block, 1)
    old_headers = 'headers={"Content-Type": "application/json"},'
    count = src.count(old_headers)
    src = src.replace(old_headers, "headers=HEADERS,")
    io.open(path, "w", encoding="utf-8", newline="").write(src)
    print(f"   user agent added, {count} request(s) now send it")
PY

"$PY" -m py_compile "$UPLOADER"
echo "   compiles"

echo
echo "== pointing the upload at $DOMAIN =="
"$PY" - "$DOMAIN" <<'PY'
import json, sys, urllib.request

base_api = "http://127.0.0.1:8000"
domain = sys.argv[1]
with urllib.request.urlopen(base_api + "/api/settings", timeout=20) as r:
    cfg = json.load(r)
before = cfg.get("upload", {}).get("base_url")
cfg.setdefault("upload", {})["base_url"] = domain
cfg["upload"]["enabled"] = True
req = urllib.request.Request(
    base_api + "/api/settings",
    data=json.dumps(cfg).encode(),
    headers={"Content-Type": "application/json"},
    method="PUT",
)
with urllib.request.urlopen(req, timeout=90) as r:
    print(f"   {before} -> {domain}  ({r.read().decode()[:60]})")
PY

echo
echo "== restarting $SERVICE =="
sudo systemctl restart "$SERVICE"
sleep 20
systemctl is-active "$SERVICE"

echo
echo "== can it actually reach FactoryBox? =="
"$PY" - "$DOMAIN" <<'PY'
import json, sys, urllib.error, urllib.request

domain = sys.argv[1].rstrip("/")
ua = "PeopleCounter/1.0 (+FactoryBox counting station)"
req = urllib.request.Request(
    domain + "/api/data",
    data=json.dumps({"api_key": "connectivity-probe", "sensors": []}).encode(),
    headers={"Content-Type": "application/json", "User-Agent": ua},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode(errors="replace")[:120]
        code = r.status
except urllib.error.HTTPError as e:
    code, body = e.code, e.read().decode(errors="replace")[:120]
except Exception as exc:
    raise SystemExit(f"   cannot reach {domain}: {type(exc).__name__}: {exc}")

if "1010" in body:
    raise SystemExit("   Cloudflare is still refusing this client (error 1010)")
print(f"   {domain} answers as the application: HTTP {code} {body}")
print("   (a rejected probe key here is the right answer -- it proves the")
print("    request reached the server rather than being blocked at the edge)")
PY

echo
echo "== upload status =="
sleep 65   # one upload cycle
"$PY" - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/upload/status", timeout=20) as r:
    print("  ", json.dumps(json.load(r)))
with urllib.request.urlopen("http://127.0.0.1:8000/api/stats", timeout=20) as r:
    print("   site:", json.dumps(json.load(r).get("site")))
PY
