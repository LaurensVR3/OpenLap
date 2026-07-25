import pytest
from gauge_channels import (
    gauge_data, dummy_gauge_data, build_multi_data, GAUGE_TYPES,
    GAUGE_CHANNELS,
)

EXPECTED_GAUGE_KEYS = {'value', 'history_vals', 'label', 'unit', 'min_val', 'max_val',
                       'symmetric', 'channel'}


def test_gauge_data_speed():
    history = [{'speed': 120.0, 'gx': 0.0, 'gy': 0.0, 'lean': 0.0,
                'rpm': 0.0, 'exhaust_temp': 0.0, 't': 1.0}]
    result = gauge_data('speed', history)
    assert result['value'] == pytest.approx(120.0)


def test_gauge_data_empty_history_returns_zero():
    result = gauge_data('speed', [])
    assert result['value'] == pytest.approx(0.0)


def test_gauge_data_returns_required_keys():
    result = gauge_data('speed', [])
    assert EXPECTED_GAUGE_KEYS.issubset(result.keys())


def test_gauge_data_channel_field():
    result = gauge_data('speed', [])
    assert result['channel'] == 'speed'


def test_dummy_gauge_data_keys():
    result = dummy_gauge_data('Dial', 'speed')
    assert EXPECTED_GAUGE_KEYS.issubset(result.keys())


def test_dummy_gauge_data_has_history():
    result = dummy_gauge_data('Dial', 'speed')
    assert isinstance(result['history_vals'], list)
    assert len(result['history_vals']) > 0


def test_all_known_channels_work():
    for channel in GAUGE_CHANNELS:
        gauge_type = 'G-Meter' if channel == 'g_meter' else 'Dial'
        result = dummy_gauge_data(gauge_type, channel)
        assert result['channel'] == channel


# ── Speed unit conversion ───────────────────────────────────────────────────

def test_gauge_data_speed_default_unit_is_kmh():
    history = [{'speed': 120.0}]
    result = gauge_data('speed', history)
    assert result['value'] == pytest.approx(120.0)
    assert result['unit'] == 'km/h'
    assert result['max_val'] == pytest.approx(250)


def test_gauge_data_speed_mph_conversion():
    history = [{'speed': 120.0}]
    result = gauge_data('speed', history, unit='mph')
    assert result['value'] == pytest.approx(120.0 * 0.621371)
    assert result['unit'] == 'mph'
    assert result['min_val'] == pytest.approx(0)
    assert result['max_val'] == pytest.approx(250 * 0.621371)


def test_gauge_data_speed_ms_conversion():
    history = [{'speed': 36.0}]
    result = gauge_data('speed', history, unit='ms')
    assert result['value'] == pytest.approx(10.0)
    assert result['unit'] == 'm/s'


def test_gauge_data_non_speed_channel_ignores_unit():
    history = [{'rpm': 5000.0}]
    result = gauge_data('rpm', history, unit='mph')
    assert result['value'] == pytest.approx(5000.0)
    assert result['unit'] == 'rpm'


def test_gauge_data_does_not_mutate_gauge_channels():
    gauge_data('speed', [{'speed': 100.0}], unit='mph')
    assert GAUGE_CHANNELS['speed']['unit'] == 'km/h'
    assert GAUGE_CHANNELS['speed']['max'] == 250


def test_gauge_data_does_not_mutate_history():
    history = [{'speed': 100.0}]
    gauge_data('speed', history, unit='mph')
    assert history[0]['speed'] == 100.0


def test_build_multi_data_speed_conversion():
    history = [{'speed': 100.0, 'gy': 1.0}]
    ref_history = [{'speed': 90.0, 'gy': 0.5}]
    result = build_multi_data(['speed', 'gforce_lat'], history, ref_history, unit='mph')
    speed_entry = next(e for e in result['multi_channels'] if e['channel'] == 'speed')
    assert speed_entry['value'] == pytest.approx(100.0 * 0.621371)
    assert speed_entry['ref_values'][0] == pytest.approx(90.0 * 0.621371)
    assert speed_entry['unit'] == 'mph'
    lat_entry = next(e for e in result['multi_channels'] if e['channel'] == 'gforce_lat')
    assert lat_entry['value'] == pytest.approx(1.0)


def test_dummy_gauge_data_speed_unit():
    result = dummy_gauge_data('Dial', 'speed', unit='mph')
    assert result['unit'] == 'mph'
    assert result['max_val'] == pytest.approx(250 * 0.621371)
    assert all(v <= result['max_val'] + 1e-6 for v in result['history_vals'])


# ── Dynamic/arbitrary channel fallback (see channel_discovery.py) ─────────────

def test_gauge_data_unknown_channel_reads_own_key():
    history = [{'Coolant Temperature': 80.0}, {'Coolant Temperature': 90.0}]
    result = gauge_data('Coolant Temperature', history)
    assert result['value'] == pytest.approx(90.0)
    assert result['history_vals'] == pytest.approx([80.0, 90.0])
    assert result['channel'] == 'Coolant Temperature'


def test_gauge_data_unknown_channel_uses_extra_label_and_unit():
    history = [{'Coolant Temperature': 80.0}]
    result = gauge_data('Coolant Temperature', history,
                         extra_label='Coolant Temperature', extra_unit='C')
    assert result['label'] == 'Coolant Temperature'
    assert result['unit'] == 'C'


def test_gauge_data_unknown_channel_defaults_label_to_channel_name():
    result = gauge_data('Some Raw Channel', [{'Some Raw Channel': 1.0}])
    assert result['label'] == 'Some Raw Channel'
    assert result['unit'] == ''


def test_gauge_data_unknown_channel_auto_ranges_min_max():
    history = [{'X': v} for v in (10.0, 20.0, 30.0)]
    result = gauge_data('X', history)
    assert result['min_val'] < 10.0
    assert result['max_val'] > 30.0


def test_gauge_data_unknown_channel_constant_value_still_produces_a_range():
    history = [{'X': 5.0}, {'X': 5.0}]
    result = gauge_data('X', history)
    assert result['min_val'] < 5.0 < result['max_val']


def test_gauge_data_unknown_channel_empty_history():
    # Empty history falls back to a single implicit 0.0 sample (matching the
    # known-channel branch's behaviour), so min/max still bracket 0.0.
    result = gauge_data('X', [])
    assert result['value'] == pytest.approx(0.0)
    assert result['min_val'] < 0.0 < result['max_val']


def test_gauge_data_known_channel_unaffected_by_fallback():
    # Sanity check the branch split didn't change existing behaviour.
    result = gauge_data('speed', [{'speed': 100.0}])
    assert result['min_val'] == 0
    assert result['max_val'] == 250


# ── GAUGE_TYPES (overlay editor's "Gauge Type" -> bucket classification) ──────

def test_gauge_types_have_valid_buckets():
    for name, meta in GAUGE_TYPES.items():
        assert meta['bucket'] in ('single', 'multi', 'none')
        assert isinstance(meta['label'], str) and meta['label']


def test_gauge_types_known_buckets():
    for name in ('Dial', 'Bar', 'Numeric', 'Line', 'Compare', 'Delta', 'Lean',
                 'Splits', 'Sector Bar'):
        assert GAUGE_TYPES[name]['bucket'] == 'single'
    assert GAUGE_TYPES['Multi-Line']['bucket'] == 'multi'
    for name in ('Info', 'Scoreboard', 'Image', 'Circuit', 'Zoomed', 'G-Meter'):
        assert GAUGE_TYPES[name]['bucket'] == 'none'


def test_dummy_gauge_data_g_meter_channel_is_fixed_by_type():
    # G-Meter is a 'none'-bucket type — its channel is implied by the type
    # itself, not user-selectable, so passing an unrelated channel is ignored.
    result = dummy_gauge_data('G-Meter', 'speed')
    assert result['channel'] == 'g_meter'
    assert 'value_gy' in result and 'history_gy' in result
