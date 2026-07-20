# -*- mode: python ; coding: utf-8 -*-
# OpenLap.spec — PyInstaller build spec
#
# Build:
#   pip install pyinstaller
#   pyinstaller OpenLap.spec
#
# Output: dist/OpenLap/  (onedir, faster startup than onefile)
#
# Requires:
#   - ffmpeg.exe / ffprobe.exe placed next to this spec (or on PATH)
#   - All Python deps installed in the active environment

import json, os, sys, shutil
from importlib.metadata import version as _pkg_version
from pathlib import Path
import playwright as _pw_mod

HERE = Path(SPECPATH)

# ── Locate the Chromium browser build matching the installed Playwright ───────
# The playwright PyPI package only ships the Node.js driver; the actual browser
# binary is downloaded separately (`playwright install chromium`) into
# %LOCALAPPDATA%\ms-playwright, keyed by a revision number pinned in this
# package's browsers.json. We bundle that exact revision into the exe so end
# users never need Playwright or a browser installed themselves. racebox_downloader.py
# launches with channel="chromium" so only this one build (not the separate
# chromium-headless-shell package) is ever needed, for both headed and headless use.
def _find_chromium_build():
    browsers_json = Path(_pw_mod.__file__).parent / 'driver' / 'package' / 'browsers.json'
    revision = None
    for b in json.loads(browsers_json.read_text())['browsers']:
        if b['name'] == 'chromium':
            revision = b['revision']
            break
    if revision is None:
        raise SystemExit("Could not find 'chromium' entry in playwright's browsers.json")

    cache_root = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
                       or (Path(os.environ['LOCALAPPDATA']) / 'ms-playwright'))
    build_dir = cache_root / f'chromium-{revision}'
    if not (build_dir / 'INSTALLATION_COMPLETE').is_file():
        raise SystemExit(
            f"Chromium revision {revision} (required by the installed "
            f"playwright=={_pkg_version('playwright')} package) is not installed "
            f"at {build_dir}.\nRun `playwright install chromium` before building."
        )
    return revision, build_dir

_CHROMIUM_REVISION, _CHROMIUM_DIR = _find_chromium_build()

# ── Locate ffmpeg / ffprobe ───────────────────────────────────────────────────
def _find_bin(name):
    """Find ffmpeg/ffprobe: look next to spec first, then PATH."""
    local = HERE / (name + '.exe')
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    return None

FFMPEG_BIN  = _find_bin('ffmpeg')
FFPROBE_BIN = _find_bin('ffprobe')

# ── Data files ────────────────────────────────────────────────────────────────
datas = [
    # Frontend (HTML/CSS/JS)
    (str(HERE / 'frontend'), 'frontend'),
    # Style plugins (matplotlib gauge renderers for video export)
    (str(HERE / 'styles'), 'styles'),
    # Playwright — bundle the entire package including its Node.js driver
    # so RaceBox cloud download works without any extra installs.
    (os.path.dirname(_pw_mod.__file__), 'playwright'),
    # ...and the actual Chromium browser binary (driver alone can't launch
    # anything). Lands at <exe dir>/ms-playwright/chromium-<rev>/ — see
    # rthooks/pyi_rth_path.py, which points PLAYWRIGHT_BROWSERS_PATH there.
    (str(_CHROMIUM_DIR), f'ms-playwright/chromium-{_CHROMIUM_REVISION}'),
]

# AIM / DLL files present in the project root
_dlls = [
    'MatLabXRK-2022-64-ReleaseU.dll',
    'libiconv-2.dll',
    'libxml2-2.dll',
    'libz.dll',
    'pthreadVC2_x64.dll',
]
for dll in _dlls:
    p = HERE / dll
    if p.is_file():
        datas.append((str(p), '.'))

# FFmpeg binaries
for _bin, _name in [(FFMPEG_BIN, 'ffmpeg.exe'), (FFPROBE_BIN, 'ffprobe.exe')]:
    if _bin:
        datas.append((_bin, '.'))

# ── Hidden imports ────────────────────────────────────────────────────────────
# PyInstaller cannot automatically detect dynamically-imported modules.
# Include all style plugins and data loaders referenced at runtime.
hidden_imports = [
    # Style plugins (loaded by style_registry.py via importlib)
    'styles.gauge_bar',
    'styles.gauge_compare',
    'styles.gauge_delta',
    'styles.gauge_dial',
    'styles.gauge_gmeter',
    'styles.gauge_image',
    'styles.gauge_info',
    'styles.gauge_lap_scoreboard',
    'styles.gauge_lean',
    'styles.gauge_line',
    'styles.gauge_multiline',
    'styles.gauge_numeric',
    'styles.gauge_sector_bar',
    'styles.gauge_splits',
    'styles.map_circuit',
    'styles.map_progress',
    'styles.map_zoomed',
    # Data loaders
    'racebox_data',
    'aim_data',
    'gpx_data',
    'motec_data',
    # PyWebView internals (platform-specific backends)
    'webview',
    'webview.platforms',
    'webview.platforms.winforms',  # Windows
    'clr',                         # pythonnet (required by winforms backend)
    # Multiprocessing support
    'multiprocessing.pool',
    'multiprocessing.managers',
    # OpenCV
    'cv2',
    # Matplotlib backends (headless)
    'matplotlib',
    'matplotlib.backends.backend_agg',
    # Misc runtime imports
    'numpy',
    'pandas',
    'PIL',
    'PIL.Image',
    'xml.etree.ElementTree',
    'json',
    'logging.handlers',
    # Playwright (RaceBox cloud download)
    'playwright',
    'playwright.sync_api',
    'playwright._impl._driver',
    'playwright._impl._transport',
    'playwright._impl._connection',
    'playwright._impl._browser_type',
    'racebox_downloader',
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['main.py'],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthooks/pyi_rth_path.py'],
    excludes=[
        # Exclude heavy packages we do not need at runtime
        'tkinter',
        'PyQt5', 'PyQt6',
        'PySide2', 'PySide6',
        'wx',
        'IPython',
        'notebook',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenLap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No terminal window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(HERE / 'frontend' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OpenLap',
)

# ── macOS .app bundle (no-op on Windows) ─────────────────────────────────────
# Uncomment on macOS:
# app = BUNDLE(
#     coll,
#     name='OpenLap.app',
#     icon=None,
#     bundle_identifier='com.openlap.app',
#     info_plist={
#         'NSHighResolutionCapable': True,
#         'CFBundleShortVersionString': '0.1.0',
#     },
# )
