"""
Gauge style: Bar
================
Horizontal fill bar.  Symmetric channels (G-forces, lean) fill from centre
outward in left/right colours.  Asymmetric channels fill left-to-right.
A small sparkline of recent values sits below the bar.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Bar"
Data keys    : value, history_vals, label, unit, min_val, max_val, symmetric
"""
STYLE_NAME   = 'Bar'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
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

    sc  = scale_factor(w, h, base_w=180, base_h=120)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis('off')

    ax.add_patch(FancyBboxPatch((0.03, 0.03), 0.94, 0.94,
        boxstyle='round,pad=0.02',
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.07), linewidth=1))

    fs_label = max(5,  min(int(10 * sc), int(w * 0.08)))
    fs_val   = max(6,  min(int(13 * sc), int(w * 0.10)))

    PAD   = 0.08
    BAR_L = PAD
    BAR_R = 1.0 - PAD
    BAR_Y = 0.44
    BAR_H = 0.22
    bar_w = BAR_R - BAR_L

    ax.text(0.50, 0.88, label.upper(),
            ha='center', va='center', color='#445566',
            fontsize=fs_label, fontfamily='monospace')

    # Track
    ax.add_patch(plt.Rectangle((BAR_L, BAR_Y), bar_w, BAR_H,
                 facecolor='#1a2530', zorder=2))

    rng = mx - mn if mx != mn else 1.0

    if symmetric:
        # Fill from centre, left=neg colour, right=pos colour
        mid = BAR_L + bar_w * (-mn / rng)
        frac = (value - mn) / rng
        fill_x = BAR_L + bar_w * max(0.0, min(1.0, frac))
        mid_x  = BAR_L + bar_w * (-mn / rng)

        if value >= 0:
            col = '#ffaa00'
            ax.add_patch(plt.Rectangle((mid_x, BAR_Y), fill_x - mid_x, BAR_H,
                         facecolor=col, alpha=0.90, zorder=3))
        else:
            col = '#44aaff'
            ax.add_patch(plt.Rectangle((fill_x, BAR_Y), mid_x - fill_x, BAR_H,
                         facecolor=col, alpha=0.90, zorder=3))

        # Centre tick
        ax.plot([mid_x, mid_x], [BAR_Y - 0.02, BAR_Y + BAR_H + 0.02],
                color='#3a4a5a', lw=1.0, zorder=4)
        val_col = col
    else:
        frac = max(0.0, min(1.0, (value - mn) / rng))
        col  = '#00ccff' if frac < 0.75 else '#ff6622'
        ax.add_patch(plt.Rectangle((BAR_L, BAR_Y), bar_w * frac, BAR_H,
                     facecolor=col, alpha=0.90, zorder=3))
        val_col = col

    # Value text
    val_str = f"{value:.1f} {unit}" if unit else f"{value:.1f}"
    ax.text(0.50, 0.30, val_str,
            ha='center', va='center', color=val_col,
            fontsize=fs_val, fontweight='bold', fontfamily='monospace')

    # Sparkline
    if len(hist) >= 2:
        n    = min(50, len(hist))
        vals = hist[-n:]
        xs   = [BAR_L + bar_w * (i / (n - 1)) for i in range(n)]
        lo, hi = mn, mx
        ys   = [BAR_Y - 0.04 - 0.10 * max(0.0, min(1.0, (v - lo) / (hi - lo or 1)))
                for v in vals]
        ax.plot(xs, ys, color='#334455', lw=max(0.6, 0.8 * sc), zorder=2)

    return fig_to_rgba(fig, (w, h))
