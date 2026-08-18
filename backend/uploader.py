"""Push counter values to the FactoryBox IoT platform.

All cameras roll up into one device on the dashboard, so entry and exit appear
on a single page. Counts only, never images. Values are posted on a timer
and buffered on disk when the network or the server is down, so a power cut at
either end cannot lose a day's numbers.

Platform contract (verified against the running server):
  POST {base}/api/data      Content-Type: application/json
  body {"api_key": "...", "timestamp": "...Z", "sensors":[{type,value,unit}], "metadata": {...}}
The api_key travels in the body, not a header. Timestamps must end in "Z" —
the server's validator rejects the "+00:00" form Python produces by default.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import database
from config import DATA_DIR

QUEUE_PATH = DATA_DIR / "upload_queue.json"
CURSOR_PATH = DATA_DIR / "upload_cursor.txt"   # last event id sent to the cloud
MAX_QUEUE = 500          # ~2 days of 5-minute batches
TIMEOUT = 15

# The platform sits behind Cloudflare, which rejects urllib's default
# "Python-urllib/3.x" outright with error 1010 -- no request ever reaches the
# server, from any network. Any honest product name gets through, so the station
# says who it is.
USER_AGENT = "PeopleCounter/1.0 (+FactoryBox counting station)"
HEADERS = {"Content-Type": "application/json", "User-Agent": USER_AGENT}


def _utc_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Uploader(threading.Thread):
    """Posts each camera's counters every `interval_s`."""

    def __init__(self, cfg: dict, stats_fn):
        super().__init__(daemon=True, name="uploader")
        self.cfg = cfg or {}
        self.stats_fn = stats_fn        # () -> list of camera stat dicts
        self._stop = threading.Event()
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.sent = 0
        self.queued = 0
        self.dropped = 0
        self.events_sent = 0

    def stop(self) -> None:
        self._stop.set()

    # ---------- offline buffer ----------

    def _load_queue(self) -> list:
        try:
            return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _collapse(pending: list) -> list:
        """Keep the newest reading of each day and drop the rest.

        These payloads are snapshots of counters that only climb until midnight,
        and the platform stores a day as the highest value it saw, so an older
        snapshot from the same day carries nothing the newer one does not. After
        a long outage the queue holds hundreds of them, and replaying the lot in
        order meant the dashboard spent a quarter of an hour showing this
        morning's number before it caught up with the live one. One reading per
        day keeps every day's figure and shows today's straight away.
        """
        newest: dict[str, dict] = {}
        for item in pending:
            stamp = str(item.get("timestamp", ""))[:10] or "unknown"
            newest[stamp] = item          # later items overwrite earlier ones
        return [newest[day] for day in sorted(newest)]

    def _save_queue(self, items: list) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = QUEUE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(items[-MAX_QUEUE:]), encoding="utf-8")
            tmp.replace(QUEUE_PATH)
        except OSError:
            pass

    # ---------- transport ----------

    # ---------- crossing log ----------

    def _read_cursor(self) -> int:
        try:
            return int(CURSOR_PATH.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_cursor(self, value: int) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CURSOR_PATH.write_text(str(int(value)), encoding="utf-8")
        except OSError:
            pass

    def _send_events(self, api_key: str) -> None:
        """Push crossings the cloud has not seen yet, oldest first, so its log
        matches this station's log line for line."""
        base = str(self.cfg.get("base_url", "")).rstrip("/")
        if not base:
            return
        cursor = self._read_cursor()
        rows = database.events_after(cursor, limit=300)
        if not rows:
            return
        body = {
            "api_key": api_key,
            "events": [
                {"id": r["id"], "direction": r["direction"],
                 "at": r["at"], "camera": r["camera"]}
                for r in rows
            ],
        }
        req = urllib.request.Request(
            f"{base}/api/people-counter/events",
            data=json.dumps(body).encode(),
            headers=HEADERS,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if r.status == 200 and '"success":true' in r.read().decode(errors="replace"):
                    self._write_cursor(rows[-1]["id"])
                    self.events_sent += len(rows)
        except urllib.error.HTTPError as e:
            self.last_error = f"events HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            self.last_error = f"events {type(e).__name__}"

    def _post(self, payload: dict) -> tuple[str, str]:
        """Returns (outcome, detail) where outcome is ok | retry | drop.

        A rejected payload (bad key, bad shape) can never succeed, so it is
        dropped rather than left at the head of the queue blocking everything
        behind it. Only transport and server-side failures are retried.
        """
        base = str(self.cfg.get("base_url", "")).rstrip("/")
        if not base:
            return "retry", "no server URL configured"
        req = urllib.request.Request(
            f"{base}/api/data",
            data=json.dumps(payload).encode(),
            headers=HEADERS,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read().decode(errors="replace")
                ok = r.status == 200 and '"success":true' in body
                return ("ok" if ok else "retry"), body[:200]
        except urllib.error.HTTPError as e:
            detail = f"HTTP {e.code}: {e.read().decode(errors='replace')[:160]}"
            if 400 <= e.code < 500 and e.code != 429:
                return "drop", detail
            return "retry", detail
        except Exception as e:  # noqa: BLE001 — network errors must never kill the thread
            return "retry", f"{type(e).__name__}: {e}"

    def _payload(self, stats: dict, api_key: str) -> dict:
        """The site total, exactly as the local displays show it: IN from the
        entrance camera, OUT from the exit camera — never the two added up."""
        site = stats.get("site", {})
        cams = stats.get("cameras", [])
        return {
            "api_key": api_key,
            "timestamp": _utc_z(),
            "sensors": [
                {"type": "people_in", "value": int(site.get("in", 0)), "unit": "persons"},
                {"type": "people_out", "value": int(site.get("out", 0)), "unit": "persons"},
                {"type": "occupancy", "value": int(site.get("inside", 0)), "unit": "persons"},
            ],
            "metadata": {
                "in_camera": site.get("in_camera"),
                "out_camera": site.get("out_camera"),
                "cameras": [
                    {"id": c["camera_id"], "name": c.get("name", ""),
                     "online": bool(c.get("online"))}
                    for c in cams
                ],
            },
        }

    # ---------- main loop ----------

    def run(self) -> None:
        interval = max(10, int(self.cfg.get("interval_s", 60)))
        pending = self._collapse(self._load_queue())

        while not self._stop.wait(interval):
            if not self.cfg.get("enabled"):
                continue
            # one device on the dashboard; fall back to the old per-camera map
            key = self.cfg.get("api_key") or next(
                (v for v in (self.cfg.get("api_keys") or {}).values() if v), ""
            )
            if not key:
                self.last_error = "no API key configured"
                continue
            try:
                stats = self.stats_fn()
                if stats.get("cameras"):
                    pending.append(self._payload(stats, key))
            except Exception as e:  # noqa: BLE001
                self.last_error = f"stats: {e}"

            # An outage leaves a pile of same-day snapshots behind; only the
            # newest of each day says anything the platform will keep.
            pending = self._collapse(pending)

            # drain oldest first; stop on the first retryable failure so
            # ordering holds, but discard anything the server will never accept
            while pending:
                outcome, msg = self._post(pending[0])
                if outcome == "retry":
                    self.last_error = msg
                    break
                pending.pop(0)
                if outcome == "ok":
                    self.sent += 1
                    self.last_ok = datetime.now().isoformat(timespec="seconds")
                    self.last_error = None
                else:
                    self.dropped += 1
                    self.last_error = f"discarded: {msg}"

            # keep the cloud crossing log in step with this station's log
            self._send_events(key)

            self.queued = len(pending)
            if len(pending) > MAX_QUEUE:
                pending = pending[-MAX_QUEUE:]
            self._save_queue(pending)

    def status(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("enabled")),
            "server": self.cfg.get("base_url", ""),
            "sent": self.sent,
            "queued": self.queued,
            "dropped": self.dropped,
            "events_sent": self.events_sent,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
        }
