"""
overlay_worker.py — Overlay rendering entry point
===================================================
Rendering is delegated to style plugins in styles/.
This module owns blend_rgba, default_layout, and the multiprocessing worker.
"""
from __future__ import annotations
import logging
import os
from typing import Tuple
from overlay_utils import blend_rgba, blend_rgba_onto_rgba, scale_factor

logger = logging.getLogger(__name__)

# Per-process image cache: (path, mtime) → RGBA ndarray
_IMAGE_CACHE: dict = {}


def default_layout() -> dict:
    """Return a default overlay layout dict (used when no config is present)."""
    return {
        'is_bike': False,
        'theme':   'Dark',
        'gauges': [
            {'channel': 'map',        'style': 'Circuit', 'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
            {'channel': 'speed',      'style': 'Dial',    'visible': True, 'x': 0.01, 'y': 0.74, 'w': 0.13, 'h': 0.23},
            {'channel': 'gforce_lat', 'style': 'Bar',     'visible': True, 'x': 0.15, 'y': 0.74, 'w': 0.10, 'h': 0.23},
            {'channel': 'gforce_lon', 'style': 'Bar',     'visible': True, 'x': 0.26, 'y': 0.74, 'w': 0.10, 'h': 0.23},
            {'channel': 'lap_time',   'style': 'Numeric', 'visible': True, 'x': 0.37, 'y': 0.74, 'w': 0.13, 'h': 0.23},
        ],
    }


def render_frame_worker(args: Tuple) -> bytes:
    """
    Multiprocessing worker: renders overlay onto one video frame.

    args = (frame_bytes, shape, cur_pt_idx,
            lap_lats, lap_lons,
            history,        # list of {t, speed, gx, gy, lean, rpm, exhaust_temp, delta_time}
            ref_history,    # list of same shape for reference lap, or []
            lap_duration,
            vw, vh,
            show_map, show_telemetry,
            is_bike,
            overlay_layout, # dict — see default_layout()
            max_speed,      # float — session max speed rounded up +10%
            sectors)        # list of pre-computed sector dicts, or []
    """
    import numpy as np
    from style_registry  import render_style
    from gauge_channels  import gauge_data, GAUGE_CHANNELS, build_multi_data, MULTI_CHANNEL

    (frame_bytes, shape, cur_pt_idx,
     lap_lats, lap_lons,
     history, ref_history, lap_duration,
     vw, vh,
     show_map, show_telemetry,
     is_bike,
     overlay_layout,
     max_speed,
     sectors,
     *_extra) = args
    session_meta      = _extra[0] if len(_extra) > 0 else {}
    ref_lats          = _extra[1] if len(_extra) > 1 else []
    ref_lons          = _extra[2] if len(_extra) > 2 else []
    ref_duration      = _extra[3] if len(_extra) > 3 else 0.0
    overlay_only      = _extra[4] if len(_extra) > 4 else False
    track_map_lats    = _extra[5] if len(_extra) > 5 else []
    track_map_lons    = _extra[6] if len(_extra) > 6 else []
    track_map_areas   = _extra[7] if len(_extra) > 7 else []

    from gauge_channels import gauge_data_lap_info

    if overlay_only:
        frame  = np.zeros((vh, vw, 4), dtype=np.uint8)
        _blend = blend_rgba_onto_rgba
    else:
        frame  = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(shape).copy()
        _blend = blend_rgba
    layout = overlay_layout or default_layout()
    theme  = layout.get('theme', 'Dark')

    # ── Gauges and map ────────────────────────────────────────────────────────
    for g in layout.get('gauges', []):
        if not g.get('visible', True):
            continue
        channel = g.get('channel', 'speed')
        style   = g.get('style',   'Numeric')
        gx      = int(g.get('x', 0.0) * vw)
        gy      = int(g.get('y', 0.0) * vh)
        gw      = max(32, int(g.get('w', 0.12) * vw))
        gh      = max(24, int(g.get('h', 0.20) * vh))

        if channel == 'info':
            gd = dict(session_meta)
            # Per-gauge overrides only fill fields that the session doesn't provide,
            # so actual session data always wins over stale overlay-editor text.
            for k, v in (g.get('info_overrides') or {}).items():
                if v and not gd.get(f'info_{k}'):
                    gd[f'info_{k}'] = v
            gd['selected_fields'] = g.get('selected_fields') or g.get('channels') or []
            gd['_theme'] = theme
            try:
                img = render_style('gauge', style, gd, gw, gh)
                _blend(frame, img, gx, gy)
            except Exception as e:
                logger.debug('Failed to render info gauge %s: %s', style, e)
            continue

        if channel == 'lap_info':
            gd = gauge_data_lap_info(history)
            gd['selected_fields'] = g.get('selected_fields') or ['lap', 'best', 'current', 'delta']
            gd['_theme'] = theme
            try:
                img = render_style('gauge', style, gd, gw, gh)
                _blend(frame, img, gx, gy)
            except Exception as e:
                logger.debug('Failed to render lap_info gauge %s: %s', style, e)
            continue

        if channel == 'image':
            image_path = g.get('image_path', '')
            if not image_path or not os.path.isfile(image_path):
                continue
            if os.path.getsize(image_path) > 50 * 1024 * 1024:
                logger.debug('Skipping oversized image (>50 MB): %s', image_path)
                continue
            try:
                import numpy as np
                from PIL import Image as _PILImage
                mtime = os.path.getmtime(image_path)
                cache_key = (image_path, mtime, gw, gh)
                rgba = _IMAGE_CACHE.get(cache_key)
                if rgba is None:
                    img = _PILImage.open(image_path).convert('RGBA')
                    img = img.resize((gw, gh), _PILImage.LANCZOS)
                    rgba = np.array(img)
                    _IMAGE_CACHE[cache_key] = rgba
                # Apply opacity from gauge config
                opacity = float(g.get('opacity', 1.0))
                if opacity < 1.0:
                    rgba = rgba.copy()
                    rgba[:, :, 3] = (rgba[:, :, 3] * opacity).astype(rgba.dtype)
                _blend(frame, rgba, gx, gy)
            except Exception as e:
                logger.debug('Failed to render image gauge %s: %s', image_path, e)
            continue

        if channel == 'map':
            if not (show_map and lap_lats):
                continue
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
                'track_map_lats':  track_map_lats  if osm_on else [],
                'track_map_lons':  track_map_lons  if osm_on else [],
                'track_map_areas': track_map_areas if osm_on else [],
            }
            try:
                mi = render_style('map', style, data, max(60, gw), max(60, gh))
                _blend(frame, mi, gx, gy)
            except Exception as e:
                logger.debug('Failed to render map gauge %s: %s', style, e)
        elif show_telemetry and history:
            if channel == MULTI_CHANNEL:
                sub_channels = g.get('multi_channels') or g.get('channels') or []
                if not sub_channels:
                    continue
                gd = build_multi_data(sub_channels, history,
                                      ref_history if ref_history else [])
                gd['_theme'] = theme
            else:
                gd = gauge_data(channel, history)
                gd['lap_duration'] = lap_duration
                gd['is_bike']      = is_bike
                gd['_theme']       = theme
                cur_elapsed = history[-1].get('t', 0.0) if history else 0.0
                gd['sectors'] = [
                    {**s, 'done': s['done'] and s.get('boundary_elapsed', float('inf')) <= cur_elapsed}
                    for s in sectors
                ]
                if channel == 'speed':
                    gd['max_val'] = max_speed
                if ref_history:
                    hk = GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed'])['hist_key']
                    gd['ref_history_vals'] = [p.get(hk, 0.0) for p in ref_history]
                if channel == 'g_meter':
                    gd['history_gy'] = [p.get('gy', 0.0) for p in history]
                    gd['value_gy']   = history[-1].get('gy', 0.0) if history else 0.0
            try:
                img = render_style('gauge', style, gd, gw, gh)
                _blend(frame, img, gx, gy)
            except Exception as e:
                logger.debug('Failed to render gauge %s/%s: %s', channel, style, e)

    return frame.tobytes()
