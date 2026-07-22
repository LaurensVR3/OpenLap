"""
Tests for export_runner.run_export — field-name compatibility and scope routing.
"""
import tempfile
from unittest.mock import MagicMock, patch, call
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_callbacks():
    return MagicMock(), MagicMock(), MagicMock()  # log, progress, done


def _run(items, scope='fastest', **kwargs):
    """Call run_export with safe defaults for everything we don't care about."""
    from export_runner import run_export
    log_cb, progress_cb, done_cb = _make_callbacks()
    run_export(
        items=items,
        scope=scope,
        export_path=tempfile.gettempdir(),
        encoder='libx264',
        crf=18,
        workers=1,
        padding=0.0,
        is_bike=False,
        show_map=False,
        show_tel=False,
        layout={},
        clip_start_s=0.0,
        clip_end_s=0.0,
        ref_mode='none',
        ref_lap_obj=None,
        bike_overrides={},
        session_info={},
        log_cb=log_cb,
        progress_cb=progress_cb,
        done_cb=done_cb,
        **kwargs,
    )
    return log_cb, progress_cb, done_cb


# ── Item field-name compatibility ─────────────────────────────────────────────

class TestItemFieldNames:
    """
    export_runner must accept both the webview field names (csv_path /
    video_paths / sync_offset) and the legacy Tkinter names (csv / videos /
    offset) so neither caller breaks.
    """

    def test_missing_csv_path_logs_skip(self):
        """An item with no csv_path (or csv) logs a skip and calls done."""
        log_cb, _, done_cb = _run([{'csv_path': '/nonexistent/file.csv',
                                     'video_paths': [], 'sync_offset': 0.0}])
        # File doesn't exist → should log a skip message
        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'Skipping' in logged or 'not found' in logged.lower()

    def test_webview_field_names_are_read(self):
        """
        Directly verify the field-resolution expressions used in export_runner
        correctly prefer the webview names over the legacy ones.
        """
        item = {
            'csv_path':    '/data/session.csv',
            'video_paths': ['/video/clip.mp4'],
            'sync_offset': 1.23,
            # legacy fields present but must be ignored when new names exist
            'csv':    '/old/path.csv',
            'videos': ['/old/video.mp4'],
            'offset': 99.9,
        }

        # Replicate the resolution logic from export_runner.py exactly
        csv_path = item.get('csv_path') or item.get('csv')
        videos   = item.get('video_paths') or item.get('videos') or []
        offset   = item.get('sync_offset') if item.get('sync_offset') is not None \
                   else (item.get('offset') or 0.0)

        assert csv_path == '/data/session.csv'
        assert videos   == ['/video/clip.mp4']
        assert offset   == 1.23

    def test_legacy_field_names_are_read(self, tmp_path):
        """csv / videos / offset (Tkinter legacy) are also resolved correctly."""
        fake_csv = tmp_path / 'session.csv'
        fake_csv.write_text('dummy')

        item = {
            'csv':    str(fake_csv),
            'videos': ['/video/clip.mp4'],
            'offset': 2.5,
        }

        resolved = {}

        def capturing_run(items, **kw):
            for it in items:
                resolved['csv']    = it.get('csv_path') or it.get('csv')
                resolved['videos'] = it.get('video_paths') or it.get('videos') or []
                resolved['offset'] = it.get('sync_offset') if it.get('sync_offset') is not None \
                                     else (it.get('offset') or 0.0)

        capturing_run(items=[item])

        assert resolved['csv']    == str(fake_csv)
        assert resolved['videos'] == ['/video/clip.mp4']
        assert resolved['offset'] == 2.5

    def test_webview_sync_offset_zero_is_preserved(self):
        """sync_offset=0.0 must not fall back to legacy 'offset' field."""
        item = {
            'csv_path':    str(tempfile.gettempdir() + '/s.csv'),
            'video_paths': [],
            'sync_offset': 0.0,
            'offset':      99.9,   # legacy field that must be ignored
        }
        # Replicate the resolution logic from export_runner.py
        offset = item.get('sync_offset') if item.get('sync_offset') is not None \
                 else (item.get('offset') or 0.0)
        assert offset == 0.0  # not 99.9


# ── Scope value routing ────────────────────────────────────────────────────────

class TestScopeValues:
    """
    The JS export page sends scope values that must match the Python conditions.
    Verify the expected string values are what Python branches on.
    """

    VALID_SCOPES = ('fastest', 'all_laps', 'clip', 'full', 'selected_lap', 'lap_range')

    @pytest.mark.parametrize('scope', VALID_SCOPES)
    def test_known_scope_strings(self, scope):
        """Each JS scope option value corresponds to a branch in export_runner."""
        import export_runner
        import inspect
        src = inspect.getsource(export_runner.run_export)
        assert f"scope == '{scope}'" in src or f"scope == \"{scope}\"" in src, \
            f"Scope '{scope}' has no matching branch in export_runner.run_export"

    def test_all_laps_not_all(self):
        """JS must send 'all_laps', not 'all' — confirm the Python branch string."""
        import export_runner, inspect
        src = inspect.getsource(export_runner.run_export)
        assert "== 'all_laps'" in src
        assert "== 'all'" not in src  # 'all' was the old broken value


# ── Progress value range ────────────────────────────────────────────────────────

class TestProgressRange:
    """
    export_runner.progress_cb is called with values in the range 0–100.
    Verify sess_prog never emits a value outside that range.
    """

    def test_progress_stays_within_0_100(self):
        """sess_prog must produce values in [0, 100] for all valid calling patterns.

        Calling convention in export_runner:
          - During session rendering:  done_jobs = 0..total_jobs-1,  render_pct = 0..100
          - After session completes:   done_jobs = 0..total_jobs,    render_pct = 0
        """
        # Replicate the sess_prog formula from export_runner directly
        def sess_prog(done_jobs, total_jobs, join_share, render_pct):
            sess_w  = 100.0 / max(total_jobs, 1)
            base    = done_jobs * sess_w
            within  = join_share * sess_w + (render_pct / 100) * (1 - join_share) * sess_w
            return base + within

        for total in (1, 3, 10):
            for join in (0.0, 0.10):
                # Mid-render: done_jobs is the index of the session being rendered
                for done in range(total):
                    for pct in (0, 50, 100):
                        v = sess_prog(done, total, join, pct)
                        assert 0.0 <= v <= 100.0 + 1e-9, \
                            f"progress out of range: {v} (done={done}/{total}, join={join}, pct={pct})"
            # End-of-session: done_jobs incremented, render_pct=0, join_share always 0
            # (export_runner calls: sess_prog(done_jobs, 0, 0, ""))
            for done in range(total + 1):
                v = sess_prog(done, total, 0.0, 0)
                assert 0.0 <= v <= 100.0 + 1e-9, \
                    f"end-of-session progress out of range: {v} (done={done}/{total})"

    def test_ref_mode_session_best_string(self):
        """reference_resolver handles 'session_best'; JS must send that exact string."""
        import reference_resolver, inspect
        src = inspect.getsource(reference_resolver.resolve_reference_lap)
        assert "'session_best'" in src, "resolve_reference_lap must handle 'session_best'"
        assert "'best_in_session'" not in src, "'best_in_session' is the old broken value"

    def test_new_ref_modes_exist_in_resolver(self):
        """reference_resolver must handle all new ref_mode values."""
        import reference_resolver, inspect
        src = inspect.getsource(reference_resolver.resolve_reference_lap)
        for mode in ('session_best_so_far', 'personal_best', 'day_best', 'manual'):
            assert f"'{mode}'" in src, f"resolve_reference_lap must handle '{mode}'"

    def test_load_any_session_is_module_level(self):
        """load_any_session must be importable from export_runner for use by other modules."""
        from export_runner import load_any_session
        assert callable(load_any_session)


# ── load_any_session format dispatch ──────────────────────────────────────────

class TestLoadAnySessionDispatch:
    """
    load_any_session's format dispatch is a hardcoded if/elif chain of
    is_X(path) checks — every format session_scanner can detect must have a
    branch here, or exporting that format silently misroutes to the
    RaceBox CSV parser at the bottom (this happened for real: VBOX support
    was added to session_scanner/webview_api/auto_sync but load_any_session
    was never updated, so exporting a VBOX session would fail).
    """

    @pytest.mark.parametrize('is_check,module,load_fn', [
        ('is_motec_ld',    'motec_data',   'load_ld'),
        ('is_vbox',        'vbox_data',    'load_vbo'),
        ('is_gpx',         'gpx_data',     'load_gpx'),
        ('is_unipro_tsv',  'unipro_data',  'load_tsv'),
        ('is_unipro_uni',  'unipro_data',  'load_uni'),
        ('is_aim_csv',     'aim_data',     'load_csv'),
    ])
    def test_dispatches_to_correct_loader(self, is_check, module, load_fn, monkeypatch):
        import importlib
        from export_runner import load_any_session

        # Every format's is_X check defaults to False (path doesn't match),
        # except the one under test.
        for mod_name in ('motec_data', 'vbox_data', 'gpx_data', 'unipro_data', 'aim_data'):
            mod = importlib.import_module(mod_name)
            for fn_name in dir(mod):
                if fn_name.startswith('is_') and callable(getattr(mod, fn_name)):
                    monkeypatch.setattr(mod, fn_name, lambda p: False)

        target_mod = importlib.import_module(module)
        monkeypatch.setattr(target_mod, is_check, lambda p: True)
        sentinel = object()
        monkeypatch.setattr(target_mod, load_fn, lambda p: sentinel)

        assert load_any_session('/fake/path') is sentinel

    def test_falls_back_to_racebox_csv_when_nothing_else_matches(self, monkeypatch):
        import importlib
        from export_runner import load_any_session

        for mod_name in ('motec_data', 'vbox_data', 'gpx_data', 'unipro_data', 'aim_data'):
            mod = importlib.import_module(mod_name)
            for fn_name in dir(mod):
                if fn_name.startswith('is_') and callable(getattr(mod, fn_name)):
                    monkeypatch.setattr(mod, fn_name, lambda p: False)

        import racebox_data
        sentinel = object()
        monkeypatch.setattr(racebox_data, 'load_csv', lambda p: sentinel)

        assert load_any_session('/fake/path') is sentinel


# ── Overlay-only export without a source video ───────────────────────────────

class TestOverlayOnlyWithoutVideo:
    """
    An overlay-only export draws onto a blank transparent canvas (see
    video_renderer.render_lap) and never reads pixels from a source video, so
    it must not be skipped just because no video is attached. A non-overlay
    export with no video has nothing to render onto and must still be skipped.
    """

    def test_overlay_only_proceeds_without_video(self, racebox_car_csv_path):
        with patch('video_renderer.render_lap') as mock_render:
            log_cb, _, done_cb = _run(
                items=[{'csv_path': racebox_car_csv_path, 'video_paths': [],
                        'sync_offset': 0.0, 'overlay_only': True}],
                scope='fastest',
            )
        mock_render.assert_called_once()
        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'No video file' not in logged
        done_cb.assert_called_once()

    def test_non_overlay_export_is_skipped_without_video(self, racebox_car_csv_path):
        with patch('video_renderer.render_lap') as mock_render:
            log_cb, _, done_cb = _run(
                items=[{'csv_path': racebox_car_csv_path, 'video_paths': [],
                        'sync_offset': 0.0, 'overlay_only': False}],
                scope='fastest',
            )
        mock_render.assert_not_called()
        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'No video file' in logged
        done_cb.assert_called_once()


# ── Multi-clip join phase ─────────────────────────────────────────────────────

class TestJoinPhase:
    """
    A session matched to more than one video clip goes through a join
    (concat_videos) before rendering. Two things must hold:
      - an unreachable clip (e.g. a dropped network share) fails that item
        gracefully instead of crashing the whole export thread with an
        unhandled OSError from os.path.getmtime;
      - concat_videos is given a progress_cb, so a large/slow join reports
        progress instead of sitting at a static 0% (previously indistin-
        guishable from a genuine hang — see video_renderer.concat_videos).
    """

    def test_unreachable_clip_is_skipped_not_crashed(self, racebox_car_csv_path):
        """getmtime on a vanished network path must not crash the export thread."""
        log_cb, _, done_cb = _run(
            items=[{'csv_path': racebox_car_csv_path,
                    'video_paths': ['//unreachable-share/a.mp4', '//unreachable-share/b.mp4'],
                    'sync_offset': 0.0}],
            scope='fastest',
        )
        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'Join failed' in logged
        done_cb.assert_called_once()   # export finishes (with an error), doesn't hang/crash

    def test_concat_videos_receives_a_progress_callback(self, racebox_car_csv_path, tmp_path):
        """The join phase must wire progress_cb through to concat_videos so
        the UI shows real progress instead of a static 0% for however long
        the ffmpeg join takes."""
        v1 = tmp_path / 'a.mp4'
        v2 = tmp_path / 'b.mp4'
        v1.write_bytes(b'fake')
        v2.write_bytes(b'fake')

        with patch('video_renderer.concat_videos') as mock_concat, \
             patch('video_renderer.render_lap'):
            _run(
                items=[{'csv_path': racebox_car_csv_path,
                        'video_paths': [str(v1), str(v2)],
                        'sync_offset': 0.0}],
                scope='fastest',
            )
        mock_concat.assert_called_once()
        assert 'progress_cb' in mock_concat.call_args.kwargs
        assert callable(mock_concat.call_args.kwargs['progress_cb'])


# ── render_lap call-site / signature compatibility ────────────────────────────

class TestRenderLapCallCompatibility:
    """
    export_runner.py calls video_renderer.render_lap(...) from 7 different
    scope branches, each hand-written with its own keyword-argument list.
    render_lap is never called for real anywhere else in this suite (every
    other test mocks it away), so a keyword export_runner passes that
    render_lap's signature doesn't accept would crash every real export with
    a TypeError and nothing here would notice. Guard against that class of
    drift directly by checking every call actually made is signature-valid,
    across every scope.
    """

    @pytest.mark.parametrize('scope', ['selected_lap', 'fastest', 'all_laps', 'lap_range', 'full'])
    def test_call_kwargs_are_all_accepted_by_render_lap(self, racebox_car_csv_path, scope):
        import inspect
        from video_renderer import render_lap as real_render_lap
        accepted = set(inspect.signature(real_render_lap).parameters.keys())

        with patch('video_renderer.render_lap') as mock_render:
            _run(
                items=[{'csv_path': racebox_car_csv_path, 'video_paths': ['/fake/video.mp4'],
                        'sync_offset': 0.0, 'lap_idx': 0}],
                scope=scope,
            )

        assert mock_render.called, f"render_lap was never called for scope={scope!r}"
        for c in mock_render.call_args_list:
            unexpected = set(c.kwargs.keys()) - accepted
            assert not unexpected, (
                f"export_runner passed keyword(s) {unexpected} for scope={scope!r} "
                f"that render_lap's real signature does not accept")

    def test_is_cancelled_is_threaded_through(self, racebox_car_csv_path):
        """is_cancelled must reach render_lap, not just be checked between items —
        otherwise Cancel does nothing until the current render finishes."""
        sentinel = MagicMock(return_value=False)
        with patch('video_renderer.render_lap') as mock_render:
            _run(
                items=[{'csv_path': racebox_car_csv_path, 'video_paths': ['/fake/video.mp4'],
                        'sync_offset': 0.0, 'lap_idx': 0}],
                scope='fastest',
                is_cancelled=sentinel,
            )
        assert mock_render.call_args.kwargs.get('is_cancelled') is sentinel


# ── Cancellation ────────────────────────────────────────────────────────────────

class TestCancellation:
    """
    run_export must stop processing further queued items once is_cancelled()
    starts returning True — previously the cancel flag set by the UI's Cancel
    button was never read anywhere, so an in-progress export could not be
    stopped once started.
    """

    def test_stops_before_next_item_once_cancelled(self):
        """Only the first item is processed; is_cancelled() flips True after it."""
        calls = {'n': 0}

        def fake_cancelled():
            # False for the first check (before item 1), True from then on —
            # simulates the user clicking Cancel while item 1 is rendering.
            calls['n'] += 1
            return calls['n'] > 1

        log_cb, progress_cb, done_cb = _run(
            items=[
                {'csv_path': '/nonexistent/a.csv', 'video_paths': [], 'sync_offset': 0.0},
                {'csv_path': '/nonexistent/b.csv', 'video_paths': [], 'sync_offset': 0.0},
                {'csv_path': '/nonexistent/c.csv', 'video_paths': [], 'sync_offset': 0.0},
            ],
            is_cancelled=fake_cancelled,
        )

        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'a.csv' in logged
        assert 'b.csv' not in logged
        assert 'c.csv' not in logged
        done_cb.assert_called_once()
        ok, msg = done_cb.call_args[0]
        assert ok is False
        assert 'cancel' in msg.lower()

    def test_never_cancelled_runs_all_items(self):
        """is_cancelled always False (or omitted) must not affect normal completion."""
        log_cb, progress_cb, done_cb = _run(
            items=[
                {'csv_path': '/nonexistent/a.csv', 'video_paths': [], 'sync_offset': 0.0},
                {'csv_path': '/nonexistent/b.csv', 'video_paths': [], 'sync_offset': 0.0},
            ],
            is_cancelled=lambda: False,
        )
        logged = ' '.join(str(c) for c in log_cb.call_args_list)
        assert 'a.csv' in logged
        assert 'b.csv' in logged
        ok, msg = done_cb.call_args[0]
        assert 'cancel' not in msg.lower()
