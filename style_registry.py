"""
style_registry.py — Discovers and routes rendering to style plugins.

Each .py file in the styles/ folder is a style plugin if it exports:
    STYLE_NAME   : str   — display name
    ELEMENT_TYPE : str   — "gauge" or "map"
    render(data, w, h)   — returns RGBA np.ndarray of shape (h, w, 4)

Works in both the main process and multiprocessing worker subprocesses.
"""
from __future__ import annotations
import importlib
import os
import sys
from typing import Dict, List, Optional

import numpy as np

# Absolute path to the styles/ package directory
_HERE       = os.path.dirname(os.path.abspath(__file__))
_STYLES_DIR = os.path.join(_HERE, 'styles')

# Ensure V3/ (parent of styles/) is importable as a package root
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Module-level cache: (element_type, style_name) -> module
_cache: Dict[str, object] = {}
_scanned = False


def _load_all() -> None:
    global _scanned
    if _scanned:
        return
    _scanned = True
    if not os.path.isdir(_STYLES_DIR):
        return
    for fname in sorted(os.listdir(_STYLES_DIR)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = f'styles.{fname[:-3]}'
        try:
            mod = importlib.import_module(mod_name)
            et  = getattr(mod, 'ELEMENT_TYPE', None)
            sn  = getattr(mod, 'STYLE_NAME',   None)
            if et and sn and hasattr(mod, 'render'):
                _cache[f'{et}::{sn}'] = mod
        except Exception as exc:
            print(f'[style_registry] Failed to load {fname}: {exc}')


def available_styles(element_type: str) -> List[str]:
    """Return sorted list of STYLE_NAMEs registered for the given element type."""
    _load_all()
    return sorted(
        sn for key, _ in _cache.items()
        for et, sn in [key.split('::', 1)]
        if et == element_type
    )


def default_style(element_type: str) -> Optional[str]:
    styles = available_styles(element_type)
    # Prefer 'Dial' or 'Circuit' as defaults, otherwise first alphabetically
    preferred = {'gauge': 'Dial', 'map': 'Circuit'}
    p = preferred.get(element_type)
    if p and p in styles:
        return p
    return styles[0] if styles else None


def render_style(element_type: str, style_name: str,
                 data: dict, w: int, h: int) -> np.ndarray:
    """
    Render one overlay element.

    Returns RGBA ndarray shape (h, w, 4).
    Raises ValueError if the style is not found.
    """
    _load_all()
    key = f'{element_type}::{style_name}'
    mod = _cache.get(key)
    if mod is None:
        raise ValueError(f"Style not found: {element_type!r} / {style_name!r}. "
                         f"Available: {available_styles(element_type)}")

    # Inject theme colour tokens so style plugins don't need to import overlay_themes.
    from overlay_themes import get_theme
    themed_data = dict(data)
    themed_data['_tc'] = get_theme(data.get('_theme', 'Dark'))

    return mod.render(themed_data, w, h)
