"""The editor preview (frontend/js/pages/editor.js) and the export
(gauge_channels.py) must show the same thing. They are separate code, and
the preview's copy had drifted: a different history span, different ranges
for altitude and lap time, and no delta channel at all. These tests read the
JS source and fail when a value stops matching its Python counterpart."""
import re
from pathlib import Path

import pytest

from gauge_channels import GAUGE_CHANNELS, HISTORY_POINTS, HISTORY_WINDOW_S

EDITOR_JS = (Path(__file__).resolve().parent.parent / 'frontend' / 'js' / 'pages' / 'editor.js'
             ).read_text(encoding='utf-8')


def _js_const(name):
    m = re.search(rf'const {name}\s*=\s*([0-9.]+)', EDITOR_JS)
    assert m, f'{name} not found in editor.js'
    return float(m.group(1))


def test_history_window_matches():
    assert _js_const('HISTORY_WINDOW_S') == HISTORY_WINDOW_S
    assert _js_const('HISTORY_POINTS') == HISTORY_POINTS


def _live_fields():
    body = EDITOR_JS[EDITOR_JS.index('function _liveFields('):]
    body = body[:body.index('\n  }\n')]
    out = {}
    for line in body.splitlines():
        m = re.search(r"(\w+):\s*\{\s*key:'(\w+)',\s*label:'([^']*)'", line)
        if not m:
            continue
        bounds = re.search(r"min:(-?[0-9.]+),\s*max:(-?[0-9.]+)", line)
        out[m.group(1)] = {'key': m.group(2), 'label': m.group(3),
                           'min': bounds.group(1) if bounds else None,
                           'max': bounds.group(2) if bounds else None}
    return out


@pytest.mark.parametrize('channel', [c for c in GAUGE_CHANNELS if c not in ('g_meter',)])
def test_every_export_channel_has_the_same_preview_definition(channel):
    fields = _live_fields()
    assert channel in fields, f'{channel} missing from editor.js _liveFields'
    js, py = fields[channel], GAUGE_CHANNELS[channel]
    assert js['key'] == py['hist_key']
    assert js['label'] == py['label']
    if channel != 'speed':   # speed's bounds are unit-converted expressions in JS
        assert float(js['min']) == py['min'] and float(js['max']) == py['max']
