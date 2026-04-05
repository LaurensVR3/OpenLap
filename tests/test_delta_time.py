"""Tests for delta_time.py — compute_lap_profile and make_delta_fn."""
import sys
import math
from pathlib import Path

import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from racebox_data import DataPoint, Lap
from delta_time import compute_lap_profile, make_delta_fn, _haversine_m


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_point(lat, lon, lap_elapsed, elapsed=0.0):
    """Minimal DataPoint for delta-time tests."""
    from datetime import datetime
    return DataPoint(
        record=0, time=datetime(2024, 1, 1),
        lat=lat, lon=lon, alt=0.0, speed=100.0,
        gforce_x=0.0, gforce_y=0.0, gforce_z=1.0,
        lap=1, gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
        lean_angle=0.0, elapsed=elapsed, lap_elapsed=lap_elapsed,
    )


def _straight_lap(n=5, lat_step=0.001, duration=60.0):
    """
    Build a Lap with `n` points spaced lat_step degrees apart along a meridian.
    lap_elapsed goes 0 .. duration evenly.
    """
    pts = [
        _make_point(
            lat=52.0 + i * lat_step,
            lon=5.0,
            lap_elapsed=duration * i / (n - 1),
        )
        for i in range(n)
    ]
    return Lap(lap_num=1, points=pts, duration=duration)


def _stationary_lap(n=5, duration=60.0):
    """Lap with all points at the same location (GPS-less fallback trigger)."""
    pts = [
        _make_point(lat=52.0, lon=5.0, lap_elapsed=duration * i / (n - 1))
        for i in range(n)
    ]
    return Lap(lap_num=1, points=pts, duration=duration)


# ── _haversine_m ──────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine_m(52.0, 5.0, 52.0, 5.0) == 0.0


def test_haversine_known_distance():
    # 1 degree of latitude ≈ 111,195 m
    d = _haversine_m(52.0, 5.0, 53.0, 5.0)
    assert 111_000 < d < 111_400


def test_haversine_symmetry():
    d1 = _haversine_m(52.0, 5.0, 52.5, 5.5)
    d2 = _haversine_m(52.5, 5.5, 52.0, 5.0)
    assert abs(d1 - d2) < 1e-6


# ── compute_lap_profile ───────────────────────────────────────────────────────

def test_profile_shape():
    lap = _straight_lap(n=5)
    elapsed, dist = compute_lap_profile(lap)
    assert len(elapsed) == 5
    assert len(dist) == 5


def test_profile_starts_at_zero():
    lap = _straight_lap(n=5)
    elapsed, dist = compute_lap_profile(lap)
    assert elapsed[0] == pytest.approx(0.0)
    assert dist[0] == pytest.approx(0.0)


def test_profile_elapsed_matches_points():
    lap = _straight_lap(n=5, duration=60.0)
    elapsed, _ = compute_lap_profile(lap)
    assert elapsed[-1] == pytest.approx(60.0)


def test_profile_distance_monotone():
    lap = _straight_lap(n=10)
    _, dist = compute_lap_profile(lap)
    diffs = np.diff(dist)
    assert np.all(diffs >= 0), "Distance should be non-decreasing"


def test_profile_distance_nonzero_for_moving_lap():
    lap = _straight_lap(n=5, lat_step=0.001)
    _, dist = compute_lap_profile(lap)
    assert dist[-1] > 50.0, "Expected meaningful track length"


def test_profile_empty_lap():
    lap = Lap(lap_num=1, points=[], duration=60.0)
    elapsed, dist = compute_lap_profile(lap)
    assert list(elapsed) == [0.0]
    assert list(dist) == [0.0]


def test_profile_single_point():
    pts = [_make_point(52.0, 5.0, 0.0)]
    lap = Lap(lap_num=1, points=pts, duration=0.0)
    elapsed, dist = compute_lap_profile(lap)
    assert len(elapsed) == 1
    assert dist[0] == 0.0


# ── make_delta_fn — normal (GPS) path ─────────────────────────────────────────

def test_delta_zero_at_identical_laps():
    """Same lap used as both reference and current → delta should be ~0 everywhere."""
    lap = _straight_lap(n=10, duration=60.0)
    fn = make_delta_fn(lap, current_lap_duration=60.0)
    _, dist = compute_lap_profile(lap)
    elapsed_arr, _ = compute_lap_profile(lap)

    for e, d in zip(elapsed_arr, dist):
        delta = fn(e, d)
        assert abs(delta) < 0.01, f"Expected ~0 delta at elapsed={e:.1f}, got {delta:.4f}"


def test_delta_positive_when_current_is_slower():
    """If current lap takes longer at same distance → positive delta."""
    ref_lap = _straight_lap(n=5, lat_step=0.001, duration=60.0)
    fn = make_delta_fn(ref_lap, current_lap_duration=70.0)

    _, ref_dist = compute_lap_profile(ref_lap)
    half_dist = float(ref_dist[-1]) / 2.0
    # At half distance, reference arrives at ~30 s; current (slower) at ~35 s
    delta = fn(35.0, half_dist)
    assert delta > 0, f"Expected positive delta (slower), got {delta}"


def test_delta_negative_when_current_is_faster():
    """If current lap is faster at same distance → negative delta."""
    ref_lap = _straight_lap(n=5, lat_step=0.001, duration=60.0)
    fn = make_delta_fn(ref_lap, current_lap_duration=50.0)

    _, ref_dist = compute_lap_profile(ref_lap)
    half_dist = float(ref_dist[-1]) / 2.0
    # At half distance, reference arrives at ~30 s; current (faster) at ~25 s
    delta = fn(25.0, half_dist)
    assert delta < 0, f"Expected negative delta (faster), got {delta}"


def test_delta_clamps_at_track_end():
    """Distances beyond the reference track end should not raise."""
    lap = _straight_lap(n=5, duration=60.0)
    fn = make_delta_fn(lap, current_lap_duration=60.0)
    _, dist = compute_lap_profile(lap)
    total = float(dist[-1])

    # Should not raise; just return a value
    result = fn(65.0, total * 1.5)
    assert isinstance(result, float)


# ── make_delta_fn — GPS-less fallback ─────────────────────────────────────────

def test_fallback_triggered_for_stationary_lap():
    """A lap where all GPS points are identical should trigger the fallback."""
    ref_lap = _stationary_lap(n=5, duration=60.0)
    # Should not raise
    fn = make_delta_fn(ref_lap, current_lap_duration=70.0)
    # Fallback: at 50% of current lap (35 s) vs 50% of ref (30 s) → delta ≈ +5
    delta = fn(35.0, 0.0)
    assert delta == pytest.approx(5.0, abs=0.1)


def test_fallback_zero_when_same_duration():
    ref_lap = _stationary_lap(n=5, duration=60.0)
    fn = make_delta_fn(ref_lap, current_lap_duration=60.0)
    # At any point, current == reference time fraction → delta == 0
    assert fn(30.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert fn(60.0, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_fallback_negative_when_faster():
    ref_lap = _stationary_lap(n=5, duration=60.0)
    fn = make_delta_fn(ref_lap, current_lap_duration=50.0)
    # At 25 s into current (50% of 50 s), reference is at 30 s (50% of 60 s)
    delta = fn(25.0, 0.0)
    assert delta < 0


# ── gauge_channels integration ────────────────────────────────────────────────

def test_delta_time_in_gauge_channels():
    from gauge_channels import GAUGE_CHANNELS
    assert 'delta_time' in GAUGE_CHANNELS


def test_delta_time_channel_metadata():
    from gauge_channels import GAUGE_CHANNELS
    ch = GAUGE_CHANNELS['delta_time']
    assert ch['hist_key'] == 'delta_time'
    assert ch['symmetric'] is True
    assert ch['min'] < 0
    assert ch['max'] > 0


def test_gauge_data_delta_time():
    from gauge_channels import gauge_data
    history = [{'delta_time': v} for v in [-1.0, -0.5, 0.2, 0.8]]
    result = gauge_data('delta_time', history)
    assert result['value'] == pytest.approx(0.8)
    assert result['history_vals'] == [-1.0, -0.5, 0.2, 0.8]
    assert result['symmetric'] is True


def test_gauge_data_delta_time_empty_history():
    from gauge_channels import gauge_data
    result = gauge_data('delta_time', [])
    assert isinstance(result['value'], float)


# ── gauge_delta style smoke test ──────────────────────────────────────────────

def test_gauge_delta_renders_positive():
    import importlib, sys
    # Avoid polluting other tests with Matplotlib state
    mod = importlib.import_module('styles.gauge_delta')
    data = {
        'value': 1.234,
        'history_vals': [0.0, 0.5, 1.0, 1.234],
        'label': 'Delta',
    }
    result = mod.render(data, w=120, h=160)
    assert result is not None
    arr = np.array(result)
    assert arr.shape == (160, 120, 4)


def test_gauge_delta_renders_negative():
    import importlib
    mod = importlib.import_module('styles.gauge_delta')
    data = {
        'value': -0.567,
        'history_vals': [0.3, 0.1, -0.2, -0.567],
        'label': 'Delta',
    }
    result = mod.render(data, w=120, h=160)
    assert result is not None


def test_gauge_delta_renders_neutral():
    import importlib
    mod = importlib.import_module('styles.gauge_delta')
    data = {
        'value': 0.05,
        'history_vals': [0.05],
        'label': 'Delta',
    }
    result = mod.render(data, w=120, h=160)
    assert result is not None


def test_gauge_delta_in_styles_list():
    from gauge_channels import GAUGE_STYLES, GAUGE_STYLES_BIKE, GAUGE_STYLES_CAR
    assert 'Delta' in GAUGE_STYLES
    assert 'Delta' in GAUGE_STYLES_BIKE
    assert 'Delta' in GAUGE_STYLES_CAR
