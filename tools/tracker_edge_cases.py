"""The awkward cases the counting has to survive.

tracker_rate_test.py covers the headline problem -- people walking past while
detection is slow. This covers what happens around it: someone standing still,
someone hidden for a moment, two people passing in opposite directions, a weak
detection that should not invent a person, and the counter's own guards.

    python tools/tracker_edge_cases.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import supervision as sv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from tracker import LineCounter, MotionTracker  # noqa: E402

W, H = 640, 360
BOX_W, BOX_H = 70, 170
FAILURES: list[str] = []


def box(cx: float, cy: float = H * 0.55) -> list[float]:
    return [cx - BOX_W / 2, cy - BOX_H / 2, cx + BOX_W / 2, cy + BOX_H / 2]


def detections(boxes: list[list[float]], conf: float | list[float] = 0.85) -> sv.Detections:
    if not boxes:
        return sv.Detections.empty()
    confs = np.full(len(boxes), conf) if isinstance(conf, float) else np.array(conf)
    return sv.Detections(
        xyxy=np.array(boxes, dtype=float),
        confidence=confs,
        class_id=np.zeros(len(boxes), dtype=int),
    )


def counter() -> LineCounter:
    return LineCounter(
        start=(W * 0.5, 0.0), end=(W * 0.5, float(H)),
        margin_frac=0.15, deadband_px=8.0, min_track_age=2, cooldown_s=2.0,
    )


def check(name: str, got, expected) -> None:
    ok = got == expected
    if not ok:
        FAILURES.append(name)
    print("  %-58s got %-14s %s" % (name, got, "ok" if ok else f"FAIL (want {expected})"))


def ids_of(tracked: sv.Detections) -> list[int]:
    if tracked.tracker_id is None:
        return []
    return [int(t) for t in tracked.tracker_id]


print("empty input")
t = MotionTracker()
out = t.update(detections([]), now=0.0)
check("no detections -> nothing returned, no crash", len(out), 0)
check("no detections -> no tracks invented", t.active_tracks, 0)

print()
print("someone standing still")
t = MotionTracker()
seen = set()
for i in range(10):
    seen.update(ids_of(t.update(detections([box(200)]), now=i * 0.4)))
check("one person, one identity", len(seen), 1)

print()
print("someone hidden for a moment (a pillar, a passing trolley)")
t = MotionTracker()
seen = set()
positions = [100, 150, 200, None, None, 350, 400]
for i, x in enumerate(positions):
    frame = detections([box(x)]) if x is not None else detections([])
    seen.update(ids_of(t.update(frame, now=i * 0.4)))
check("identity survives two missed updates", len(seen), 1)

print()
print("a track that has been gone longer than lost_seconds")
t = MotionTracker(lost_seconds=3.0)
seen = set()
seen.update(ids_of(t.update(detections([box(100)]), now=0.0)))
seen.update(ids_of(t.update(detections([box(105)]), now=0.4)))
seen.update(ids_of(t.update(detections([box(110)]), now=9.0)))   # 8.6s later
check("forgotten after the timeout, new identity", len(seen), 2)

print()
print("a weak detection must not invent a person")
t = MotionTracker(activation_conf=0.35)
out = t.update(detections([box(200)], conf=0.15), now=0.0)
check("0.15 confidence starts no track", len(out), 0)
out = t.update(detections([box(200)], conf=0.85), now=0.4)
check("0.85 confidence does", len(out), 1)
out = t.update(detections([box(215)], conf=0.15), now=0.8)
check("...and then holds it through a weak frame", len(out), 1)

print()
print("two people passing in opposite directions")
t = MotionTracker()
c = counter()
n_in = n_out = 0
seen = set()
left, right = 60.0, 580.0
for i in range(12):
    frame = detections([box(left), box(right)])
    tracked = t.update(frame, now=i * 0.4)
    seen.update(ids_of(tracked))
    a, b, _, _ = c.update(tracked)
    n_in += a
    n_out += b
    left += 48
    right -= 48
check("two identities", len(seen), 2)
check("one crossing each way", (n_in, n_out), (1, 1))

print()
print("counter guards")
t = MotionTracker()
c = counter()
first = t.update(detections([box(300)]), now=0.0)
n_in, n_out, _, _ = c.update(first)
check("a first sighting never counts (min_track_age)", (n_in, n_out), (0, 0))

c = counter()
t = MotionTracker()
jitter = [318, 322, 318, 322, 318]   # wobbling on the line, inside the deadband
counted = 0
for i, x in enumerate(jitter):
    tracked = t.update(detections([box(x)]), now=i * 0.4)
    a, b, _, _ = c.update(tracked)
    counted += a + b
check("jitter across the line counts nothing (deadband)", counted, 0)

print()
print("crossing outside the line's ends is ignored")
c = LineCounter(
    start=(W * 0.5, H * 0.40), end=(W * 0.5, H * 0.60),   # short line, middle only
    margin_frac=0.15, deadband_px=8.0, min_track_age=2, cooldown_s=2.0,
)
t = MotionTracker()
counted = 0
for i, x in enumerate([200, 260, 320, 380, 440]):
    tracked = t.update(detections([box(x, cy=H * 0.10)]), now=i * 0.4)  # far above it
    a, b, _, _ = c.update(tracked)
    counted += a + b
check("someone crossing well past the end is not counted", counted, 0)

print()
print("reset clears everything (used at midnight)")
t = MotionTracker()
t.update(detections([box(100)]), now=0.0)
t.reset()
check("no tracks left after reset", t.active_tracks, 0)
after = t.update(detections([box(100)]), now=0.4)
check("numbering starts again", ids_of(after), [1])

print()
print("FAILURES:", len(FAILURES), FAILURES if FAILURES else "")
sys.exit(1 if FAILURES else 0)
