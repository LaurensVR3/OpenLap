"""
overlay_utils.py — Shared utilities for overlay style rendering.
Imported by rb_overlay.py and all style files in styles/.
"""
from __future__ import annotations
import math
from io import BytesIO
from typing import Tuple

import numpy as np


def scale_factor(vw: int, vh: int, base_w: int = 1920, base_h: int = 1080) -> float:
    """Scale factor relative to a reference resolution."""
    return math.sqrt((vw * vh) / (base_w * base_h))


def fig_to_rgba(fig, size: Tuple[int, int]) -> np.ndarray:
    """Convert a matplotlib figure to an RGBA numpy array at the given (w, h) size."""
    from PIL import Image
    import matplotlib.pyplot as plt
    buf = BytesIO()
    fig.savefig(buf, format='png', transparent=True,
                bbox_inches='tight', pad_inches=0, dpi=fig.dpi)
    plt.close(fig)
    buf.seek(0)
    img = Image.open(buf).convert('RGBA').resize(size, Image.LANCZOS)
    return np.array(img)


def blend_rgba(frame: np.ndarray, rgba: np.ndarray, x: int, y: int) -> None:
    """Alpha-composite an RGBA image onto a BGR frame in-place."""
    h, w = rgba.shape[:2]
    fh, fw = frame.shape[:2]
    x1, y1 = max(x, 0), max(y, 0)
    x2, y2 = min(x + w, fw), min(y + h, fh)
    if x2 <= x1 or y2 <= y1:
        return
    sx, sy = x1 - x, y1 - y
    src   = rgba[sy:sy+(y2-y1), sx:sx+(x2-x1)]
    alpha = src[:, :, 3:4].astype(np.float32) / 255.0
    rgb   = src[:, :, :3][:, :, ::-1].astype(np.float32)   # RGBA→BGR
    roi   = frame[y1:y2, x1:x2].astype(np.float32)
    frame[y1:y2, x1:x2] = (roi * (1 - alpha) + rgb * alpha).astype(np.uint8)


# ── Dummy data for editor previews ────────────────────────────────────────────

def dummy_telemetry_data(is_bike: bool = False) -> dict:
    """Realistic-looking dummy telemetry for style previews."""
    hist = []
    for i in range(80):
        t     = i * 0.5
        speed = 130 + 55 * math.sin(t * 0.28) + 15 * math.sin(t * 1.1)
        gx    = 0.4 * math.sin(t * 0.65) - 0.2 * math.sin(t * 2.1)
        gy    = 1.1 * math.sin(t * 0.38) + 0.4 * math.sin(t * 1.4)
        lean  = gy * 28.0
        hist.append({'t': t, 'speed': max(0.0, speed),
                     'gx': gx, 'gy': gy, 'lean': lean})
    return {'history': hist, 'lap_duration': 83.5, 'is_bike': is_bike}


def dummy_map_data() -> dict:
    """Oval-ish dummy track for map style previews."""
    n = 120
    lats, lons = [], []
    for i in range(n):
        a = i * 2 * math.pi / n
        lat = 51.500 + 0.0045 * math.sin(a) + 0.0005 * math.sin(3 * a)
        lon = 4.4000 + 0.0090 * math.cos(a) + 0.0010 * math.cos(2 * a)
        lats.append(lat)
        lons.append(lon)
    return {'lats': lats, 'lons': lons, 'cur_idx': 35}
