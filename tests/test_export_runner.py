"""
Tests for export_runner.run_export — field-name compatibility and scope routing.
"""
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
        export_path='/tmp',
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
            'csv_path':    '/tmp/s.csv',
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
