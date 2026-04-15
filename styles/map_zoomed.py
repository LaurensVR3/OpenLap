"""
Map style: Zoomed
=================
Centred on the current GPS position with a configurable radius (metres).
Optionally renders the reference-lap trace in purple.

ELEMENT_TYPE : "map"
Data keys    : lats, lons, cur_idx,
               zoom_radius_m  (default 150),
               show_ref       (default False),
               ref_lats, ref_lons  (reference-lap GPS arrays, may be empty)
"""
STYLE_NAME   = "Zoomed"
ELEMENT_TYPE = "map"

import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def _gps_to_local(lats, lons, center_lat, center_lon):
    """Convert lat/lon sequences to local (x, y) in metres."""
    lat_m = 111000.0
    lon_m = 111000.0 * math.cos(math.radians(center_lat))
    x = [(lo - center_lon) * lon_m for lo in lons]
    y = [(la - center_lat) * lat_m for la in lats]
    return x, y


def render(data: dict, w: int, h: int):
    import numpy as np
    from overlay_utils import fig_to_rgba

    lats        = data.get('lats', [])
    lons        = data.get('lons', [])
    cur_idx     = int(data.get('cur_idx', 0))
    radius      = max(10.0, float(data.get('zoom_radius_m', 150)))
    show_ref    = bool(data.get('show_ref', False))
    ref_lats    = data.get('ref_lats', [])
    ref_lons    = data.get('ref_lons', [])
    ref_cur_idx = int(data.get('ref_cur_idx', 0))

    T            = data.get('_tc', {})
    map_bg       = T.get('map_bg_rgba',     (0, 0, 0, 0.65))
    track_outer  = T.get('map_track_outer', '#1a2a3a')
    track_inner  = T.get('map_track_inner', '#2255aa')
    driven_col   = T.get('map_driven',      '#ffffff')
    dot_col      = T.get('map_dot',         '#ff2222')
    start_col    = T.get('map_start',       '#00ff88')
    ref_col      = '#cc44ff'

    if not lats or len(lats) < 2:
        # Fall back to plain Circuit style when there is no GPS data
        from styles.map_circuit import render as _circuit
        return _circuit(data, w, h)

    safe_idx    = max(0, min(cur_idx, len(lats) - 1))
    center_lat  = lats[safe_idx]
    center_lon  = lons[safe_idx]

    x, y        = _gps_to_local(lats, lons, center_lat, center_lon)

    dpi = 100
    fig, ax = plt.subplots(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)
    ax.set_facecolor(map_bg)

    # Full track outline
    ax.plot(x, y, color=track_outer, lw=5.0, solid_capstyle='round', zorder=1)
    ax.plot(x, y, color=track_inner, lw=2.5, solid_capstyle='round', zorder=2)

    # Reference lap trace + reference dot
    if show_ref and ref_lats and len(ref_lats) >= 2:
        rx, ry = _gps_to_local(ref_lats, ref_lons, center_lat, center_lon)
        ax.plot(rx, ry, color=ref_col, lw=2.0, alpha=0.80,
                solid_capstyle='round', zorder=3)
        safe_ref_idx = max(0, min(ref_cur_idx, len(ref_lats) - 1))
        ax.plot(rx[safe_ref_idx], ry[safe_ref_idx], 'o',
                color=ref_col, ms=max(5, min(w, h) // 32),
                mec='white', mew=1.4, zorder=6)

    # Driven portion
    if safe_idx > 1:
        n = min(safe_idx + 1, len(x))
        ax.plot(x[:n], y[:n], color=driven_col, lw=3.0,
                alpha=0.92, solid_capstyle='round', zorder=4)

    # Start marker
    ax.plot(x[0], y[0], 's', color=start_col,
            ms=max(5, min(w, h) // 40), mec='white', mew=1.2, zorder=5)

    # Current position dot
    dot_ms = max(7, min(w, h) // 25)
    ax.plot(x[safe_idx], y[safe_idx], 'o',
            color=dot_col, ms=dot_ms, mec='white', mew=1.8, zorder=7)

    # View centred on current position ± radius
    ax.set_xlim(-radius, radius)
    ax.set_ylim(-radius, radius)
    ax.set_aspect('equal')
    ax.axis('off')

    fig.tight_layout(pad=0.2)
    rgba = fig_to_rgba(fig, (w, h))

    # Radial feather/fade: fade to transparent near the edges
    cy_px, cx_px = h / 2.0, w / 2.0
    ys = np.arange(h, dtype=np.float32) - cy_px
    xs = np.arange(w, dtype=np.float32) - cx_px
    dist = np.sqrt(xs[np.newaxis, :] ** 2 + ys[:, np.newaxis] ** 2)
    inner_r = min(w, h) * 0.32   # fully opaque inside this radius
    outer_r = min(w, h) * 0.50   # fully transparent at this radius
    fade = np.clip((outer_r - dist) / max(1.0, outer_r - inner_r), 0.0, 1.0)
    rgba = rgba.copy()
    rgba[:, :, 3] = (rgba[:, :, 3].astype(np.float32) * fade).astype(np.uint8)

    return rgba
