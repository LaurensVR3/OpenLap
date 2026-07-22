"""
Every style plugin in styles/*.py reads its colours via T.get('some_key', ...)
where T is the theme dict injected as data['_tc']. If a style references a key
that no palette in overlay_themes.THEMES actually defines, T.get() silently
falls through to the hardcoded default every time — the style just never
responds to theme selection, with no error to notice it by (this is exactly
how gauge_gmeter.py and map_progress.py went unnoticed: both referenced
'gauge_acc'/'gauge_bg'/etc., which no palette defines).

This scans every style's source for T.get('key', ...) calls and asserts the
key exists in every theme in overlay_themes.THEMES, so a similar typo/renamed
key doesn't silently ship again.
"""
import inspect
import re
import importlib

import pytest

from overlay_themes import THEMES

_KEY_RE = re.compile(r"T\.get\(\s*'([A-Za-z_]\w*)'")


def _theme_keys_referenced_by(module) -> set:
    src = inspect.getsource(module)
    return set(_KEY_RE.findall(src))


@pytest.mark.parametrize('style_file', [
    'gauge_bar', 'gauge_compare', 'gauge_delta', 'gauge_dial', 'gauge_gmeter',
    'gauge_image', 'gauge_info', 'gauge_lap_scoreboard', 'gauge_lean',
    'gauge_line', 'gauge_multiline', 'gauge_numeric', 'gauge_sector_bar',
    'gauge_splits', 'map_circuit', 'map_progress', 'map_zoomed',
])
def test_every_referenced_theme_key_exists_in_every_palette(style_file):
    module = importlib.import_module(f'styles.{style_file}')
    referenced = _theme_keys_referenced_by(module)
    if not referenced:
        pytest.skip(f'{style_file} reads no T.get(...) theme keys')

    for theme_name, palette in THEMES.items():
        missing = referenced - set(palette.keys())
        assert not missing, (
            f"styles/{style_file}.py references theme key(s) {missing} "
            f"that theme '{theme_name}' does not define — T.get() silently "
            f"falls back to the hardcoded default, so this style never "
            f"actually responds to switching to '{theme_name}'.")
