"""Prove people are still counted when detection is slow.

No camera needed. A walk is simulated as boxes crossing the line at a walking
pace, and those boxes go through the real association step and the real
LineCounter at the rate the station actually manages.

What is being tested is association, not detection. ByteTrack matches by how
much two boxes overlap, and at two or three detections a second a walking person
does not overlap their own previous box at all -- one stride is wider than they
are. Nothing matches, the tracker returns nobody, and a person crosses the line
in plain sight without being counted, while somebody standing still tracks
perfectly. backend/tracker.py:MotionTracker is what fixes it.

    python tools/tracker_rate_test.py

One walker must mean one identity and one count. Two walking side by side must
stay two identities and two counts: matching people by distance alone lets the
follower steal the leader's detection, so the pairing has to be chosen as a
whole.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import supervision as sv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from tracker import LineCounter, MotionTracker  # noqa: E402

FRAME_W, FRAME_H = 640, 360
BOX_W, BOX_H = 70, 170          # a person a couple of metres from the lens
WALK_PX_PER_S = 190.0           # ~1.4 m/s across a 640 px wide view


def build_tracker(rate: float, conf: float = 0.35, lost_seconds: float = 3.0):
    return sv.ByteTrack(
        track_activation_threshold=conf,
        minimum_matching_threshold=0.9 if rate < 6.0 else 0.8,
        lost_track_buffer=max(1, int(30 * lost_seconds)),
        frame_rate=max(1, round(rate)),
    )


def new_counter() -> LineCounter:
    return LineCounter(
        start=(FRAME_W * 0.5, 0.0),
        end=(FRAME_W * 0.5, float(FRAME_H)),
        margin_frac=0.15,
        deadband_px=max(4.0, 0.008 * (FRAME_W + FRAME_H)),
        min_track_age=2,
        cooldown_s=2.0,
    )


def walk(rate: float, walkers: int, gap_px: float = 0.0) -> list[np.ndarray]:
    """`walkers` people crossing left to right, sampled at `rate` per second."""
    step = WALK_PX_PER_S / rate
    frames = []
    lead = -BOX_W / 2.0
    while lead < FRAME_W + BOX_W + gap_px * walkers:
        boxes = []
        for i in range(walkers):
            x = lead - i * gap_px
            cy = FRAME_H * 0.55
            boxes.append([x - BOX_W / 2, cy - BOX_H / 2, x + BOX_W / 2, cy + BOX_H / 2])
        frames.append(np.array(boxes, dtype=float))
        lead += step
    return frames


def run(rate: float, walkers: int = 1, gap_px: float = 0.0, use_fix: bool = True) -> dict:
    tracker = MotionTracker(lost_seconds=3.0) if use_fix else build_tracker(rate)
    counter = new_counter()
    ids: set[int] = set()
    counted = 0
    clock = [0.0]   # simulated wall clock: the tracker measures speed from it

    for boxes in walk(rate, walkers, gap_px):
        detections = sv.Detections(
            xyxy=boxes,
            confidence=np.full(len(boxes), 0.85),
            class_id=np.zeros(len(boxes), dtype=int),
        )
        clock[0] += 1.0 / rate
        tracked = (
            tracker.update(detections, now=clock[0])
            if use_fix
            else tracker.update_with_detections(detections)
        )
        if tracked.tracker_id is not None:
            ids.update(int(t) for t in tracked.tracker_id)
        n_in, n_out, _, _ = counter.update(tracked)
        counted += n_in + n_out

    return {"ids": len(ids), "counted": counted}


def main() -> None:
    failures = 0

    print("one walker, one crossing -> 1 identity, 1 count")
    print(f"{'rate/s':>7} {'px/step':>8} {'without fix':>22} {'with fix':>18}")
    print("-" * 60)
    for rate in (2.0, 3.0, 4.0, 5.0, 8.0, 12.0):
        before = run(rate, use_fix=False)
        after = run(rate, use_fix=True)
        good = after["ids"] == 1 and after["counted"] == 1
        failures += 0 if good else 1
        print("%7.1f %8.1f   ids=%d counted=%d%s   ids=%d counted=%d  %s" % (
            rate, WALK_PX_PER_S / rate,
            before["ids"], before["counted"],
            "" if before["counted"] else " (missed)",
            after["ids"], after["counted"],
            "ok" if good else "FAIL"))

    print()
    print("two walking abreast must not be merged -> 2 identities, 2 counts")
    print(f"{'rate/s':>7} {'gap px':>7} {'ids':>5} {'counted':>8}")
    print("-" * 34)
    for rate in (2.0, 3.0, 5.0):
        for gap in (90.0, 130.0):
            r = run(rate, walkers=2, gap_px=gap)
            good = r["ids"] == 2 and r["counted"] == 2
            failures += 0 if good else 1
            print("%7.1f %7.0f %5d %8d  %s" % (
                rate, gap, r["ids"], r["counted"], "ok" if good else "FAIL"))

    print()
    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
