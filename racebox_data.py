"""
rb_data.py — RaceBox CSV loader
==================================
Auto-detects car vs bike mode:
  Car:  GForceX, GForceY, GForceZ
  Bike: GForceX, GForceZ, LeanAngle  (no GForceY)

Data types (DataPoint, Lap, Session) are defined in data_model.py.
"""

from __future__ import annotations
import csv
import io
import logging
from typing import List, Dict, Optional

from data_model import DataPoint, Lap, Session
from exceptions import MissingHeaderError, NoDataRowsError
from utils import compute_lean_angle

logger = logging.getLogger(__name__)

_INLAP_SLOWNESS_THRESHOLD = 1.5  # flag last lap as inlap if >50% slower than median


def _detect_bike(columns: List[str]) -> bool:
    # Primary indicator: RaceBox explicitly omits GForceY on bike exports.
    # LeanAngle may or may not be present depending on app export settings.
    return 'GForceY' not in columns


def load_csv(path: str) -> Session:
    with open(path, 'r', encoding='utf-8-sig') as f:
        raw_lines = f.readlines()

    data_start = next(
        (i for i, l in enumerate(raw_lines) if l.startswith('Record,Time,')), None)
    if data_start is None:
        raise MissingHeaderError(f"No data header in {path}")

    meta: Dict[str, str] = {}
    for line in raw_lines[:data_start]:
        parts = line.strip().split(',')
        if len(parts) >= 2:
            meta[parts[0].strip()] = parts[1].strip()

    columns  = [c.strip() for c in raw_lines[data_start].strip().split(',')]
    is_bike  = _detect_bike(columns)

    reader   = csv.DictReader(io.StringIO(''.join(raw_lines[data_start:])))
    raw_rows = [r for r in reader if r.get('Record', '').strip().isdigit()]
    if not raw_rows:
        raise NoDataRowsError(f"No data rows in {path}")

    all_pts: List[DataPoint] = [DataPoint.from_row(r, is_bike) for r in raw_rows]

    # If this is a bike session but LeanAngle was not exported, compute it from
    # speed × yaw rate (GyroZ).  Formula: lean = atan(v_m_s × ω_rad_s / g)
    # GyroZ from RaceBox is in °/s; speed is in km/h.
    if is_bike and 'LeanAngle' not in columns:
        for pt in all_pts:
            pt.lean_angle = compute_lean_angle(pt.speed, pt.gyro_z, pt.gforce_y)

    t0 = all_pts[0].time
    for pt in all_pts:
        pt.elapsed = (pt.time - t0).total_seconds()

    from collections import defaultdict
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
        dur  = (pts[-1].time - pts[0].time).total_seconds()
        lap  = Lap(lap_num=lap_num, points=pts, duration=dur,
                   is_outlap=(lap_num == 0))
        laps.append(lap)

    timed = [l for l in laps if l.lap_num > 0]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        if timed[-1].duration > med * _INLAP_SLOWNESS_THRESHOLD:
            timed[-1].is_inlap = True

    return Session(
        source=meta.get('Data Source', ''), date_utc=meta.get('Date UTC', ''),
        track=meta.get('Track', ''), configuration=meta.get('Configuration', ''),
        session_type=meta.get('Session Type', ''),
        best_lap_time=float(meta.get('Best Lap Time', 0)),
        all_points=all_pts, laps=laps, is_bike=is_bike, csv_path=path,
    )
