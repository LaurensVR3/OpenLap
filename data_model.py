"""
data_model.py — Shared data types for OpenLap
==============================================
DataPoint, Lap, and Session live here so that all data loaders can import
from a common module without creating circular dependencies through
racebox_data.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


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
    gear:         int   = 0    # 0 = neutral
    extra:        Dict[str, float] = field(default_factory=dict)
    # Generic catch-all for channels beyond the fixed set above (e.g. raw
    # MoTeC/AIM channels with no dedicated field) — source-agnostic, keyed
    # by channel name. See Session.extra_channel_meta for label/unit.

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
            # Optional: not in stock RaceBox exports, but present on some custom
            # RaceBox-format devices. 'or 0.0' covers missing/empty/None values.
            rpm        = float(row.get('Rpm', row.get('rpm', 0.0)) or 0.0),
            gear       = int(row.get('Gear', row.get('gear', 0)) or 0),
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


# A lap this much slower than the median is the drive back to the pits, and
# one this much shorter is a lap cut off by the start or end of recording.
_INLAP_SLOWNESS  = 1.5
_PARTIAL_LAP     = 0.5
# A gap between two samples this many sample intervals long is a pause in
# recording, not the moment the lap changed.
_GAP_INTERVALS   = 5.0


def build_laps(points: List[DataPoint], keep_lap_elapsed: bool = False) -> List['Lap']:
    """Cut a session's points into laps, the same way for every source.

    *points* must be in time order with .elapsed and .lap (the logger's lap
    number) set. Each *contiguous run* of one lap number is a lap. Grouping by
    number instead merged runs that share a number: RaceBox numbers both the
    drive out and the drive back 0, which made one "outlap" spanning the whole
    session with a hole in the middle.

    A lap's duration is the start of the next lap minus its own start. The
    old last-sample-minus-first-sample left out one sample interval per lap
    (40 ms at 25 Hz). Across a recording pause, and for the last lap, it is
    the lap's own span plus one interval.

    lap_elapsed is set from the lap's first point unless *keep_lap_elapsed*
    (for loaders whose device logs its own lap clock; a first lap already
    under way when recording began then counts that time too).

    Laps are then classified by classify_laps(). Lap numbers stay unique: a
    run whose number was already used gets the next free number.
    """
    if not points:
        return []
    runs: List[List[DataPoint]] = []
    for pt in points:
        if runs and runs[-1][-1].lap == pt.lap:
            runs[-1].append(pt)
        else:
            runs.append([pt])

    dts = sorted(b.elapsed - a.elapsed for a, b in zip(points, points[1:])
                 if b.elapsed > a.elapsed)
    dt = dts[len(dts) // 2] if dts else 0.0

    laps: List[Lap] = []
    used: set = set()
    for i, run in enumerate(runs):
        start = run[0].elapsed
        if not keep_lap_elapsed:
            for pt in run:
                pt.lap_elapsed = pt.elapsed - start
        nxt = runs[i + 1][0].elapsed if i + 1 < len(runs) else None
        if nxt is not None and nxt - run[-1].elapsed <= max(dt * _GAP_INTERVALS, 1e-9):
            dur = nxt - start
        else:
            dur = run[-1].elapsed - start + dt
        if keep_lap_elapsed:
            dur += max(0.0, run[0].lap_elapsed)
        num = run[0].lap
        if num in used:
            num = max(used) + 1
        used.add(num)
        laps.append(Lap(lap_num=num, points=run, duration=dur, is_outlap=(num == 0 and i == 0)))

    classify_laps(laps, runs)
    return laps


def classify_laps(laps: List['Lap'], runs: Optional[List[List[DataPoint]]] = None) -> None:
    """Mark outlaps and inlaps, in place, by one rule set for every source:

    * a leading lap numbered 0 is the outlap, a trailing one the inlap;
    * with 3+ timed laps, the last one is an inlap if it is much slower than
      the median (the drive back) or much shorter (cut off by the end of
      recording), and the first one an outlap if much shorter (recording
      started mid-lap).
    """
    if runs is not None and len(laps) > 1 and runs[-1][0].lap == 0:
        laps[-1].is_inlap = True
    timed = [l for l in laps if not l.is_outlap and not l.is_inlap]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        last = timed[-1]
        if last.duration > med * _INLAP_SLOWNESS or last.duration < med * _PARTIAL_LAP:
            last.is_inlap = True
        if timed[0].duration < med * _PARTIAL_LAP:
            timed[0].is_outlap = True


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
    source_speed_unit: str = 'kmh'   # 'kmh' | 'mph' | 'ms' — unit detected in the source file
    extra_channel_meta: Dict[str, dict] = field(default_factory=dict)
    # channel name -> {'label': str, 'unit': str}, for whatever's in each DataPoint.extra

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
        extra_keys = p0.extra.keys() | p1.extra.keys()
        extra = {k: p0.extra.get(k, 0.0) + (p1.extra.get(k, 0.0) - p0.extra.get(k, 0.0)) * a
                 for k in extra_keys}
        return DataPoint(
            record=p0.record, time=p0.time,
            lat=L('lat'), lon=L('lon'), alt=L('alt'), speed=L('speed'),
            gforce_x=L('gforce_x'), gforce_y=L('gforce_y'), gforce_z=L('gforce_z'),
            lap=p0.lap, gyro_x=L('gyro_x'), gyro_y=L('gyro_y'), gyro_z=L('gyro_z'),
            lean_angle=L('lean_angle'), elapsed=elapsed, lap_elapsed=L('lap_elapsed'),
            rpm=L('rpm'), exhaust_temp=L('exhaust_temp'),
            gear=p0.gear,  # discrete — nearest sample rather than interpolated
            extra=extra,
        )
