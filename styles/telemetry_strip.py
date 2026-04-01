"""
Telemetry style: Strip
======================
Horizontal bar with speed readout (left), rolling time-series graphs
for speed / longitudinal-G / lateral-G (centre), and lap timer (right).

Works best as a wide, short element (e.g. 40 % × 22 % of video).

ELEMENT_TYPE : "telemetry"
Data keys    : history, lap_duration, is_bike
"""
STYLE_NAME   = "Strip"
ELEMENT_TYPE = "telemetry"

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    history      = data.get('history', [])
    lap_duration = data.get('lap_duration', 0.0)
    is_bike      = data.get('is_bike', False)

    sc  = scale_factor(w, h, base_w=800, base_h=180)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    bg = fig.add_axes([0, 0, 1, 1])
    bg.set_xlim(0, 1); bg.set_ylim(0, 1)
    bg.add_patch(plt.Rectangle((0, 0), 1, 1,
        facecolor=(0, 0, 0, 0.70), transform=bg.transAxes, zorder=0))
    bg.axis('off')

    PAD   = 0.015
    SPD_W = 0.14
    TIM_W = 0.13
    GR_L  = PAD + SPD_W + PAD
    GR_W  = 1.0 - GR_L - TIM_W - PAD
    GR_B  = 0.10
    GR_H  = 0.82
    SUB_H = GR_H / 3
    _spd_col_px = SPD_W * w
    _tim_col_px = TIM_W * w

    if history:
        times  = [p['t']     for p in history]
        speeds = [p['speed'] for p in history]
        gxs    = [p['gx']    for p in history]
        gys    = [p['gy']    for p in history]
        t_now   = times[-1]; spd_now = speeds[-1]
        gx_now  = gxs[-1];   gy_now  = gys[-1]
    else:
        times = speeds = gxs = gys = [0.0]
        t_now = spd_now = gx_now = gy_now = 0.0

    WIN   = 10.0
    t_min = max(0.0, t_now - WIN)
    fs_lbl = max(5, int(7 * sc))

    def add_graph(bottom, ylabel, y_vals, colors_pos, colors_neg=None,
                  zero_line=True, y_min=None, y_max=None):
        ax = fig.add_axes([GR_L, GR_B + bottom, GR_W, SUB_H * 0.87])
        ax.set_facecolor((0, 0, 0, 0)); ax.patch.set_alpha(0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xlim(t_min, t_min + WIN)
        ylo = (y_min if y_min is not None else
               min(min(y_vals) * 1.2, -0.1) if zero_line else 0)
        yhi = (y_max if y_max is not None else max(max(y_vals) * 1.2, 0.1))
        ax.set_ylim(ylo, yhi)
        ax.set_xticks([]); ax.set_yticks([])
        if zero_line:
            ax.axhline(0, color='white', lw=0.5, alpha=0.2)
        if colors_neg:
            ax.fill_between(times, [max(0, v) for v in y_vals], alpha=0.30, color=colors_pos)
            ax.fill_between(times, [min(0, v) for v in y_vals], alpha=0.30, color=colors_neg)
            ax.plot(times, y_vals, color=colors_pos, lw=1.3, alpha=0.9)
        else:
            ax.fill_between(times, y_vals, alpha=0.25, color=colors_pos)
            ax.plot(times, y_vals, color=colors_pos, lw=1.5)
        ax.axvline(t_now, color='white', lw=0.9, alpha=0.35)
        ax.text(-0.005, 0.5, ylabel, transform=ax.transAxes,
                color='#7799bb', fontsize=fs_lbl, va='center', ha='right',
                fontfamily='monospace')

    add_graph(2*SUB_H, 'km/h', speeds, '#00dcff', zero_line=False,
              y_min=0, y_max=max(max(speeds)*1.15, 30))
    add_graph(1*SUB_H, 'lon G', gxs, '#00ff88', colors_neg='#ff4444')
    if is_bike:
        lean_vals = [p.get('lean', 0.0) for p in history] if history else [0.0]
        mx = max(max(abs(v) for v in lean_vals), 5.0) * 1.15
        add_graph(0, 'lean°', lean_vals, '#ff9944', colors_neg='#ff9944',
                  y_min=-mx, y_max=mx)
    else:
        add_graph(0, 'lat G', gys, '#ffcc44', colors_neg='#ffcc44')

    # Speed readout
    sp = fig.add_axes([PAD, 0.05, SPD_W, 0.90])
    sp.set_facecolor((0,0,0,0)); sp.patch.set_alpha(0); sp.axis('off')
    sp.set_xlim(0,1); sp.set_ylim(0,1)
    fs_big  = max(14, min(int(36*sc), int(_spd_col_px / 2.5)))
    fs_unit = max(7,  min(int(13*sc), int(_spd_col_px / 5.0)))
    fs_g    = max(6,  min(int(10*sc), int(_spd_col_px / 6.0)))
    sp.text(0.5, 0.74, f"{spd_now:.0f}", ha='center', va='center',
            color='white', fontsize=fs_big, fontweight='bold', fontfamily='monospace')
    sp.text(0.5, 0.46, 'km/h', ha='center', va='center',
            color='#6688aa', fontsize=fs_unit, fontfamily='monospace')
    lon_col = '#00ff88' if gx_now >= 0 else '#ff4444'
    sp.text(0.5, 0.26, f"{'↑' if gx_now>=0 else '↓'} {abs(gx_now):.2f}G",
            ha='center', va='center', color=lon_col, fontsize=fs_g, fontfamily='monospace')
    if is_bike:
        lean_now = history[-1].get('lean', 0.0) if history else 0.0
        sp.text(0.5, 0.12, f"{'↙' if lean_now<0 else '↘'} {abs(lean_now):.1f}°",
                ha='center', va='center', color='#ff9944', fontsize=fs_g, fontfamily='monospace')
    else:
        sp.text(0.5, 0.12, f"{'←' if gy_now<0 else '→'} {abs(gy_now):.2f}G",
                ha='center', va='center', color='#ffcc44', fontsize=fs_g, fontfamily='monospace')

    # Lap timer
    tm = fig.add_axes([1.0 - TIM_W, 0.05, TIM_W, 0.90])
    tm.set_facecolor((0,0,0,0)); tm.patch.set_alpha(0); tm.axis('off')
    tm.set_xlim(0,1); tm.set_ylim(0,1)
    finished = lap_duration > 0 and t_now >= lap_duration - 0.05
    disp_t   = min(t_now, lap_duration) if lap_duration > 0 else t_now
    m_t, s_t = int(disp_t // 60), disp_t % 60
    timer_col = '#ffcc00' if finished else '#00ff88'
    fs_timer  = max(8, min(int(19*sc), int(_tim_col_px / 6.7)))
    tm.text(0.5, 0.72, f"{m_t:02d}:{s_t:05.2f}", ha='center', va='center',
            color=timer_col, fontsize=fs_timer, fontweight='bold', fontfamily='monospace')
    prog    = min(1.0, t_now / lap_duration) if lap_duration > 0 else 0
    bar_col = '#ffcc00' if finished else '#00cc66'
    tm.add_patch(plt.Rectangle((0.05, 0.34), 0.90, 0.09, facecolor='#1a2a3a', zorder=2))
    tm.add_patch(plt.Rectangle((0.05, 0.34), 0.90*prog, 0.09, facecolor=bar_col, zorder=3))
    tm.text(0.5, 0.16, 'FINISHED' if finished else 'LAP TIME', ha='center', va='center',
            color='#aa8800' if finished else '#445566',
            fontsize=max(5, min(int(8*sc), int(_tim_col_px/8.0))), fontfamily='monospace')

    return fig_to_rgba(fig, (w, h))
