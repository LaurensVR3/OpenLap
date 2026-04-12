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

import os, sys, shutil
from pathlib import Path

HERE = Path(SPECPATH)

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
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages we do not need at runtime
        'tkinter',
        'PyQt5', 'PyQt6',
        'PySide2', 'PySide6',
        'wx',
        'IPython',
        'notebook',
        'pytest',
        'playwright',      # only used for RaceBox downloader, optional
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
    # icon='frontend/icon.ico',   # uncomment when icon is added
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
