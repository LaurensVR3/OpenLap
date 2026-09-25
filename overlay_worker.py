"""
overlay_worker.py — Renders one frame's overlay, in a worker process.
=====================================================================
Rendering of each gauge is delegated to the style plugins in styles/. This
module decides where each gauge goes, feeds it the right data, and composites
the results onto a transparent RGBA canvas. video_renderer pipes those
canvases to FFmpeg, which overlays them on the source video (or, for an
overlay-only export, encodes them on their own with alpha).

For a normal export each frame's gauges are packed into an *atlas*: every
gauge drawn at full size into its own slot of one compact image (see
atlas_layout), which FFmpeg crops back apart and overlays at each gauge's
position (onto a transparent frame for an overlay-only export). At 2.7K
the gauges are spread over the whole frame but cover a fraction of it, and
an atlas of just their pixels is far less to draw, pickle, pipe and
overlay than a frame-sized canvas: measured on a real 2.7K export, 3.9x
faster than the old full-frame pipeline at the same worker count.

A task is (ctx_id, ctx_blob, frame). ctx is everything constant for one
render (layout, map, session info, ...), pickled once by the parent and
unpickled once per worker per render, instead of once per frame.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
from typing import Optional, Tuple

from overlay_utils import scale_factor  # noqa: F401 (re-exported)

logger = logging.getLogger(__name__)

# Per-process caches. _IMAGE_CACHE: (path, mtime, w, h) → resized RGBA.
# _LAST: gauge index → (data digest, RGBA) for the previous frame, so a gauge
# whose data did not change (Info, Image, a stationary kart's dial, a
# finished sector bar) is not redrawn through matplotlib every frame.
_IMAGE_CACHE: dict = {}
_LAST: dict = {}
_CTX: dict = {'id': None, 'ctx': None}


def default_layout() -> dict:
    """Return a default overlay layout dict (used when no config is present)."""
    return {
        'is_bike': False,
        'theme':   'Dark',
        'gauges': [
            {'type': 'Circuit', 'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
            {'type': 'Dial',    'channel': 'speed',      'visible': True, 'x': 0.01, 'y': 0.74, 'w': 0.13, 'h': 0.23},
            {'type': 'Bar',     'channel': 'gforce_lat', 'visible': True, 'x': 0.15, 'y': 0.74, 'w': 0.10, 'h': 0.23},
            {'type': 'Bar',     'channel': 'gforce_lon', 'visible': True, 'x': 0.26, 'y': 0.74, 'w': 0.10, 'h': 0.23},
            {'type': 'Numeric', 'channel': 'lap_time',   'visible': True, 'x': 0.37, 'y': 0.74, 'w': 0.13, 'h': 0.23},
        ],
    }


_MAP_TYPES = ('Circuit', 'Zoomed', 'Progress')


def gauge_rect(g: dict, vw: int, vh: int) -> Tuple[int, int, int, int]:
    """Pixel rectangle (x, y, w, h) a gauge is drawn at in a vw×vh frame."""
    x = int(g.get('x', 0.0) * vw)
    y = int(g.get('y', 0.0) * vh)
    w = max(32, int(g.get('w', 0.12) * vw))
    h = max(24, int(g.get('h', 0.20) * vh))
    if g.get('type') in _MAP_TYPES:
        w, h = max(60, w), max(60, h)
    return x, y, w, h


def _gauge_drawn(g: dict, show_map: bool, show_telemetry: bool) -> bool:
    if not g.get('visible', True):
        return False
    if g.get('type') in _MAP_TYPES:
        return show_map
    if g.get('type') in ('Info', 'Scoreboard', 'Image'):
        return True
    return show_telemetry


def atlas_layout(layout: dict, vw: int, vh: int, show_map: bool = True,
                 show_telemetry: bool = True):
    """Pack every drawn gauge into one atlas image.

    Returns (tiles, (atlas_w, atlas_h)) with tiles a list of dicts
    {idx, x, y, w, h, ax, ay}: gauge index in the layout, its position and
    size in the frame, and its slot in the atlas. Sizes and atlas positions
    are even (4:2:0 video cannot be cropped at odd offsets); frame positions
    are rounded to even for the same reason, at most a 1 px shift. Tiles
    keep layout order, which is the order FFmpeg overlays them in, so
    overlapping gauges stack exactly as they do in the editor. Returns
    ([], None) when nothing is drawn.
    """
    import math
    tiles = []
    for idx, g in enumerate((layout or {}).get('gauges', [])):
        if not _gauge_drawn(g, show_map, show_telemetry):
            continue
        x, y, w, h = gauge_rect(g, vw, vh)
        tiles.append({'idx': idx, 'x': x // 2 * 2, 'y': y // 2 * 2,
                      'w': w + w % 2, 'h': h + h % 2})
    if not tiles:
        return [], None
    area  = sum(t['w'] * t['h'] for t in tiles)
    width = max(max(t['w'] for t in tiles), int(math.sqrt(area) * 1.2))
    width += width % 2
    # Shelf packing, tallest first: rows of tiles left to right.
    x = y = shelf = 0
    for t in sorted(tiles, key=lambda t: -t['h']):
        if x and x + t['w'] > width:
            y, x, shelf = y + shelf, 0, 0
        t['ax'], t['ay'] = x, y
        x += t['w']
        shelf = max(shelf, t['h'])
    height = y + shelf
    return tiles, (width, height + height % 2)


def pack_context(ctx: dict) -> bytes:
    return pickle.dumps(ctx, protocol=pickle.HIGHEST_PROTOCOL)


def _context(ctx_id, ctx_blob) -> dict:
    if _CTX['id'] != ctx_id:
        _CTX['id'], _CTX['ctx'] = ctx_id, pickle.loads(ctx_blob)
        _LAST.clear()
    return _CTX['ctx']


def _digest(obj) -> bytes:
    return hashlib.blake2b(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL),
                           digest_size=16).digest()


def _render_cached(idx: int, element: str, gtype: str, gd: dict, w: int, h: int):
    """render_style(), skipped when this gauge's data is identical to the
    previous frame's (same inputs → same pixels)."""
    from style_registry import render_style
    key = _digest((element, gtype, w, h, gd))
    last = _LAST.get(idx)
    if last is not None and last[0] == key:
        return last[1]
    img = render_style(element, gtype, gd, w, h)
    _LAST[idx] = (key, img)
    return img


def _image_gauge(g: dict, gw: int, gh: int):
    import numpy as np
    image_path = g.get('image_path', '')
    if not image_path or not os.path.isfile(image_path):
        return None
    if os.path.getsize(image_path) > 50 * 1024 * 1024:
        logger.debug('Skipping oversized image (>50 MB): %s', image_path)
        return None
    from PIL import Image as _PILImage
    cache_key = (image_path, os.path.getmtime(image_path), gw, gh)
    rgba = _IMAGE_CACHE.get(cache_key)
    if rgba is None:
        img = _PILImage.open(image_path).convert('RGBA').resize((gw, gh), _PILImage.LANCZOS)
        rgba = _IMAGE_CACHE[cache_key] = np.array(img)
    opacity = float(g.get('opacity', 1.0))
    if opacity < 1.0:
        rgba = rgba.copy()
        rgba[:, :, 3] = (rgba[:, :, 3] * opacity).astype(rgba.dtype)
    return rgba


def render_overlay(ctx: dict, frame: dict):
    """Draw every gauge for one frame into its slot of the atlas
    (ctx['tiles'], ctx['atlas']). Returns an (h, w, 4) uint8 RGBA array."""
    import numpy as np
    from gauge_channels import gauge_data, GAUGE_CHANNELS, build_multi_data, gauge_data_lap_info

    vw, vh = ctx['vw'], ctx['vh']
    tiles = {t['idx']: t for t in ctx['tiles']}
    aw, ah = ctx['atlas']
    atlas = np.zeros((ah, aw, 4), dtype=np.uint8)
    layout = ctx['layout'] or default_layout()
    theme  = layout.get('theme', 'Dark')
    speed_unit = ctx.get('speed_unit', 'kmh')
    extra_meta = ctx.get('extra_meta') or {}

    history     = frame.get('history') or []
    ref_history = frame.get('ref_history') or []
    cur_pt_idx  = frame.get('cur_map_idx', 0)
    lap_lats, lap_lons = ctx.get('map_lats') or [], ctx.get('map_lons') or []
    ref_lats, ref_lons = ctx.get('ref_lats') or [], ctx.get('ref_lons') or []

    for idx, g in enumerate(layout.get('gauges', [])):
        if not _gauge_drawn(g, ctx['show_map'], ctx['show_telemetry']):
            continue
        gtype   = g.get('type', 'Dial')
        channel = g.get('channel', 'speed')
        if idx not in tiles:
            continue
        gx, gy, gw, gh = gauge_rect(g, vw, vh)
        ox, oy = tiles[idx]['ax'], tiles[idx]['ay']   # its slot in the atlas
        img = None
        try:
            if gtype == 'Info':
                gd = dict(ctx.get('session_meta') or {})
                # Per-gauge overrides only fill fields the session doesn't
                # provide, so session data wins over stale editor text.
                for k, v in (g.get('info_overrides') or {}).items():
                    if v and not gd.get(f'info_{k}'):
                        gd[f'info_{k}'] = v
                gd['selected_fields'] = g.get('selected_fields') or g.get('channels') or []
                gd['_theme'] = theme
                img = _render_cached(idx, 'gauge', gtype, gd, gw, gh)

            elif gtype == 'Scoreboard':
                gd = gauge_data_lap_info(history)
                gd['selected_fields'] = g.get('selected_fields') or ['lap', 'best', 'current', 'delta']
                gd['_theme'] = theme
                img = _render_cached(idx, 'gauge', gtype, gd, gw, gh)

            elif gtype == 'Image':
                img = _image_gauge(g, gw, gh)

            elif gtype in _MAP_TYPES:
                if not lap_lats:
                    continue
                ref_duration = ctx.get('ref_duration', 0.0)
                if ref_lats:
                    if ref_duration > 0 and history:
                        cur_elapsed = history[-1].get('t', 0.0)
                        ref_frac    = min(1.0, max(0.0, cur_elapsed / ref_duration))
                        ref_cur_idx = int(ref_frac * max(0, len(ref_lats) - 1))
                    else:
                        ref_cur_idx = int(cur_pt_idx / max(1, len(lap_lats) - 1)
                                          * max(0, len(ref_lats) - 1))
                else:
                    ref_cur_idx = 0
                osm_on = g.get('track_map_enabled', True)
                data = {
                    'lats': lap_lats, 'lons': lap_lons, 'cur_idx': cur_pt_idx,
                    '_theme': theme,
                    'zoom_radius_m':  g.get('zoom_radius_m', 150),
                    'show_ref':       g.get('show_ref', True),
                    'ref_lats':       ref_lats,
                    'ref_lons':       ref_lons,
                    'ref_cur_idx':    ref_cur_idx,
                    'track_map_lats':  ctx.get('track_map_lats', []) if osm_on else [],
                    'track_map_lons':  ctx.get('track_map_lons', []) if osm_on else [],
                    'track_map_areas': ctx.get('track_map_areas', []) if osm_on else [],
                }
                img = _render_cached(idx, 'map', gtype, data, gw, gh)

            elif history:
                if gtype == 'Multi-Line':
                    sub_channels = g.get('multi_channels') or g.get('channels') or []
                    if not sub_channels:
                        continue
                    gd = build_multi_data(sub_channels, history, ref_history,
                                          unit=speed_unit, extra_meta=extra_meta)
                    gd['_theme'] = theme
                else:
                    if gtype == 'G-Meter':
                        channel = 'g_meter'   # fixed by the type itself, not user-selectable
                    gd = gauge_data(channel, history, unit=speed_unit, extra_meta=extra_meta)
                    gd['lap_duration'] = ctx.get('lap_duration', 0.0)
                    gd['is_bike']      = ctx.get('is_bike', False)
                    gd['_theme']       = theme
                    cur_elapsed = history[-1].get('t', 0.0)
                    gd['sectors'] = [
                        {**s, 'done': s['done'] and s.get('boundary_elapsed', float('inf')) <= cur_elapsed}
                        for s in ctx.get('sectors') or []
                    ]
                    if channel == 'speed':
                        gd['max_val'] = ctx.get('max_speed', gd['max_val'])
                    if ref_history and channel in GAUGE_CHANNELS:
                        hk = GAUGE_CHANNELS[channel]['hist_key']
                        ref_vals = [p.get(hk, 0.0) for p in ref_history]
                        if channel == 'speed' and speed_unit != 'kmh':
                            from units import KMH_PER_UNIT
                            factor = KMH_PER_UNIT.get(speed_unit, 1.0)
                            ref_vals = [v * factor for v in ref_vals]
                        gd['ref_history_vals'] = ref_vals
                    if gtype == 'G-Meter':
                        gd['history_gy'] = [p.get('gy', 0.0) for p in history]
                        gd['value_gy']   = history[-1].get('gy', 0.0)
                img = _render_cached(idx, 'gauge', gtype, gd, gw, gh)
        except Exception as e:
            logger.debug('Failed to render gauge %s/%s: %s', gtype, channel, e)
            # A style's render() may have called plt.figure() before raising
            # and never reached the plt.close() in fig_to_rgba(). Workers live
            # for the whole export, so close everything rather than leak.
            import matplotlib.pyplot as plt
            plt.close('all')
            continue

        if img is None:
            continue
        # Each slot holds one gauge on a transparent background: a copy, not
        # a blend. FFmpeg does the compositing.
        h, w = min(img.shape[0], tiles[idx]['h']), min(img.shape[1], tiles[idx]['w'])
        atlas[oy:oy + h, ox:ox + w] = img[:h, :w]

    return atlas


def render_frame_worker(task) -> bytes:
    """Multiprocessing entry point: task = (ctx_id, ctx_blob, frame).
    Returns the overlay canvas as raw RGBA bytes."""
    ctx_id, ctx_blob, frame = task
    return render_overlay(_context(ctx_id, ctx_blob), frame).tobytes()
