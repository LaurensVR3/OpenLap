"""
Gauge style: Line
=================
Area chart of the channel's recent history.  The current value is shown
as a large readout in the top-right corner.  A horizontal zero line is
drawn for symmetric channels.  The fill under the trace is colour-coded
by channel type.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Line"
Data keys    : value, history_vals, label, unit, min_val, max_val, symmetric, channel
"""
STYLE_NAME   = 'Line'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value     = data.get('value',       0.0)
    hist      = data.get('history_vals', [value])
    label     = data.get('label',       '')
    unit      = data.get('unit',        '')
    mn        = data.get('min_val',     0.0)
    mx        = data.get('max_val',     100.0)
    symmetric = data.get('symmetric',   False)
    channel   = data.get('channel',     '')

    T         = data.get('_tc', {})
    bg_rgba   = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge   = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    track_col = T.get('track',        '#1a2530')
    fill_pos  = T.get('fill_pos',     '#ffaa00')
    fill_neg  = T.get('fill_neg',     '#44aaff')
    fill_lo   = T.get('fill_lo',      '#00ccff')
    fill_hi   = T.get('fill_hi',      '#ff4422')
    label_col = T.get('label',        '#445566')
    unit_col  = T.get('unit',         '#5577aa')

    sc  = scale_factor(w, h, base_w=220, base_h=100)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    # Outer background
    ax_bg = fig.add_axes([0, 0, 1, 1])
    ax_bg.set_facecolor((0, 0, 0, 0))
    ax_bg.axis('off')
    ax_bg.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    fs_label = max(5,  min(int(9  * sc), int(w * 0.07)))
    fs_val   = max(6,  min(int(14 * sc), int(w * 0.11)))
    fs_unit  = max(4,  min(int(7  * sc), int(w * 0.06)))

    # Chart axes
    ax = fig.add_axes([0.04, 0.18, 0.68, 0.62])
    ax.set_facecolor((0, 0, 0, 0))
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    n    = min(120, len(hist))
    vals = list(hist[-n:])
    xs   = np.arange(len(vals))

    rng = mx - mn if mx != mn else 1.0
    ax.set_xlim(0, max(1, len(vals) - 1))
    pad = rng * 0.08
    ax.set_ylim(mn - pad, mx + pad)

    if symmetric:
        line_col = fill_pos if value >= 0 else fill_neg
        fill_col = line_col
        ax.axhline(0.0, color=track_col, lw=0.8, zorder=1)
    else:
        frac     = max(0.0, min(1.0, (value - mn) / rng))
        line_col = fill_hi if frac > 0.80 else fill_lo
        fill_col = line_col

    if len(vals) >= 2:
        ys = np.array(vals, dtype=float)
        ax.plot(xs, ys, color=line_col, lw=max(1.0, 1.4 * sc),
                solid_capstyle='round', zorder=3)
        baseline = 0.0 if symmetric else mn
        ax.fill_between(xs, baseline, ys,
                        color=fill_col, alpha=0.18, zorder=2)

    ax_bg.text(0.04, 0.90, label.upper(),
               ha='left', va='top', color=label_col,
               fontsize=fs_label, fontfamily='sans-serif',
               transform=ax_bg.transAxes)

    if channel == 'lap_time':
        m = int(value // 60); s = value % 60
        val_str  = f"{m}:{s:05.2f}" if value >= 60 else f"{value:.2f}"
        fs_val   = max(5, min(int(11 * sc), int(w * 0.09)))
    elif abs(value) >= 10000:
        val_str = f"{value:,.0f}"
    elif abs(value) >= 100:
        val_str = f"{value:.0f}"
    elif abs(value) >= 10:
        val_str = f"{value:.1f}"
    else:
        val_str = f"{value:.2f}"

    ax_bg.text(0.95, 0.56, val_str,
               ha='right', va='center', color=line_col,
               fontsize=fs_val, fontweight='bold', fontfamily='sans-serif',
               transform=ax_bg.transAxes)
    if unit:
        ax_bg.text(0.95, 0.28, unit,
                   ha='right', va='center', color=unit_col,
                   fontsize=fs_unit, fontfamily='sans-serif',
                   transform=ax_bg.transAxes)

    return fig_to_rgba(fig, (w, h))
