"""YOLO11n person detection on OpenVINO (Intel-optimized CPU inference).

Inference goes straight through the OpenVINO runtime instead of the
ultralytics wrapper: measured on an i5-10300H, the wrapper costs ~170-225 ms
per frame against ~26 ms for the same model called directly (the wrapper
re-runs its own preprocessing and torch-based NMS every call). Ultralytics is
still used, lazily, for the one-time IR export.

Each CameraWorker owns its own PersonDetector instance (no cross-thread locking).
"""
import os
import threading
from pathlib import Path

import cv2
import numpy as np
import openvino as ov
import supervision as sv

from config import MODELS_DIR

_export_lock = threading.Lock()
PERSON_CLASS = 0  # COCO

# One OpenVINO Core and one compiled model per (device, size), shared by every
# camera. Compiling the model separately per camera creates a second GPU
# context; on a small iGPU (16 EU Jasper Lake) the second context exhausts
# device memory and the OpenCL runtime aborts the whole process with
# CL_OUT_OF_RESOURCES. Sharing the CompiledModel and giving each worker only
# its own InferRequest is the supported multi-stream pattern.
_cache_lock = threading.Lock()
_core: "ov.Core | None" = None
_compiled: dict[tuple[str, int], object] = {}

# Small integrated GPUs also drive the desktop. Two camera threads submitting
# inference concurrently made the i915 driver reset the GPU ("Resetting rcs0
# for preemption time out"), which stalls detection and can kill the process.
# Serialising submissions costs nothing here — two cameras need ~10 inferences
# per second and one takes ~20 ms, so the GPU is idle ~80% of the time.
_gpu_lock = threading.Lock()


def _get_core() -> "ov.Core":
    global _core
    if _core is None:
        _core = ov.Core()
        try:
            cache = MODELS_DIR / "ov_cache"
            cache.mkdir(parents=True, exist_ok=True)
            _core.set_property({"CACHE_DIR": str(cache)})
        except Exception:  # noqa: BLE001
            pass
    return _core


def model_dir(imgsz: int) -> Path:
    return MODELS_DIR / f"yolo11n_openvino_{imgsz}"


def ensure_model(imgsz: int = 416) -> Path:
    """Export YOLO11n to OpenVINO IR once per input size; reuse afterwards."""
    with _export_lock:
        target = model_dir(imgsz)
        if target.exists() and any(target.glob("*.xml")):
            return target

        # legacy single-size folder from earlier versions
        legacy = MODELS_DIR / "yolo11n_openvino_model"
        if imgsz == 416 and legacy.exists() and any(legacy.glob("*.xml")):
            return legacy

        from ultralytics import YOLO  # heavy; only needed for the export

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        pt_path = MODELS_DIR / "yolo11n.pt"
        model = YOLO(str(pt_path) if pt_path.exists() else "yolo11n.pt")
        exported = Path(model.export(format="openvino", imgsz=imgsz, half=False))
        if exported.resolve() != target.resolve():
            import shutil

            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(exported), str(target))
        return target


def _get_compiled(device: str, imgsz: int, threads: int | None):
    """Compile once per (device, size); every camera shares the result."""
    device = (device or "CPU").upper()
    key = (device, imgsz)
    with _cache_lock:
        hit = _compiled.get(key)
        if hit is not None:
            return hit

        core = _get_core()
        model = core.read_model(next(ensure_model(imgsz).glob("*.xml")))

        cfg = {"PERFORMANCE_HINT": "LATENCY"}
        if device.startswith("CPU") and threads:
            # several cameras each grabbing every core just makes them fight
            cfg["INFERENCE_NUM_THREADS"] = int(threads)
        try:
            compiled = core.compile_model(model, device, cfg)
            actual = device
        except Exception as exc:  # noqa: BLE001 — a bad/absent GPU must not kill the camera
            print(f"[detector] {device} unavailable ({exc}); falling back to CPU", flush=True)
            cfg.pop("INFERENCE_NUM_THREADS", None)
            if threads:
                cfg["INFERENCE_NUM_THREADS"] = int(threads)
            compiled = core.compile_model(model, "CPU", cfg)
            actual = "CPU"

        shape = model.inputs[0].partial_shape
        real_size = int(shape[2].get_length()) if shape[2].is_static else imgsz
        entry = (compiled, actual, real_size)
        _compiled[key] = entry
        if actual != device:
            _compiled[(actual, imgsz)] = entry
        print(f"[detector] model {imgsz}px compiled on {actual}", flush=True)
        return entry


class PersonDetector:
    def __init__(self, imgsz: int = 416, threads: int | None = None, device: str = "CPU"):
        self.compiled, self.device, self.imgsz = _get_compiled(device, imgsz, threads)
        self.request = self.compiled.create_infer_request()
        self.output = self.compiled.output(0)
        self._lock = _gpu_lock if self.device.startswith("GPU") else None

    def _infer(self, blob: np.ndarray):
        if self._lock is None:
            return self.request.infer({0: blob})[self.output]
        with self._lock:
            return self.request.infer({0: blob})[self.output]

    def _letterbox(self, frame: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        h, w = frame.shape[:2]
        scale = min(self.imgsz / h, self.imgsz / w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.imgsz, self.imgsz, 3), 114, np.uint8)
        top, left = (self.imgsz - nh) // 2, (self.imgsz - nw) // 2
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, scale, left, top

    def detect(self, frame: np.ndarray, conf: float = 0.35, iou: float = 0.45) -> sv.Detections:
        """Person detections in original-frame coordinates."""
        canvas, scale, dx, dy = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True)
        pred = self._infer(blob)[0]  # (4 + num_classes, N)

        scores = pred[4 + PERSON_CLASS]
        keep = scores > conf
        if not np.any(keep):
            return sv.Detections.empty()

        boxes = pred[:4, keep].T  # cx, cy, w, h in letterbox space
        scores = scores[keep]
        half_w = boxes[:, 2] / 2
        half_h = boxes[:, 3] / 2
        xyxy = np.empty((len(boxes), 4), dtype=np.float32)
        xyxy[:, 0] = (boxes[:, 0] - half_w - dx) / scale
        xyxy[:, 1] = (boxes[:, 1] - half_h - dy) / scale
        xyxy[:, 2] = (boxes[:, 0] + half_w - dx) / scale
        xyxy[:, 3] = (boxes[:, 1] + half_h - dy) / scale

        h, w = frame.shape[:2]
        np.clip(xyxy[:, [0, 2]], 0, w - 1, out=xyxy[:, [0, 2]])
        np.clip(xyxy[:, [1, 3]], 0, h - 1, out=xyxy[:, [1, 3]])

        wh_boxes = np.stack(
            [xyxy[:, 0], xyxy[:, 1], xyxy[:, 2] - xyxy[:, 0], xyxy[:, 3] - xyxy[:, 1]], 1
        )
        idx = cv2.dnn.NMSBoxes(wh_boxes.tolist(), scores.tolist(), conf, iou)
        if len(idx) == 0:
            return sv.Detections.empty()
        idx = np.asarray(idx).flatten()

        return sv.Detections(
            xyxy=xyxy[idx],
            confidence=scores[idx].astype(np.float32),
            class_id=np.zeros(len(idx), dtype=int),
        )
