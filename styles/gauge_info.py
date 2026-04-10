"""
Gauge style: Info
=================
Session info text panel — shows static metadata (track, date/time, vehicle,
session type) plus the live exhaust temperature when available.
Empty / zero fields are hidden automatically.

ELEMENT_TYPE : "gauge"
STYLE_NAME   : "Info"
Data keys    : info_track, info_date, info_time, info_vehicle, info_session,
               info_source, exhaust_temp
"""
STYLE_NAME   = 'Info'
ELEMENT_TYPE = 'gauge'

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba, scale_factor

    T         = data.get('_tc', {})
    bg_rgba   = T.get('bg_rgba',      (0.04, 0.06, 0.10, 0.78))
    bg_edge   = T.get('bg_edge_rgba', (1.00, 1.00, 1.00, 0.08))
    text_col  = T.get('text',         'white')
    label_col = T.get('label',        '#445566')
    fill_pos  = T.get('fill_pos',     '#ffaa00')

    # Build (label, value) pairs — omit empty / zero fields
    fields: list[tuple[str, str]] = []

    track = data.get('info_track', '')
    if track:
        fields.append(('TRACK', track))

    date_s = data.get('info_date', '')
    time_s = data.get('info_time', '')
    if date_s and time_s:
        fields.append(('DATE', f"{date_s}  {time_s}"))
    elif date_s:
        fields.append(('DATE', date_s))

    vehicle = data.get('info_vehicle', '')
    if vehicle:
        fields.append(('VEHICLE', vehicle))

    session_t = data.get('info_session', '')
    if session_t:
        fields.append(('SESSION', session_t))

    temp = data.get('exhaust_temp', 0.0)
    if temp and temp > 0.0:
        fields.append(('EXH TEMP', f"{temp:.0f} °C"))

    source = data.get('info_source', '')
    if source:
        fields.append(('SOURCE', source))

    if not fields:
        fields = [('INFO', '—')]

    sc  = scale_factor(w, h, base_w=220, base_h=130)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor((0, 0, 0, 0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Background pill
    ax.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
        boxstyle='round,pad=0.025',
        facecolor=bg_rgba, edgecolor=bg_edge, linewidth=0.8, zorder=1))

    # Thin accent bar on the left edge
    ax.plot([0.04, 0.04], [0.12, 0.88],
            color=fill_pos, lw=2.0, solid_capstyle='round', zorder=3)

    n = len(fields)
    pad_l    = 0.09
    y_top    = 0.88
    y_bottom = 0.10
    row_h    = (y_top - y_bottom) / max(n, 1)

    fs_label = max(4, min(int(6.5 * sc), int(h * 0.055)))
    fs_value = max(5, min(int(9.5 * sc), int(h * 0.082)))

    for i, (lbl, val) in enumerate(fields):
        yc    = y_top - row_h * (i + 0.5)
        y_lbl = yc + row_h * 0.20
        y_val = yc - row_h * 0.18

        ax.text(pad_l, y_lbl, lbl,
                ha='left', va='center', color=label_col,
                fontsize=fs_label, zorder=4)
        ax.text(pad_l, y_val, val,
                ha='left', va='center', color=text_col,
                fontsize=fs_value, fontweight='bold', zorder=4)

    return fig_to_rgba(fig, (w, h))
