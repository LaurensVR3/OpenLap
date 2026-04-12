"""
rb_data.py — RaceBox CSV data model
=====================================
Auto-detects car vs bike mode:
  Car:  GForceX, GForceY, GForceZ
  Bike: GForceX, GForceZ, LeanAngle  (no GForceY)
"""

from __future__ import annotations
import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional

from exceptions import MissingHeaderError, NoDataRowsError
from utils import compute_lean_angle

logger = logging.getLogger(__name__)


@dataclass
class DataPoint:
    record:      int
    time:        datetime
    lat:         float
    lon:         float
    alt:         float
    speed:       float        # km/h
    gforce_x:    float        # longitudinal G
    gforce_y:    float        # lateral G (car) or 0.0 (bike)
    gforce_z:    float        # vertical G
    lap:         int
    gyro_x:      float
    gyro_y:      float
    gyro_z:      float
    lean_angle:   float = 0.0  # degrees (bike only)
    elapsed:      float = 0.0
    lap_elapsed:  float = 0.0
    rpm:          float = 0.0
    exhaust_temp: float = 0.0  # °C

    @staticmethod
    def from_row(row: dict, is_bike: bool) -> 'DataPoint':
        return DataPoint(
            record     = int(row['Record']),
            time       = datetime.fromisoformat(row['Time'].replace('Z', '+00:00')),
            lat        = float(row['Latitude']),
            lon        = float(row['Longitude']),
            alt        = float(row['Altitude']),
            speed      = float(row['Speed']),
            gforce_x   = float(row['GForceX']),
            gforce_y   = 0.0 if is_bike else float(row.get('GForceY', 0.0)),
            gforce_z   = float(row['GForceZ']),
            lap        = int(row['Lap']),
            gyro_x     = float(row['GyroX']),
            gyro_y     = float(row['GyroY']),
            gyro_z     = float(row['GyroZ']),
            lean_angle = float(row.get('LeanAngle', 0.0)) if is_bike else 0.0,
        )


@dataclass
class Lap:
    lap_num:   int
    points:    List[DataPoint]
    duration:  float
    is_outlap: bool = False
    is_inlap:  bool = False

    @property
    def elapsed_start(self) -> float:
        return self.points[0].elapsed if self.points else 0.0

    @property
    def elapsed_end(self) -> float:
        return self.points[-1].elapsed if self.points else 0.0

    @property
    def max_speed(self) -> float:
        return max((p.speed for p in self.points), default=0.0)

    @property
    def max_lat_g(self) -> float:
        return max((abs(p.gforce_y) for p in self.points), default=0.0)

    @property
    def max_lon_g(self) -> float:
        return max((abs(p.gforce_x) for p in self.points), default=0.0)

    @property
    def max_lean(self) -> float:
        return max((abs(p.lean_angle) for p in self.points), default=0.0)

    def format_duration(self) -> str:
        m, s = int(self.duration // 60), self.duration % 60
        return f"{m}:{s:06.3f}"


@dataclass
class Session:
    source:        str
    date_utc:      str
    track:         str
    configuration: str
    session_type:  str
    best_lap_time: float
    all_points:    List[DataPoint]
    laps:          List[Lap]
    is_bike:       bool = False
    csv_path:      str  = ''

    @property
    def start_time(self) -> Optional[datetime]:
        return self.all_points[0].time if self.all_points else None

    @property
    def end_time(self) -> Optional[datetime]:
        return self.all_points[-1].time if self.all_points else None

    @property
    def timed_laps(self) -> List[Lap]:
        return [l for l in self.laps if not l.is_outlap and not l.is_inlap]

    @property
    def fastest_lap(self) -> Optional[Lap]:
        timed = self.timed_laps
        return min(timed, key=lambda l: l.duration) if timed else None

    def lap_by_num(self, n: int) -> Optional[Lap]:
        return next((l for l in self.laps if l.lap_num == n), None)

    def interpolate_at(self, elapsed: float) -> Optional[DataPoint]:
        pts = self.all_points
        if not pts or elapsed < pts[0].elapsed or elapsed > pts[-1].elapsed:
            return None
        lo, hi = 0, len(pts) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if pts[mid].elapsed <= elapsed:
                lo = mid
            else:
                hi = mid
        p0, p1 = pts[lo], pts[hi]
        dt = p1.elapsed - p0.elapsed
        if dt == 0:
            return p0
        a = (elapsed - p0.elapsed) / dt
        L = lambda attr: getattr(p0, attr) + (getattr(p1, attr) - getattr(p0, attr)) * a
        return DataPoint(
            record=p0.record, time=p0.time,
            lat=L('lat'), lon=L('lon'), alt=L('alt'), speed=L('speed'),
            gforce_x=L('gforce_x'), gforce_y=L('gforce_y'), gforce_z=L('gforce_z'),
            lap=p0.lap, gyro_x=L('gyro_x'), gyro_y=L('gyro_y'), gyro_z=L('gyro_z'),
            lean_angle=L('lean_angle'), elapsed=elapsed, lap_elapsed=L('lap_elapsed'),
            rpm=L('rpm'), exhaust_temp=L('exhaust_temp'),
        )


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
        if timed[-1].duration > med * 1.5:
            timed[-1].is_inlap = True

    return Session(
        source=meta.get('Data Source', ''), date_utc=meta.get('Date UTC', ''),
        track=meta.get('Track', ''), configuration=meta.get('Configuration', ''),
        session_type=meta.get('Session Type', ''),
        best_lap_time=float(meta.get('Best Lap Time', 0)),
        all_points=all_pts, laps=laps, is_bike=is_bike, csv_path=path,
    )
