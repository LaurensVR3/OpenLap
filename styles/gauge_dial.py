"""
Gauge style: Dial
=================
Circular arc gauge.  Arc spans 240° (from 210° to 330° going clockwise).
Asymmetric channels: arc from bottom-left (low) to bottom-right (high).
Symmetric channels:  zero at top, fill colour flips left/right.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Dial"
Data keys    : value, label, unit, min_val, max_val, symmetric, channel
"""
STYLE_NAME   = 'Dial'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Arc, FancyArrowPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value     = data.get('value',     0.0)
    label     = data.get('label',     '')
    unit      = data.get('unit',      '')
    mn        = data.get('min_val',   0.0)
    mx        = data.get('max_val',   100.0)
    symmetric = data.get('symmetric', False)
    channel   = data.get('channel',   '')

    T           = data.get('_tc', {})
    bg_rgba     = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge     = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    track_col   = T.get('track',        '#1a2530')
    fill_pos    = T.get('fill_pos',     '#ffaa00')
    fill_neg    = T.get('fill_neg',     '#44aaff')
    fill_lo     = T.get('fill_lo',      '#00ccff')
    fill_hi     = T.get('fill_hi',      '#ff4422')
    text_col    = T.get('text',         'white')
    label_col   = T.get('label',        '#445566')
    unit_col    = T.get('unit',         '#5577aa')

    sc  = scale_factor(w, h, base_w=160, base_h=160)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_aspect('equal')
    ax.set_xlim(-1.2, 1.2); ax.set_ylim(-1.2, 1.2)
    ax.axis('off')

    # Background circle
    ax.add_patch(plt.Circle((0, 0), 1.18,
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    fs_value = max(8,  min(int(28 * sc), int(w * 0.22)))
    fs_label = max(5,  min(int(9  * sc), int(w * 0.08)))
    fs_unit  = max(4,  min(int(7  * sc), int(w * 0.06)))

    ARC_START  = 210.0
    ARC_SWEEP  = 240.0
    ARC_END    = ARC_START - ARC_SWEEP
    R_TRACK    = 0.85
    R_FILL     = 0.85
    LW_TRACK   = max(4, int(10 * sc))
    LW_FILL    = max(4, int(10 * sc))

    rng  = mx - mn if mx != mn else 1.0
    frac = max(0.0, min(1.0, (value - mn) / rng))

    # Track arc (full)
    theta_track = np.linspace(np.radians(ARC_START), np.radians(ARC_END), 120)
    ax.plot(R_TRACK * np.cos(theta_track), R_TRACK * np.sin(theta_track),
            color=track_col, lw=LW_TRACK, solid_capstyle='round', zorder=2)

    # Fill arc
    if symmetric:
        zero_angle = np.radians(90.0)
        val_angle  = zero_angle - np.radians(ARC_SWEEP) * (frac - 0.5)
        fill_col   = fill_pos if value >= 0 else fill_neg
        theta_fill = np.linspace(zero_angle, val_angle, 60)
    else:
        val_angle  = np.radians(ARC_START - ARC_SWEEP * frac)
        fill_col   = fill_lo if frac < 0.80 else fill_hi
        theta_fill = np.linspace(np.radians(ARC_START), val_angle, max(2, int(60 * frac)))

    if len(theta_fill) >= 2:
        ax.plot(R_FILL * np.cos(theta_fill), R_FILL * np.sin(theta_fill),
                color=fill_col, lw=LW_FILL, solid_capstyle='round', zorder=3)

    # Needle
    needle_angle = val_angle
    nx = 0.70 * np.cos(needle_angle)
    ny = 0.70 * np.sin(needle_angle)
    ax.annotate('', xy=(nx, ny), xytext=(0, 0),
                arrowprops=dict(arrowstyle='->', color=text_col,
                                lw=max(1.0, 1.5 * sc)))
    ax.plot(0, 0, 'o', color=text_col, markersize=max(3, 5 * sc), zorder=5)

    # Tick marks
    for tick_frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
        ta = np.radians(ARC_START - ARC_SWEEP * tick_frac)
        r0, r1 = 0.73, 0.82
        ax.plot([r0 * np.cos(ta), r1 * np.cos(ta)],
                [r0 * np.sin(ta), r1 * np.sin(ta)],
                color='#2a3a4a', lw=max(0.8, 1.0 * sc), zorder=2)

    # Value text
    if channel == 'lap_time':
        m = int(value // 60); s = value % 60
        val_str = f"{m}:{s:05.2f}" if value >= 60 else f"{value:.2f}"
        fs_value = max(6, min(int(18 * sc), int(w * 0.14)))
    elif abs(value) >= 10000:
        val_str = f"{value:,.0f}"
    elif abs(value) >= 100:
        val_str = f"{value:.0f}"
    elif abs(value) >= 10:
        val_str = f"{value:.1f}"
    else:
        val_str = f"{value:.2f}"

    ax.text(0, -0.28, val_str,
            ha='center', va='center', color=text_col,
            fontsize=fs_value, fontweight='bold', fontfamily='monospace', zorder=6)
    ax.text(0, -0.58, unit,
            ha='center', va='center', color=unit_col,
            fontsize=fs_unit, fontfamily='monospace', zorder=6)
    ax.text(0, 0.55, label.upper(),
            ha='center', va='center', color=label_col,
            fontsize=fs_label, fontfamily='monospace', zorder=6)

    return fig_to_rgba(fig, (w, h))
