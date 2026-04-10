"""
overlay_worker.py — Overlay rendering entry point
===================================================
Rendering is delegated to style plugins in styles/.
This module owns blend_rgba, default_layout, and the multiprocessing worker.
"""
from __future__ import annotations
from overlay_utils import blend_rgba, scale_factor   # re-export scale_factor for rb_render


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


def render_frame_worker(args: tuple) -> bytes:
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
    session_meta = _extra[0] if _extra else {}

    frame  = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(shape).copy()
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
            gd['exhaust_temp'] = history[-1].get('exhaust_temp', 0.0) if history else 0.0
            gd['_theme'] = theme
            try:
                img = render_style('gauge', style, gd, gw, gh)
                blend_rgba(frame, img, gx, gy)
            except Exception:
                pass
            continue

        if channel == 'map':
            if not (show_map and lap_lats):
                continue
            data = {'lats': lap_lats, 'lons': lap_lons, 'cur_idx': cur_pt_idx,
                    '_theme': theme}
            try:
                mi = render_style('map', style, data, max(60, gw), max(60, gh))
                blend_rgba(frame, mi, gx, gy)
            except Exception:
                pass
        elif show_telemetry and history:
            if channel == MULTI_CHANNEL:
                sub_channels = g.get('channels', [])
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
                blend_rgba(frame, img, gx, gy)
            except Exception:
                pass

    return frame.tobytes()
