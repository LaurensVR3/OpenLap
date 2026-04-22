"""
webview_api.py — Python API exposed to JavaScript via window.pywebview.api.

All public methods are called by JS with await window.pywebview.api.method(args).
Return values must be JSON-serialisable.
Push-events (export progress, scan updates) are sent via window.evaluate_js().
"""
from __future__ import annotations

import http.server
import logging
import mimetypes
import os
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
                parts = rng.replace('bytes=', '').split('-')
                start = int(parts[0]) if parts[0] else 0
                end   = int(parts[1]) if parts[1] else size - 1
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
        self.send_header('Access-Control-Allow-Origin', '*')
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

    # ── Called by main.py once the window is ready ────────────────────────────
    def set_window(self, window: webview.Window) -> None:
        self._window = window

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
        if hasattr(self, '_video_port'):
            return self._video_port
        try:
            server = http.server.HTTPServer(('127.0.0.1', 0), _VideoFileHandler)
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
        # Update string fields
        simple_fields = [
            'racebox_path', 'aim_path', 'motec_path', 'gpx_path',
            'telemetry_path', 'video_path', 'export_path', 'racebox_email',
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
        self._config.overlay = overlay_from_dict(data)
        self._config.save()

    def save_overlay_as(self, name: str, data: dict) -> None:
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

        from session_scanner import (
            scan_csvs, scan_videos, group_videos, match_sessions,
            scan_pending_xrk, convert_xrk_files, MatchedSession,
        )

        folder = str(Path(folder).resolve())
        video_folder = self._config.video_path or folder

        # Auto-convert any XRK files that don't yet have a CSV.
        # Progress messages are pushed to JS so the status bar stays informative.
        pending_xrk = scan_pending_xrk(folder)
        if pending_xrk:
            def _xrk_progress(msg: str) -> None:
                self._push('scan_status', message=msg)
            convert_xrk_files(folder, progress_cb=_xrk_progress)

        # Scan telemetry files (includes any CSVs just produced above)
        csv_paths = scan_csvs(folder)

        # Scan video files
        try:
            videos = scan_videos(video_folder)
        except Exception:
            videos = []

        groups = group_videos(videos)
        matches = match_sessions(csv_paths, groups)

        # Any XRK that still has no CSV (DLL missing / conversion failed) →
        # show as a pending session so the user can retry manually.
        existing_csv_paths = {m.csv_path for m in matches}
        for xrk_path, csv_path in scan_pending_xrk(folder):
            if csv_path not in existing_csv_paths:
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

        logger.info('scan_sessions: %s → %d sessions', folder, len(result))
        return result

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
            result.append({
                'csv_path':         csv,
                'source':           s.get('source', 'RaceBox'),
                'csv_start':        s.get('csv_start'),
                'matched':          s.get('matched', False),
                'needs_conversion': s.get('needs_conversion', False),
                'xrk_path':        s.get('xrk_path'),
                'video_paths':     s.get('video_paths', []),
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

            # GPX / MoTeC: need a full load but they're usually small
            if suffix in ('.gpx', '.ld'):
                session = self._load_session(csv_path)
                if not session:
                    return {'track': '', 'laps': '', 'best': '', 'best_secs': None}
                laps = getattr(session, 'laps', [])
                durs = [l.duration for l in laps if l.duration]
                best = min(durs) if durs else None
                return {
                    'track':     getattr(session, 'track', '') or '',
                    'laps':      str(len(laps)),
                    'best':      f'{best:.3f}s' if best else '',
                    'best_secs': best,
                }

            # AIM CSV: no metadata header; use filename
            if suffix == '.csv':
                track = laps_str = best_str = ''
                best_secs = None
                with open(csv_path, encoding='utf-8-sig', errors='ignore') as f:
                    first = f.readline()
                    if first.startswith('Time (s),'):
                        # AIM format — no header block
                        return {
                            'track': '',
                            'laps': '',
                            'best': '',
                            'best_secs': None,
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
                        'best': best_str, 'best_secs': best_secs}

        except Exception:
            logger.exception('get_session_meta failed for %s', csv_path)
        return {'track': '', 'laps': '', 'best': '', 'best_secs': None}

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
        self._config.session_info[csv_path] = overrides
        self._config.save()

    def bulk_rename_track(self, csv_paths: list, new_name: str) -> dict:
        """Set the track override to new_name for each path in csv_paths.

        The caller (JS) is responsible for determining which paths to rename,
        since it has access to the enriched _meta that the backend does not.
        Returns {'updated': N}.
        """
        updated = 0
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
        if not isinstance(getattr(self._config, 'track_map_selections', None), dict):
            self._config.track_map_selections = {}
        key = track_key.lower().strip()
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
        for i, s in enumerate(sessions):
            if self._auto_sync_cancel.is_set():
                break
            if self._export_thread and self._export_thread.is_alive():
                break

            csv_path = s['csv_path']
            self._push('auto_sync_progress',
                       status='processing', csv_path=csv_path,
                       current=i + 1, total=total)

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
                break

            if offset is not None:
                # Don't overwrite a user-confirmed offset that was set while we were processing
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
                bike_overrides    = self._config.bike_overrides,
                session_info      = self._config.session_info,
                log_cb            = log_cb,
                progress_cb       = progress_cb,
                done_cb           = done_cb,
                overlay_only          = params.get('overlay_only', False),
                track_map_selections  = getattr(self._config, 'track_map_selections', {}) or {},
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
            candidate = os.path.join(base, 'ffmpeg.exe')
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
    def aim_dll_status(self) -> dict:
        """Return whether the AIM MatLabXRK DLL is present."""
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
                return {'found': True, 'path': dlls[0]}
        return {'found': False, 'path': ''}

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
            import xrk_to_csv as _xrk
            import glob as _glob, sys
            from pathlib import Path
            search_dirs = [str(Path.home() / '.openlap')]
            if getattr(sys, 'frozen', False):
                search_dirs += [sys._MEIPASS, os.path.dirname(sys.executable)]
            else:
                search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
            dll_path = next(
                (d[0] for base in search_dirs
                 for d in [_glob.glob(os.path.join(base, 'MatLabXRK*.dll'))] if d),
                None
            )
            _xrk.xrk_to_csv(actual_xrk, csv_path, dll_path)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Manual video assignment ───────────────────────────────────────────────
    def assign_video(self, csv_path: str, video_path: str) -> None:
        """Manually link a video file to a telemetry session."""
        abs_csv = str(Path(csv_path).resolve())
        si = self._config.session_info.setdefault(abs_csv, {})
        si['_video_override'] = str(Path(video_path).resolve())
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
        import gpx_data, aim_data, racebox_data, motec_data
        if motec_data.is_motec_ld(csv_path):
            return motec_data.load_ld(csv_path)
        if gpx_data.is_gpx(csv_path):
            return gpx_data.load_gpx(csv_path)
        if aim_data.is_aim_csv(csv_path):
            return aim_data.load_csv(csv_path)
        return racebox_data.load_csv(csv_path)
