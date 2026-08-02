"""
Tests for video_renderer helper functions that don't require ffmpeg or video files.
Covers: _build_session_meta, _build_map_data, _setup_delta_time, sync offset logic.
"""
import math
import pytest


# ── _build_session_meta ────────────────────────────────────────────────────────

class TestBuildSessionMeta:
    def _make_session(self, **kwargs):
        """Build a minimal mock session object."""
        from unittest.mock import MagicMock
        sess = MagicMock()
        sess.track        = kwargs.get('track', 'Spa-Francorchamps')
        sess.vehicle      = kwargs.get('vehicle', 'GT3')
        sess.session_type = kwargs.get('session_type', 'Race')
        sess.source       = kwargs.get('source', 'RaceBox')
        sess.date_utc     = kwargs.get('date_utc', '2024-06-15T10:30:00Z')
        sess.all_points   = []
        return sess

    def test_basic_fields(self):
        from video_renderer import _build_session_meta
        sess = self._make_session()
        meta = _build_session_meta(sess)
        assert meta['info_track']   == 'Spa-Francorchamps'
        assert meta['info_session'] == 'Race'
        assert meta['info_source']  == 'RaceBox'

    def test_date_parsed(self):
        from video_renderer import _build_session_meta
        sess = self._make_session(date_utc='2024-06-15T10:30:00Z')
        meta = _build_session_meta(sess)
        assert meta['info_date'] == '2024-06-15'
        assert meta['info_time'] == '10:30'

    def test_info_overrides_applied(self):
        from video_renderer import _build_session_meta
        sess = self._make_session(track='Old Track')
        meta = _build_session_meta(sess, info_overrides={'info_track': 'New Track'})
        assert meta['info_track'] == 'New Track'

    def test_empty_override_not_applied(self):
        from video_renderer import _build_session_meta
        sess = self._make_session(track='Real Track')
        meta = _build_session_meta(sess, info_overrides={'info_track': ''})
        assert meta['info_track'] == 'Real Track'

    def test_missing_date_utc(self):
        from video_renderer import _build_session_meta
        from unittest.mock import MagicMock
        sess = MagicMock()
        sess.date_utc     = None
        sess.track        = 'Nürburgring'
        sess.vehicle      = ''
        sess.session_type = ''
        sess.source       = ''
        sess.all_points   = []
        meta = _build_session_meta(sess)
        assert meta['info_date'] == ''
        assert meta['info_time'] == ''


# ── _build_map_data ────────────────────────────────────────────────────────────

class TestBuildMapData:
    def _make_point(self, lat, lon):
        from unittest.mock import MagicMock
        p = MagicMock()
        p.lat = lat
        p.lon = lon
        return p

    def _make_job(self, points=None):
        from unittest.mock import MagicMock
        job = MagicMock()
        if points is not None:
            job.lap.points = points
        else:
            job.lap = None
        return job

    def test_show_map_false_returns_empty(self):
        from video_renderer import _build_map_data
        from unittest.mock import MagicMock
        job  = self._make_job([self._make_point(50.4, 5.9)])
        sess = MagicMock()
        sess.all_points = job.lap.points
        lats, lons, arr = _build_map_data(job, sess, show_map=False)
        assert lats == [] and lons == [] and arr is None

    def test_no_points_returns_none_array(self):
        from video_renderer import _build_map_data
        from unittest.mock import MagicMock
        job  = self._make_job([])
        sess = MagicMock()
        sess.all_points = []
        lats, lons, arr = _build_map_data(job, sess, show_map=True)
        assert arr is None

    def test_gps_points_produce_numpy_array(self):
        import numpy as np
        from video_renderer import _build_map_data
        from unittest.mock import MagicMock
        pts = [self._make_point(50.4 + i * 0.001, 5.9 + i * 0.001) for i in range(10)]
        job  = self._make_job(pts)
        sess = MagicMock()
        sess.all_points = pts
        lats, lons, arr = _build_map_data(job, sess, show_map=True)
        assert arr is not None
        assert arr.shape[1] == 2
        assert len(lats) == len(lons) > 0


# ── _N_SECTORS constant ────────────────────────────────────────────────────────

def test_n_sectors_constant():
    from video_renderer import _N_SECTORS
    assert isinstance(_N_SECTORS, int)
    assert _N_SECTORS > 0


# ── Sync offset frame range calculation ───────────────────────────────────────

class TestSyncOffsetFrameRange:
    """
    The render_lap frame range calculation:
        vid_start = max(0, sync_offset + gpx_start - padding)
        f_start   = max(0, int(vid_start * fps))

    Verify the math for a few representative cases.
    """

    def _calc(self, sync_offset, gpx_start, gpx_end, fps, total_frames,
              padding=5.0):
        import math
        vid_lap_start = sync_offset + gpx_start
        vid_lap_end   = sync_offset + gpx_end
        vid_start     = max(0.0, vid_lap_start - padding)
        vid_end       = min(total_frames / fps, vid_lap_end + padding)
        f_start       = max(0, int(vid_start * fps))
        f_end         = min(total_frames, int(math.ceil(vid_end * fps)))
        return f_start, f_end

    def test_zero_offset(self):
        f_start, f_end = self._calc(0.0, 10.0, 90.0, 30.0, 3600)
        assert f_start == int((10.0 - 5.0) * 30)   # 150
        assert f_end   == int(math.ceil((90.0 + 5.0) * 30))  # 2850

    def test_positive_offset_shifts_window(self):
        f_start_no, _ = self._calc(0.0, 10.0, 90.0, 30.0, 9000)
        f_start_w,  _ = self._calc(5.0, 10.0, 90.0, 30.0, 9000)
        assert f_start_w > f_start_no

    def test_negative_offset_clamps_to_zero(self):
        # sync_offset=-20 would push vid_start below 0 — must clamp
        f_start, _ = self._calc(-20.0, 5.0, 85.0, 30.0, 9000)
        assert f_start == 0

    def test_lap_beyond_video_gives_zero_frames(self):
        # 60-second video at 30fps = 1800 frames; lap at 200–280s is outside
        f_start, f_end = self._calc(0.0, 200.0, 280.0, 30.0, 1800)
        assert f_end <= f_start   # no valid frame range


# ── Overlay-only virtual canvas (no source video) ─────────────────────────────

class TestOverlayOnlyVirtualDuration:
    """
    render_lap synthesizes fps/dimensions/duration when overlay_only=True and
    the source video is missing (cap metadata reads as 0). Duration must track
    the actual lap or session length, not a fixed floor — an earlier version of
    this fix hardcoded a 3600s minimum, which silently produced an hour of
    blank overlay for any full-session export shorter than that.
    """

    FPS = 30.0

    def _virtual_total(self, sync_offset, padding, gpx_end=None, session_end=None):
        virtual_end = sync_offset + (gpx_end if gpx_end is not None else session_end)
        return int(math.ceil((virtual_end + padding + 2.0) * self.FPS))

    def test_lap_scope_duration_matches_lap_not_a_fixed_floor(self):
        # A short (20s) lap must not be inflated to an hour of frames.
        total = self._virtual_total(sync_offset=0.0, padding=5.0, gpx_end=20.0)
        assert total < 3600 * self.FPS
        assert total >= int((20.0 + 5.0) * self.FPS)

    def test_full_session_duration_scales_with_session_length(self):
        short_total = self._virtual_total(sync_offset=0.0, padding=0.0, session_end=300.0)   # 5 min
        long_total  = self._virtual_total(sync_offset=0.0, padding=0.0, session_end=7200.0)  # 2 h
        # Duration must track the session, not collapse to a shared fixed floor.
        assert long_total > short_total
        assert short_total < 3600 * self.FPS
        assert long_total  > 3600 * self.FPS

    def test_source_has_no_fixed_duration_floor(self):
        """Guard against reintroducing a hardcoded minimum virtual duration."""
        import video_renderer, inspect
        src = inspect.getsource(video_renderer.render_lap)
        assert 'max(3600' not in src


# ── concat_videos join progress / stall handling ───────────────────────────────

class TestConcatVideosProgress:
    """
    concat_videos's join used to give zero progress feedback while ffmpeg
    ran — a multi-GB join over a slow/network-mounted source looked
    indistinguishable from a genuine hang. Verify: (1) progress_cb is driven
    from ffmpeg's own -progress output against the summed input duration,
    and (2) a stalled join (no progress for stall_timeout_s) is killed and
    raises VideoConcatError rather than blocking forever.
    """

    def _fake_proc(self, stdout_lines, returncode=0, poll_sequence=None):
        """A minimal stand-in for subprocess.Popen with the bits concat_videos
        actually touches: .stdout (iterable of bytes), .stderr (iterable),
        .poll()/.wait()/.kill(), .returncode."""
        from unittest.mock import MagicMock
        proc = MagicMock()
        proc.stdout = iter(stdout_lines)
        proc.stderr = iter([])
        proc.returncode = returncode
        # poll() returns None until the sequence is exhausted, then returncode
        poll_vals = list(poll_sequence) if poll_sequence is not None else [None, returncode]
        proc.poll.side_effect = poll_vals
        return proc

    def test_reports_progress_from_ffmpeg_out_time(self, tmp_path, monkeypatch):
        import video_renderer as vr

        monkeypatch.setattr(vr, '_probe_duration_s', lambda p: 10.0)
        lines = [
            b'out_time_ms=2000000\n',
            b'out_time_ms=5000000\n',
            b'out_time_ms=9500000\n',
            b'progress=end\n',
        ]
        proc = self._fake_proc(lines, returncode=0)
        monkeypatch.setattr(vr, '_popen', lambda *a, **k: proc)

        seen = []
        vr.concat_videos(['a.mp4', 'b.mp4'], str(tmp_path / 'out.mp4'),
                          progress_cb=lambda pct, msg: seen.append(pct))

        assert len(seen) == 3
        assert seen == sorted(seen)          # monotonically increasing
        # total_s = summed duration of both inputs (10.0s each) = 20.0s
        assert seen[-1] == pytest.approx(47.5, abs=0.1)   # 9.5s / 20.0s total

    def test_no_progress_cb_uses_plain_run(self, tmp_path, monkeypatch):
        """Without progress_cb, falls back to the original one-shot _run path
        (no -progress plumbing, no probing input durations)."""
        import video_renderer as vr
        from unittest.mock import MagicMock

        probed = []
        monkeypatch.setattr(vr, '_probe_duration_s', lambda p: probed.append(p) or 10.0)
        result = MagicMock(returncode=0)
        run_mock = MagicMock(return_value=result)
        monkeypatch.setattr(vr, '_run', run_mock)

        vr.concat_videos(['a.mp4', 'b.mp4'], str(tmp_path / 'out.mp4'))

        run_mock.assert_called_once()
        assert probed == []   # duration probing only happens when progress_cb is given

    def test_stalled_join_is_killed_and_raises(self, tmp_path, monkeypatch):
        import video_renderer as vr
        from exceptions import VideoConcatError

        monkeypatch.setattr(vr, '_probe_duration_s', lambda p: 100.0)
        # No progress lines at all — poll() keeps returning None (still
        # running) until stall detection kicks in and kills it. Both the
        # stream-copy attempt and its re-encode fallback stall the same way,
        # so _popen must hand back a fresh process each call.
        procs = []
        def _new_proc(*a, **k):
            p = self._fake_proc(stdout_lines=[], returncode=0,
                                 poll_sequence=[None] * 100)
            procs.append(p)
            return p
        monkeypatch.setattr(vr, '_popen', _new_proc)

        with pytest.raises(VideoConcatError):
            vr.concat_videos(['a.mp4', 'b.mp4'], str(tmp_path / 'out.mp4'),
                              progress_cb=lambda pct, msg: None,
                              stall_timeout_s=0.05)
        assert procs and all(p.kill.assert_called_once() is None for p in procs)


# ── quality_args ───────────────────────────────────────────────────────────────

class TestQualityArgs:
    """Constant-quality flag selection per encoder family.

    The VideoToolbox cases are the reason this helper exists: ffmpeg accepts
    -qp for h264_videotoolbox but ignores it, so the Quality slider silently
    did nothing on macOS. These assertions run anywhere — no VideoToolbox
    hardware or ffmpeg needed.
    """

    def test_libx264_uses_crf(self):
        from video_renderer import quality_args
        assert quality_args('libx264', 18) == ['-crf', '18']

    def test_nvenc_uses_cq(self):
        from video_renderer import quality_args
        assert quality_args('h264_nvenc', 20) == ['-rc', 'vbr', '-cq', '20', '-b:v', '0']

    def test_videotoolbox_uses_qv_not_qp(self):
        from video_renderer import quality_args
        args = quality_args('h264_videotoolbox', 18)
        assert '-qp' not in args
        assert args[0] == '-q:v'

    def test_videotoolbox_scale_is_inverted(self):
        """Higher CRF means worse quality; higher -q:v means better."""
        from video_renderer import quality_args
        best  = int(quality_args('h264_videotoolbox', 12)[1])
        worst = int(quality_args('h264_videotoolbox', 32)[1])
        assert best > worst

    def test_videotoolbox_matches_measured_libx264_equivalents(self):
        """Anchors: measured against libx264 output size on the same source.

        crf 12 -> q:v 76 and crf 18 -> q:v 65 each landed within ~1.5% of the
        equivalent libx264 encode. If the mapping is reworked, re-measure
        rather than just updating these numbers.
        """
        from video_renderer import quality_args
        assert quality_args('h264_videotoolbox', 12) == ['-q:v', '76']
        assert quality_args('h264_videotoolbox', 18) == ['-q:v', '65']

    def test_videotoolbox_clamped_to_valid_range(self):
        """-q:v outside 1-100 is rejected by ffmpeg; CRF is not hard-bounded."""
        from video_renderer import quality_args
        assert quality_args('h264_videotoolbox', 0)[1]   == '100'
        assert quality_args('h264_videotoolbox', 51)[1]  == '1'
        assert quality_args('h264_videotoolbox', 99)[1]  == '1'
        assert quality_args('h264_videotoolbox', -10)[1] == '100'

    def test_hevc_videotoolbox_also_covered(self):
        from video_renderer import quality_args
        assert quality_args('hevc_videotoolbox', 18) == ['-q:v', '65']

    def test_other_encoders_unchanged(self):
        """AMF/QSV/libx265 honour -qp — this fix must not alter them."""
        from video_renderer import quality_args
        for enc in ('libx265', 'h264_amf', 'h264_qsv', 'hevc_nvenc'):
            assert quality_args(enc, 22) == ['-qp', '22']
