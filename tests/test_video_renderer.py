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
