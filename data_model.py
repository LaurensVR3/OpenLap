"""
data_model.py — Shared data types for OpenLap
==============================================
DataPoint, Lap, and Session live here so that all data loaders can import
from a common module without creating circular dependencies through
racebox_data.py.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


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
    lean_angle:   float = 0.0  # degrees (positive = right lean)
    elapsed:      float = 0.0
    lap_elapsed:  float = 0.0
    rpm:          float = 0.0
    exhaust_temp: float = 0.0  # °C

    @staticmethod
    def from_row(row: dict, is_bike: bool) -> 'DataPoint':
        # Negate RaceBox sensor LeanAngle: sensor convention positive=left lean;
        # DataPoint convention is positive=right lean.
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
            lean_angle = -float(row.get('LeanAngle', 0.0)) if is_bike else 0.0,
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
