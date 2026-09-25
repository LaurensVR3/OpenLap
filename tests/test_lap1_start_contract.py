"""
Lap 1 start contract — the one formula every part of OpenLap must agree on.

    video_time(lap start) = sync_offset + lap.elapsed_start
    lap.elapsed_start     = elapsed of the lap's first telemetry point
    sync_offset           = video time at telemetry elapsed 0
                            (auto_sync: session_time = video_time - sync_offset)

The Data page "Mark Lap 1 start" button, the Overlay editor's lap seek, the
export frame window in video_renderer.render_lap and auto_sync's correlation
all encode this. It has been broken and re-fixed several times, always by one
side drifting (an outlap treated as 0, a lap index vs lap number mix-up, or a
video timeline that no longer matches the exported one). These tests pin each
side to the same numbers so a change to any of them fails here first.
The JS side of the same contract lives in frontend/tests/data_sync.test.js.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── Loader side: elapsed_start of the first timed lap ─────────────────────────

def test_racebox_first_timed_lap_starts_after_the_outlap(racebox_car_session):
    laps = racebox_car_session.laps
    assert laps[0].lap_num == 0 and laps[0].is_outlap
    first_timed = next(l for l in laps if not l.is_outlap)
    assert first_timed.lap_num == 1
    # elapsed_start is the lap's own first point, on the session clock that
    # starts at 0 with the first telemetry row — never re-based per lap.
    assert first_timed.elapsed_start == first_timed.points[0].elapsed
    assert racebox_car_session.all_points[0].elapsed == 0.0
    assert first_timed.elapsed_start > 0.0
    # …and it is exactly where the RaceBox lap counter first reads 1. (Lap 0
    # also holds the in-lap rows after the last timed lap, so lap 0's *last*
    # point is not the lap 1 boundary — only its first-lap-1 row is.)
    first_lap1_row = next(p for p in racebox_car_session.all_points if p.lap == 1)
    assert first_timed.elapsed_start == first_lap1_row.elapsed


def test_get_laps_exposes_the_same_elapsed_start_to_the_frontend(
        tmp_config_dir, racebox_car_csv_path, racebox_car_session):
    """The Data page computes outlapDur from get_laps()[..].elapsed_start of
    the first non-outlap entry; that must be the loader's value, not 0."""
    from webview_api import WebviewAPI
    api = WebviewAPI()
    rows = api.get_laps(racebox_car_csv_path)
    assert rows, 'get_laps returned nothing for the RaceBox fixture'
    first_timed_row = next(r for r in rows if not r['is_outlap'])
    first_timed_lap = next(l for l in racebox_car_session.laps if not l.is_outlap)
    assert first_timed_row['elapsed_start'] == pytest.approx(first_timed_lap.elapsed_start, abs=0.001)
    assert first_timed_row['lap_num'] == 1
    # lap_idx indexes session.laps (outlap included) — the editor/export use
    # it to pick the lap, so it must not be confused with lap_num.
    assert first_timed_row['lap_idx'] == racebox_car_session.laps.index(first_timed_lap)


# ── Export side: render_lap's frame window ────────────────────────────────────

def _session_with_outlap(outlap_s=20.0, lap_s=10.0, hz=10):
    from data_model import DataPoint, Lap, Session
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def _pt(i, elapsed, lap, lap_elapsed):
        return DataPoint(record=i, time=now, lat=0.0, lon=0.0, alt=0.0, speed=100.0,
                         gforce_x=0.0, gforce_y=0.0, gforce_z=1.0, lap=lap,
                         gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
                         elapsed=elapsed, lap_elapsed=lap_elapsed)

    out_pts = [_pt(i, i / hz, 0, i / hz) for i in range(int(outlap_s * hz))]
    lap_pts = [_pt(len(out_pts) + i, outlap_s + i / hz, 1, i / hz)
               for i in range(int(lap_s * hz) + 1)]
    outlap = Lap(lap_num=0, points=out_pts, duration=outlap_s, is_outlap=True)
    lap1   = Lap(lap_num=1, points=lap_pts, duration=lap_s)
    sess = Session(all_points=out_pts + lap_pts, laps=[outlap, lap1], source='racebox',
                   date_utc=None, track='', configuration='', session_type='',
                   best_lap_time=None)
    return sess, lap1


def test_frame_window_starts_at_sync_offset_plus_lap_start():
    from video_renderer import RenderJob, frame_window
    sess, lap1 = _session_with_outlap(outlap_s=20.0, lap_s=10.0)
    job = RenderJob('Lap01', lap1)
    assert job.gpx_start == lap1.elapsed_start == pytest.approx(20.0)
    # Lap 1 starts at telemetry 20.0 s; with the video 2.5 s ahead of the
    # telemetry it is at video 22.5 s → frame 675 at 30 fps.
    f_start, _ = frame_window(job, sync_offset=2.5, padding=0.0, fps=30.0, total_frames=3600)
    assert f_start == int((2.5 + lap1.elapsed_start) * 30.0) == 675


def test_render_lap_seeks_ffmpeg_to_sync_offset_plus_lap_start(tmp_path):
    """The export reads the video from exactly that frame: FFmpeg's input
    seek is the frame window's start."""
    from fractions import Fraction
    from video_renderer import RenderJob, VideoInfo, render_lap
    sess, lap1 = _session_with_outlap(outlap_s=20.0, lap_s=10.0)
    cmds = []

    class _Proc:
        def __init__(self, cmd):
            cmds.append(cmd)
            self.stdin = MagicMock()
            self.stderr = iter([])
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            open(cmds[-1][-1], 'wb').close()
            return 0

        def kill(self):
            self.returncode = -9

    layout = {'gauges': [{'type': 'Numeric', 'channel': 'speed', 'visible': True,
                          'x': 0, 'y': 0, 'w': 0.5, 'h': 0.5}]}
    with patch('video_renderer.probe_video',
               return_value=VideoInfo(64, 48, Fraction(30), 120.0)), \
         patch('video_renderer._popen', side_effect=lambda cmd, **_: _Proc(cmd)), \
         patch('video_renderer.render_frame_worker', return_value=b'\0'):
        render_lap(video_path='fake.mp4', out_path=str(tmp_path / 'out.mp4'),
                   session=sess, job=RenderJob('Lap01', lap1), sync_offset=2.5,
                   encoder='libx264', crf=18, n_workers=1, show_map=False,
                   show_telemetry=True, padding=0.0, overlay_layout=layout,
                   log_cb=lambda _m: None)
    cmd = cmds[0]
    assert float(cmd[cmd.index('-ss') + 1]) == pytest.approx(675 / 30.0) == pytest.approx(22.5)


# ── Auto-sync side: offset sign convention ────────────────────────────────────

def test_auto_sync_offset_is_video_time_of_telemetry_zero():
    """auto_sync's result must be usable directly as sync_offset: the video is
    `offset` seconds ahead of the telemetry, so lap 1 (telemetry t=20) sits at
    video t = offset + 20 — same formula the Mark button and render_lap use."""
    from auto_sync import _correlate
    fps = 5.0
    n = int(120 * fps)
    t = np.arange(n) / fps
    rng = np.random.default_rng(1)
    tel = np.zeros(n)
    for c in (20.0, 35.0, 60.0, 85.0):
        tel += np.exp(-0.5 * ((t - c) / 1.0) ** 2)
    tel += rng.normal(0, 0.01, n)
    true_offset = 7.0                    # camera started 7 s before the logger
    shift = int(round(true_offset * fps))
    vid = np.zeros(n)
    vid[shift:] = tel[:n - shift]        # same events, 7 s later in video time
    vid += rng.normal(0, 0.01, n)

    offset, conf = _correlate(vid, tel, fps=fps, search_window_s=30.0)
    assert offset == pytest.approx(true_offset, abs=0.3)
    assert conf > 1.0
    lap1_video_time = offset + 20.0
    assert lap1_video_time == pytest.approx(27.0, abs=0.3)
