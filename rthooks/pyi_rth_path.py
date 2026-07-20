r"""
PyInstaller runtime hook — fix PATH and Playwright browser location.

1. Prepend _MEIPASS to PATH so bundled binaries (ffmpeg, ffprobe) are
   found by subprocess calls that use bare command names.

2. Point PLAYWRIGHT_BROWSERS_PATH at the Chromium build bundled into
   ms-playwright/ next to the exe (see OpenLap.spec). Without this, the
   bundled playwright driver defaults to looking inside
   _internal\playwright\driver\package\.local-browsers\, which is never
   populated, or falls back to the current user's %LOCALAPPDATA%\ms-playwright
   — which may not exist, or may hold a different revision than this build
   was compiled against. Bundling means end users never need Playwright or a
   browser installed themselves, and the exe can't drift out of sync with
   whatever the user happens to have cached.
"""
import os
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    os.environ['PATH'] = sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(sys._MEIPASS, 'ms-playwright')
