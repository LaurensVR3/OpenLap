import pytest
from aim_data import _find_col, _safe, is_aim_csv, load_csv
from exceptions import NoDataRowsError


# ── _find_col ──────────────────────────────────────────────────────────────────

def test_find_col_speed():
    cols = ['GPS_Speed [m/s]', 'GPS_Latitude', 'GPS_Longitude']
    assert _find_col(cols, 'speed') == 'GPS_Speed [m/s]'


def test_find_col_lat():
    cols = ['GPS_Speed [m/s]', 'GPS_Latitude', 'GPS_Longitude']
    assert _find_col(cols, 'lat') == 'GPS_Latitude'


def test_find_col_missing_returns_none():
    cols = ['GPS_Latitude', 'GPS_Longitude']
    assert _find_col(cols, 'rpm') is None


def test_find_col_case_insensitive():
    cols = ['GPS_SPEED [km/h]']
    assert _find_col(cols, 'speed') == 'GPS_SPEED [km/h]'


# ── _safe ──────────────────────────────────────────────────────────────────────

def test_safe_valid_float():
    assert _safe('12.5') == pytest.approx(12.5)


def test_safe_nan_returns_default():
    assert _safe('nan') == pytest.approx(0.0)


def test_safe_inf_returns_default():
    assert _safe('inf') == pytest.approx(0.0)


def test_safe_negative_inf_returns_default():
    assert _safe('-inf') == pytest.approx(0.0)


def test_safe_non_numeric_returns_default():
    assert _safe('abc') == pytest.approx(0.0)


def test_safe_none_returns_default():
    assert _safe(None) == pytest.approx(0.0)


def test_safe_custom_default():
    assert _safe('nan', default=-1.0) == pytest.approx(-1.0)


# ── is_aim_csv ────────────────────────────────────────────────────────────────

def test_is_aim_csv_positive(aim_csv_path):
    assert is_aim_csv(aim_csv_path) is True


def test_is_aim_csv_negative_racebox(racebox_car_csv_path):
    assert is_aim_csv(racebox_car_csv_path) is False


def test_is_aim_csv_negative_plain(not_telemetry_csv_path):
    assert is_aim_csv(not_telemetry_csv_path) is False


def test_is_aim_csv_nonexistent():
    assert is_aim_csv('/nonexistent/path/file.csv') is False


# ── load_csv ───────────────────────────────────────────────────────────────────

def test_load_csv_source(aim_csv_path):
    session = load_csv(aim_csv_path)
    assert session.source == 'AIM Mychron'


def test_load_csv_has_points(aim_csv_path):
    session = load_csv(aim_csv_path)
    assert len(session.all_points) > 0


def test_load_csv_speed_unit_conversion(aim_csv_path):
    # Fixture uses [m/s] column — speeds should be multiplied by 3.6
    session = load_csv(aim_csv_path)
    # Row at t=1.0 has GPS_Speed=10.0 m/s → expect 36.0 km/h
    pt = session.interpolate_at(1.0)
    assert pt is not None
    assert pt.speed == pytest.approx(36.0, abs=1.0)


def test_load_csv_session_date_from_comment(aim_csv_path):
    session = load_csv(aim_csv_path)
    assert '2024-06-15' in session.date_utc


def test_load_csv_has_laps(aim_csv_path):
    session = load_csv(aim_csv_path)
    assert len(session.laps) > 0


def test_load_csv_empty_raises(tmp_path):
    empty = tmp_path / "empty_aim.csv"
    empty.write_text("# Session-Date: 2024-06-15T12:00:00Z\nTime (s),GPS_Speed [m/s]\n")
    with pytest.raises(NoDataRowsError):
        load_csv(str(empty))
