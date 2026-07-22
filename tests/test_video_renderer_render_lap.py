"""
Tests for video_renderer.render_lap() itself — the actual frame-render loop,
not just its helper functions (see test_video_renderer.py for those).

render_lap is never called for real anywhere else in the test suite (every
export_runner test mocks it away), which is exactly how a real bug shipped
undetected earlier: export_runner started passing is_cancelled=... to
render_lap before render_lap's signature accepted it, and nothing caught the
resulting TypeError until it was checked by hand. These tests exercise the
real function — with cv2 and the per-frame renderer mocked out, since a video
file and a rendered frame's pixel content aren't what's under test here — to
guard the specific failure modes fixed alongside it: no cleanup on an
exception mid-render, no NaN-fps guard, and no way to cancel a render already
in progress.
"""
from unittest.mock import MagicMock, patch
import pytest


def _make_session_and_job(n_points=5, duration=10.0):
    """Minimal real Session/RenderJob/Lap — enough for render_lap to compute
    a valid frame range and iterate without raising on missing data."""
    from data_model import DataPoint, Lap, Session
    from datetime import datetime, timezone
    from video_renderer import RenderJob

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    pts = [
        DataPoint(
            record=i, time=now, lat=0.0, lon=0.0, alt=0.0, speed=100.0,
            gforce_x=0.0, gforce_y=0.0, gforce_z=1.0, lap=1,
            gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
            elapsed=i * (duration / n_points), lap_elapsed=i * (duration / n_points),
        )
        for i in range(n_points)
    ]
    lap = Lap(lap_num=1, points=pts, duration=duration)
    sess = Session(all_points=pts, laps=[lap], source='racebox',
                    date_utc=None, track='', configuration='', session_type='',
                    best_lap_time=None)
    job = RenderJob('Lap01', lap)
    return sess, job


def _fake_capture(n_frames=60, fps=30.0, w=64, h=48):
    """A cv2.VideoCapture stand-in with sane, valid metadata and n_frames of
    solid-color BGR frames available via .read()."""
    import numpy as np
    import cv2

    cap = MagicMock()
    cap.isOpened.return_value = True
    frame_count = {'i': 0}

    def _get(prop):
        return {
            cv2.CAP_PROP_FPS: fps,
            cv2.CAP_PROP_FRAME_COUNT: float(n_frames),
            cv2.CAP_PROP_FRAME_WIDTH: float(w),
            cv2.CAP_PROP_FRAME_HEIGHT: float(h),
        }.get(prop, 0.0)
    cap.get.side_effect = _get

    def _set(prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            frame_count['i'] = int(value)
        return True
    cap.set.side_effect = _set

    def _read():
        if frame_count['i'] >= n_frames:
            return False, None
        frame_count['i'] += 1
        return True, np.zeros((h, w, 3), dtype=np.uint8)
    cap.read.side_effect = _read

    return cap


@pytest.fixture(autouse=True)
def _fast_frame_worker():
    """Skip real matplotlib gauge rendering — irrelevant to what's under test
    here and would make these tests slow and layout-sensitive."""
    import numpy as np
    with patch('video_renderer.render_frame_worker',
               return_value=np.zeros((48, 64, 3), dtype=np.uint8).tobytes()):
        yield


class TestCancellationStopsRenderPromptly:
    def test_cancelled_before_first_chunk_returns_without_raising(self, tmp_path):
        from video_renderer import render_lap
        sess, job = _make_session_and_job()
        logs = []
        with patch('cv2.VideoCapture', return_value=_fake_capture()):
            render_lap(
                video_path='fake.mp4', out_path=str(tmp_path / 'out.mp4'),
                session=sess, job=job, sync_offset=0.0, encoder='libx264',
                crf=18, n_workers=1, show_map=False, show_telemetry=False,
                padding=0.0, log_cb=logs.append,
                is_cancelled=lambda: True,
            )
        assert any('cancel' in m.lower() for m in logs)
        # Must not have reached the mux step (no output file finalized).
        assert not (tmp_path / 'out.mp4').exists()

    def test_not_cancelled_processes_normally(self, tmp_path):
        """Sanity check: is_cancelled=lambda: False must not block a normal render."""
        from video_renderer import render_lap
        sess, job = _make_session_and_job()
        with patch('cv2.VideoCapture', return_value=_fake_capture()), \
             patch('video_renderer.mux_audio') as mock_mux:
            render_lap(
                video_path='fake.mp4', out_path=str(tmp_path / 'out.mp4'),
                session=sess, job=job, sync_offset=0.0, encoder='libx264',
                crf=18, n_workers=1, show_map=False, show_telemetry=False,
                padding=0.0, is_cancelled=lambda: False,
            )
        mock_mux.assert_called_once()


class TestNaNFpsDoesNotCrash:
    def test_nan_fps_falls_back_instead_of_raising_valueerror(self, tmp_path):
        """A corrupt video reporting NaN fps must hit the existing friendly
        LapOutOfRangeError path, not an uncaught ValueError from int(nan)."""
        from video_renderer import render_lap
        from exceptions import LapOutOfRangeError
        sess, job = _make_session_and_job()

        cap = MagicMock()
        cap.isOpened.return_value = False
        cap.get.side_effect = lambda prop: float('nan')

        with patch('cv2.VideoCapture', return_value=cap):
            with pytest.raises(LapOutOfRangeError):
                render_lap(
                    video_path='corrupt.mp4', out_path=str(tmp_path / 'out.mp4'),
                    session=sess, job=job, sync_offset=0.0, encoder='libx264',
                    crf=18, n_workers=1, show_map=False, show_telemetry=False,
                    padding=0.0,
                )


class TestCleanupOnException:
    def test_exception_mid_render_still_releases_capture(self, tmp_path):
        from video_renderer import render_lap
        sess, job = _make_session_and_job(n_points=5, duration=10.0)
        cap = _fake_capture()

        with patch('cv2.VideoCapture', return_value=cap), \
             patch('video_renderer.render_frame_worker', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError):
                render_lap(
                    video_path='fake.mp4', out_path=str(tmp_path / 'out.mp4'),
                    session=sess, job=job, sync_offset=0.0, encoder='libx264',
                    crf=18, n_workers=1, show_map=False, show_telemetry=False,
                    padding=0.0,
                )
        cap.release.assert_called_once()
