"""
Tests for auto_sync.py — video/telemetry sync offset detection.

All tests use small synthetic numpy signal arrays (no real video files or
ffmpeg/ffprobe subprocess calls, except where explicitly monkeypatched).
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from auto_sync import _correlate, _append_gap_frames, _video_gap_seconds, _load_session


# ── _load_session — source dispatch ───────────────────────────────────────────

class TestLoadSessionDispatch:
    """
    _load_session's source dispatch is a hardcoded if/elif chain — every
    telemetry source session_scanner._csv_source() can return must have a
    branch here, or auto-sync silently fails for that source with
    'Unknown telemetry source' (this happened for real: 'Unipro' was added
    to session_scanner/webview_api but forgotten here — see
    tests/test_webview_api.py's TestSaveConfigFields for the sibling bug in
    save_config()). Mocks each loader so this doesn't need real telemetry files.
    """

    @pytest.mark.parametrize('source,module,fn', [
        ('RaceBox',      'racebox_data', 'load_csv'),
        ('AIM Mychron',  'aim_data',     'load_csv'),
        ('AIM',          'aim_data',     'load_csv'),
        ('GPX',          'gpx_data',     'load_gpx'),
        ('MoTeC',        'motec_data',   'load_ld'),
        ('VBOX',         'vbox_data',    'load_vbo'),
        ('Unipro',       'unipro_data',  'load_uni'),
    ])
    def test_dispatches_to_correct_loader(self, source, module, fn, monkeypatch):
        import importlib
        mod = importlib.import_module(module)
        sentinel = object()
        monkeypatch.setattr(mod, fn, lambda csv_path: sentinel)
        assert _load_session('/fake/path', source) is sentinel

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match='Unknown telemetry source'):
            _load_session('/fake/path', 'NotARealSource')

    def test_unipro_tsv_extension_routes_to_load_tsv(self, monkeypatch):
        """Unipro has two on-disk formats sharing one source name — the
        extension (not just the source string) decides which loader runs."""
        import unipro_data
        sentinel = object()
        monkeypatch.setattr(unipro_data, 'is_unipro_tsv', lambda p: True)
        monkeypatch.setattr(unipro_data, 'load_tsv', lambda csv_path: sentinel)
        assert _load_session('/fake/path.tsv', 'Unipro') is sentinel

    def test_unipro_uni_extension_routes_to_load_uni(self, monkeypatch):
        import unipro_data
        sentinel = object()
        monkeypatch.setattr(unipro_data, 'is_unipro_tsv', lambda p: False)
        monkeypatch.setattr(unipro_data, 'load_uni', lambda csv_path: sentinel)
        assert _load_session('/fake/path.uni', 'Unipro') is sentinel


# ── _correlate — single-segment sanity baseline ───────────────────────────────

def test_correlate_single_segment_recovers_known_offset():
    # Telemetry signal: a few Gaussian "events" over a 60s window.
    fps = 5.0
    n = int(60.0 * fps)
    t = np.arange(n) / fps
    rng = np.random.default_rng(0)
    tel_sig = np.zeros(n)
    for center in (10.0, 25.0, 40.0, 50.0):
        tel_sig += np.exp(-0.5 * ((t - center) / 1.0) ** 2)
    tel_sig += rng.normal(0, 0.01, n)

    # Video signal is the same pattern, delayed by 3 seconds (video events
    # happen 3s later than the matching telemetry events).
    shift_frames = int(round(3.0 * fps))
    vid_sig = np.zeros(n)
    vid_sig[shift_frames:] = tel_sig[: n - shift_frames]
    vid_sig += rng.normal(0, 0.01, n)

    offset, confidence = _correlate(vid_sig, tel_sig, fps=fps, search_window_s=30.0)
    assert offset == pytest.approx(3.0, abs=0.3)
    assert confidence > 1.0   # comfortably above MIN_CONFIDENCE


# ── _append_gap_frames ─────────────────────────────────────────────────────────

def test_append_gap_frames_inserts_correct_frame_count():
    sig = [1.0, 2.0, 3.0]
    n_appended = _append_gap_frames(sig, gap_s=2.0, fps=5.0)
    assert n_appended == 10
    assert len(sig) == 13


def test_append_gap_frames_zero_gap_appends_nothing():
    sig = [1.0, 2.0]
    n_appended = _append_gap_frames(sig, gap_s=0.0, fps=5.0)
    assert n_appended == 0
    assert sig == [1.0, 2.0]


def test_append_gap_frames_sub_frame_gap_appends_nothing():
    sig = [1.0]
    # 0.05s at 5 fps rounds to 0 frames
    n_appended = _append_gap_frames(sig, gap_s=0.05, fps=5.0)
    assert n_appended == 0


def test_append_gap_frames_uses_mean_of_existing_signal():
    sig = [2.0, 4.0]   # mean = 3.0
    _append_gap_frames(sig, gap_s=1.0, fps=5.0)
    assert sig[2:] == pytest.approx([3.0] * 5)


def test_append_gap_frames_empty_signal_uses_zero_fill():
    sig = []
    _append_gap_frames(sig, gap_s=1.0, fps=5.0)
    assert sig == pytest.approx([0.0] * 5)


# ── Multi-segment gap: naive concatenation is wrong, gap-filled is correct ────

def _build_gap_scenario(true_offset_s: float, gap_s: float, fps: float = 5.0):
    """
    Construct a telemetry signal with one distinctive event, and a "video"
    that observed that same event but recorded in two segments with a real
    gap_s-second break in between (camera stopped, no frames produced —
    but real time kept elapsing).

    Returns (naive_concat, fixed_concat, tel_sig, expected_offset) where
    expected_offset is the offset _correlate should recover from fixed_concat
    (and NOT from naive_concat, which is missing gap_s seconds of timeline).
    """
    rng = np.random.default_rng(42)

    tel_duration_s = 260.0
    tel_n = int(tel_duration_s * fps)
    tel_t = np.arange(tel_n) / fps
    event_tel_time = 160.0
    tel_sig = np.exp(-0.5 * ((tel_t - event_tel_time) / 1.0) ** 2)
    tel_sig += rng.normal(0, 0.01, tel_n)

    # video_time v maps to tel_time (v + true_offset_s) — i.e. the camera
    # "sees" the telemetry event at video_time = event_tel_time - true_offset_s
    def sample_true_video(v_times: np.ndarray) -> np.ndarray:
        idx = np.clip(
            np.round((v_times + true_offset_s) * fps).astype(int), 0, tel_n - 1)
        return tel_sig[idx]

    # Segment A: video_time [0, 80) — before the event, unremarkable.
    seg_a = sample_true_video(np.arange(0, 80, 1.0 / fps))
    # Real gap: camera off from video_time 80 to 80+gap_s — no frames.
    # Segment B: resumes at video_time (80+gap_s) and runs to 220 — contains
    # the event (video_time of event = event_tel_time - true_offset_s).
    seg_b_start = 80.0 + gap_s
    seg_b = sample_true_video(np.arange(seg_b_start, 220, 1.0 / fps))

    naive_concat = np.concatenate([seg_a, seg_b])

    fixed_list = list(seg_a)
    _append_gap_frames(fixed_list, gap_s, fps)
    fixed_list.extend(seg_b)
    fixed_concat = np.array(fixed_list)

    return naive_concat, fixed_concat, tel_sig, true_offset_s


def test_multi_segment_naive_concat_offset_is_wrong_by_roughly_the_gap():
    naive_concat, _fixed, tel_sig, true_offset_s = _build_gap_scenario(
        true_offset_s=5.0, gap_s=50.0)
    naive_offset, _conf = _correlate(naive_concat, tel_sig, fps=5.0, search_window_s=120.0)
    # The naive (no-gap-accounting) offset is wrong by ~ the 50s gap that
    # concatenation silently dropped from the timeline.
    error = abs(abs(naive_offset) - true_offset_s)
    assert error == pytest.approx(50.0, abs=1.0)


def test_multi_segment_gap_filled_concat_recovers_correct_offset():
    _naive, fixed_concat, tel_sig, true_offset_s = _build_gap_scenario(
        true_offset_s=5.0, gap_s=50.0)
    fixed_offset, _conf = _correlate(fixed_concat, tel_sig, fps=5.0, search_window_s=120.0)
    # With the real gap accounted for, the recovered offset magnitude should
    # match the true offset closely.
    assert abs(fixed_offset) == pytest.approx(true_offset_s, abs=1.0)


def test_multi_segment_fix_beats_naive():
    naive_concat, fixed_concat, tel_sig, true_offset_s = _build_gap_scenario(
        true_offset_s=5.0, gap_s=50.0)
    naive_offset, _ = _correlate(naive_concat, tel_sig, fps=5.0, search_window_s=120.0)
    fixed_offset, _ = _correlate(fixed_concat, tel_sig, fps=5.0, search_window_s=120.0)

    naive_error = abs(abs(naive_offset) - true_offset_s)
    fixed_error = abs(abs(fixed_offset) - true_offset_s)
    assert fixed_error < naive_error
    assert fixed_error < 1.0


# ── _video_gap_seconds ──────────────────────────────────────────────────────────

def test_video_gap_seconds_computes_real_gap(monkeypatch):
    t0 = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=130)   # segment B starts 130s after segment A started

    def _fake_ct(path):
        return {'a.mp4': t0, 'b.mp4': t1}[path]

    monkeypatch.setattr('auto_sync._probe_creation_time', _fake_ct)
    # Segment A duration is 80s, so the real gap between A's end and B's
    # start is 130 - 80 = 50s.
    gap = _video_gap_seconds('a.mp4', prev_duration=80.0, cur_path='b.mp4')
    assert gap == pytest.approx(50.0)


def test_video_gap_seconds_clamps_negative_to_zero(monkeypatch):
    t0 = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)   # overlapping / back-to-back segments

    monkeypatch.setattr('auto_sync._probe_creation_time',
                        lambda path: {'a.mp4': t0, 'b.mp4': t1}[path])
    gap = _video_gap_seconds('a.mp4', prev_duration=80.0, cur_path='b.mp4')
    assert gap == 0.0


def test_video_gap_seconds_missing_creation_time_falls_back_to_zero(monkeypatch):
    monkeypatch.setattr('auto_sync._probe_creation_time', lambda path: None)
    gap = _video_gap_seconds('a.mp4', prev_duration=80.0, cur_path='b.mp4')
    assert gap == 0.0


# ── correlate_channels — secondary telemetry sync ────────────────────────────

class TestCorrelateChannels:
    """
    correlate_channels() cross-correlates two telemetry files logged during
    the same run (rather than video motion vs G-force) — trying every
    channel both files have usable data for (RPM, G-force, Speed, Altitude)
    and keeping whichever gives the best-confidence match. Its offset must
    follow session_merge.py's convention (secondary_elapsed = primary_elapsed
    + offset) — the OPPOSITE argument order from _correlate()'s own
    (vid_sig, tel_sig) usage elsewhere in this file, verified empirically
    during implementation.
    """

    @staticmethod
    def _fake_session(dt, n, **channel_values):
        """channel_values: any of rpm=, speed=, gforce_x=, gforce_y=, alt=
        as arrays of length n; anything not passed stays at the DataPoint
        default (0.0), same "absent channel" convention the loaders use."""
        from data_model import DataPoint, Session
        pts = [
            DataPoint(
                record=i, time=None, lat=0.0, lon=0.0,
                alt=float(channel_values.get('alt', [0.0] * n)[i]),
                speed=float(channel_values.get('speed', [0.0] * n)[i]),
                gforce_x=float(channel_values.get('gforce_x', [0.0] * n)[i]),
                gforce_y=float(channel_values.get('gforce_y', [0.0] * n)[i]),
                gforce_z=0.0, lap=0, gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
                elapsed=i * dt, rpm=float(channel_values.get('rpm', [0.0] * n)[i]),
            )
            for i in range(n)
        ]
        return Session(
            source='Test', date_utc='', track='', configuration='',
            session_type='', best_lap_time=0.0, all_points=pts, laps=[],
        )

    @staticmethod
    def _shifted_spiky_profile(fps, n, t, shift_s, base=3000.0, amplitude=4000.0, noise=20.0, seed=1):
        """A distinctive profile (idle + a few sharp spikes at fixed times —
        like gear-shift RPM blips or braking-zone G-force peaks) plus a
        time-shifted, independently-noised copy — gives a clean correlation
        peak the way real gearshifts/braking zones do, mirroring the style
        of _correlate()'s own test above."""
        rng = np.random.default_rng(seed)
        profile = np.full(n, base)
        for center in (10.0, 25.0, 40.0, 50.0):
            profile += amplitude * np.exp(-0.5 * ((t - center) / 0.8) ** 2)
        primary = profile + rng.normal(0, noise, n)

        shift_frames = int(round(shift_s * fps))
        secondary = np.zeros(n)
        secondary[: n - shift_frames] = profile[shift_frames:]
        secondary += rng.normal(0, noise, n)
        return primary, secondary

    def test_recovers_known_offset_via_rpm(self, monkeypatch):
        import auto_sync

        fps = 5.0
        n = int(60.0 * fps)
        t = np.arange(n) / fps
        # Secondary logger started 4s after primary:
        # secondary_elapsed = primary_elapsed - 4  ->  offset (this module's
        # convention) should come out to -4.0.
        primary_rpm, secondary_rpm = self._shifted_spiky_profile(fps, n, t, shift_s=4.0)

        sessions = {
            '/primary.ld':    self._fake_session(1.0 / fps, n, rpm=primary_rpm),
            '/secondary.csv': self._fake_session(1.0 / fps, n, rpm=secondary_rpm),
        }
        monkeypatch.setattr(auto_sync, '_load_session', lambda path, source: sessions[path])

        offset, confidence, channel = auto_sync.correlate_channels(
            '/primary.ld', '/secondary.csv', 'MoTeC', 'AIM Mychron',
            search_window_s=20.0, fps=fps,
        )
        assert offset == pytest.approx(-4.0, abs=0.5)
        assert confidence > auto_sync.MIN_CONFIDENCE
        assert channel == 'RPM'

    def test_falls_back_to_speed_when_no_rpm_data(self, monkeypatch):
        # Neither file has RPM (e.g. a GPS-only logger paired with another
        # GPS-only logger) — correlation should still succeed via Speed.
        import auto_sync

        fps = 5.0
        n = int(60.0 * fps)
        t = np.arange(n) / fps
        primary_speed, secondary_speed = self._shifted_spiky_profile(
            fps, n, t, shift_s=2.0, base=80.0, amplitude=40.0, noise=1.0)

        sessions = {
            '/primary.ld':    self._fake_session(1.0 / fps, n, speed=primary_speed),
            '/secondary.csv': self._fake_session(1.0 / fps, n, speed=secondary_speed),
        }
        monkeypatch.setattr(auto_sync, '_load_session', lambda path, source: sessions[path])

        offset, confidence, channel = auto_sync.correlate_channels(
            '/primary.ld', '/secondary.csv', 'RaceBox', 'GPX',
            search_window_s=20.0, fps=fps,
        )
        assert offset == pytest.approx(-2.0, abs=0.5)
        assert confidence > auto_sync.MIN_CONFIDENCE
        assert channel == 'Speed'

    def test_prefers_rpm_over_speed_when_both_usable(self, monkeypatch):
        # RPM is tried before Speed (see _CORRELATION_CANDIDATES order) — if
        # RPM alone already clears MIN_CONFIDENCE, it should win even though
        # a (noisier) Speed signal is also present on both sides.
        import auto_sync

        fps = 5.0
        n = int(60.0 * fps)
        t = np.arange(n) / fps
        primary_rpm, secondary_rpm = self._shifted_spiky_profile(fps, n, t, shift_s=4.0)
        # Much noisier speed signal — still usable, but a visibly worse match.
        primary_speed, secondary_speed = self._shifted_spiky_profile(
            fps, n, t, shift_s=4.0, base=80.0, amplitude=40.0, noise=25.0, seed=2)

        sessions = {
            '/primary.ld':    self._fake_session(1.0 / fps, n, rpm=primary_rpm, speed=primary_speed),
            '/secondary.csv': self._fake_session(1.0 / fps, n, rpm=secondary_rpm, speed=secondary_speed),
        }
        monkeypatch.setattr(auto_sync, '_load_session', lambda path, source: sessions[path])

        _, _, channel = auto_sync.correlate_channels(
            '/primary.ld', '/secondary.csv', 'MoTeC', 'AIM Mychron',
            search_window_s=20.0, fps=fps,
        )
        assert channel == 'RPM'

    def test_no_usable_channel_returns_zero_confidence(self, monkeypatch):
        import auto_sync
        sessions = {
            '/primary.ld':    self._fake_session(0.2, 50),  # every channel absent/flat
            '/secondary.csv': self._fake_session(0.2, 50),
        }
        monkeypatch.setattr(auto_sync, '_load_session', lambda path, source: sessions[path])
        offset, confidence, channel = auto_sync.correlate_channels(
            '/primary.ld', '/secondary.csv', 'MoTeC', 'AIM Mychron',
        )
        assert offset == 0.0
        assert confidence == 0.0
        assert channel == ''
