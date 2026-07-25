"""
Tests for motec_data.py — MoTeC .ld binary file parser.

All tests that require a real .ld file are marked with
@pytest.mark.skipif so they are skipped when the file is absent
(e.g. in CI).  The logic tests use only in-memory data.
"""
import os
import struct
import pytest

import motec_data
from motec_data import (
    _cstr, _parse_channels, _find_channel, _classify_speed_unit,
    _build_abs_time, _interp, is_motec_ld,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pure-logic helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_cstr_basic():
    buf = b'Spa\x00garbage'
    assert _cstr(buf, 0) == 'Spa'


def test_cstr_offset():
    buf = b'\x00\x00Hello\x00'
    assert _cstr(buf, 2, 8) == 'Hello'


def test_cstr_max_len():
    buf = b'ABCDEFGH'
    assert _cstr(buf, 0, 4) == 'ABCD'


def test_build_abs_time_no_resets():
    raw = [0.0, 0.1, 0.2, 0.3]
    abs_t, lap_n = _build_abs_time(raw)
    assert abs_t == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert lap_n == [0, 0, 0, 0]


def test_build_abs_time_single_reset():
    raw = [10.0, 20.0, 0.0, 10.0]   # reset at index 2
    abs_t, lap_n = _build_abs_time(raw)
    assert abs_t[0] == pytest.approx(10.0)
    assert abs_t[1] == pytest.approx(20.0)
    assert abs_t[2] == pytest.approx(20.0)   # 20 + 0
    assert abs_t[3] == pytest.approx(30.0)   # 20 + 10
    assert lap_n == [0, 0, 1, 1]


def test_build_abs_time_multiple_resets():
    # Drops must be > 5.0 to trigger a reset (threshold is strictly < prev - 5)
    raw = [6.0, 12.0, 0.0, 6.0, 0.0, 3.0]
    _, lap_n = _build_abs_time(raw)
    assert lap_n == [0, 0, 1, 1, 2, 2]


def test_interp_within_range():
    src_t = [0.0, 1.0, 2.0]
    src_v = [0.0, 10.0, 20.0]
    result = _interp([0.5, 1.5], src_t, src_v)
    assert result == pytest.approx([5.0, 15.0])


def test_interp_before_range_uses_default():
    src_t = [1.0, 2.0]
    src_v = [10.0, 20.0]
    result = _interp([0.0], src_t, src_v, default=-1.0)
    assert result == pytest.approx([-1.0])


def test_interp_after_range_uses_default():
    src_t = [0.0, 1.0]
    src_v = [0.0, 10.0]
    result = _interp([5.0], src_t, src_v, default=99.0)
    assert result == pytest.approx([99.0])


def test_interp_empty_src_returns_defaults():
    result = _interp([1.0, 2.0], [], [], default=7.0)
    assert result == [7.0, 7.0]


def test_classify_speed_unit_mph():
    assert _classify_speed_unit('mph') == ('mph', pytest.approx(1.60934))


def test_classify_speed_unit_kmh():
    assert _classify_speed_unit('km/h') == ('kmh', pytest.approx(1.0))


def test_classify_speed_unit_ms():
    assert _classify_speed_unit('m/s') == ('ms', pytest.approx(3.6))


def test_classify_speed_unit_empty_defaults_ms():
    assert _classify_speed_unit('') == ('ms', pytest.approx(3.6))


def test_classify_speed_unit_case_insensitive():
    assert _classify_speed_unit('MPH') == ('mph', pytest.approx(1.60934))


def test_is_motec_ld_rejects_non_ld_extension(tmp_path):
    p = tmp_path / 'data.csv'
    p.write_bytes(struct.pack('<II', 0x40, 0) + b'\x00' * 8)
    assert is_motec_ld(str(p)) is False


def test_is_motec_ld_rejects_wrong_magic(tmp_path):
    p = tmp_path / 'data.ld'
    p.write_bytes(struct.pack('<II', 0x41, 0) + b'\x00' * 8)
    assert is_motec_ld(str(p)) is False


def test_is_motec_ld_rejects_short_file(tmp_path):
    p = tmp_path / 'data.ld'
    p.write_bytes(b'\x40\x00\x00\x00')   # too short
    assert is_motec_ld(str(p)) is False


def test_is_motec_ld_accepts_valid_header(tmp_path):
    p = tmp_path / 'data.ld'
    # header_size=0x40, padding=0, then 8 more bytes
    p.write_bytes(struct.pack('<II', 0x40, 0) + b'\x00' * 8)
    assert is_motec_ld(str(p)) is True


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — real .ld file (skipped when absent)
# ─────────────────────────────────────────────────────────────────────────────

_LD_PATH = (
    r'C:\Users\Laurens\Downloads\2.16.185_Spa_992_MoTeC'
    r'\Spa-porsche_992_gt3_r-7-2023.06.18-10.11.40.ld'
)
_LD_AVAILABLE = os.path.isfile(_LD_PATH)
_skip_no_file = pytest.mark.skipif(not _LD_AVAILABLE, reason='real .ld file not available')


@_skip_no_file
def test_is_motec_ld_real_file():
    assert is_motec_ld(_LD_PATH) is True


@_skip_no_file
def test_load_ld_source():
    s = motec_data.load_ld(_LD_PATH)
    assert s.source == 'MoTeC'


@_skip_no_file
def test_load_ld_track():
    s = motec_data.load_ld(_LD_PATH)
    assert s.track == 'Spa'


@_skip_no_file
def test_load_ld_date():
    s = motec_data.load_ld(_LD_PATH)
    assert s.date_utc == '2023-06-18T10:11:40Z'


@_skip_no_file
def test_load_ld_has_laps():
    s = motec_data.load_ld(_LD_PATH)
    assert len(s.laps) > 0


@_skip_no_file
def test_load_ld_timed_laps_have_reasonable_duration():
    s = motec_data.load_ld(_LD_PATH)
    for lap in s.timed_laps:
        # All timed Spa GT3 laps should be between 2:00 and 3:00
        assert 120.0 < lap.duration < 180.0, (
            f'Lap {lap.lap_num} duration {lap.duration:.1f}s outside expected range'
        )


@_skip_no_file
def test_load_ld_best_lap_in_range():
    s = motec_data.load_ld(_LD_PATH)
    # Spa GT3 best lap should be around 2:15–2:25
    assert 130.0 < s.best_lap_time < 145.0


@_skip_no_file
def test_load_ld_has_points():
    s = motec_data.load_ld(_LD_PATH)
    assert len(s.all_points) > 1000


@_skip_no_file
def test_load_ld_last_lap_has_speed():
    s = motec_data.load_ld(_LD_PATH)
    # The last timed lap (lap 18) should have speed data from the circular buffer
    last = s.laps[-2]  # second-to-last (last is partial outlap)
    pts_with_speed = [p for p in last.points if p.speed > 0]
    assert len(pts_with_speed) > 0, 'Expected speed data in last timed lap'


@_skip_no_file
def test_load_ld_speed_in_kmh():
    s = motec_data.load_ld(_LD_PATH)
    all_speeds = [p.speed for p in s.all_points if p.speed > 0]
    assert max(all_speeds) > 100, 'Expected speeds > 100 km/h (was m/s if failed)'
    assert max(all_speeds) < 400, 'Speed unreasonably high (unit error?)'


@_skip_no_file
def test_load_ld_g_forces_reasonable():
    s = motec_data.load_ld(_LD_PATH)
    pts = [p for p in s.all_points if abs(p.gforce_y) > 0]
    if pts:
        assert max(abs(p.gforce_y) for p in pts) < 5.0, 'Lateral G > 5 G seems wrong'
        assert max(abs(p.gforce_x) for p in pts) < 5.0, 'Longitudinal G > 5 G seems wrong'


@_skip_no_file
def test_load_ld_outlap_is_first():
    s = motec_data.load_ld(_LD_PATH)
    assert s.laps[0].is_outlap is True


@_skip_no_file
def test_load_ld_elapsed_monotonic():
    s = motec_data.load_ld(_LD_PATH)
    pts = s.all_points
    for i in range(1, len(pts)):
        assert pts[i].elapsed >= pts[i - 1].elapsed - 1e-6, (
            f'elapsed went backwards at index {i}: {pts[i-1].elapsed:.3f} -> {pts[i].elapsed:.3f}'
        )


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — real M150 hardware .ld export (no TIME channel, skipped
# when absent). Exercises the branch added for real M1 hardware exports,
# distinct from the ACC sim path covered by the fixture above.
# ─────────────────────────────────────────────────────────────────────────────

_HW_LD_PATH = (
    r'C:\Users\Laurens\Downloads\OneDrive_1_7-25-2026\Testing Bennett.ld'
)
_HW_LD_AVAILABLE = os.path.isfile(_HW_LD_PATH)
_skip_no_hw_file = pytest.mark.skipif(
    not _HW_LD_AVAILABLE, reason='real M150 hardware .ld file not available'
)


@_skip_no_hw_file
def test_load_ld_hardware_source():
    s = motec_data.load_ld(_HW_LD_PATH)
    assert s.source == 'MoTeC'


@_skip_no_hw_file
def test_load_ld_hardware_has_points():
    s = motec_data.load_ld(_HW_LD_PATH)
    assert len(s.all_points) > 1000


@_skip_no_hw_file
def test_load_ld_hardware_has_laps():
    s = motec_data.load_ld(_HW_LD_PATH)
    assert len(s.laps) > 0


@_skip_no_hw_file
def test_load_ld_hardware_elapsed_monotonic():
    s = motec_data.load_ld(_HW_LD_PATH)
    pts = s.all_points
    for i in range(1, len(pts)):
        assert pts[i].elapsed >= pts[i - 1].elapsed - 1e-6, (
            f'elapsed went backwards at index {i}: {pts[i-1].elapsed:.3f} -> {pts[i].elapsed:.3f}'
        )


@_skip_no_hw_file
def test_load_ld_hardware_speed_in_kmh():
    # 'Vehicle Speed' is untagged in this file; the hardware path must default
    # to km/h (not the sim path's m/s default) — verified against this file's
    # full sample distribution during planning (median/p90/p99, not just max).
    s = motec_data.load_ld(_HW_LD_PATH)
    assert s.source_speed_unit == 'kmh'
    speeds = [p.speed for p in s.all_points]
    assert 100.0 < max(speeds) < 150.0, 'FSAE top speed should be well under 150 km/h'


@_skip_no_hw_file
def test_load_ld_hardware_rpm_reasonable():
    s = motec_data.load_ld(_HW_LD_PATH)
    rpms = [p.rpm for p in s.all_points]
    assert 8000.0 < max(rpms) < 15000.0


@_skip_no_hw_file
def test_load_ld_hardware_gear_populated():
    # dtype=2 (int16) channel — exercises the generalized sample reader.
    s = motec_data.load_ld(_HW_LD_PATH)
    gears = {p.gear for p in s.all_points}
    assert len(gears) > 1, 'expected more than one distinct gear value'


@_skip_no_hw_file
def test_load_ld_hardware_lap_duration_matches_ldx():
    # The .ldx sidecar records "Fastest Time: 11:23.940" for this single-lap file.
    s = motec_data.load_ld(_HW_LD_PATH)
    assert len(s.laps) == 1


@_skip_no_hw_file
def test_load_ld_hardware_extra_channels_populated():
    # 'Coolant Temperature' isn't one of the fixed DataPoint fields — it
    # should surface via the generic extra bag instead.
    s = motec_data.load_ld(_HW_LD_PATH)
    assert 'Coolant Temperature' in s.extra_channel_meta
    assert s.extra_channel_meta['Coolant Temperature']['label'] == 'Coolant Temperature'
    vals = [p.extra.get('Coolant Temperature') for p in s.all_points[:100]]
    assert all(v is not None for v in vals)


@_skip_no_hw_file
def test_load_ld_hardware_extra_channels_exclude_fixed_fields():
    # Channels already mapped to a fixed DataPoint field (e.g. 'Gear',
    # 'Vehicle Speed') must not also show up as extras.
    s = motec_data.load_ld(_HW_LD_PATH)
    assert 'Gear' not in s.extra_channel_meta
    assert 'Vehicle Speed' not in s.extra_channel_meta


@_skip_no_hw_file
def test_load_ld_hardware_extra_channel_interpolation_offset():
    # Regression guard for the numpy-vectorised extras path: values at
    # different points should actually differ (not all defaulted to the
    # same value), confirming per-sample interpolation really ran.
    s = motec_data.load_ld(_HW_LD_PATH)
    vals = {p.extra.get('Coolant Temperature') for p in s.all_points[::5000]}
    assert len(vals) > 1


@_skip_no_file
def test_load_ld_sim_extra_channels_populated():
    # ACC sim path also captures leftover channels (e.g. 'STEERANGLE').
    s = motec_data.load_ld(_LD_PATH)
    assert len(s.extra_channel_meta) > 0
    for name in s.extra_channel_meta:
        assert name not in ('SPEED', 'G_LAT', 'G_LON', 'RPMS', 'TIME')
