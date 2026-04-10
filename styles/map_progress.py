"""
Map style: Progress
===================
Horizontal progress bar showing how far through the lap/stage the driver is.
Useful for drag racing, hillclimbs, and point-to-point events where the
overhead circuit view is not meaningful.

ELEMENT_TYPE : "map"
Data keys    : lats, lons, cur_idx
"""
STYLE_NAME   = "Progress"
ELEMENT_TYPE = "map"

import numpy as np


def render(data: dict, w: int, h: int):
    lats    = data.get('lats', [])
    lons    = data.get('lons', [])
    cur_idx = data.get('cur_idx', 0)
    T       = data.get('_tc', {})

    n       = max(len(lats), 1)
    pct     = min(1.0, max(0.0, cur_idx / n))

    # Colours from theme
    bg_col    = _hex_to_rgb(T.get('gauge_bg',   '#1a1d2e'))
    fill_col  = _hex_to_rgb(T.get('gauge_acc',  '#4f8ef7'))
    track_col = _hex_to_rgb(T.get('gauge_track','#21253a'))
    text_col  = _hex_to_rgb(T.get('gauge_text', '#e8eaf6'))

    canvas = np.zeros((h, w, 4), dtype=np.uint8)

    # Background
    canvas[:, :, :3] = bg_col
    canvas[:, :,  3] = 180

    pad_x = max(6, w // 12)
    pad_y = max(6, h // 4)
    bar_x1, bar_x2 = pad_x, w - pad_x
    bar_y1, bar_y2 = pad_y, h - pad_y
    bar_w  = bar_x2 - bar_x1
    bar_h  = bar_y2 - bar_y1
    radius = max(2, bar_h // 3)

    # Track (empty portion)
    _rounded_rect(canvas, bar_x1, bar_y1, bar_x2, bar_y2, radius, track_col, 255)

    # Fill (driven portion)
    fill_end = bar_x1 + max(2 * radius, int(bar_w * pct))
    fill_end = min(fill_end, bar_x2)
    _rounded_rect(canvas, bar_x1, bar_y1, fill_end, bar_y2, radius, fill_col, 255)

    # Percentage label centred in bar
    label = f"{int(pct * 100)}%"
    _draw_text_center(canvas, label, w // 2, (bar_y1 + bar_y2) // 2, text_col)

    return canvas


# ── helpers ──────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str):
    h = h.lstrip('#')
    if len(h) == 6:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    return (200, 200, 200)


def _rounded_rect(canvas, x1, y1, x2, y2, r, colour, alpha):
    from PIL import Image, ImageDraw
    h, w = canvas.shape[:2]
    img  = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r,
                            fill=(*colour, alpha))
    arr = np.array(img)
    mask = arr[:, :, 3] > 0
    canvas[mask] = arr[mask]


def _draw_text_center(canvas, text, cx, cy, colour):
    from PIL import Image, ImageDraw, ImageFont
    h, w = canvas.shape[:2]
    img  = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_size = max(10, h // 3)
    try:
        from PIL import ImageFont as _IF
        fnt = _IF.truetype('arial.ttf', font_size)
    except Exception:
        fnt = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw // 2, cy - th // 2), text, font=fnt,
              fill=(*colour, 230))
    arr  = np.array(img)
    mask = arr[:, :, 3] > 0
    canvas[mask] = arr[mask]
