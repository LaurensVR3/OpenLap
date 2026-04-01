"""
Telemetry style: Inputs
========================
Vertical bar display approximating driver inputs from G-force data.

  Left column  : Throttle (green ↑) and Brake (red ↓) — from longitudinal G
  Right column : Steering left/right                  — from lateral G

Works best as a tall, narrow element placed at the left or right edge
of the video (e.g. ~10 % wide × 35 % tall).

Note: throttle/brake are inferred from longitudinal G, not pedal sensors.
      Lateral G is used as a steering proxy.

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "Inputs"
ELEMENT_TYPE = "telemetry"

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np


def _frac(val: float, max_g: float = 2.5) -> float:
    return min(1.0, max(0.0, abs(val) / max_g))


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    history      = data.get('history', [])
    lap_duration = data.get('lap_duration', 0.0)
    is_bike      = data.get('is_bike', False)

    if history:
        t_now = history[-1]['t']
        spd   = history[-1]['speed']
        gx    = history[-1]['gx']
        gy    = history[-1]['gy']
        lean  = history[-1].get('lean', 0.0)
    else:
        t_now = spd = gx = gy = lean = 0.0

    sc  = scale_factor(w, h, base_w=110, base_h=340)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.add_patch(FancyBboxPatch((0.03, 0.03), 0.94, 0.94,
        boxstyle="round,pad=0.02",
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.07), linewidth=1))

    fs_lbl = max(4, int(7  * sc))
    fs_spd = max(7, int(13 * sc))
    fs_tim = max(5, int(9  * sc))

    # Vertical layout (bottom → top):
    #  0.05-0.08 — progress bar
    #  0.13      — lap timer
    #  0.20      — km/h label
    #  0.27      — speed number
    #  0.34      — separator line
    #  0.38-0.92 — input bars + column headers at 0.94

    BAR_B = 0.38
    BAR_T = 0.92
    BAR_H = BAR_T - BAR_B
    MID_Y = BAR_B + BAR_H / 2

    THROT_L, THROT_R = 0.08, 0.44
    STEER_L, STEER_R = 0.56, 0.92
    BAR_BG = '#1a2530'

    # ── Throttle / Brake (left column) ────────────────────────────────────────
    ax.add_patch(plt.Rectangle((THROT_L, BAR_B), THROT_R - THROT_L, BAR_H,
                 facecolor=BAR_BG, zorder=2))
    ax.plot([THROT_L, THROT_R], [MID_Y, MID_Y],
            color='#3a4a5a', lw=0.8, zorder=3)

    if gx >= 0:
        frac = _frac(gx)
        ax.add_patch(plt.Rectangle(
            (THROT_L, MID_Y), THROT_R - THROT_L, BAR_H / 2 * frac,
            facecolor='#00cc55', alpha=0.85, zorder=4))
    else:
        frac = _frac(gx)
        ax.add_patch(plt.Rectangle(
            (THROT_L, MID_Y - BAR_H / 2 * frac), THROT_R - THROT_L, BAR_H / 2 * frac,
            facecolor='#ee3333', alpha=0.85, zorder=4))

    ax.text((THROT_L + THROT_R) / 2, BAR_T + 0.015, 'T/B',
            ha='center', va='bottom', color='#556677',
            fontsize=fs_lbl, fontfamily='monospace')

    # ── Steering (right column) ────────────────────────────────────────────────
    steer_val = lean if is_bike else gy
    steer_max = 40.0 if is_bike else 2.5
    steer_frac = min(1.0, max(0.0, abs(steer_val) / steer_max))
    steer_col  = '#ffaa00' if steer_val >= 0 else '#44aaff'

    ax.add_patch(plt.Rectangle((STEER_L, BAR_B), STEER_R - STEER_L, BAR_H,
                 facecolor=BAR_BG, zorder=2))
    ax.plot([STEER_L, STEER_R], [MID_Y, MID_Y],
            color='#3a4a5a', lw=0.8, zorder=3)

    bar_half = BAR_H / 2 * steer_frac
    if steer_val >= 0:
        ax.add_patch(plt.Rectangle(
            (STEER_L, MID_Y), STEER_R - STEER_L, bar_half,
            facecolor=steer_col, alpha=0.85, zorder=4))
    else:
        ax.add_patch(plt.Rectangle(
            (STEER_L, MID_Y - bar_half), STEER_R - STEER_L, bar_half,
            facecolor=steer_col, alpha=0.85, zorder=4))

    steer_label = 'lean' if is_bike else 'latG'
    ax.text((STEER_L + STEER_R) / 2, BAR_T + 0.015, steer_label,
            ha='center', va='bottom', color='#556677',
            fontsize=fs_lbl, fontfamily='monospace')

    # ── Separator line ─────────────────────────────────────────────────────────
    ax.plot([0.08, 0.92], [0.34, 0.34], color='#1e2e3e', lw=0.8)

    # ── Speed + timer (bottom zone: 0.05–0.33) ────────────────────────────────
    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m, s     = int(disp_t // 60), disp_t % 60
    tcol     = '#ffcc00' if finished else '#00ff88'

    ax.text(0.50, 0.28, f"{spd:.0f}",
            ha='center', va='center', color='white',
            fontsize=fs_spd, fontweight='bold', fontfamily='monospace')
    ax.text(0.50, 0.21, 'km/h',
            ha='center', va='center', color='#445566',
            fontsize=fs_lbl, fontfamily='monospace')
    ax.text(0.50, 0.14, f"{m:02d}:{s:05.2f}",
            ha='center', va='center', color=tcol,
            fontsize=fs_tim, fontweight='bold', fontfamily='monospace')

    prog    = min(1.0, t_now / lap_duration) if lap_duration > 0 else 0
    bar_col = '#ffcc00' if finished else '#00cc66'
    ax.add_patch(plt.Rectangle((0.08, 0.06), 0.84, 0.04,
                 facecolor='#1a2a3a', zorder=2))
    ax.add_patch(plt.Rectangle((0.08, 0.06), 0.84 * prog, 0.04,
                 facecolor=bar_col, zorder=3))

    return fig_to_rgba(fig, (w, h))
