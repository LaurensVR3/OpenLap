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
    from gauge_channels import GAUGE_CHANNELS
    return {
        'map':       {'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
        'map_style': 'Circuit',
        'is_bike':   False,
        'gauges': [
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
            history,        # list of {t, speed, gx, gy, lean, rpm, exhaust_temp}
            lap_duration,
            vw, vh,
            show_map, show_telemetry,
            is_bike,
            overlay_layout, # dict — see default_layout()
            max_speed)      # float — session max speed rounded up +10%
    """
    import numpy as np
    from style_registry  import render_style
    from gauge_channels  import gauge_data

    (frame_bytes, shape, cur_pt_idx,
     lap_lats, lap_lons,
     history, lap_duration,
     vw, vh,
     show_map, show_telemetry,
     is_bike,
     overlay_layout,
     max_speed) = args

    frame  = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(shape).copy()
    layout = overlay_layout or default_layout()

    # ── Gauges ────────────────────────────────────────────────────────────────
    if show_telemetry and history:
        for g in layout.get('gauges', []):
            if not g.get('visible', True):
                continue
            channel = g.get('channel', 'speed')
            style   = g.get('style',   'Numeric')
            gx      = int(g.get('x', 0.0) * vw)
            gy      = int(g.get('y', 0.0) * vh)
            gw      = max(32, int(g.get('w', 0.12) * vw))
            gh      = max(24, int(g.get('h', 0.20) * vh))

            gd = gauge_data(channel, history)
            gd['lap_duration'] = lap_duration
            gd['is_bike']      = is_bike
            if channel == 'speed':
                gd['max_val'] = max_speed

            try:
                img = render_style('gauge', style, gd, gw, gh)
                blend_rgba(frame, img, gx, gy)
            except Exception:
                pass

    # ── Map ───────────────────────────────────────────────────────────────────
    if show_map and lap_lats:
        mp    = layout.get('map', {})
        m_x   = int(mp.get('x', 0.74) * vw)
        m_y   = int(mp.get('y', 0.02) * vh)
        m_w   = max(60, int(mp.get('w', 0.24) * vw))
        m_h   = max(60, int(mp.get('h', 0.30) * vh))
        style = layout.get('map_style', 'Circuit')
        data  = {'lats': lap_lats, 'lons': lap_lons, 'cur_idx': cur_pt_idx}
        try:
            mi = render_style('map', style, data, m_w, m_h)
            blend_rgba(frame, mi, m_x, m_y)
        except Exception:
            pass

    return frame.tobytes()
