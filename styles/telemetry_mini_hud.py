"""
Telemetry style: Mini HUD
==========================
Compact overlay showing speed and lap timer only.
Designed for minimal screen coverage — works well as a small square
in any corner of the video.

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "Mini HUD"
ELEMENT_TYPE = "telemetry"

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    history      = data.get('history', [])
    lap_duration = data.get('lap_duration', 0.0)
    is_bike      = data.get('is_bike', False)

    if history:
        t_now  = history[-1]['t']
        spd    = history[-1]['speed']
        gx     = history[-1]['gx']
        gy     = history[-1]['gy']
        lean   = history[-1].get('lean', 0.0)
    else:
        t_now = spd = gx = gy = lean = 0.0

    sc  = scale_factor(w, h, base_w=200, base_h=160)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.add_patch(FancyBboxPatch((0.04, 0.04), 0.92, 0.92,
        boxstyle="round,pad=0.02",
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.08), linewidth=1))

    fs_big  = max(18, min(int(44 * sc), int(w * 0.28)))
    fs_unit = max(7,  min(int(14 * sc), int(w * 0.09)))
    fs_sub  = max(5,  min(int(10 * sc), int(w * 0.07)))
    fs_tim  = max(8,  min(int(17 * sc), int(w * 0.12)))

    # Vertical layout (bottom → top):
    #  0.13 — G readout
    #  0.20-0.27 — progress bar
    #  0.46 — lap timer
    #  0.63 — km/h label
    #  0.80 — speed number

    ax.text(0.50, 0.80, f"{spd:.0f}",
            ha='center', va='center', color='white',
            fontsize=fs_big, fontweight='bold', fontfamily='monospace')
    ax.text(0.50, 0.63, 'km/h',
            ha='center', va='center', color='#5577aa',
            fontsize=fs_unit, fontfamily='monospace')

    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m, s     = int(disp_t // 60), disp_t % 60
    tcol     = '#ffcc00' if finished else '#00ff88'
    ax.text(0.50, 0.46, f"{m:02d}:{s:05.2f}",
            ha='center', va='center', color=tcol,
            fontsize=fs_tim, fontweight='bold', fontfamily='monospace')

    prog    = min(1.0, t_now / lap_duration) if lap_duration > 0 else 0
    bar_col = '#ffcc00' if finished else '#00cc66'
    ax.add_patch(plt.Rectangle((0.08, 0.20), 0.84, 0.07,
                 facecolor='#1a2a3a', zorder=2))
    ax.add_patch(plt.Rectangle((0.08, 0.20), 0.84 * prog, 0.07,
                 facecolor=bar_col, zorder=3))

    if is_bike:
        side_txt = f"{'↙' if lean<0 else '↘'} {abs(lean):.1f}°"
        side_col = '#ff9944'
    else:
        side_txt = f"{'↑' if gx>=0 else '↓'}{abs(gx):.2f}  {'←' if gy<0 else '→'}{abs(gy):.2f}"
        side_col = '#7799bb'
    ax.text(0.50, 0.11, side_txt,
            ha='center', va='center', color=side_col,
            fontsize=fs_sub, fontfamily='monospace')

    return fig_to_rgba(fig, (w, h))
