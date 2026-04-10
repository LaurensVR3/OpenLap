"""
Gauge style: Multi-Line
=======================
Renders multiple channels as overlaid traces on a shared chart.
Each channel is independently normalised to 0–1 on the Y axis so
wildly different scales (e.g. 250 km/h vs 3 G) coexist cleanly.

A compact right-hand legend shows each channel's colour, label, and
current value in its native unit.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Multi-Line"
Data keys    : multi_channels (list), _tc

Each entry in multi_channels:
    channel   : str   — channel key
    label     : str
    unit      : str
    values    : list[float]  — history
    value     : float        — current value
    min_val   : float
    max_val   : float
    symmetric : bool
    color_idx : int          — index into GAUGE_COLOURS
"""
STYLE_NAME   = 'Multi-Line'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch

from gauge_channels import GAUGE_COLOURS

_DELTA_PURPLE = '#c084fc'
_DELTA_GREEN  = '#22dd66'
_DELTA_YELLOW = '#ffd700'
_DELTA_RED    = '#ff4444'

def _delta_colour(value: float) -> str:
    """Colour a delta_time value: purple=fast, green=slightly fast, yellow/red=slow."""
    if value <= -0.10:
        return _DELTA_PURPLE
    if value < 0.0:
        return _DELTA_GREEN
    if value < 1.0:
        return _DELTA_YELLOW
    return _DELTA_RED


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    entries = data.get('multi_channels', [])
    T       = data.get('_tc', {})

    bg_rgba   = T.get('bg_rgba',      (0.04, 0.06, 0.10, 0.82))
    bg_edge   = T.get('bg_edge_rgba', (1.00, 1.00, 1.00, 0.14))

    sc  = scale_factor(w, h, base_w=320, base_h=120)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax_bg = fig.add_axes([0, 0, 1, 1])
    ax_bg.set_facecolor((0, 0, 0, 0))
    ax_bg.axis('off')
    ax_bg.add_patch(FancyBboxPatch(
        (0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    if not entries:
        ax_bg.text(0.5, 0.5, 'No channels', ha='center', va='center',
                   color='#445566', fontsize=max(6, int(8 * sc)),
                   fontfamily='sans-serif', transform=ax_bg.transAxes)
        return fig_to_rgba(fig, (w, h))

    # Legend column width: proportional to number of channels + label length
    max_label = max((len(e['label']) for e in entries), default=5)
    legend_w  = min(0.38, max(0.22, 0.05 + max_label * 0.022))

    # Chart axes (left portion)
    chart_l = 0.05
    chart_r = 1.0 - legend_w - 0.03
    ax = fig.add_axes([chart_l, 0.12, chart_r - chart_l, 0.76])
    ax.set_facecolor((0, 0, 0, 0))
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, 1.05)

    # Zero line (subtle)
    ax.axhline(0.5, color='#ffffff', lw=0.4, alpha=0.10, zorder=1)

    n_pts = max((len(e['values']) for e in entries), default=1)

    for entry in entries:
        vals    = entry.get('values', [])
        mn      = entry.get('min_val', 0.0)
        mx      = entry.get('max_val', 1.0)
        sym     = entry.get('symmetric', False)
        cidx    = entry.get('color_idx', 0)
        if entry.get('channel') == 'delta_time':
            colour = _delta_colour(entry.get('value', 0.0))
        else:
            colour = GAUGE_COLOURS[cidx % len(GAUGE_COLOURS)]

        rng = mx - mn if mx != mn else 1.0
        n   = len(vals)
        if n < 2:
            continue

        # Normalise to 0-1
        norm = [(v - mn) / rng for v in vals]
        # For symmetric channels, centre at 0.5
        if sym:
            norm = [0.5 + (v - 0.5) for v in norm]

        xs = np.linspace(0, 1, n)
        ys = np.clip(norm, -0.05, 1.05)

        lw = max(1.0, 1.5 * sc)
        ax.plot(xs, ys, color=colour, lw=lw,
                solid_capstyle='round', alpha=0.90, zorder=3)

        # Subtle fill
        baseline = 0.5 if sym else 0.0
        ax.fill_between(xs, baseline, ys, color=colour, alpha=0.08, zorder=2)

        # Current value dot
        ax.plot(1.0, float(np.clip(norm[-1], -0.05, 1.05)),
                'o', color=colour, ms=max(3, 4 * sc), zorder=5,
                mec='white', mew=max(0.5, 0.6 * sc))

    # Legend (right side) — one combined line per entry to avoid any overlap
    fs_leg = max(5, min(int(8 * sc), int(h * 0.09)))

    legend_x  = 1.0 - legend_w + 0.01
    n_entries  = len(entries)
    row_h      = 0.80 / max(n_entries, 1)

    for i, entry in enumerate(entries):
        cidx   = entry.get('color_idx', 0)
        if entry.get('channel') == 'delta_time':
            colour = _delta_colour(entry.get('value', 0.0))
        else:
            colour = GAUGE_COLOURS[cidx % len(GAUGE_COLOURS)]
        label  = entry.get('label', '')
        unit   = entry.get('unit',  '')
        value  = entry.get('value', 0.0)

        y_centre = 0.88 - (i + 0.5) * row_h

        # Colour swatch
        swatch_h = min(0.05, row_h * 0.55)
        ax_bg.add_patch(plt.Rectangle(
            (legend_x, y_centre - swatch_h / 2), 0.022, swatch_h,
            facecolor=colour, edgecolor='none',
            transform=ax_bg.transAxes, zorder=4))

        # Format value
        if abs(value) >= 1000:
            val_str = f'{value:,.0f}'
        elif abs(value) >= 100:
            val_str = f'{value:.0f}'
        elif abs(value) >= 10:
            val_str = f'{value:.1f}'
        else:
            val_str = f'{value:.2f}'
        if unit:
            val_str += f'\u202f{unit}'

        # Single combined line: "LABEL  val unit"
        short_label = label[:6].upper()
        combined    = f'{short_label:<6}  {val_str}'
        ax_bg.text(legend_x + 0.032, y_centre,
                   combined,
                   ha='left', va='center', color=colour,
                   fontsize=fs_leg, fontweight='bold', fontfamily='sans-serif',
                   transform=ax_bg.transAxes)

    return fig_to_rgba(fig, (w, h))
