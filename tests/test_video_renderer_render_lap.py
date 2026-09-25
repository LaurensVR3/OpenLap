"""
Tests for video_renderer.render_lap() itself: the frame loop feeding FFmpeg,
its failure handling, and — with a real FFmpeg, when one is installed — the
exported file itself.

render_lap is mocked away in every export_runner test, which is how a real
bug once shipped: export_runner passed is_cancelled=... before render_lap
accepted it. The mocked tests here run the real function with FFmpeg and the
gauge drawing replaced; the end-to-end tests render real gauges over a real
(generated) clip and inspect the output with ffprobe.
"""
import os
import shutil
import subprocess
from fractions import Fraction
from unittest.mock import patch

import pytest


def _make_session_and_job(n_points=21, duration=10.0, lap_offset=0.0):
    """Minimal real Session/RenderJob/Lap."""
    from data_model import DataPoint, Lap, Session
    from datetime import datetime, timezone
    from video_renderer import RenderJob

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = duration / (n_points - 1)
    pts = [
        DataPoint(
            record=i, time=now, lat=51.0 + i * 1e-5, lon=4.0 + i * 1e-5, alt=0.0,
            speed=60.0 + i, gforce_x=0.1, gforce_y=-0.2, gforce_z=1.0, lap=1,
            gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
            elapsed=lap_offset + i * step, lap_elapsed=i * step,
        )
        for i in range(n_points)
    ]
    lap = Lap(lap_num=1, points=pts, duration=duration)
    sess = Session(all_points=pts, laps=[lap], source='racebox',
                   date_utc=None, track='', configuration='', session_type='',
                   best_lap_time=None)
    return sess, RenderJob('Lap01', lap)


_LAYOUT = {'theme': 'Dark', 'gauges': [
    {'type': 'Numeric', 'channel': 'speed', 'visible': True, 'x': 0.05, 'y': 0.05, 'w': 0.3, 'h': 0.3},
    {'type': 'Bar', 'channel': 'gforce_lat', 'visible': True, 'x': 0.6, 'y': 0.6, 'w': 0.3, 'h': 0.3},
]}


# ── Mocked FFmpeg ─────────────────────────────────────────────────────────────

class _FakeStdin:
    def __init__(self, fail_after=None, exc=BrokenPipeError):
        self.frames, self.fail_after, self.exc, self.closed = 0, fail_after, exc, False

    def write(self, data):
        if self.fail_after is not None and self.frames >= self.fail_after:
            raise self.exc()
        self.frames += 1

    def close(self):
        self.closed = True


class _FakeProc:
    """Stands in for the FFmpeg process: records the command, accepts frames
    on stdin, and creates the output file on a clean exit like FFmpeg would."""

    def __init__(self, cmd, returncode=0, stderr=b'', fail_after=None):
        self.cmd, self._rc, self.stdin = cmd, returncode, _FakeStdin(fail_after)
        self.stderr = iter([stderr] if stderr else [])
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -9 if self.killed else self._rc
            if self.returncode == 0:
                open(self.cmd[-1], 'wb').close()   # FFmpeg writes the output last
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _render(tmp_path, *, proc_kwargs=None, info=None, **kw):
    """Run render_lap with FFmpeg and gauge drawing mocked. Returns
    (the fake FFmpeg process, out_path)."""
    import numpy as np
    from video_renderer import VideoInfo, render_lap
    sess, job = kw.pop('session_job', None) or _make_session_and_job()
    info = info or VideoInfo(width=64, height=48, fps=Fraction(30), duration=20.0, has_audio=True)
    procs = []

    def fake_popen(cmd, **_):
        procs.append(_FakeProc(cmd, **(proc_kwargs or {})))
        return procs[-1]

    out = str(tmp_path / 'out.mp4')
    args = dict(video_path='clip.mp4', out_path=out, session=sess, job=job,
                sync_offset=0.0, encoder='libx264', crf=18, n_workers=1,
                show_map=False, show_telemetry=True, padding=0.0,
                overlay_layout=_LAYOUT, log_cb=lambda _m: None)
    args.update(kw)
    with patch('video_renderer.probe_video', return_value=info), \
         patch('video_renderer._popen', side_effect=fake_popen), \
         patch('video_renderer.render_frame_worker',
               side_effect=lambda task: np.zeros(1, dtype=np.uint8).tobytes()):
        render_lap(**args)
    return procs[0] if procs else None, out


class TestOutputOnlyOnSuccess:
    def test_success_renames_part_file_into_place(self, tmp_path):
        proc, out = _render(tmp_path)
        assert os.path.exists(out)
        assert proc.cmd[-1].endswith('.part.mp4')
        assert proc.stdin.frames == 300 and proc.stdin.closed   # 10 s at 30 fps

    def test_ffmpeg_failure_raises_with_its_message_and_leaves_no_file(self, tmp_path):
        from exceptions import VideoMuxError
        with pytest.raises(VideoMuxError, match='Unknown encoder'):
            _render(tmp_path, proc_kwargs={'returncode': 1, 'stderr': b'Unknown encoder foo\n'})
        assert os.listdir(tmp_path) == []

    def test_silent_ffmpeg_failure_still_gives_advice(self, tmp_path):
        from exceptions import VideoMuxError
        with pytest.raises(VideoMuxError, match='Detect Encoders'):
            _render(tmp_path, proc_kwargs={'returncode': 1})

    def test_source_ending_early_is_a_note_not_a_failure(self, tmp_path):
        logs = []
        proc, out = _render(tmp_path, proc_kwargs={'fail_after': 120}, log_cb=logs.append)
        assert os.path.exists(out)
        assert any('source video ended early' in m for m in logs)


class TestCancellation:
    def test_cancel_before_first_chunk_kills_ffmpeg_and_writes_nothing(self, tmp_path):
        proc, out = _render(tmp_path, is_cancelled=lambda: True)
        assert proc.killed
        assert os.listdir(tmp_path) == []

    def test_not_cancelled_processes_normally(self, tmp_path):
        proc, out = _render(tmp_path, is_cancelled=lambda: False)
        assert os.path.exists(out) and not proc.killed


class TestExceptionCleanup:
    def test_worker_error_propagates_and_cleans_up(self, tmp_path):
        from video_renderer import VideoInfo, render_lap
        sess, job = _make_session_and_job()
        procs = []
        with patch('video_renderer.probe_video',
                   return_value=VideoInfo(64, 48, Fraction(30), 20.0)), \
             patch('video_renderer._popen',
                   side_effect=lambda cmd, **_: procs.append(_FakeProc(cmd)) or procs[-1]), \
             patch('video_renderer.render_frame_worker', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError, match='boom'):
                render_lap('clip.mp4', str(tmp_path / 'out.mp4'), sess, job, 0.0,
                           'libx264', 18, 1, False, True, padding=0.0,
                           overlay_layout=_LAYOUT)
        assert procs[0].killed
        assert os.listdir(tmp_path) == []


class TestEncoderFallback:
    def test_unusable_hardware_encoder_falls_back_before_rendering(self, tmp_path):
        logs = []
        with patch('video_renderer.encoder_works', return_value=False):
            proc, _ = _render(tmp_path, encoder='h264_nvenc', log_cb=logs.append)
        assert proc.cmd[proc.cmd.index('-c:v') + 1] == 'libx264'
        assert any('libx264' in m for m in logs)


class TestMissingExportFolder:
    def test_missing_folder_is_an_error_not_a_silent_success(self, tmp_path):
        from exceptions import VideoMuxError
        with pytest.raises(VideoMuxError, match='does not exist'):
            _render(tmp_path, out_path=str(tmp_path / 'gone' / 'out.mp4'))


class TestFrameWindowAndSeek:
    def test_single_clip_seeks_to_lap_start(self, tmp_path):
        from video_renderer import VideoInfo
        sess, job = _make_session_and_job(lap_offset=20.0)
        proc, _ = _render(tmp_path, session_job=(sess, job), sync_offset=2.5,
                          info=VideoInfo(64, 48, Fraction(30), 60.0, has_audio=True))
        assert proc.cmd[proc.cmd.index('-ss') + 1] == '22.500000'

    def test_multi_clip_opens_only_the_clips_the_window_covers(self, tmp_path):
        from video_renderer import VideoInfo
        sess, job = _make_session_and_job(lap_offset=25.0, duration=10.0)
        infos = {'a.mp4': VideoInfo(64, 48, Fraction(30), 20.0),
                 'b.mp4': VideoInfo(64, 48, Fraction(30), 20.0),
                 'c.mp4': VideoInfo(64, 48, Fraction(30), 20.0)}
        with patch('video_renderer.probe_video', side_effect=lambda p: infos[p]):
            import numpy as np
            from video_renderer import render_lap
            procs = []
            with patch('video_renderer._popen',
                       side_effect=lambda cmd, **_: procs.append(_FakeProc(cmd)) or procs[-1]), \
                 patch('video_renderer.render_frame_worker', side_effect=lambda t: b'\0'):
                render_lap('a.mp4', str(tmp_path / 'o.mp4'), sess, job, 0.0, 'libx264', 18, 1,
                           False, True, padding=0.0, overlay_layout=_LAYOUT,
                           video_paths=['a.mp4', 'b.mp4', 'c.mp4'])
        cmd = procs[0].cmd
        inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-i']
        # The lap is 25-35 s into the recording: only clip b, entered 5 s in.
        assert inputs[0] == 'b.mp4' and 'a.mp4' not in inputs and 'c.mp4' not in inputs
        assert cmd[cmd.index('-ss') + 1] == '5.000000'


class TestClipSources:
    def test_window_spanning_a_boundary_uses_both_clips(self):
        from video_renderer import clip_sources
        assert clip_sources(['a', 'b', 'c'], [10.0, 10.0, 10.0], 8.0, 14.0) == [('a', 8.0), ('b', 0.0)]

    def test_window_inside_one_clip(self):
        from video_renderer import clip_sources
        assert clip_sources(['a', 'b'], [10.0, 10.0], 12.0, 15.0) == [('b', 2.0)]


class TestAtlasLayout:
    def test_tiles_do_not_overlap_in_the_atlas_and_keep_layout_order(self):
        from overlay_worker import atlas_layout
        layout = {'gauges': [dict(g, visible=True) for g in (
            {'type': 'Numeric', 'x': 0.0, 'y': 0.0, 'w': 0.2, 'h': 0.2},
            {'type': 'Bar', 'x': 0.7, 'y': 0.7, 'w': 0.3, 'h': 0.1},
            {'type': 'Dial', 'x': 0.4, 'y': 0.1, 'w': 0.15, 'h': 0.4},
        )]}
        tiles, (aw, ah) = atlas_layout(layout, 1920, 1080)
        assert [t['idx'] for t in tiles] == [0, 1, 2]
        for t in tiles:
            assert t['ax'] + t['w'] <= aw and t['ay'] + t['h'] <= ah
            assert t['w'] % 2 == t['h'] % 2 == t['ax'] % 2 == t['ay'] % 2 == 0
        for i, a in enumerate(tiles):
            for b in tiles[i + 1:]:
                assert (a['ax'] + a['w'] <= b['ax'] or b['ax'] + b['w'] <= a['ax'] or
                        a['ay'] + a['h'] <= b['ay'] or b['ay'] + b['h'] <= a['ay'])
        assert aw * ah < 1920 * 1080 / 2   # the point of it

    def test_nothing_visible_means_no_overlay(self):
        from overlay_worker import atlas_layout
        assert atlas_layout({'gauges': [{'type': 'Dial', 'visible': False}]}, 640, 480) == ([], None)


# ── Real FFmpeg, end to end ───────────────────────────────────────────────────

_FFMPEG = shutil.which('ffmpeg') and shutil.which('ffprobe')
needs_ffmpeg = pytest.mark.skipif(not _FFMPEG, reason='FFmpeg not installed')


def _make_clip(path, seconds, color='blue'):
    """A clip with PCM audio: AAC pads each clip ~21 ms past its video, and
    FFmpeg's concat starts the next clip after the longer stream, which shifts
    frames after a join by up to one frame slot — real chaptered camera files
    do not have that gap, and this test is about the join itself."""
    subprocess.run(['ffmpeg', '-v', 'error', '-y',
                    '-f', 'lavfi', '-i', f'color={color}:s=320x240:r=30:d={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:d={seconds}',
                    '-c:v', 'libx264', '-g', '30', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le',
                    '-shortest', str(path)], check=True)


def _probe(path):
    import json
    r = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-print_format', 'json',
                        '-show_streams', str(path)], capture_output=True, text=True, check=True)
    return {s['codec_type']: s for s in json.loads(r.stdout)['streams']}


@needs_ffmpeg
class TestRealExport:
    def _render_real(self, tmp_path, clips, out_name='out.mp4', **kw):
        from video_renderer import render_lap
        sess, job = _make_session_and_job(lap_offset=kw.pop('lap_offset', 1.0), duration=2.0)
        out = str(tmp_path / out_name)
        render_lap(clips[0], out, sess, job, sync_offset=0.0, encoder='libx264', crf=30,
                   n_workers=1, show_map=False, show_telemetry=True, padding=0.0,
                   overlay_layout=_LAYOUT, video_paths=clips, **kw)
        return out

    def test_lap_export_has_the_lap_s_frames_audio_and_gauges(self, tmp_path):
        clip = tmp_path / 'clip.mov'
        _make_clip(clip, 5)
        out = self._render_real(tmp_path, [str(clip)])
        streams = _probe(out)
        assert int(streams['video']['nb_read_frames']) == 60   # 2 s at 30 fps
        assert 'audio' in streams
        # A gauge was drawn over the plain blue source: the top-left corner is
        # no longer the source colour.
        import numpy as np
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', out, '-frames:v', '1',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                             capture_output=True, check=True).stdout
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3)
        assert not np.allclose(frame[40, 40], frame[120, 160], atol=10)

    def test_lap_across_a_clip_boundary(self, tmp_path):
        a, b = tmp_path / 'a.mov', tmp_path / 'b.mov'
        _make_clip(a, 2, 'blue')
        _make_clip(b, 2, 'red')
        out = self._render_real(tmp_path, [str(a), str(b)], lap_offset=1.0)
        assert int(_probe(out)['video']['nb_read_frames']) == 60
        import numpy as np

        def pixel(t):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out,
                                  '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                 capture_output=True, check=True).stdout
            return np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3)[120, 160]
        assert pixel(0.5)[2] > 150        # still clip a (blue) half a second in
        assert pixel(1.5)[0] > 150        # clip b (red) after the boundary at 1.0 s

    def test_overlay_only_is_transparent_prores(self, tmp_path):
        clip = tmp_path / 'clip.mov'
        _make_clip(clip, 5)
        out = self._render_real(tmp_path, [str(clip)], out_name='ov.mov', overlay_only=True)
        v = _probe(out)['video']
        assert v['codec_name'] == 'prores' and 'a' in v['pix_fmt']
        assert int(v['nb_read_frames']) == 60


@needs_ffmpeg
class TestSecondVideo:
    """A Video gauge's box shows the second recording from the moment it
    covers, in step with the main video (layer time = main time + offset)."""

    def test_second_video_appears_in_its_box_at_its_time(self, tmp_path):
        import numpy as np
        from video_renderer import render_lap
        main, cam2 = tmp_path / 'main.mov', tmp_path / 'cam2.mov'
        _make_clip(main, 5, 'blue')
        _make_clip(cam2, 5, 'red')
        sess, job = _make_session_and_job(lap_offset=1.0, duration=2.0)
        layout = {'theme': 'Dark', 'gauges': [
            {'type': 'Video', 'video_source': 'camera', 'visible': True,
             'x': 0.5, 'y': 0.5, 'w': 0.5, 'h': 0.5},
        ]}
        out = str(tmp_path / 'out.mp4')
        # the second camera started 2 s into the main video: its time = main - 2
        render_lap(str(main), out, sess, job, sync_offset=0.0, encoder='libx264', crf=30,
                   n_workers=1, show_map=False, show_telemetry=True, padding=0.0,
                   overlay_layout=layout, video_paths=[str(main)],
                   video_layers=[{'gauge_idx': 0, 'clips': [str(cam2)], 'offset': -2.0}])

        def px(t, x, y):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', out, '-frames:v', '1',
                                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                 capture_output=True, check=True).stdout
            return np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3)[y, x]
        # the export starts at main t=1 s: the second camera starts 1 s later
        assert px(0.5, 240, 180)[2] > 150          # still the main (blue) picture
        assert px(1.5, 240, 180)[0] > 150          # the second (red) video in its box
        assert px(1.5, 40, 40)[2] > 150            # outside the box: the main video


def test_layer_offsets_line_both_laps_up_from_their_start():
    """Reference lap video: both laps play from their own line crossing."""
    from datetime import datetime, timezone
    from data_model import DataPoint, Lap
    from video_layers import layers_for

    def lap(start, csv):
        pts = [DataPoint(record=0, time=datetime(2024, 1, 1, tzinfo=timezone.utc), lat=0, lon=0,
                         alt=0, speed=0, gforce_x=0, gforce_y=0, gforce_z=0, lap=1, gyro_x=0,
                         gyro_y=0, gyro_z=0, elapsed=start + 0.03, lap_elapsed=0.03)]
        return Lap(lap_num=1, points=pts, duration=60.0, session_csv=csv)
    layout = {'gauges': [{'type': 'Video', 'video_source': 'reference', 'visible': True},
                         {'type': 'Video', 'video_source': 'camera', 'visible': True}]}
    cache = {'sessions': [{'csv_path': '/ref.csv', 'video_paths': ['/r.mp4']}]}
    layers = layers_for('/cur.csv', lap(100.0, '/cur.csv'), layout, 5.0,
                        {'/cur.csv': {'paths': ['/c2.mp4'], 'offset': 12.0}},
                        {'/ref.csv': 3.0}, cache, lap(40.0, '/ref.csv'))
    ref, cam = layers
    # main lap starts at video 105; ref lap at its video 43: layer = main - 62
    assert ref['offset'] == pytest.approx(-62.0) and ref['clips'] == ['/r.mp4']
    assert cam['offset'] == pytest.approx(-12.0)
