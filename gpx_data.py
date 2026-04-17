"""
gpx_data.py — GPX track file data model
=========================================
Parses standard GPX 1.0/1.1 files and returns the same
Session/Lap/DataPoint objects used by racebox_data.py.

Speed is read from Garmin TrackPointExtension (<gpxtpx:speed> in m/s)
when present; otherwise derived from consecutive GPS coordinates via
the Haversine formula.

Longitudinal G  = Δspeed / Δtime / 9.81
Lateral G       = speed * heading_rate / 9.81   (centripetal approximation)

All derived channels are smoothed with a Gaussian kernel to suppress
GPS noise before differentiation.
"""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

from data_model import DataPoint, Lap, Session
from exceptions import NoDataRowsError, MissingHeaderError

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_EARTH_R_KM = 6371.0
_G          = 9.80665          # m/s² per G
_SMOOTH_SIGMA = 3.0            # Gaussian sigma in samples for speed smoothing

# GPX XML namespaces we might encounter
_GPX_NS = {
    'gpx10': 'http://www.topografix.com/GPX/1/0',
    'gpx11': 'http://www.topografix.com/GPX/1/1',
    'gpxtpx': 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1',
    'gpxtpx2': 'http://www.garmin.com/xmlschemas/TrackPointExtensionv2/xsd',
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _local(elem: ET.Element) -> str:
    """Return the local tag name, stripping any XML namespace."""
    tag = elem.tag
    return tag.split('}')[-1] if '}' in tag else tag


def _find_local(parent: ET.Element, local_name: str) -> Optional[ET.Element]:
    """Find first direct child with the given local tag name."""
    for child in parent:
        if _local(child) == local_name:
            return child
    return None


def _find_all_local(parent: ET.Element, local_name: str) -> List[ET.Element]:
    """Find all direct children with the given local tag name."""
    return [c for c in parent if _local(c) == local_name]


def _text(elem: Optional[ET.Element], default: str = '') -> str:
    return (elem.text or '').strip() if elem is not None else default


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(max(0.0, a)))


def _bearing_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 in radians."""
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δλ = math.radians(lon2 - lon1)
    x = math.sin(Δλ) * math.cos(φ2)
    y = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    return math.atan2(x, y)


def _gaussian_smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    """1-D Gaussian convolution (reflect padding to reduce edge artefacts)."""
    if len(values) < 3 or sigma <= 0:
        return values.copy()
    radius = max(1, int(math.ceil(3 * sigma)))
    kernel_size = 2 * radius + 1
    x = np.arange(kernel_size) - radius
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    # Reflect-pad to avoid edge effects
    pad = np.pad(values, radius, mode='reflect')
    return np.convolve(pad, kernel, mode='valid')


def _angular_diff(a: float, b: float) -> float:
    """Signed angular difference b − a, wrapped to (−π, π]."""
    d = b - a
    while d > math.pi:
        d -= 2 * math.pi
    while d <= -math.pi:
        d += 2 * math.pi
    return d


# ── Public API ─────────────────────────────────────────────────────────────────

def is_gpx(path: str) -> bool:
    """Return True if the file appears to be a GPX file."""
    if not path.lower().endswith('.gpx'):
        return False
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            chunk = f.read(256)
        return '<gpx' in chunk.lower()
    except Exception:
        return False


def load_gpx(path: str) -> Session:
    """
    Parse a GPX file and return a Session.

    The entire track is treated as a single timed lap (lap 1) since standard
    GPX has no lap-boundary data.  If the file contains multiple <trk> segments,
    all track points are merged in order.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise MissingHeaderError(f"GPX parse error in {path}: {exc}") from exc

    root = tree.getroot()

    # Collect all track points from all tracks and segments
    raw: List[dict] = []
    for trk in _find_all_local(root, 'trk'):
        for seg in _find_all_local(trk, 'trkseg'):
            for pt in _find_all_local(seg, 'trkpt'):
                try:
                    lat = float(pt.get('lat', ''))
                    lon = float(pt.get('lon', ''))
                except ValueError:
                    continue

                ele_el  = _find_local(pt, 'ele')
                time_el = _find_local(pt, 'time')

                alt     = float(_text(ele_el, '0') or '0')
                time_s  = _text(time_el)

                # Parse timestamp
                ts: Optional[datetime] = None
                if time_s:
                    try:
                        ts = datetime.fromisoformat(time_s.replace('Z', '+00:00'))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                # Try to read speed from Garmin extensions (m/s)
                ext_speed: Optional[float] = None
                ext_el = _find_local(pt, 'extensions')
                if ext_el is not None:
                    for child in ext_el.iter():
                        if _local(child) == 'speed':
                            try:
                                ext_speed = float(child.text or '')
                            except (ValueError, TypeError):
                                pass
                            break

                raw.append({
                    'lat':       lat,
                    'lon':       lon,
                    'alt':       alt,
                    'time':      ts,
                    'ext_speed': ext_speed,   # m/s or None
                })

    if not raw:
        raise NoDataRowsError(f"No track points found in {path}")

    # ── Drop points with no timestamp, sort by time ────────────────────────────
    timed = [r for r in raw if r['time'] is not None]
    if not timed:
        # Fall back: assign uniform 1 Hz timestamps
        logger.warning('GPX file %s has no timestamps — assuming 1 Hz', path)
        t0 = datetime(2000, 1, 1, tzinfo=timezone.utc)
        for i, r in enumerate(raw):
            r['time'] = t0.replace(second=0) if i == 0 else raw[0]['time']
        # Re-use raw unchanged
        timed = raw

    timed.sort(key=lambda r: r['time'])

    n = len(timed)
    lats    = np.array([r['lat']  for r in timed])
    lons    = np.array([r['lon']  for r in timed])
    alts    = np.array([r['alt']  for r in timed])
    times   = [r['time'] for r in timed]
    t0      = times[0]

    # Elapsed time in seconds
    elapsed = np.array([(t - t0).total_seconds() for t in times])

    # ── Speed (km/h) ───────────────────────────────────────────────────────────
    # Prefer extension speed (already m/s → convert to km/h)
    ext_speeds = np.array(
        [r['ext_speed'] if r['ext_speed'] is not None else float('nan')
         for r in timed]
    )
    have_ext_speed = not np.all(np.isnan(ext_speeds))

    if have_ext_speed:
        # Fill NaN by interpolation where ext_speed is missing
        nans = np.isnan(ext_speeds)
        if nans.any():
            xs = np.where(~nans)[0]
            ext_speeds[nans] = np.interp(np.where(nans)[0], xs, ext_speeds[xs])
        speed_kmh = _gaussian_smooth(ext_speeds * 3.6, _SMOOTH_SIGMA)
        logger.debug('GPX: using extension speed for %d points', n)
    else:
        # Derive from Haversine distance / elapsed time
        dist_km = np.zeros(n)
        for i in range(1, n):
            dist_km[i] = _haversine_km(lats[i-1], lons[i-1], lats[i], lons[i])

        dt = np.diff(elapsed, prepend=elapsed[0] if n > 1 else 1.0)
        dt[dt < 1e-6] = 1e-6   # avoid division by zero

        speed_ms_raw = np.zeros(n)
        speed_ms_raw[1:] = dist_km[1:] * 1000.0 / dt[1:]

        # Clamp obvious outliers (> 400 km/h) before smoothing
        speed_ms_raw = np.clip(speed_ms_raw, 0.0, 111.0)
        speed_kmh_raw = speed_ms_raw * 3.6
        speed_kmh = _gaussian_smooth(speed_kmh_raw, _SMOOTH_SIGMA)
        logger.debug('GPX: derived speed from GPS for %d points', n)

    speed_kmh = np.clip(speed_kmh, 0.0, 1000.0)
    speed_ms  = speed_kmh / 3.6

    # ── Bearings and heading rate ──────────────────────────────────────────────
    bearings = np.zeros(n)
    for i in range(1, n):
        bearings[i] = _bearing_rad(lats[i-1], lons[i-1], lats[i], lons[i])
    bearings[0] = bearings[1] if n > 1 else 0.0

    heading_rate = np.zeros(n)   # rad/s
    for i in range(1, n):
        dt_i = elapsed[i] - elapsed[i-1]
        if dt_i > 1e-6:
            heading_rate[i] = _angular_diff(bearings[i-1], bearings[i]) / dt_i
    heading_rate[0] = heading_rate[1] if n > 1 else 0.0
    heading_rate = _gaussian_smooth(heading_rate, _SMOOTH_SIGMA)

    # ── Longitudinal G (from speed derivative) ─────────────────────────────────
    lon_g = np.zeros(n)
    for i in range(1, n):
        dt_i = elapsed[i] - elapsed[i-1]
        if dt_i > 1e-6:
            lon_g[i] = (speed_ms[i] - speed_ms[i-1]) / dt_i / _G
    lon_g[0] = lon_g[1] if n > 1 else 0.0
    lon_g = np.clip(_gaussian_smooth(lon_g, _SMOOTH_SIGMA), -5.0, 5.0)

    # ── Lateral G (centripetal: v * ω / g) ────────────────────────────────────
    lat_g = np.clip(speed_ms * heading_rate / _G, -5.0, 5.0)
    lat_g = _gaussian_smooth(lat_g, _SMOOTH_SIGMA)

    # ── Build DataPoints ───────────────────────────────────────────────────────
    all_pts: List[DataPoint] = []
    for i in range(n):
        pt = DataPoint(
            record      = i,
            time        = t0,           # constant; elapsed is used for sync
            lat         = float(lats[i]),
            lon         = float(lons[i]),
            alt         = float(alts[i]),
            speed       = float(speed_kmh[i]),
            gforce_x    = float(lon_g[i]),
            gforce_y    = float(lat_g[i]),
            gforce_z    = 0.0,
            lap         = 1,
            gyro_x      = 0.0,
            gyro_y      = 0.0,
            gyro_z      = 0.0,
            elapsed     = float(elapsed[i]),
            lap_elapsed = float(elapsed[i]),
        )
        all_pts.append(pt)

    # ── Single timed lap (the whole track) ────────────────────────────────────
    total_dur = float(elapsed[-1]) if n > 1 else 0.0
    lap = Lap(lap_num=1, points=all_pts, duration=total_dur,
              is_outlap=False, is_inlap=False)

    # Extract a track name from the file
    trk_name_el = None
    for trk in _find_all_local(root, 'trk'):
        trk_name_el = _find_local(trk, 'name')
        if trk_name_el is not None:
            break

    import os
    track_name = (_text(trk_name_el)
                  or os.path.splitext(os.path.basename(path))[0])

    date_str = t0.strftime('%Y-%m-%dT%H:%M:%SZ')

    logger.info('GPX loaded: %d points, %.1fs, track=%s', n, total_dur, track_name)

    return Session(
        source        = 'GPX',
        date_utc      = date_str,
        track         = track_name,
        configuration = '',
        session_type  = '',
        best_lap_time = total_dur,
        all_points    = all_pts,
        laps          = [lap],
        is_bike       = False,
        csv_path      = path,
    )
