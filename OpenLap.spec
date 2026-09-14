# -*- mode: python ; coding: utf-8 -*-
# OpenLap.spec — PyInstaller build spec
#
# Build:
#   pip install pyinstaller
#   pyinstaller OpenLap.spec
#
# Output: dist/OpenLap/  (onedir, faster startup than onefile)
#         dist/OpenLap.app/  (additionally, on macOS)
#
# Requires:
#   - ffmpeg / ffprobe placed next to this spec (.exe on Windows) or on PATH
#   - All Python deps installed in the active environment
#
# Playwright is optional: it is only needed for the RaceBox cloud download.
# Without it the build still succeeds and everything else works — see the
# warning printed at build time.
#
# macOS note: the .app is self-contained except for Chromium, which cannot be
# bundled through PyInstaller (it is a nested, signed .app). RaceBox cloud
# download there falls back to the user's own `playwright install chromium`.

import json, os, shutil, sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

HERE    = Path(SPECPATH)
IS_WIN  = sys.platform == 'win32'
IS_MAC  = sys.platform == 'darwin'
EXE_EXT = '.exe' if IS_WIN else ''

_ver_ns = {}
exec((HERE / '_version.py').read_text(), _ver_ns)
APP_VERSION = _ver_ns['__version__']

try:
    import playwright as _pw_mod
except ImportError:
    _pw_mod = None

# ── Locate the Chromium browser build matching the installed Playwright ───────
# The playwright PyPI package only ships the Node.js driver; the actual browser
# binary is downloaded separately (`playwright install chromium`) into a
# per-user cache, keyed by a revision number pinned in this package's
# browsers.json. We bundle that exact revision into the app so end users never
# need Playwright or a browser installed themselves. racebox_downloader.py
# launches with channel="chromium" so only this one build (not the separate
# chromium-headless-shell package) is ever needed, for both headed and headless use.
#
# The cache location is platform-specific (this is Playwright's own default):
#   Windows  %LOCALAPPDATA%\ms-playwright
#   macOS    ~/Library/Caches/ms-playwright
#   Linux    ~/.cache/ms-playwright
def _playwright_cache_root():
    override = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if override:
        return Path(override)
    if IS_WIN:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise SystemExit('LOCALAPPDATA is not set — cannot locate the Playwright browser cache.')
        return Path(local) / 'ms-playwright'
    if IS_MAC:
        return Path.home() / 'Library' / 'Caches' / 'ms-playwright'
    return Path(os.environ.get('XDG_CACHE_HOME') or (Path.home() / '.cache')) / 'ms-playwright'


def _find_chromium_build():
    """(revision, build_dir) for the bundled Chromium, or (None, None)."""
    if _pw_mod is None:
        return None, None

    browsers_json = Path(_pw_mod.__file__).parent / 'driver' / 'package' / 'browsers.json'
    revision = None
    for b in json.loads(browsers_json.read_text())['browsers']:
        if b['name'] == 'chromium':
            revision = b['revision']
            break
    if revision is None:
        raise SystemExit("Could not find 'chromium' entry in playwright's browsers.json")

    build_dir = _playwright_cache_root() / f'chromium-{revision}'
    if not (build_dir / 'INSTALLATION_COMPLETE').is_file():
        raise SystemExit(
            f"Chromium revision {revision} (required by the installed "
            f"playwright=={_pkg_version('playwright')} package) is not installed "
            f"at {build_dir}.\nRun `playwright install chromium` before building."
        )
    return revision, build_dir


_CHROMIUM_REVISION, _CHROMIUM_DIR = _find_chromium_build()

if _pw_mod is None:
    print('*' * 79)
    print('WARNING: playwright is not installed, so this build will NOT be able to')
    print('         download sessions from the RaceBox cloud. Everything else works.')
    print('         To include it:  pip install "playwright==1.58.0"')
    print('                         playwright install chromium')
    print('*' * 79)
elif IS_MAC:
    print('*' * 79)
    print('NOTE: Chromium is not bundled on macOS (see the comment by the datas')
    print('      entry below). RaceBox cloud download therefore needs Chromium in')
    print('      the *user\'s* own Playwright cache, i.e. a one-off:')
    print('          playwright install chromium')
    print('      Every other feature is fully self-contained.')
    print('*' * 79)

# ── Locate ffmpeg / ffprobe ───────────────────────────────────────────────────
def _find_bin(name):
    """Find ffmpeg/ffprobe: look next to spec first, then PATH."""
    local = HERE / (name + EXE_EXT)
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
]

if _pw_mod is not None:
    # Playwright — bundle the entire package including its Node.js driver
    # so RaceBox cloud download works without any extra installs.
    datas.append((os.path.dirname(_pw_mod.__file__), 'playwright'))

    # ...and the actual Chromium browser binary (driver alone can't launch
    # anything). Lands at <exe dir>/ms-playwright/chromium-<rev>/ — see
    # rthooks/pyi_rth_path.py, which points PLAYWRIGHT_BROWSERS_PATH there.
    #
    # Not on macOS: PyInstaller ad-hoc re-signs every Mach-O it collects, and
    # Chromium arrives as a nested .app whose inner executable cannot be signed
    # on its own ("bundle format unrecognized, invalid, or unsuitable"), which
    # fails the whole build. Copying it in after the build is not a fix either:
    # adding files to a signed .app invalidates its signature, and re-signing
    # over Chromium hits the same wall. So the macOS build ships without it and
    # rthooks/pyi_rth_path.py leaves PLAYWRIGHT_BROWSERS_PATH alone, which lets
    # Playwright find the user's own `playwright install chromium`.
    if not IS_MAC:
        datas.append((str(_CHROMIUM_DIR), f'ms-playwright/chromium-{_CHROMIUM_REVISION}'))

# AIM / DLL files present in the project root (Windows-only; macOS and Linux
# read XRK through libxrk, which pip installs as a normal wheel).
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

# FFmpeg binaries. These keep their own filename, which is what makes this
# work on both platforms: 'ffmpeg.exe' on Windows and 'ffmpeg' elsewhere, the
# two names utils.tool_path() looks for.
for _bin in (FFMPEG_BIN, FFPROBE_BIN):
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
    # PyWebView internals
    'webview',
    'webview.platforms',
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
]

# The pywebview backend is platform-specific, and naming another platform's
# backend here only produces a missing-module warning that hides real ones.
if IS_WIN:
    hidden_imports += [
        'webview.platforms.winforms',
        'clr',                       # pythonnet (required by winforms backend)
    ]
elif IS_MAC:
    hidden_imports += ['webview.platforms.cocoa']
else:
    hidden_imports += ['webview.platforms.gtk']

if _pw_mod is not None:
    hidden_imports += [
        'playwright',
        'playwright.sync_api',
        'playwright._impl._driver',
        'playwright._impl._transport',
        'playwright._impl._connection',
        'playwright._impl._browser_type',
        'racebox_downloader',
    ]

# ── Icon ──────────────────────────────────────────────────────────────────────
# Windows wants .ico; macOS wants .icns and errors on an .ico. We only ship the
# .ico today, so a macOS build takes the default icon until an .icns is added.
if IS_WIN:
    ICON_PATH = str(HERE / 'frontend' / 'icon.ico')
else:
    _icns = HERE / 'frontend' / 'icon.icns'
    ICON_PATH = str(_icns) if _icns.is_file() else None

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

# UPX mangles Mach-O binaries badly enough that the result will not launch (and
# would invalidate any code signature), so it is Windows/Linux only.
USE_UPX = not IS_MAC

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenLap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=USE_UPX,
    console=False,          # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=USE_UPX,
    upx_exclude=[],
    name='OpenLap',
)

# ── macOS .app bundle ─────────────────────────────────────────────────────────
# BUNDLE is a no-op on other platforms, but referencing it at all is a syntax
# error there, so it stays behind the guard.
if IS_MAC:
    app = BUNDLE(
        coll,
        name='OpenLap.app',
        icon=ICON_PATH,
        bundle_identifier='com.openlap.app',
        version=APP_VERSION,
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            # The app reads telemetry and video the user picks through a native
            # folder dialog; macOS 13+ will not hand those over without this.
            'NSDesktopFolderUsageDescription':   'OpenLap reads telemetry and video files you select.',
            'NSDocumentsFolderUsageDescription': 'OpenLap reads telemetry and video files you select.',
            'NSDownloadsFolderUsageDescription': 'OpenLap reads telemetry and video files you select.',
            'NSRemovableVolumesUsageDescription':
                'OpenLap reads telemetry and video files directly from a camera or lap timer SD card.',
        },
    )
