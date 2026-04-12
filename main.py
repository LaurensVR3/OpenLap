"""
main.py — OpenLap entry point (PyWebView build).

Run with:
    python main.py

For the old Tkinter UI (while migrating), run:
    python app_shell.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s %(name)s: %(message)s',
)

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
    webview.start(debug=('--debug' in sys.argv))


if __name__ == '__main__':
    # Required for multiprocessing on Windows (used by video export workers)
    from multiprocessing import freeze_support
    freeze_support()
    main()
