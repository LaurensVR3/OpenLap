import pandas as pd
import pytest
from aim_data import _find_col, _safe, is_aim_csv, load_csv, sniff_speed_unit
from exceptions import NoDataRowsError


# ── _find_col ──────────────────────────────────────────────────────────────────
# _find_col() now takes a DataFrame (not a bare column-name list) since
# _PREFER_NONZERO_FIELDS needs the actual data to tell a genuinely-populated
# candidate apart from one that's flat 0.0 throughout. _df() below defaults
# every column to non-zero samples so plain name-matching tests are
# unaffected by that check; the flat-vs-nonzero behavior itself is tested
# separately below.

def _df(cols, data=None):
    data = data or {}
    return pd.DataFrame({c: data.get(c, [1.0, 1.0]) for c in cols})


def test_find_col_speed():
    df = _df(['GPS_Speed [m/s]', 'GPS_Latitude', 'GPS_Longitude'])
    assert _find_col(df, 'speed') == 'GPS_Speed [m/s]'


def test_find_col_lat():
    df = _df(['GPS_Speed [m/s]', 'GPS_Latitude', 'GPS_Longitude'])
    assert _find_col(df, 'lat') == 'GPS_Latitude'


def test_find_col_missing_returns_none():
    df = _df(['GPS_Latitude', 'GPS_Longitude'])
    assert _find_col(df, 'rpm') is None


def test_find_col_case_insensitive():
    df = _df(['GPS_SPEED [km/h]'])
    assert _find_col(df, 'speed') == 'GPS_SPEED [km/h]'


def test_find_col_bare_accelx():
    # Bare 'AccelX' naming convention (documented in module docstring) must
    # actually match — this was previously missing from _PATTERNS.
    df = _df(['AccelX', 'AccelY', 'AccelZ'])
    assert _find_col(df, 'gforce_x') == 'AccelX'


def test_find_col_bare_accely():
    df = _df(['AccelX', 'AccelY', 'AccelZ'])
    assert _find_col(df, 'gforce_y') == 'AccelY'


def test_find_col_bare_accelz():
    df = _df(['AccelX', 'AccelY', 'AccelZ'])
    assert _find_col(df, 'gforce_z') == 'AccelZ'


def test_find_col_gear_prefers_ecu_gear():
    # CAN-fed AIM loggers (e.g. wired to a MoTeC M150) export both an
    # ECU_GEAR channel and a PreCalcGear estimate — ECU_GEAR should win.
    df = _df(['ECU_GEAR [gear]', 'PreCalcGear'])
    assert _find_col(df, 'gear') == 'ECU_GEAR [gear]'


def test_find_col_gear_bare_fallback():
    df = _df(['Gear'])
    assert _find_col(df, 'gear') == 'Gear'


def test_find_col_gear_prefers_flat_named_match_over_unrelated_nonzero_column():
    # gear is NOT in _PREFER_NONZERO_FIELDS: a flat-zero ECU_GEAR channel
    # (e.g. neutral for a whole short capture) must still win over some
    # other column that merely happens to also match a gear-ish pattern —
    # second-guessing a flat discrete-state value risks swapping in data
    # that isn't even the same kind of reading.
    df = _df(['ECU_GEAR [gear]', 'ECU_GEAR_LV [#]'],
             data={'ECU_GEAR [gear]': [0.0, 0.0], 'ECU_GEAR_LV [#]': [622.3, 622.3]})
    assert _find_col(df, 'gear') == 'ECU_GEAR [gear]'


# ── _find_col — _PREFER_NONZERO_FIELDS (rpm, gforce_x/y/z) ────────────────────

def test_find_col_rpm_prefers_ecu_rpm_over_unpopulated_rpm():
    df = _df(['RPM [rpm]', 'ECU RPM [rpm]'],
             data={'RPM [rpm]': [0.0, 0.0], 'ECU RPM [rpm]': [0.0, 4500.0]})
    assert _find_col(df, 'rpm') == 'ECU RPM [rpm]'


def test_find_col_gforce_skips_flat_gps_computed_column():
    # A GPS-computed accel channel can exist in the header but never get
    # populated (e.g. no GPS lock during a short capture) — a raw
    # accelerometer channel with real data should be preferred.
    df = _df(['GPS LonAcc [g]', 'InlineAcc [g]'],
             data={'GPS LonAcc [g]': [0.0, 0.0], 'InlineAcc [g]': [0.0, 1.02]})
    assert _find_col(df, 'gforce_x') == 'InlineAcc [g]'


def test_find_col_gforce_falls_back_to_flat_when_nothing_is_populated():
    # If every candidate is flat, still return one deterministically rather
    # than None (matches _safe()'s existing "absent -> 0.0" convention).
    df = _df(['GPS LonAcc [g]', 'AccelX'],
             data={'GPS LonAcc [g]': [0.0, 0.0], 'AccelX': [0.0, 0.0]})
    assert _find_col(df, 'gforce_x') == 'GPS LonAcc [g]'


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


def test_load_csv_ecu_gear_extracted(tmp_path):
    # Previously _PATTERNS had no 'gear' entry at all, so DataPoint.gear stayed
    # 0.0 even when the CAN-fed ECU_GEAR column was right there in the CSV.
    csv_path = tmp_path / "ecu_gear.csv"
    csv_path.write_text(
        "# Session-Date: 2024-06-15T12:00:00Z\n"
        "Time (s),GPS_Speed [m/s],GPS_Latitude,GPS_Longitude,ECU_GEAR [gear],Lap\n"
        "0.0,0.0,50.4372,5.9719,0,0\n"
        "1.0,10.0,50.4373,5.9720,3,0\n"
    )
    session = load_csv(str(csv_path))
    gears = [p.gear for p in session.all_points]
    assert gears == [0, 3]


def test_load_csv_extra_channels_captured(tmp_path):
    # Any column not mapped to a fixed field (e.g. CAN-fed ECU_ECT) becomes
    # a generic DataPoint.extra entry, keyed by its own name.
    csv_path = tmp_path / "extras.csv"
    csv_path.write_text(
        "# Session-Date: 2024-06-15T12:00:00Z\n"
        "Time (s),GPS_Speed [m/s],GPS_Latitude,GPS_Longitude,ECU_ECT [C],Lap\n"
        "0.0,0.0,50.4372,5.9719,80.0,0\n"
        "1.0,10.0,50.4373,5.9720,90.0,0\n"
    )
    session = load_csv(str(csv_path))
    assert 'ECU_ECT' in session.extra_channel_meta
    assert session.extra_channel_meta['ECU_ECT'] == {'label': 'ECU_ECT', 'unit': 'C'}
    vals = [p.extra.get('ECU_ECT') for p in session.all_points]
    assert vals == [pytest.approx(80.0), pytest.approx(90.0)]


def test_load_csv_extra_channels_exclude_fixed_fields(tmp_path):
    csv_path = tmp_path / "extras2.csv"
    csv_path.write_text(
        "# Session-Date: 2024-06-15T12:00:00Z\n"
        "Time (s),GPS_Speed [m/s],GPS_Latitude,GPS_Longitude,ECU_ECT [C],Lap\n"
        "0.0,0.0,50.4372,5.9719,80.0,0\n"
    )
    session = load_csv(str(csv_path))
    for fixed_col in ('GPS_Speed', 'GPS_Latitude', 'GPS_Longitude', 'Lap'):
        assert fixed_col not in session.extra_channel_meta


def test_load_csv_extra_channel_no_unit_suffix(tmp_path):
    # Columns without a "[unit]" suffix still become extras, with unit=''.
    csv_path = tmp_path / "extras3.csv"
    csv_path.write_text(
        "# Session-Date: 2024-06-15T12:00:00Z\n"
        "Time (s),GPS_Speed [m/s],ANTI_LAG_STATE\n"
        "0.0,0.0,1\n"
    )
    session = load_csv(str(csv_path))
    assert session.extra_channel_meta['ANTI_LAG_STATE'] == {'label': 'ANTI_LAG_STATE', 'unit': ''}


def test_load_csv_empty_raises(tmp_path):
    empty = tmp_path / "empty_aim.csv"
    empty.write_text("# Session-Date: 2024-06-15T12:00:00Z\nTime (s),GPS_Speed [m/s]\n")
    with pytest.raises(NoDataRowsError):
        load_csv(str(empty))
