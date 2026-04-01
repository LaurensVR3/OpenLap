"""
Telemetry style: G-Force Crosshair
====================================
Lateral G (X-axis) vs longitudinal G (Y-axis) plotted as a moving dot
on a crosshair grid with G-rings. A fading trail shows the recent history.
Speed and lap timer are displayed below the crosshair.

Works well as a roughly square element.

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "G-Force Crosshair"
ELEMENT_TYPE = "telemetry"

import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    history      = data.get('history', [])
    lap_duration = data.get('lap_duration', 0.0)
    is_bike      = data.get('is_bike', False)

    if history:
        t_now   = history[-1]['t']
        spd_now = history[-1]['speed']
        gx_now  = history[-1]['gx']   # longitudinal (+ = accel, − = brake)
        gy_now  = history[-1]['gy']   # lateral      (+ = right, − = left)
        lean    = history[-1].get('lean', 0.0)
    else:
        t_now = spd_now = gx_now = gy_now = lean = 0.0

    sc  = scale_factor(w, h, base_w=360, base_h=420)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    # ── Crosshair axes (top ~72 % of element) ────────────────────────────────
    CROSS_H = 0.72
    ax = fig.add_axes([0.06, 1 - CROSS_H, 0.88, CROSS_H * 0.97])
    ax.set_facecolor((0, 0, 0, 0.80))
    ax.set_aspect('equal')
    MAX_G = 3.0
    ax.set_xlim(-MAX_G, MAX_G)
    ax.set_ylim(-MAX_G, MAX_G)
    ax.axis('off')

    # Outer boundary circle
    outer = plt.Circle((0, 0), MAX_G * 0.96,
                        fill=False, color='#2a3a4a', lw=1.5)
    ax.add_patch(outer)

    # G-rings at 1 G and 2 G
    for r, lbl in [(1.0, '1G'), (2.0, '2G')]:
        ring = plt.Circle((0, 0), r, fill=False,
                           color='#2a3a4a', lw=0.8, linestyle='--', alpha=0.7)
        ax.add_patch(ring)
        ax.text(0.04, r + 0.08, lbl, ha='left', va='bottom',
                color='#3a5060', fontsize=max(5, int(7 * sc)),
                fontfamily='monospace')

    # Crosshair lines
    ax.axhline(0, color='#2a3a50', lw=1.0, alpha=0.9, zorder=2)
    ax.axvline(0, color='#2a3a50', lw=1.0, alpha=0.9, zorder=2)

    # Axis labels
    fs_ax = max(5, int(7 * sc))
    ax.text( MAX_G * 0.92, 0.12, '→', ha='right', va='bottom',
             color='#445566', fontsize=fs_ax)
    ax.text(-MAX_G * 0.92, 0.12, '←', ha='left',  va='bottom',
             color='#445566', fontsize=fs_ax)
    ax.text(0.08,  MAX_G * 0.88, '↑', ha='left', va='top',
             color='#445566', fontsize=fs_ax)
    ax.text(0.08, -MAX_G * 0.88, '↓', ha='left', va='bottom',
             color='#445566', fontsize=fs_ax)

    # ── Fading trail ─────────────────────────────────────────────────────────
    TRAIL = min(60, len(history))
    if TRAIL > 1:
        trail = history[-TRAIL:]
        xs     = [p['gy'] for p in trail]
        ys     = [p['gx'] for p in trail]
        alphas = np.linspace(0.04, 0.55, len(trail))
        sizes  = np.linspace(6, 30, len(trail))
        for x, y, a, s in zip(xs, ys, alphas, sizes):
            ax.scatter([x], [y], s=s, c=[[0.2, 0.7, 1.0, a]],
                       linewidths=0, zorder=3)

    # ── Current position dot ──────────────────────────────────────────────────
    if gx_now < -0.3:
        dot_col = '#ff4444'   # braking
    elif abs(gy_now) > 1.2:
        dot_col = '#ffcc00'   # heavy cornering
    else:
        dot_col = '#00ff88'   # acceleration / light load
    ax.scatter([gy_now], [gx_now],
               s=max(70, int(110 * sc)), c=[dot_col],
               edgecolors='white', linewidths=max(1, int(1.5 * sc)), zorder=5)

    # ── Bottom bar: speed + timer ─────────────────────────────────────────────
    bot = fig.add_axes([0, 0, 1, 1 - CROSS_H])
    bot.set_facecolor((0, 0, 0, 0.80)); bot.patch.set_alpha(1.0)
    bot.axis('off')
    bot.set_xlim(0, 1); bot.set_ylim(0, 1)

    fs_spd = max(12, min(int(26 * sc), int(w * 0.12)))
    fs_lbl = max(6,  int(10 * sc))
    fs_sub = max(5,  int(7  * sc))

    # Speed (left half)
    bot.text(0.25, 0.68, f"{spd_now:.0f}",
             ha='center', va='center', color='white',
             fontsize=fs_spd, fontweight='bold', fontfamily='monospace')
    bot.text(0.25, 0.22, 'km/h',
             ha='center', va='center', color='#6688aa',
             fontsize=fs_lbl, fontfamily='monospace')

    # G-force secondary readout (left half, tiny)
    if is_bike:
        bot.text(0.25, 0.02, f"{'↙' if lean<0 else '↘'} {abs(lean):.0f}°",
                 ha='center', va='bottom', color='#ff9944',
                 fontsize=fs_sub, fontfamily='monospace')
    else:
        bot.text(0.25, 0.02,
                 f"{'↑' if gx_now>=0 else '↓'}{abs(gx_now):.1f}  "
                 f"{'←' if gy_now<0 else '→'}{abs(gy_now):.1f}",
                 ha='center', va='bottom', color='#7799bb',
                 fontsize=fs_sub, fontfamily='monospace')

    # Lap timer (right half)
    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m, s     = int(disp_t // 60), disp_t % 60
    tcol     = '#ffcc00' if finished else '#00ff88'
    fs_tim   = max(8, min(int(18 * sc), int(w * 0.10)))
    bot.text(0.75, 0.68, f"{m:02d}:{s:05.2f}",
             ha='center', va='center', color=tcol,
             fontsize=fs_tim, fontweight='bold', fontfamily='monospace')
    bot.text(0.75, 0.22, 'FINISHED' if finished else 'LAP TIME',
             ha='center', va='center',
             color='#aa8800' if finished else '#445566',
             fontsize=fs_sub, fontfamily='monospace')

    # Progress bar
    prog    = min(1.0, t_now / lap_duration) if lap_duration > 0 else 0
    bar_col = '#ffcc00' if finished else '#00cc66'
    bot.add_patch(plt.Rectangle((0.52, 0.08), 0.44, 0.10,
                  facecolor='#1a2a3a', zorder=2))
    bot.add_patch(plt.Rectangle((0.52, 0.08), 0.44 * prog, 0.10,
                  facecolor=bar_col, zorder=3))

    return fig_to_rgba(fig, (w, h))
