"""Shared utilities used across multiple modules."""
from __future__ import annotations
import math
import subprocess
import sys


def _run(cmd, **kwargs):
    """subprocess.run with no visible console window on Windows."""
    if sys.platform == 'win32':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault('startupinfo', si)
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    kwargs.setdefault('capture_output', True)
    return subprocess.run(cmd, **kwargs)


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
        return math.degrees(math.atan2(v * w, 9.81))
    if abs(gforce_y) > 1e-6:
        return math.degrees(math.atan(gforce_y))
    return 0.0
