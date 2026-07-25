"""
Tests for channel_discovery.py — discovering and classifying a session's
available gauge channels (fixed GAUGE_CHANNELS + dynamic DataPoint.extra).
"""
import pytest

from channel_discovery import is_noisy_channel, list_channels
from data_model import DataPoint, Session
from gauge_channels import GAUGE_CHANNELS


# ── is_noisy_channel ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', [
    'Throttle Limit State',
    'CAN Bus 1 Diagnostic',
    'ECU Internal 1V2 Diagnostic',
    'Ignition Output Cut Count',
    'ECU Uptime',
    'ECU CPU Usage',
    'Warning Source',
    'Launch State',
    'Fuel Cylinder 1 Primary Pin Diagnostic',
])
def test_name_heuristic_flags_diagnostic_channels(name):
    assert is_noisy_channel(name, [1.0] * 20) is True


@pytest.mark.parametrize('name', [
    'Coolant Temperature',
    'Throttle Position',
    'Brake Pressure Front',
    'Steering Angle',
    'Fuel Pressure',
])
def test_name_heuristic_keeps_real_sensor_channels(name):
    # Varying values (not a flag/state) so only the name heuristic is in play.
    values = [float(i) for i in range(20)]
    assert is_noisy_channel(name, values) is False


def test_low_cardinality_fallback_flags_state_like_channels():
    # A channel with an innocuous name but only 3 distinct values across the
    # session — behaves like a flag/state, not a continuous measurement.
    values = [0.0, 1.0, 2.0] * 10
    assert is_noisy_channel('Some Custom Channel', values) is True


def test_high_cardinality_channel_not_flagged_by_name_or_cardinality():
    values = [float(i) * 0.37 for i in range(50)]
    assert is_noisy_channel('Some Custom Channel', values) is False


def test_empty_values_not_flagged_by_cardinality_alone():
    assert is_noisy_channel('Some Custom Channel', []) is False


# ── list_channels ─────────────────────────────────────────────────────────────

def _pt(elapsed, extra=None):
    return DataPoint(
        record=0, time=None, lat=0.0, lon=0.0, alt=0.0, speed=0.0,
        gforce_x=0.0, gforce_y=0.0, gforce_z=0.0, lap=0,
        gyro_x=0.0, gyro_y=0.0, gyro_z=0.0, elapsed=elapsed,
        extra=extra or {},
    )


def _session(points, extra_channel_meta=None):
    return Session(
        source='Test', date_utc='', track='', configuration='',
        session_type='', best_lap_time=0.0, all_points=points, laps=[],
        extra_channel_meta=extra_channel_meta or {},
    )


def test_list_channels_always_includes_fixed_gauge_channels():
    session = _session([_pt(0.0)])
    result = list_channels(session)
    keys = {c['key'] for c in result}
    assert set(GAUGE_CHANNELS.keys()).issubset(keys)


def test_list_channels_fixed_channels_never_marked_noisy():
    session = _session([_pt(0.0)])
    result = list_channels(session)
    fixed = [c for c in result if c['key'] in GAUGE_CHANNELS]
    assert all(c['noisy'] is False for c in fixed)


def test_list_channels_includes_extras_with_label_and_unit():
    points = [_pt(float(i), extra={'Coolant Temperature': float(i)}) for i in range(20)]
    session = _session(points, extra_channel_meta={
        'Coolant Temperature': {'label': 'Coolant Temperature', 'unit': 'C'},
    })
    result = list_channels(session)
    entry = next(c for c in result if c['key'] == 'Coolant Temperature')
    assert entry['label'] == 'Coolant Temperature'
    assert entry['unit'] == 'C'
    assert entry['noisy'] is False  # varies + real-sensor-sounding name


def test_list_channels_flags_noisy_extra():
    points = [_pt(float(i), extra={'CAN Bus 1 Diagnostic': 0.0}) for i in range(20)]
    session = _session(points, extra_channel_meta={
        'CAN Bus 1 Diagnostic': {'label': 'CAN Bus 1 Diagnostic', 'unit': ''},
    })
    result = list_channels(session)
    entry = next(c for c in result if c['key'] == 'CAN Bus 1 Diagnostic')
    assert entry['noisy'] is True


def test_list_channels_no_extras_returns_only_fixed():
    session = _session([_pt(0.0)])
    result = list_channels(session)
    assert len(result) == len(GAUGE_CHANNELS)


def test_list_channels_empty_session():
    session = _session([])
    result = list_channels(session)
    assert len(result) == len(GAUGE_CHANNELS)
