"""
webview_api.py — Python API exposed to JavaScript via window.pywebview.api.

All public methods are called by JS with await window.pywebview.api.method(args).
Return values must be JSON-serialisable.
Push-events (export progress, scan updates) are sent via window.evaluate_js().
"""
from __future__ import annotations

import concurrent.futures
import http.server
import math
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

from session_scanner import VIDEO_EXTENSIONS as _VIDEO_EXTS, IMAGE_EXTENSIONS as _IMAGE_EXTS
# The local file server serves videos (Data/Overlay preview) and images
# (Image/Logo gauge preview) — nothing else, and only paths the app itself
# resolved or the user picked (see _register_known_video_path).
_ALLOWED_MEDIA_EXTENSIONS = frozenset(_VIDEO_EXTS | _IMAGE_EXTS)


def _dialog_start_dir(path: str) -> str:
    """Resolve a best-effort starting directory for a file/folder picker from
    a caller-supplied path (a folder, a file, or empty/missing). Falls back
    to '' (pywebview's own default) rather than guessing when nothing
    usable is found."""
    if not path:
        return ''
    if os.path.isdir(path):
        return path
    parent = os.path.dirname(path)
    return parent if os.path.isdir(parent) else ''

# OSM way ids surfaced by track_map_cache.fetch_candidates() are always plain
# (positive) integers — the Overpass query in that module only ever queries
# `way(...)`, never `relation(...)`, for the candidate list. A leading '-' is
# allowed anyway purely as defense in depth (some OSM tooling mints negative
# synthetic ids for relations) even though this codebase never produces one.
_VALID_OSM_ID_RE = re.compile(r'^-?\d+$')

AUTO_SYNC_WORKERS = 2   # concurrent ffmpeg decodes — kept modest, CPU-heavy work

# Version of what get_session_meta() derives from a file (lap count, best
# lap). Cached entries are keyed on the file's size and date, which do not
# change when OpenLap's lap logic does: bump this whenever it changes, or an
# update keeps listing the old numbers for every unchanged file.
_META_VERSION = 2


def _version_tuple(v: str) -> tuple:
    """'v0.3.10' -> (0, 3, 10); anything after the numbers (e.g. '-dev') is ignored."""
    import re as _re
    m = _re.match(r'v?(\d+(?:\.\d+)*)', str(v).strip())
    return tuple(int(x) for x in m.group(1).split('.')) if m else ()


def _positive_int(v) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _positive_float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None

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
        ext = os.path.splitext(raw)[1].lower()
        if ext not in _ALLOWED_MEDIA_EXTENSIONS:
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
        self._channel_sync_cancel  = threading.Event()
        self._channel_sync_thread: Optional[threading.Thread] = None
        self._thread_lock      = threading.Lock()
        self._file_meta_cache: Optional[dict] = None
        # RLock (not Lock): get_session_meta wraps fetch+mutate+save of the
        # meta cache in a single `with self._meta_cache_lock:` block, and that
        # block calls _get_file_meta_cache()/_save_file_meta_cache(), which
        # each acquire this same lock again — a plain Lock would deadlock.
        self._meta_cache_lock  = threading.RLock()
        self._config_lock      = threading.Lock()
        self._video_port_lock  = threading.Lock()
        from session_loader import SessionCache
        self._session_cache    = SessionCache()

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

    def get_video_fps(self, path: str) -> float:
        """Frame rate of a video file (ffprobe), for frame-accurate stepping
        in the Data tab — an HTML video element has no frame rate, and a
        30 fps guess stepped two frames at a time on 60 fps footage and never
        landed on a frame at 25/50 fps. 0.0 if it cannot be read."""
        cache = self.__dict__.setdefault('_fps_cache', {})
        if path not in cache:
            try:
                from video_renderer import probe_video
                cache[path] = float(probe_video(path).fps)
            except Exception:
                logger.debug('get_video_fps failed for %s', path, exc_info=True)
                return 0.0
        return cache[path]

    # ── Config ────────────────────────────────────────────────────────────────
    def get_config(self) -> dict:
        cfg = asdict(self._config)
        # Inject the helper method result as a plain list
        cfg['all_telemetry_paths'] = self._config.all_telemetry_paths()
        # Presets are stored as raw JSON and only routed through
        # app_config.overlay_from_dict()'s migration when actually activated
        # (see AppConfig._from_dict) — migrate them here too so the editor's
        # live "switch preset" path (which reads straight from this dict,
        # not through overlay_from_dict()) never sees a stale gauge schema.
        from app_config import migrate_gauges
        for preset in cfg.get('presets', {}).values():
            if 'gauges' in preset:
                preset['gauges'] = migrate_gauges(preset['gauges'])
        # Image/Logo gauges preview through the local file server, which only
        # serves known paths: register every image a layout refers to.
        for layout in [cfg.get('overlay') or {}] + list(cfg.get('presets', {}).values()):
            for g in layout.get('gauges', []) or []:
                if g.get('image_path'):
                    _register_known_video_path(g['image_path'])
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
            if 'output_height' in data:
                self._config.output_height = _positive_int(data['output_height']) or 0
            if 'output_fps' in data:
                self._config.output_fps = _positive_float(data['output_fps']) or 0.0
            if 'bitrate_kbps' in data:
                self._config.bitrate_kbps = _positive_int(data['bitrate_kbps']) or 0
            # Merge dict fields (JS may send partial updates)
            if 'offsets' in data and isinstance(data['offsets'], dict):
                self._config.offsets.update(data['offsets'])
            if 'offset_sources' in data and isinstance(data['offset_sources'], dict):
                self._config.offset_sources.update(data['offset_sources'])
                # Re-confirming a session's offset by hand settles its review.
                for csv, src in data['offset_sources'].items():
                    if src == 'user' and csv in self._config.offset_review:
                        self._config.offset_review.remove(csv)
            if 'bike_overrides' in data and isinstance(data['bike_overrides'], dict):
                self._config.bike_overrides.update(data['bike_overrides'])
            if 'auto_sync_enabled' in data:
                self._config.auto_sync_enabled = bool(data['auto_sync_enabled'])
            if 'check_updates' in data:
                self._config.check_updates = bool(data['check_updates'])
            if 'secondary_source' in data and isinstance(data['secondary_source'], dict):
                self._config.secondary_source.update(data['secondary_source'])
            if 'secondary_offsets' in data and isinstance(data['secondary_offsets'], dict):
                self._config.secondary_offsets.update(data['secondary_offsets'])
            if 'secondary_offset_sources' in data and isinstance(data['secondary_offset_sources'], dict):
                self._config.secondary_offset_sources.update(data['secondary_offset_sources'])
            self._config.save()

    # ── Overlay ───────────────────────────────────────────────────────────────
    def get_overlay(self) -> dict:
        overlay = asdict(self._config.overlay)
        for g in overlay.get('gauges', []):
            if g.get('image_path'):
                _register_known_video_path(g['image_path'])
        return overlay

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

    def load_export_queue(self) -> list:
        """The export queue as it was when the app last closed."""
        import json
        from app_config import CONFIG_FILE
        try:
            with open(CONFIG_FILE.parent / 'export_queue.json', encoding='utf-8') as f:
                items = json.load(f)
            return items if isinstance(items, list) else []
        except Exception:
            return []

    def save_export_queue(self, items: list) -> None:
        from app_config import CONFIG_FILE
        from utils import write_json_atomic
        try:
            write_json_atomic(CONFIG_FILE.parent / 'export_queue.json', list(items or []))
        except Exception:
            logger.exception('Could not save the export queue')

    def list_presets(self) -> list:
        return list(self._config.presets.keys())

    def rename_preset(self, old: str, new: str) -> None:
        new = (new or '').strip()
        with self._config_lock:
            if not new or old not in self._config.presets or new in self._config.presets:
                return
            self._config.presets = {(new if k == old else k): v for k, v in self._config.presets.items()}
            if self._config.active_preset == old:
                self._config.active_preset = new
            self._config.save()

    def delete_preset(self, name: str) -> None:
        """Remove a preset. The live layout is kept: it just stops being tied
        to a preset, so the next launch reopens it instead of a deleted one."""
        with self._config_lock:
            if self._config.presets.pop(name, None) is None:
                return
            if self._config.active_preset == name:
                self._config.active_preset = ''
            self._config.save()

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

        # GoPro recordings carry their own GPS (gopro_data): one no logger
        # session was matched to becomes a session of its own, already in
        # sync with its video (offset 0, on the camera's own clock).
        claimed = {m.video_group.paths[0] for m in matches if m.matched and m.video_group}
        for g in groups:
            if g.files and g.files[0].gpmf and g.paths[0] not in claimed:
                matches.append(MatchedSession(
                    csv_path=g.paths[0], video_group=g, time_delta=0.0,
                    csv_start=g.start_time, video_start=g.start_time, matched=True,
                    source='GoPro'))

        # Load cached offsets
        offsets        = self._config.offsets
        offset_sources = self._config.offset_sources
        auto_failed    = set(self._config.auto_sync_failed)

        result = []
        for m in matches:
            csv = m.csv_path
            override = self._video_override_for(csv)
            if override:
                _register_known_video_path(override)
            result.append({
                'csv_path':         csv,
                'source':           m.source,
                'csv_start':        m.csv_start.isoformat() if m.csv_start else None,
                'matched':          True if override else m.matched,
                'needs_conversion': m.needs_conversion,
                'xrk_path':        m.xrk_path,
                'video_paths':     [override] if override
                                   else (m.video_group.paths if m.video_group else []),
                'video_override':  bool(override),
                'other_videos':    [g.paths for g in m.other_groups],
                'sync_offset':     offsets.get(csv, 0.0 if m.source == 'GoPro' else None),
                'sync_source':     offset_sources.get(csv, 'camera' if m.source == 'GoPro' else None),
                'auto_sync_failed': csv in auto_failed,
                'track':           '',
                'laps':            '',
                'best':            None,
            })

        self._migrate_offsets(result, {v.path: v.duration for v in videos})
        logger.info('scan_all_sessions: %s → %d sessions', folders, len(result))
        return result

    def _migrate_offsets(self, result: list, durations: dict) -> None:
        """Keep sync offsets valid when a rescan matches a session to
        different video than before.

        An offset is a time into the session's *first* clip, so it follows
        that clip. When matching improves (a missing chapter is added in
        front, another camera's clip is taken out, a merged neighbouring
        recording is split off) the first clip can change:
          * the old first clip is still in the list → the offset is shifted
            by the length of the clips now in front of it, and stays exact;
          * it is gone, offset auto-detected → cleared, so auto-sync redoes it;
          * it is gone, offset set by hand → kept, but marked for review
            ("check" in the Data tab) until the user confirms it again.
        """
        from app_config import load_scan_cache
        previous = {s.get('csv_path'): s.get('video_paths') or []
                    for s in load_scan_cache().get('sessions', [])}
        changed = False
        with self._config_lock:
            for r in result:
                csv, new = r['csv_path'], r.get('video_paths') or []
                old = previous.get(csv)
                off = self._config.offsets.get(csv)
                if (off is None or r.get('video_override') or not old or not new
                        or old[0] == new[0]):
                    continue
                source = self._config.offset_sources.get(csv)
                if old[0] in new:
                    shift = sum(durations.get(p, 0.0) for p in new[:new.index(old[0])])
                    self._config.offsets[csv] = off + shift
                    r['sync_offset'] = off + shift
                    logger.info('Offset for %s moved %.2fs → %.2fs: %d clip(s) now precede %s',
                                csv, off, off + shift, new.index(old[0]), os.path.basename(old[0]))
                elif source == 'auto':
                    self._config.offsets.pop(csv, None)
                    self._config.offset_sources.pop(csv, None)
                    r['sync_offset'] = r['sync_source'] = None
                    logger.info('Cleared auto offset for %s: its video is now %s', csv,
                                os.path.basename(new[0]))
                elif csv not in self._config.offset_review:
                    self._config.offset_review.append(csv)
                    logger.info('Offset for %s kept for review: video was %s, now %s', csv,
                                os.path.basename(old[0]), os.path.basename(new[0]))
                changed = True
            review = set(self._config.offset_review)
            for r in result:
                r['sync_review'] = r['csv_path'] in review
            if changed:
                self._config.save()

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
        from app_config import SCAN_CACHE_FILE
        from utils import write_json_atomic
        try:
            write_json_atomic(SCAN_CACHE_FILE, {'sessions': sessions}, indent=2)
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
            # A hand-assigned video is authoritative over the cached path,
            # which may predate the assignment. (Clearing one is the caller's
            # job: the Data page re-saves this cache right after unassigning.)
            override = self._video_override_for(csv)
            vpaths = [override] if override else s.get('video_paths', [])
            # Re-register on every cache load (not just live scans) so playback
            # still works for a session restored from disk before any rescan
            # has run in this process.
            for vp in vpaths:
                _register_known_video_path(vp)
            result.append({
                'csv_path':         csv,
                'source':           s.get('source', 'RaceBox'),
                'csv_start':        s.get('csv_start'),
                'matched':          True if override else s.get('matched', False),
                'needs_conversion': s.get('needs_conversion', False),
                'xrk_path':        s.get('xrk_path'),
                'video_paths':     vpaths,
                'video_override':  bool(override),
                'sync_offset':     offsets.get(csv, 0.0 if s.get('source') == 'GoPro' else None),
                'sync_source':     offset_sources.get(csv, 'camera' if s.get('source') == 'GoPro' else None),
                'sync_review':     csv in self._config.offset_review,
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
            from session_scanner import VIDEO_EXTENSIONS
            if suffix in ('.gpx', '.ld', '.vbo', '.uni', '.tsv') or suffix in VIDEO_EXTENSIONS:
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
                    if (stat and entry and entry.get('size') == stat[0] and entry.get('mtime') == stat[1]
                            and entry.get('v') == _META_VERSION):
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
                        meta_cache[csv_path] = {'size': stat[0], 'mtime': stat[1], 'data': result,
                                                'v': _META_VERSION}
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
                    # Generic dynamic channels (see channel_discovery.py)
                    **p.extra,
                }
                points.append(d)
            return points
        except Exception as e:
            logger.exception('load_lap_history failed for %s lap %d: %s', csv_path, lap_idx, e)
            return []

    def get_reference_preview(self, csv_path: str, lap_idx: int, ref_mode: str,
                              ref_lap_csv_path: str = '', ref_lap_num: int = 0) -> dict:
        """Reference-lap data for the editor preview of one lap, per
        telemetry sample of that lap: the same delta, reference traces,
        sectors and ghost track the export computes per frame (see
        video_renderer.delta_and_reference_at), so Delta, Compare, Splits,
        Sector Bar and the maps' ghost dot preview with real data.

        Returns {ok, desc, delta[], ref{channel: []}, sectors[], ref_lats[],
        ref_lons[], ref_duration} or {ok: False, desc}.
        """
        try:
            if not ref_mode or ref_mode == 'none':
                return {'ok': False, 'desc': 'none'}
            from reference_resolver import resolve_reference_lap
            from app_config import load_scan_cache
            import video_renderer as vr
            session = self._load_session(csv_path)
            if not session or not (0 <= int(lap_idx) < len(session.laps)):
                return {'ok': False, 'desc': 'no such lap'}
            lap = session.laps[int(lap_idx)]
            ref, desc = resolve_reference_lap(
                ref_mode=ref_mode, sess=session, session_info=dict(self._config.session_info),
                scan_cache=load_scan_cache(), ref_lap_csv_path=ref_lap_csv_path or '',
                ref_lap_num=int(ref_lap_num or 0), current_lap_num=lap.lap_num,
                load_session_fn=self._load_session)
            if ref is None:
                return {'ok': False, 'desc': desc}
            dt_state = vr._setup_delta_time(ref, vr.RenderJob('', lap), session)
            delta, channels = [], {k: [] for k in vr._REF_KEYS}
            for p in lap.points:
                d, rp = vr.delta_and_reference_at(dt_state, p.lap, p.lap_elapsed)
                delta.append(d)
                for k in vr._REF_KEYS:
                    channels[k].append(rp[k] if rp else 0.0)
            lats, lons = vr.reference_map_track(ref)
            return {'ok': True, 'desc': desc, 'delta': delta, 'ref': channels,
                    'sectors': [{**s, 'boundary_elapsed': (s['boundary_elapsed']
                                 if math.isfinite(s['boundary_elapsed']) else None)}
                                for s in dt_state['sectors']],
                    'ref_lats': lats, 'ref_lons': lons, 'ref_duration': ref.duration}
        except Exception:
            logger.exception('get_reference_preview failed for %s lap %s', csv_path, lap_idx)
            return {'ok': False, 'desc': 'error'}

    def set_second_camera(self, csv_path: str, paths: list) -> dict:
        """Use *paths* (another camera's recording of this session) for Video
        gauges; syncs them to the main video by audio in the background and
        pushes second_camera_sync {csv_path, offset, confidence}."""
        paths = [str(Path(p).resolve()) for p in paths or [] if p]
        for p in paths:
            _register_known_video_path(p)
        with self._config_lock:
            if not paths:
                self._config.second_camera.pop(csv_path, None)
                self._config.save()
                return {}
            self._config.second_camera[csv_path] = {'paths': paths, 'offset': None, 'source': 'auto'}
            self._config.save()
        main = self._main_video_paths(csv_path)

        def _sync():
            from auto_sync import audio_offset
            try:
                offset, conf = audio_offset(main, paths) if main else (None, 0.0)
            except Exception:
                logger.exception('Second camera sync failed for %s', csv_path)
                offset, conf = None, 0.0
            with self._config_lock:
                cam = self._config.second_camera.get(csv_path)
                if cam and cam.get('source') != 'user' and cam.get('paths') == paths:
                    cam['offset'] = offset
                    self._config.save()
            self._push('second_camera_sync', csv_path=csv_path, offset=offset, confidence=conf)
        threading.Thread(target=_sync, daemon=True).start()
        return self._config.second_camera[csv_path]

    def set_second_camera_offset(self, csv_path: str, offset: float) -> None:
        """Set the second camera's offset by hand (seconds of main video at
        which the second recording starts)."""
        with self._config_lock:
            cam = self._config.second_camera.get(csv_path)
            if cam:
                cam['offset'] = float(offset)
                cam['source'] = 'user'
                self._config.save()

    def _main_video_paths(self, csv_path: str) -> list:
        from app_config import load_scan_cache
        override = self._video_override_for(csv_path)
        if override:
            return [override]
        entry = next((s for s in load_scan_cache().get('sessions', []) if s.get('csv_path') == csv_path), {})
        return entry.get('video_paths') or []

    def get_video_layers(self, csv_path: str, lap_idx: int, layout: dict) -> list:
        """The editor preview's second videos for one lap: [{gauge_idx,
        source, clips: [{path, start, duration}], offset}] with layer time =
        main video time + offset — the same numbers the export uses."""
        try:
            from app_config import load_scan_cache
            from reference_resolver import resolve_reference_lap
            from video_layers import layers_for
            from video_renderer import probe_video
            s = self._load_session(csv_path)
            lap = s.laps[int(lap_idx)] if 0 <= int(lap_idx) < len(s.laps) else None
            scan_cache = load_scan_cache()
            ref = None
            if (layout or {}).get('ref_mode', 'none') not in ('none', ''):
                ref, _ = resolve_reference_lap(
                    ref_mode=layout['ref_mode'], sess=s, session_info=dict(self._config.session_info),
                    scan_cache=scan_cache, ref_lap_csv_path=layout.get('ref_lap_csv_path', ''),
                    ref_lap_num=int(layout.get('ref_lap_num') or 0),
                    current_lap_num=lap.lap_num if lap else None, load_session_fn=self._load_session)
            layers = layers_for(csv_path, lap, layout, self._config.offsets.get(csv_path, 0.0),
                                self._config.second_camera, self._config.offsets, scan_cache, ref)
            for layer in layers:
                t, clips = 0.0, []
                for p in layer['clips']:
                    _register_known_video_path(p)
                    d = probe_video(p).duration
                    clips.append({'path': p, 'start': t, 'duration': d})
                    t += d
                layer['clips'] = clips
            return layers
        except Exception:
            logger.exception('get_video_layers failed for %s', csv_path)
            return []

    def get_track_lines(self, csv_path: str) -> dict:
        """The session's GPS outline (one lap, for drawing) and the lines its
        laps are cut at: the user's set if it runs through one, else the
        automatic one. {outline: {lats, lons}, finish, sectors, user}."""
        try:
            import lap_detection
            s = self._load_session(csv_path)
            lap = s.fastest_lap or (s.laps[0] if s.laps else None)
            pts = lap.points if lap else s.all_points
            step = max(1, len(pts) // 800)
            outline = {'lats': [p.lat for p in pts[::step] if p.lat],
                       'lons': [p.lon for p in pts[::step] if p.lat]}
            if s.finish_line:
                return {'outline': outline, 'finish': s.finish_line,
                        'sectors': s.sector_lines, 'user': True}
            line = (lap_detection.finish_line_from_laps(s.all_points)
                    or lap_detection.auto_finish_line(s.all_points))
            return {'outline': outline, 'finish': line.to_dict() if line else None,
                    'sectors': [], 'user': False}
        except Exception:
            logger.exception('get_track_lines failed for %s', csv_path)
            return {'outline': {'lats': [], 'lons': []}, 'finish': None, 'sectors': [], 'user': False}

    def set_track_line(self, csv_path: str, kind: str, lat: float = 0.0, lon: float = 0.0) -> dict:
        """Change the start/finish or sector lines of the circuit *csv_path*
        runs on. kind: 'finish' (place it at lat/lon), 'sector' (add one at
        lat/lon), 'clear_sectors', or 'reset' (drop the user lines: laps come
        from the logger, or automatic detection, again). The line is square
        to the track at the point nearest the click. Returns get_track_lines()."""
        import lap_detection
        s = self._load_one_session(csv_path)
        with self._config_lock:
            sets = self._config.track_lines
            current = lap_detection.pick_line_set(s.all_points, sets)
            if kind == 'reset':
                if current in sets:
                    sets.remove(current)
            elif kind == 'clear_sectors':
                if current:
                    current['sectors'] = []
            elif kind in ('finish', 'sector'):
                line = lap_detection.line_at(s.all_points, float(lat), float(lon))
                if line is None:
                    return self.get_track_lines(csv_path)
                if kind == 'finish':
                    if current:
                        current['finish'] = line.to_dict()
                    else:
                        sets.append({'finish': line.to_dict(), 'sectors': []})
                else:
                    if current is None:
                        auto = (lap_detection.finish_line_from_laps(s.all_points)
                                or lap_detection.auto_finish_line(s.all_points))
                        if auto is None:
                            return self.get_track_lines(csv_path)
                        current = {'finish': auto.to_dict(), 'sectors': []}
                        sets.append(current)
                    current['sectors'].append(line.to_dict())
                    self._order_sectors(s, current)
            self._config.save()
        self._session_cache.clear()
        with self._meta_cache_lock:          # cached lap counts/best laps are stale now
            self._get_file_meta_cache()['meta'].clear()
            self._save_file_meta_cache()
        return self.get_track_lines(csv_path)

    @staticmethod
    def _order_sectors(session, line_set: dict) -> None:
        """Keep sector lines in driving order: by when a lap crosses each."""
        import lap_detection
        probe = lap_detection.FinishLine.from_dict(line_set['finish'])
        cr = lap_detection.crossings(session.all_points, probe)
        if len(cr) < 2:
            return
        lap_pts = [p for p in session.all_points if cr[0] <= p.elapsed < cr[1]]

        def when(d):
            c = lap_detection.crossings(lap_pts, lap_detection.FinishLine.from_dict(d), min_lap_s=1e9)
            return c[0] if c else float('inf')
        line_set['sectors'].sort(key=when)

    def list_session_channels(self, csv_path: str) -> list:
        """Return the gauge-selectable channels available in *one*
        telemetry file (no secondary-source merge applied) — used by the
        Data-tab channel-mapping picker, which needs to know what each file
        independently offers before deciding how to combine them."""
        try:
            import channel_discovery
            session = self._session_cache.get(csv_path, loader=lambda p: self._load_one_session(p))
            return channel_discovery.list_channels(session)
        except Exception:
            logger.exception('list_session_channels failed for %s', csv_path)
            return []

    def get_available_channels(self, csv_path: str) -> list:
        """Return the gauge-selectable channels available in the (possibly
        secondary-source-merged) session for *csv_path* — used by the
        Overlay editor's gauge-type picker."""
        try:
            import channel_discovery
            session = self._load_session(csv_path)
            return channel_discovery.list_channels(session)
        except Exception:
            logger.exception('get_available_channels failed for %s', csv_path)
            return []

    # ── File dialogs ──────────────────────────────────────────────────────────
    def open_folder_dialog(self, start_dir: str = '') -> Optional[str]:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=_dialog_start_dir(start_dir),
        )
        if result:
            return str(Path(result[0]).resolve())
        return None

    def open_file_dialog(self, filters: list = None, start_dir: str = '') -> Optional[str]:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=filters or [],
            directory=_dialog_start_dir(start_dir),
        )
        if result:
            picked = str(Path(result[0]).resolve())
            # The user chose it: fine to preview (the server still checks the
            # extension, so this only ever admits a video or an image).
            _register_known_video_path(picked)
            return picked
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
        # Always tell the UI the run is over. If this thread dies without
        # sending auto_sync_done (e.g. scipy missing from a release build made
        # the import below fail) the Data page shows "auto-syncing…" forever.
        error = ''
        try:
            self._auto_sync_sessions(sessions)
        except Exception as e:
            logger.exception('Auto-sync failed')
            error = f'Auto-sync failed: {e}'
        self._push('auto_sync_done', error=error)

    def _auto_sync_sessions(self, sessions: list) -> None:
        from auto_sync import (run_auto_sync, CONFIDENCE_THRESHOLD,
                               MIN_CONFIDENCE)

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

            # Every event carries the session it belongs to and the
            # thresholds it is judged against. The UI used to latch those from
            # the first 'processing' event into page-local state, which reset
            # whenever the Data page was reopened (showing "session 0 of 0")
            # and was shared between the two sessions syncing concurrently, so
            # the count did not identify whose confidence was being reported.
            # The thresholds travel too rather than being repeated as a
            # literal in the JS, where the displayed one had already drifted
            # away from the real acceptance floor.
            def _progress(vid_t, offset, conf, _csv=csv_path, _idx=idx):
                self._push('auto_sync_progress',
                           status='checking', csv_path=_csv,
                           current=_idx, total=total,
                           vid_t=vid_t, offset=offset, confidence=conf,
                           early_exit_confidence=CONFIDENCE_THRESHOLD,
                           min_confidence=MIN_CONFIDENCE)

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
                                   current=idx, total=total,
                                   offset=offset, confidence=confidence)
                else:
                    if csv_path not in self._config.auto_sync_failed:
                        self._config.auto_sync_failed.append(csv_path)
                    self._config.save()
                    self._push('auto_sync_progress',
                               status='failed', csv_path=csv_path,
                               current=idx, total=total,
                               confidence=confidence)

        with concurrent.futures.ThreadPoolExecutor(max_workers=AUTO_SYNC_WORKERS) as ex:
            list(ex.map(_process, sessions))

    # ── Secondary telemetry sync (multi-channel cross-correlation) ──────────────
    def start_channel_sync(self, sessions: list) -> dict:
        """Start background cross-correlation sync for sessions that have a
        secondary telemetry source assigned but no offset yet — tries every
        channel both files have usable data for (RPM, G-force, Speed,
        Altitude) and keeps whichever gives the best match (see
        auto_sync.correlate_channels).

        Returns {'queued': N}.
        """
        with self._thread_lock:
            if self._export_thread and self._export_thread.is_alive():
                return {'queued': 0}
            if self._channel_sync_thread and self._channel_sync_thread.is_alive():
                return {'queued': 0}

        # secondary_sync_failed is intentionally NOT checked here — unlike
        # start_auto_sync() (which runs automatically across every session
        # after a scan, where re-trying known-bad ones every time would be
        # wasteful), this is only ever invoked as an explicit single-session
        # "Auto-sync" button click. A prior failure must never silently
        # block a user-initiated retry.
        eligible = [
            s for s in sessions
            if self._config.secondary_source.get(s.get('csv_path', ''))
            and self._config.secondary_offsets.get(s['csv_path']) is None
        ]
        if not eligible:
            return {'queued': 0}

        self._channel_sync_cancel.clear()
        self._channel_sync_thread = threading.Thread(
            target=self._run_channel_sync_bg,
            args=(eligible,),
            daemon=True,
        )
        self._channel_sync_thread.start()
        return {'queued': len(eligible)}

    def cancel_channel_sync(self) -> None:
        self._channel_sync_cancel.set()

    def _run_channel_sync_bg(self, sessions: list) -> None:
        # Same guarantee as _run_auto_sync_bg: the UI must always get a done event.
        error = ''
        try:
            self._channel_sync_sessions(sessions)
        except Exception as e:
            logger.exception('Channel sync failed')
            error = f'Sync failed: {e}'
        self._push('channel_sync_done', error=error)

    def _channel_sync_sessions(self, sessions: list) -> None:
        from auto_sync import correlate_channels, MIN_CONFIDENCE
        from session_scanner import _csv_source

        total = len(sessions)
        progress_lock = threading.Lock()
        started = 0

        def _process(s: dict) -> None:
            nonlocal started
            if self._channel_sync_cancel.is_set():
                return
            if self._export_thread and self._export_thread.is_alive():
                return

            csv_path = s['csv_path']
            secondary_path = self._config.secondary_source.get(csv_path)
            if not secondary_path or not os.path.isfile(secondary_path):
                return

            with progress_lock:
                started += 1
                idx = started
            self._push('channel_sync_progress',
                       status='processing', csv_path=csv_path,
                       current=idx, total=total)

            try:
                offset, confidence, channel = correlate_channels(
                    primary_csv       = csv_path,
                    secondary_csv     = secondary_path,
                    primary_source    = s.get('source', 'RaceBox'),
                    secondary_source  = _csv_source(secondary_path),
                )
            except Exception:
                logger.exception('Channel sync failed for %s / %s', csv_path, secondary_path)
                offset, confidence, channel = 0.0, 0.0, ''

            if self._channel_sync_cancel.is_set():
                return

            with self._config_lock:
                if confidence >= MIN_CONFIDENCE:
                    if self._config.secondary_offset_sources.get(csv_path) != 'user':
                        self._config.secondary_offsets[csv_path]        = offset
                        self._config.secondary_offset_sources[csv_path] = 'auto'
                        if csv_path in self._config.secondary_sync_failed:
                            self._config.secondary_sync_failed.remove(csv_path)
                        self._config.save()
                        self._push('channel_sync_progress',
                                   status='done', csv_path=csv_path,
                                   offset=offset, confidence=confidence, channel=channel)
                else:
                    if csv_path not in self._config.secondary_sync_failed:
                        self._config.secondary_sync_failed.append(csv_path)
                    self._config.save()
                    self._push('channel_sync_progress',
                               status='failed', csv_path=csv_path,
                               confidence=confidence)

        with concurrent.futures.ThreadPoolExecutor(max_workers=AUTO_SYNC_WORKERS) as ex:
            list(ex.map(_process, sessions))

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
                # Merged into each session exactly as the editor preview does,
                # so gauges bound to a secondary file's channels export too.
                track_lines           = [dict(t) for t in self._config.track_lines],
                second_camera         = {k: dict(v) for k, v in self._config.second_camera.items()},
                offsets               = dict(self._config.offsets),
                secondary_source      = dict(self._config.secondary_source),
                secondary_offsets     = dict(self._config.secondary_offsets),
                output_height         = _positive_int(params.get('output_height')),
                output_fps            = _positive_float(params.get('output_fps')),
                bitrate_kbps          = _positive_int(params.get('bitrate_kbps')) or 0,
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
                from utils import _popen
                proc = _popen(      # no console window flashing up on Windows
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
        Returns {version, ffmpeg_path, encoders: [{name, label, available,
        detail}]} or {error} when FFmpeg itself could not be run.

        Reports the *reason* for a failure rather than a cheerful "unknown".
        A broken-but-present FFmpeg used to render as version "unknown" with
        every encoder unavailable, which reads like "this machine has no
        encoders" instead of "FFmpeg is not working" (issue #20).
        """
        from utils import _run, ffmpeg_path
        from exceptions import FFmpegNotFoundError

        ffmpeg_bin = ffmpeg_path()

        def _ff(args, timeout):
            """Run FFmpeg, returning (returncode, stdout, stderr)."""
            r = _run([ffmpeg_bin] + args, text=True, timeout=timeout)
            return r.returncode, (r.stdout or ''), (r.stderr or '')

        try:
            rc, out, err = _ff(['-hide_banner', '-version'], 10)
        except FFmpegNotFoundError as e:
            return {'error': str(e)}
        except Exception as e:
            return {'error': f'Could not run FFmpeg at {ffmpeg_bin}: {e}'}

        if rc != 0:
            detail = (err or out).strip().splitlines()
            return {'error': f'FFmpeg at {ffmpeg_bin} exited with code {rc}: '
                             f'{detail[0] if detail else "no output"}'}

        first = out.splitlines()[0] if out else ''
        if 'version' not in first:
            return {'error': f'{ffmpeg_bin} ran but did not report a version, so it is '
                             f'probably not FFmpeg. First line of output: '
                             f'{first.strip()[:120] or "(nothing)"}'}
        version = first.split('version')[-1].strip().split(' ')[0]

        candidates = [
            ('libx264',           'H.264 software'),
            ('libx265',           'H.265 software'),
            ('h264_nvenc',        'H.264 NVIDIA NVENC'),
            ('hevc_nvenc',        'H.265 NVIDIA NVENC'),
            ('h264_videotoolbox', 'H.264 Apple VideoToolbox'),
            ('h264_amf',          'H.264 AMD AMF'),
            ('h264_qsv',          'H.264 Intel QSV'),
        ]

        # What this build was compiled with. Definitive for "absent", but not
        # for "works": nvenc is compiled into most builds and still fails
        # without the matching hardware.
        built_in: set = set()
        try:
            rc_e, out_e, _ = _ff(['-hide_banner', '-encoders'], 15)
            if rc_e == 0:
                for line in out_e.splitlines():
                    parts = line.split()
                    # " V....D name   Description"
                    if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in 'VAS':
                        built_in.add(parts[1])
        except Exception:
            logger.debug('check_encoders: -encoders listing failed', exc_info=True)

        # The functional probe needs the lavfi input to synthesise a source.
        # Builds without it would otherwise fail every probe and report the
        # whole machine as having no encoders at all, software ones included.
        try:
            probe_usable = _ff(
                ['-hide_banner', '-f', 'lavfi', '-i', 'nullsrc=s=64x64:d=0.1',
                 '-f', 'null', '-'], 8)[0] == 0
        except Exception:
            probe_usable = False

        def _probe(enc):
            try:
                return _ff(['-hide_banner', '-f', 'lavfi', '-i', 'nullsrc=s=64x64:d=0.1',
                            '-vcodec', enc, '-f', 'null', '-'], 8)[0] == 0
            except Exception:
                return False

        encoders = []
        for name, label in candidates:
            if built_in and name not in built_in:
                encoders.append({'name': name, 'label': label, 'available': False,
                                 'detail': 'not in this FFmpeg build'})
            elif probe_usable:
                ok = _probe(name)
                encoders.append({'name': name, 'label': label, 'available': ok,
                                 'detail': '' if ok else 'present but failed to encode'})
            else:
                # Cannot test for real; report what the build claims.
                listed = name in built_in
                encoders.append({'name': name, 'label': label, 'available': listed,
                                 'detail': 'in this build (not verified)' if listed
                                           else 'not in this FFmpeg build'})

        return {'version': version, 'ffmpeg_path': ffmpeg_bin, 'encoders': encoders}

    # ── Updates ────────────────────────────────────────────────────────────────
    RELEASES_API = 'https://api.github.com/repos/LaurensVR3/OpenLap/releases/latest'

    def check_for_update(self) -> dict:
        """{newer, latest, url} from GitHub's latest release, or {} when the
        check is switched off, offline, or fails. One unauthenticated GET of
        public release information; nothing about the user is sent."""
        if not self._config.check_updates:
            return {}
        import json
        import urllib.request
        from _version import __version__
        try:
            req = urllib.request.Request(self.RELEASES_API, headers={
                'Accept': 'application/vnd.github+json', 'User-Agent': f'OpenLap/{__version__}'})
            with urllib.request.urlopen(req, timeout=5) as r:
                rel = json.load(r)
        except Exception:
            logger.debug('Update check failed', exc_info=True)
            return {}
        tag = str(rel.get('tag_name') or '')
        return {'newer': _version_tuple(tag) > _version_tuple(__version__),
                'latest': tag.lstrip('v'), 'url': rel.get('html_url') or ''}

    def open_url(self, url: str) -> None:
        """Open a web page in the system browser (release notes)."""
        if not str(url).startswith('https://'):
            return
        import webbrowser
        webbrowser.open(url)

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

    def unassign_video(self, csv_path: str) -> None:
        """Undo assign_video() — drop the manual video link for a session.

        Leaves everything else about the session alone; a rescan is then free
        to match it to a video automatically again, exactly as if it had never
        been assigned by hand.
        """
        with self._config_lock:
            for key in self._session_info_keys(csv_path):
                si = self._config.session_info.get(key)
                if not si:
                    continue
                si.pop('_video_override', None)
                if not si:                       # nothing else was overridden
                    self._config.session_info.pop(key, None)
            self._config.save()

    def _session_info_keys(self, csv_path: str) -> list:
        """Both spellings a session may be keyed under in config.

        Offsets are written from JS with the path exactly as the scan produced
        it, while assign_video() resolves it first. They are normally the same
        string, but a session reached through a different spelling (a mapped
        drive, a UNC path, a symlinked folder) would otherwise leave a stale
        entry behind that no later lookup can find.
        """
        keys = [csv_path]
        try:
            resolved = str(Path(csv_path).resolve())
            if resolved != csv_path:
                keys.append(resolved)
        except OSError:
            pass
        return keys

    def _video_override_for(self, csv_path: str) -> Optional[str]:
        """The manually assigned video for a session, or None.

        Applied on every scan and cache load so a hand-assigned video survives
        a rescan — without this the assignment lives only in the scan cache and
        the next scan silently reverts it to whatever automatic matching finds.
        """
        for key in self._session_info_keys(csv_path):
            override = (self._config.session_info.get(key) or {}).get('_video_override')
            if override:
                return override
        return None

    # ── Sync offset ───────────────────────────────────────────────────────────
    def clear_offset(self, csv_path: str) -> None:
        """Forget a session's sync offset, whether set by hand or auto-detected.

        Needs its own method because save_config() merges dict fields, so JS
        can overwrite an offset but never remove one. Also clears the
        auto-sync failure marker, so the session goes back to being a
        candidate for auto-sync rather than staying permanently skipped.
        """
        with self._config_lock:
            for key in self._session_info_keys(csv_path):
                self._config.offsets.pop(key, None)
                self._config.offset_sources.pop(key, None)
                while key in self._config.auto_sync_failed:
                    self._config.auto_sync_failed.remove(key)
                while key in self._config.offset_review:
                    self._config.offset_review.remove(key)
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
    def _load_one_session(csv_path: str):
        """Load a single telemetry file, auto-detecting its format. Does not
        apply any secondary-source merge — use _load_session() for that."""
        from session_loader import load_file
        return load_file(csv_path)

    def _load_session(self, csv_path: str):
        """Load a telemetry file, transparently merging in a secondary source
        if one has been assigned to it (see session_merge.merge_sessions).
        Every UI endpoint (get_session_meta, get_laps, load_lap_history,
        channels, track map) goes through this. Export loads the same way via
        session_loader.load_merged, uncached because it mutates sessions.

        Cached: the returned Session is shared, so callers must not mutate it.
        """
        return self._session_cache.get(
            csv_path,
            self._config.secondary_source.get(csv_path),
            self._config.secondary_offsets.get(csv_path, 0.0),
            loader=lambda p: self._load_one_session(p),
            track_lines=self._config.track_lines,
        )

    def confirm_clear_queue(self) -> bool:
        if self._window is None:
            return False
        return bool(self._window.create_confirmation_dialog(
            'Clear queue', 'Remove all laps from the export queue?'
        ))
