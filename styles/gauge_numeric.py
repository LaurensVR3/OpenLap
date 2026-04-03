"""
Gauge style: Numeric
====================
Large centred value readout with label and unit.
Works for any channel.  For lap_time the value is formatted as M:SS.mmm.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Numeric"
Data keys    : value, label, unit, min_val, max_val, symmetric, channel
"""
STYLE_NAME   = 'Numeric'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    value   = data.get('value',   0.0)
    label   = data.get('label',   '')
    unit    = data.get('unit',    '')
    channel = data.get('channel', '')

    sc  = scale_factor(w, h, base_w=120, base_h=160)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis('off')

    ax.add_patch(FancyBboxPatch((0.04, 0.04), 0.92, 0.92,
        boxstyle='round,pad=0.02',
        facecolor=(0, 0, 0, 0.72), edgecolor=(1, 1, 1, 0.07), linewidth=1))

    fs_label = max(5,  min(int(11 * sc), int(w * 0.13)))
    fs_value = max(10, min(int(34 * sc), int(w * 0.38)))
    fs_unit  = max(5,  min(int(9  * sc), int(w * 0.10)))

    # Format value
    if channel == 'lap_time':
        m  = int(value // 60)
        s  = value % 60
        txt = f"{m}:{s:06.3f}" if value >= 60 else f"{value:.3f}"
        fs_value = max(8, min(int(20 * sc), int(w * 0.22)))
    elif abs(value) >= 10000:
        txt = f"{value:,.0f}"
    elif abs(value) >= 100:
        txt = f"{value:.0f}"
    elif abs(value) >= 10:
        txt = f"{value:.1f}"
    else:
        txt = f"{value:.2f}"

    ax.text(0.50, 0.78, label.upper(),
            ha='center', va='center', color='#445566',
            fontsize=fs_label, fontfamily='monospace')
    ax.text(0.50, 0.50, txt,
            ha='center', va='center', color='white',
            fontsize=fs_value, fontweight='bold', fontfamily='monospace')
    ax.text(0.50, 0.24, unit,
            ha='center', va='center', color='#5577aa',
            fontsize=fs_unit, fontfamily='monospace')

    return fig_to_rgba(fig, (w, h))
