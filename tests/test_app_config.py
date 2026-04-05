import json
import pytest
from dataclasses import asdict

from app_config import (
    AppConfig, OverlayLayout, OverlayElement, GaugeConfig,
    overlay_from_dict, _from_dict,
)


# ── Default values ─────────────────────────────────────────────────────────────

def test_default_telemetry_path():
    assert AppConfig().telemetry_path == ""


def test_default_map_style():
    assert AppConfig().overlay.map_style == 'Circuit'


def test_default_theme():
    assert AppConfig().overlay.theme == 'Dark'


def test_default_has_gauges():
    assert len(AppConfig().overlay.gauges) > 0


# ── Save / load round-trip ─────────────────────────────────────────────────────

def test_save_and_load_round_trip(tmp_config_dir):
    cfg = AppConfig()
    cfg.telemetry_path = '/some/path'
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.telemetry_path == '/some/path'


def test_overlay_theme_preserved(tmp_config_dir):
    cfg = AppConfig()
    cfg.overlay.theme = 'Light'
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.overlay.theme == 'Light'


def test_offsets_preserved(tmp_config_dir):
    cfg = AppConfig()
    cfg.offsets['/some/file.csv'] = 3.14
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.offsets['/some/file.csv'] == pytest.approx(3.14)


def test_gauges_preserved_after_round_trip(tmp_config_dir):
    cfg = AppConfig()
    cfg.overlay.gauges[0].channel = 'rpm'
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.overlay.gauges[0].channel == 'rpm'


def test_presets_preserved(tmp_config_dir):
    cfg = AppConfig()
    cfg.presets['MyPreset'] = asdict(cfg.overlay)
    cfg.save()

    loaded = AppConfig.load()
    assert 'MyPreset' in loaded.presets


# ── Missing / corrupt file ─────────────────────────────────────────────────────

def test_load_missing_file_returns_defaults(tmp_config_dir):
    # No config file written — should return defaults without error
    loaded = AppConfig.load()
    assert loaded.telemetry_path == ""


def test_load_corrupt_file_returns_defaults(tmp_config_dir):
    import app_config
    app_config.CONFIG_FILE.write_text("not valid json")
    loaded = AppConfig.load()
    assert loaded.telemetry_path == ""


# ── overlay_from_dict ──────────────────────────────────────────────────────────

def test_overlay_from_dict_empty():
    layout = overlay_from_dict({})
    assert isinstance(layout, OverlayLayout)


def test_overlay_from_dict_round_trip():
    original = OverlayLayout()
    serialized = asdict(original)
    restored = overlay_from_dict(serialized)
    assert restored.map_style == original.map_style
    assert restored.theme == original.theme
    assert len(restored.gauges) == len(original.gauges)


def test_overlay_from_dict_missing_keys():
    layout = overlay_from_dict({'theme': 'Carbon'})
    assert layout.theme == 'Carbon'
    assert layout.map_style == 'Circuit'  # default


# ── _from_dict ─────────────────────────────────────────────────────────────────

def test_from_dict_empty():
    cfg = _from_dict({})
    assert cfg.telemetry_path == ""
    assert isinstance(cfg.overlay, OverlayLayout)
