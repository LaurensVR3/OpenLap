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

    sc  = scale_factor(w, h, base_w=220, base_h=100)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    # Outer background
    ax_bg = fig.add_axes([0, 0, 1, 1])
    ax_bg.set_facecolor((0, 0, 0, 0))
    ax_bg.axis('off')
    ax_bg.add_patch(FancyBboxPatch((0.01, 0.02), 0.98, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.07), linewidth=1))

    fs_label = max(5,  min(int(9  * sc), int(w * 0.07)))
    fs_val   = max(6,  min(int(14 * sc), int(w * 0.11)))
    fs_unit  = max(4,  min(int(7  * sc), int(w * 0.06)))

    # ── Chart axes ────────────────────────────────────────────────────────────
    # Reserve right strip for the value readout
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
    # Expand y range slightly so trace doesn't touch the edges
    pad = rng * 0.08
    ax.set_ylim(mn - pad, mx + pad)

    # Colour: symmetric channels are amber/blue; others use cyan→red ramp
    if symmetric:
        line_col = '#ffaa00' if value >= 0 else '#44aaff'
        fill_col = line_col
        zero_y   = 0.0
        ax.axhline(zero_y, color='#2a3a4a', lw=0.8, zorder=1)
    else:
        frac     = max(0.0, min(1.0, (value - mn) / rng))
        line_col = '#ff4422' if frac > 0.80 else '#00ccff'
        fill_col = line_col

    if len(vals) >= 2:
        ys = np.array(vals, dtype=float)
        ax.plot(xs, ys, color=line_col, lw=max(1.0, 1.4 * sc),
                solid_capstyle='round', zorder=3)
        baseline = 0.0 if symmetric else mn
        ax.fill_between(xs, baseline, ys,
                        color=fill_col, alpha=0.18, zorder=2)

    # ── Label (top-left inside chart area) ───────────────────────────────────
    ax_bg.text(0.04, 0.90, label.upper(),
               ha='left', va='top', color='#445566',
               fontsize=fs_label, fontfamily='monospace',
               transform=ax_bg.transAxes)

    # ── Value readout (right panel) ───────────────────────────────────────────
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
               fontsize=fs_val, fontweight='bold', fontfamily='monospace',
               transform=ax_bg.transAxes)
    if unit:
        ax_bg.text(0.95, 0.28, unit,
                   ha='right', va='center', color='#5577aa',
                   fontsize=fs_unit, fontfamily='monospace',
                   transform=ax_bg.transAxes)

    return fig_to_rgba(fig, (w, h))
