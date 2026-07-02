import sys, os, time
import numpy as np
from msgq.visionipc import VisionIpcClient, VisionStreamType

STREAMS = [
    ("road", VisionStreamType.VISION_STREAM_ROAD),
    ("wide", VisionStreamType.VISION_STREAM_WIDE_ROAD),
    ("driver", VisionStreamType.VISION_STREAM_DRIVER),
]

for name, st in STREAMS:
    try:
        c = VisionIpcClient("camerad", st, True)
        if not c.connect(False):
            print(f"{name}: no server"); continue
        buf = None
        for _ in range(150):
            buf = c.recv(100)
            if buf is not None:
                break
        if buf is None:
            print(f"{name}: no frame"); continue
        arr = np.frombuffer(buf.data, dtype=np.uint8).copy()
        y = arr[: buf.uv_offset]
        print(f"{name}: {buf.width}x{buf.height} stride={buf.stride} uv_offset={buf.uv_offset} len={len(arr)} y_mean={y.mean():.1f}")
        with open(f"/data/ref_legacy_{name}.nv12", "wb") as f:
            f.write(bytes(arr))
        with open(f"/data/ref_legacy_{name}.meta", "w") as f:
            f.write(f"{buf.width} {buf.height} {buf.stride} {buf.uv_offset}\n")
        print(f"{name}: saved")
    except Exception as e:
        print(f"{name}: ERR {e}")
