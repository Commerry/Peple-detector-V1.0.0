"""Measure whether this machine can run the people counter, and how many cameras.

Run on the target PC:
    .venv\\Scripts\\python.exe tools\\check_machine.py

Prints the real inference speed for every device OpenVINO can see (CPU and
integrated GPU), then recommends settings.
"""
import platform
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import openvino as ov  # noqa: E402
import psutil  # noqa: E402

# detections/second needed per camera for reliable line counting
NEED_PER_CAMERA = 5.0


def human_ram(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def main() -> None:
    print("-" * 66)
    print(" People Counter - machine capability check")
    print("-" * 66)

    cpu = platform.processor() or "unknown"
    try:
        import subprocess

        if platform.system() == "Windows":
            out = subprocess.run(
                ["wmic", "cpu", "get", "name"], capture_output=True, text=True, timeout=15
            ).stdout.splitlines()
            names = [x.strip() for x in out if x.strip() and "Name" not in x]
            if names:
                cpu = names[0]
        else:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except Exception:  # noqa: BLE001
        pass

    # AVX2 makes CPU inference several times faster; its absence is why small
    # Celeron/Atom boxes must use the integrated GPU instead
    avx2 = None
    try:
        if platform.system() != "Windows":
            info = Path("/proc/cpuinfo").read_text()
            avx2 = "avx2" in info
    except Exception:  # noqa: BLE001
        pass

    cores = psutil.cpu_count(logical=False) or 1
    threads = psutil.cpu_count(logical=True) or 1
    ram = psutil.virtual_memory().total
    disk = psutil.disk_usage(str(Path(__file__).resolve().anchor))

    print(f"CPU        : {cpu}")
    print(f"Cores      : {cores} physical / {threads} logical")
    if avx2 is not None:
        print(f"AVX2       : {'yes' if avx2 else 'NO - CPU inference will be slow, use the iGPU'}")
    print(f"RAM        : {human_ram(ram)}")
    print(f"Disk free  : {human_ram(disk.free)} of {human_ram(disk.total)}")

    from detector import ensure_model  # noqa: E402  (slow import)

    core = ov.Core()
    devices = core.available_devices
    print(f"Devices    : {', '.join(devices)}")
    for d in devices:
        try:
            print(f"             {d} = {core.get_property(d, 'FULL_DEVICE_NAME')}")
        except Exception:  # noqa: BLE001
            pass

    frame = (np.random.rand(576, 704, 3) * 255).astype(np.uint8)
    results: dict[tuple[str, int], float] = {}

    for imgsz in (320, 416):
        print(f"\nPreparing model at {imgsz}x{imgsz} (first run downloads/exports)...")
        path = ensure_model(imgsz)
        model = core.read_model(next(Path(path).glob("*.xml")))
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (imgsz, imgsz)), 1 / 255.0, swapRB=True
        )
        for dev in devices:
            if dev.startswith("GPU") and "dGPU" in str(
                core.get_property(dev, "FULL_DEVICE_NAME")
            ):
                pass  # still measure discrete GPUs, they are valid options
            try:
                compiled = core.compile_model(model, dev, {"PERFORMANCE_HINT": "LATENCY"})
                req = compiled.create_infer_request()
                for _ in range(5):
                    req.infer({0: blob})
                n = 25
                t0 = time.perf_counter()
                for _ in range(n):
                    req.infer({0: blob})
                ms = (time.perf_counter() - t0) / n * 1000
                results[(dev, imgsz)] = ms
                print(f"  {dev:7s} {imgsz}: {ms:7.1f} ms  -> {1000 / ms:5.1f} detections/sec")
            except Exception as exc:  # noqa: BLE001
                print(f"  {dev:7s} {imgsz}: unavailable ({str(exc)[:60]})")

    if not results:
        print("\nNo device could run the model — installation problem.")
        return

    # video decode cost matters as much as inference on weak CPUs
    enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])[1]
    t0 = time.perf_counter()
    for _ in range(100):
        cv2.imdecode(enc, cv2.IMREAD_COLOR)
    decode_ms = (time.perf_counter() - t0) / 100 * 1000
    print(f"\nFrame decode : {decode_ms:.1f} ms per frame (per camera stream)")

    best_dev, best_size = min(results, key=results.get)
    best_ms = results[(best_dev, best_size)]
    rate = 1000 / best_ms

    print("\n" + "=" * 66)
    print(" VERDICT")
    print("-" * 66)

    # Two independent limits:
    #  1) inference throughput, shared by all cameras
    #  2) CPU-side work per camera (RTSP decode, tracking, drawing, Python) —
    #     this is what actually caps a weak box, and it needs a whole core each
    usable = rate * 0.7  # headroom for the CPU-side work above
    cams_by_inference = int(usable // NEED_PER_CAMERA)
    cams_by_cpu = max(1, cores - 1)  # leave one core for the OS and the web server
    cams = max(0, min(cams_by_inference, cams_by_cpu))

    print(f"Fastest option : {best_dev} at {best_size}px  ({rate:.0f} detections/sec raw)")
    print(f"Usable rate    : ~{usable:.0f} detections/sec after decode + tracking")
    print(f"Limit (model)  : {cams_by_inference} camera(s) at {NEED_PER_CAMERA:.0f} detections/sec each")
    print(f"Limit (cores)  : {cams_by_cpu} camera(s) - each needs roughly one core for decode + tracking")
    print(f"=> CAMERAS     : {cams}")

    if ram < 6 * 1024 ** 3:
        print("RAM            : LOW — 8 GB or more recommended")
    else:
        print(f"RAM            : OK ({human_ram(ram)}; the app uses ~0.5 GB for 2 cameras)")

    print("\nRecommended settings (Settings > System / Cameras):")
    print(f"  Processing device  : {best_dev}")
    print(f"  Inference size     : {best_size}")
    every = 2 if usable >= 20 else 3 if usable >= 10 else 4
    print(f"  Detect every N     : {every}")
    print("  RTSP URL           : use the camera substream (640x480 or 704x576)")
    if cams < 2:
        print("\n  NOTE: this machine is marginal for 2 cameras. Run one camera per")
        print("        machine, or lower the resolution / raise Detect every N.")
    if decode_ms > 12:
        print("\n  NOTE: video decoding is slow here - use the camera substream")
        print("        (640x480). A 1080p main stream will not keep up.")


if __name__ == "__main__":
    main()
