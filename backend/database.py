"""SQLite storage — events log + aggregate queries. Thread-safe via a single lock."""
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from config import DATA_DIR

DB_PATH = DATA_DIR / "people_counter.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL,
                track_id INTEGER,
                direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
                timestamp TEXT NOT NULL,
                snapshot TEXT,
                attributes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_cam_ts
                ON events (camera_id, timestamp);
            """
        )
        conn.commit()


def close() -> None:
    """Checkpoint the WAL and close on shutdown so no data sits in -wal."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None


def insert_event(
    camera_id: int,
    track_id: int | None,
    direction: str,
    snapshot: str | None = None,
    attributes: str | None = None,
) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO events (camera_id, track_id, direction, timestamp, snapshot, attributes)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (camera_id, track_id, direction, ts, snapshot, attributes),
        )
        conn.commit()


def today_counts(camera_id: int) -> dict:
    """Counts for the current calendar day (used to restore state after restart)."""
    day = date.today().isoformat()
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT direction, COUNT(*) FROM events"
            " WHERE camera_id = ? AND timestamp >= ? GROUP BY direction",
            (camera_id, day),
        ).fetchall()
    counts = {"IN": 0, "OUT": 0}
    for direction, n in rows:
        counts[direction] = n
    return counts


def hourly_summary(camera_id: int | None, day: str) -> list[dict]:
    """Per-hour IN/OUT counts for one date (YYYY-MM-DD). camera_id None = all cameras."""
    q = (
        "SELECT strftime('%H', timestamp) AS hour,"
        " SUM(direction = 'IN') AS n_in, SUM(direction = 'OUT') AS n_out"
        " FROM events WHERE date(timestamp) = ?"
    )
    params: list = [day]
    if camera_id is not None:
        q += " AND camera_id = ?"
        params.append(camera_id)
    q += " GROUP BY hour ORDER BY hour"
    with _lock:
        rows = _get_conn().execute(q, params).fetchall()
    found = {int(h): (i or 0, o or 0) for h, i, o in rows}
    return [
        {"hour": h, "in": found.get(h, (0, 0))[0], "out": found.get(h, (0, 0))[1]}
        for h in range(24)
    ]


def daily_summary(camera_id: int | None, days: int) -> list[dict]:
    """Per-day totals for the last N days, inclusive of today.

    The cutoff is computed in Python: timestamps are written in local time,
    while SQLite's date('now') is UTC — comparing them shifts the window by
    the UTC offset (7 hours here) and drops or adds a day.
    """
    days = max(1, min(days, 365))
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    q = (
        "SELECT date(timestamp) AS day,"
        " SUM(direction = 'IN') AS n_in, SUM(direction = 'OUT') AS n_out"
        " FROM events WHERE date(timestamp) >= ?"
    )
    params: list = [cutoff]
    if camera_id is not None:
        q += " AND camera_id = ?"
        params.append(camera_id)
    q += " GROUP BY day ORDER BY day"
    with _lock:
        rows = _get_conn().execute(q, params).fetchall()
    return [{"date": d, "in": i or 0, "out": o or 0} for d, i, o in rows]


def events_after(last_id: int, limit: int = 300) -> list[dict]:
    """Crossings newer than `last_id`, oldest first — used by the uploader so
    the cloud log matches this station's own log exactly."""
    with _lock:
        rows = _get_conn().execute(
            "SELECT id, camera_id, direction, timestamp FROM events"
            " WHERE id > ? ORDER BY id LIMIT ?",
            (int(last_id), max(1, min(limit, 1000))),
        ).fetchall()
    return [
        {"id": i, "camera": cam, "direction": d, "at": ts}
        for i, cam, d, ts in rows
    ]


def recent_events(camera_id: int | None, limit: int = 20) -> list[dict]:
    q = (
        "SELECT camera_id, track_id, direction, timestamp, snapshot"
        " FROM events"
    )
    params: list = []
    if camera_id is not None:
        q += " WHERE camera_id = ?"
        params.append(camera_id)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))  # LIMIT -1 means "unlimited" in SQLite
    with _lock:
        rows = _get_conn().execute(q, params).fetchall()
    return [
        {"camera_id": c, "track_id": t, "direction": d, "timestamp": ts, "snapshot": s}
        for c, t, d, ts, s in rows
    ]


def export_rows(camera_id: int | None, date_from: str, date_to: str) -> list[tuple]:
    q = (
        "SELECT id, camera_id, track_id, direction, timestamp, snapshot"
        " FROM events WHERE date(timestamp) BETWEEN ? AND ?"
    )
    params: list = [date_from, date_to]
    if camera_id is not None:
        q += " AND camera_id = ?"
        params.append(camera_id)
    q += " ORDER BY timestamp"
    with _lock:
        return _get_conn().execute(q, params).fetchall()
