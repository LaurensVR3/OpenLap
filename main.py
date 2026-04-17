"""
main.py — OpenLap entry point.

Run with:
    python main.py
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


def _setup_logging() -> None:
    log_dir = Path.home() / '.openlap' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(name)s — %(message)s')
    fh = logging.handlers.RotatingFileHandler(
        str(log_dir / 'openlap.log'), maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)


_setup_logging()

# ── Locate frontend assets ────────────────────────────────────────────────────
# When running from source:  frontend/ is next to main.py
# When bundled by PyInstaller: sys._MEIPASS contains extracted files
if getattr(sys, 'frozen', False):
    _BASE = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    _BASE = Path(__file__).parent

FRONTEND_DIR  = _BASE / 'frontend'
FRONTEND_HTML = FRONTEND_DIR / 'index.html'

if not FRONTEND_HTML.exists():
    sys.exit(f'Frontend not found at {FRONTEND_HTML}')


def main():
    import webview
    from webview_api import WebviewAPI

    api = WebviewAPI()

    _icon = str(_BASE / 'frontend' / 'icon.ico')

    window = webview.create_window(
        title      = 'OpenLap',
        url        = str(FRONTEND_HTML),
        js_api     = api,
        width      = 1280,
        height     = 840,
        min_size   = (960, 640),
        background_color = '#0d0f18',
    )

    api.set_window(window)

    # Use GUI thread blocking call — webview.start() must be on main thread.
    # Disable DevTools in packaged builds; keep enabled when running from source.
    webview.start(debug=not getattr(sys, 'frozen', False),
                  icon=_icon if os.path.isfile(_icon) else None)


if __name__ == '__main__':
    # Required for multiprocessing on Windows (used by video export workers)
    from multiprocessing import freeze_support
    freeze_support()
    main()
