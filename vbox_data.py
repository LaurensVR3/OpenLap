"""
vbox_data.py — Racelogic VBOX .vbo loader
==========================================
Parses the text-based VBOX format.  Files are divided into named sections
([header], [channel units], [channel names], [comments], [data]) and data
rows are whitespace-delimited.

Coordinate format : DDMM.MMMMM (degrees + decimal minutes) → decimal degrees
Time format       : HHMMSS.SS combined with date from [comments]
Speed             : 'velocity kmh' in km/h; bare 'velocity' assumed knots
G-forces          : G units — lateral-acc → gforce_y, longitudinal-acc → gforce_x
Lap detection     : 'lap trigger' channel counter if present, else single lap (1)
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import floor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data_model import DataPoint, Lap, Session

logger = logging.getLogger(__name__)

_INLAP_SLOWNESS_THRESHOLD = 1.5


# ── Public detection ──────────────────────────────────────────────────────────

def is_vbox(path: str) -> bool:
    """Return True if *path* is a Racelogic VBOX text file."""
    if Path(path).suffix.lower() != '.vbo':
        return False
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            head = f.read(512)
        return '[header]' in head.lower()
    except Exception:
        return False


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_sections(path: str) -> Dict[str, List[str]]:
    """Split a .vbo file into named sections; skip blank lines within sections."""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            line = line.rstrip('\n\r')
            if line.startswith('[') and line.endswith(']'):
                current = line[1:-1].strip().lower()
                sections[current] = []
            elif current is not None and line.strip():
                sections[current].append(line)
    return sections


def _parse_date_from_comments(comments: str) -> Optional[datetime]:
    """Extract the session date from the [comments] section text."""
    # "File created on DD/MM/YYYY at HH:MM:SS by VBOX …"
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', comments)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(year, month, day, tzinfo=timezone.utc)
    return None


def _dms_to_decimal(raw: float, hemisphere: str) -> float:
    """Convert Racelogic DDMM.MMMMM encoding to decimal degrees."""
    deg = floor(abs(raw) / 100)
    minutes = abs(raw) - deg * 100
    decimal = deg + minutes / 60.0
    return -decimal if hemisphere in ('S', 'W') else decimal


def _parse_hhmmss(raw: float) -> Tuple[int, int, float]:
    """Decompose HHMMSS.SS float into (hours, minutes, seconds)."""
    h = int(raw) // 10000
    m = (int(raw) // 100) % 100
    s = round(raw - h * 10000 - m * 100, 6)
    return h, m, s


# ── Main loader ───────────────────────────────────────────────────────────────

def load_vbo(path: str) -> Session:
    sections = _parse_sections(path)

    header_lines = sections.get('header', [])
    if not header_lines:
        raise ValueError(f"No [header] section in {path}")

    channels = [c.strip().lower() for c in header_lines]

    unit_lines = [u.strip().lower() for u in sections.get('channel units', [])]
    units: Dict[str, str] = dict(zip(channels, unit_lines)) if unit_lines else {}

    # ── Channel index lookup ──────────────────────────────────────────────────

    def _find(*names: str) -> Optional[int]:
        for name in names:
            for i, ch in enumerate(channels):
                if ch == name or ch.startswith(name):
                    return i
        return None

    idx_time    = _find('time')
    idx_lat     = _find('latitude north', 'latitude south', 'latitude')
    idx_lon     = _find('longitude east', 'longitude west', 'longitude')
    idx_speed   = _find('velocity kmh', 'velocity mph', 'velocity', 'speed')
    idx_height  = _find('height', 'altitude')
    idx_lat_g   = _find('lateral-acc', 'lateral acc', 'ay')
    idx_lon_g   = _find('longitudinal-acc', 'longitudinal acc', 'ax')
    idx_vert_g  = _find('az', 'vertical-acc', 'vertical acc')
    idx_lap     = _find('lap trigger', 'lap-trigger', 'lapctr', 'lap beacon', 'lap count')
    idx_rpm     = _find('rpm')
    idx_yaw     = _find('yaw rate', 'yaw-rate')

    if idx_time is None or idx_lat is None or idx_lon is None:
        raise ValueError(f"Missing required channels (time/lat/lon) in {path}")

    # Hemisphere: read from channel name
    lat_hem = 'S' if any('south' in c for c in channels if 'latitude' in c) else 'N'
    lon_hem = 'W' if any('west'  in c for c in channels if 'longitude' in c) else 'E'

    # Speed conversion factor
    speed_ch = channels[idx_speed] if idx_speed is not None else ''
    speed_unit = units.get(speed_ch, '')
    if 'kmh' in speed_ch or 'km/h' in speed_unit or 'kph' in speed_unit:
        speed_factor = 1.0
    elif 'mph' in speed_ch or 'mph' in speed_unit:
        speed_factor = 1.60934
    elif 'm/s' in speed_unit:
        speed_factor = 3.6
    else:
        speed_factor = 1.852  # bare 'velocity' → knots

    # Session date from [comments]
    comments_text = '\n'.join(sections.get('comments', []))
    session_date = _parse_date_from_comments(comments_text)

    # ── Data rows ─────────────────────────────────────────────────────────────

    data_lines = sections.get('data', [])
    if not data_lines:
        raise ValueError(f"No [data] section in {path}")

    all_pts: List[DataPoint] = []
    prev_dt: Optional[datetime] = None
    day_offset = 0

    for record_idx, line in enumerate(data_lines):
        cols = line.split()
        min_idx = max(c for c in [idx_time, idx_lat, idx_lon] if c is not None)
        if len(cols) <= min_idx:
            continue

        def _col(idx: Optional[int], default: float = 0.0) -> float:
            if idx is None or idx >= len(cols):
                return default
            try:
                return float(cols[idx])
            except ValueError:
                return default

        h, m, s = _parse_hhmmss(_col(idx_time))
        if session_date is not None:
            dt = session_date + timedelta(hours=h, minutes=m, seconds=s, days=day_offset)
            if prev_dt is not None and (dt - prev_dt).total_seconds() < -3600:
                day_offset += 1
                dt += timedelta(days=1)
        else:
            dt = datetime(1970, 1, 1, h, m, int(s),
                          microsecond=int((s % 1) * 1_000_000),
                          tzinfo=timezone.utc)
        prev_dt = dt

        lat = _dms_to_decimal(_col(idx_lat), lat_hem)
        lon = _dms_to_decimal(_col(idx_lon), lon_hem)

        speed  = _col(idx_speed) * speed_factor
        lat_g  = _col(idx_lat_g)   # → gforce_y (lateral)
        lon_g  = _col(idx_lon_g)   # → gforce_x (longitudinal)
        vert_g = _col(idx_vert_g)
        height = _col(idx_height)
        rpm    = _col(idx_rpm)
        yaw    = _col(idx_yaw)     # deg/s; stored in gyro_z slot

        # lap trigger increments at each beacon crossing (0 = outlap)
        lap_num = int(_col(idx_lap)) if idx_lap is not None else 1

        all_pts.append(DataPoint(
            record     = record_idx,
            time       = dt,
            lat        = lat,
            lon        = lon,
            alt        = height,
            speed      = speed,
            gforce_x   = lon_g,
            gforce_y   = lat_g,
            gforce_z   = vert_g,
            lap        = lap_num,
            gyro_x     = 0.0,
            gyro_y     = 0.0,
            gyro_z     = yaw,
            rpm        = rpm,
        ))

    if not all_pts:
        raise ValueError(f"No valid data rows parsed from {path}")

    # ── Elapsed times ─────────────────────────────────────────────────────────

    t0 = all_pts[0].time
    for pt in all_pts:
        pt.elapsed = (pt.time - t0).total_seconds()

    # ── Build laps ────────────────────────────────────────────────────────────

    buckets: Dict[int, List[DataPoint]] = defaultdict(list)
    for pt in all_pts:
        buckets[pt.lap].append(pt)

    laps: List[Lap] = []
    for lap_num in sorted(buckets.keys()):
        pts = buckets[lap_num]
        if not pts:
            continue
        lap_t0 = pts[0].time
        for pt in pts:
            pt.lap_elapsed = (pt.time - lap_t0).total_seconds()
        dur = (pts[-1].time - pts[0].time).total_seconds()
        laps.append(Lap(lap_num=lap_num, points=pts, duration=dur,
                        is_outlap=(lap_num == 0)))

    timed = [l for l in laps if l.lap_num > 0]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        if timed[-1].duration > med * _INLAP_SLOWNESS_THRESHOLD:
            timed[-1].is_inlap = True

    best_lap_time = min((l.duration for l in timed), default=0.0)
    date_str = session_date.strftime('%Y-%m-%dT%H:%M:%SZ') if session_date else ''

    return Session(
        source        = 'VBOX',
        date_utc      = date_str,
        track         = '',
        configuration = '',
        session_type  = '',
        best_lap_time = best_lap_time,
        all_points    = all_pts,
        laps          = laps,
        is_bike       = False,
        csv_path      = path,
    )
