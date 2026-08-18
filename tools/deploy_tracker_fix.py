"""Ship the tracking fix to the counting station and prove it took.

Uploads the new tracker, checks it compiles, runs the regression test on the
station itself, frees the cores the detector needs, restarts the service and
reports the detection rate it settles at.

    export COUNTER_HOST=10.1.100.87 COUNTER_USER=adminpse
    python tools/deploy_tracker_fix.py                 # code + cpu cleanup
    python tools/deploy_tracker_fix.py --headless      # also drop the desktop

The password is read from COUNTER_PASSWORD or asked for; --host and --user
override the environment.

--headless is for when the displays move off this machine and onto a PC that
opens http://10.1.100.87:8000 over the LAN. Without a desktop there is no X, no
GNOME, no browser and no screensaver on the counting station, which on the
measurements taken here is most of a core handed back to detection.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

import paramiko

# Nothing about the station is baked in: it moves between sites, and its login
# has no business living in a repository.
HOST = os.environ.get("COUNTER_HOST", "")
USER = os.environ.get("COUNTER_USER", "adminpse")
PASSWORD = os.environ.get("COUNTER_PASSWORD", "")
APP = os.environ.get("COUNTER_PATH", "/home/adminpse/people-counter")
PY = f"{APP}/.venv/bin/python"
ROOT = Path(__file__).resolve().parent.parent

FILES = [
    ("backend/tracker.py", f"{APP}/backend/tracker.py"),
    ("backend/camera_worker.py", f"{APP}/backend/camera_worker.py"),
    ("tools/tracker_rate_test.py", f"{APP}/tools/tracker_rate_test.py"),
]

FREE_THE_CORES = r"""
export DISPLAY=:0
export XAUTHORITY=/run/user/1000/gdm/Xauthority
# xscreensaver cycles GL demos -- flipscreen3d, peepers -- at about half a core
# each. A counting station has no use for a screensaver.
xscreensaver-command -exit 2>/dev/null
pkill -f xscreensaver 2>/dev/null
for demo in flipscreen3d peepers glmatrix hypertorus kaleidocycle glslideshow; do
    pkill -f "$demo" 2>/dev/null
done
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/xscreensaver.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=XScreenSaver
Exec=/bin/true
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
xset s off 2>/dev/null; xset -dpms 2>/dev/null; xset s noblank 2>/dev/null
echo "screensaver processes left: $(pgrep -cf 'xscreensaver|flipscreen3d|peepers')"
"""

ONE_CORE_EACH = r"""
import json, urllib.request
BASE = "http://127.0.0.1:8000"

def get(p):
    with urllib.request.urlopen(BASE + p, timeout=20) as r:
        return json.load(r)

def put(p, body):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status

cfg = get("/api/settings")
cfg["model"]["threads_per_camera"] = 1   # two cameras, two cores, no fighting
for cam in cfg["cameras"]:
    cam["detect_every_n"] = 1
print("threads_per_camera=1, detect_every_n=1 ->", put("/api/settings", cfg))
"""


def connect(host: str, user: str, password: str, timeout: float = 90.0) -> paramiko.SSHClient:
    deadline = time.time() + timeout
    while time.time() < deadline:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, username=user, password=password, timeout=8)
            return client
        except Exception:
            client.close()
            time.sleep(5)
    raise SystemExit(f"{host} is not answering — check it is powered on and reachable.")


def run(client: paramiko.SSHClient, command: str, timeout: float = 600.0) -> str:
    channel = client.get_transport().open_session()
    channel.settimeout(timeout)
    channel.set_combine_stderr(True)
    channel.exec_command(command)
    out = channel.makefile("r").read().decode(errors="replace")
    channel.recv_exit_status()
    channel.close()
    return out.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true",
                        help="boot without a desktop; displays move to a PC on the LAN")
    parser.add_argument("--host", default=HOST, help="station address")
    parser.add_argument("--user", default=USER, help="login on the station")
    args = parser.parse_args()

    host = args.host or input("station address: ").strip()
    user = args.user
    password = PASSWORD or getpass.getpass(f"password for {user}@{host}: ")

    client = connect(host, user, password)
    print("connected to", host)

    print("\n=== backing up what is being replaced ===")
    print(run(client, f"cd {APP}/backend && for f in tracker.py camera_worker.py; do "
                      f"cp $f $f.bak-$(date +%H%M%S); done && ls -t *.bak-* | head -2"))

    print("\n=== uploading ===")
    sftp = client.open_sftp()
    for local, remote in FILES:
        sftp.put(str(ROOT / local), remote)
        print("  ", local)
    sftp.close()

    print("\n=== compiles? ===")
    print(run(client, f"cd {APP}/backend && {PY} -m py_compile tracker.py camera_worker.py "
                      f"&& echo 'compile OK'"))

    print("\n=== regression test, on the station ===")
    print(run(client, f"cd {APP} && {PY} tools/tracker_rate_test.py 2>&1 | tail -16"))

    print("\n=== free the cores ===")
    print(run(client, f"bash -s <<'SH'\n{FREE_THE_CORES}\nSH"))

    print("\n=== restart and settle ===")
    print(run(client, f"echo {password} | sudo -S systemctl restart peoplecounter "
                      f">/dev/null 2>&1; sleep 25; systemctl is-active peoplecounter"))
    print(run(client, f"{PY} - <<'PY'\n{ONE_CORE_EACH}\nPY"))

    if args.headless:
        print("\n=== dropping the desktop ===")
        print(run(client, f"echo {password} | sudo -S systemctl set-default multi-user.target "
                          f"2>&1 | tail -1; systemctl get-default"))
        print("the displays now live on whatever PC opens http://%s:8000" % host)
        print("to bring the desktop back: sudo systemctl set-default graphical.target")

    print("\n=== detection rate after settling ===")
    print(run(client, "sleep 90; " + PY + " - <<'PY'\n"
              "import json, urllib.request\n"
              "with urllib.request.urlopen('http://127.0.0.1:8000/api/stats', timeout=15) as r:\n"
              "    s = json.load(r)\n"
              "print('site:', json.dumps(s.get('site')))\n"
              "for c in s.get('cameras', []):\n"
              "    print('cam %s  %.1f detections/s  in=%s out=%s online=%s' % (\n"
              "        c['camera_id'], float(c.get('fps') or 0), c.get('in'), c.get('out'),\n"
              "        c.get('online')))\n"
              "PY", timeout=300))
    print(run(client, "uptime; ps -eo pcpu,comm --sort=-pcpu | head -6"))

    client.close()
    print("\ndone. Walk past a camera and watch the counters move.")


if __name__ == "__main__":
    sys.exit(main())
