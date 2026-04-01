"""
Telemetry style: Dashboard
===========================
Wide game-style HUD with three columns:

  Left   : Speed readout + throttle/brake bar (from longitudinal G)
  Centre : Lateral G bar + steering indicator
  Right  : Lap timer + progress bar

No rolling time-series graphs — focuses on the current moment only.
Works best as a wide, moderately tall element (e.g. 50 % × 28 % of video).

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "Dashboard"
ELEMENT_TYPE = "telemetry"

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np


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

    sc  = scale_factor(w, h, base_w=720, base_h=200)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    bg = fig.add_axes([0, 0, 1, 1])
    bg.set_facecolor((0, 0, 0, 0))
    bg.set_xlim(0, 1); bg.set_ylim(0, 1)
    bg.axis('off')
    bg.add_patch(FancyBboxPatch((0.005, 0.04), 0.990, 0.92,
        boxstyle="round,pad=0.01",
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.06), linewidth=1))

    fs_big  = max(16, min(int(38 * sc), int(w * 0.10)))
    fs_med  = max(8,  min(int(16 * sc), int(w * 0.045)))
    fs_sml  = max(6,  min(int(11 * sc), int(w * 0.030)))
    fs_tiny = max(5,  min(int(8  * sc), int(w * 0.022)))

    PAD   = 0.015
    COL1R = 0.30
    COL2L = COL1R + PAD * 2
    COL2R = 0.68
    COL3L = COL2R + PAD * 2

    # ── LEFT: Speed + Throttle/Brake bar ──────────────────────────────────────
    ax_l = fig.add_axes([PAD, 0.05, COL1R - PAD * 2, 0.90])
    ax_l.set_facecolor((0, 0, 0, 0)); ax_l.patch.set_alpha(0); ax_l.axis('off')
    ax_l.set_xlim(0, 1); ax_l.set_ylim(0, 1)

    # Speed number
    ax_l.text(0.50, 0.75, f"{spd:.0f}",
              ha='center', va='center', color='white',
              fontsize=fs_big, fontweight='bold', fontfamily='monospace')
    ax_l.text(0.50, 0.53, 'km/h',
              ha='center', va='center', color='#5577aa',
              fontsize=fs_sml, fontfamily='monospace')

    # Throttle/Brake horizontal bar (centre-out)
    BAR_L, BAR_R = 0.06, 0.94
    BAR_Y, BAR_H = 0.28, 0.14
    BAR_BG = '#1a2530'
    ax_l.add_patch(plt.Rectangle((BAR_L, BAR_Y), BAR_R - BAR_L, BAR_H,
                   facecolor=BAR_BG, zorder=2))
    ax_l.plot([(BAR_L + BAR_R) / 2, (BAR_L + BAR_R) / 2], [BAR_Y, BAR_Y + BAR_H],
              color='#3a4a5a', lw=0.8, zorder=3)
    bar_half  = (BAR_R - BAR_L) / 2
    bar_mid   = (BAR_L + BAR_R) / 2
    MAX_G     = 2.5
    if gx >= 0:
        frac = min(1.0, gx / MAX_G)
        ax_l.add_patch(plt.Rectangle((bar_mid, BAR_Y), bar_half * frac, BAR_H,
                       facecolor='#00cc55', alpha=0.85, zorder=4))
        val_col, val_sym = '#00cc55', '↑'
    else:
        frac = min(1.0, abs(gx) / MAX_G)
        ax_l.add_patch(plt.Rectangle((bar_mid - bar_half * frac, BAR_Y), bar_half * frac, BAR_H,
                       facecolor='#ee3333', alpha=0.85, zorder=4))
        val_col, val_sym = '#ee3333', '↓'
    ax_l.text(0.50, BAR_Y - 0.04, f"{val_sym} {abs(gx):.2f} G lon",
              ha='center', va='top', color=val_col,
              fontsize=fs_tiny, fontfamily='monospace')

    # ── CENTRE: Lateral G / steering ──────────────────────────────────────────
    ax_c = fig.add_axes([COL2L, 0.05, COL2R - COL2L, 0.90])
    ax_c.set_facecolor((0, 0, 0, 0)); ax_c.patch.set_alpha(0); ax_c.axis('off')
    ax_c.set_xlim(-1, 1); ax_c.set_ylim(0, 1)

    steer_val = lean if is_bike else gy
    steer_max = 40.0 if is_bike else 2.5
    steer_frac = min(1.0, abs(steer_val) / steer_max)
    steer_col  = '#ffaa00' if steer_val > 0 else '#44aaff'
    steer_dir  = '→' if steer_val > 0 else '←'
    steer_unit = '°' if is_bike else 'G'

    # Label
    ax_c.text(0, 0.92, 'LATERAL' if not is_bike else 'LEAN',
              ha='center', va='top', color='#445566',
              fontsize=fs_tiny, fontfamily='monospace')

    # G-bar track (full width)
    ax_c.add_patch(plt.Rectangle((-0.92, 0.60), 1.84, 0.20,
                   facecolor='#1a2530', zorder=2))
    ax_c.plot([0, 0], [0.58, 0.82], color='#3a4a5a', lw=0.9, zorder=3)

    # Fill bar from centre
    fill_w = 0.92 * steer_frac
    if steer_val > 0:
        ax_c.add_patch(plt.Rectangle((0, 0.60), fill_w, 0.20,
                       facecolor=steer_col, alpha=0.85, zorder=4))
    elif steer_val < 0:
        ax_c.add_patch(plt.Rectangle((-fill_w, 0.60), fill_w, 0.20,
                       facecolor=steer_col, alpha=0.85, zorder=4))

    # G ticks at ±1G / ±2G  (or ±15° / ±30° for bike)
    tick_vals = [15, 30] if is_bike else [1.0, 2.0]
    for tv in tick_vals:
        tx = 0.92 * min(1.0, tv / steer_max)
        for sign in (-1, 1):
            ax_c.plot([sign * tx, sign * tx], [0.57, 0.83],
                      color='#2a3a4a', lw=0.7, zorder=3)

    # Value readout — sits between G-bar and speed trace
    ax_c.text(0, 0.52, f"{steer_dir} {abs(steer_val):.1f}{steer_unit}",
              ha='center', va='center', color=steer_col,
              fontsize=fs_sml, fontweight='bold', fontfamily='monospace')

    # Speed trace mini-bar
    if len(history) >= 2:
        win = history[-min(40, len(history)):]
        t0, t1 = win[0]['t'], win[-1]['t']
        dt = t1 - t0 if t1 > t0 else 1.0
        max_spd = max(p['speed'] for p in win)
        max_spd = max(max_spd * 1.1, 30.0)
        BG_Y, BG_H = 0.20, 0.24
        ax_c.add_patch(plt.Rectangle((-0.92, BG_Y), 1.84, BG_H,
                       facecolor='#111820', zorder=2))
        xs = [((p['t'] - t0) / dt) * 1.84 - 0.92 for p in win]
        ys = [BG_Y + (p['speed'] / max_spd) * BG_H for p in win]
        ax_c.plot(xs, ys, color='#00dcff', lw=max(1.0, 1.5 * sc), zorder=3)
        ax_c.text(0, BG_Y - 0.06, '10s speed',
                  ha='center', va='top', color='#2a3a4a',
                  fontsize=fs_tiny, fontfamily='monospace')

    # ── RIGHT: Lap timer ───────────────────────────────────────────────────────
    ax_r = fig.add_axes([COL3L, 0.05, 1.0 - COL3L - PAD, 0.90])
    ax_r.set_facecolor((0, 0, 0, 0)); ax_r.patch.set_alpha(0); ax_r.axis('off')
    ax_r.set_xlim(0, 1); ax_r.set_ylim(0, 1)

    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m, s     = int(disp_t // 60), disp_t % 60
    tcol     = '#ffcc00' if finished else '#00ff88'
    fs_timer = max(10, min(int(22 * sc), int(w * 0.06)))

    ax_r.text(0.50, 0.75, f"{m:02d}:{s:05.2f}",
              ha='center', va='center', color=tcol,
              fontsize=fs_timer, fontweight='bold', fontfamily='monospace')
    ax_r.text(0.50, 0.53, 'FINISHED' if finished else 'LAP TIME',
              ha='center', va='center',
              color='#aa8800' if finished else '#445566',
              fontsize=fs_tiny, fontfamily='monospace')

    # Progress bar
    prog    = min(1.0, t_now / lap_duration) if lap_duration > 0 else 0
    bar_col = '#ffcc00' if finished else '#00cc66'
    ax_r.add_patch(plt.Rectangle((0.06, 0.32), 0.88, 0.12,
                   facecolor='#1a2a3a', zorder=2))
    ax_r.add_patch(plt.Rectangle((0.06, 0.32), 0.88 * prog, 0.12,
                   facecolor=bar_col, zorder=3))

    # Lap elapsed seconds
    ax_r.text(0.50, 0.22, f"{t_now:.1f}s",
              ha='center', va='center', color='#334455',
              fontsize=fs_tiny, fontfamily='monospace')

    return fig_to_rgba(fig, (w, h))
