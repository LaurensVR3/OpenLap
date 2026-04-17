"""
delta_time.py — Reference lap delta time computation
======================================================
Computes the running time gap between the current lap and a chosen reference
lap at each identical track position, using cumulative GPS arc length as the
position axis (not elapsed time).

Algorithm
---------
1. Build a (lap_elapsed_s, cumulative_dist_m) profile for the reference lap.
2. For each moment in the current lap: look up the reference driver's elapsed
   time at the same cumulative distance → delta = current_elapsed - ref_elapsed.
   Positive = behind reference (slower).  Negative = ahead (faster).

GPS-less fallback
-----------------
If a lap has no meaningful GPS variation (all-zero or indoor logger), the
profile degenerates.  In that case the function falls back to a direct
time comparison: delta = current_elapsed - (current_elapsed / current_duration
* reference_duration), which is zero at the start and reflects only the
total-time difference at the end.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Tuple

import numpy as np

from data_model import Lap

logger = logging.getLogger(__name__)

_MIN_TRACK_LENGTH_M = 50.0   # below this we consider GPS data unusable


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R  = 6_371_000.0
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a  = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def compute_lap_profile(lap: Lap) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build parallel arrays of lap_elapsed (s) and cumulative_distance (m) for a lap.

    Returns
    -------
    elapsed_s : shape (n,)  — seconds from lap start
    dist_m    : shape (n,)  — cumulative metres from lap start
    """
    pts = lap.points
    if not pts:
        return np.array([0.0]), np.array([0.0])

    n = len(pts)
    elapsed = np.array([p.lap_elapsed for p in pts], dtype=float)
    dist    = np.zeros(n, dtype=float)

    for i in range(1, n):
        p0, p1 = pts[i - 1], pts[i]
        dist[i] = dist[i - 1] + _haversine_m(p0.lat, p0.lon, p1.lat, p1.lon)

    return elapsed, dist


def make_delta_fn(
    reference_lap: Lap,
    current_lap_duration: float = 0.0,
) -> Callable[[float, float], float]:
    """
    Build a delta function from a reference lap.

    Parameters
    ----------
    reference_lap        : the lap to compare against
    current_lap_duration : duration of the lap being rendered (used only for
                           the GPS-less fallback)

    Returns
    -------
    delta(current_lap_elapsed, current_dist_m) → delta_seconds
        Positive  = current is slower than reference at this position.
        Negative  = current is faster than reference at this position.
    """
    ref_elapsed, ref_dist = compute_lap_profile(reference_lap)
    total_ref_dist = float(ref_dist[-1])

    if total_ref_dist < _MIN_TRACK_LENGTH_M:
        # GPS-less fallback: pure time comparison normalised by lap fractions
        logger.warning(
            'Reference lap has little GPS track data (%.1f m) — '
            'falling back to time-based delta', total_ref_dist
        )
        ref_dur = reference_lap.duration or 1.0
        cur_dur = current_lap_duration or ref_dur

        def _time_delta(current_elapsed: float, _dist_m: float) -> float:
            frac    = current_elapsed / cur_dur
            ref_pos = frac * ref_dur
            return current_elapsed - ref_pos

        return _time_delta

    # Normal path — distance-based interpolation
    # ref_dist is monotonically non-decreasing (cumulative sum)
    # Ensure strict monotonicity for np.interp by deduplicating equal distances
    _, unique_idx = np.unique(ref_dist, return_index=True)
    ref_dist_u    = ref_dist[unique_idx]
    ref_elapsed_u = ref_elapsed[unique_idx]

    def _dist_delta(current_elapsed: float, current_dist_m: float) -> float:
        d            = min(current_dist_m, total_ref_dist)
        ref_at_d     = float(np.interp(d, ref_dist_u, ref_elapsed_u))
        return current_elapsed - ref_at_d

    return _dist_delta
