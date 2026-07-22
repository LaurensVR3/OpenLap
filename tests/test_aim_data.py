import pytest
from aim_data import _find_col, _safe, is_aim_csv, load_csv, sniff_speed_unit
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


def test_find_col_bare_accelx():
    # Bare 'AccelX' naming convention (documented in module docstring) must
    # actually match — this was previously missing from _PATTERNS.
    cols = ['AccelX', 'AccelY', 'AccelZ']
    assert _find_col(cols, 'gforce_x') == 'AccelX'


def test_find_col_bare_accely():
    cols = ['AccelX', 'AccelY', 'AccelZ']
    assert _find_col(cols, 'gforce_y') == 'AccelY'


def test_find_col_bare_accelz():
    cols = ['AccelX', 'AccelY', 'AccelZ']
    assert _find_col(cols, 'gforce_z') == 'AccelZ'


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


def test_load_csv_source_speed_unit_ms(aim_csv_path):
    # Fixture's GPS_Speed column is tagged [m/s]
    session = load_csv(aim_csv_path)
    assert session.source_speed_unit == 'ms'


# ── sniff_speed_unit ─────────────────────────────────────────────────────────

def test_sniff_speed_unit_ms():
    assert sniff_speed_unit('GPS_Speed [m/s]') == 'ms'


def test_sniff_speed_unit_mph():
    assert sniff_speed_unit('GPS_Speed [mph]') == 'mph'


def test_sniff_speed_unit_untagged_defaults_kmh():
    assert sniff_speed_unit('GPS_Speed') == 'kmh'


def test_sniff_speed_unit_none():
    assert sniff_speed_unit(None) == 'kmh'


def test_load_csv_has_laps(aim_csv_path):
    session = load_csv(aim_csv_path)
    assert len(session.laps) > 0


def test_load_csv_bare_accel_headers_read_gforce(tmp_path):
    # AIM CSV using the bare 'AccelX/AccelY/AccelZ' naming convention documented
    # in the module docstring — previously matched nothing and silently read
    # gforce as 0.0 for every row.
    csv_path = tmp_path / "bare_accel.csv"
    csv_path.write_text(
        "# Session-Date: 2024-06-15T12:00:00Z\n"
        "Time (s),GPS_Speed [m/s],GPS_Latitude,GPS_Longitude,AccelX,AccelY,AccelZ,Lap\n"
        "0.0,0.0,50.4372,5.9719,0.5,0.6,1.0,0\n"
        "1.0,10.0,50.4373,5.9720,0.5,0.6,1.0,0\n"
    )
    session = load_csv(str(csv_path))
    pt = session.interpolate_at(0.0)
    assert pt is not None
    assert pt.gforce_x == pytest.approx(0.5)
    assert pt.gforce_y == pytest.approx(0.6)
    assert pt.gforce_z == pytest.approx(1.0)


def test_load_csv_empty_raises(tmp_path):
    empty = tmp_path / "empty_aim.csv"
    empty.write_text("# Session-Date: 2024-06-15T12:00:00Z\nTime (s),GPS_Speed [m/s]\n")
    with pytest.raises(NoDataRowsError):
        load_csv(str(empty))
