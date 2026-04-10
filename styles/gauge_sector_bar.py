"""
Gauge style: Sector Bar
=======================
Compact strip that shows sector splits as they complete.
Completed sectors pop into view coloured by delta vs reference:

  Purple  — faster by more than 0.1 s  (purple = significant gain)
  Green   — faster than reference       (delta < 0)
  Yellow  — slower by up to 1.0 s      (marginal loss)
  Red     — slower by more than 1.0 s  (significant loss)

Pending sectors are shown as small tick marks so the driver can see
how many sectors remain, but they take no meaningful space.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Sector Bar"
Data keys    : sectors (list of dicts with keys: num, delta, done),
               _tc
"""
STYLE_NAME   = 'Sector Bar'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch


# Delta thresholds for colour assignment
_PURPLE_THRESH = -0.10   # faster than ref by ≥ 0.10 s  → purple
_YELLOW_THRESH =  0.0    # any slower than ref           → yellow
_RED_THRESH    =  1.0    # slower by ≥ 1.0 s            → red

# Colours (RGBA)
_COL_PURPLE = (0.60, 0.15, 0.85, 0.95)
_COL_GREEN  = (0.05, 0.72, 0.28, 0.95)
_COL_YELLOW = (0.95, 0.80, 0.05, 0.95)
_COL_RED    = (0.88, 0.20, 0.18, 0.95)
_COL_TICK   = (0.35, 0.35, 0.50, 0.60)   # pending tick marks


def _sector_colour(delta: float):
    if delta <= _PURPLE_THRESH:
        return _COL_PURPLE
    if delta < _YELLOW_THRESH:
        return _COL_GREEN
    if delta < _RED_THRESH:
        return _COL_YELLOW
    return _COL_RED


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    sectors = data.get('sectors', [])
    T       = data.get('_tc', {})

    bg_rgba   = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge   = T.get('bg_edge_rgba', (1, 1, 1, 0.07))

    sc  = scale_factor(w, h, base_w=320, base_h=60)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Background pill
    ax.add_patch(FancyBboxPatch(
        (0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1))

    n = len(sectors)
    if n == 0:
        ax.text(0.5, 0.5, 'No ref lap', ha='center', va='center',
                color='#445566', fontsize=max(5, int(8 * sc)),
                fontfamily='sans-serif')
        return fig_to_rgba(fig, (w, h))

    done_sectors    = [s for s in sectors if s.get('done') and s.get('delta') is not None]
    pending_sectors = [s for s in sectors if not s.get('done')]
    n_done    = len(done_sectors)
    n_pending = len(pending_sectors)

    PAD_X   = 0.03
    PAD_Y   = 0.12
    GAP     = 0.008
    TICK_W  = 0.012          # width of each pending tick
    BOX_Y1  = PAD_Y
    BOX_Y2  = 1.0 - PAD_Y
    BOX_H   = BOX_Y2 - BOX_Y1
    fs      = max(4, min(int(9 * sc), int(h * 0.25)))

    # Total available width for completed boxes
    tick_total = n_pending * (TICK_W + GAP) if n_pending else 0
    box_total  = (1.0 - 2 * PAD_X) - tick_total
    if n_done > 0:
        box_w = max(0.05, (box_total - GAP * (n_done - 1)) / n_done)
    else:
        box_w = 0.0

    # Draw completed sectors (left to right)
    cursor = PAD_X
    for s in done_sectors:
        delta  = s['delta']
        face   = _sector_colour(delta)

        r = max(0.008, BOX_H * 0.18)
        ax.add_patch(FancyBboxPatch(
            (cursor, BOX_Y1), box_w, BOX_H,
            boxstyle=f'round,pad={r:.3f}',
            facecolor=face, edgecolor=(1, 1, 1, 0.12), linewidth=0.5))

        # Sector label (top, small)
        ax.text(cursor + box_w / 2, BOX_Y2 - BOX_H * 0.22,
                f'S{s.get("num", "?")}',
                ha='center', va='center',
                color='white', alpha=0.75,
                fontsize=max(3, int(fs * 0.62)),
                fontfamily='sans-serif')

        # Delta (centre)
        sign = '+' if delta >= 0 else ''
        ax.text(cursor + box_w / 2, (BOX_Y1 + BOX_Y2) / 2,
                f'{sign}{delta:.2f}',
                ha='center', va='center',
                color='white', fontweight='bold',
                fontsize=fs, fontfamily='sans-serif')

        cursor += box_w + GAP

    # Draw pending sector ticks
    for s in pending_sectors:
        tick_x = cursor
        ax.add_patch(FancyBboxPatch(
            (tick_x, BOX_Y1 + BOX_H * 0.25), TICK_W, BOX_H * 0.50,
            boxstyle='round,pad=0.002',
            facecolor=_COL_TICK, edgecolor='none'))
        # tiny sector number
        ax.text(tick_x + TICK_W / 2, BOX_Y1 + BOX_H * 0.50,
                f'S{s.get("num", "?")}',
                ha='center', va='center',
                color='#aaaacc', alpha=0.60,
                fontsize=max(3, int(fs * 0.55)),
                fontfamily='sans-serif')
        cursor += TICK_W + GAP

    return fig_to_rgba(fig, (w, h))
