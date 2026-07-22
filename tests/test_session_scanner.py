from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from session_scanner import (
    VideoFile, VideoGroup,
    group_videos, _make_group,
    _read_csv_start_time, _csv_source, _sniff_candidate,
    match_sessions, MatchedSession,
    solve_camera_offset,
    MAX_GAP, MATCH_WINDOW,
)


def _utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _make_video(path: str, creation_time: datetime, duration: float) -> VideoFile:
    return VideoFile(path=path, creation_time=creation_time, duration=duration)


# ── group_videos ───────────────────────────────────────────────────────────────

def test_group_videos_empty():
    assert group_videos([]) == []


def test_group_videos_single():
    v = _make_video('/a.mp4', _utc(2024, 1, 1), 60.0)
    groups = group_videos([v])
    assert len(groups) == 1
    assert groups[0].total_dur == pytest.approx(60.0)


def test_group_videos_consecutive_grouped():
    t0 = _utc(2024, 1, 1)
    v1 = _make_video('/a.mp4', t0, 60.0)
    # Second video starts 10 seconds after first ends — within MAX_GAP
    v2 = _make_video('/b.mp4', t0 + timedelta(seconds=70), 60.0)
    groups = group_videos([v1, v2])
    assert len(groups) == 1
    assert groups[0].total_dur == pytest.approx(120.0)


def test_group_videos_gap_splits():
    t0 = _utc(2024, 1, 1)
    v1 = _make_video('/a.mp4', t0, 60.0)
    # Gap of 300s — well beyond MAX_GAP (120s)
    v2 = _make_video('/b.mp4', t0 + timedelta(seconds=360), 60.0)
    groups = group_videos([v1, v2])
    assert len(groups) == 2


def test_group_videos_total_duration():
    t0 = _utc(2024, 1, 1)
    v1 = _make_video('/a.mp4', t0, 30.0)
    v2 = _make_video('/b.mp4', t0 + timedelta(seconds=35), 45.0)
    groups = group_videos([v1, v2])
    assert groups[0].total_dur == pytest.approx(75.0)


def test_group_videos_start_time():
    t0 = _utc(2024, 1, 1, 10, 0, 0)
    v1 = _make_video('/a.mp4', t0, 60.0)
    v2 = _make_video('/b.mp4', t0 + timedelta(seconds=65), 60.0)
    groups = group_videos([v1, v2])
    assert groups[0].start_time == t0


# ── _read_csv_start_time ───────────────────────────────────────────────────────

def test_read_csv_start_time_racebox(racebox_car_csv_path):
    dt = _read_csv_start_time(racebox_car_csv_path)
    assert dt is not None
    assert dt.tzinfo is not None  # must be timezone-aware
    assert dt.year == 2024
    assert dt.month == 6
    assert dt.day == 15


def test_read_csv_start_time_aim(aim_csv_path):
    dt = _read_csv_start_time(aim_csv_path)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2024


# ── _csv_source ────────────────────────────────────────────────────────────────

def test_csv_source_aim(aim_csv_path):
    assert _csv_source(aim_csv_path) == 'AIM Mychron'


def test_csv_source_racebox(racebox_car_csv_path):
    assert _csv_source(racebox_car_csv_path) == 'RaceBox'


def test_csv_source_unipro_tsv(tmp_path):
    p = tmp_path / 'session.tsv'
    p.write_text('dummy', encoding='utf-8')
    assert _csv_source(str(p)) == 'Unipro'


def test_csv_source_unipro_uni(tmp_path):
    p = tmp_path / 'session.uni'
    p.write_text('dummy', encoding='utf-8')
    assert _csv_source(str(p)) == 'Unipro'


# ── Unipro .tsv scan integration ───────────────────────────────────────────────

class TestUniproTsvScanning:
    _HEADER = ('"Start Date"\t"Start Time"\t"Lap Number"\t"Session Time"\t'
               '"Latitude"\t"Longitude"\n')

    def test_sniff_candidate_accepts_real_header(self, tmp_path):
        p = tmp_path / 'session.tsv'
        p.write_text(self._HEADER + '2026-07-20\t12:40:56\t0\t0\t50.09\t4.50\n',
                     encoding='utf-8')
        assert _sniff_candidate(str(p), '.tsv') is True

    def test_sniff_candidate_rejects_unrelated_tsv(self, tmp_path):
        p = tmp_path / 'not_unipro.tsv'
        p.write_text('"Foo"\t"Bar"\n1\t2\n', encoding='utf-8')
        assert _sniff_candidate(str(p), '.tsv') is False

    def test_read_csv_start_time_from_filename_pattern(self, tmp_path):
        """.tsv start time comes from the YYMMDD_HHMM filename stamp — the
        file itself isn't cheap to fully parse (see unipro_data.load_tsv's
        stray-block docstring), so this must not require reading the body."""
        p = tmp_path / '260720_1240_Marienbourg GPS_Lowie.tsv'
        p.write_text(self._HEADER, encoding='utf-8')  # header only, no data rows
        dt = _read_csv_start_time(str(p))
        assert dt is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 7, 20, 12, 40)

    def test_read_csv_start_time_falls_back_to_mtime_without_filename_pattern(self, tmp_path):
        p = tmp_path / 'not_a_recognised_pattern.tsv'
        p.write_text(self._HEADER, encoding='utf-8')
        dt = _read_csv_start_time(str(p))
        assert dt is not None
        assert dt.tzinfo is not None


# ── match_sessions ─────────────────────────────────────────────────────────────

def test_match_sessions_within_window(racebox_car_csv_path):
    csv_start = _read_csv_start_time(racebox_car_csv_path)
    # Video starts 30 seconds after CSV — within MATCH_WINDOW
    video_time = csv_start + timedelta(seconds=30)
    v = _make_video('/video.mp4', video_time, 600.0)
    group = _make_group([v])

    results = match_sessions([racebox_car_csv_path], [group])
    assert len(results) == 1
    assert results[0].matched is True
    assert results[0].time_delta == pytest.approx(30.0, abs=1.0)


def test_match_sessions_outside_window(racebox_car_csv_path):
    csv_start = _read_csv_start_time(racebox_car_csv_path)
    # Video is 4000 seconds away — beyond MATCH_WINDOW (3600s)
    video_time = csv_start + timedelta(seconds=4000)
    v = _make_video('/video.mp4', video_time, 600.0)
    group = _make_group([v])

    results = match_sessions([racebox_car_csv_path], [group])
    assert results[0].matched is False


def test_match_sessions_no_videos(racebox_car_csv_path):
    results = match_sessions([racebox_car_csv_path], [])
    assert len(results) == 1
    assert results[0].matched is False
    assert results[0].video_group is None


def test_match_sessions_sorts_by_csv_start(racebox_car_csv_path, racebox_bike_csv_path):
    # Bike CSV starts at 11:00, car CSV at 10:00 — result should be car first
    results = match_sessions([racebox_bike_csv_path, racebox_car_csv_path], [])
    starts = [r.csv_start for r in results if r.csv_start]
    assert starts == sorted(starts)


# ── solve_camera_offset ───────────────────────────────────────────────────────

def test_solve_camera_offset_empty_inputs():
    assert solve_camera_offset([], []) == (0.0, 0)
    v = _make_group([_make_video('/a.mp4', _utc(2026, 1, 1), 60.0)])
    assert solve_camera_offset([v], []) == (0.0, 0)
    assert solve_camera_offset([], [_utc(2026, 1, 1)]) == (0.0, 0)


def test_solve_camera_offset_recovers_known_offset():
    true_starts = [_utc(2026, 6, 24, 9, 0, 0),
                   _utc(2026, 6, 24, 10, 30, 0),
                   _utc(2026, 6, 24, 12, 0, 0)]
    applied_offset = 7100.0  # camera clock reports times ~2h behind reality
    groups = [
        _make_group([_make_video(f'/v{i}.mp4', st - timedelta(seconds=applied_offset), 600.0)])
        for i, st in enumerate(true_starts)
    ]
    offset, count = solve_camera_offset(groups, true_starts)
    assert count == 3
    assert offset == pytest.approx(applied_offset, abs=0.01)


def test_solve_camera_offset_recovers_multiday_offset():
    # Camera date, not just time, was wrong — offset spans several days.
    true_starts = [_utc(2026, 6, 24, 9, 0, 0), _utc(2026, 6, 24, 14, 0, 0)]
    applied_offset = 3 * 86400 + 3661.0
    groups = [
        _make_group([_make_video(f'/v{i}.mp4', st - timedelta(seconds=applied_offset), 300.0)])
        for i, st in enumerate(true_starts)
    ]
    offset, count = solve_camera_offset(groups, true_starts)
    assert count == 2
    assert offset == pytest.approx(applied_offset, abs=0.01)


def test_solve_camera_offset_ignores_decoys():
    true_starts = [_utc(2026, 6, 24, 9, 0, 0),
                   _utc(2026, 6, 24, 11, 15, 0),
                   _utc(2026, 6, 24, 15, 40, 0)]
    applied_offset = -5000.0
    groups = [
        _make_group([_make_video(f'/v{i}.mp4', st - timedelta(seconds=applied_offset), 400.0)])
        for i, st in enumerate(true_starts)
    ]
    # Decoy video group with a totally unrelated raw timestamp.
    groups.append(_make_group([_make_video('/decoy.mp4', _utc(2019, 1, 1, 3, 0, 0), 120.0)]))
    # Decoy session with no corresponding video at all.
    session_times = true_starts + [_utc(2026, 6, 24, 20, 0, 0)]

    offset, count = solve_camera_offset(groups, session_times)
    assert count == 3
    assert offset == pytest.approx(applied_offset, abs=0.01)


def test_solve_camera_offset_partial_match_picks_best_alignment():
    # Mirrors the real scenario: a folder of clips where only some correspond
    # to a telemetry session for that day (e.g. paddock/warm-up footage mixed
    # in) — the solver should still land on the offset that matches the most.
    true_starts = [_utc(2026, 6, 24, 9, 0, 0),
                   _utc(2026, 6, 24, 10, 20, 0),
                   _utc(2026, 6, 24, 13, 5, 0),
                   _utc(2026, 6, 24, 16, 45, 0)]
    applied_offset = 12345.0
    matching_groups = [
        _make_group([_make_video(f'/m{i}.mp4', st - timedelta(seconds=applied_offset), 500.0)])
        for i, st in enumerate(true_starts)
    ]
    # Extra clips from the same camera/day with the same clock error, but far
    # from any session time even once the correct offset is applied.
    extra_groups = [
        _make_group([_make_video('/extra1.mp4',
                     true_starts[0] - timedelta(seconds=applied_offset) - timedelta(minutes=90), 60.0)]),
        _make_group([_make_video('/extra2.mp4',
                     true_starts[-1] - timedelta(seconds=applied_offset) + timedelta(minutes=90), 60.0)]),
    ]
    offset, count = solve_camera_offset(matching_groups + extra_groups, true_starts)
    assert count == 4
    assert offset == pytest.approx(applied_offset, abs=0.01)
