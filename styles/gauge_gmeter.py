"""
Gauge style: G-Meter
====================
2-D circular G-force display.  Plots the car's combined longitudinal (gx)
and lateral (gy) G-loading as a moving dot inside a calibrated circle.

A fading trace shows the last few seconds of history.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "G-Meter"
Data keys    : value (gx now), value_gy (gy now),
               history_vals (gx list), history_gy (gy list),
               min_val, max_val, _tc
"""
STYLE_NAME   = 'G-Meter'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import numpy as np
from matplotlib.patches import Circle, FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba

    gx_now   = float(data.get('value',       0.0))
    gy_now   = float(data.get('value_gy',    0.0))
    gx_hist  = list(data.get('history_vals', [gx_now]))
    gy_hist  = list(data.get('history_gy',   [gy_now]))
    g_range  = float(data.get('max_val',     3.0))

    T         = data.get('_tc', {})
    bg_rgba   = T.get('bg_rgba',       (0, 0, 0, 0.72))
    bg_edge   = T.get('bg_edge_rgba',  (1, 1, 1, 0.07))
    label_col = T.get('label',         '#445566')
    acc_col   = T.get('gauge_acc',     '#4f8ef7')
    warn_col  = T.get('fill_hi',       '#ff4422')

    size  = min(w, h)
    dpi   = 100
    fig   = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    # Background rounded rect
    ax_bg = fig.add_axes([0, 0, 1, 1])
    ax_bg.set_facecolor((0, 0, 0, 0))
    ax_bg.axis('off')
    ax_bg.add_patch(FancyBboxPatch(
        (0.01, 0.01), 0.98, 0.98,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    # Main axes (square, centred) — extra bottom margin keeps the G readout
    # from overlapping the ACCEL label.
    m_side = 0.14
    m_top  = 0.08
    m_bot  = 0.22
    ax = fig.add_axes([m_side, m_bot, 1 - 2 * m_side, 1 - m_bot - m_top])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_aspect('equal')
    ax.set_xlim(-g_range * 1.20, g_range * 1.20)
    ax.set_ylim(-g_range * 1.20, g_range * 1.20)
    ax.axis('off')

    # Grid circles
    for r in [g_range / 3, g_range * 2 / 3, g_range]:
        ax.add_patch(Circle((0, 0), r, fill=False,
                             edgecolor='#ffffff', linewidth=0.5, alpha=0.15, zorder=1))
    # Cross-hairs
    ax.axhline(0, color='#ffffff', lw=0.5, alpha=0.20, zorder=1)
    ax.axvline(0, color='#ffffff', lw=0.5, alpha=0.20, zorder=1)

    # Ring labels
    fs = max(4, int(size * 0.050))
    for r, label in [(g_range / 3, f'{g_range/3:.0f}G'),
                     (g_range * 2/3, f'{g_range*2/3:.0f}G'),
                     (g_range, f'{g_range:.0f}G')]:
        ax.text(r * 0.05, r + g_range * 0.03, label,
                ha='center', va='bottom', color='#ffffff',
                alpha=0.28, fontsize=fs, fontfamily='sans-serif')

    # Axis labels — positioned just inside the plot boundary so they stay clear
    fs_ax = max(5, int(size * 0.060))
    ax.text(0,  g_range * 1.12, 'BRAKE',  ha='center', va='bottom',
            color=label_col, fontsize=fs_ax, fontfamily='sans-serif')
    ax.text(0, -g_range * 1.12, 'ACCEL',  ha='center', va='top',
            color=label_col, fontsize=fs_ax, fontfamily='sans-serif')
    ax.text( g_range * 1.12, 0, 'R',      ha='left',   va='center',
            color=label_col, fontsize=fs_ax, fontfamily='sans-serif')
    ax.text(-g_range * 1.12, 0, 'L',      ha='right',  va='center',
            color=label_col, fontsize=fs_ax, fontfamily='sans-serif')

    # Current G readout — compact, shown at the bottom of the bg area
    fs_val = max(5, int(size * 0.065))
    ax_bg.text(0.50, 0.06,
               f'{gx_now:+.2f}  /  {gy_now:+.2f}',
               ha='center', va='bottom', color='#ccccdd',
               fontsize=fs_val, fontfamily='sans-serif',
               transform=ax_bg.transAxes)

    # History trace (fading)
    n_trace = min(60, len(gx_hist))
    if n_trace >= 2:
        xs = np.array(gx_hist[-n_trace:], dtype=float)
        ys = np.array(gy_hist[-n_trace:], dtype=float) if len(gy_hist) >= n_trace else \
             np.zeros(n_trace, dtype=float)
        alphas = np.linspace(0.05, 0.50, n_trace)
        for i in range(n_trace - 1):
            ax.plot(xs[i:i+2], ys[i:i+2],
                    color=acc_col, lw=max(0.8, size * 0.006),
                    alpha=float(alphas[i]), solid_capstyle='round', zorder=3)

    # Current dot — colour by magnitude
    g_mag = min(np.hypot(gx_now, gy_now) / g_range, 1.0)
    dot_col = warn_col if g_mag > 0.80 else acc_col
    dot_ms  = max(5, int(size * 0.09))
    ax.plot(gx_now, gy_now, 'o',
            color=dot_col, ms=dot_ms,
            mec='white', mew=max(0.8, size * 0.005),
            zorder=5,
            path_effects=[pe.withStroke(linewidth=dot_ms * 0.4, foreground='black')])

    return fig_to_rgba(fig, (w, h))
