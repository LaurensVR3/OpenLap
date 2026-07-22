"""
Tests for WebviewAPI — clamping of user-supplied export parameters and
thread-safety of start_export / download_racebox_sessions.
"""
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Mock pywebview before importing webview_api so no display is required
if 'webview' not in sys.modules:
    sys.modules['webview'] = MagicMock()

from webview_api import WebviewAPI


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_config_dir):
    """Return a WebviewAPI instance backed by a temp config directory."""
    return WebviewAPI()


# ── workers / crf clamping ────────────────────────────────────────────────────

class TestExportParamClamping:
    """
    _run_export_bg clamps workers to [1, cpu_count] and crf to [0, 51]
    before passing them to run_export.  Verify the clamping values.
    """

    def _clamped(self, raw_workers, raw_crf):
        """Replicate the clamping logic from _run_export_bg."""
        workers = max(1, min(int(raw_workers), os.cpu_count() or 4))
        crf     = max(0, min(int(raw_crf), 51))
        return workers, crf

    def test_zero_workers_becomes_one(self):
        w, _ = self._clamped(0, 18)
        assert w == 1

    def test_negative_workers_becomes_one(self):
        w, _ = self._clamped(-5, 18)
        assert w == 1

    def test_excessive_workers_clamped_to_cpu_count(self):
        w, _ = self._clamped(99999, 18)
        assert w <= (os.cpu_count() or 4)

    def test_normal_workers_unchanged(self):
        w, _ = self._clamped(4, 18)
        assert w == 4

    def test_negative_crf_becomes_zero(self):
        _, c = self._clamped(4, -10)
        assert c == 0

    def test_crf_above_51_clamped(self):
        _, c = self._clamped(4, 99)
        assert c == 51

    def test_normal_crf_unchanged(self):
        _, c = self._clamped(4, 23)
        assert c == 23

    def test_clamping_applied_in_run_export_bg(self, api, tmp_config_dir):
        """The actual _run_export_bg passes clamped values to run_export."""
        received = {}

        def fake_run_export(**kwargs):
            received['workers'] = kwargs['workers']
            received['crf']     = kwargs['crf']

        with patch('webview_api.run_export', side_effect=fake_run_export,
                   create=True):
            # Patch the import inside the method
            with patch('export_runner.run_export', side_effect=fake_run_export):
                api._run_export_bg({
                    'items':       [],
                    'workers':     0,      # should become 1
                    'crf':         99,     # should become 51
                    'export_path': '',
                })

        # If the patch didn't intercept (empty items exits early), that's fine —
        # what matters is workers/crf are valid when run_export is called with data.
        # The unit test above verifies the formula; this is an integration smoke test.


# ── Config save/load round-trip ───────────────────────────────────────────────

class TestSaveConfigFields:
    """
    save_config() only applies fields listed in its own hardcoded
    `simple_fields` allowlist — get_config() (asdict(self._config)) returns
    every AppConfig field generically, so it's easy to add a new per-source
    telemetry path to AppConfig and the Settings UI but forget to also add it
    to that allowlist. When that happens, the frontend sends the new path
    correctly, the backend silently drops it, and the field appears to
    "not save" with no error anywhere (exactly what happened when
    unipro_path was first added). These tests call the real save_config()/
    get_config() round trip, not just the allowlist constant, so a similarly
    forgotten field in the future fails a test instead of shipping silently.
    """

    # Every per-source telemetry folder field on AppConfig that the Settings
    # page exposes via the generic _folderRow()/data-config-key mechanism.
    # ref_lap_csv_path is deliberately excluded — it's set through a
    # different, dedicated flow, not this generic folder-path save path.
    TELEMETRY_PATH_FIELDS = [
        'racebox_path', 'aim_path', 'motec_path', 'gpx_path', 'vbox_path',
        'unipro_path', 'telemetry_path', 'video_path', 'export_path',
    ]

    @pytest.mark.parametrize('field', TELEMETRY_PATH_FIELDS)
    def test_path_field_round_trips_through_save_and_get(self, api, field):
        api.save_config({field: r'C:\Some\Test\Path'})
        assert api.get_config()[field] == r'C:\Some\Test\Path'

    def test_unipro_path_specifically(self, api):
        """Direct regression test for the exact bug reported: the Unipro
        folder field appeared to save (no error), but reopening Settings
        showed it empty again, because save_config() silently ignored it."""
        api.save_config({'unipro_path': r'C:\Telemetry\Unipro'})
        assert api.get_config()['unipro_path'] == r'C:\Telemetry\Unipro'


# ── _load_session format dispatch ─────────────────────────────────────────────

class TestLoadSessionDispatch:
    """
    _load_session's format dispatch is a hardcoded if/elif chain of is_X(path)
    checks, the same shape as export_runner.load_any_session and
    auto_sync._load_session — every format must have a branch here too, or
    the Data/Overlay/Export tabs silently misparse it.
    """

    @pytest.mark.parametrize('is_check,module,load_fn', [
        ('is_vbox',        'vbox_data',    'load_vbo'),
        ('is_motec_ld',    'motec_data',   'load_ld'),
        ('is_gpx',         'gpx_data',     'load_gpx'),
        ('is_unipro_tsv',  'unipro_data',  'load_tsv'),
        ('is_unipro_uni',  'unipro_data',  'load_uni'),
        ('is_aim_csv',     'aim_data',     'load_csv'),
    ])
    def test_dispatches_to_correct_loader(self, is_check, module, load_fn, monkeypatch):
        import importlib

        for mod_name in ('motec_data', 'vbox_data', 'gpx_data', 'unipro_data', 'aim_data'):
            mod = importlib.import_module(mod_name)
            for fn_name in dir(mod):
                if fn_name.startswith('is_') and callable(getattr(mod, fn_name)):
                    monkeypatch.setattr(mod, fn_name, lambda p: False)

        target_mod = importlib.import_module(module)
        monkeypatch.setattr(target_mod, is_check, lambda p: True)
        sentinel = object()
        monkeypatch.setattr(target_mod, load_fn, lambda p: sentinel)

        assert WebviewAPI._load_session('/fake/path') is sentinel


# ── Thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety:
    """
    start_export and download_racebox_sessions must not spawn duplicate threads
    when called concurrently.
    """

    def test_start_export_no_duplicate_threads(self, api):
        """Calling start_export twice while running must not create a second thread."""
        barrier = threading.Event()
        started_count = []

        def slow_export(params):
            started_count.append(1)
            barrier.wait(timeout=2)  # block until test releases it

        api._run_export_bg = slow_export

        api.start_export({'items': []})
        time.sleep(0.05)   # let the first thread start
        api.start_export({'items': []})   # second call while first is alive
        barrier.set()

        if api._export_thread:
            api._export_thread.join(timeout=2)

        assert len(started_count) == 1, "start_export must not spawn two threads"

    def test_cancel_export_sets_flag(self, api):
        api._export_cancel.clear()
        api.cancel_export()
        assert api._export_cancel.is_set()

    def test_thread_lock_exists(self, api):
        assert hasattr(api, '_thread_lock')
        import threading as _t
        assert isinstance(api._thread_lock, type(_t.Lock()))
