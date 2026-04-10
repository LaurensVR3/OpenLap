"""
Gauge style: Compare
====================
Dual-trace line chart for a single channel.  The current lap (solid
coloured line) is plotted alongside the reference lap (dashed grey line),
both aligned by track position via the shared history index.

If no reference data is available the gauge falls back to a plain line
chart identical to the Line style.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Compare"
Data keys    : value, history_vals, ref_history_vals, label, unit,
               min_val, max_val, symmetric, channel, _tc
"""
STYLE_NAME   = 'Compare'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value     = data.get('value',            0.0)
    hist      = data.get('history_vals',     [value])
    ref_hist  = data.get('ref_history_vals', [])
    label     = data.get('label',            '')
    unit      = data.get('unit',             '')
    mn        = data.get('min_val',          0.0)
    mx        = data.get('max_val',          100.0)
    symmetric = data.get('symmetric',        False)
    channel   = data.get('channel',         '')

    T         = data.get('_tc', {})
    bg_rgba   = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge   = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    fill_pos  = T.get('fill_pos',     '#ffaa00')
    fill_neg  = T.get('fill_neg',     '#44aaff')
    fill_lo   = T.get('fill_lo',      '#00ccff')
    fill_hi   = T.get('fill_hi',      '#ff4422')
    label_col = T.get('label',        '#445566')
    unit_col  = T.get('unit',         '#5577aa')
    track_col = T.get('track',        '#1a2530')

    sc  = scale_factor(w, h, base_w=220, base_h=100)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax_bg = fig.add_axes([0, 0, 1, 1])
    ax_bg.set_facecolor((0, 0, 0, 0))
    ax_bg.axis('off')
    ax_bg.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    fs_label = max(5, min(int(9  * sc), int(w * 0.07)))
    fs_val   = max(6, min(int(13 * sc), int(w * 0.10)))
    fs_unit  = max(4, min(int(7  * sc), int(w * 0.06)))
    fs_leg   = max(4, min(int(6  * sc), int(w * 0.055)))

    rng = mx - mn if mx != mn else 1.0
    if symmetric:
        line_col = fill_pos if value >= 0 else fill_neg
    else:
        frac     = max(0.0, min(1.0, (value - mn) / rng))
        line_col = fill_hi if frac > 0.80 else fill_lo

    ref_col = '#999999'

    # ── Chart axes ────────────────────────────────────────────────────────────
    ax = fig.add_axes([0.04, 0.20, 0.70, 0.62])
    ax.set_facecolor((0, 0, 0, 0))
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    n        = min(120, len(hist))
    cur_vals = list(hist[-n:])
    xs       = np.arange(len(cur_vals))

    pad = rng * 0.10
    ax.set_xlim(0, max(1, len(cur_vals) - 1))
    ax.set_ylim(mn - pad, mx + pad)

    if symmetric:
        ax.axhline(0.0, color=track_col, lw=0.6, zorder=1)

    # Reference trace (dashed grey, drawn first so current trace is on top)
    has_ref = bool(ref_hist) and len(ref_hist) >= 2
    if has_ref:
        ref_vals = list(ref_hist[-n:])
        if len(ref_vals) < len(cur_vals):
            ref_vals = [ref_vals[0]] * (len(cur_vals) - len(ref_vals)) + ref_vals
        elif len(ref_vals) > len(cur_vals):
            ref_vals = ref_vals[-len(cur_vals):]
        ref_ys = np.array(ref_vals, dtype=float)
        ax.plot(np.arange(len(ref_ys)), ref_ys,
                color=ref_col, lw=max(0.8, 1.0 * sc),
                linestyle='--', alpha=0.70, zorder=2)

    # Current trace
    if len(cur_vals) >= 2:
        ys       = np.array(cur_vals, dtype=float)
        baseline = 0.0 if symmetric else mn
        ax.plot(xs, ys, color=line_col,
                lw=max(1.0, 1.4 * sc), solid_capstyle='round', zorder=3)
        ax.fill_between(xs, baseline, ys,
                        color=line_col, alpha=0.12, zorder=2)

    # ── Labels ────────────────────────────────────────────────────────────────
    ax_bg.text(0.04, 0.90, label.upper(),
               ha='left', va='top', color=label_col,
               fontsize=fs_label, fontfamily='sans-serif',
               transform=ax_bg.transAxes)

    if channel == 'lap_time':
        m_v = int(value // 60); s_v = value % 60
        val_str = f"{m_v}:{s_v:05.2f}" if value >= 60 else f"{value:.2f}"
    elif abs(value) >= 10000:
        val_str = f"{value:,.0f}"
    elif abs(value) >= 100:
        val_str = f"{value:.0f}"
    elif abs(value) >= 10:
        val_str = f"{value:.1f}"
    else:
        val_str = f"{value:.2f}"

    ax_bg.text(0.97, 0.88, val_str,
               ha='right', va='top', color=line_col,
               fontsize=fs_val, fontweight='bold', fontfamily='sans-serif',
               transform=ax_bg.transAxes)
    if unit:
        ax_bg.text(0.97, 0.60, unit,
                   ha='right', va='center', color=unit_col,
                   fontsize=fs_unit, fontfamily='sans-serif',
                   transform=ax_bg.transAxes)

    # Legend
    if has_ref:
        ax_bg.text(0.97, 0.28, '\u2014 NOW',
                   ha='right', va='center', color=line_col,
                   fontsize=fs_leg, fontfamily='sans-serif',
                   transform=ax_bg.transAxes)
        ax_bg.text(0.97, 0.13, '-- REF',
                   ha='right', va='center', color=ref_col,
                   fontsize=fs_leg, fontfamily='sans-serif',
                   transform=ax_bg.transAxes)

    return fig_to_rgba(fig, (w, h))
