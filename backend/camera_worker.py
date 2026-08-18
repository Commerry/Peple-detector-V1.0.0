"""Per-camera pipeline thread: RTSP read -> detect (frame skip) -> track -> count -> annotate.

- Auto-reconnects when the stream drops.
- Detection runs every N frames; MotionTracker carries identities between them.
- Counting uses a virtual line (sv.LineZone) with CENTER anchor.
- JPEG annotation/encoding is skipped when no browser is viewing (saves CPU).
- Counters reset at midnight; history stays in SQLite.
"""
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

import database
from config import SNAPSHOT_DIR
from detector import PersonDetector
from tracker import LineCounter, MotionTracker

LINE_COLOR = (80, 200, 120)
BOX_COLOR = (255, 170, 0)
IN_COLOR = (246, 130, 59)    # BGR — blue, matches the dashboards
OUT_COLOR = (22, 115, 249)   # BGR — orange

# Detection floor fed to the tracker. Low-confidence boxes keep existing tracks
# alive through partial occlusion; the per-camera `conf` setting acts as the
# activation threshold for NEW tracks instead of discarding boxes outright.
DETECT_CONF_FLOOR = 0.1
MIN_LINE_FRACTION = 0.02    # counting line shorter than this counts nothing


class FrameGrabber(threading.Thread):
    """Reads the stream continuously and keeps only the newest frame.

    Decoding is much cheaper than detection, so without this the decoder queue
    grows until the "live" picture lags minutes behind reality. Dropping frames
    inside the detection loop instead (the earlier approach) throttled the
    detection rate itself: on a slow CPU it skipped so many frames that only
    ~1.3 detections per second survived.
    """

    def __init__(self, worker: "CameraWorker"):
        super().__init__(daemon=True, name=f"grab-{worker.cam_id}")
        self.w = worker
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self.stream_fps = 0.0
        self.online = False
        # How many frames per second actually need decoding. grab() just pulls
        # the packet off the socket and is nearly free; retrieve() does the
        # H.264 decode and costs ~20 ms per frame on a Celeron — with two
        # cameras that is the single biggest CPU item, bigger than detection.
        # Decoding only the frames the pipeline consumes roughly halves it.
        self.decode_fps = 10.0

    def latest(self, last_seq: int, timeout: float = 2.0):
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            return self._frame, self._seq

    def run(self) -> None:
        w = self.w
        cap = None
        fail = 0
        reconnects = 0
        t0 = time.monotonic()
        frames = 0
        last_decode = 0.0
        is_file = w.is_file
        file_fps = 25.0

        while not w._stop_event.is_set():
            if cap is None:
                cap = w._open_capture()
                if cap is None:
                    self.online = False
                    self.stream_fps = 0.0
                    reconnects += 1
                    if w._stop_event.wait(min(30.0, 2.0 ** min(reconnects, 5))):
                        break
                    continue
                if is_file:
                    file_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                fail = 0
                t0 = time.monotonic()
                frames = 0

            # grab() pulls the next packet without decoding it — this is what
            # keeps the pipeline at the live edge for almost no CPU.
            if not cap.grab():
                fail += 1
                if is_file and fail == 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop test videos
                    continue
                if fail > 25:
                    cap.release()
                    cap = None
                    self.online = False
                    self.stream_fps = 0.0
                    continue
                if w._stop_event.wait(0.05):
                    break
                continue

            fail = 0
            reconnects = 0
            self.online = True
            frames += 1

            now = time.monotonic()
            if now - last_decode >= 1.0 / max(1.0, self.decode_fps):
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    last_decode = now
                    with self._cond:
                        self._frame = frame
                        self._seq += 1
                        self._cond.notify_all()
            dt = time.monotonic() - t0
            if dt >= 2.0:
                self.stream_fps = frames / dt
                t0 = time.monotonic()
                frames = 0

            if is_file:
                time.sleep(1.0 / file_fps)

        if cap is not None:
            cap.release()
        self.online = False
        self.stream_fps = 0.0


class CameraWorker(threading.Thread):
    def __init__(
        self,
        cam_cfg: dict,
        model_cfg: dict,
        snap_cfg: dict,
        jpeg_quality: int,
        tracking_cfg: dict | None = None,
        counting_cfg: dict | None = None,
    ):
        super().__init__(daemon=True, name=f"camera-{cam_cfg['id']}")
        self.cfg = cam_cfg
        self.cam_id = cam_cfg["id"]
        self.model_cfg = model_cfg
        self.snap_cfg = snap_cfg
        self.jpeg_quality = int(jpeg_quality)
        # Longest edge of a streamed preview frame. 0 sends full resolution.
        self.preview_max_width = max(0, int(model_cfg.get("preview_max_width", 960)))

        self._stop_event = threading.Event()
        self._jpeg_cond = threading.Condition()
        self._latest_jpeg: bytes | None = None
        self._jpeg_seq = 0
        self._jpeg_at = 0.0  # monotonic time of the last published frame
        self._viewers = 0
        self._viewers_lock = threading.Lock()
        self._last_publish = 0.0  # caps stream encode at ~12 fps

        self.online = False
        self.fps = 0.0
        self.error: str | None = None
        self.count_in = 0
        self.count_out = 0
        self._day = date.today()
        self._detect_t0 = 0.0  # start of the current detection (for backlog check)
        self._detect_ms = 0.0  # rolling detection cost

        restored = database.today_counts(self.cam_id)
        self.count_in = restored["IN"]
        self.count_out = restored["OUT"]

        self.tracking_cfg = tracking_cfg or {}
        self.counting_cfg = counting_cfg or {}
        self._detector: PersonDetector | None = None
        self._tracker: MotionTracker | None = None
        self._line_counter: LineCounter | None = None
        self._frame_size: tuple[int, int] | None = None  # (w, h)
        self._crowded = False  # people close together -> detect every frame
        self._grabber: "FrameGrabber | None" = None
        source = self.cfg.get("source") or ""
        self.is_file = bool(source) and isinstance(source, str) and Path(source).is_file()

    # ---------- viewer accounting (MJPEG clients) ----------

    def add_viewer(self) -> None:
        with self._viewers_lock:
            self._viewers += 1

    def remove_viewer(self) -> None:
        with self._viewers_lock:
            self._viewers = max(0, self._viewers - 1)

    @property
    def has_viewers(self) -> bool:
        with self._viewers_lock:
            return self._viewers > 0

    def wait_jpeg(self, last_seq: int, timeout: float = 2.0) -> tuple[bytes | None, int]:
        """Block until a JPEG newer than last_seq exists (or timeout)."""
        deadline = time.monotonic() + timeout
        with self._jpeg_cond:
            while self._latest_jpeg is None or self._jpeg_seq == last_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._jpeg_cond.wait(remaining):
                    break
            return self._latest_jpeg, self._jpeg_seq

    def current_jpeg(self) -> bytes | None:
        with self._jpeg_cond:
            return self._latest_jpeg

    def _publish_jpeg(self, frame: np.ndarray) -> None:
        # The wall displays show the picture in a box about a thousand pixels
        # wide, so sending full camera resolution buys nothing and costs a lot:
        # every frame is encoded here and decoded again in the browser, and both
        # ends share two cores with the detector. Shrinking the preview took
        # detection from roughly 1 to 4 per second per camera with the picture
        # still on screen.
        limit = self.preview_max_width
        if limit and frame.shape[1] > limit:
            scale = limit / float(frame.shape[1])
            frame = cv2.resize(
                frame,
                (limit, max(1, int(round(frame.shape[0] * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if ok:
            with self._jpeg_cond:
                self._latest_jpeg = buf.tobytes()
                self._jpeg_seq += 1
                self._jpeg_at = time.monotonic()
                self._jpeg_cond.notify_all()

    # ---------- stats ----------

    def stats(self) -> dict:
        inside = max(0, self.count_in - self.count_out)
        return {
            "camera_id": self.cam_id,
            "name": self.cfg.get("name", f"Camera {self.cam_id}"),
            "online": self.online and self.is_alive(),
            "fps": round(self.fps, 1),
            "in": self.count_in,
            "out": self.count_out,
            "inside": inside,
            "error": self.error,
        }

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    @property
    def frame_age(self) -> float:
        """Seconds since the last published JPEG (inf if none yet)."""
        with self._jpeg_cond:
            return float("inf") if self._jpeg_at == 0.0 else time.monotonic() - self._jpeg_at

    def stop(self) -> None:
        self._stop_event.set()
        with self._jpeg_cond:
            self._jpeg_cond.notify_all()  # release streaming clients immediately
        g = self._grabber
        if g is not None:
            with g._cond:
                g._cond.notify_all()

    # ---------- pipeline ----------

    def _open_capture(self) -> cv2.VideoCapture | None:
        src = self.cfg["source"]
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG if isinstance(src, str) else cv2.CAP_ANY)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _setup_line(self, w: int, h: int) -> None:
        ln = self.cfg.get("line") or {}
        try:
            x1, y1 = float(ln["x1"]), float(ln["y1"])
            x2, y2 = float(ln["x2"]), float(ln["y2"])
        except (KeyError, TypeError, ValueError):
            x1, y1, x2, y2 = 0.1, 0.5, 0.9, 0.5
        # A zero/near-zero length line silently counts nothing forever: its
        # normal is undefined so every distance reads as 0. Fall back instead.
        if ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 < MIN_LINE_FRACTION:
            self.error = "counting line too short — using default"
            x1, y1, x2, y2 = 0.1, 0.5, 0.9, 0.5
        ln = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        cc = self.counting_cfg
        deadband_frac = float(cc.get("deadband_frac", 0.008))
        self._line_counter = LineCounter(
            start=(ln["x1"] * w, ln["y1"] * h),
            end=(ln["x2"] * w, ln["y2"] * h),
            margin_frac=float(cc.get("margin_frac", 0.15)),
            deadband_px=max(4.0, deadband_frac * (w + h)),
            min_track_age=int(cc.get("min_track_age", 2)),
            cooldown_s=float(cc.get("cooldown_s", 2.0)),
        )
        self._frame_size = (w, h)

    def _check_daily_reset(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self.count_in = 0
            self.count_out = 0
            if self._line_counter and self._frame_size:
                self._setup_line(*self._frame_size)  # fresh crossing state
            if self._tracker is not None:
                self._tracker.reset()

    def _save_snapshot(self, frame: np.ndarray, direction: str) -> str | None:
        if not self.snap_cfg.get("enabled", True):
            return None
        day_dir = SNAPSHOT_DIR / date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        name = f"cam{self.cam_id}_{datetime.now().strftime('%H%M%S_%f')}_{direction}.jpg"
        path = day_dir / name
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return f"{date.today().isoformat()}/{name}"

    @staticmethod
    def _is_crowded(detections: sv.Detections) -> bool:
        """True when any two tracked boxes (padded 15%) overlap — people close
        together or passing each other. Used to boost the detection rate."""
        boxes = detections.xyxy
        if boxes is None or len(boxes) < 2:
            return False
        n = len(boxes)
        for i in range(n):
            x1a, y1a, x2a, y2a = boxes[i]
            pw, ph = (x2a - x1a) * 0.15, (y2a - y1a) * 0.15
            x1a, y1a, x2a, y2a = x1a - pw, y1a - ph, x2a + pw, y2a + ph
            for j in range(i + 1, n):
                x1b, y1b, x2b, y2b = boxes[j]
                if x1a < x2b and x2a > x1b and y1a < y2b and y2a > y1b:
                    return True
        return False

    def _annotate(
        self, frame: np.ndarray, detections: sv.Detections, with_label: bool = False
    ) -> np.ndarray:
        lc = self._line_counter
        if lc is not None:
            cv2.line(
                frame,
                (int(lc.start[0]), int(lc.start[1])),
                (int(lc.end[0]), int(lc.end[1])),
                LINE_COLOR,
                2,
            )
            # Arrows at the midpoint for both directions. The one this camera
            # does not count (per count_mode) is drawn dim, so the operator can
            # see at a glance which way is measured here.
            mid = (lc.start + lc.end) / 2
            direction = -1.0 if self.cfg.get("invert_direction") else 1.0
            mode = self.cfg.get("count_mode", "both")
            for label, sign, bright, active in (
                ("IN", 1.0, IN_COLOR, mode != "out_only"),
                ("OUT", -1.0, OUT_COLOR, mode != "in_only"),
            ):
                colour = bright if active else tuple(int(v * 0.35) for v in bright)
                tip = mid + lc.normal * 34 * direction * sign
                cv2.arrowedLine(
                    frame,
                    (int(mid[0]), int(mid[1])),
                    (int(tip[0]), int(tip[1])),
                    colour, 2, tipLength=0.35,
                )
                cv2.putText(
                    frame, label, (int(tip[0]) + 6, int(tip[1]) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2, cv2.LINE_AA,
                )
        if detections.tracker_id is not None:
            for xyxy, tid in zip(detections.xyxy, detections.tracker_id):
                x1, y1, x2, y2 = xyxy.astype(int)
                cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, 2)
                cv2.putText(
                    frame, f"#{tid}", (x1, max(0, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_COLOR, 1, cv2.LINE_AA,
                )
        if with_label:  # burned-in counts only on snapshot evidence, not the live stream
            label = f"IN {self.count_in}  OUT {self.count_out}"
            cv2.putText(
                frame, label, (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA,
            )
        return frame

    def _init_tracker(self, conf: float) -> None:
        """Build the tracker that carries identities between detections.

        MotionTracker works from wall-clock time and each person's own measured
        speed, so it does not care whether this machine manages two detections a
        second or fifteen. That matters here: the rate on this box swings with
        the picture being watched, and the tracker it replaced was told a rate
        up front and quietly stopped matching anyone who walked.
        """
        self._tracker = MotionTracker(
            lost_seconds=float(self.tracking_cfg.get("lost_seconds", 3.0)),
            activation_conf=conf,
        )

    def run(self) -> None:
        """Thread entry point. Never let an exception escape: a dead worker
        would keep reporting `online` forever with no way to recover short of
        restarting the app."""
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.online = False
            self.fps = 0.0

    def _run(self) -> None:
        self._detector = PersonDetector(
            imgsz=int(self.model_cfg.get("imgsz", 416)),
            threads=self.model_cfg.get("threads_per_camera"),
            device=self.model_cfg.get("device", "CPU"),
        )
        try:
            detect_every = max(1, int(self.cfg.get("detect_every_n", 3)))
        except (TypeError, ValueError):
            detect_every = 3
        try:
            conf = min(0.95, max(0.05, float(self.cfg.get("conf", 0.35))))
        except (TypeError, ValueError):
            conf = 0.35
        invert = bool(self.cfg.get("invert_direction", False))
        # Drawing + JPEG encoding for viewers competes with detection on a small
        # CPU; capping the preview rate keeps counting accurate when someone is
        # watching. Only affects the picture, never the counts.
        try:
            preview_fps = max(1.0, min(25.0, float(self.model_cfg.get("preview_fps", 10))))
        except (TypeError, ValueError):
            preview_fps = 10.0
        publish_interval = 1.0 / preview_fps

        last_detections = sv.Detections.empty()
        fps_t0 = time.monotonic()
        fps_frames = 0
        seq = 0
        last_detect = 0.0

        grabber = FrameGrabber(self)
        grabber.start()
        self._grabber = grabber

        while not self._stop_event.is_set():
            frame, seq = grabber.latest(seq, timeout=2.0)
            self.online = grabber.online
            if frame is None or not grabber.online:
                self.fps = 0.0
                continue
            self.error = None
            self._check_daily_reset()

            h, w = frame.shape[:2]
            if self._frame_size != (w, h):
                self._setup_line(w, h)

            # Target detection rate: one detection per `detect_every_n` frames
            # of the real stream. It is a rate, not a frame counter, so a slow
            # machine simply detects less often on the freshest frame instead
            # of falling behind live.
            stream_fps = grabber.stream_fps or 15.0
            step = 1 if self._crowded else detect_every
            min_interval = step / max(1.0, stream_fps)

            # Ask the grabber to decode only what is actually consumed:
            # the detection rate, plus the preview rate while someone watches.
            need = 1.0 / min_interval
            if self.has_viewers:
                need = max(need, preview_fps)
            grabber.decode_fps = min(stream_fps, need * 1.3 + 1.0)

            now = time.monotonic()
            if self._tracker is None:
                self._init_tracker(conf)

            if self._tracker is not None and now - last_detect >= min_interval:
                last_detect = now
                # Low floor: 0.1..conf boxes keep existing tracks locked through
                # partial occlusion; only boxes above `conf` start a new track.
                t_detect = time.monotonic()
                detections = self._detector.detect(frame, conf=DETECT_CONF_FLOOR)
                detections = self._tracker.update(detections)
                self._detect_ms = 0.7 * self._detect_ms + 0.3 * (
                    (time.monotonic() - t_detect) * 1000.0
                )
                last_detections = detections
                self._crowded = self._is_crowded(detections)
                n_in, n_out, ids_in, ids_out = self._line_counter.update(detections)
                if invert:
                    n_in, n_out, ids_in, ids_out = n_out, n_in, ids_out, ids_in
                # One-way gates: ignore crossings in the direction that can't
                # physically happen — kills phantom counts entirely.
                mode = self.cfg.get("count_mode", "both")
                if mode == "in_only":
                    n_out, ids_out = 0, []
                elif mode == "out_only":
                    n_in, ids_in = 0, []
                if n_in or n_out:
                    annotated = self._annotate(frame.copy(), detections, with_label=True)
                    for direction, ids in (("IN", ids_in), ("OUT", ids_out)):
                        for tid in ids:
                            snap = self._save_snapshot(annotated, direction)
                            database.insert_event(self.cam_id, tid, direction, snap)
                    self.count_in += n_in
                    self.count_out += n_out

                fps_frames += 1
                if now - fps_t0 >= 2.0:
                    self.fps = fps_frames / (now - fps_t0)
                    fps_t0 = now
                    fps_frames = 0

            if self.has_viewers:
                now = time.monotonic()
                if now - self._last_publish >= publish_interval:
                    self._last_publish = now
                    self._publish_jpeg(self._annotate(frame.copy(), last_detections))

        grabber.join(timeout=5)
        self.online = False
        self.fps = 0.0
