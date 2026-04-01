# app_config.py — persistent application configuration
# Saved to ~/.telemetry_overlay/config.json

from __future__ import annotations
import json
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict

CONFIG_FILE = Path.home() / '.openlap' / 'config.json'
_OLD_CONFIG_V2 = Path.home() / '.telemetry_overlay' / 'config.json'
_OLD_CONFIG_V1 = Path.home() / '.racebox_studio'    / 'config.json'


@dataclass
class OverlayElement:
    """One overlay element with normalized position/size (0..1 of video dimensions)."""
    visible: bool = True
    x: float = 0.0   # left edge as fraction of video width
    y: float = 0.0   # top edge as fraction of video height
    w: float = 0.25  # width as fraction of video width
    h: float = 0.25  # height as fraction of video height


def _default_map() -> OverlayElement:
    return OverlayElement(visible=True, x=0.74, y=0.02, w=0.24, h=0.30)


def _default_telemetry() -> OverlayElement:
    return OverlayElement(visible=True, x=0.01, y=0.75, w=0.40, h=0.22)


@dataclass
class OverlayLayout:
    is_bike:         bool = False
    show_speed:      bool = True
    show_gforce:     bool = True
    show_lean:       bool = True   # only meaningful when is_bike=True
    map_style:       str  = 'Circuit'
    telemetry_style: str  = 'Strip'
    map:             OverlayElement = field(default_factory=_default_map)
    telemetry:       OverlayElement = field(default_factory=_default_telemetry)


@dataclass
class AppConfig:
    telemetry_path: str = ""
    video_path:     str = ""
    export_path:    str = ""
    overlay:        OverlayLayout = field(default_factory=OverlayLayout)
    offsets:        Dict[str, float] = field(default_factory=dict)
    # key = absolute CSV path, value = float sync offset in seconds

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=2)

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
                    pass
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return _from_dict(data)
        except Exception:
            return cls()


# ── Reconstruction helpers ─────────────────────────────────────────────────────

def _from_dict(data: dict) -> AppConfig:
    overlay_data = data.get('overlay', {})
    map_data  = overlay_data.get('map',       {})
    tel_data  = overlay_data.get('telemetry', {})
    overlay = OverlayLayout(
        is_bike         = overlay_data.get('is_bike',          False),
        show_speed      = overlay_data.get('show_speed',       True),
        show_gforce     = overlay_data.get('show_gforce',      True),
        show_lean       = overlay_data.get('show_lean',        True),
        map_style       = overlay_data.get('map_style',       'Circuit'),
        telemetry_style = overlay_data.get('telemetry_style', 'Strip'),
        map         = OverlayElement(**{k: map_data[k]  for k in OverlayElement.__dataclass_fields__ if k in map_data})
                      if map_data else _default_map(),
        telemetry   = OverlayElement(**{k: tel_data[k]  for k in OverlayElement.__dataclass_fields__ if k in tel_data})
                      if tel_data else _default_telemetry(),
    )
    return AppConfig(
        telemetry_path = data.get('telemetry_path', ''),
        video_path     = data.get('video_path',     ''),
        export_path    = data.get('export_path',    ''),
        overlay        = overlay,
        offsets        = data.get('offsets',        {}),
    )
