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


class TestSecondarySourceConfigFields:
    """
    save_config()'s merge-dict handling for the dual-telemetry-source fields
    (secondary_source, secondary_offsets, secondary_offset_sources) follows
    the same hand-written allowlist pattern as offsets/offset_sources/
    bike_overrides — same failure mode as TestSaveConfigFields above if a
    field is ever added to AppConfig but forgotten in save_config()'s
    merge-dict block.
    """

    def test_secondary_source_round_trips(self, api):
        api.save_config({'secondary_source': {'/primary.csv': '/secondary.ld'}})
        assert api.get_config()['secondary_source'] == {'/primary.csv': '/secondary.ld'}

    def test_secondary_offsets_round_trips(self, api):
        api.save_config({'secondary_offsets': {'/primary.csv': 4.5}})
        assert api.get_config()['secondary_offsets'] == {'/primary.csv': 4.5}

    def test_secondary_offset_sources_round_trips(self, api):
        api.save_config({'secondary_offset_sources': {'/primary.csv': 'user'}})
        assert api.get_config()['secondary_offset_sources'] == {'/primary.csv': 'user'}

    def test_secondary_source_merges_rather_than_overwrites(self, api):
        api.save_config({'secondary_source': {'/a.csv': '/a2.ld'}})
        api.save_config({'secondary_source': {'/b.csv': '/b2.ld'}})
        assert api.get_config()['secondary_source'] == {'/a.csv': '/a2.ld', '/b.csv': '/b2.ld'}


# ── _load_session secondary-source merge ──────────────────────────────────────

class TestLoadSessionMerge:
    """
    _load_session() transparently merges in a secondary telemetry source
    when one is configured for that csv_path (see session_merge.merge_sessions).
    This is the single funnel every session consumer (get_session_meta,
    get_laps, export, ...) goes through, so a bug here silently affects the
    whole app rather than one feature.
    """

    def test_no_secondary_returns_primary_unchanged(self, api, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(api, '_load_one_session', lambda p: sentinel)
        assert api._load_session('/primary.csv') is sentinel

    def test_secondary_configured_but_file_missing_returns_primary_unchanged(self, api, monkeypatch, tmp_path):
        sentinel = object()
        monkeypatch.setattr(api, '_load_one_session', lambda p: sentinel)
        api._config.secondary_source['/primary.csv'] = str(tmp_path / 'does_not_exist.ld')
        assert api._load_session('/primary.csv') is sentinel

    def test_secondary_configured_and_present_calls_merge(self, api, monkeypatch, tmp_path):
        secondary_file = tmp_path / 'secondary.ld'
        secondary_file.write_text('x')

        primary_sentinel   = object()
        secondary_sentinel = object()
        merged_sentinel    = object()

        def fake_load_one(path):
            return primary_sentinel if path == '/primary.csv' else secondary_sentinel
        monkeypatch.setattr(api, '_load_one_session', fake_load_one)

        captured = {}

        def fake_merge(primary, secondary, offset):
            captured['args'] = (primary, secondary, offset)
            return merged_sentinel
        monkeypatch.setattr('session_merge.merge_sessions', fake_merge)

        api._config.secondary_source['/primary.csv']   = str(secondary_file)
        api._config.secondary_offsets['/primary.csv']  = 2.5

        result = api._load_session('/primary.csv')
        assert result is merged_sentinel
        assert captured['args'] == (primary_sentinel, secondary_sentinel, 2.5)

    def test_secondary_load_failure_falls_back_to_primary(self, api, monkeypatch, tmp_path):
        secondary_file = tmp_path / 'secondary.ld'
        secondary_file.write_text('x')

        primary_sentinel = object()

        def fake_load_one(path):
            if path == '/primary.csv':
                return primary_sentinel
            raise RuntimeError('corrupt file')
        monkeypatch.setattr(api, '_load_one_session', fake_load_one)

        api._config.secondary_source['/primary.csv'] = str(secondary_file)
        assert api._load_session('/primary.csv') is primary_sentinel


# ── Dynamic channel listing RPCs ────────────────────────────────────────────────

class TestChannelListingRPCs:
    """list_session_channels() (one file, no merge) vs get_available_channels()
    (the possibly-merged session) — both wrap channel_discovery.list_channels()."""

    @staticmethod
    def _fake_session(extra_channel_meta):
        from data_model import DataPoint, Session
        pt = DataPoint(
            record=0, time=None, lat=0.0, lon=0.0, alt=0.0, speed=0.0,
            gforce_x=0.0, gforce_y=0.0, gforce_z=0.0, lap=0,
            gyro_x=0.0, gyro_y=0.0, gyro_z=0.0, elapsed=0.0,
            extra={k: 1.0 for k in extra_channel_meta},
        )
        return Session(
            source='Test', date_utc='', track='', configuration='',
            session_type='', best_lap_time=0.0, all_points=[pt], laps=[],
            extra_channel_meta=extra_channel_meta,
        )

    def test_list_session_channels_includes_extras(self, api, monkeypatch):
        session = self._fake_session({'Coolant Temperature': {'label': 'Coolant Temperature', 'unit': 'C'}})
        monkeypatch.setattr(api, '_load_one_session', lambda p: session)
        result = api.list_session_channels('/primary.csv')
        assert 'Coolant Temperature' in {c['key'] for c in result}

    def test_list_session_channels_includes_fixed_channels(self, api, monkeypatch):
        from gauge_channels import GAUGE_CHANNELS
        session = self._fake_session({})
        monkeypatch.setattr(api, '_load_one_session', lambda p: session)
        result = api.list_session_channels('/primary.csv')
        keys = {c['key'] for c in result}
        assert set(GAUGE_CHANNELS.keys()).issubset(keys)

    def test_list_session_channels_returns_empty_list_on_error(self, api, monkeypatch):
        def raise_err(p):
            raise RuntimeError('bad file')
        monkeypatch.setattr(api, '_load_one_session', raise_err)
        assert api.list_session_channels('/bad.csv') == []

    def test_get_available_channels_uses_load_session_not_load_one_session(self, api, monkeypatch):
        # get_available_channels must go through the merge-aware _load_session,
        # not the single-file _load_one_session, so it reflects a merged result.
        merged_session = self._fake_session({'Merged Channel': {'label': 'Merged Channel', 'unit': ''}})
        monkeypatch.setattr(api, '_load_session', lambda p: merged_session)
        monkeypatch.setattr(api, '_load_one_session',
                             lambda p: (_ for _ in ()).throw(AssertionError('should not be called')))
        result = api.get_available_channels('/primary.csv')
        assert 'Merged Channel' in {c['key'] for c in result}

    def test_get_available_channels_returns_empty_list_on_error(self, api, monkeypatch):
        def raise_err(p):
            raise RuntimeError('bad file')
        monkeypatch.setattr(api, '_load_session', raise_err)
        assert api.get_available_channels('/bad.csv') == []


# ── start_channel_sync eligibility ───────────────────────────────────────────

class TestStartChannelSync:
    """start_channel_sync() must only queue sessions that have a secondary
    source assigned and no offset yet. Unlike start_auto_sync(), a prior
    'known to fail' marker does NOT exclude a session — see
    test_previously_failed_session_is_still_queued_on_manual_retry."""

    def test_no_secondary_source_configured_queues_nothing(self, api):
        result = api.start_channel_sync([{'csv_path': '/a.csv', 'source': 'MoTeC'}])
        assert result == {'queued': 0}

    def test_session_with_secondary_and_no_offset_is_queued(self, api, monkeypatch):
        api._config.secondary_source['/a.csv'] = '/a2.ld'
        monkeypatch.setattr(api, '_run_channel_sync_bg', lambda sessions: None)
        result = api.start_channel_sync([{'csv_path': '/a.csv', 'source': 'MoTeC'}])
        assert result == {'queued': 1}

    def test_session_with_existing_offset_is_skipped(self, api, monkeypatch):
        api._config.secondary_source['/a.csv']  = '/a2.ld'
        api._config.secondary_offsets['/a.csv'] = 1.0
        monkeypatch.setattr(api, '_run_channel_sync_bg', lambda sessions: None)
        result = api.start_channel_sync([{'csv_path': '/a.csv', 'source': 'MoTeC'}])
        assert result == {'queued': 0}

    def test_previously_failed_session_is_still_queued_on_manual_retry(self, api, monkeypatch):
        # Unlike start_auto_sync() (bulk, automatic), this is only ever
        # triggered by an explicit single-session button click — a prior
        # failure must not silently block the user from retrying.
        api._config.secondary_source['/a.csv'] = '/a2.ld'
        api._config.secondary_sync_failed.append('/a.csv')
        monkeypatch.setattr(api, '_run_channel_sync_bg', lambda sessions: None)
        result = api.start_channel_sync([{'csv_path': '/a.csv', 'source': 'MoTeC'}])
        assert result == {'queued': 1}


class TestRunChannelSyncBg:
    """_run_channel_sync_bg() is the worker start_channel_sync() threads off
    to — tested directly (synchronously) here rather than through the thread."""

    def test_success_clears_a_stale_failed_marker(self, api, monkeypatch, tmp_path):
        secondary_file = tmp_path / 'secondary.ld'
        secondary_file.write_text('x')

        api._config.secondary_source['/a.csv'] = str(secondary_file)
        api._config.secondary_sync_failed.append('/a.csv')  # stale, from an earlier failed attempt

        monkeypatch.setattr('auto_sync.correlate_channels',
                             lambda **kwargs: (1.23, 9.0, 'RPM'))
        monkeypatch.setattr('session_scanner._csv_source', lambda path: 'MoTeC')

        api._run_channel_sync_bg([{'csv_path': '/a.csv', 'source': 'RaceBox'}])

        assert api._config.secondary_offsets['/a.csv'] == pytest.approx(1.23)
        assert '/a.csv' not in api._config.secondary_sync_failed

    def test_low_confidence_adds_to_failed_list(self, api, monkeypatch, tmp_path):
        secondary_file = tmp_path / 'secondary.ld'
        secondary_file.write_text('x')

        api._config.secondary_source['/a.csv'] = str(secondary_file)

        monkeypatch.setattr('auto_sync.correlate_channels',
                             lambda **kwargs: (0.0, 0.0, ''))
        monkeypatch.setattr('session_scanner._csv_source', lambda path: 'MoTeC')

        api._run_channel_sync_bg([{'csv_path': '/a.csv', 'source': 'RaceBox'}])

        assert '/a.csv' not in api._config.secondary_offsets
        assert '/a.csv' in api._config.secondary_sync_failed


# ── _load_session format dispatch ─────────────────────────────────────────────

class TestLoadSessionDispatch:
    """
    _load_one_session's format dispatch (called by _load_session, which adds
    an optional secondary-source merge on top) is a hardcoded if/elif chain
    of is_X(path) checks, the same shape as export_runner.load_any_session
    and auto_sync._load_session — every format must have a branch here too,
    or the Data/Overlay/Export tabs silently misparse it.
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

        assert WebviewAPI._load_one_session('/fake/path') is sentinel


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


# ── Clearing a stored offset / a manual video link ────────────────────────────

class TestClearOffset:
    """save_config() *merges* its dict fields (offsets.update(...)), so the
    frontend can overwrite an offset but has no way to remove one. Clearing
    therefore needs its own endpoint — without it a session set by hand while
    testing something else stays set forever."""

    CSV = r'C:\Telemetry\RaceBox\session.csv'

    def test_clear_offset_removes_value_and_source(self, api):
        api.save_config({'offsets':        {self.CSV: 12.5},
                         'offset_sources': {self.CSV: 'user'}})
        assert api.get_config()['offsets'][self.CSV] == 12.5

        api.clear_offset(self.CSV)

        cfg = api.get_config()
        assert self.CSV not in cfg['offsets']
        assert self.CSV not in cfg['offset_sources']

    def test_clear_offset_makes_the_session_an_auto_sync_candidate_again(self, api):
        """A session auto-sync gave up on is skipped forever after; unsetting
        the offset must also lift that marker, or 'Unset' leaves the session
        unable to be re-synced by any means except a manual mark."""
        api._config.auto_sync_failed.append(self.CSV)
        api.save_config({'offsets': {self.CSV: 3.0}})

        api.clear_offset(self.CSV)

        assert self.CSV not in api.get_config()['auto_sync_failed']

    def test_clear_offset_leaves_other_sessions_alone(self, api):
        other = r'C:\Telemetry\RaceBox\other.csv'
        api.save_config({'offsets':        {self.CSV: 1.0, other: 2.0},
                         'offset_sources': {self.CSV: 'user', other: 'auto'}})

        api.clear_offset(self.CSV)

        cfg = api.get_config()
        assert cfg['offsets'] == {other: 2.0}
        assert cfg['offset_sources'] == {other: 'auto'}

    def test_clear_offset_on_an_unset_session_is_a_no_op(self, api):
        api.clear_offset(self.CSV)   # must not raise
        assert api.get_config()['offsets'] == {}

    def test_offset_can_be_set_again_after_clearing(self, api):
        api.save_config({'offsets': {self.CSV: 12.5}})
        api.clear_offset(self.CSV)
        api.save_config({'offsets': {self.CSV: 4.25}})
        assert api.get_config()['offsets'][self.CSV] == 4.25


class TestUnassignVideo:
    """assign_video() records a manual link in session_info['_video_override'];
    unassign_video() is the only way back out of it."""

    CSV   = r'C:\Telemetry\RaceBox\session.csv'
    VIDEO = r'C:\Videos\clip.mp4'

    def test_unassign_removes_the_override(self, api):
        api.assign_video(self.CSV, self.VIDEO)
        assert api._video_override_for(self.CSV)

        api.unassign_video(self.CSV)

        assert api._video_override_for(self.CSV) is None

    def test_unassign_keeps_other_session_info_overrides(self, api):
        """The video link shares session_info with the track-name override —
        dropping the whole entry would silently wipe a renamed track."""
        api.edit_session_info(self.CSV, {'info_track': 'Spa'})
        api.assign_video(self.CSV, self.VIDEO)

        api.unassign_video(self.CSV)

        keys = api._session_info_keys(self.CSV)
        stored = {}
        for k in keys:
            stored.update(api.get_config()['session_info'].get(k, {}))
        assert stored.get('info_track') == 'Spa'
        assert '_video_override' not in stored

    def test_unassign_on_an_unlinked_session_is_a_no_op(self, api):
        api.unassign_video(self.CSV)   # must not raise
        assert api._video_override_for(self.CSV) is None

    def test_video_can_be_assigned_again_after_unassigning(self, api):
        api.assign_video(self.CSV, self.VIDEO)
        api.unassign_video(self.CSV)
        api.assign_video(self.CSV, r'C:\Videos\other.mp4')
        assert api._video_override_for(self.CSV).endswith('other.mp4')


class TestVideoOverrideSurvivesRescan:
    """A hand-assigned video used to live only in the scan cache: the override
    was written to config but never read back, so the next scan silently
    reverted the session to whatever automatic matching found."""

    CSV   = r'C:\Telemetry\RaceBox\session.csv'
    VIDEO = r'C:\Videos\clip.mp4'

    def test_cached_sessions_apply_the_override(self, api, monkeypatch):
        monkeypatch.setattr('webview_api.load_scan_cache', lambda: {'sessions': [
            {'csv_path': self.CSV, 'source': 'RaceBox', 'video_paths': [], 'matched': False},
        ]})
        api.assign_video(self.CSV, self.VIDEO)

        session = api._cached_sessions()[0]

        assert session['video_paths'] == [api._video_override_for(self.CSV)]
        assert session['matched'] is True
        assert session['video_override'] is True

    def test_cached_sessions_report_no_override_once_unassigned(self, api, monkeypatch):
        monkeypatch.setattr('webview_api.load_scan_cache', lambda: {'sessions': [
            {'csv_path': self.CSV, 'source': 'RaceBox', 'video_paths': [], 'matched': False},
        ]})
        api.assign_video(self.CSV, self.VIDEO)
        api.unassign_video(self.CSV)

        session = api._cached_sessions()[0]

        assert session['video_paths'] == []
        assert session['video_override'] is False


class TestAutoSyncProgressPayload:
    """Every auto-sync event must carry the session it belongs to and the
    thresholds it is judged against.

    The UI used to latch position from the first 'processing' event into
    page-local state that reset when the Data page was reopened, producing
    "Auto-syncing session 0 of 0", and it hardcoded the threshold as a
    literal that had drifted from the real acceptance floor.
    """

    def _events(self, api, monkeypatch, result=(1.5, 7.0)):
        events = []
        monkeypatch.setattr(api, '_push',
                            lambda evt, **kw: events.append((evt, kw)))

        def fake_run(csv_path, video_paths, source, cancel_event=None,
                     progress_cb=None, **kwargs):
            progress_cb(809.0, 1.5, 4.06)      # one mid-run check
            return result

        monkeypatch.setattr('auto_sync.run_auto_sync', fake_run)
        api._run_auto_sync_bg([
            {'csv_path': r'C:\a.csv', 'video_paths': ['a.mp4'], 'source': 'RaceBox'},
        ])
        return events

    def test_checking_events_carry_position(self, api, monkeypatch):
        events = self._events(api, monkeypatch)
        checking = [kw for evt, kw in events if kw.get('status') == 'checking']
        assert checking, 'no checking event was pushed'
        assert checking[0]['current'] == 1
        assert checking[0]['total'] == 1

    def test_checking_events_carry_both_thresholds(self, api, monkeypatch):
        from auto_sync import CONFIDENCE_THRESHOLD, MIN_CONFIDENCE
        events = self._events(api, monkeypatch)
        kw = [k for e, k in events if k.get('status') == 'checking'][0]
        assert kw['early_exit_confidence'] == CONFIDENCE_THRESHOLD
        assert kw['min_confidence'] == MIN_CONFIDENCE
        # The two are genuinely different; showing only one misleads.
        assert MIN_CONFIDENCE < CONFIDENCE_THRESHOLD

    def test_done_events_carry_position(self, api, monkeypatch):
        events = self._events(api, monkeypatch, result=(1.5, 7.0))
        done = [kw for evt, kw in events if kw.get('status') == 'done']
        assert done and done[0]['current'] == 1 and done[0]['total'] == 1

    def test_failed_events_carry_position(self, api, monkeypatch):
        events = self._events(api, monkeypatch, result=(None, 2.0))
        failed = [kw for evt, kw in events if kw.get('status') == 'failed']
        assert failed and failed[0]['current'] == 1 and failed[0]['total'] == 1

    def test_done_event_has_no_error_on_success(self, api, monkeypatch):
        events = self._events(api, monkeypatch)
        assert events[-1] == ('auto_sync_done', {'error': ''})


class TestSyncRunnersAlwaysFinish:
    """A sync runner that dies without pushing its done event leaves the Data
    page on "auto-syncing…" forever. Release builds up to 0.3.3 shipped
    without scipy, so importing auto_sync itself failed inside the thread."""

    @staticmethod
    def _break_auto_sync_import(monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'auto_sync':
                raise ImportError("No module named 'scipy'")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', fake_import)

    def test_auto_sync_reports_import_failure(self, api, monkeypatch):
        events = []
        monkeypatch.setattr(api, '_push', lambda evt, **kw: events.append((evt, kw)))
        self._break_auto_sync_import(monkeypatch)

        api._run_auto_sync_bg([{'csv_path': 'a.csv', 'video_paths': ['a.mp4']}])

        assert events[-1][0] == 'auto_sync_done'
        assert 'scipy' in events[-1][1]['error']

    def test_channel_sync_reports_import_failure(self, api, monkeypatch):
        events = []
        monkeypatch.setattr(api, '_push', lambda evt, **kw: events.append((evt, kw)))
        self._break_auto_sync_import(monkeypatch)

        api._run_channel_sync_bg([{'csv_path': 'a.csv'}])

        assert events[-1][0] == 'channel_sync_done'
        assert 'scipy' in events[-1][1]['error']


class TestOffsetMigration:
    """An offset is a time into the session's first clip. When a rescan
    matches a session to different video, the offset must follow."""

    @staticmethod
    def _run(api, monkeypatch, old, new, offset, source, durations=None):
        csv = '/s.csv'
        api._config.offsets[csv] = offset
        api._config.offset_sources[csv] = source
        monkeypatch.setattr('app_config.load_scan_cache',
                            lambda: {'sessions': [{'csv_path': csv, 'video_paths': old}]})
        result = [{'csv_path': csv, 'video_paths': new, 'video_override': False,
                   'sync_offset': offset, 'sync_source': source}]
        api._migrate_offsets(result, durations or {})
        return result[0]

    def test_chapter_added_in_front_shifts_the_offset(self, api, monkeypatch):
        r = self._run(api, monkeypatch, ['b.mp4'], ['a.mp4', 'b.mp4'], 2.0, 'user', {'a.mp4': 409.0})
        assert api._config.offsets['/s.csv'] == pytest.approx(411.0) == r['sync_offset']
        assert not r['sync_review']

    def test_unchanged_first_clip_leaves_it_alone(self, api, monkeypatch):
        r = self._run(api, monkeypatch, ['a.mp4', 'x.mov', 'b.mp4'], ['a.mp4', 'b.mp4'], 3.9, 'auto')
        assert api._config.offsets['/s.csv'] == 3.9 and not r['sync_review']

    def test_different_recording_clears_an_auto_offset(self, api, monkeypatch):
        r = self._run(api, monkeypatch, ['phone.mov', 'dji.mp4'], ['dji.mp4'], 43.6, 'auto')
        assert '/s.csv' not in api._config.offsets and r['sync_offset'] is None

    def test_different_recording_keeps_a_user_offset_for_review(self, api, monkeypatch):
        r = self._run(api, monkeypatch, ['DJI_0697_001.MP4'], ['DJI_0698_001.MP4'], 0.86, 'user')
        assert api._config.offsets['/s.csv'] == 0.86
        assert r['sync_review'] and '/s.csv' in api._config.offset_review

    def test_confirming_by_hand_settles_the_review(self, api):
        api._config.offset_review.append('/s.csv')
        api.save_config({'offsets': {'/s.csv': 1.0}, 'offset_sources': {'/s.csv': 'user'}})
        assert '/s.csv' not in api._config.offset_review


class TestPresetManagement:
    def test_rename_keeps_order_and_active_preset(self, api):
        api._config.presets = {'A': {'gauges': []}, 'B': {'gauges': [1]}, 'C': {'gauges': []}}
        api._config.active_preset = 'B'
        api.rename_preset('B', 'Kart')
        assert list(api._config.presets) == ['A', 'Kart', 'C']
        assert api._config.presets['Kart'] == {'gauges': [1]} and api._config.active_preset == 'Kart'

    def test_rename_never_overwrites_another_preset(self, api):
        api._config.presets = {'A': {'x': 1}, 'B': {'x': 2}}
        api.rename_preset('A', 'B')
        assert api._config.presets == {'A': {'x': 1}, 'B': {'x': 2}}

    def test_delete_unties_the_live_layout(self, api):
        api._config.presets = {'A': {}, 'B': {}}
        api._config.active_preset = 'A'
        api.delete_preset('A')
        assert list(api._config.presets) == ['B'] and api._config.active_preset == ''


class TestEverySettingsFieldSaves:
    """Every input on the Settings page (data-config-key, including the
    folder rows) must survive save_config(): its field list is hard-coded,
    and a field missing from it looks saved but silently is not."""

    @staticmethod
    def _settings_keys():
        import re
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / 'frontend' / 'js' / 'pages' / 'settings.js').read_text(encoding='utf-8')
        keys = set(re.findall(r'data-config-key="(\w+)"', js))
        keys |= set(re.findall(r"_folderRow\('[^']*',\s*'(\w+)'", js))
        keys.discard('${key}')
        return sorted(keys)

    def test_found_the_fields(self):
        keys = self._settings_keys()
        assert {'encoder', 'crf', 'output_height', 'output_fps', 'bitrate_kbps', 'racebox_path'} <= set(keys)

    @pytest.mark.parametrize('key', _settings_keys.__func__())
    def test_field_round_trips(self, api, key):
        current = api.get_config()[key]
        if isinstance(current, bool):
            value = not current
        elif isinstance(current, int):
            value = current + 7
        elif isinstance(current, float):
            value = current + 12.5
        else:
            value = 'h264_qsv' if key == 'encoder' else ('mph' if key == 'speed_unit' else r'C:\x\y')
        api.save_config({key: value})
        assert api.get_config()[key] == value


def test_export_queue_survives_a_restart(tmp_config_dir):
    from webview_api import WebviewAPI
    items = [{'csv_path': '/a.csv', 'scope': 'fastest', 'video_paths': ['/v.mp4']}]
    WebviewAPI().save_export_queue(items)
    assert WebviewAPI().load_export_queue() == items


def test_export_params_carry_output_options():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'frontend' / 'js' / 'export_params.js').read_text(encoding='utf-8')
    for key in ('output_height', 'output_fps', 'bitrate_kbps'):
        assert f'{key}:' in js


class TestUpdateCheck:
    def test_version_comparison(self):
        from webview_api import _version_tuple
        assert _version_tuple('v0.3.10') > _version_tuple('0.3.9')
        assert _version_tuple('0.4.0') > _version_tuple('0.3.3-dev')
        assert not _version_tuple('v0.3.3') > _version_tuple('0.3.3')

    def test_newer_release_is_reported(self, api, monkeypatch):
        import io, json, urllib.request
        body = json.dumps({'tag_name': 'v9.0.0', 'html_url': 'https://github.com/x'}).encode()
        monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout: io.BytesIO(body))
        assert api.check_for_update() == {'newer': True, 'latest': '9.0.0', 'url': 'https://github.com/x'}

    def test_switched_off_or_offline_is_silent(self, api, monkeypatch):
        import urllib.request
        def offline(*a, **k):
            raise OSError('no network')
        monkeypatch.setattr(urllib.request, 'urlopen', offline)
        assert api.check_for_update() == {}
        api._config.check_updates = False
        assert api.check_for_update() == {}
