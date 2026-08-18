"""Line-crossing counter with hysteresis — replaces sv.LineZone.

Why not sv.LineZone:
- It only counts inside the exact line segment; people crossing slightly past
  an endpoint are silently missed.
- No jitter guard: a box shrinking under partial occlusion can flip the center
  across the line and back, producing double/phantom counts.
- No per-track cooldown, no minimum track age.

Counting rules:
- Signed perpendicular distance to the line decides the side. The stored side
  only updates when the point is beyond `deadband_px` from the line
  (hysteresis) — jitter inside the band never flips state.
- A count fires when a track's stored side flips, its projection falls within
  the segment extended by `margin_frac` on both ends, and the track is at
  least `min_track_age` updates old.
- The same track cannot count the same direction twice within `cooldown_s`.
"""
import time

import numpy as np
import supervision as sv


try:  # supervision brings scipy in, but the tracker must not depend on that
    from scipy.optimize import linear_sum_assignment as _linear_sum_assignment
except ImportError:  # pragma: no cover - exercised only where scipy is absent
    _linear_sum_assignment = None


def _solve_assignment(cost: np.ndarray, no_match_cost: float) -> list[tuple[int, int]]:
    """Cheapest overall pairing of tracks to detections.

    `cost` is tracks x detections with np.inf where a pair is out of range.
    Leaving a track or a detection unpaired costs `no_match_cost`, so a pairing
    is only made when it beats starting a new identity.
    """
    n_tracks, n_dets = cost.shape
    if n_tracks == 0 or n_dets == 0:
        return []

    size = n_tracks + n_dets
    padded = np.full((size, size), no_match_cost * 2.0, dtype=float)
    finite = np.where(np.isinf(cost), no_match_cost * 2.0, cost)
    padded[:n_tracks, :n_dets] = finite

    if _linear_sum_assignment is not None:
        rows, cols = _linear_sum_assignment(padded)
    else:
        rows, cols = _greedy_assignment(padded)

    pairs = []
    for r, c in zip(rows, cols):
        if r < n_tracks and c < n_dets and np.isfinite(cost[r, c]):
            pairs.append((int(r), int(c)))
    return pairs


def _greedy_assignment(matrix: np.ndarray):
    """Fallback used only when scipy is missing: nearest pair first."""
    order = np.dstack(np.unravel_index(np.argsort(matrix, axis=None), matrix.shape))[0]
    used_rows: set[int] = set()
    used_cols: set[int] = set()
    rows, cols = [], []
    for r, c in order:
        if r in used_rows or c in used_cols:
            continue
        used_rows.add(int(r))
        used_cols.add(int(c))
        rows.append(int(r))
        cols.append(int(c))
    return rows, cols


class MotionTracker:
    """Identity across slow detections, matched by where a person is heading.

    ByteTrack matches a detection to a track by how much the two boxes overlap,
    which is the right thing at video rate, where a walking person barely moves
    between frames. On a two-core box detecting two or three times a second it
    fails outright: one stride is wider than a person, consecutive boxes do not
    touch at all, so nothing matches, every detection starts a fresh tentative
    track, and the tracker hands back nobody. Someone walks across the line in
    plain sight and is never counted -- while a person standing still tracks
    perfectly, because their boxes do overlap. No threshold helps: with zero
    overlap the distance is 1.0 whatever the setting.

    Growing the boxes before matching was tried and cannot work either. Grown
    far enough to bridge a stride, they swallow the person walking alongside.

    So identity is decided by distance and heading instead. A track predicts
    where it will be from its own measured velocity, and takes the nearest
    detection within a gate that is generous enough for a stride but never wide
    enough to reach the next person. New tracks are handed out immediately, not
    after two consecutive matches -- LineCounter's `min_track_age` is what keeps
    a first sighting from counting, and it can do that job only if it is told
    about the sighting.
    """

    def __init__(
        self,
        lost_seconds: float = 3.0,
        activation_conf: float = 0.35,
        base_gate_frac: float = 1.6,
        velocity_smoothing: float = 0.6,
    ):
        self.lost_seconds = float(lost_seconds)
        self.activation_conf = float(activation_conf)
        self.base_gate_frac = float(base_gate_frac)
        self.velocity_smoothing = float(velocity_smoothing)
        self._tracks: dict[int, dict] = {}
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    @property
    def active_tracks(self) -> int:
        return len(self._tracks)

    def _new_track(self, box: np.ndarray, now: float) -> int:
        tid = self._next_id
        self._next_id += 1
        self._tracks[tid] = {
            "box": box.astype(float),
            "centre": np.array(
                [(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0], dtype=float
            ),
            "velocity": np.zeros(2, dtype=float),
            "last_seen": now,
        }
        return tid

    def update(self, detections: sv.Detections, now: float | None = None) -> sv.Detections:
        now = time.monotonic() if now is None else now

        # forget tracks nobody has seen for a while
        for tid in [t for t, s in self._tracks.items()
                    if now - s["last_seen"] > self.lost_seconds]:
            del self._tracks[tid]

        if len(detections) == 0:
            return detections

        boxes = np.asarray(detections.xyxy, dtype=float)
        centres = np.stack(
            [(boxes[:, 0] + boxes[:, 2]) / 2.0, (boxes[:, 1] + boxes[:, 3]) / 2.0], axis=1
        )
        widths = boxes[:, 2] - boxes[:, 0]
        heights = boxes[:, 3] - boxes[:, 1]

        # A gate wide enough for one stride. It is deliberately not narrowed to
        # keep a neighbour out: shrunk to half the gap between two people walking
        # abreast, it no longer spans a stride and every update invented new
        # identities. People are kept apart by the assignment below instead --
        # each track takes at most one detection and each detection one track, so
        # two people yield two identities even when they move in step.
        gates = self.base_gate_frac * np.maximum(widths, heights * 0.5)

        track_ids = list(self._tracks.keys())
        assignment: dict[int, int] = {}   # detection index -> track id
        if track_ids:
            cost = np.full((len(track_ids), len(boxes)), np.inf)
            for ti, tid in enumerate(track_ids):
                state = self._tracks[tid]
                elapsed = max(1e-3, now - state["last_seen"])
                predicted = state["centre"] + state["velocity"] * elapsed
                distances = np.linalg.norm(centres - predicted, axis=1)
                cost[ti] = np.where(distances <= gates, distances, np.inf)

            # Nearest-first pairing is not good enough here. Two people walking
            # in step are each roughly a stride apart from the other's previous
            # position, so the follower's track grabs the leader's detection --
            # it is the closer of the two -- the leader is left to start a new
            # identity, and it happens again on every update. Choosing the
            # pairing with the lowest total cost keeps them in their own lanes.
            for ti, di in _solve_assignment(cost, float(np.max(gates)) if len(gates) else 0.0):
                assignment[di] = track_ids[ti]

        ids = np.zeros(len(boxes), dtype=int)
        confidence = (
            detections.confidence
            if detections.confidence is not None
            else np.ones(len(boxes))
        )
        for di in range(len(boxes)):
            tid = assignment.get(di)
            if tid is None:
                # only a confident detection may introduce a new person; weaker
                # boxes exist to hold known tracks through partial occlusion
                if float(confidence[di]) < self.activation_conf:
                    ids[di] = 0
                    continue
                tid = self._new_track(boxes[di], now)
            else:
                state = self._tracks[tid]
                elapsed = max(1e-3, now - state["last_seen"])
                measured = (centres[di] - state["centre"]) / elapsed
                smoothing = self.velocity_smoothing
                state["velocity"] = (
                    smoothing * state["velocity"] + (1.0 - smoothing) * measured
                )
                state["centre"] = centres[di]
                state["box"] = boxes[di]
                state["last_seen"] = now
            ids[di] = tid

        keep = ids > 0
        tracked = sv.Detections(
            xyxy=boxes[keep],
            confidence=np.asarray(confidence)[keep],
            class_id=(
                detections.class_id[keep] if detections.class_id is not None else None
            ),
        )
        tracked.tracker_id = ids[keep]
        return tracked




class LineCounter:
    def __init__(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        margin_frac: float = 0.15,
        deadband_px: float = 6.0,
        min_track_age: int = 2,
        cooldown_s: float = 2.0,
    ):
        self.start = np.array(start, dtype=float)
        self.end = np.array(end, dtype=float)
        v = self.end - self.start
        self.length = float(np.linalg.norm(v)) or 1.0
        self.u = v / self.length                    # unit vector along the line
        self.normal = np.array([-self.u[1], self.u[0]])  # unit normal = IN side
        self._u = self.u
        self._n = self.normal
        self.margin = margin_frac * self.length
        self.deadband = deadband_px
        self.min_track_age = min_track_age
        self.cooldown_s = cooldown_s
        self._state: dict[int, dict] = {}

    def update(self, detections: sv.Detections) -> tuple[int, int, list[int], list[int]]:
        """Feed one tracker update. Returns (n_in, n_out, ids_in, ids_out)."""
        now = time.monotonic()
        ids_in: list[int] = []
        ids_out: list[int] = []

        if detections.tracker_id is not None:
            for xyxy, tid in zip(detections.xyxy, detections.tracker_id):
                tid = int(tid)
                cx = (xyxy[0] + xyxy[2]) / 2.0
                cy = (xyxy[1] + xyxy[3]) / 2.0
                rel = np.array([cx, cy]) - self.start
                dist = float(rel @ self._n)   # signed perpendicular distance
                proj = float(rel @ self._u)   # position along the line

                st = self._state.setdefault(
                    tid, {"sign": 0, "age": 0, "last_count": {}}
                )
                st["age"] += 1
                st["last_seen"] = now

                if abs(dist) < self.deadband:
                    continue  # inside the hysteresis band — keep previous side

                sign = 1 if dist > 0 else -1
                prev = st["sign"]
                st["sign"] = sign
                if prev == 0 or sign == prev:
                    continue

                # side flipped — validate the crossing
                if st["age"] < self.min_track_age:
                    continue
                if not (-self.margin <= proj <= self.length + self.margin):
                    continue
                direction = "in" if sign > 0 else "out"
                last_t = st["last_count"].get(direction)
                if last_t is not None and now - last_t < self.cooldown_s:
                    continue
                st["last_count"][direction] = now
                (ids_in if sign > 0 else ids_out).append(tid)

        stale = [k for k, s in self._state.items() if now - s.get("last_seen", now) > 60]
        for k in stale:
            del self._state[k]

        return len(ids_in), len(ids_out), ids_in, ids_out
