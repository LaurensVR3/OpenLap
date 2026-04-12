"""
webview_api.py — Python API exposed to JavaScript via window.pywebview.api.

All public methods are called by JS with await window.pywebview.api.method(args).
Return values must be JSON-serialisable.
Push-events (export progress, scan updates) are sent via window.evaluate_js().
"""
from __future__ import annotations

import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import webview

from app_config import AppConfig, overlay_from_dict, load_scan_cache, save_scan_cache


class WebviewAPI:
    """
    One instance of this class is created in main.py and passed to
    webview.create_window(js_api=api).  Every public method becomes
    callable from JavaScript as: await window.pywebview.api.<method>(...)
    """

    def __init__(self):
        self._config: AppConfig = AppConfig.load()
        self._window: Optional[webview.Window] = None
        self._export_cancel = threading.Event()
        self._export_thread: Optional[threading.Thread] = None

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
        # Merge dict fields (JS may send partial updates)
        if 'offsets' in data and isinstance(data['offsets'], dict):
            self._config.offsets.update(data['offsets'])
        if 'bike_overrides' in data and isinstance(data['bike_overrides'], dict):
            self._config.bike_overrides.update(data['bike_overrides'])
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
            scan_csvs, scan_videos, group_videos, match_sessions, scan_pending_xrk
        )

        folder = str(Path(folder).resolve())
        video_folder = self._config.video_path or folder

        # Scan telemetry files
        csv_paths = scan_csvs(folder)

        # Scan video files
        try:
            videos = scan_videos(video_folder)
        except Exception:
            videos = []

        groups = group_videos(videos)
        matches = match_sessions(csv_paths, groups)

        # Load cached offsets
        offsets = self._config.offsets

        result = []
        for m in matches:
            result.append({
                'csv_path':       m.csv_path,
                'source':         m.source,
                'csv_start':      m.csv_start.isoformat() if m.csv_start else None,
                'matched':        m.matched,
                'needs_conversion': m.needs_conversion,
                'xrk_path':       m.xrk_path,
                'video_paths':    m.video_group.paths if m.video_group else [],
                'sync_offset':    offsets.get(m.csv_path),
                'track':          '',
                'laps':           '',
                'best':           None,
            })

        # Save cache for next startup
        try:
            save_scan_cache(folder, video_folder, matches, {})
        except Exception:
            pass

        return result

    def _cached_sessions(self) -> list:
        """Return cached sessions from disk without rescanning."""
        cache = load_scan_cache()
        sessions = cache.get('sessions', [])
        offsets  = self._config.offsets
        result = []
        for s in sessions:
            result.append({
                'csv_path':       s.get('csv_path', ''),
                'source':         s.get('source', 'RaceBox'),
                'csv_start':      s.get('csv_start'),
                'matched':        s.get('matched', False),
                'needs_conversion': s.get('needs_conversion', False),
                'xrk_path':       s.get('xrk_path'),
                'video_paths':    s.get('video_paths', []),
                'sync_offset':    offsets.get(s.get('csv_path', '')),
                'track':          s.get('track', ''),
                'laps':           s.get('laps', ''),
                'best':           s.get('best') or None,
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
            pass
        return {'track': '', 'laps': '', 'best': '', 'best_secs': None}

    # ── Lap loading ───────────────────────────────────────────────────────────
    def get_laps(self, csv_path: str) -> list:
        """Return lap list for a session: [{lap_idx, duration, is_best}]."""
        try:
            session = self._load_session(csv_path)
            if not session or not session.laps:
                return []

            best_dur = min((l.duration for l in session.laps if l.duration), default=None)
            result = []
            for i, lap in enumerate(session.laps):
                result.append({
                    'lap_idx':  i,
                    'duration': lap.duration,
                    'is_best':  (lap.duration is not None and best_dur is not None
                                 and abs(lap.duration - best_dur) < 0.001),
                })
            return result
        except Exception as e:
            return []

    def load_lap_history(self, csv_path: str, lap_idx: int) -> list:
        """Return telemetry data points for one lap as a list of dicts."""
        try:
            from utils import compute_lean_angle
            session = self._load_session(csv_path)
            if not session or lap_idx >= len(session.laps):
                return []
            lap = session.laps[lap_idx]
            points = []
            for p in lap.data_points:
                d = {
                    't':           p.elapsed,
                    'speed':       p.speed_kmh,
                    'gx':          p.gforce_lon,
                    'gy':          p.gforce_lat,
                    'rpm':         p.rpm or 0,
                    'exhaust_temp': p.exhaust_temp or 0,
                    'alt':         p.altitude or 0,
                    'lat':         p.lat,
                    'lon':         p.lon,
                }
                # Add lean angle if available
                if hasattr(p, 'lean'):
                    d['lean'] = p.lean
                points.append(d)
            return points
        except Exception:
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

    # ── Export ────────────────────────────────────────────────────────────────
    def start_export(self, params: dict) -> None:
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

    def _run_export_bg(self, params: dict) -> None:
        from export_runner import run_export

        def log_cb(msg):
            self._push('export_log', message=msg)

        def progress_cb(pct, msg=''):
            self._push('export_progress', value=pct, message=msg)

        def done_cb(ok, msg=''):
            self._push('export_done', ok=ok, message=msg)

        try:
            run_export(
                items         = params.get('items', []),
                scope         = params.get('scope', 'fastest'),
                export_path   = params.get('export_path', ''),
                encoder       = params.get('encoder', 'libx264'),
                crf           = params.get('crf', 18),
                workers       = params.get('workers', 4),
                padding       = params.get('padding', 5.0),
                is_bike       = params.get('is_bike', False),
                show_map      = params.get('show_map', True),
                show_tel      = params.get('show_tel', True),
                layout        = params.get('layout', {}),
                clip_start_s  = params.get('clip_start_s', 0.0),
                clip_end_s    = params.get('clip_end_s', 0.0),
                ref_mode      = params.get('ref_mode', 'none'),
                ref_lap_obj   = None,
                bike_overrides = self._config.bike_overrides,
                session_info  = self._config.session_info,
                log_cb        = log_cb,
                progress_cb   = progress_cb,
                done_cb       = done_cb,
            )
        except Exception as e:
            done_cb(False, str(e))

    # ── RaceBox cloud ─────────────────────────────────────────────────────────
    def racebox_login(self, email: str, password: str) -> dict:
        """Test RaceBox cloud credentials. Returns {ok: bool, error?: str}."""
        try:
            from racebox_downloader import RaceBoxSource
            src = RaceBoxSource(email=email, password=password)
            src.test_login()
            self._config.racebox_email = email
            self._config.save()
            return {'ok': True}
        except ImportError:
            return {'ok': False, 'error': 'racebox_downloader not available'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Encoder detection ──────────────────────────────────────────────────────
    def check_encoders(self) -> dict:
        """
        Probe FFmpeg and report which video encoders are available.
        Returns {version, encoders: [{name, label, available}]} or {error}.
        """
        import subprocess, shutil, os, sys

        ffmpeg_bin = shutil.which('ffmpeg')
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
        return {
            'python': f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
            'config': str(CONFIG_FILE),
        }

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
