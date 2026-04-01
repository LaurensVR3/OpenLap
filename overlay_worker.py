"""
rb_overlay.py — Overlay rendering entry point
===============================================
Rendering is delegated to style plugins in styles/.
This module owns blend_rgba, default_layout, and the multiprocessing worker.
"""
from __future__ import annotations
from overlay_utils import blend_rgba, scale_factor   # re-export scale_factor for rb_render


def default_layout() -> dict:
    """Return a default overlay layout dict."""
    return {
        'map':            {'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
        'telemetry':      {'visible': True, 'x': 0.01, 'y': 0.75, 'w': 0.40, 'h': 0.22},
        'is_bike':        False,
        'map_style':      'Circuit',
        'telemetry_style':'Strip',
    }


def render_frame_worker(args: tuple) -> bytes:
    """
    Multiprocessing worker: renders overlay onto one video frame.

    args = (frame_bytes, shape, cur_pt_idx,
            lap_lats, lap_lons,
            history,           # list of {t, speed, gx, gy, lean}
            lap_duration,
            vw, vh,
            show_map, show_telemetry,
            is_bike,
            overlay_layout)    # dict — see default_layout()
    """
    import numpy as np
    from style_registry import render_style

    (frame_bytes, shape, cur_pt_idx,
     lap_lats, lap_lons,
     history, lap_duration,
     vw, vh,
     show_map, show_telemetry,
     is_bike,
     overlay_layout) = args

    frame  = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(shape).copy()
    layout = overlay_layout or default_layout()

    if show_telemetry and history:
        tel   = layout.get('telemetry', {})
        t_x   = int(tel.get('x', 0.01) * vw)
        t_y   = int(tel.get('y', 0.75) * vh)
        t_w   = max(64, int(tel.get('w', 0.40) * vw))
        t_h   = max(32, int(tel.get('h', 0.22) * vh))
        style = layout.get('telemetry_style', 'Strip')
        data  = {'history': history, 'lap_duration': lap_duration, 'is_bike': is_bike}
        strip = render_style('telemetry', style, data, t_w, t_h)
        blend_rgba(frame, strip, t_x, t_y)

    if show_map and lap_lats:
        mp    = layout.get('map', {})
        m_x   = int(mp.get('x', 0.74) * vw)
        m_y   = int(mp.get('y', 0.02) * vh)
        m_w   = max(60, int(mp.get('w', 0.24) * vw))
        m_h   = max(60, int(mp.get('h', 0.30) * vh))
        style = layout.get('map_style', 'Circuit')
        data  = {'lats': lap_lats, 'lons': lap_lons, 'cur_idx': cur_pt_idx}
        mi    = render_style('map', style, data, m_w, m_h)
        blend_rgba(frame, mi, m_x, m_y)

    return frame.tobytes()
