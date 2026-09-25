"""data_model.build_laps: the one way every loader cuts a session into laps."""
from datetime import datetime, timezone

import pytest

from data_model import DataPoint, build_laps

_T = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _pts(spec, hz=10):
    """spec: [(lap_number, seconds), ...] → points at *hz*, in order."""
    out, t = [], 0.0
    for lap, secs in spec:
        for _ in range(int(round(secs * hz))):
            out.append(DataPoint(record=len(out), time=_T, lat=0, lon=0, alt=0, speed=0,
                                 gforce_x=0, gforce_y=0, gforce_z=1, lap=lap,
                                 gyro_x=0, gyro_y=0, gyro_z=0, elapsed=t))
            t += 1.0 / hz
    return out


def test_duration_runs_to_the_next_lap_start():
    laps = build_laps(_pts([(0, 5), (1, 30), (2, 31), (3, 29)]))
    assert [round(l.duration, 6) for l in laps] == [5.0, 30.0, 31.0, 29.0]


def test_last_lap_counts_its_final_sample_interval():
    laps = build_laps(_pts([(1, 30), (2, 30)]))
    assert laps[-1].duration == pytest.approx(30.0)


def test_racebox_trailing_lap_zero_is_a_separate_inlap():
    """RaceBox numbers the drive out and the drive back both 0; grouping by
    number merged them into one outlap spanning the whole session."""
    laps = build_laps(_pts([(0, 20), (1, 30), (2, 30), (3, 30), (0, 40)]))
    assert [l.lap_num for l in laps] == [0, 1, 2, 3, 4]
    assert laps[0].is_outlap and laps[0].duration == pytest.approx(20.0)
    assert laps[-1].is_inlap and laps[-1].duration == pytest.approx(40.0)
    assert laps[-1].points[0].lap_elapsed == 0.0
    assert [l.lap_num for l in laps if not (l.is_outlap or l.is_inlap)] == [1, 2, 3]


def test_recording_pause_does_not_stretch_the_lap():
    pts = _pts([(1, 30), (2, 30)])
    for p in pts[300:]:
        p.elapsed += 120.0          # logger paused for two minutes between laps
    laps = build_laps(pts)
    assert laps[0].duration == pytest.approx(30.0)


@pytest.mark.parametrize('spec,which', [
    ([(1, 30), (2, 30), (3, 30), (4, 60)], 'last_slow'),     # drive back to the pits
    ([(1, 30), (2, 30), (3, 30), (4, 5)],  'last_short'),    # recording cut off
    ([(1, 5), (2, 30), (3, 30), (4, 30)],  'first_short'),   # recording started mid-lap
])
def test_same_classification_rules_for_every_source(spec, which):
    laps = build_laps(_pts(spec))
    if which == 'first_short':
        assert laps[0].is_outlap
    else:
        assert laps[-1].is_inlap


def test_device_lap_clock_is_kept_and_a_lap_under_way_counts_its_earlier_part():
    pts = _pts([(1, 30), (2, 30)])
    for p in pts[:300]:
        p.lap_elapsed = 10.0 + p.elapsed      # recording began 10 s into lap 1
    for p in pts[300:]:
        p.lap_elapsed = p.elapsed - 30.0
    laps = build_laps(pts, keep_lap_elapsed=True)
    assert laps[0].points[0].lap_elapsed == 10.0
    assert laps[0].duration == pytest.approx(40.0)


# ── laps from the GPS track (lap_detection) ───────────────────────────────────

import math


def _track_pts(laps=5, lap_s=30.0, hz=10, radius_m=80.0, straight=False):
    """A circle driven *laps* times at constant speed (or a straight road)."""
    out = []
    n = int(laps * lap_s * hz)
    for i in range(n):
        t = i / hz
        if straight:
            east, north = t * 20.0, 0.0
        else:
            a = 2 * math.pi * t / lap_s
            east, north = radius_m * math.sin(a), radius_m * (1 - math.cos(a))
        out.append(DataPoint(record=i, time=_T, lat=50.0 + north / 111_000.0,
                             lon=5.0 + east / (111_000.0 * math.cos(math.radians(50.0))),
                             alt=0, speed=60, gforce_x=0, gforce_y=0, gforce_z=1, lap=1,
                             gyro_x=0, gyro_y=0, gyro_z=0, elapsed=t))
    return out


def test_laps_found_from_a_circuit_track_are_the_lap_period():
    from data_model import laps_from_track
    laps = laps_from_track(_track_pts(laps=5, lap_s=30.0))
    full = [l for l in laps if not l.is_outlap and not l.is_inlap]
    assert len(full) >= 3
    assert all(l.duration == pytest.approx(30.0, abs=0.02) for l in full)


def test_a_point_to_point_track_has_no_laps():
    from data_model import laps_from_track
    assert laps_from_track(_track_pts(laps=1, lap_s=120.0, straight=True)) is None


def test_crossings_are_timed_between_samples():
    """At 10 Hz a crossing lands between samples; its time is interpolated,
    so laps are not quantised to the 0.1 s sample grid."""
    import lap_detection as ld
    pts = _track_pts(laps=4, lap_s=30.03, hz=10)
    line = ld.auto_finish_line(pts)
    cr = ld.crossings(pts, line)
    assert all(b - a == pytest.approx(30.03, abs=0.01) for a, b in zip(cr, cr[1:]))


def test_logger_boundaries_are_refined_to_the_line():
    """A logger that switches lap on its first sample past the line: the lap
    is timed from the crossing itself."""
    pts = _track_pts(laps=5, lap_s=30.03, hz=10)
    for p in pts:
        p.lap = int(p.elapsed // 30.03)     # lap k from the first sample past k laps
    laps = build_laps(pts)
    complete = [l for l in laps if not l.is_outlap and not l.is_inlap][:-1]   # last is cut off
    assert len(complete) >= 3 and all(l.duration == pytest.approx(30.03, abs=0.01) for l in complete)
