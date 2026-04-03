# gauge_channels.py — Metadata for all renderable gauge channels
# Imported by styles, overlay_worker, and the UI.

GAUGE_CHANNELS = {
    'speed':        {'label': 'Speed',        'unit': 'km/h', 'hist_key': 'speed',        'min': 0,    'max': 250,   'symmetric': False},
    'rpm':          {'label': 'RPM',          'unit': 'rpm',  'hist_key': 'rpm',           'min': 0,    'max': 14000, 'symmetric': False},
    'exhaust_temp': {'label': 'Exhaust Temp', 'unit': '°C',   'hist_key': 'exhaust_temp',  'min': 0,    'max': 900,   'symmetric': False},
    'gforce_lon':   {'label': 'Long G',       'unit': 'G',    'hist_key': 'gx',            'min': -3,   'max': 3,     'symmetric': True},
    'gforce_lat':   {'label': 'Lat G',        'unit': 'G',    'hist_key': 'gy',            'min': -3,   'max': 3,     'symmetric': True},
    'lean':         {'label': 'Lean',         'unit': '°',    'hist_key': 'lean',          'min': -60,  'max': 60,    'symmetric': True},
    'lap_time':     {'label': 'Lap Time',     'unit': '',     'hist_key': 't',             'min': 0,    'max': 120,   'symmetric': False},
}

GAUGE_STYLES      = ['Numeric', 'Bar', 'Dial', 'Line', 'Lean']
GAUGE_STYLES_BIKE = ['Numeric', 'Bar', 'Dial', 'Line', 'Lean']  # all styles in bike mode
GAUGE_STYLES_CAR  = ['Numeric', 'Bar', 'Dial', 'Line']          # no Lean in car mode

GAUGE_COLOURS = [
    '#00d4ff', '#ff6b35', '#a8ff3e', '#ff3ea8',
    '#ffd700', '#3ea8ff', '#ff3e3e', '#3effd7',
    '#c084fc', '#fb923c',
]


def gauge_data(channel: str, history: list) -> dict:
    """Build the data dict passed to a gauge render() function."""
    meta = GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed'])
    hk   = meta['hist_key']
    vals = [p.get(hk, 0.0) for p in history] if history else [0.0]
    return {
        'value':       vals[-1] if vals else 0.0,
        'history_vals': vals,
        'label':       meta['label'],
        'unit':        meta['unit'],
        'min_val':     meta['min'],
        'max_val':     meta['max'],
        'symmetric':   meta['symmetric'],
        'channel':     channel,
    }


def dummy_gauge_data(channel: str) -> dict:
    """Fake data for overlay editor previews."""
    import math, random
    meta = GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed'])
    mn, mx = meta['min'], meta['max']
    rng    = mx - mn
    # Oscillate a plausible demo value
    t = 0.0
    vals = []
    for i in range(40):
        t += 0.1
        v = mn + rng * (0.35 + 0.25 * math.sin(t * 1.3) + 0.10 * math.sin(t * 3.1))
        vals.append(max(mn, min(mx, v)))
    return {
        'value':        vals[-1],
        'history_vals': vals,
        'label':        meta['label'],
        'unit':         meta['unit'],
        'min_val':      mn,
        'max_val':      mx,
        'symmetric':    meta['symmetric'],
        'channel':      channel,
    }
