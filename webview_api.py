"""
webview_api.py — Python API exposed to JavaScript via window.pywebview.api.

All public methods are called by JS with await window.pywebview.api.method(args).
Return values must be JSON-serialisable.
Push-events (export progress, scan updates) are sent via window.evaluate_js().
"""
from __future__ import annotations

import concurrent.futures
import http.server
import logging
import mimetypes
import os
import re
import socketserver
import threading
import urllib.parse
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import webview

from app_config import AppConfig, overlay_from_dict, load_scan_cache

logger = logging.getLogger(__name__)

_ALLOWED_VIDEO_EXTENSIONS = frozenset({
    '.mp4', '.mov', '.avi', '.mkv', '.m4v',
    '.MP4', '.MOV', '.AVI', '.MKV', '.M4V',
})

# OSM way ids surfaced by track_map_cache.fetch_candidates() are always plain
# (positive) integers — the Overpass query in that module only ever queries
# `way(...)`, never `relation(...)`, for the candidate list. A leading '-' is
# allowed anyway purely as defense in depth (some OSM tooling mints negative
# synthetic ids for relations) even though this codebase never produces one.
_VALID_OSM_ID_RE = re.compile(r'^-?\d+$')

AUTO_SYNC_WORKERS = 2   # concurrent ffmpeg decodes — kept modest, CPU-heavy work

# Paths this running app instance has itself resolved via session-scanning,
# manual video assignment, or camera-folder linking. The video server only
# ever serves a path that both (a) matches the extension whitelist and
# (b) appears in this set — so an arbitrary cross-origin fetch() from some
# unrelated site open in the user's regular browser can't use the local
# video-server port as a generic "read any file the attacker can name" oracle;
# it can only read files OpenLap itself already discovered/linked.
_known_video_paths: set = set()
_known_video_paths_lock = threading.Lock()


def _norm_video_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _register_known_video_path(path: str) -> None:
    if not path:
        return
    try:
        norm = _norm_video_path(path)
    except Exception:
        return
    with _known_video_paths_lock:
        _known_video_paths.add(norm)


def _is_known_video_path(path: str) -> bool:
    try:
        norm = _norm_video_path(path)
    except Exception:
        return False
    with _known_video_paths_lock:
        return norm in _known_video_paths


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTPServer that handles each connection on its own thread.

    Browsers routinely open several overlapping range requests while
    buffering/seeking; the plain single-threaded HTTPServer serializes them,
    which can stall playback. daemon_threads=True so these never block
    process exit.
    """
    daemon_threads = True


class _VideoFileHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves arbitrary local files with range support.

    The URL path is the absolute file path with forward slashes, e.g.
    /C:/Videos/race.mp4  → opens C:/Videos/race.mp4 on Windows.
    """

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if 'f' in params:
            # Path delivered as ?f=<url-encoded Windows path> — no slash mangling
            raw = params['f'][0]
        else:
            # Legacy fallback: path embedded in URL path (only works for local C:/ paths)
            raw = urllib.parse.unquote(parsed.path)
            if raw.startswith('/') and len(raw) > 2 and raw[2] == ':':
                raw = raw[1:]

        # Security: only serve recognised video extensions to prevent path traversal
        ext = os.path.splitext(raw)[1]
        if ext not in _ALLOWED_VIDEO_EXTENSIONS:
            logger.warning('VideoServer 403: disallowed extension %s for %s', ext, raw)
            self.send_error(403, 'Forbidden')
            return

        # Security: only serve paths the app itself has resolved (matched session
        # videos, manually-assigned videos, linked camera-folder clips) — not any
        # arbitrary path a caller can name. See _register_known_video_path().
        if not _is_known_video_path(raw):
            logger.warning('VideoServer 403: unrecognised path %s', raw)
            self.send_error(403, 'Forbidden')
            return

        logger.debug('VideoServer GET %s → %s (exists=%s)', self.path, raw, os.path.isfile(raw))
        if not os.path.isfile(raw):
            logger.warning('VideoServer 404: %s', raw)
            self.send_error(404, 'File not found')
            return
        size  = os.path.getsize(raw)
        mime  = mimetypes.guess_type(raw)[0] or 'application/octet-stream'
        rng   = self.headers.get('Range', '')
        if rng:
            try:
                spec = rng.replace('bytes=', '')
                if spec.startswith('-'):
                    # Suffix form per RFC 7233, e.g. "bytes=-500" → last 500
                    # bytes of the file (start is NOT byte offset 0 here).
                    suffix_len = int(spec[1:])
                    if suffix_len <= 0:
                        raise ValueError('non-positive suffix length')
                    start = max(0, size - suffix_len)
                    end   = size - 1
                else:
                    parts = spec.split('-')
                    start = int(parts[0]) if parts[0] else 0
                    end   = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
            except (ValueError, IndexError):
                self.send_error(400, 'Invalid Range header')
                return
            end = min(end, size - 1)
            if start < 0 or start > end or start >= size:
                self.send_error(416, 'Range Not Satisfiable')
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            start, end, length = 0, size - 1, size
            self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        # No Access-Control-Allow-Origin header: a same-origin <video src="...">
        # request to this exact 127.0.0.1:PORT origin does not need CORS headers
        # at all, and omitting it means a cross-origin fetch() from some other
        # site open in the user's browser gets an opaque response it cannot read.
        self.end_headers()
        try:
            with open(raw, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass  # suppress server logs


class WebviewAPI:
    """
    One instance of this class is created in main.py and passed to
    webview.create_window(js_api=api).  Every public method becomes
    callable from JavaScript as: await window.pywebview.api.<method>(...)
    """

    def __init__(self):
        self._config: AppConfig = AppConfig.load()
        self._window: Optional[webview.Window] = None
        self._export_cancel    = threading.Event()
        self._export_thread:   Optional[threading.Thread] = None
        self._rb_cancel        = threading.Event()
        self._rb_thread:       Optional[threading.Thread] = None
        self._auto_sync_cancel = threading.Event()
        self._auto_sync_thread: Optional[threading.Thread] = None
        self._thread_lock      = threading.Lock()
        self._file_meta_cache: Optional[dict] = None
        # RLock (not Lock): get_session_meta wraps fetch+mutate+save of the
        # meta cache in a single `with self._meta_cache_lock:` block, and that
        # block calls _get_file_meta_cache()/_save_file_meta_cache(), which
        # each acquire this same lock again — a plain Lock would deadlock.
        self._meta_cache_lock  = threading.RLock()
        self._config_lock      = threading.Lock()
        self._video_port_lock  = threading.Lock()

    # ── Called by main.py once the window is ready ────────────────────────────
    def set_window(self, window: webview.Window) -> None:
        self._window = window

    # ── Per-file metadata cache (video ffprobe, CSV sniff, session meta) ──────
    def _get_file_meta_cache(self) -> dict:
        with self._meta_cache_lock:
            if self._file_meta_cache is None:
                from app_config import load_file_meta_cache
                self._file_meta_cache = load_file_meta_cache()
            return self._file_meta_cache

    def _save_file_meta_cache(self) -> None:
        with self._meta_cache_lock:
            if self._file_meta_cache is not None:
                from app_config import save_file_meta_cache
                save_file_meta_cache(self._file_meta_cache)

    def _push(self, event_type: str, **payload) -> None:
        """Push a CustomEvent to JavaScript."""
        if self._window is None:
            return
        import json
        detail = json.dumps({'type': event_type, **payload})
        # Escape single quotes in detail for safe JS injection
        detail_escaped = detail.replace('\\', '\\\\').replace("'", "\\'")
        self._window.evaluate_js(
            f"window.dispatchEvent(new CustomEvent('openlap', {{detail: JSON.parse('{detail_escaped}')}}));"
        )

    # ── Video file server ─────────────────────────────────────────────────────
    def get_video_server_port(self) -> int:
        """Return the localhost port of the video file server, starting it if needed."""
        # Guard the lazy-init check+create with a lock — without it two
        # near-simultaneous callers can both observe "no server yet" and each
        # spin up their own HTTPServer, leaking one forever.
        with self._video_port_lock:
            if hasattr(self, '_video_port'):
                return self._video_port
            try:
                server = _ThreadingHTTPServer(('127.0.0.1', 0), _VideoFileHandler)
                self._video_port = server.server_address[1]
                t = threading.Thread(target=server.serve_forever, daemon=True)
                t.start()
                logger.info('Video file server started on port %d', self._video_port)
            except Exception:
                logger.exception('Failed to start video file server')
                self._video_port = 0
            return self._video_port

    # ── Config ────────────────────────────────────────────────────────────────
    def get_config(self) -> dict:
        cfg = asdict(self._config)
        # Inject the helper method result as a plain list
        cfg['all_telemetry_paths'] = self._config.all_telemetry_paths()
        return cfg

    def save_config(self, data: dict) -> None:
        with self._config_lock:
            # Update string fields
            simple_fields = [
                'racebox_path', 'aim_path', 'motec_path', 'gpx_path', 'vbox_path',
                'unipro_path', 'telemetry_path', 'video_path', 'export_path', 'racebox_email',
            ]
            for f in simple_fields:
                if f in data:
                    setattr(self._config, f, data[f])
            if 'encoder' in data:
                self._config.encoder = str(data['encoder'])
            if 'crf' in data:
                self._config.crf = int(data['crf'])
            if 'workers' in data:
                self._config.workers = int(data['workers'])
            if 'speed_unit' in data:
                self._config.speed_unit = str(data['speed_unit'])
            # Merge dict fields (JS may send partial updates)
            if 'offsets' in data and isinstance(data['offsets'], dict):
                self._config.offsets.update(data['offsets'])
            if 'offset_sources' in data and isinstance(data['offset_sources'], dict):
                self._config.offset_sources.update(data['offset_sources'])
            if 'bike_overrides' in data and isinstance(data['bike_overrides'], dict):
                self._config.bike_overrides.update(data['bike_overrides'])
            if 'auto_sync_enabled' in data:
                self._config.auto_sync_enabled = bool(data['auto_sync_enabled'])
            self._config.save()

    # ── Overlay ───────────────────────────────────────────────────────────────
    def get_overlay(self) -> dict:
        return asdict(self._config.overlay)

    def save_overlay(self, data: dict) -> None:
        with self._config_lock:
            self._config.overlay = overlay_from_dict(data)
            self._config.save()

    def save_overlay_as(self, name: str, data: dict) -> None:
        with self._config_lock:
            self._config.presets[name] = data
            self._config.overlay = overlay_from_dict(data)
            self._config.active_preset = name
            self._config.save()

    def list_presets(self) -> list:
        return list(self._config.presets.keys())

    # ── Session scanning ──────────────────────────────────────────────────────
    def scan_sessions(self, folder: str) -> list:
        """
        Scan a folder for telemetry files and match them to videos.
        Pass folder='__cache__' to return the last cached scan result.
        Returns a list of session dicts consumable by the JS Data page.
        """
        if folder == '__cache__':
            return self._cached_sessions()
        return self.scan_all_sessions([folder])

    def scan_all_sessions(self, telemetry_paths: list) -> list:
        """
        Scan all given telemetry folders and match them against a single video
        folder scan. The video folder is only ever scanned once per call — it
        used to be rescanned once per telemetry path, which meant every video
        got ffprobed N times for N configured telemetry folders.
        Returns a list of session dicts consumable by the JS Data page.
        """
        from session_scanner import (
            scan_csvs, scan_videos, group_videos, match_sessions,
            scan_pending_xrk, convert_xrk_files, MatchedSession,
        )

        folders = [str(Path(p).resolve()) for p in telemetry_paths if p]
        video_folder = self._config.video_path or (folders[0] if folders else '')

        # Auto-convert any XRK files that don't yet have a CSV, across all paths.
        # Progress messages are pushed to JS so the status bar stays informative.
        for folder in folders:
            pending_xrk = scan_pending_xrk(folder)
            if pending_xrk:
                def _xrk_progress(msg: str) -> None:
                    self._push('scan_status', message=msg)
                convert_xrk_files(folder, progress_cb=_xrk_progress)

        file_cache = self._get_file_meta_cache()

        # Scan telemetry files across all configured paths (includes any CSVs
        # just produced above), deduplicating paths reachable from more than
        # one configured folder.
        csv_paths: list = []
        seen_csv = set()
        for folder in folders:
            for p in scan_csvs(folder, cache=file_cache['csvs']):
                if p not in seen_csv:
                    seen_csv.add(p)
                    csv_paths.append(p)

        # Scan the video folder exactly once, regardless of how many telemetry
        # folders were passed in.
        try:
            videos = scan_videos(video_folder, cache=file_cache['videos']) if video_folder else []
        except Exception:
            videos = []

        # Fold in any manually-linked camera folders (action cams with a wrong
        # clock — see link_camera_folder()). Same cached scan_videos(), just with
        # each entry's stored constant offset applied to creation_time so the
        # normal grouping/matching below treats them like any other video.
        from datetime import timedelta
        seen_video_paths = {v.path for v in videos}
        for entry in self._config.linked_camera_folders:
            lf_folder = entry.get('folder', '')
            offset    = entry.get('offset_seconds', 0.0)
            if not lf_folder:
                continue
            try:
                lf_videos = scan_videos(lf_folder, cache=file_cache['videos'])
            except Exception:
                continue
            for v in lf_videos:
                if v.path in seen_video_paths:
                    continue
                seen_video_paths.add(v.path)
                if v.creation_time:
                    v.creation_time = v.creation_time + timedelta(seconds=offset)
                videos.append(v)
        videos.sort(key=lambda v: v.sort_key)

        # Register every scanned video path with the video server's known-path
        # allowlist (see _is_known_video_path) so it's servable over HTTP.
        for v in videos:
            _register_known_video_path(v.path)

        self._save_file_meta_cache()

        groups = group_videos(videos)
        matches = match_sessions(csv_paths, groups)

        # Any XRK that still has no CSV (DLL missing / conversion failed) →
        # show as a pending session so the user can retry manually.
        existing_csv_paths = {m.csv_path for m in matches}
        for folder in folders:
            for xrk_path, csv_path in scan_pending_xrk(folder):
                if csv_path not in existing_csv_paths:
                    existing_csv_paths.add(csv_path)
                    matches.append(MatchedSession(
                        csv_path        = csv_path,
                        video_group     = None,
                        time_delta      = float('inf'),
                        csv_start       = None,
                        video_start     = None,
                        matched         = False,
                        source          = 'AIM Mychron',
                        needs_conversion= True,
                        xrk_path        = xrk_path,
                    ))

        # Load cached offsets
        offsets        = self._config.offsets
        offset_sources = self._config.offset_sources
        auto_failed    = set(self._config.auto_sync_failed)

        result = []
        for m in matches:
            csv = m.csv_path
            result.append({
                'csv_path':         csv,
                'source':           m.source,
                'csv_start':        m.csv_start.isoformat() if m.csv_start else None,
                'matched':          m.matched,
                'needs_conversion': m.needs_conversion,
                'xrk_path':        m.xrk_path,
                'video_paths':     m.video_group.paths if m.video_group else [],
                'sync_offset':     offsets.get(csv),
                'sync_source':     offset_sources.get(csv),
                'auto_sync_failed': csv in auto_failed,
                'track':           '',
                'laps':            '',
                'best':            None,
            })

        logger.info('scan_all_sessions: %s → %d sessions', folders, len(result))
        return result

    def link_camera_folder(self, day: str, folder: str, day_sessions: list) -> dict:
        """Manually link a folder of action-cam clips to a day of telemetry sessions.

        Solves for the constant clock offset (session_scanner.solve_camera_offset)
        that best aligns the folder's video timestamps with that day's session
        start times, and persists it so every future scan applies the same
        correction — for cameras whose date/time was never set correctly.

        day_sessions: [{csv_path, csv_start}, ...] for the day being linked, as
        already held by the JS Data page (avoids re-deriving "sessions on day X"
        on the backend).
        Returns {offset_seconds, matched_count, total_groups, total_sessions}.
        """
        from session_scanner import scan_videos, group_videos, solve_camera_offset
        from datetime import datetime as _dt

        folder = str(Path(folder).resolve())
        file_cache = self._get_file_meta_cache()
        try:
            videos = scan_videos(folder, cache=file_cache['videos'])
        except Exception:
            videos = []
        for v in videos:
            _register_known_video_path(v.path)
        self._save_file_meta_cache()

        groups = group_videos(videos)

        session_times = []
        for s in day_sessions:
            raw = s.get('csv_start')
            if not raw:
                continue
            try:
                session_times.append(_dt.fromisoformat(raw.replace('Z', '+00:00')))
            except Exception:
                continue

        offset, matched_count = solve_camera_offset(groups, session_times)

        with self._config_lock:
            entries = [e for e in self._config.linked_camera_folders
                       if not (e.get('day') == day and e.get('folder') == folder)]
            entries.append({'day': day, 'folder': folder, 'offset_seconds': offset, 'source': 'auto'})
            self._config.linked_camera_folders = entries
            self._config.save()

        logger.info('link_camera_folder: %s + %s → offset=%.1fs matched=%d/%d',
                   day, folder, offset, matched_count, len(groups))
        return {
            'offset_seconds': offset,
            'matched_count':  matched_count,
            'total_groups':   len(groups),
            'total_sessions': len(session_times),
        }

    def unlink_camera_folder(self, day: str, folder: str) -> None:
        """Remove a previously linked camera folder for a day."""
        folder = str(Path(folder).resolve())
        with self._config_lock:
            self._config.linked_camera_folders = [
                e for e in self._config.linked_camera_folders
                if not (e.get('day') == day and e.get('folder') == folder)
            ]
            self._config.save()

    def save_sessions_cache(self, sessions: list) -> None:
        """Persist the full merged session list (from all paths) for fast startup.

        Called by JS after collecting results from all telemetry paths so the
        cache always reflects the complete set, not just the last path scanned.
        """
        import json
        from pathlib import Path as _Path
        from app_config import SCAN_CACHE_FILE
        try:
            SCAN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {'sessions': sessions}
            with open(SCAN_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info('Saved %d sessions to scan cache', len(sessions))
        except Exception:
            logger.exception('Failed to save sessions cache')

    def _cached_sessions(self) -> list:
        """Return cached sessions from disk without rescanning."""
        cache          = load_scan_cache()
        sessions       = cache.get('sessions', [])
        offsets        = self._config.offsets
        offset_sources = self._config.offset_sources
        auto_failed    = set(self._config.auto_sync_failed)
        result = []
        for s in sessions:
            csv = s.get('csv_path', '')
            vpaths = s.get('video_paths', [])
            # Re-register on every cache load (not just live scans) so playback
            # still works for a session restored from disk before any rescan
            # has run in this process.
            for vp in vpaths:
                _register_known_video_path(vp)
            result.append({
                'csv_path':         csv,
                'source':           s.get('source', 'RaceBox'),
                'csv_start':        s.get('csv_start'),
                'matched':          s.get('matched', False),
                'needs_conversion': s.get('needs_conversion', False),
                'xrk_path':        s.get('xrk_path'),
                'video_paths':     vpaths,
                'sync_offset':     offsets.get(csv),
                'sync_source':     offset_sources.get(csv),
                'auto_sync_failed': csv in auto_failed,
                'track':           s.get('track', ''),
                'laps':            s.get('laps', ''),
                'best':            s.get('best') or None,
            })
        return result

    # ── Session metadata (fast header read) ──────────────────────────────────
    def get_session_meta(self, csv_path: str) -> dict:
        """
        Quick read of track name, lap count, and best lap time.
        Reads only the CSV header block — does not parse all data points.
        """
        try:
            import os
            suffix = os.path.splitext(csv_path)[1].lower()

            # GPX / MoTeC / VBOX: need a full load but they're usually small.
            # Cache the derived result by (size, mtime) so repeat scans of an
            # unchanged file don't re-parse it every time.
            if suffix in ('.gpx', '.ld', '.vbo', '.uni', '.tsv'):
                stat = None
                try:
                    st = os.stat(csv_path)
                    stat = (st.st_size, st.st_mtime)
                except OSError:
                    pass

                # Fetching the cache, checking the cached entry, mutating it,
                # and triggering the save must all happen under the SAME lock
                # as one atomic per-call operation. Several threads race in
                # here concurrently (frontend fires getSessionMeta 6-at-a-time
                # via Promise.all) — without this, one thread's dict mutation
                # can land while another thread's _save_file_meta_cache() is
                # mid-`json.dump` iteration over the same dict, raising
                # "RuntimeError: dictionary changed size during iteration".
                # The actual (possibly slow) file parse below stays outside
                # the lock so concurrent metadata reads aren't serialized.
                with self._meta_cache_lock:
                    meta_cache = self._get_file_meta_cache()['meta']
                    entry = meta_cache.get(csv_path)
                    if stat and entry and entry.get('size') == stat[0] and entry.get('mtime') == stat[1]:
                        return entry['data']

                session = self._load_session(csv_path)
                if not session:
                    result = {'track': '', 'laps': '', 'best': '', 'best_secs': None, 'speed_unit': 'kmh'}
                else:
                    laps = getattr(session, 'laps', [])
                    durs = [l.duration for l in laps if l.duration]
                    best = min(durs) if durs else None
                    result = {
                        'track':      getattr(session, 'track', '') or '',
                        'laps':       str(len(laps)),
                        'best':       f'{best:.3f}s' if best else '',
                        'best_secs':  best,
                        'speed_unit': getattr(session, 'source_speed_unit', 'kmh'),
                    }
                if stat:
                    with self._meta_cache_lock:
                        meta_cache = self._get_file_meta_cache()['meta']
                        meta_cache[csv_path] = {'size': stat[0], 'mtime': stat[1], 'data': result}
                        self._save_file_meta_cache()
                return result

            # AIM CSV: no metadata header; use filename
            if suffix == '.csv':
                track = laps_str = best_str = ''
                best_secs = None
                with open(csv_path, encoding='utf-8-sig', errors='ignore') as f:
                    first = f.readline()
                    if first.startswith('Time (s),'):
                        # AIM format — no header block
                        import aim_data
                        return {
                            'track': '',
                            'laps': '',
                            'best': '',
                            'best_secs': None,
                            'speed_unit': aim_data.sniff_speed_unit(first),
                        }
                    # RaceBox CSV — key:value header
                    from itertools import chain
                    for line in chain([first], f):
                        if line.startswith('Track,'):
                            track = line.strip().split(',', 1)[1]
                        elif line.startswith('Laps,'):
                            laps_str = line.strip().split(',', 1)[1]
                        elif line.startswith('Best Lap Time,'):
                            raw = line.strip().split(',', 1)[1]
                            try:
                                best_secs = float(raw)
                                best_str  = f'{best_secs:.3f}s'
                            except Exception:
                                best_str = raw
                        elif line.startswith('Record,'):
                            break
                return {'track': track, 'laps': laps_str,
                        'best': best_str, 'best_secs': best_secs, 'speed_unit': 'kmh'}

        except Exception:
            logger.exception('get_session_meta failed for %s', csv_path)
        return {'track': '', 'laps': '', 'best': '', 'best_secs': None, 'speed_unit': 'kmh'}

    # ── Lap loading ───────────────────────────────────────────────────────────
    def get_laps(self, csv_path: str) -> list:
        """Return lap list for a session: [{lap_idx, duration, is_best}]."""
        try:
            session = self._load_session(csv_path)
            if not session or not session.laps:
                return []

            best_dur = min((l.duration for l in session.timed_laps if l.duration), default=None)
            result = []
            for i, lap in enumerate(session.laps):
                result.append({
                    'lap_idx':      i,
                    'lap_num':      lap.lap_num,
                    'duration':     lap.duration,
                    'is_best':      (not lap.is_outlap and not lap.is_inlap
                                     and lap.duration is not None and best_dur is not None
                                     and abs(lap.duration - best_dur) < 0.001),
                    'elapsed_start': round(lap.elapsed_start, 3) if hasattr(lap, 'elapsed_start') and lap.elapsed_start is not None else 0.0,
                    'is_outlap':    lap.is_outlap if hasattr(lap, 'is_outlap') else False,
                    'is_inlap':     lap.is_inlap  if hasattr(lap, 'is_inlap')  else False,
                })
            return result
        except Exception:
            logger.exception('get_laps failed for %s', csv_path)
            return []

    def load_lap_history(self, csv_path: str, lap_idx: int) -> list:
        """Return telemetry data points for one lap as a list of dicts."""
        try:
            session = self._load_session(csv_path)
            if not session or lap_idx >= len(session.laps):
                return []
            lap = session.laps[lap_idx]
            points = []
            for p in lap.points:
                d = {
                    't':            p.lap_elapsed,   # lap-relative elapsed (0 → lap_duration)
                    'speed':        p.speed,         # km/h
                    'gx':           p.gforce_x,      # longitudinal G
                    'gy':           p.gforce_y,      # lateral G
                    'rpm':          p.rpm or 0,
                    'exhaust_temp': p.exhaust_temp or 0,
                    'alt':          p.alt,
                    'lat':          p.lat,
                    'lon':          p.lon,
                    'lean':         p.lean_angle,
                    'gear':         p.gear or 0,
                }
                points.append(d)
            return points
        except Exception as e:
            logger.exception('load_lap_history failed for %s lap %d: %s', csv_path, lap_idx, e)
            return []

    # ── File dialogs ──────────────────────────────────────────────────────────
    def open_folder_dialog(self) -> Optional[str]:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG
        )
        if result:
            return str(Path(result[0]).resolve())
        return None

    def open_file_dialog(self, filters: list = None) -> Optional[str]:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=filters or []
        )
        if result:
            return str(Path(result[0]).resolve())
        return None

    # ── Weather ───────────────────────────────────────────────────────────────
    def get_weather(self, lat: float, lon: float, date_iso: str) -> dict:
        try:
            from weather import fetch_weather
            weather_str, wind_str = fetch_weather(lat, lon, date_iso)
            return {'weather': weather_str, 'wind': wind_str}
        except Exception:
            return {'weather': '—', 'wind': '—'}

    # ── Session info overrides ────────────────────────────────────────────────
    def edit_session_info(self, csv_path: str, overrides: dict) -> None:
        with self._config_lock:
            self._config.session_info[csv_path] = overrides
            self._config.save()

    def bulk_rename_track(self, csv_paths: list, new_name: str) -> dict:
        """Set the track override to new_name for each path in csv_paths.

        The caller (JS) is responsible for determining which paths to rename,
        since it has access to the enriched _meta that the backend does not.
        Returns {'updated': N}.
        """
        updated = 0
        with self._config_lock:
            for csv_path in csv_paths:
                if not csv_path:
                    continue
                abs_path = os.path.abspath(csv_path)
                existing = self._config.session_info.get(abs_path, {})
                self._config.session_info[abs_path] = {**existing, 'info_track': new_name}
                updated += 1

            if updated:
                self._config.save()
        return {'updated': updated}

    def get_laps_for_ref_picker(self, csv_path: str) -> list:
        """Return timed laps from all sessions sharing the same track as csv_path.

        Groups laps by session for the manual reference lap picker UI.
        Returns [{csv_path, date, laps: [{lap_num, duration, is_best}]}].
        """
        from app_config import load_scan_cache
        session = self._load_session(csv_path)
        if not session:
            return []

        abs_path      = os.path.abspath(csv_path)
        base_track    = session.track or ''
        override      = self._config.session_info.get(abs_path, {}).get('info_track', '').strip()
        current_track = (override or base_track).strip().lower()

        cache   = load_scan_cache()
        entries = cache.get('sessions', [])
        results = []

        for entry in entries:
            ep = entry.get('csv_path', '')
            if not ep or not os.path.exists(ep):
                continue
            abs_ep    = os.path.abspath(ep)
            ov_track  = self._config.session_info.get(abs_ep, {}).get('info_track', '').strip()
            raw_track = entry.get('track', '').strip()
            try:
                sess = self._load_session(ep)
                if not sess:
                    continue
                # Fall back to actual session track when scan cache entry is stale/empty
                entry_trk = (ov_track or raw_track or sess.track or '').strip().lower()
                # When current session has a track name, filter to matching sessions only.
                # When it has no track name, show everything so the user isn't blocked.
                if current_track and entry_trk != current_track:
                    continue
                timed    = sess.timed_laps
                best_dur = min((l.duration for l in timed), default=None)
                laps     = [
                    {
                        'lap_num':  l.lap_num,
                        'duration': round(l.duration, 3),
                        'is_best':  best_dur is not None and abs(l.duration - best_dur) < 0.001,
                    }
                    for l in timed
                ]
                if laps:
                    results.append({
                        'csv_path': ep,
                        'date':     entry.get('csv_start', ''),
                        'laps':     laps,
                    })
            except Exception as e:
                logger.debug('get_laps_for_ref_picker: could not load %s: %s', ep, e)

        return results

    # ── Track map (OSM) ──────────────────────────────────────────────────────
    def get_track_map_candidates(self, csv_path: str) -> dict:
        """Return {candidates, selected_osm_id, auto_osm_id, track_key} for a session.

        Queries Overpass API (cached on disk). May be slow on first call.
        Returns {candidates: [], selected_osm_id: '', auto_osm_id: '', track_key: ''} on error.
        """
        from track_map_cache import fetch_candidates, auto_select
        empty = {'candidates': [], 'selected_osm_id': '', 'auto_osm_id': '', 'track_key': ''}
        try:
            session = self._load_session(csv_path)
            if not session:
                return empty
            pts  = session.all_points
            lats = [p.lat for p in pts if p.lat]
            lons = [p.lon for p in pts if p.lon]
            if not lats:
                return empty

            clat = sum(lats) / len(lats)
            clon = sum(lons) / len(lons)
            candidates = fetch_candidates(clat, clon)
            auto_id    = auto_select(candidates, lats, lons) or ''

            abs_csv    = os.path.abspath(csv_path)
            track_name = (self._config.session_info.get(abs_csv, {}).get('info_track')
                          or getattr(session, 'track', '') or '').lower().strip()
            selections = getattr(self._config, 'track_map_selections', {}) or {}
            selected_id = selections.get(track_name, '')

            # Slim down — strip full geometry to keep response size small
            slim = [
                {
                    'osm_id':          c['osm_id'],
                    'name':            c['name'],
                    'centroid_dist_m': round(c.get('centroid_dist_m', 0)),
                }
                for c in candidates
            ]
            return {
                'candidates':      slim,
                'selected_osm_id': selected_id,
                'auto_osm_id':     auto_id,
                'track_key':       track_name,
            }
        except Exception:
            logger.exception('get_track_map_candidates failed for %s', csv_path)
            return empty

    def set_track_map_selection(self, track_key: str, osm_id: str) -> None:
        """Save (or clear) the user-chosen OSM way for a track name."""
        key = track_key.lower().strip()
        if osm_id and not _VALID_OSM_ID_RE.match(str(osm_id)):
            # osm_id ends up in a cache filename (track_map_cache._cache_path via
            # load_geometry) — reject anything that isn't a plain integer id
            # instead of silently persisting it into config.
            logger.warning('set_track_map_selection: rejecting invalid osm_id %r', osm_id)
            return
        with self._config_lock:
            if not isinstance(getattr(self._config, 'track_map_selections', None), dict):
                self._config.track_map_selections = {}
            if osm_id:
                self._config.track_map_selections[key] = str(osm_id)
            else:
                self._config.track_map_selections.pop(key, None)
            self._config.save()

    def get_track_map_geometry(self, csv_path: str,
                               centroid_lat: float = None,
                               centroid_lon: float = None) -> dict:
        """Return {lats, lons, areas} for the selected/auto OSM track map of a session.

        centroid_lat/lon should be supplied by the caller (already computed JS-side
        from loaded telemetry) so this method never needs to reload the session file.
        Overpass queries happen only via get_track_map_candidates (user-triggered).
        """
        from track_map_cache import load_geometry, load_areas, auto_select, _cache_path
        import json as _json
        try:
            abs_csv    = os.path.abspath(csv_path)
            track_name = (self._config.session_info.get(abs_csv, {}).get('info_track', '')
                          or self._fast_track_name(csv_path)).lower().strip()
            selections = getattr(self._config, 'track_map_selections', {}) or {}
            osm_id     = selections.get(track_name, '')

            # Auto-select from disk cache using caller-supplied centroid — no session load
            if not osm_id and centroid_lat is not None and centroid_lon is not None:
                grid_lat = round(centroid_lat, 1)
                grid_lon = round(centroid_lon, 1)
                cp = _cache_path(f'candidates_{grid_lat:.1f}_{grid_lon:.1f}')
                if cp.exists():
                    try:
                        with open(cp, 'r', encoding='utf-8') as f:
                            cached = _json.load(f)
                        osm_id = auto_select(cached, [centroid_lat], [centroid_lon]) or ''
                    except Exception:
                        pass

            areas = []
            if centroid_lat is not None and centroid_lon is not None:
                areas = load_areas(centroid_lat, centroid_lon)

            if not osm_id:
                return {'lats': [], 'lons': [], 'areas': areas}

            # Defense in depth: osm_id ends up in a cache filename inside
            # load_geometry(). Values written via set_track_map_selection() are
            # already validated, but a hand-edited config.json or a value from
            # before this check existed should not silently reach that path build.
            if not _VALID_OSM_ID_RE.match(str(osm_id)):
                logger.warning('get_track_map_geometry: rejecting invalid osm_id %r', osm_id)
                return {'lats': [], 'lons': [], 'areas': areas}

            geometry = load_geometry(osm_id)
            if not geometry:
                return {'lats': [], 'lons': [], 'areas': areas}

            return {
                'lats':  [g['lat'] for g in geometry],
                'lons':  [g['lon'] for g in geometry],
                'areas': areas,
            }
        except Exception:
            logger.exception('get_track_map_geometry failed for %s', csv_path)
            return {'lats': [], 'lons': [], 'areas': []}

    @staticmethod
    def _fast_track_name(csv_path: str) -> str:
        """Read track name from CSV header only — no full session parse."""
        try:
            suffix = os.path.splitext(csv_path)[1].lower()
            if suffix == '.csv':
                with open(csv_path, encoding='utf-8-sig', errors='ignore') as fh:
                    for line in fh:
                        if line.startswith('Track,'):
                            return line.strip().split(',', 1)[1]
                        if line.startswith('Record,') or line.startswith('Time (s),'):
                            break
        except Exception:
            pass
        return ''

    # ── Export ────────────────────────────────────────────────────────────────
    def start_export(self, params: dict) -> None:
        # Stop any running auto-sync before beginning export
        self._auto_sync_cancel.set()
        with self._thread_lock:
            if self._export_thread and self._export_thread.is_alive():
                return
            self._export_cancel.clear()
            self._export_thread = threading.Thread(
                target=self._run_export_bg,
                args=(params,),
                daemon=True,
            )
            self._export_thread.start()

    def cancel_export(self) -> None:
        self._export_cancel.set()

    # ── Auto sync ─────────────────────────────────────────────────────────────
    def start_auto_sync(self, sessions: list) -> dict:
        """Start background auto-sync for sessions that need it.

        Only runs if auto_sync_enabled is True. Skips sessions that already
        have any offset or are in the auto_sync_failed list. Does not start
        during an active export.

        Returns {'queued': N}.
        """
        if not self._config.auto_sync_enabled:
            return {'queued': 0}

        with self._thread_lock:
            if self._export_thread and self._export_thread.is_alive():
                return {'queued': 0}
            if self._auto_sync_thread and self._auto_sync_thread.is_alive():
                return {'queued': 0}

        failed_set = set(self._config.auto_sync_failed)
        eligible = [
            s for s in sessions
            if s.get('matched')
            and s.get('video_paths')
            and self._config.offsets.get(s['csv_path']) is None
            and s['csv_path'] not in failed_set
        ]
        if not eligible:
            return {'queued': 0}

        self._auto_sync_cancel.clear()
        self._auto_sync_thread = threading.Thread(
            target=self._run_auto_sync_bg,
            args=(eligible,),
            daemon=True,
        )
        self._auto_sync_thread.start()
        return {'queued': len(eligible)}

    def cancel_auto_sync(self) -> None:
        self._auto_sync_cancel.set()

    def _run_auto_sync_bg(self, sessions: list) -> None:
        from auto_sync import run_auto_sync

        total = len(sessions)
        progress_lock = threading.Lock()
        started = 0

        def _process(s: dict) -> None:
            nonlocal started
            if self._auto_sync_cancel.is_set():
                return
            if self._export_thread and self._export_thread.is_alive():
                return

            csv_path = s['csv_path']
            with progress_lock:
                started += 1
                idx = started
            self._push('auto_sync_progress',
                       status='processing', csv_path=csv_path,
                       current=idx, total=total)

            def _progress(vid_t, offset, conf, _csv=csv_path):
                self._push('auto_sync_progress',
                           status='checking', csv_path=_csv,
                           vid_t=vid_t, offset=offset, confidence=conf)

            offset, confidence = run_auto_sync(
                csv_path    = csv_path,
                video_paths = s.get('video_paths', []),
                source      = s.get('source', 'RaceBox'),
                cancel_event = self._auto_sync_cancel,
                progress_cb  = _progress,
            )

            if self._auto_sync_cancel.is_set():
                return

            # Config saves are serialized — two workers finishing at the same
            # moment must not interleave writes to the same JSON file.
            with self._config_lock:
                if offset is not None:
                    # Don't overwrite a user-confirmed offset set while we were processing
                    if self._config.offset_sources.get(csv_path) != 'user':
                        self._config.offsets[csv_path]        = offset
                        self._config.offset_sources[csv_path] = 'auto'
                        self._config.save()
                        self._push('auto_sync_progress',
                                   status='done', csv_path=csv_path,
                                   offset=offset, confidence=confidence)
                else:
                    if csv_path not in self._config.auto_sync_failed:
                        self._config.auto_sync_failed.append(csv_path)
                    self._config.save()
                    self._push('auto_sync_progress',
                               status='failed', csv_path=csv_path,
                               confidence=confidence)

        with concurrent.futures.ThreadPoolExecutor(max_workers=AUTO_SYNC_WORKERS) as ex:
            list(ex.map(_process, sessions))

        self._push('auto_sync_done')

    def _run_export_bg(self, params: dict) -> None:
        from export_runner import run_export

        def log_cb(msg):
            self._push('export_log', message=msg)

        def progress_cb(pct, msg=''):
            self._push('export_progress', value=pct, message=msg)

        def done_cb(ok, msg=''):
            self._push('export_done', ok=ok, message=msg)

        _workers = max(1, min(int(params.get('workers', 4)), os.cpu_count() or 4))
        _crf     = max(0, min(int(params.get('crf', 18)), 51))
        try:
            run_export(
                items             = params.get('items', []),
                scope             = params.get('scope', 'fastest'),
                export_path       = params.get('export_path', ''),
                encoder           = params.get('encoder', 'libx264'),
                crf               = _crf,
                workers           = _workers,
                padding           = params.get('padding', 5.0),
                is_bike           = params.get('is_bike', False),
                show_map          = params.get('show_map', True),
                show_tel          = params.get('show_tel', True),
                layout            = params.get('layout', {}),
                clip_start_s      = params.get('clip_start_s', 0.0),
                clip_end_s        = params.get('clip_end_s', 0.0),
                ref_mode          = params.get('ref_mode', 'none'),
                ref_lap_obj       = None,
                ref_lap_csv_path  = params.get('ref_lap_csv_path', ''),
                ref_lap_num       = int(params.get('ref_lap_num', 0) or 0),
                # Shallow copies: these are flat dicts of primitives, so a copy
                # is cheap and means edit_session_info()/bulk_rename_track()
                # mutating the live config on the main thread while this
                # background export thread iterates cannot raise
                # "RuntimeError: dictionary changed size during iteration".
                bike_overrides    = dict(self._config.bike_overrides),
                session_info      = dict(self._config.session_info),
                log_cb            = log_cb,
                progress_cb       = progress_cb,
                done_cb           = done_cb,
                overlay_only          = params.get('overlay_only', False),
                track_map_selections  = getattr(self._config, 'track_map_selections', {}) or {},
                speed_unit_pref       = params.get('speed_unit', 'auto'),
                is_cancelled          = self._export_cancel.is_set,
            )
        except Exception as e:
            done_cb(False, str(e))

    # ── RaceBox cloud ─────────────────────────────────────────────────────────
    def racebox_playwright_status(self) -> dict:
        """Return whether playwright and Chromium are ready to use."""
        try:
            from playwright._impl._driver import compute_driver_executable
            node_exe, cli_js = compute_driver_executable()
            import os
            playwright_ok = os.path.isfile(str(node_exe))
        except Exception:
            return {'playwright': False, 'chromium': False}

        # Check if Chromium exists in PLAYWRIGHT_BROWSERS_PATH (same location
        # the runtime hook and the driver will use at runtime).
        import glob as _glob, os
        local_app = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        browsers_path = os.environ.get(
            'PLAYWRIGHT_BROWSERS_PATH',
            os.path.join(local_app, 'ms-playwright'),
        )
        chromium_dirs = _glob.glob(os.path.join(browsers_path, 'chromium*'))
        return {'playwright': playwright_ok, 'chromium': bool(chromium_dirs)}

    def install_playwright_chromium(self) -> None:
        """Download Chromium for Playwright in the background.
        Pushes events: racebox_setup_log {message}, racebox_setup_done {ok, message}."""
        import threading

        def _run():
            try:
                from playwright._impl._driver import compute_driver_executable
                node_exe, cli_js = compute_driver_executable()
                import subprocess, os
                self._push('racebox_setup_log', message='Downloading Chromium (~130 MB, one-time)…')
                env = os.environ.copy()
                proc = subprocess.Popen(
                    [str(node_exe), str(cli_js), 'install', 'chromium'],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env,
                )
                # Read char-by-char so \r-terminated progress lines are captured
                buf = ''
                while True:
                    ch = proc.stdout.read(1)
                    if not ch:
                        break
                    if ch in ('\n', '\r'):
                        line = buf.strip()
                        if line:
                            self._push('racebox_setup_log', message=line)
                        buf = ''
                    else:
                        buf += ch
                if buf.strip():
                    self._push('racebox_setup_log', message=buf.strip())
                proc.wait()
                if proc.returncode == 0:
                    self._push('racebox_setup_done', ok=True,
                               message='Chromium installed. You can now use RaceBox cloud download.')
                else:
                    self._push('racebox_setup_done', ok=False,
                               message=f'Install failed (exit {proc.returncode}).')
            except Exception as e:
                self._push('racebox_setup_done', ok=False, message=f'Error: {e}')

        threading.Thread(target=_run, daemon=True).start()

    def racebox_login(self, email: str, password: str) -> dict:
        """Check whether saved RaceBox auth is still valid (headless).
        If no saved auth exists, returns a prompt to use Download Sessions instead.
        email/password args are unused — auth is browser-based via Playwright."""
        try:
            from racebox_downloader import RaceBoxSource
        except ImportError:
            return {'ok': False, 'error': 'Playwright / racebox_downloader not available in this build.'}

        src = RaceBoxSource()
        if not src.is_authenticated():
            return {
                'ok': False,
                'error': 'Not logged in yet. Click "Download Sessions" — a browser will open for first-time login.',
            }

        # Validate saved auth headlessly
        logs: list[str] = []
        ok = src.authenticate(log_cb=logs.append)
        if ok:
            return {'ok': True}
        return {'ok': False, 'error': '\n'.join(logs) or 'Auth validation failed.'}

    # ── Encoder detection ──────────────────────────────────────────────────────
    def check_encoders(self) -> dict:
        """
        Probe FFmpeg and report which video encoders are available.
        Returns {version, encoders: [{name, label, available}]} or {error}.
        """
        import subprocess, shutil, os, sys

        ffmpeg_bin = os.environ.get('FFMPEG_BIN') or shutil.which('ffmpeg')
        if not ffmpeg_bin:
            base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            fname = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
            candidate = os.path.join(base, fname)
            if os.path.isfile(candidate):
                ffmpeg_bin = candidate
        if not ffmpeg_bin:
            return {'error': 'FFmpeg not found in PATH.'}

        try:
            r = subprocess.run([ffmpeg_bin, '-version'], capture_output=True, text=True, timeout=10)
            first = r.stdout.splitlines()[0] if r.stdout else ''
            version = first.split('version')[-1].strip().split(' ')[0] if 'version' in first else 'unknown'
        except Exception as e:
            return {'error': f'FFmpeg error: {e}'}

        candidates = [
            ('libx264',           'H.264 software'),
            ('libx265',           'H.265 software'),
            ('h264_nvenc',        'H.264 NVIDIA NVENC'),
            ('hevc_nvenc',        'H.265 NVIDIA NVENC'),
            ('h264_videotoolbox', 'H.264 Apple VideoToolbox'),
            ('h264_amf',          'H.264 AMD AMF'),
            ('h264_qsv',          'H.264 Intel QSV'),
        ]

        def _probe(enc):
            try:
                r = subprocess.run(
                    [ffmpeg_bin, '-f', 'lavfi', '-i', 'nullsrc=s=64x64:d=0.1',
                     '-vcodec', enc, '-f', 'null', '-'],
                    capture_output=True, timeout=8
                )
                return r.returncode == 0
            except Exception:
                return False

        encoders = [
            {'name': n, 'label': l, 'available': _probe(n)}
            for n, l in candidates
        ]
        return {'version': version, 'encoders': encoders}

    # ── About ──────────────────────────────────────────────────────────────────
    def get_about_info(self) -> dict:
        """Return diagnostic strings for the About section."""
        import sys
        from app_config import CONFIG_FILE
        from _version import __version__
        return {
            'version': __version__,
            'python': f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
            'config': str(CONFIG_FILE),
        }

    # ── AIM DLL status ────────────────────────────────────────────────────────
    @staticmethod
    def _find_matlab_xrk_dll() -> str:
        """Search known install locations for the MatLabXRK*.dll reader.

        Shared by aim_dll_status() and convert_xrk_session() — both used to
        duplicate this search-path-construction + glob logic verbatim.
        Returns the first match found, or '' if none.
        """
        import glob as _glob, sys, os
        from pathlib import Path
        # Persistent user directory is checked first so the DLL survives app rebuilds.
        search_dirs = [str(Path.home() / '.openlap')]
        if getattr(sys, 'frozen', False):
            search_dirs += [sys._MEIPASS, os.path.dirname(sys.executable)]
        else:
            search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        for base in search_dirs:
            dlls = _glob.glob(os.path.join(base, 'MatLabXRK*.dll'))
            if dlls:
                return dlls[0]
        return ''

    def aim_dll_status(self) -> dict:
        """Return AIM XRK reader availability.

        Two readers exist:
          - Windows-only MatLabXRK DLL (downloaded from aim-sportline.com)
          - Cross-platform libxrk (PyPI; ships native wheels for win/mac/linux)
        Either one is sufficient for XRK conversion. Frontend uses
        `xrk_supported` to decide whether to show AIM-related UI.
        """
        import sys
        dll_path = self._find_matlab_xrk_dll()

        try:
            import libxrk  # noqa: F401
            libxrk_available = True
        except ImportError:
            libxrk_available = False

        return {
            'found': bool(dll_path),
            'path': dll_path,
            'libxrk_available': libxrk_available,
            'xrk_supported': bool(dll_path) or libxrk_available,
            'is_windows': sys.platform == 'win32',
        }

    def download_aim_dll(self) -> dict:
        """Download the AIM MatLabXRK DLL from aim-sportline.com in a background thread.
        Progress is pushed as openlap events: aim_dll_progress {value, message}, aim_dll_done {ok, message}."""
        import threading

        def _run():
            try:
                import sys, os
                from xrk_to_csv import _download_dll_urllib, _install_dll_from_zip, DLL_ZIP_URL
                self._push('aim_dll_progress', value=10, message='Connecting to aim-sportline.com…')
                data = _download_dll_urllib()
                if not data:
                    self._push('aim_dll_done', ok=False, message='Download failed — could not reach aim-sportline.com.')
                    return
                self._push('aim_dll_progress', value=70, message='Extracting DLL…')
                from pathlib import Path as _Path
                install_dir = str(_Path.home() / '.openlap')
                os.makedirs(install_dir, exist_ok=True)
                import io, zipfile, glob as _glob
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for entry in zf.namelist():
                        if not entry.lower().endswith('.dll'):
                            continue
                        local_name = os.path.basename(entry)
                        if not local_name:
                            continue
                        local_path = os.path.join(install_dir, local_name)
                        if os.path.isfile(local_path):
                            continue
                        with zf.open(entry) as src, open(local_path, 'wb') as dst:
                            dst.write(src.read())
                dlls = _glob.glob(os.path.join(install_dir, 'MatLabXRK*.dll'))
                if dlls:
                    self._push('aim_dll_progress', value=100, message='DLL installed.')
                    self._push('aim_dll_done', ok=True, message='MatLabXRK DLL installed — restart OpenLap to use AIM XRK conversion.')
                else:
                    self._push('aim_dll_done', ok=False, message='Zip downloaded but MatLabXRK DLL not found inside.')
            except Exception as e:
                self._push('aim_dll_done', ok=False, message=f'Error: {e}')

        threading.Thread(target=_run, daemon=True).start()

    # ── AIM XRK conversion ────────────────────────────────────────────────────
    def convert_xrk_session(self, csv_path: str) -> dict:
        """Convert a single AIM XRK file to CSV. csv_path is the expected CSV output path."""
        import os
        xrk_path = os.path.splitext(csv_path)[0]
        # Try common XRK extensions
        actual_xrk = None
        for ext in ('.xrk', '.xrz', '.drk', '.XRK', '.XRZ', '.DRK'):
            candidate = xrk_path + ext
            if os.path.isfile(candidate):
                actual_xrk = candidate
                break
        if not actual_xrk:
            return {'ok': False, 'error': 'XRK source file not found'}
        try:
            dll_path = self._find_matlab_xrk_dll()
            if dll_path:
                import xrk_to_csv as _xrk
                _xrk.xrk_to_csv(actual_xrk, csv_path, dll_path)
            else:
                from xrk_to_csv_libxrk import xrk_to_csv_libxrk
                xrk_to_csv_libxrk(actual_xrk, csv_path)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Manual video assignment ───────────────────────────────────────────────
    def assign_video(self, csv_path: str, video_path: str) -> None:
        """Manually link a video file to a telemetry session."""
        abs_csv = str(Path(csv_path).resolve())
        abs_video = str(Path(video_path).resolve())
        _register_known_video_path(abs_video)
        with self._config_lock:
            si = self._config.session_info.setdefault(abs_csv, {})
            si['_video_override'] = abs_video
            self._config.save()

    # ── RaceBox session download ──────────────────────────────────────────────
    def download_racebox_sessions(self) -> None:
        """Start a background RaceBox download. Progress is pushed as events:
            racebox_log      {message}
            racebox_progress {value: 0-100, message}
            racebox_done     {ok, message, n_downloaded}
        """
        with self._thread_lock:
            if self._rb_thread and self._rb_thread.is_alive():
                return   # already running
            self._rb_cancel.clear()
            self._rb_thread = threading.Thread(
                target=self._run_racebox_bg, daemon=True)
            self._rb_thread.start()

    def cancel_racebox_download(self) -> None:
        self._rb_cancel.set()

    def _run_racebox_bg(self) -> None:
        def log(msg: str) -> None:
            self._push('racebox_log', message=msg)

        def progress(pct: float, msg: str = '') -> None:
            self._push('racebox_progress', value=pct, message=msg)

        def done(ok: bool, msg: str = '', n: int = 0) -> None:
            self._push('racebox_done', ok=ok, message=msg, n_downloaded=n)

        try:
            from racebox_downloader import RaceBoxSource
        except ImportError:
            done(False, 'Playwright / racebox_downloader not available in this build.')
            return

        dest = self._config.racebox_path or self._config.telemetry_path
        if not dest:
            done(False, 'No RaceBox folder configured — set it in Settings.')
            return

        try:
            src = RaceBoxSource(data_dir=dest)

            # Authenticate (opens browser on first run; headless thereafter)
            log('Authenticating…')
            ok = src.authenticate(log_cb=log)
            if not ok:
                done(False, 'Authentication failed.')
                return
            if self._rb_cancel.is_set():
                done(False, 'Cancelled.')
                return

            # List sessions
            log('Fetching session list from racebox.pro…')
            sessions = src.list_sessions(log_cb=log)
            if not sessions:
                done(True, 'No sessions found on racebox.pro.', 0)
                return

            new = [s for s in sessions if not src.already_downloaded(s, dest)]
            log(f'{len(sessions)} session(s) on server — {len(new)} new to download.')

            if not new:
                done(True, 'Already up to date.', 0)
                return

            # Download new sessions
            downloaded = 0
            for i, sess in enumerate(new):
                if self._rb_cancel.is_set():
                    done(False, f'Cancelled after {downloaded} download(s).',
                         downloaded)
                    return

                progress((i / len(new)) * 100, f'{i+1}/{len(new)}: {sess.label()}')
                path = src.download(sess, dest,
                                    progress_cb=None, log_cb=log)
                if path:
                    downloaded += 1

            progress(100, 'Done.')
            done(True, f'{downloaded} of {len(new)} session(s) downloaded.', downloaded)

        except Exception as exc:
            logger.exception('RaceBox download error')
            done(False, str(exc))

    # ── Internal helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _load_session(csv_path: str):
        import gpx_data, aim_data, racebox_data, motec_data, vbox_data, unipro_data
        if vbox_data.is_vbox(csv_path):
            return vbox_data.load_vbo(csv_path)
        if motec_data.is_motec_ld(csv_path):
            return motec_data.load_ld(csv_path)
        if gpx_data.is_gpx(csv_path):
            return gpx_data.load_gpx(csv_path)
        if unipro_data.is_unipro_tsv(csv_path):
            return unipro_data.load_tsv(csv_path)
        if unipro_data.is_unipro_uni(csv_path):
            return unipro_data.load_uni(csv_path)
        if aim_data.is_aim_csv(csv_path):
            return aim_data.load_csv(csv_path)
        return racebox_data.load_csv(csv_path)

    def confirm_clear_queue(self) -> bool:
        if self._window is None:
            return False
        return bool(self._window.create_confirmation_dialog(
            'Clear queue', 'Remove all laps from the export queue?'
        ))
