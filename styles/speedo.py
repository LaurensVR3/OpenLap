"""
Telemetry style: Speedometer
=============================
Circular gauge with a colour-coded arc, needle, large speed readout,
G-force indicators, and lap timer.

Works well as a roughly square element.

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "Speedometer"
ELEMENT_TYPE = "telemetry"

import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


_ANG_START = 220.0   # degrees CCW from +x axis (= bottom-left, = 0 km/h)
_ANG_SWEEP = 240.0   # total sweep (CW → decreasing angle)


def _spd_to_rad(speed: float, max_speed: float) -> float:
    frac = min(1.0, max(0.0, speed / max_speed))
    return math.radians(_ANG_START - frac * _ANG_SWEEP)


def _arc_xy(r: float, v_start: float, v_end: float, max_speed: float, n: int = 80):
    a1 = math.radians(_ANG_START - (v_start / max_speed) * _ANG_SWEEP)
    a2 = math.radians(_ANG_START - (v_end   / max_speed) * _ANG_SWEEP)
    angs = np.linspace(a1, a2, max(2, n))
    return r * np.cos(angs), r * np.sin(angs)


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    history      = data.get('history', [])
    lap_duration = data.get('lap_duration', 0.0)
    is_bike      = data.get('is_bike', False)
    max_speed    = float(data.get('max_speed', 300.0))

    if history:
        t_now   = history[-1]['t']
        spd     = history[-1]['speed']
        gx      = history[-1]['gx']
        gy      = history[-1]['gy']
        lean    = history[-1].get('lean', 0.0)
    else:
        t_now = spd = gx = gy = lean = 0.0

    sc  = scale_factor(w, h, base_w=360, base_h=400)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0.82))
    ax.set_xlim(-1.35, 1.35)
    ax.set_ylim(-1.20, 1.30)
    ax.set_aspect('equal')
    ax.axis('off')

    # ── Gauge arcs ────────────────────────────────────────────────────────────
    LW_TRACK = max(6, int(10 * sc))
    LW_LIVE  = max(5, int(9  * sc))
    R        = 0.90

    def draw_arc(v0, v1, col, lw, r=R):
        xs, ys = _arc_xy(r, v0, v1, max_speed)
        ax.plot(xs, ys, color=col, lw=lw,
                solid_capstyle='butt', zorder=2)

    # Track (grey background)
    draw_arc(0, max_speed, '#1a2a3a', LW_TRACK + 2)
    # Colour bands — proportional: 0-50% green, 50-75% yellow, 75-100% red
    b1, b2 = max_speed * 0.50, max_speed * 0.75
    draw_arc(0,  b1,         '#1a6633', LW_TRACK)
    draw_arc(b1, b2,         '#7a7a00', LW_TRACK)
    draw_arc(b2, max_speed,  '#882200', LW_TRACK)

    # Live speed fill
    spd_col = '#00ff88' if spd < b1 else ('#ffcc00' if spd < b2 else '#ff4444')
    if spd > 0:
        draw_arc(0, min(spd, max_speed), spd_col, LW_LIVE)

    # ── Tick marks and labels ─────────────────────────────────────────────────
    fs_tick  = max(5, int(8 * sc))
    tick_step = 20 if max_speed <= 100 else 50
    for v in range(0, int(max_speed) + 1, tick_step):
        ang  = _spd_to_rad(v, max_speed)
        r_in, r_out = 0.78, R + 0.06
        ax.plot([r_in  * math.cos(ang), r_out * math.cos(ang)],
                [r_in  * math.sin(ang), r_out * math.sin(ang)],
                color='#667788', lw=max(1, int(1.5*sc)), zorder=3)
        lx = 0.66 * math.cos(ang)
        ly = 0.66 * math.sin(ang)
        ax.text(lx, ly, str(v), ha='center', va='center',
                color='#8899aa', fontsize=fs_tick, fontfamily='monospace')

    # ── Needle ────────────────────────────────────────────────────────────────
    needle_ang = _spd_to_rad(min(spd, max_speed), max_speed)
    needle_len = 0.78
    ax.plot([0, needle_len * math.cos(needle_ang)],
            [0, needle_len * math.sin(needle_ang)],
            color=spd_col, lw=max(2, int(3*sc)),
            solid_capstyle='round', zorder=5)
    # Hub cap
    ax.scatter([0], [0], s=max(35, int(55*sc)), c='white', zorder=6)
    ax.scatter([0], [0], s=max(12, int(18*sc)), c='#111827', zorder=7)

    # ── Speed number ──────────────────────────────────────────────────────────
    fs_big  = max(16, min(int(34 * sc), int(w * 0.22)))
    fs_unit = max(8,  min(int(12 * sc), int(w * 0.07)))
    ax.text(0,  -0.12, f"{spd:.0f}",
            ha='center', va='center', color='white',
            fontsize=fs_big, fontweight='bold', fontfamily='monospace', zorder=4)
    ax.text(0,  -0.46, 'km/h',
            ha='center', va='center', color='#6688aa',
            fontsize=fs_unit, fontfamily='monospace')

    # ── G-force readout ───────────────────────────────────────────────────────
    fs_g = max(7, min(int(10 * sc), int(w * 0.06)))
    if is_bike:
        ax.text(0, -0.72,
                f"{'↙' if lean<0 else '↘'} {abs(lean):.1f}°",
                ha='center', va='center', color='#ff9944',
                fontsize=fs_g, fontfamily='monospace')
    else:
        gx_col = '#00ff88' if gx >= 0 else '#ff4444'
        ax.text(-0.55, -0.72,
                f"{'↑' if gx>=0 else '↓'} {abs(gx):.1f}G",
                ha='center', va='center', color=gx_col,
                fontsize=fs_g, fontfamily='monospace')
        ax.text( 0.55, -0.72,
                f"{'→' if gy>=0 else '←'} {abs(gy):.1f}G",
                ha='center', va='center', color='#ffcc44',
                fontsize=fs_g, fontfamily='monospace')

    # ── Lap timer ─────────────────────────────────────────────────────────────
    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m, s     = int(disp_t // 60), disp_t % 60
    tcol     = '#ffcc00' if finished else '#00ff88'
    fs_tim   = max(8, min(int(16 * sc), int(w * 0.10)))
    ax.text(0, -0.98, f"{m:02d}:{s:05.2f}",
            ha='center', va='center', color=tcol,
            fontsize=fs_tim, fontweight='bold', fontfamily='monospace')
    ax.text(0, -1.14, 'FINISHED' if finished else 'LAP TIME',
            ha='center', va='center',
            color='#aa8800' if finished else '#445566',
            fontsize=max(5, int(7 * sc)), fontfamily='monospace')

    return fig_to_rgba(fig, (w, h))
