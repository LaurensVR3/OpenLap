"""
Gauge style: Splits
===================
Sector-split comparison table.  The lap is divided into N equal-distance
mini-sectors.  Each row shows the reference sector time, the current lap
sector time, and the split delta (green = faster, red = slower).

Sectors that are not yet completed show dashes.  The next incomplete
sector is subtly highlighted to indicate where the car currently is.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Splits"
Data keys    : value (current lap elapsed s), sectors (list of dicts),
               label, _tc

Each sector dict:
    num              : int   — sector number (1-based)
    ref_t            : float — reference lap sector duration (s)
    cur_t            : float | None — current lap sector duration (s), None if not yet done
    delta            : float | None — cur_t - ref_t
    done             : bool
    boundary_elapsed : float — current lap elapsed time when sector ends
                               (inf if sector not reachable yet)
"""
STYLE_NAME   = 'Splits'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    cur_elapsed = data.get('value',   0.0)
    sectors     = data.get('sectors', [])
    label       = data.get('label',   'Lap Time')

    T         = data.get('_tc', {})
    bg_rgba   = T.get('bg_rgba',      (0, 0, 0, 0.72))
    bg_edge   = T.get('bg_edge_rgba', (1, 1, 1, 0.07))
    label_col = T.get('label',        '#445566')
    text_col  = T.get('text',         'white')

    sc  = scale_factor(w, h, base_w=160, base_h=200)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.add_patch(FancyBboxPatch(
        (0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.02',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=1,
    ))

    fs_title  = max(5, min(int(8 * sc), int(w * 0.09)))
    fs_row    = max(5, min(int(9 * sc), int(w * 0.09)))
    fs_hdr    = max(4, min(int(6 * sc), int(w * 0.075)))

    ax.text(0.50, 0.93, 'SPLITS',
            ha='center', va='center', color=label_col,
            fontsize=fs_title, fontfamily='sans-serif')

    if not sectors:
        ax.text(0.50, 0.50, 'No ref lap',
                ha='center', va='center', color=label_col,
                fontsize=fs_row, fontfamily='sans-serif')
        return fig_to_rgba(fig, (w, h))

    n     = len(sectors)
    # Allocate rows between y=0.85 and y=0.10
    y_top = 0.84
    row_h = (y_top - 0.10) / (n + 1)   # +1 for the header row

    # Header row
    y_hdr = y_top
    ax.text(0.12, y_hdr - row_h * 0.45, 'S',    ha='center', va='center', color=label_col, fontsize=fs_hdr, fontfamily='sans-serif')
    ax.text(0.38, y_hdr - row_h * 0.45, 'REF',  ha='center', va='center', color=label_col, fontsize=fs_hdr, fontfamily='sans-serif')
    ax.text(0.62, y_hdr - row_h * 0.45, 'CUR',  ha='center', va='center', color=label_col, fontsize=fs_hdr, fontfamily='sans-serif')
    ax.text(0.87, y_hdr - row_h * 0.45, 'DIFF', ha='center', va='center', color=label_col, fontsize=fs_hdr, fontfamily='sans-serif')

    # Identify next incomplete sector to highlight
    next_incomplete = None
    for i, s in enumerate(sectors):
        if not s.get('done', False):
            next_incomplete = i
            break

    for i, s in enumerate(sectors):
        y = y_top - (i + 1) * row_h

        ref_t = s.get('ref_t')
        cur_t = s.get('cur_t')
        delta = s.get('delta')
        num   = s.get('num', i + 1)
        done  = s.get('done', False)

        # Subtle highlight on the sector the car is currently in
        if i == next_incomplete:
            ax.add_patch(Rectangle(
                (0.05, y + 0.005), 0.90, row_h - 0.01,
                facecolor=(1, 1, 1, 0.07), edgecolor='none'))

        row_y = y + row_h * 0.5

        ax.text(0.12, row_y, f'S{num}',
                ha='center', va='center', color=text_col,
                fontsize=fs_row, fontfamily='sans-serif')

        ref_str = f'{ref_t:.2f}' if ref_t is not None else '\u2014'
        ax.text(0.38, row_y, ref_str,
                ha='center', va='center', color='#888888',
                fontsize=fs_row, fontfamily='sans-serif')

        if cur_t is not None:
            ax.text(0.62, row_y, f'{cur_t:.2f}',
                    ha='center', va='center', color=text_col,
                    fontsize=fs_row, fontfamily='sans-serif')
            if delta is not None:
                if abs(delta) < 0.01:
                    d_col = '#e8e8e8'
                elif delta < 0:
                    d_col = '#22dd66'
                else:
                    d_col = '#ff4444'
                ax.text(0.87, row_y, f'{delta:+.2f}',
                        ha='center', va='center', color=d_col,
                        fontsize=fs_row, fontweight='bold', fontfamily='sans-serif')
        else:
            dim = '#444444'
            ax.text(0.62, row_y, '\u2014', ha='center', va='center',
                    color=dim, fontsize=fs_row, fontfamily='sans-serif')
            ax.text(0.87, row_y, '\u2014', ha='center', va='center',
                    color=dim, fontsize=fs_row, fontfamily='sans-serif')

    return fig_to_rgba(fig, (w, h))
