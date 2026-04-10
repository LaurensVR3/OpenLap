"""
Gauge style: Delta
==================
Shows the running time gap between the current lap and a reference lap.

  Positive (+) → current is behind the reference (slower) — shown in red.
  Negative (−) → current is ahead of the reference (faster) — shown in green.
  Near zero (±0.1 s) → white / neutral.

A small sparkline at the bottom shows the delta trend over the last
few seconds of history.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Delta"
Data keys    : value (delta_seconds), history_vals, label, unit, _tc
"""
STYLE_NAME   = 'Delta'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np


# Thresholds for colour bands
_NEUTRAL_BAND = 0.10   # seconds — shown as white


def _delta_colour(delta: float) -> str:
    """Map delta value to a display colour."""
    if abs(delta) <= _NEUTRAL_BAND:
        return '#e8e8e8'
    return '#22dd66' if delta < 0 else '#ff4444'   # green = faster, red = slower


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value    = data.get('value', 0.0)
    history  = data.get('history_vals', [0.0])
    label    = data.get('label', 'Delta')

    T        = data.get('_tc', {})
    bg_rgba  = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge  = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    label_col = T.get('label',       '#445566')

    sc  = scale_factor(w, h, base_w=120, base_h=160)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Background card
    ax.add_patch(FancyBboxPatch(
        (0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1,
    ))

    colour = _delta_colour(value)

    # Sign + value text  e.g.  "+1.234"  or  "−0.567"
    if value >= 0:
        txt = f'+{value:.3f}'
    else:
        txt = f'\u2212{abs(value):.3f}'    # Unicode minus sign for aesthetics

    fs_label = max(5,  min(int(10 * sc), int(w * 0.12)))
    fs_value = max(9,  min(int(28 * sc), int(w * 0.30)))

    ax.text(0.50, 0.80, label.upper(),
            ha='center', va='center', color=label_col,
            fontsize=fs_label, fontfamily='sans-serif')

    ax.text(0.50, 0.54, txt,
            ha='center', va='center', color=colour,
            fontsize=fs_value, fontweight='bold', fontfamily='sans-serif')

    # ── Sparkline (delta history trend) ───────────────────────────────────────
    if len(history) >= 2:
        ax_spark = fig.add_axes([0.08, 0.06, 0.84, 0.18])
        ax_spark.set_facecolor((0, 0, 0, 0))
        ax_spark.axis('off')

        # Keep last 150 samples for the trend line
        vals = np.array(history[-150:], dtype=float)
        xs   = np.linspace(0, 1, len(vals))

        # Shade positive (behind) red, negative (ahead) green
        zero = np.zeros_like(vals)
        ax_spark.fill_between(xs, vals, zero,
                              where=vals >= 0,
                              color='#ff4444', alpha=0.35, linewidth=0)
        ax_spark.fill_between(xs, vals, zero,
                              where=vals <= 0,
                              color='#22dd66', alpha=0.35, linewidth=0)
        ax_spark.plot(xs, vals, color=colour, linewidth=0.8, alpha=0.9)
        ax_spark.axhline(0, color='#ffffff40', linewidth=0.5)

    return fig_to_rgba(fig, (w, h))
