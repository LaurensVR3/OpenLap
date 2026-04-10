"""
Gauge style: Lean
=================
Motorcycle lean angle visualisation.  Shows a silhouette of a bike tilted
to the current lean angle, with the angle value and a balance bar below.

Best used for the 'lean' channel in motorcycle (is_bike) mode, but will
render for any channel (treating it as a tilt angle in degrees).

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Lean"
Data keys    : value, label, unit, min_val, max_val, symmetric
"""
STYLE_NAME   = 'Lean'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
from matplotlib.transforms import Affine2D


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value   = data.get('value',   0.0)
    label   = data.get('label',   'Lean')
    unit    = data.get('unit',    '°')
    mn      = data.get('min_val', -60.0)
    mx      = data.get('max_val',  60.0)

    T            = data.get('_tc', {})
    bg_rgba      = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge      = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    ground_col   = T.get('ground',       '#2a3a4a')
    bike_body    = T.get('bike_body',    'white')
    bike_parts   = T.get('bike_parts',   '#aabbcc')
    rider_body   = T.get('rider_body',   '#667799')
    rider_head   = T.get('rider_head',   '#8899aa')
    lean_safe    = T.get('lean_safe',    '#00cc66')
    lean_warn    = T.get('lean_warn',    '#ffaa00')
    lean_danger  = T.get('lean_danger',  '#ff3333')
    label_col    = T.get('label',        '#445566')

    sc  = scale_factor(w, h, base_w=140, base_h=180)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(-1.0, 1.0); ax.set_ylim(-1.0, 1.0)
    ax.set_aspect('equal')
    ax.axis('off')

    ax.add_patch(plt.Circle((0, 0), 0.98,
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    fs_val   = max(8,  min(int(24 * sc), int(w * 0.18)))
    fs_label = max(5,  min(int(8  * sc), int(w * 0.07)))

    value    = max(-90.0, min(90.0, value))
    lean_rad = np.radians(-value)

    def rot(pts):
        c, s = np.cos(lean_rad), np.sin(lean_rad)
        return [(x * c - y * s, x * s + y * c) for x, y in pts]

    body = rot([(-0.04, -0.30), (-0.04, 0.28)])
    ax.plot([body[0][0], body[1][0]], [body[0][1], body[1][1]],
            color=bike_body, lw=max(2.5, 4.0 * sc), solid_capstyle='round', zorder=3)

    rear_cx, rear_cy  = rot([(0.18,  -0.38)])[0]
    front_cx, front_cy = rot([(-0.22, -0.30)])[0]

    for cx, cy, r in [(rear_cx, rear_cy, 0.20), (front_cx, front_cy, 0.19)]:
        wheel = plt.Circle((cx, cy), r * sc * 0.6,
                            fill=False, edgecolor=bike_parts, linewidth=max(1.5, 2.5 * sc))
        ax.add_patch(wheel)

    fork_top = rot([(-0.06, 0.22)])[0]
    ax.plot([fork_top[0], front_cx], [fork_top[1], front_cy],
            color=bike_parts, lw=max(1.5, 2.5 * sc), zorder=3)

    rider_pts = rot([(0.0, 0.26)])
    rider_x, rider_y = rider_pts[0]
    head_x, head_y   = rot([(0.0, 0.50)])[0]
    ax.add_patch(plt.Circle((rider_x, rider_y), 0.10 * sc * 0.7,
                             facecolor=rider_body, alpha=0.90, zorder=4))
    ax.add_patch(plt.Circle((head_x, head_y),  0.07 * sc * 0.7,
                             facecolor=rider_head, alpha=0.90, zorder=4))

    ax.plot([-0.85, 0.85], [-0.72, -0.72],
            color=ground_col, lw=max(0.8, 1.2 * sc), zorder=1)

    abs_lean = abs(value)
    if abs_lean < 20:
        val_col = lean_safe
    elif abs_lean < 40:
        val_col = lean_warn
    else:
        val_col = lean_danger

    direction = 'R' if value > 0 else ('L' if value < 0 else '')
    val_str   = f"{direction}{abs_lean:.1f}{unit}"

    ax.text(0, -0.82, val_str,
            ha='center', va='center', color=val_col,
            fontsize=fs_val, fontweight='bold', fontfamily='sans-serif', zorder=6)
    ax.text(0, 0.85, label.upper(),
            ha='center', va='center', color=label_col,
            fontsize=fs_label, fontfamily='sans-serif', zorder=6)

    return fig_to_rgba(fig, (w, h))
