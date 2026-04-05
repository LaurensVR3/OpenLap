from pathlib import Path

import pytest

from gpx_data import is_gpx, load_gpx
from exceptions import NoDataRowsError, MissingHeaderError

FIXTURES = Path(__file__).parent / "fixtures"
GPX_PATH          = str(FIXTURES / "sample.gpx")
GPX_NO_SPEED_PATH = str(FIXTURES / "sample_no_speed.gpx")
CSV_PATH          = str(FIXTURES / "racebox_car.csv")


# ── is_gpx ─────────────────────────────────────────────────────────────────────

def test_is_gpx_positive():
    assert is_gpx(GPX_PATH) is True


def test_is_gpx_negative_csv():
    assert is_gpx(CSV_PATH) is False


def test_is_gpx_negative_wrong_extension(tmp_path):
    f = tmp_path / "track.txt"
    f.write_text("<gpx>...</gpx>")
    assert is_gpx(str(f)) is False


def test_is_gpx_nonexistent():
    assert is_gpx('/nonexistent/file.gpx') is False


# ── load_gpx — source and structure ───────────────────────────────────────────

def test_load_gpx_source():
    session = load_gpx(GPX_PATH)
    assert session.source == 'GPX'


def test_load_gpx_has_points():
    session = load_gpx(GPX_PATH)
    assert len(session.all_points) == 8


def test_load_gpx_has_one_lap():
    session = load_gpx(GPX_PATH)
    assert len(session.laps) == 1
    assert session.laps[0].lap_num == 1


def test_load_gpx_lap_is_not_outlap():
    session = load_gpx(GPX_PATH)
    assert session.laps[0].is_outlap is False


def test_load_gpx_track_name():
    session = load_gpx(GPX_PATH)
    assert 'Spa' in session.track or session.track != ''


def test_load_gpx_date_utc():
    session = load_gpx(GPX_PATH)
    assert '2024-06-15' in session.date_utc


def test_load_gpx_not_bike():
    session = load_gpx(GPX_PATH)
    assert session.is_bike is False


# ── Speed ──────────────────────────────────────────────────────────────────────

def test_load_gpx_speed_from_extension():
    # Fixture has extension speed — peak should reflect the 30 m/s → 108 km/h point
    session = load_gpx(GPX_PATH)
    max_speed = max(p.speed for p in session.all_points)
    assert max_speed > 50.0   # 30 m/s * 3.6 = 108 km/h, smoothed down somewhat


def test_load_gpx_speed_non_negative():
    session = load_gpx(GPX_PATH)
    for pt in session.all_points:
        assert pt.speed >= 0.0


def test_load_gpx_derived_speed():
    # File with no extension speed — speed must still be derived and non-negative
    session = load_gpx(GPX_NO_SPEED_PATH)
    speeds = [p.speed for p in session.all_points]
    assert all(s >= 0.0 for s in speeds)
    assert max(speeds) > 0.0   # moving track — should have positive speed


# ── Elapsed time ───────────────────────────────────────────────────────────────

def test_load_gpx_elapsed_starts_at_zero():
    session = load_gpx(GPX_PATH)
    assert session.all_points[0].elapsed == pytest.approx(0.0)


def test_load_gpx_elapsed_monotonic():
    session = load_gpx(GPX_PATH)
    pts = session.all_points
    for a, b in zip(pts, pts[1:]):
        assert b.elapsed >= a.elapsed


def test_load_gpx_duration():
    session = load_gpx(GPX_PATH)
    # Fixture spans 7 seconds (13:00:00 to 13:00:07)
    assert session.laps[0].duration == pytest.approx(7.0, abs=0.1)


# ── Derived G-forces ───────────────────────────────────────────────────────────

def test_load_gpx_gforce_x_range():
    session = load_gpx(GPX_PATH)
    for pt in session.all_points:
        assert -5.0 <= pt.gforce_x <= 5.0


def test_load_gpx_gforce_y_range():
    session = load_gpx(GPX_PATH)
    for pt in session.all_points:
        assert -5.0 <= pt.gforce_y <= 5.0


# ── interpolate_at works with GPX session ─────────────────────────────────────

def test_load_gpx_interpolate_at_midpoint():
    session = load_gpx(GPX_PATH)
    pts = session.all_points
    mid_t = (pts[2].elapsed + pts[3].elapsed) / 2
    result = session.interpolate_at(mid_t)
    assert result is not None


# ── Error cases ────────────────────────────────────────────────────────────────

def test_load_gpx_empty_trkseg_raises(tmp_path):
    bad = tmp_path / "empty.gpx"
    bad.write_text('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg></trkseg></trk></gpx>')
    with pytest.raises(NoDataRowsError):
        load_gpx(str(bad))


def test_load_gpx_corrupt_xml_raises(tmp_path):
    bad = tmp_path / "corrupt.gpx"
    bad.write_text('<gpx><this is not valid xml')
    with pytest.raises(MissingHeaderError):
        load_gpx(str(bad))


# ── session_scanner integration ────────────────────────────────────────────────

def test_scan_csvs_finds_gpx(tmp_path):
    from session_scanner import scan_csvs
    import shutil
    shutil.copy(GPX_PATH, tmp_path / "track.gpx")
    results = scan_csvs(str(tmp_path))
    gpx_results = [r for r in results if r.endswith('.gpx')]
    assert len(gpx_results) == 1


def test_csv_source_gpx():
    from session_scanner import _csv_source
    assert _csv_source(GPX_PATH) == 'GPX'


def test_read_csv_start_time_gpx():
    from session_scanner import _read_csv_start_time
    dt = _read_csv_start_time(GPX_PATH)
    assert dt is not None
    assert dt.year == 2024
    assert dt.month == 6
    assert dt.tzinfo is not None
