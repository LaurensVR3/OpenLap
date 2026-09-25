"""
session_loader.py — The one place a telemetry file becomes a Session.

Format dispatch used to be written out three times (webview_api, export_runner,
auto_sync) and the copies drifted: export never merged a secondary telemetry
source, so gauges bound to a secondary channel previewed fine and exported
blank; auto_sync dispatched on a source *string* and raised for any source it
had not been taught. Everything now goes through here.

    load_file(path)                      one file, format detected from content
    load_merged(path, secondary, offset) + optional secondary-source merge
    SessionCache                         memoised load_merged for the UI
"""
from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)


def load_file(path: str):
    """Load a single telemetry file, detecting its format from content.

    Raw AIM XRK paths resolve to their converted CSV first; anything no
    specific loader claims falls through to the RaceBox CSV parser.
    """
    import gpx_data, aim_data, racebox_data, motec_data, vbox_data, unipro_data
    from session_scanner import resolve_xrk_csv
    path = resolve_xrk_csv(path)
    if vbox_data.is_vbox(path):
        return vbox_data.load_vbo(path)
    if motec_data.is_motec_ld(path):
        return motec_data.load_ld(path)
    if gpx_data.is_gpx(path):
        return gpx_data.load_gpx(path)
    if unipro_data.is_unipro_tsv(path):
        return unipro_data.load_tsv(path)
    if unipro_data.is_unipro_uni(path):
        return unipro_data.load_uni(path)
    if aim_data.is_aim_csv(path):
        return aim_data.load_csv(path)
    return racebox_data.load_csv(path)


def apply_track_lines(session, line_sets) -> None:
    """Cut *session*'s laps at the user's start/finish line and attach their
    sector lines, when the session runs through one of *line_sets*
    (AppConfig.track_lines). A user line overrides the logger's own laps:
    setting it is an explicit choice of where laps start."""
    if not line_sets or not session.all_points:
        return
    import lap_detection
    from data_model import build_laps
    chosen = lap_detection.pick_line_set(session.all_points, line_sets)
    if chosen is None:
        return
    cr = lap_detection.crossings(session.all_points, lap_detection.FinishLine.from_dict(chosen['finish']))
    starts = lap_detection.assign_laps(session.all_points, cr)
    laps = build_laps(session.all_points, boundaries=starts, refine=False)
    if len(laps) > 1:
        laps[-1].is_inlap = True     # never reaches the line again
    session.laps = laps
    session.finish_line = dict(chosen['finish'])
    session.sector_lines = [dict(s) for s in chosen.get('sectors') or []]
    timed = session.timed_laps
    if timed:
        session.best_lap_time = min(l.duration for l in timed)


def load_merged(path: str, secondary_path: Optional[str] = None,
                secondary_offset: float = 0.0, loader=None, track_lines=None):
    """Load *path*, merging in *secondary_path* when one is assigned and exists.

    A secondary file that fails to load is logged and skipped rather than
    failing the primary: the session is still usable without it.
    """
    loader = loader or load_file
    primary = loader(path)
    session = primary
    if secondary_path and os.path.isfile(secondary_path):
        try:
            secondary = loader(secondary_path)
        except Exception:
            logger.exception('Failed to load secondary telemetry %s for %s', secondary_path, path)
        else:
            from session_merge import merge_sessions
            session = merge_sessions(primary, secondary, secondary_offset or 0.0)
    try:
        apply_track_lines(session, track_lines)
    except Exception:
        logger.exception('Could not apply track lines to %s', path)
    return session


def _stat_key(path: Optional[str]):
    if not path:
        return None
    try:
        st = os.stat(path)
        return (path, st.st_size, st.st_mtime_ns)
    except OSError:
        return (path, None, None)


class SessionCache:
    """Small LRU of merged sessions, for the UI's read-only endpoints.

    Opening one session in the editor used to parse the file four times
    (meta, laps, channels, first lap) and again on every lap switch, plus a
    full re-merge when a secondary file was attached. Entries are keyed on
    both files' size and mtime and the merge offset, so an edited file or a
    changed offset is a miss, never a stale hit. Concurrent requests for the
    same session wait for one load instead of each parsing the file.

    Returned sessions are shared: callers must not mutate them. Export
    mutates sessions (bike overrides, derived lean angles), so it loads
    fresh through load_merged() instead.
    """

    def __init__(self, max_entries: int = 4):
        self._max = max_entries
        self._entries: 'OrderedDict[tuple, object]' = OrderedDict()
        self._inflight: dict = {}
        self._lock = threading.Lock()

    def get(self, path: str, secondary_path: Optional[str] = None,
            secondary_offset: float = 0.0, loader=None, track_lines=None):
        import json as _json
        key = (_stat_key(path), _stat_key(secondary_path) if secondary_path else None,
               float(secondary_offset or 0.0),
               _json.dumps(track_lines or [], sort_keys=True))
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
            waiter = self._inflight.get(key)
            if waiter is None:
                waiter = self._inflight[key] = {'event': threading.Event()}
                owner = True
            else:
                owner = False

        if not owner:
            waiter['event'].wait()
            if 'error' in waiter:
                raise waiter['error']
            return waiter['session']

        try:
            session = load_merged(path, secondary_path, secondary_offset, loader=loader,
                                  track_lines=track_lines)
        except BaseException as e:
            waiter['error'] = e
            with self._lock:
                self._inflight.pop(key, None)
            waiter['event'].set()
            raise

        waiter['session'] = session
        with self._lock:
            self._inflight.pop(key, None)
            self._entries[key] = session
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        waiter['event'].set()
        return session

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
