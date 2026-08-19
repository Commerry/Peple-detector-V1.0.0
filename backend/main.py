"""FastAPI app: MJPEG streams, WebSocket stats, settings, history, health.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import csv
import io
import json
import os
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Must be set before any VideoCapture is opened (FFmpeg reads it at open time).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
)

import psutil
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import database
import xlsx_report
from camera_worker import CameraWorker
from config import DATA_DIR, SNAPSHOT_DIR, load_config, save_config
from uploader import Uploader

app = FastAPI(title="People Counter")


@app.middleware("http")
async def no_cache_html(request, call_next):
    """index.html must always revalidate — a cached shell pointing at purged
    hashed assets renders a broken page after every frontend rebuild."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


# ---------- worker manager ----------

class WorkerManager:
    def __init__(self):
        self.workers: dict[int, CameraWorker] = {}
        self._draining: list[CameraWorker] = []  # asked to stop, not dead yet
        self._lock = threading.Lock()

    def _reap_draining(self) -> None:
        """Join any worker that finally exited; keep the rest for next time."""
        still = []
        for w in self._draining:
            w.join(timeout=0)
            if w.is_alive():
                still.append(w)
        self._draining = still

    def start_all(self) -> None:
        cfg = load_config()
        model_cfg = dict(cfg.get("model", {}))
        # Split the cores between cameras: two models each grabbing every core
        # thrash against each other on a small CPU.
        enabled = sum(
            1 for c in cfg["cameras"] if c.get("enabled") and isinstance(c.get("id"), int)
        )
        # Only worth splitting on a machine with cores to spare: on a dual-core
        # box one thread per camera doubles inference latency, and the bursty
        # workload schedules better if both share both cores.
        cores = os.cpu_count() or 4
        if not model_cfg.get("threads_per_camera") and enabled > 1 and cores >= 4:
            model_cfg["threads_per_camera"] = max(1, cores // enabled)
        with self._lock:
            self._reap_draining()
            draining_ids = {w.cam_id for w in self._draining}
            for cam in cfg["cameras"]:
                cam_id = cam.get("id")
                if not isinstance(cam_id, int) or not cam.get("enabled"):
                    continue
                if cam_id in self.workers or cam_id in draining_ids:
                    # never run two pipelines for one camera: the old thread is
                    # still holding the stream and would double-count events
                    continue
                w = CameraWorker(
                    cam, model_cfg, cfg.get("snapshots", {}),
                    cfg.get("jpeg_quality", 70),
                    tracking_cfg=cfg.get("tracking", {}),
                    counting_cfg=cfg.get("counting", {}),
                )
                w.start()
                self.workers[cam_id] = w

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self.workers.values())
            self.workers.clear()
            for w in workers:
                w.stop()
            for w in workers:
                w.join(timeout=5)
            # a worker blocked in cap.read() on a hung stream can outlive the
            # join; track it so a replacement isn't started underneath it
            self._draining.extend(w for w in workers if w.is_alive())
            self._reap_draining()

    def restart(self) -> None:
        self.stop_all()
        self.start_all()

    def supervise(self) -> None:
        """Keep every enabled camera running: re-spawn threads that died, and
        start cameras that were skipped earlier because their previous worker
        was still draining (a hung RTSP read can outlive the restart)."""
        with self._lock:
            self._reap_draining()
            dead = [cid for cid, w in self.workers.items() if not w.is_alive()]
            for cid in dead:
                self.workers.pop(cid, None)
        if dead:
            print(f"[supervisor] restarting dead camera workers: {dead}")
        # start_all only fills gaps, so calling it every tick is safe and also
        # covers cameras that had no worker at all
        self.start_all()

    def get(self, cam_id: int) -> CameraWorker | None:
        w = self.workers.get(cam_id)
        return w if w is not None and w.is_alive() else None


manager = WorkerManager()


uploader: Uploader | None = None


def _start_uploader() -> None:
    """(Re)start the FactoryBox pusher with the current settings."""
    global uploader
    if uploader is not None:
        uploader.stop()
    uploader = Uploader(load_config().get("upload", {}), _all_stats)
    uploader.start()


@app.on_event("startup")
def _startup() -> None:
    database.init_db()
    manager.start_all()
    _start_uploader()
    threading.Thread(target=_snapshot_cleanup_loop, daemon=True).start()
    threading.Thread(target=_supervisor_loop, daemon=True).start()


def _supervisor_loop() -> None:
    """Restart camera workers that died so a crash can't take a camera
    offline until someone notices and restarts the whole app."""
    while True:
        time.sleep(10)
        try:
            manager.supervise()
        except Exception:  # noqa: BLE001
            pass


@app.on_event("shutdown")
def _shutdown() -> None:
    manager.stop_all()
    database.close()


def _snapshot_cleanup_loop() -> None:
    """Delete snapshot folders older than keep_days. Runs hourly."""
    while True:
        try:
            keep = int(load_config().get("snapshots", {}).get("keep_days", 7))
            cutoff = date.today() - timedelta(days=keep)
            if SNAPSHOT_DIR.exists():
                for day_dir in SNAPSHOT_DIR.iterdir():
                    try:
                        if day_dir.is_dir() and date.fromisoformat(day_dir.name) < cutoff:
                            shutil.rmtree(day_dir, ignore_errors=True)
                    except ValueError:
                        continue
        except Exception:
            pass
        time.sleep(3600)


# ---------- video ----------

@app.get("/video/{cam_id}")
def video_stream(cam_id: int):
    worker = manager.get(cam_id)
    if worker is None:
        raise HTTPException(404, "camera not running")

    def gen():
        worker.add_viewer()
        seq = 0
        idle = 0.0
        try:
            while True:
                # end the response instead of looping forever on a stopped or
                # dead worker (each open generator pins a threadpool slot)
                if worker.stopping or not worker.is_alive():
                    return
                jpeg, new_seq = worker.wait_jpeg(seq)
                if jpeg is None or new_seq == seq:
                    idle += 2.0
                    if idle >= 30.0:  # nothing new for 30s — let the client retry
                        return
                    continue
                idle = 0.0
                seq = new_seq
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            worker.remove_viewer()

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/frame/{cam_id}")
def single_frame(cam_id: int):
    """One current frame (used by the line editor)."""
    worker = manager.get(cam_id)
    if worker is None:
        raise HTTPException(404, "camera not running")
    worker.add_viewer()
    try:
        jpeg, _ = worker.wait_jpeg(-1, timeout=5.0)
    finally:
        worker.remove_viewer()
    if jpeg is None:
        raise HTTPException(503, "no frame available yet")
    return Response(jpeg, media_type="image/jpeg")


# ---------- stats / history ----------

BASELINE_PATH = DATA_DIR / "display_baseline.json"


def _load_baseline() -> dict:
    """What was on the counters when someone last pressed Reset today.

    The screens show counts measured from that moment; the database keeps every
    crossing regardless, so history and exports are untouched. Yesterday's
    baseline means nothing after the midnight rollover, hence the date check.
    """
    try:
        raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        if raw.get("date") == date.today().isoformat():
            return {int(k): v for k, v in raw.get("cameras", {}).items()}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _apply_baseline(cams: list[dict]) -> None:
    baseline = _load_baseline()
    if not baseline:
        return
    for cam in cams:
        base = baseline.get(cam["camera_id"])
        if not base:
            continue
        cam["in"] = max(0, cam["in"] - int(base.get("in", 0)))
        cam["out"] = max(0, cam["out"] - int(base.get("out", 0)))
        cam["inside"] = max(0, cam["in"] - cam["out"])


def _all_stats() -> dict:
    cfg = load_config()
    cams = []
    seen: set[int] = set()
    for cam in cfg["cameras"]:
        cam_id = cam.get("id")
        if not isinstance(cam_id, int) or cam_id in seen:
            continue  # a hand-edited config must not break the whole payload
        seen.add(cam_id)
        w = manager.get(cam_id)
        if w:
            cams.append(w.stats())
        else:
            counts = database.today_counts(cam_id)
            cams.append({
                "camera_id": cam_id, "name": cam.get("name", ""),
                "online": False, "fps": 0.0,
                "in": counts["IN"], "out": counts["OUT"],
                "inside": max(0, counts["IN"] - counts["OUT"]),
                "error": None if cam.get("enabled") else "disabled",
            })
    # Screens count from the last Reset press; the database keeps everything.
    _apply_baseline(cams)

    # Site total: IN comes from the entrance camera, OUT from the exit camera.
    # Every display and the cloud upload use this one number, so they agree.
    site_cfg = cfg.get("site", {})
    in_cam = site_cfg.get("in_camera", 1)
    out_cam = site_cfg.get("out_camera", 2)
    by_id = {c["camera_id"]: c for c in cams}
    site_in = int(by_id.get(in_cam, {}).get("in", 0))
    site_out = int(by_id.get(out_cam, {}).get("out", 0))

    return {
        "cameras": cams,
        "site": {
            "in": site_in,
            "out": site_out,
            "inside": max(0, site_in - site_out),
            "in_camera": in_cam,
            "out_camera": out_cam,
        },
        "cpu": psutil.cpu_percent(interval=None),
        "mem": psutil.virtual_memory().percent,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/stats")
def get_stats():
    return _all_stats()


@app.post("/api/counters/reset")
def reset_counters():
    """Zero what the screens show, keep everything in the database.

    The current raw totals become the day's baseline; every display counts on
    from here. History, exports and the crossing log are untouched, and the
    midnight rollover clears the baseline along with the day itself."""
    cfg = load_config()
    cameras: dict[str, dict] = {}
    for cam in cfg["cameras"]:
        cam_id = cam.get("id")
        if not isinstance(cam_id, int):
            continue
        w = manager.get(cam_id)
        if w:
            raw = w.stats()
            cameras[str(cam_id)] = {"in": raw["in"], "out": raw["out"]}
        else:
            counts = database.today_counts(cam_id)
            cameras[str(cam_id)] = {"in": counts["IN"], "out": counts["OUT"]}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BASELINE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"date": date.today().isoformat(), "cameras": cameras}),
        encoding="utf-8",
    )
    os.replace(tmp, BASELINE_PATH)
    return {"ok": True, "stats": _all_stats()}


def _validate_config(cfg: dict) -> None:
    """Reject anything that would silently break counting or a worker thread.
    Returning 400 here turns invisible failures into a visible error."""
    cams = cfg.get("cameras")
    if not isinstance(cams, list) or not cams:
        raise HTTPException(400, "cameras must be a non-empty list")
    seen: set[int] = set()
    for cam in cams:
        if not isinstance(cam, dict):
            raise HTTPException(400, "each camera must be an object")
        cam_id = cam.get("id")
        if not isinstance(cam_id, int) or isinstance(cam_id, bool):
            raise HTTPException(400, f"camera id must be a whole number: {cam_id!r}")
        if cam_id in seen:
            raise HTTPException(400, f"duplicate camera id: {cam_id}")
        seen.add(cam_id)
        if not isinstance(cam.get("source"), str):
            raise HTTPException(400, f"camera {cam_id}: source must be text")
        if cam.get("enabled") and not cam["source"].strip():
            raise HTTPException(400, f"camera {cam_id}: enabled but has no RTSP URL")
        line = cam.get("line")
        if not isinstance(line, dict):
            raise HTTPException(400, f"camera {cam_id}: counting line missing")
        try:
            x1, y1 = float(line["x1"]), float(line["y1"])
            x2, y2 = float(line["x2"]), float(line["y2"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, f"camera {cam_id}: counting line malformed")
        if not all(0.0 <= v <= 1.0 for v in (x1, y1, x2, y2)):
            raise HTTPException(400, f"camera {cam_id}: line points must be inside the frame")
        if ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 < 0.02:
            raise HTTPException(400, f"camera {cam_id}: counting line is too short")
        try:
            conf = float(cam.get("conf", 0.35))
            every = int(cam.get("detect_every_n", 3))
        except (TypeError, ValueError):
            raise HTTPException(400, f"camera {cam_id}: confidence / detect interval invalid")
        if not 0.05 <= conf <= 0.95:
            raise HTTPException(400, f"camera {cam_id}: confidence must be 0.05-0.95")
        if not 1 <= every <= 30:
            raise HTTPException(400, f"camera {cam_id}: detect interval must be 1-30")
        if cam.get("count_mode", "both") not in ("both", "in_only", "out_only"):
            raise HTTPException(400, f"camera {cam_id}: unknown count mode")


@app.get("/api/history/hourly")
def history_hourly(day: str | None = None, camera_id: int | None = None):
    return database.hourly_summary(camera_id, day or date.today().isoformat())


@app.get("/api/history/daily")
def history_daily(days: int = 7, camera_id: int | None = None):
    return database.daily_summary(camera_id, days)


@app.get("/api/events/recent")
def events_recent(camera_id: int | None = None, limit: int = 20):
    return database.recent_events(camera_id, min(limit, 100))


def _export_range(date_from: str, date_to: str):
    """Parse and order an export range, and read the crossings inside it."""
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400, "dates must be YYYY-MM-DD")
    if d_from > d_to:  # a reversed range silently exports an empty file
        d_from, d_to = d_to, d_from
    return d_from, d_to


def _local_zone_name() -> str:
    """The zone as a person would name it, not as an offset like "+07"."""
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return datetime.now().astimezone().strftime("%Z")


def _report_data(rows: list[tuple], day: str) -> dict:
    """Fold raw crossings into the figures a report shows.

    Everything is derived from the same rows the log lists, so the totals in a
    file can always be traced to the lines beneath them.
    """
    daily: dict[str, dict] = {}
    hourly = {h: {"hour": h, "in": 0, "out": 0} for h in range(24)}
    cameras: dict[int, dict] = {}
    totals = {"in": 0, "out": 0}

    for _id, cam, _track, direction, stamp, _snap in rows:
        key = "in" if direction == "IN" else "out"
        totals[key] += 1
        bucket = daily.setdefault(stamp[:10], {"date": stamp[:10], "in": 0, "out": 0})
        bucket[key] += 1
        cam_row = cameras.setdefault(cam, {"name": f"Camera {cam}", "in": 0, "out": 0})
        cam_row[key] += 1
        if stamp[:10] == day:
            try:
                hourly[int(stamp[11:13])][key] += 1
            except (ValueError, KeyError):
                pass

    return {
        "totals": totals,
        "daily": [daily[k] for k in sorted(daily)],
        "hourly": list(hourly.values()),
        "cameras": [cameras[k] for k in sorted(cameras)],
    }


@app.get("/api/export.csv")
def export_csv(date_from: str, date_to: str, camera_id: int | None = None):
    d_from, d_to = _export_range(date_from, date_to)
    rows = database.export_rows(camera_id, d_from.isoformat(), d_to.isoformat())
    data = _report_data(rows, d_to.isoformat())
    buf = io.StringIO()
    writer = csv.writer(buf)
    # totals first: a file read on its own has to state what it adds up to
    writer.writerow(["People counter - event log"])
    writer.writerow(["Range", d_from.isoformat(), d_to.isoformat()])
    writer.writerow(["Camera", "all" if camera_id is None else camera_id])
    writer.writerow(["Total IN", data["totals"]["in"]])
    writer.writerow(["Total OUT", data["totals"]["out"]])
    writer.writerow(["Net (IN - OUT)", data["totals"]["in"] - data["totals"]["out"]])
    writer.writerow(["Records", len(rows)])
    writer.writerow([])
    writer.writerow(["id", "camera_id", "track_id", "direction", "timestamp", "snapshot"])
    writer.writerows(rows)
    writer.writerow(["TOTAL", "", "", f"IN={data['totals']['in']}",
                     f"OUT={data['totals']['out']}", ""])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=events.csv"},
    )


@app.get("/api/export.xlsx")
def export_xlsx(date_from: str, date_to: str, camera_id: int | None = None):
    """The same range as an Excel workbook, with bar charts drawn from its cells."""
    d_from, d_to = _export_range(date_from, date_to)
    rows = database.export_rows(camera_id, d_from.isoformat(), d_to.isoformat())
    data = _report_data(rows, d_to.isoformat())
    blob = xlsx_report.build_people_report(
        title="People counter report",
        range_label=f"{d_from.isoformat()} to {d_to.isoformat()}",
        timezone=_local_zone_name(),
        totals=data["totals"],
        cameras=data["cameras"],
        hourly=data["hourly"],
        daily=data["daily"],
        events=[
            {"time": stamp.replace("T", " "), "direction": direction, "camera": cam}
            for _id, cam, _track, direction, stamp, _snap in rows
        ],
    )
    name = f"people-counter-{d_from.isoformat()}-to-{d_to.isoformat()}.xlsx"
    return Response(
        blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


# ---------- settings ----------

@app.get("/api/devices")
def get_devices():
    """Inference devices OpenVINO can see (CPU, iGPU, ...). On low-power boxes
    the integrated GPU is usually the faster choice."""
    try:
        import openvino as ov

        core = ov.Core()
        out = []
        for d in core.available_devices:
            try:
                name = core.get_property(d, "FULL_DEVICE_NAME")
            except Exception:  # noqa: BLE001
                name = d
            out.append({"id": d, "name": name})
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"id": "CPU", "name": f"CPU (device query failed: {exc})"}]


@app.get("/api/settings")
def get_settings():
    return load_config()


@app.put("/api/settings")
async def put_settings(cfg: dict):
    if not isinstance(cfg, dict):
        raise HTTPException(400, "invalid config")
    _validate_config(cfg)
    save_config(cfg)
    await asyncio.to_thread(manager.restart)
    await asyncio.to_thread(_start_uploader)
    return {"ok": True}


@app.get("/api/upload/status")
def upload_status():
    return uploader.status() if uploader else {"enabled": False}


# ---------- websocket ----------

@app.websocket("/ws")
async def ws_stats(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            # _all_stats touches SQLite under a lock — never on the event loop,
            # or every stream and HTTP response stalls with it
            await ws.send_json(await asyncio.to_thread(_all_stats))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001 — a bad config entry must not kill the socket
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ---------- static ----------

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOT_DIR)), name="snapshots")
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
