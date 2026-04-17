import pytest
from racebox_data import _detect_bike, load_csv, Lap, DataPoint
from exceptions import MissingHeaderError, NoDataRowsError


# ── _detect_bike ───────────────────────────────────────────────────────────────

def test_detect_bike_car():
    cols = ['Record', 'Time', 'GForceX', 'GForceY', 'GForceZ']
    assert _detect_bike(cols) is False


def test_detect_bike_bike():
    cols = ['Record', 'Time', 'GForceX', 'GForceZ', 'LeanAngle']
    assert _detect_bike(cols) is True


def test_detect_bike_requires_both_conditions():
    # Has LeanAngle but also has GForceY → not bike
    cols = ['Record', 'Time', 'GForceX', 'GForceY', 'GForceZ', 'LeanAngle']
    assert _detect_bike(cols) is False


# ── load_csv — basic session shape ─────────────────────────────────────────────

def test_load_csv_car_is_not_bike(racebox_car_session):
    assert racebox_car_session.is_bike is False


def test_load_csv_car_has_points(racebox_car_session):
    assert len(racebox_car_session.all_points) > 0


def test_load_csv_car_has_laps(racebox_car_session):
    assert len(racebox_car_session.laps) > 0


def test_load_csv_bike_is_bike(racebox_bike_csv_path):
    session = load_csv(racebox_bike_csv_path)
    assert session.is_bike is True


def test_load_csv_bike_lean_angle(racebox_bike_csv_path):
    session = load_csv(racebox_bike_csv_path)
    leans = [p.lean_angle for p in session.all_points]
    assert any(l != 0.0 for l in leans)  # fixture has non-zero lean angles


# ── elapsed is monotonically non-decreasing ────────────────────────────────────

def test_elapsed_monotonic(racebox_car_session):
    pts = racebox_car_session.all_points
    for a, b in zip(pts, pts[1:]):
        assert b.elapsed >= a.elapsed


# ── lap_elapsed resets to 0 at start of each lap ──────────────────────────────

def test_lap_elapsed_resets(racebox_car_session):
    for lap in racebox_car_session.laps:
        assert lap.points[0].lap_elapsed == pytest.approx(0.0, abs=1e-6)


def test_lap_elapsed_non_negative(racebox_car_session):
    for lap in racebox_car_session.laps:
        for pt in lap.points:
            assert pt.lap_elapsed >= 0.0


# ── outlap detection ───────────────────────────────────────────────────────────

def test_outlap_is_lap_zero(racebox_car_session):
    outlaps = [l for l in racebox_car_session.laps if l.lap_num == 0]
    assert len(outlaps) == 1
    assert outlaps[0].is_outlap is True


def test_non_outlap_laps_not_marked(racebox_car_session):
    for lap in racebox_car_session.laps:
        if lap.lap_num != 0:
            assert lap.is_outlap is False


# ── inlap detection ────────────────────────────────────────────────────────────

def test_inlap_detected_on_long_last_lap(racebox_car_session):
    # Lap 3 in the fixture is ~162s vs laps 1+2 at ~60s each — should be inlap
    timed = [l for l in racebox_car_session.laps if l.lap_num > 0]
    assert len(timed) >= 3
    assert timed[-1].is_inlap is True


# ── timed_laps / fastest_lap ───────────────────────────────────────────────────

def test_timed_laps_excludes_outlap(racebox_car_session):
    for lap in racebox_car_session.timed_laps:
        assert lap.lap_num != 0
        assert lap.is_outlap is False


def test_fastest_lap_is_shortest(racebox_car_session):
    timed = racebox_car_session.timed_laps
    fastest = racebox_car_session.fastest_lap
    if fastest and timed:
        assert fastest.duration == min(l.duration for l in timed)


# ── Lap.format_duration ────────────────────────────────────────────────────────

def test_format_duration_basic():
    lap = Lap(lap_num=1, points=[], duration=83.456, is_outlap=False)
    assert lap.format_duration() == "1:23.456"


def test_format_duration_sub_minute():
    lap = Lap(lap_num=1, points=[], duration=45.0, is_outlap=False)
    result = lap.format_duration()
    assert result.startswith("0:")


# ── Session.interpolate_at ─────────────────────────────────────────────────────

def test_interpolate_at_start(racebox_car_session):
    pts = racebox_car_session.all_points
    result = racebox_car_session.interpolate_at(pts[0].elapsed)
    assert result is not None
    assert result.speed == pytest.approx(pts[0].speed, abs=1e-6)


def test_interpolate_at_midpoint(racebox_car_session):
    pts = racebox_car_session.all_points
    p0, p1 = pts[1], pts[2]
    mid_t = (p0.elapsed + p1.elapsed) / 2
    result = racebox_car_session.interpolate_at(mid_t)
    assert result is not None
    expected_speed = (p0.speed + p1.speed) / 2
    assert result.speed == pytest.approx(expected_speed, abs=1e-6)


def test_interpolate_at_before_start_returns_none(racebox_car_session):
    assert racebox_car_session.interpolate_at(-1.0) is None


def test_interpolate_at_after_end_returns_none(racebox_car_session):
    last = racebox_car_session.all_points[-1].elapsed
    assert racebox_car_session.interpolate_at(last + 1.0) is None


# ── Error cases ────────────────────────────────────────────────────────────────

def test_load_csv_missing_header_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("col1,col2\n1,2\n")
    with pytest.raises(MissingHeaderError):
        load_csv(str(bad))


def test_load_csv_no_data_rows_raises(tmp_path):
    # Valid header, but all rows have non-numeric Record field
    bad = tmp_path / "empty.csv"
    bad.write_text("Record,Time,Latitude,Longitude,Altitude,Speed,GForceX,GForceY,GForceZ,Lap,GyroX,GyroY,GyroZ\n")
    with pytest.raises(NoDataRowsError):
        load_csv(str(bad))
