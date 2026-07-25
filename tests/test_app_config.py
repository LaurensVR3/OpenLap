import json
import pytest
from dataclasses import asdict

from app_config import (
    AppConfig, OverlayLayout,
    overlay_from_dict, _from_dict,
)


# ── Default values ─────────────────────────────────────────────────────────────

def test_default_telemetry_path():
    assert AppConfig().telemetry_path == ""


def test_default_map_style():
    map_gauge = next((g for g in AppConfig().overlay.gauges if g['type'] == 'Circuit'), None)
    assert map_gauge is not None


def test_default_theme():
    assert AppConfig().overlay.theme == 'Dark'


def test_default_has_gauges():
    assert len(AppConfig().overlay.gauges) > 0


def test_default_speed_unit_is_auto():
    assert AppConfig().speed_unit == 'auto'


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
    cfg.overlay.gauges[1]['channel'] = 'rpm'
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.overlay.gauges[1]['channel'] == 'rpm'


def test_speed_unit_preserved(tmp_config_dir):
    cfg = AppConfig()
    cfg.speed_unit = 'mph'
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.speed_unit == 'mph'


def test_speed_unit_missing_key_defaults_to_auto(tmp_config_dir):
    import app_config
    app_config.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    app_config.CONFIG_FILE.write_text(json.dumps({'telemetry_path': '/x'}))
    loaded = AppConfig.load()
    assert loaded.speed_unit == 'auto'


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
    original   = OverlayLayout()
    serialized = asdict(original)
    restored   = overlay_from_dict(serialized)
    assert restored.theme == original.theme
    assert len(restored.gauges) == len(original.gauges)
    orig_map    = next((g for g in original.gauges  if g['type'] == 'Circuit'), None)
    restored_map = next((g for g in restored.gauges if g['type'] == 'Circuit'), None)
    assert orig_map is not None and restored_map is not None


def test_overlay_from_dict_missing_keys():
    layout   = overlay_from_dict({'theme': 'Carbon'})
    assert layout.theme == 'Carbon'
    map_gauge = next((g for g in layout.gauges if g['type'] == 'Circuit'), None)
    assert map_gauge is not None  # default


# ── {channel, style} -> {type, channel} schema migration ───────────────────────

def test_migrates_old_schema_real_channel_gauge():
    layout = overlay_from_dict({'gauges': [
        {'channel': 'speed', 'style': 'Dial', 'visible': True, 'x': 0, 'y': 0, 'w': 0.1, 'h': 0.1},
    ]})
    g = layout.gauges[0]
    assert g['type'] == 'Dial'
    assert g['channel'] == 'speed'
    assert 'style' not in g


@pytest.mark.parametrize('old_channel,old_style', [
    ('map', 'Circuit'),
    ('map', 'Zoomed'),
    ('info', 'Info'),
    ('lap_info', 'Scoreboard'),
    ('multi', 'Multi-Line'),
    ('image', 'Image'),
    ('g_meter', 'G-Meter'),
])
def test_migrates_old_schema_pseudo_channel_gauge_drops_channel(old_channel, old_style):
    layout = overlay_from_dict({'gauges': [
        {'channel': old_channel, 'style': old_style, 'visible': True, 'x': 0, 'y': 0, 'w': 0.1, 'h': 0.1},
    ]})
    g = layout.gauges[0]
    assert g['type'] == old_style
    assert 'channel' not in g
    assert 'style' not in g


def test_migrates_old_schema_multi_channels_field_name():
    layout = overlay_from_dict({'gauges': [
        {'channel': 'multi', 'style': 'Multi-Line', 'channels': ['speed', 'rpm'],
         'visible': True, 'x': 0, 'y': 0, 'w': 0.1, 'h': 0.1},
    ]})
    g = layout.gauges[0]
    assert g['type'] == 'Multi-Line'
    assert g['multi_channels'] == ['speed', 'rpm']
    assert 'channel' not in g


def test_new_schema_gauge_passes_through_unchanged():
    layout = overlay_from_dict({'gauges': [
        {'type': 'Bar', 'channel': 'rpm', 'visible': True, 'x': 0, 'y': 0, 'w': 0.1, 'h': 0.1},
    ]})
    g = layout.gauges[0]
    assert g['type'] == 'Bar'
    assert g['channel'] == 'rpm'


# ── _from_dict ─────────────────────────────────────────────────────────────────

def test_from_dict_empty():
    cfg = _from_dict({})
    assert cfg.telemetry_path == ""
    assert isinstance(cfg.overlay, OverlayLayout)
    assert cfg.speed_unit == 'auto'


def test_from_dict_speed_unit():
    cfg = _from_dict({'speed_unit': 'ms'})
    assert cfg.speed_unit == 'ms'


# ── Atomic save ────────────────────────────────────────────────────────────────

def test_save_is_atomic_no_leftover_tmp_file(tmp_config_dir):
    cfg = AppConfig()
    cfg.telemetry_path = '/atomic/path'
    cfg.save()

    import app_config
    tmp_files = list(app_config.CONFIG_FILE.parent.glob('*.tmp-*'))
    assert tmp_files == []


def test_save_atomic_content_correct_after_save(tmp_config_dir):
    cfg = AppConfig()
    cfg.telemetry_path = '/atomic/path2'
    cfg.save()

    import app_config
    with open(app_config.CONFIG_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data['telemetry_path'] == '/atomic/path2'


def test_save_creates_backup_of_previous_version(tmp_config_dir):
    import app_config

    cfg = AppConfig()
    cfg.telemetry_path = '/first'
    cfg.save()

    cfg.telemetry_path = '/second'
    cfg.save()

    backup_file = app_config.CONFIG_FILE.parent / (app_config.CONFIG_FILE.name + '.bak')
    assert backup_file.exists()
    with open(backup_file, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    assert backup_data['telemetry_path'] == '/first'


# ── Concurrent save safety ─────────────────────────────────────────────────────

def test_concurrent_save_does_not_corrupt_file(tmp_config_dir):
    import threading
    import app_config

    def _save_with(path_value):
        cfg = AppConfig()
        cfg.telemetry_path = path_value
        cfg.save()

    threads = [
        threading.Thread(target=_save_with, args=('/from-thread-a',)),
        threading.Thread(target=_save_with, args=('/from-thread-b',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The final file must be valid, complete JSON with one of the two
    # written values — never a torn/garbled mix of both writes.
    with open(app_config.CONFIG_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data['telemetry_path'] in ('/from-thread-a', '/from-thread-b')


# ── Load recovers from backup when the primary file is corrupt ────────────────

def test_load_recovers_from_backup_when_primary_corrupt(tmp_config_dir):
    import app_config

    cfg = AppConfig()
    cfg.telemetry_path = '/good-backup-value'
    cfg.save()

    cfg.telemetry_path = '/second-value'
    cfg.save()   # this backs up '/good-backup-value' to config.json.bak

    # Corrupt the primary config file
    app_config.CONFIG_FILE.write_text('{not valid json', encoding='utf-8')

    # Primary is corrupt; the backup holds the version saved just before the
    # corrupt write ('/good-backup-value', backed up during the 2nd save()).
    loaded = AppConfig.load()
    assert loaded.telemetry_path == '/good-backup-value'


# ── schedule_save no longer uses object.__setattr__ bypass ────────────────────

def test_schedule_save_sets_timer_attribute_directly(tmp_config_dir):
    cfg = AppConfig()
    cfg.telemetry_path = '/scheduled'
    cfg.schedule_save(delay=0.01)
    assert cfg._save_timer is not None
    cfg._save_timer.join(timeout=2.0)

    loaded = AppConfig.load()
    assert loaded.telemetry_path == '/scheduled'
