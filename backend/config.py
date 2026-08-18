"""Configuration management — loads/saves config.json with thread-safe access."""
import copy
import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
MODELS_DIR = Path(__file__).resolve().parent / "models"

DEFAULT_CONFIG = {
    "cameras": [
        {
            "id": 1,
            "name": "Entrance",
            "source": "rtsp://user:pass@192.168.1.101:554/stream2",
            "enabled": False,
            "line": {"x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5},
            "invert_direction": False,
            "count_mode": "both",  # both | in_only | out_only
            "detect_every_n": 3,
            "conf": 0.35,
        },
        {
            "id": 2,
            "name": "Exit",
            "source": "rtsp://user:pass@192.168.1.102:554/stream2",
            "enabled": False,
            "line": {"x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5},
            "invert_direction": False,
            "count_mode": "both",  # both | in_only | out_only
            "detect_every_n": 3,
            "conf": 0.35,
        },
    ],
    # preview_max_width shrinks streamed frames before they are encoded. The
    # picture is shown in a box far narrower than the camera's own resolution, so
    # sending it full size only takes cores away from detection.
    "model": {"imgsz": 416, "device": "CPU", "preview_fps": 10, "preview_max_width": 960},
    # Which camera provides which direction for the site total. One camera
    # watches the entrance and only counts IN; the other watches the exit and
    # only counts OUT — they are never added together.
    "site": {"in_camera": 1, "out_camera": 2},
    "tracking": {"lost_seconds": 3.0},
    "counting": {
        "cooldown_s": 2.0,
        "deadband_frac": 0.008,   # hysteresis band, fraction of (w + h)
        "margin_frac": 0.15,      # line extension past both endpoints
        "min_track_age": 2,       # tracker updates required before counting
    },
    "snapshots": {"enabled": True, "keep_days": 7},
    "jpeg_quality": 70,
    # Push counts to the FactoryBox IoT platform (numbers only, no images)
    "upload": {
        "enabled": False,
        "base_url": "http://10.1.100.200",
        "interval_s": 60,
        "api_keys": {},   # {"1": "fbx_...", "2": "fbx_..."}
    },
}

_lock = threading.Lock()
_cache: dict | None = None


def _with_defaults(cfg: dict) -> dict:
    """Fill in any top-level section a hand-edited or older config is missing,
    so the UI never binds to an undefined object."""
    for key, default in DEFAULT_CONFIG.items():
        if key == "cameras":
            continue
        if isinstance(default, dict):
            section = cfg.get(key)
            if not isinstance(section, dict):
                cfg[key] = copy.deepcopy(default)
            else:
                for k, v in default.items():
                    section.setdefault(k, v)
        else:
            cfg.setdefault(key, default)
    if not isinstance(cfg.get("cameras"), list):
        cfg["cameras"] = copy.deepcopy(DEFAULT_CONFIG["cameras"])
    return cfg


def load_config() -> dict:
    """In-memory cached config — callers hit this every second (stats/WS),
    so disk is only touched on first load and on save. Returns a copy: callers
    must not be able to mutate the shared cache by accident."""
    global _cache
    with _lock:
        if _cache is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            loaded = None
            if CONFIG_PATH.exists():
                try:
                    loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    # truncated by a power cut mid-write — keep the bad file for
                    # inspection and fall back to defaults so the app still boots
                    try:
                        CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".json.corrupt"))
                    except OSError:
                        pass
            if not isinstance(loaded, dict):
                loaded = copy.deepcopy(DEFAULT_CONFIG)
                _write(loaded)
            _cache = _with_defaults(loaded)
        return copy.deepcopy(_cache)


def _write(cfg: dict) -> None:
    """Atomic write — a crash mid-save must never truncate config.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def save_config(cfg: dict) -> None:
    global _cache
    with _lock:
        cfg = _with_defaults(copy.deepcopy(cfg))
        _write(cfg)
        _cache = cfg
