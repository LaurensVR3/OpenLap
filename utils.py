"""Shared utilities used across multiple modules."""
from __future__ import annotations
import math
import os
import shutil
import subprocess
import sys


def _win_flags() -> dict:
    """Return Windows-specific Popen kwargs that suppress the console window."""
    if sys.platform != 'win32':
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}


# ── FFmpeg / ffprobe location ─────────────────────────────────────────────────
#
# Everything that shells out to FFmpeg must agree on *which* FFmpeg, or the
# Settings check can pass against one binary while every export fails against
# another. That is not hypothetical: the encoder check used to consult an
# FFMPEG_BIN override and a copy bundled beside the app, while every call site
# doing real work passed a bare "ffmpeg" and relied on PATH. In the packaged
# onedir build those are different directories.

_TOOL_ENV = {'ffmpeg': 'FFMPEG_BIN', 'ffprobe': 'FFPROBE_BIN'}


def _bundled_dirs() -> list:
    """Directories shipped alongside the app that may contain the binaries."""
    dirs = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        dirs.append(meipass)                       # PyInstaller extraction dir
    if getattr(sys, 'frozen', False):
        dirs.append(os.path.dirname(sys.executable))   # next to OpenLap.exe
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    return dirs


def tool_path(name: str) -> str:
    """Absolute path to *name* ('ffmpeg' or 'ffprobe'), or the bare name.

    Resolution order: an explicit environment override, then PATH, then the
    directories shipped with the app. Falls back to the bare name so the
    caller still gets a real launch attempt (and a clean FFmpegNotFoundError
    from _run/_popen) rather than a silent no-op.
    """
    override = os.environ.get(_TOOL_ENV.get(name, ''))
    if override and os.path.isfile(override):
        return override
    found = shutil.which(name)
    if found:
        return found
    exe = f'{name}.exe' if sys.platform == 'win32' else name
    for d in _bundled_dirs():
        candidate = os.path.join(d, exe)
        if os.path.isfile(candidate):
            return candidate
    return name


def ffmpeg_path() -> str:
    return tool_path('ffmpeg')


def ffprobe_path() -> str:
    return tool_path('ffprobe')


def _not_found(cmd, exc: OSError):
    """Turn a failed launch into an error that names the real problem.

    A missing FFmpeg otherwise surfaces as a bare "[WinError 2] The system
    cannot find the file specified", which reads like a problem with the
    user's video or telemetry file rather than a missing dependency.
    """
    from exceptions import FFmpegNotFoundError
    name = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
    return FFmpegNotFoundError(
        f"Could not run {os.path.basename(str(name))!r}: {exc}. "
        f"Install FFmpeg and make sure it is on your PATH, or set the "
        f"FFMPEG_BIN / FFPROBE_BIN environment variable to its full path."
    )


def _run(cmd, **kwargs):
    """subprocess.run with no visible console window on Windows."""
    for k, v in _win_flags().items():
        kwargs.setdefault(k, v)
    kwargs.setdefault('capture_output', True)
    try:
        return subprocess.run(cmd, **kwargs)
    except (FileNotFoundError, NotADirectoryError) as e:
        raise _not_found(cmd, e) from e


def _popen(cmd, **kwargs):
    """subprocess.Popen with no visible console window on Windows."""
    for k, v in _win_flags().items():
        kwargs.setdefault(k, v)
    try:
        return subprocess.Popen(cmd, **kwargs)
    except (FileNotFoundError, NotADirectoryError) as e:
        raise _not_found(cmd, e) from e


def compute_lean_angle(speed_kmh: float, gyro_z_deg_s: float,
                       gforce_y: float) -> float:
    """Compute lean angle in degrees from available sensor data.

    Prefers gyro-based (speed × yaw rate) when GyroZ has a meaningful
    value, falls back to lateral G for sources without a gyroscope.

    Returns:
        Lean angle in degrees (positive = right lean, negative = left lean).
    """
    if abs(gyro_z_deg_s) > 1e-6:
        v = speed_kmh / 3.6                   # km/h → m/s
        w = gyro_z_deg_s * math.pi / 180.0    # °/s  → rad/s
        # Negate: gyro-based formula gives positive=left; we return positive=right.
        return -math.degrees(math.atan2(v * w, 9.81))
    if abs(gforce_y) > 1e-6:
        # Negate: lateral G positive=left; we return positive=right.
        return -math.degrees(math.atan(gforce_y))
    return 0.0
