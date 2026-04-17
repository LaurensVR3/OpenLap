# gauge_channels.py — Metadata for all renderable gauge channels
# Imported by styles, overlay_worker, and the UI.

MULTI_CHANNEL = 'multi'   # pseudo-channel: combines multiple real channels

# Selectable fields for the Info gauge (key → display label)
INFO_FIELDS: dict[str, str] = {
    'track':    'Track',
    'datetime': 'Date & Time',
    'vehicle':  'Vehicle',
    'session':  'Session type',
    'weather':  'Weather',
    'wind':     'Wind',
}
# Default set shown when no explicit selection has been made
INFO_FIELDS_DEFAULT = ['track', 'datetime', 'vehicle', 'weather', 'wind']

GAUGE_CHANNELS = {
    'speed':        {'label': 'Speed',        'unit': 'km/h', 'hist_key': 'speed',        'min': 0,    'max': 250,   'symmetric': False},
    'rpm':          {'label': 'RPM',          'unit': 'rpm',  'hist_key': 'rpm',           'min': 0,    'max': 14000, 'symmetric': False},
    'exhaust_temp': {'label': 'Exhaust Temp', 'unit': '°C',   'hist_key': 'exhaust_temp',  'min': 0,    'max': 900,   'symmetric': False},
    'gforce_lon':   {'label': 'Long G',       'unit': 'G',    'hist_key': 'gx',            'min': -3,   'max': 3,     'symmetric': True},
    'gforce_lat':   {'label': 'Lat G',        'unit': 'G',    'hist_key': 'gy',            'min': -3,   'max': 3,     'symmetric': True},
    'g_meter':      {'label': 'G-Meter',      'unit': 'G',    'hist_key': 'gx',            'min': -3,   'max': 3,     'symmetric': True},
    'lean':         {'label': 'Lean',         'unit': '°',    'hist_key': 'lean',          'min': -60,  'max': 60,    'symmetric': True},
    'altitude':     {'label': 'Altitude',     'unit': 'm',    'hist_key': 'alt',           'min': 0,    'max': 1000,  'symmetric': False},
    'lap_time':     {'label': 'Lap Time',     'unit': '',     'hist_key': 't',             'min': 0,    'max': 120,   'symmetric': False},
    'delta_time':   {'label': 'Delta',        'unit': 's',    'hist_key': 'delta_time',    'min': -30,  'max': 30,    'symmetric': True},
}

GAUGE_STYLES      = ['Numeric', 'Bar', 'Dial', 'Line', 'Lean', 'Delta', 'Compare', 'Splits']
GAUGE_STYLES_BIKE = ['Numeric', 'Bar', 'Dial', 'Line', 'Lean', 'Delta', 'Compare', 'Splits']
GAUGE_STYLES_CAR  = ['Numeric', 'Bar', 'Dial', 'Line',          'Delta', 'Compare', 'Splits']

GAUGE_COLOURS = [
    '#00d4ff', '#ff6b35', '#a8ff3e', '#ff3ea8',
    '#ffd700', '#3ea8ff', '#ff3e3e', '#3effd7',
    '#c084fc', '#fb923c',
]

# Per-channel valid styles. Channels not listed use the default set.
CHANNEL_STYLES = {
    'delta_time': ['Delta', 'Numeric', 'Line', 'Compare'],
    'lap_time':   ['Numeric', 'Splits', 'Sector Bar', 'Line', 'Compare', 'Bar'],
    'lean':       ['Lean', 'Bar', 'Dial', 'Line', 'Numeric', 'Compare'],
    'g_meter':    ['G-Meter'],
    'altitude':   ['Line', 'Bar', 'Numeric', 'Compare'],
    'multi':      ['Multi-Line'],
    'info':       ['Info'],
    'lap_info':   ['Scoreboard'],
}
_DEFAULT_GAUGE_STYLES = ['Bar', 'Compare', 'Dial', 'Line', 'Numeric']


def get_channel_styles(channel: str, is_bike: bool = False) -> list:
    """Return the ordered list of style names valid for *channel*."""
    if channel == 'map':
        from style_registry import available_styles
        return available_styles('map') or ['Circuit']
    if channel == MULTI_CHANNEL:
        return ['Multi-Line']
    if channel == 'info':
        return ['Info']
    if channel == 'lap_info':
        return ['Scoreboard']
    if channel in CHANNEL_STYLES:
        return list(CHANNEL_STYLES[channel])
    return list(_DEFAULT_GAUGE_STYLES)


def gauge_data(channel: str, history: list) -> dict:
    """Build the data dict passed to a gauge render() function."""
    meta = GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed'])
    hk   = meta['hist_key']
    vals = [p.get(hk, 0.0) for p in history] if history else [0.0]
    raw_value = vals[-1] if vals else 0.0
    return {
        'value':            raw_value,
        'history_vals':     vals,
        'ref_history_vals': [],   # populated by overlay_worker when reference lap is set
        'sectors':          [],   # populated by overlay_worker when reference lap is set
        'label':            meta['label'],
        'unit':             meta['unit'],
        'min_val':          meta['min'],
        'max_val':          meta['max'],
        'symmetric':        meta['symmetric'],
        'channel':          channel,
    }


def build_multi_data(channels_list: list, history: list,
                     ref_history: list = None) -> dict:
    """
    Build the data dict for a Multi-Line gauge.
    Each entry in channels_list must be a key of GAUGE_CHANNELS.
    """
    entries = []
    for i, ch in enumerate(channels_list):
        meta = GAUGE_CHANNELS.get(ch)
        if not meta:
            continue
        hk   = meta['hist_key']
        vals = [p.get(hk, 0.0) for p in history] if history else [0.0]
        ref_vals = []
        if ref_history:
            ref_vals = [p.get(hk, 0.0) for p in ref_history]
        entries.append({
            'channel':          ch,
            'label':            meta['label'],
            'unit':             meta['unit'],
            'values':           vals,
            'value':            vals[-1] if vals else 0.0,
            'ref_values':       ref_vals,
            'min_val':          meta['min'],
            'max_val':          meta['max'],
            'symmetric':        meta['symmetric'],
            'color_idx':        i,
        })
    return {'multi_channels': entries}


def gauge_data_lap_info(history: list) -> dict:
    """Extract lap-scoreboard data from the current history tail."""
    last = history[-1] if history else {}
    return {
        'lap_num':    last.get('li_lap_num',    1),
        'total_laps': last.get('li_total_laps', 1),
        'lap_elapsed': last.get('t',            0.0),
        'best_so_far': last.get('li_best_so_far'),   # float or None
        'delta_time':  last.get('delta_time'),        # live delta vs reference lap, or None
    }


def dummy_lap_info_data() -> dict:
    """Fake lap-scoreboard data for overlay editor previews."""
    return {
        'lap_num':    3,
        'total_laps': 8,
        'lap_elapsed': 45.234,
        'best_so_far': 83.456,
        'delta_time': -0.234,
    }


def dummy_info_data() -> dict:
    """Fake session-info data for overlay editor previews."""
    return {
        'info_track':   'Spa-Francorchamps',
        'info_date':    '2024-06-15',
        'info_time':    '14:32',
        'info_vehicle': 'Porsche 992 GT3 R',
        'info_session': 'Practice',
        'info_weather': '22°C  Partly cloudy',
        'info_wind':    'NW  8 km/h',
    }


def dummy_gauge_data(channel: str) -> dict:
    """Fake data for overlay editor previews."""
    import math
    meta = GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed'])
    mn, mx = meta['min'], meta['max']
    rng    = mx - mn
    t = 0.0
    vals = []
    for i in range(40):
        t += 0.1
        v = mn + rng * (0.35 + 0.25 * math.sin(t * 1.3) + 0.10 * math.sin(t * 3.1))
        vals.append(max(mn, min(mx, v)))
    ref_vals = [max(mn, min(mx, v * (0.96 + 0.08 * math.sin(i * 0.5 + 0.8))))
                for i, v in enumerate(vals)]
    d = {
        'value':            vals[-1],
        'history_vals':     vals,
        'ref_history_vals': ref_vals,
        'sectors': [
            {'num': 1, 'ref_t': 24.5, 'cur_t': 24.3, 'delta': -0.20, 'done': True,  'boundary_elapsed': 24.3},
            {'num': 2, 'ref_t': 23.1, 'cur_t': 24.4, 'delta': +1.30, 'done': True,  'boundary_elapsed': 48.7},
            {'num': 3, 'ref_t': 25.8, 'cur_t': None,  'delta': None,  'done': False, 'boundary_elapsed': float('inf')},
        ],
        'label':        meta['label'],
        'unit':         meta['unit'],
        'min_val':      mn,
        'max_val':      mx,
        'symmetric':    meta['symmetric'],
        'channel':      channel,
    }
    # Multi-Line preview: show speed + lat G as demo
    if channel == MULTI_CHANNEL:
        fake_history = []
        for i in range(40):
            t = i * 0.1
            fake_history.append({
                'speed': 100 + 80 * math.sin(t * 0.9),
                'gy':    1.5 * math.sin(t * 1.8),
                'gx':    0.8 * math.sin(t * 1.2),
            })
        return build_multi_data(['speed', 'gforce_lat', 'gforce_lon'], fake_history)

    # Info preview
    if channel == 'info':
        return dummy_info_data()

    # Lap scoreboard preview
    if channel == 'lap_info':
        return dummy_lap_info_data()

    # G-Meter preview: generate a circular trace
    if channel == 'g_meter':
        gx_v = [2.0 * math.sin(i * 0.25) for i in range(40)]
        gy_v = [2.0 * math.cos(i * 0.25) for i in range(40)]
        d['history_vals'] = gx_v
        d['history_gy']   = gy_v
        d['value']        = gx_v[-1]
        d['value_gy']     = gy_v[-1]
    return d
