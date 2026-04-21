# app_config.py — persistent application configuration
# Saved to ~/.telemetry_overlay/config.json

from __future__ import annotations
import json
import logging
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

CONFIG_FILE     = Path.home() / '.openlap' / 'config.json'
SCAN_CACHE_FILE = Path.home() / '.openlap' / 'scan_cache.json'
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
        {'channel': 'map',        'style': 'Circuit', 'visible': True, 'x': 0.74, 'y': 0.02, 'w': 0.24, 'h': 0.30},
        {'channel': 'speed',      'style': 'Dial',    'visible': True, 'x': 0.01, 'y': 0.74, 'w': 0.13, 'h': 0.23},
        {'channel': 'gforce_lat', 'style': 'Bar',     'visible': True, 'x': 0.15, 'y': 0.74, 'w': 0.10, 'h': 0.23},
        {'channel': 'gforce_lon', 'style': 'Bar',     'visible': True, 'x': 0.26, 'y': 0.74, 'w': 0.10, 'h': 0.23},
        {'channel': 'lap_time',   'style': 'Numeric', 'visible': True, 'x': 0.37, 'y': 0.74, 'w': 0.13, 'h': 0.23},
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
    offset_sources:    Dict[str, str]  = field(default_factory=dict)
    # 'user' = manually confirmed, 'auto' = auto-detected (unconfirmed)
    auto_sync_failed:  List[str]       = field(default_factory=list)
    # csv_paths where auto-sync was tried but confidence was too low
    auto_sync_enabled: bool            = False
    track_map_selections: Dict[str, str] = field(default_factory=dict)
    # track_name_lower → osm_way_id; controls which OSM way is used as circuit outline

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
                  self.gpx_path, self.telemetry_path):
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
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=2, default=str)

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
        object.__setattr__(self, '_save_timer', t)  # type: ignore[arg-type]

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
            logger.warning('Failed to load config from %s, using defaults', CONFIG_FILE, exc_info=True)
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


# ── Reconstruction helpers ─────────────────────────────────────────────────────

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

    if raw_gauges:
        gauges = []
        for g in raw_gauges:
            gd = dict(g)
            # Migrate old 'channels' field name to 'multi_channels'
            if 'channels' in gd and 'multi_channels' not in gd and gd.get('channel') == 'multi':
                gd['multi_channels'] = gd.pop('channels')
            gauges.append(gd)
    else:
        gauges = _default_gauges()

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
        offset_sources       = data.get('offset_sources',       {}),
        auto_sync_failed     = data.get('auto_sync_failed',     []),
        auto_sync_enabled    = bool(data.get('auto_sync_enabled', False)),
        track_map_selections = data.get('track_map_selections', {}),
    )
