"""
Tests for session_merge.py — combining two telemetry Sessions into one.

Primary's own data (fixed fields, its own extras, lap structure) is never
overwritten; every channel the secondary session has is injected as an
additional, file-name-qualified extra channel (see QUALIFIABLE_FIELDS).

All tests use small synthetic Session/DataPoint fixtures — no real telemetry
files needed.
"""
import pytest

from data_model import DataPoint, Lap, Session
from session_merge import QUALIFIABLE_FIELDS, merge_sessions


def _pt(elapsed, **overrides):
    base = dict(
        record=0, time=None, lat=0.0, lon=0.0, alt=0.0, speed=0.0,
        gforce_x=0.0, gforce_y=0.0, gforce_z=0.0, lap=0,
        gyro_x=0.0, gyro_y=0.0, gyro_z=0.0, lean_angle=0.0,
        elapsed=elapsed, lap_elapsed=elapsed, rpm=0.0, exhaust_temp=0.0, gear=0,
    )
    base.update(overrides)
    return DataPoint(**base)


def _session(source, points, csv_path=None, is_outlap=True, is_inlap=False,
             extra_channel_meta=None):
    lap = Lap(
        lap_num=0, points=points,
        duration=points[-1].elapsed - points[0].elapsed,
        is_outlap=is_outlap, is_inlap=is_inlap,
    )
    return Session(
        source=source, date_utc='2024-01-01T00:00:00Z', track='Test',
        configuration='', session_type='', best_lap_time=0.0,
        all_points=points, laps=[lap], is_bike=False,
        csv_path=csv_path or f'/fake/{source}.csv',
        extra_channel_meta=extra_channel_meta or {},
    )


def _make_pair(n=10, offset=0.0, primary_extra=None, secondary_extra=None,
                secondary_csv_path='/fake/Secondary Data.ld'):
    """Primary spans elapsed 0..n-1; secondary spans a wider, offset-shifted
    range so interpolate_at never runs out of bounds for any offset used in
    these tests. Secondary's fixed fields are all non-zero/distinct so
    "was this value pulled from secondary" is unambiguous."""
    primary_pts = [
        _pt(float(i), lat=100.0 + i, lon=200.0 + i, **(primary_extra or {}))
        for i in range(n)
    ]
    secondary_pts = [
        _pt(
            float(i) - 5.0 + offset,
            rpm=1000.0 + 100 * i, gear=(i % 6) + 1, exhaust_temp=80.0 + i,
            speed=50.0 + i, lat=900.0 + i, lon=800.0 + i, alt=700.0 + i,
            gforce_x=0.5 + 0.01 * i, gforce_y=0.6 + 0.01 * i, gforce_z=0.7 + 0.01 * i,
            lean_angle=4.0 + i,
            **(secondary_extra or {}),
        )
        for i in range(n + 20)
    ]
    primary = _session('Primary', primary_pts)
    secondary = _session('Secondary', secondary_pts, csv_path=secondary_csv_path)
    return primary, secondary


# ── Primary is never overwritten ────────────────────────────────────────────

def test_primary_fixed_fields_are_never_overwritten():
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    for pt in merged.all_points:
        assert pt.rpm == 0.0
        assert pt.gear == 0
        assert pt.speed == 0.0


def test_primary_own_extra_channel_is_untouched():
    primary_pts = [_pt(float(i), extra={'ECU_ECT': 20.0 + i}) for i in range(10)]
    secondary_pts = [_pt(float(i), extra={'ECU_ECT': 999.0}) for i in range(10)]
    primary = _session('Primary', primary_pts,
                        extra_channel_meta={'ECU_ECT': {'label': 'ECU_ECT', 'unit': 'C'}})
    secondary = _session('Secondary', secondary_pts, csv_path='/fake/Secondary Data.ld',
                          extra_channel_meta={'ECU_ECT': {'label': 'ECU_ECT', 'unit': 'F'}})
    merged = merge_sessions(primary, secondary)
    vals = [p.extra.get('ECU_ECT') for p in merged.all_points]
    assert vals == [pytest.approx(20.0 + i) for i in range(10)]
    assert merged.extra_channel_meta['ECU_ECT']['unit'] == 'C'


# ── Secondary's channels are injected qualified by file name ───────────────

@pytest.mark.parametrize('attr', list(QUALIFIABLE_FIELDS.keys()))
def test_each_qualifiable_field_is_injected_under_qualified_key(attr):
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    label, unit = QUALIFIABLE_FIELDS[attr]
    qkey = f'{label} (Secondary Data.ld)'

    assert qkey in merged.extra_channel_meta
    assert merged.extra_channel_meta[qkey]['unit'] == unit

    vals = [p.extra.get(qkey) for p in merged.all_points]
    assert any(v != 0.0 for v in vals), f'{qkey} was not populated from secondary'
    # Primary's own (untouched) field for the same attribute must stay all-zero
    assert all(getattr(p, attr) == 0.0 for p in merged.all_points)


def test_secondary_extra_channel_is_injected_qualified():
    primary_pts = [_pt(float(i)) for i in range(10)]
    secondary_pts = [_pt(float(i), extra={'Coolant Temperature': 80.0 + i}) for i in range(10)]
    primary = _session('Primary', primary_pts)
    secondary = _session('Secondary', secondary_pts, csv_path='/fake/Secondary Data.ld',
                          extra_channel_meta={'Coolant Temperature': {'label': 'Coolant Temperature', 'unit': ''}})
    merged = merge_sessions(primary, secondary)
    qkey = 'Coolant Temperature (Secondary Data.ld)'
    assert qkey in merged.extra_channel_meta
    assert merged.extra_channel_meta[qkey]['label'] == qkey
    vals = [p.extra.get(qkey) for p in merged.all_points]
    assert vals == [pytest.approx(80.0 + i) for i in range(10)]


def test_overlapping_extra_channel_name_stays_distinct_per_file():
    # Both files have a channel literally named 'ECU_ECT' — primary's own
    # copy must stay unqualified while secondary's is injected qualified,
    # so both remain separately selectable rather than one overwriting the other.
    primary_pts = [_pt(float(i), extra={'ECU_ECT': 20.0 + i}) for i in range(10)]
    secondary_pts = [_pt(float(i), extra={'ECU_ECT': 999.0}) for i in range(10)]
    primary = _session('Primary', primary_pts,
                        extra_channel_meta={'ECU_ECT': {'label': 'ECU_ECT', 'unit': 'C'}})
    secondary = _session('Secondary', secondary_pts, csv_path='/fake/Secondary Data.ld',
                          extra_channel_meta={'ECU_ECT': {'label': 'ECU_ECT', 'unit': 'F'}})
    merged = merge_sessions(primary, secondary)
    assert 'ECU_ECT' in merged.extra_channel_meta
    assert 'ECU_ECT (Secondary Data.ld)' in merged.extra_channel_meta
    assert merged.extra_channel_meta['ECU_ECT']['unit'] == 'C'
    assert merged.extra_channel_meta['ECU_ECT (Secondary Data.ld)']['unit'] == 'F'
    primary_vals   = [p.extra.get('ECU_ECT') for p in merged.all_points]
    secondary_vals = [p.extra.get('ECU_ECT (Secondary Data.ld)') for p in merged.all_points]
    assert primary_vals == [pytest.approx(20.0 + i) for i in range(10)]
    assert all(v == pytest.approx(999.0) for v in secondary_vals)


def test_qualifier_uses_secondary_csv_basename():
    primary, secondary = _make_pair(secondary_csv_path='/some/dir/MoTeC Log.ld')
    merged = merge_sessions(primary, secondary)
    assert 'Speed (MoTeC Log.ld)' in merged.extra_channel_meta


# ── Offset shifting ────────────────────────────────────────────────────────

def test_offset_shifts_which_secondary_sample_is_used():
    primary, secondary = _make_pair()
    merged_no_offset = merge_sessions(primary, secondary, offset=0.0)
    merged_shifted    = merge_sessions(primary, secondary, offset=3.0)

    key = 'RPM (Secondary Data.ld)'
    rpm_no_offset = [p.extra.get(key) for p in merged_no_offset.all_points]
    rpm_shifted   = [p.extra.get(key) for p in merged_shifted.all_points]
    assert rpm_no_offset != rpm_shifted


def test_offset_convention_matches_secondary_elapsed_equals_primary_plus_offset():
    # secondary's rpm at elapsed=5 is 1000 + 100*10 = 2000 (secondary built with
    # elapsed = i - 5, so index i=10 -> elapsed=5).
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary, offset=3.0)
    pt2 = next(p for p in merged.all_points if p.elapsed == 2.0)
    assert pt2.extra['RPM (Secondary Data.ld)'] == pytest.approx(2000.0)


def test_secondary_out_of_range_leaves_point_with_only_primary_data():
    # Primary point far outside secondary's covered range: interpolate_at
    # returns None, so that point is passed through completely untouched.
    primary_pts = [_pt(0.0), _pt(1000.0)]
    secondary_pts = [_pt(0.0, rpm=5000.0), _pt(1.0, rpm=5000.0)]
    primary = _session('Primary', primary_pts)
    secondary = _session('Secondary', secondary_pts, csv_path='/fake/Secondary Data.ld')
    merged = merge_sessions(primary, secondary)
    far_pt = next(p for p in merged.all_points if p.elapsed == 1000.0)
    assert far_pt.extra == {}


# ── Lap structure preservation ────────────────────────────────────────────

def test_merged_laps_preserve_outlap_inlap_flags():
    primary, secondary = _make_pair()
    primary.laps[0].is_outlap = False
    primary.laps[0].is_inlap = True
    merged = merge_sessions(primary, secondary)
    assert merged.laps[0].is_outlap is False
    assert merged.laps[0].is_inlap is True
    assert merged.laps[0].lap_num == primary.laps[0].lap_num


def test_merged_lap_points_reference_merged_datapoints():
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    # lap.points must be the *merged* points (with the qualified secondary
    # extras filled in), not the original primary points.
    assert merged.laps[0].points == merged.all_points
    assert any(p.extra.get('RPM (Secondary Data.ld)') for p in merged.laps[0].points)


def test_merged_point_count_matches_primary():
    primary, secondary = _make_pair(n=10)
    merged = merge_sessions(primary, secondary)
    assert len(merged.all_points) == len(primary.all_points)


# ── Session-level metadata ────────────────────────────────────────────────

def test_merged_source_combines_both_names():
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    assert 'Primary' in merged.source
    assert 'Secondary' in merged.source


def test_merged_session_keeps_primary_metadata():
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    assert merged.track == primary.track
    assert merged.csv_path == primary.csv_path


def test_no_extra_channels_on_either_side_still_injects_qualifiable_fields():
    # Even with zero extra_channel_meta on both sides, the 8 qualifiable
    # fixed fields are always injected once a secondary is attached.
    primary, secondary = _make_pair()
    merged = merge_sessions(primary, secondary)
    assert len(merged.extra_channel_meta) == len(QUALIFIABLE_FIELDS)
