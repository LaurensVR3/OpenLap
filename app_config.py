# app_config.py — persistent application configuration
# Saved to ~/.openlap/config.json

from __future__ import annotations
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# Guards the config.json read-modify-write in AppConfig.save() so a debounced
# schedule_save() timer firing on a background thread (e.g. after auto_sync
# writes an offset) can't race a normal user-driven save() and tear the file.
_config_save_lock = threading.Lock()

CONFIG_FILE          = Path.home() / '.openlap' / 'config.json'
SCAN_CACHE_FILE      = Path.home() / '.openlap' / 'scan_cache.json'
FILE_META_CACHE_FILE = Path.home() / '.openlap' / 'file_meta_cache.json'
_OLD_CONFIG_V2  = Path.home() / '.telemetry_overlay' / 'config.json'
_OLD_CONFIG_V1  = Path.home() / '.racebox_studio'    / 'config.json'


@dataclass
class OverlayElement:
    """One overlay element with normalized position/size (0..1 of video dimensions)."""
    visible: bool = True
    x: float = 0.0   # left edge as fraction of video width
    y: float = 0.0   # top edge as fraction of video height
    w: float = 0.25  # width as fraction of video width
    h: float = 0.25  # height as fraction of video height


def _default_gauges() -> List[dict]:
    """Return the built-in default gauge layout as plain dicts."""
    return [
        {'type': 'Circuit', 'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
        {'type': 'Dial',    'channel': 'speed',      'visible': True, 'x': 0.01, 'y': 0.74, 'w': 0.13, 'h': 0.23},
        {'type': 'Bar',     'channel': 'gforce_lat', 'visible': True, 'x': 0.15, 'y': 0.74, 'w': 0.10, 'h': 0.23},
        {'type': 'Bar',     'channel': 'gforce_lon', 'visible': True, 'x': 0.26, 'y': 0.74, 'w': 0.10, 'h': 0.23},
        {'type': 'Numeric', 'channel': 'lap_time',   'visible': True, 'x': 0.37, 'y': 0.74, 'w': 0.13, 'h': 0.23},
    ]


@dataclass
class OverlayLayout:
    is_bike:          bool       = False
    theme:            str        = 'Dark'
    ref_mode:         str        = 'none'   # 'none' | 'session_best' | 'personal_best' | 'day_best' | 'session_best_so_far' | 'manual'
    ref_lap_csv_path: str        = ''       # used when ref_mode='manual'
    ref_lap_num:      int        = 0        # used when ref_mode='manual'
    gauges:           List[dict] = field(default_factory=_default_gauges)


@dataclass
class AppConfig:
    # Per-source telemetry directories (preferred)
    racebox_path:   str = ""
    aim_path:       str = ""
    motec_path:     str = ""
    gpx_path:       str = ""
    vbox_path:      str = ""
    unipro_path:    str = ""
    # Legacy single telemetry folder — kept as scan-all fallback for old configs
    telemetry_path: str = ""
    video_path:     str = ""
    export_path:    str = ""
    overlay:        OverlayLayout = field(default_factory=OverlayLayout)
    offsets:        Dict[str, float] = field(default_factory=dict)
    # key = absolute CSV path, value = float sync offset in seconds
    bike_overrides: Dict[str, bool]  = field(default_factory=dict)
    # key = absolute CSV path, value = True (bike) / False (car override)
    presets:        Dict[str, dict] = field(default_factory=dict)
    # name -> serialized OverlayLayout dict
    active_preset:  str = ""
    session_info:   Dict[str, dict] = field(default_factory=dict)
    # key = absolute CSV path, value = {track, vehicle, session_type} manual overrides
    racebox_email:  str = ""
    # Stored for convenience; password is never persisted
    encoder: str   = 'libx264'
    crf:     int   = 18
    workers: int   = 4
    speed_unit: str = 'auto'
    # 'auto' (use each session's detected source unit) | 'kmh' | 'mph' | 'ms'
    offset_sources:    Dict[str, str]  = field(default_factory=dict)
    # 'user' = manually confirmed, 'auto' = auto-detected (unconfirmed)
    auto_sync_failed:  List[str]       = field(default_factory=list)
    # csv_paths where auto-sync was tried but confidence was too low
    auto_sync_enabled: bool            = False
    track_map_selections: Dict[str, str] = field(default_factory=dict)
    # track_name_lower → osm_way_id; controls which OSM way is used as circuit outline
    linked_camera_folders: List[dict] = field(default_factory=list)
    # [{day: 'YYYY-MM-DD', folder: str, offset_seconds: float, source: 'auto'}, ...]
    # Manual fix for action cams with a wrong clock — see webview_api.link_camera_folder()
    secondary_source: Dict[str, str] = field(default_factory=dict)
    # key = primary absolute CSV path, value = secondary absolute CSV path
    secondary_offsets: Dict[str, float] = field(default_factory=dict)
    # key = primary CSV path, value = float offset in seconds (secondary_elapsed = primary_elapsed + offset)
    secondary_offset_sources: Dict[str, str] = field(default_factory=dict)
    # 'user' = manually confirmed, 'auto' = auto-detected via RPM cross-correlation (unconfirmed)
    secondary_sync_failed: List[str] = field(default_factory=list)
    # primary csv_paths where RPM auto-sync was tried but confidence was too low

    def all_telemetry_paths(self) -> List[str]:
        """Return all unique non-empty telemetry paths to scan.

        Uses case-insensitive, normalised path comparison so the same
        directory configured with different separators or casing only
        appears once.
        """
        import os as _os
        seen: set = set()
        result: List[str] = []
        for p in (self.racebox_path, self.aim_path, self.motec_path,
                  self.gpx_path, self.vbox_path, self.unipro_path, self.telemetry_path):
            p = p.strip()
            if not p:
                continue
            key = _os.path.normcase(_os.path.normpath(p))
            if key not in seen:
                seen.add(key)
                result.append(p)
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist config to disk atomically, keeping a backup of the
        previous last-known-good file.

        Guarded by a module-level lock since schedule_save() may fire this
        from a background Timer thread (e.g. debounced UI edits) at the same
        time auto_sync's background thread writes offsets back via a normal
        save() call — without the lock, two concurrent read-modify-writes
        could interleave and corrupt the file.
        """
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        backup_file = CONFIG_FILE.parent / (CONFIG_FILE.name + '.bak')
        payload = json.dumps(asdict(self), indent=2, default=str)

        with _config_save_lock:
            # Keep the previous version as a backup before overwriting, so a
            # load() that hits a corrupt/torn primary file has somewhere to
            # fall back to other than hard defaults.
            if CONFIG_FILE.exists():
                try:
                    shutil.copy2(CONFIG_FILE, backup_file)
                except OSError:
                    logger.debug('Could not write config backup %s', backup_file, exc_info=True)

            # Atomic write: write the full payload to a temp file in the same
            # directory, then os.replace() it over the real file. os.replace
            # is atomic on both Windows and POSIX, so a concurrent reader
            # (or a crash mid-write) never observes a partially-written file.
            tmp_file = CONFIG_FILE.parent / (
                f'{CONFIG_FILE.name}.tmp-{os.getpid()}-{threading.get_ident()}')
            try:
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    f.write(payload)
                os.replace(tmp_file, CONFIG_FILE)
            finally:
                # Clean up the temp file if os.replace() never happened
                # (e.g. the write itself raised).
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass

    def schedule_save(self, delay: float = 0.5) -> None:
        """Debounced save — flushes to disk at most once per *delay* seconds.

        Safe to call rapidly (e.g. on every gauge drag tick); the actual
        write is deferred until the flurry of calls stops.
        """
        import threading
        existing = getattr(self, '_save_timer', None)
        if existing is not None:
            existing.cancel()
        t = threading.Timer(delay, self.save)
        t.daemon = True
        t.start()
        self._save_timer = t

    @classmethod
    def load(cls) -> 'AppConfig':
        # One-time migration from older config locations
        if not CONFIG_FILE.exists():
            _src = next((p for p in (_OLD_CONFIG_V2, _OLD_CONFIG_V1) if p.exists()), None)
            if _src:
                try:
                    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(_src, CONFIG_FILE)
                except Exception:
                    logger.debug('Config migration from %s failed', _src, exc_info=True)
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return _from_dict(data)
        except Exception:
            logger.warning('Failed to load config from %s, trying backup', CONFIG_FILE, exc_info=True)
            backup_file = CONFIG_FILE.parent / (CONFIG_FILE.name + '.bak')
            if backup_file.exists():
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    logger.warning('Recovered config from backup %s', backup_file)
                    return _from_dict(data)
                except Exception:
                    logger.warning('Backup config also unreadable, using defaults',
                                   exc_info=True)
            return cls()


# ── Scan cache ────────────────────────────────────────────────────────────────

def save_scan_cache(tel_path: str, vid_path: str,
                    sessions: list, session_meta: dict) -> None:
    """Persist lightweight scan results so the tree can be populated immediately on next launch."""
    entries = []
    for m in sessions:
        meta = session_meta.get(m.csv_path, {})
        entries.append({
            'csv_path':        m.csv_path,
            'source':          m.source,
            'csv_start':       m.csv_start.isoformat() if m.csv_start else None,
            'matched':         m.matched,
            'video_paths':     m.video_group.paths if m.video_group else [],
            'video_total_dur': m.video_group.total_dur if m.video_group else 0.0,
            'needs_conversion': m.needs_conversion,
            'xrk_path':        m.xrk_path,
            'track':           meta.get('track', ''),
            'laps':            meta.get('laps', ''),
            'best':            meta.get('best', ''),
        })
    data = {'tel_path': tel_path, 'tel_paths': tel_path, 'vid_path': vid_path, 'sessions': entries}
    try:
        SCAN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCAN_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        logger.debug('Failed to write scan cache', exc_info=True)


def load_scan_cache() -> dict:
    """Return cached scan data, or {} on miss/error."""
    try:
        with open(SCAN_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ── Per-file metadata cache ─────────────────────────────────────────────────────
# Keyed by absolute path, invalidated on (size, mtime) change. Lets rescans skip
# ffprobe / file-header reads / full session parses for files that haven't changed.
# Namespaces: 'videos' (ffprobe results), 'csvs' (telemetry-file sniff results),
# 'meta' (lightweight session meta for formats that require a full parse).

_file_meta_cache_lock = threading.Lock()


def load_file_meta_cache() -> dict:
    """Load the per-file metadata cache, or an empty structure on miss/error."""
    with _file_meta_cache_lock:
        try:
            with open(FILE_META_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    for key in ('videos', 'csvs', 'meta'):
        data.setdefault(key, {})
    return data


def save_file_meta_cache(cache: dict) -> None:
    """Persist the per-file metadata cache."""
    with _file_meta_cache_lock:
        try:
            FILE_META_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FILE_META_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f)
        except Exception:
            logger.debug('Failed to write file meta cache', exc_info=True)


# ── Reconstruction helpers ─────────────────────────────────────────────────────

def migrate_gauges(raw_gauges: List[dict]) -> List[dict]:
    """Migrate a list of gauge dicts to the current schema. Used both by
    overlay_from_dict() (for the active overlay) and by webview_api's
    get_config() (for the raw preset dicts it hands to the frontend —
    presets are stored as plain JSON and never routed through
    overlay_from_dict() until they're actually activated, so a preset
    switched to live in the editor without an app restart would otherwise
    still be in whatever schema it was saved in)."""
    gauges = []
    for g in raw_gauges:
        gd = dict(g)
        # Migrate old 'channels' field name to 'multi_channels'
        if 'channels' in gd and 'multi_channels' not in gd and gd.get('channel') == 'multi':
            gd['multi_channels'] = gd.pop('channels')
        # Migrate the old {channel, style} gauge schema to {type, channel}:
        # 'type' takes over 'style' verbatim (style names and type names
        # are the same strings, e.g. 'Dial', 'Circuit'). Pseudo-channels
        # (map/info/lap_info/multi/image/g_meter) never had a real
        # user-chosen channel, so drop the placeholder value — the new
        # schema only carries 'channel' for gauges where it's meaningful.
        if 'style' in gd and 'type' not in gd:
            gd['type'] = gd.pop('style')
            if gd.get('channel') in ('map', 'info', 'lap_info', 'multi', 'image', 'g_meter'):
                gd.pop('channel', None)
        gauges.append(gd)
    return gauges


def overlay_from_dict(overlay_data: dict) -> OverlayLayout:
    """Deserialize an OverlayLayout from a plain dict (e.g. from a preset or config)."""
    raw_gauges = list(overlay_data.get('gauges', []))

    # ── Migrate old configs that stored map as a separate field ───────────────
    old_map = overlay_data.get('map', {})
    old_map_style = overlay_data.get('map_style', 'Circuit')
    if old_map and not any(g.get('channel') == 'map' for g in raw_gauges):
        raw_gauges.insert(0, {
            'channel': 'map',
            'style':   old_map_style,
            'visible': old_map.get('visible', True),
            'x': old_map.get('x', 0.74),
            'y': old_map.get('y', 0.02),
            'w': old_map.get('w', 0.24),
            'h': old_map.get('h', 0.30),
        })

    gauges = migrate_gauges(raw_gauges) if raw_gauges else _default_gauges()

    return OverlayLayout(
        is_bike          = overlay_data.get('is_bike', False),
        theme            = overlay_data.get('theme',   'Dark'),
        ref_mode         = overlay_data.get('ref_mode', 'none'),
        ref_lap_csv_path = overlay_data.get('ref_lap_csv_path', ''),
        ref_lap_num      = int(overlay_data.get('ref_lap_num', 0) or 0),
        gauges           = gauges,
    )


def _from_dict(data: dict) -> AppConfig:
    presets       = data.get('presets', {})
    active_preset = data.get('active_preset', '')

    # Always reconstruct the overlay from the saved preset when one is active,
    # so unsaved in-session edits (add/remove gauge etc.) are discarded on restart.
    if active_preset and active_preset in presets:
        overlay = overlay_from_dict(presets[active_preset])
    else:
        overlay = overlay_from_dict(data.get('overlay', {}))

    return AppConfig(
        racebox_path   = data.get('racebox_path',   ''),
        aim_path       = data.get('aim_path',       ''),
        motec_path     = data.get('motec_path',     ''),
        gpx_path       = data.get('gpx_path',       ''),
        vbox_path      = data.get('vbox_path',      ''),
        unipro_path    = data.get('unipro_path',    ''),
        telemetry_path = data.get('telemetry_path', ''),
        video_path     = data.get('video_path',     ''),
        export_path    = data.get('export_path',    ''),
        overlay        = overlay,
        offsets        = data.get('offsets',        {}),
        bike_overrides = data.get('bike_overrides', {}),
        presets        = presets,
        active_preset  = active_preset,
        session_info   = data.get('session_info',   {}),
        racebox_email  = data.get('racebox_email',  ''),
        encoder           = data.get('encoder',           'libx264'),
        crf               = int(data.get('crf',           18)),
        workers           = int(data.get('workers',       4)),
        speed_unit        = data.get('speed_unit',        'auto'),
        offset_sources       = data.get('offset_sources',       {}),
        auto_sync_failed     = data.get('auto_sync_failed',     []),
        auto_sync_enabled    = bool(data.get('auto_sync_enabled', False)),
        track_map_selections = data.get('track_map_selections', {}),
        linked_camera_folders = data.get('linked_camera_folders', []),
        secondary_source          = data.get('secondary_source',          {}),
        secondary_offsets         = data.get('secondary_offsets',         {}),
        secondary_offset_sources  = data.get('secondary_offset_sources',  {}),
        secondary_sync_failed     = data.get('secondary_sync_failed',     []),
    )
