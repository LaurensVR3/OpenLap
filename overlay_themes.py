# overlay_themes.py — Colour palettes for gauge and map overlay styles.
"""
Each theme dict is injected into render data as '_tc' by style_registry.render_style().
Style plugins read colours via:  T = data.get('_tc', {})
"""
from __future__ import annotations

THEMES: dict[str, dict] = {
    'Dark': {
        # Background pill / rounded box
        'bg_rgba':          (0.00, 0.00, 0.00, 0.72),
        'bg_edge_rgba':     (1.00, 1.00, 1.00, 0.07),
        # Arc / bar track (unfilled portion)
        'track':            '#1a2530',
        # Fill colours
        'fill_pos':         '#ffaa00',   # symmetric positive  (right / warm)
        'fill_neg':         '#44aaff',   # symmetric negative  (left  / cool)
        'fill_lo':          '#00ccff',   # asymmetric low value
        'fill_hi':          '#ff4422',   # asymmetric high value (warning)
        # Text
        'text':             'white',
        'label':            '#445566',
        'unit':             '#5577aa',
        # History trace / sparkline
        'trace':            '#334455',
        # Lean gauge extras
        'ground':           '#2a3a4a',
        'bike_body':        'white',
        'bike_parts':       '#aabbcc',
        'rider_body':       '#667799',
        'rider_head':       '#8899aa',
        'lean_safe':        '#00cc66',
        'lean_warn':        '#ffaa00',
        'lean_danger':      '#ff3333',
        # Map
        'map_bg_rgba':      (0.00, 0.00, 0.00, 0.65),
        'map_track_outer':  '#1a2a3a',
        'map_track_inner':  '#2255aa',
        'map_driven':       '#ffffff',
        'map_dot':          '#ff2222',
        'map_start':        '#00ff88',
    },
    'Light': {
        'bg_rgba':          (0.96, 0.97, 0.99, 0.92),
        'bg_edge_rgba':     (0.00, 0.00, 0.00, 0.12),
        'track':            '#c8d4e0',
        'fill_pos':         '#cc5500',
        'fill_neg':         '#0055cc',
        'fill_lo':          '#006699',
        'fill_hi':          '#cc1100',
        'text':             '#111111',
        'label':            '#778899',
        'unit':             '#4466aa',
        'trace':            '#99aabb',
        'ground':           '#99aabb',
        'bike_body':        '#222222',
        'bike_parts':       '#556677',
        'rider_body':       '#8899aa',
        'rider_head':       '#667788',
        'lean_safe':        '#009944',
        'lean_warn':        '#cc6600',
        'lean_danger':      '#cc1100',
        'map_bg_rgba':      (0.94, 0.96, 0.99, 0.92),
        'map_track_outer':  '#bfccd8',
        'map_track_inner':  '#4477cc',
        'map_driven':       '#111111',
        'map_dot':          '#cc2200',
        'map_start':        '#009955',
    },
    'Colorful': {
        'bg_rgba':          (0.04, 0.01, 0.12, 0.88),
        'bg_edge_rgba':     (0.55, 0.22, 1.00, 0.35),
        'track':            '#1a0535',
        'fill_pos':         '#ff3399',
        'fill_neg':         '#00ff99',
        'fill_lo':          '#ff9900',
        'fill_hi':          '#ff0033',
        'text':             '#ffffff',
        'label':            '#aa66ee',
        'unit':             '#cc88ff',
        'trace':            '#440066',
        'ground':           '#440066',
        'bike_body':        '#ffffff',
        'bike_parts':       '#cc88ff',
        'rider_body':       '#7733cc',
        'rider_head':       '#aa55ff',
        'lean_safe':        '#00ff99',
        'lean_warn':        '#ff9900',
        'lean_danger':      '#ff0033',
        'map_bg_rgba':      (0.04, 0.01, 0.12, 0.88),
        'map_track_outer':  '#1a0535',
        'map_track_inner':  '#8833ff',
        'map_driven':       '#ff9900',
        'map_dot':          '#ff0055',
        'map_start':        '#00ff99',
    },
    'Monochrome': {
        'bg_rgba':          (0.00, 0.00, 0.00, 0.80),
        'bg_edge_rgba':     (1.00, 1.00, 1.00, 0.18),
        'track':            '#282828',
        'fill_pos':         '#ffffff',
        'fill_neg':         '#aaaaaa',
        'fill_lo':          '#ffffff',
        'fill_hi':          '#ffffff',
        'text':             '#ffffff',
        'label':            '#666666',
        'unit':             '#888888',
        'trace':            '#333333',
        'ground':           '#333333',
        'bike_body':        'white',
        'bike_parts':       '#888888',
        'rider_body':       '#666666',
        'rider_head':       '#888888',
        'lean_safe':        '#cccccc',
        'lean_warn':        '#ffffff',
        'lean_danger':      '#ffffff',
        'map_bg_rgba':      (0.00, 0.00, 0.00, 0.80),
        'map_track_outer':  '#282828',
        'map_track_inner':  '#666666',
        'map_driven':       '#ffffff',
        'map_dot':          '#ffffff',
        'map_start':        '#aaaaaa',
    },
}

DEFAULT_THEME = 'Dark'


def get_theme(name: str) -> dict:
    """Return theme colour dict by name, falling back to Dark."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def theme_names() -> list[str]:
    return list(THEMES.keys())
