from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from session_scanner import (
    VideoFile, VideoGroup,
    group_videos, _make_group,
    _read_csv_start_time, _csv_source,
    match_sessions, MatchedSession,
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
