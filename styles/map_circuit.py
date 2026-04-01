"""
Map style: Circuit
==================
Classic overhead circuit map with track outline, driven portion
highlighted in white, current position dot, and start marker.

ELEMENT_TYPE : "map"
Data keys    : lats, lons, cur_idx
"""
STYLE_NAME   = "Circuit"
ELEMENT_TYPE = "map"

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(data: dict, w: int, h: int):
    from overlay_utils import fig_to_rgba

    lats    = data['lats']
    lons    = data['lons']
    cur_idx = data['cur_idx']
    size    = min(w, h)

    dpi = 100
    fig, ax = plt.subplots(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_alpha(0)
    ax.set_facecolor((0, 0, 0, 0.65))

    ax.plot(lons, lats, color='#1a2a3a', lw=5.0, solid_capstyle='round', zorder=1)
    ax.plot(lons, lats, color='#2255aa', lw=2.5, solid_capstyle='round', zorder=2)

    if cur_idx > 1:
        n = min(cur_idx + 1, len(lats))
        ax.plot(lons[:n], lats[:n], color='#ffffff', lw=3.0,
                alpha=0.92, solid_capstyle='round', zorder=3)

    if 0 <= cur_idx < len(lats):
        ax.plot(lons[cur_idx], lats[cur_idx], 'o',
                color='#ff2222', ms=max(7, size // 30),
                mec='white', mew=1.8, zorder=6)

    ax.plot(lons[0], lats[0], 's',
            color='#00ff88', ms=max(5, size // 40),
            mec='white', mew=1.2, zorder=5)

    ml  = (max(lats) - min(lats)) * 0.14 or 0.0008
    mlo = (max(lons) - min(lons)) * 0.14 or 0.0008
    ax.set_xlim(min(lons) - mlo, max(lons) + mlo)
    ax.set_ylim(min(lats) - ml,  max(lats) + ml)
    ax.set_aspect('equal')
    ax.axis('off')
    fig.tight_layout(pad=0.2)

    return fig_to_rgba(fig, (w, h))
