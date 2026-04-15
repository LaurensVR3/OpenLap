"""
PyInstaller runtime hook — fix PATH and Playwright browser location.

1. Prepend _MEIPASS to PATH so bundled binaries (ffmpeg, ffprobe) are
   found by subprocess calls that use bare command names.

2. Set PLAYWRIGHT_BROWSERS_PATH to the standard %LOCALAPPDATA%\ms-playwright
   location.  Without this, the bundled playwright driver defaults to looking
   for Chromium inside _internal\playwright\driver\package\.local-browsers\,
   which is never populated.
"""
import os
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    os.environ['PATH'] = sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')

    local_app = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    os.environ.setdefault(
        'PLAYWRIGHT_BROWSERS_PATH',
        os.path.join(local_app, 'ms-playwright'),
    )
