"""
Gauge style: Image / Logo
=========================
Renders a static image (logo, watermark) onto the overlay.
Matches the JS GaugeImage renderer in frontend/js/gauges/image.js.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Image"
Data keys    : image_path, opacity (0.0–1.0), fit ('contain'|'cover'|'stretch')
"""
STYLE_NAME   = 'Image'
ELEMENT_TYPE = 'gauge'

import numpy as np


def render(data: dict, w: int, h: int) -> np.ndarray:
    from PIL import Image as PILImage

    path    = data.get('image_path', '')
    opacity = float(data.get('opacity', 1.0))
    fit     = data.get('fit', 'contain')

    out = np.zeros((h, w, 4), dtype=np.uint8)

    if not path:
        return _placeholder(out, w, h)

    try:
        img = PILImage.open(path).convert('RGBA')
    except Exception:
        return _placeholder(out, w, h)

    iw, ih = img.size

    if fit == 'contain':
        scale = min(w / iw, h / ih)
        nw = int(iw * scale)
        nh = int(ih * scale)
        img = img.resize((nw, nh), PILImage.LANCZOS)
        dx = (w - nw) // 2
        dy = (h - nh) // 2
    elif fit == 'cover':
        scale = max(w / iw, h / ih)
        nw = int(iw * scale)
        nh = int(ih * scale)
        img = img.resize((nw, nh), PILImage.LANCZOS)
        dx = (w - nw) // 2
        dy = (h - nh) // 2
    else:  # stretch
        img = img.resize((w, h), PILImage.LANCZOS)
        nw, nh, dx, dy = w, h, 0, 0

    if opacity < 1.0:
        r, g, b, a = img.split()
        a = a.point(lambda x: int(x * opacity))
        img = PILImage.merge('RGBA', (r, g, b, a))

    arr = np.array(img)

    # Clip paste region to canvas bounds (handles cover overhang)
    sx = max(0, -dx)
    sy = max(0, -dy)
    ex = min(nw, w - dx)
    ey = min(nh, h - dy)
    cx = max(0, dx)
    cy = max(0, dy)

    if ex > sx and ey > sy:
        out[cy:cy + (ey - sy), cx:cx + (ex - sx)] = arr[sy:ey, sx:ex]

    return out


def _placeholder(out: np.ndarray, w: int, h: int) -> np.ndarray:
    """Checkerboard placeholder — matches the JS GaugeImage._placeholder."""
    cell = max(4, min(w, h) // 8)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            col = 42 if ((x // cell + y // cell) % 2 == 0) else 24
            xe = min(x + cell, w)
            ye = min(y + cell, h)
            out[y:ye, x:xe, :3] = col
            out[y:ye, x:xe, 3]  = 255
    return out
