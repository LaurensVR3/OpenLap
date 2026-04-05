import pytest
from gauge_channels import gauge_data, dummy_gauge_data, GAUGE_CHANNELS

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
    result = dummy_gauge_data('speed')
    assert EXPECTED_GAUGE_KEYS.issubset(result.keys())


def test_dummy_gauge_data_has_history():
    result = dummy_gauge_data('speed')
    assert isinstance(result['history_vals'], list)
    assert len(result['history_vals']) > 0


def test_all_known_channels_work():
    for channel in GAUGE_CHANNELS:
        result = dummy_gauge_data(channel)
        assert result['channel'] == channel
