# gauge_channels.py — Metadata for all renderable gauge channels
# Imported by styles, overlay_worker, and the UI.

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
    'gear':         {'label': 'Gear',         'unit': '',     'hist_key': 'gear',          'min': 0,    'max': 6,     'symmetric': False},
}

GAUGE_COLOURS = [
    '#00d4ff', '#ff6b35', '#a8ff3e', '#ff3ea8',
    '#ffd700', '#3ea8ff', '#ff3e3e', '#3effd7',
    '#c084fc', '#fb923c',
]

# Gauge types selectable in the overlay editor's "Gauge Type" picker, each
# mapped to a STYLE_NAME registered in style_registry.py (see styles/*.py)
# and a bucket describing what drives its data:
#   'single' — reads one freely-chosen telemetry channel (any key from
#              GAUGE_CHANNELS or a dynamic/session-extra channel)
#   'multi'  — reads a user-built list of channels (gauge['multi_channels'])
#   'none'   — fixed shape, no user-chosen channel (own sub-config instead:
#              info fields, image path, map settings; G-Meter's channel is
#              hardcoded to 'g_meter' by the type itself, not user-selectable)
# 'Progress' (styles/map_progress.py) is intentionally omitted — no JS
# live-preview renderer exists for it yet, so it stays unexposed in the
# editor exactly as before this table replaced CHANNEL_STYLES.
GAUGE_TYPES: dict[str, dict] = {
    'Dial':        {'label': 'Dial',         'bucket': 'single'},
    'Bar':         {'label': 'Bar',          'bucket': 'single'},
    'Numeric':     {'label': 'Numeric',      'bucket': 'single'},
    'Line':        {'label': 'Line',         'bucket': 'single'},
    'Compare':     {'label': 'Compare',      'bucket': 'single'},
    'Delta':       {'label': 'Delta',        'bucket': 'single'},
    'Lean':        {'label': 'Lean',         'bucket': 'single'},
    'Splits':      {'label': 'Splits',       'bucket': 'single'},
    'Sector Bar':  {'label': 'Sector Bar',   'bucket': 'single'},
    'Multi-Line':  {'label': 'Multi-Line',   'bucket': 'multi'},
    'Info':        {'label': 'Session Info', 'bucket': 'none'},
    'Scoreboard':  {'label': 'Lap Info',     'bucket': 'none'},
    'Image':       {'label': 'Image / Logo', 'bucket': 'none'},
    'Circuit':     {'label': 'Circuit Map',  'bucket': 'none'},
    'Zoomed':      {'label': 'Zoomed Map',   'bucket': 'none'},
    'G-Meter':     {'label': 'G-Meter',      'bucket': 'none'},
}


def _resolved_channel_meta(channel: str, unit: str) -> dict:
    """Channel metadata, with the 'speed' channel's unit/bounds converted to
    the display *unit* ('kmh'|'mph'|'ms'). Never mutates GAUGE_CHANNELS."""
    meta = dict(GAUGE_CHANNELS.get(channel, GAUGE_CHANNELS['speed']))
    if channel == 'speed' and unit != 'kmh':
        from units import KMH_PER_UNIT, unit_label
        factor = KMH_PER_UNIT.get(unit, 1.0)
        meta['unit'] = unit_label(unit)
        meta['min']  = meta['min'] * factor
        meta['max']  = meta['max'] * factor
    return meta


def _auto_range(vals: list) -> tuple[float, float]:
    """Auto-derive display bounds for a channel with no curated min/max,
    padding ~10% so the trace doesn't touch the gauge edges."""
    if not vals:
        return 0.0, 1.0
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.1
    return lo - pad, hi + pad


def gauge_data(channel: str, history: list, unit: str = 'kmh',
                extra_label: str = '', extra_unit: str = '') -> dict:
    """Build the data dict passed to a gauge render() function.

    For a dynamic/arbitrary channel (not in GAUGE_CHANNELS — see
    channel_discovery.py), hist_key is the channel name itself (extras are
    already flattened under their own name by video_renderer.py /
    load_lap_history), label/unit come from *extra_label*/*extra_unit* (the
    caller already fetched these via get_available_channels()/
    list_session_channels()), and min/max are auto-ranged from the actual
    data since there's no curated bound for an arbitrary channel.
    """
    if channel in GAUGE_CHANNELS:
        meta = _resolved_channel_meta(channel, unit)
        hk   = meta['hist_key']
        vals = [p.get(hk, 0.0) for p in history] if history else [0.0]
        if channel == 'speed' and unit != 'kmh':
            from units import KMH_PER_UNIT
            factor = KMH_PER_UNIT.get(unit, 1.0)
            vals = [v * factor for v in vals]
        label, unit_out = meta['label'], meta['unit']
        min_val, max_val, symmetric = meta['min'], meta['max'], meta['symmetric']
    else:
        vals = [p.get(channel, 0.0) for p in history] if history else [0.0]
        label, unit_out = (extra_label or channel), extra_unit
        min_val, max_val = _auto_range(vals)
        symmetric = False

    raw_value = vals[-1] if vals else 0.0
    return {
        'value':            raw_value,
        'history_vals':     vals,
        'ref_history_vals': [],   # populated by overlay_worker when reference lap is set
        'sectors':          [],   # populated by overlay_worker when reference lap is set
        'label':            label,
        'unit':             unit_out,
        'min_val':          min_val,
        'max_val':          max_val,
        'symmetric':        symmetric,
        'channel':          channel,
    }


def build_multi_data(channels_list: list, history: list,
                     ref_history: list = None, unit: str = 'kmh') -> dict:
    """
    Build the data dict for a Multi-Line gauge.
    Each entry in channels_list must be a key of GAUGE_CHANNELS.
    """
    from units import KMH_PER_UNIT
    entries = []
    for i, ch in enumerate(channels_list):
        if ch not in GAUGE_CHANNELS:
            continue
        meta = _resolved_channel_meta(ch, unit)
        hk   = meta['hist_key']
        vals = [p.get(hk, 0.0) for p in history] if history else [0.0]
        ref_vals = []
        if ref_history:
            ref_vals = [p.get(hk, 0.0) for p in ref_history]
        if ch == 'speed' and unit != 'kmh':
            factor = KMH_PER_UNIT.get(unit, 1.0)
            vals     = [v * factor for v in vals]
            ref_vals = [v * factor for v in ref_vals]
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


def dummy_gauge_data(gauge_type: str, channel: str = '', unit: str = 'kmh') -> dict:
    """Fake data for overlay editor previews. *channel* only matters for a
    'single'-bucket gauge_type (see GAUGE_TYPES); 'none'/'multi'-bucket types
    (Multi-Line, Info, Scoreboard, G-Meter) ignore it and return their own
    fixed preview shape."""
    import math
    if gauge_type == 'G-Meter':
        channel = 'g_meter'   # fixed by the type itself, not user-selectable
    meta = _resolved_channel_meta(channel or 'speed', unit)
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
    if gauge_type == 'Multi-Line':
        fake_history = []
        for i in range(40):
            t = i * 0.1
            fake_history.append({
                'speed': 100 + 80 * math.sin(t * 0.9),
                'gy':    1.5 * math.sin(t * 1.8),
                'gx':    0.8 * math.sin(t * 1.2),
            })
        return build_multi_data(['speed', 'gforce_lat', 'gforce_lon'], fake_history, unit=unit)

    # Info preview
    if gauge_type == 'Info':
        return dummy_info_data()

    # Lap scoreboard preview
    if gauge_type == 'Scoreboard':
        return dummy_lap_info_data()

    # G-Meter preview: generate a circular trace
    if gauge_type == 'G-Meter':
        gx_v = [2.0 * math.sin(i * 0.25) for i in range(40)]
        gy_v = [2.0 * math.cos(i * 0.25) for i in range(40)]
        d['history_vals'] = gx_v
        d['history_gy']   = gy_v
        d['value']        = gx_v[-1]
        d['value_gy']     = gy_v[-1]
    return d
