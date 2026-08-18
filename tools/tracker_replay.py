"""Replay a camera at the rate the station really manages, and see what counts.

The station detects a couple of times a second, not fifteen. This runs the real
detector, tracker and line counter at a chosen rate against a live camera or a
recorded clip, and reports what matters when the rate is low:

  * how many identities were handed out for the people that walked past
  * how many times a track changed sides of the line
  * how many of those side changes were actually counted

A side change that is not counted is a missed person, and the usual cause is the
tracker losing the identity between two updates.

    python tools/tracker_replay.py --source rtsp://... --rate 2.5 --seconds 60
    python tools/tracker_replay.py --source clip.mp4 --rate 2.5 --frame-rate-hint 12
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from detector import PersonDetector  # noqa: E402
from tracker import LineCounter  # noqa: E402

DETECT_CONF_FLOOR = 0.1


def build_tracker(rate: float, conf: float, lost_seconds: float, tuned_for: float | None):
    """`tuned_for` is what the tracker is told; None means tell it the truth."""
    told = float(tuned_for if tuned_for else rate)
    return sv.ByteTrack(
        track_activation_threshold=conf,
        minimum_matching_threshold=0.9 if told < 6.0 else 0.8,
        lost_track_buffer=max(1, int(30 * lost_seconds)),
        frame_rate=max(1, round(told)),
    )


def side_of(counter: LineCounter, box) -> int:
    """Which side of the line a box sits on: -1, 0 or 1.

    Uses the box centre and the counter's own deadband, the same anchor the
    counter itself measures from, so the two agree on what a side change is.
    """
    x1, y1, x2, y2 = box
    point = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0], dtype=float)
    distance = float((point - counter.start) @ counter.normal)
    if abs(distance) < counter.deadband:
        return 0
    return 1 if distance > 0 else -1


def replay(args) -> dict:
    cap = cv2.VideoCapture(args.source, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.source}")
    ok, frame = cap.read()
    if not ok or frame is None:
        raise SystemExit("opened but no frames")
    h, w = frame.shape[:2]

    line = json.loads(args.line) if args.line else {"x1": 0.5, "y1": 0.05, "x2": 0.5, "y2": 0.95}
    counter = LineCounter(
        start=(line["x1"] * w, line["y1"] * h),
        end=(line["x2"] * w, line["y2"] * h),
        margin_frac=args.margin,
        deadband_px=max(4.0, args.deadband * (w + h)),
        min_track_age=args.min_age,
        cooldown_s=args.cooldown,
    )
    detector = PersonDetector(imgsz=args.imgsz, threads=args.threads, device=args.device)

    interval = 1.0 / args.rate
    tracker = build_tracker(args.rate, args.conf, args.lost_seconds, args.tuned_for)

    ids_seen: set[int] = set()
    last_side: dict[int, int] = {}
    side_changes = 0
    counted_in = counted_out = 0
    updates = 0
    detections_total = 0
    started = time.monotonic()
    next_detect = started

    while time.monotonic() - started < args.seconds:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        now = time.monotonic()
        if now < next_detect:
            continue
        next_detect = now + interval

        detections = detector.detect(frame, conf=DETECT_CONF_FLOOR)
        detections = tracker.update_with_detections(detections)
        updates += 1
        detections_total += len(detections)

        n_in, n_out, _, _ = counter.update(detections)
        counted_in += n_in
        counted_out += n_out

        if detections.tracker_id is not None:
            for box, tid in zip(detections.xyxy, detections.tracker_id):
                tid = int(tid)
                ids_seen.add(tid)
                side = side_of(counter, box)
                if side == 0:
                    continue
                previous = last_side.get(tid)
                if previous is not None and previous != side:
                    side_changes += 1
                last_side[tid] = side

    cap.release()
    elapsed = time.monotonic() - started
    return {
        "told_rate": args.tuned_for or args.rate,
        "real_rate": round(updates / elapsed, 2) if elapsed else 0,
        "updates": updates,
        "avg_people_per_update": round(detections_total / updates, 2) if updates else 0,
        "track_ids_handed_out": len(ids_seen),
        "side_changes": side_changes,
        "counted_in": counted_in,
        "counted_out": counted_out,
        "counted_total": counted_in + counted_out,
        "missed_side_changes": max(0, side_changes - (counted_in + counted_out)),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="rtsp url or video file")
    p.add_argument("--rate", type=float, default=2.5, help="detections per second to emulate")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--tuned-for", type=float, default=None,
                   help="rate to LIE to the tracker about (reproduces the old bug)")
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--device", default="CPU")
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--lost-seconds", type=float, default=3.0)
    p.add_argument("--margin", type=float, default=0.15)
    p.add_argument("--deadband", type=float, default=0.008)
    p.add_argument("--min-age", type=int, default=2)
    p.add_argument("--cooldown", type=float, default=2.0)
    p.add_argument("--line", default=None, help='json, e.g. {"x1":0.5,"y1":0,"x2":0.5,"y2":1}')
    args = p.parse_args()

    result = replay(args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
