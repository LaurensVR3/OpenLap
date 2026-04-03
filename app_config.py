# app_config.py — persistent application configuration
# Saved to ~/.telemetry_overlay/config.json

from __future__ import annotations
import json
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List

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


@dataclass
class GaugeConfig:
    """One individual gauge element."""
    channel: str  = 'speed'
    style:   str  = 'Dial'
    visible: bool = True
    x: float = 0.0
    y: float = 0.0
    w: float = 0.12
    h: float = 0.20


def _default_map() -> OverlayElement:
    return OverlayElement(visible=True, x=0.74, y=0.02, w=0.24, h=0.30)


def _default_gauges() -> List[GaugeConfig]:
    return [
        GaugeConfig(channel='speed',      style='Dial',    x=0.01, y=0.74, w=0.13, h=0.23),
        GaugeConfig(channel='gforce_lat', style='Bar',     x=0.15, y=0.74, w=0.10, h=0.23),
        GaugeConfig(channel='gforce_lon', style='Bar',     x=0.26, y=0.74, w=0.10, h=0.23),
        GaugeConfig(channel='lap_time',   style='Numeric', x=0.37, y=0.74, w=0.13, h=0.23),
    ]


@dataclass
class OverlayLayout:
    is_bike:    bool          = False
    map_style:  str           = 'Circuit'
    map:        OverlayElement = field(default_factory=_default_map)
    gauges:     List[GaugeConfig] = field(default_factory=_default_gauges)


@dataclass
class AppConfig:
    telemetry_path: str = ""
    video_path:     str = ""
    export_path:    str = ""
    overlay:        OverlayLayout = field(default_factory=OverlayLayout)
    offsets:        Dict[str, float] = field(default_factory=dict)
    # key = absolute CSV path, value = float sync offset in seconds
    presets:        Dict[str, dict] = field(default_factory=dict)
    # name -> serialized OverlayLayout dict
    active_preset:  str = ""

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

def overlay_from_dict(overlay_data: dict) -> OverlayLayout:
    """Deserialize an OverlayLayout from a plain dict (e.g. from a preset or config)."""
    map_data = overlay_data.get('map', {})
    map_elem = (OverlayElement(**{k: map_data[k] for k in OverlayElement.__dataclass_fields__ if k in map_data})
                if map_data else _default_map())

    raw_gauges = overlay_data.get('gauges', [])
    if raw_gauges:
        gauges = [GaugeConfig(**{k: g[k] for k in GaugeConfig.__dataclass_fields__ if k in g})
                  for g in raw_gauges]
    else:
        gauges = _default_gauges()

    return OverlayLayout(
        is_bike   = overlay_data.get('is_bike',  False),
        map_style = overlay_data.get('map_style', 'Circuit'),
        map       = map_elem,
        gauges    = gauges,
    )


def _from_dict(data: dict) -> AppConfig:
    overlay = overlay_from_dict(data.get('overlay', {}))
    return AppConfig(
        telemetry_path = data.get('telemetry_path', ''),
        video_path     = data.get('video_path',     ''),
        export_path    = data.get('export_path',    ''),
        overlay        = overlay,
        offsets        = data.get('offsets',        {}),
        presets        = data.get('presets',        {}),
        active_preset  = data.get('active_preset',  ''),
    )
