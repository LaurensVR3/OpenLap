"""
lap_detection.py — Laps from the GPS track and a start/finish line.

For sources that record no laps (GPX, phone apps) or only guess them (a raw
Unipro .uni), laps are found by detecting when the track crosses a
start/finish line. For sources that do record laps, the same line is used to
time each lap boundary *between* two samples instead of at the first sample
of the new lap, which is otherwise off by up to one sample interval (40 ms at
25 Hz) per boundary.

A line is a point on the track, the direction of travel there, and a half
width: a crossing is the track passing through that short segment, moving
the right way.

    FinishLine(lat, lon, heading_deg, half_width_m)
    auto_finish_line(points)            -> FinishLine | None   (no lap data needed)
    finish_line_from_laps(points)       -> FinishLine | None   (where the logger's laps change)
    crossings(points, line)             -> [elapsed seconds]   (sub-sample, debounced)
    assign_laps(points, crossing_times) -> lap numbers written to points
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence

import numpy as np

_R = 6371000.0            # Earth radius, m
MIN_LAP_S = 15.0          # nothing faster is a lap (debounces GPS jitter at the line)
HALF_WIDTH_M = 15.0       # tracks are 8-15 m wide; a pit lane usually runs further off


@dataclass
class FinishLine:
    lat: float
    lon: float
    heading_deg: float    # direction of travel across the line, clockwise from north
    half_width_m: float = HALF_WIDTH_M

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> 'FinishLine':
        return FinishLine(float(d['lat']), float(d['lon']), float(d['heading_deg']),
                          float(d.get('half_width_m', HALF_WIDTH_M)))


def _local_xy(lats, lons, lat0, lon0):
    """Equirectangular metres east/north of (lat0, lon0) — exact enough over a
    few km, which is all a circuit is."""
    k = math.cos(math.radians(lat0))
    x = np.radians(np.asarray(lons, dtype=float) - lon0) * _R * k
    y = np.radians(np.asarray(lats, dtype=float) - lat0) * _R
    return x, y


def _arrays(points, with_laps: bool = False):
    """Elapsed, lat, lon (and lap numbers) of the points with a GPS fix."""
    t = np.array([p.elapsed for p in points], dtype=float)
    lat = np.array([p.lat for p in points], dtype=float)
    lon = np.array([p.lon for p in points], dtype=float)
    ok = (lat != 0) & (lon != 0) & np.isfinite(lat) & np.isfinite(lon)
    if with_laps:
        laps = np.array([p.lap for p in points])
        return t[ok], lat[ok], lon[ok], laps[ok]
    return t[ok], lat[ok], lon[ok]


def _heading_at(x, y, i, span=3) -> float:
    a, b = max(0, i - span), min(len(x) - 1, i + span)
    return math.degrees(math.atan2(x[b] - x[a], y[b] - y[a])) % 360.0


def crossings(points: Sequence, line: FinishLine, min_lap_s: float = MIN_LAP_S) -> List[float]:
    """Elapsed times at which the track crosses *line* in its direction,
    interpolated between the two samples either side."""
    t, lat, lon = _arrays(points)
    if len(t) < 2:
        return []
    x, y = _local_xy(lat, lon, line.lat, line.lon)
    h = math.radians(line.heading_deg)
    ax, ay = math.sin(h), math.cos(h)            # along the direction of travel
    along = x * ax + y * ay
    across = -x * ay + y * ax
    out: List[float] = []
    idx = np.nonzero((along[:-1] < 0) & (along[1:] >= 0))[0]
    for i in idx:
        frac = -along[i] / (along[i + 1] - along[i])
        if abs(across[i] + frac * (across[i + 1] - across[i])) > line.half_width_m:
            continue
        tc = float(t[i] + frac * (t[i + 1] - t[i]))
        if out and tc - out[-1] < min_lap_s:
            continue
        out.append(tc)
    return out


def auto_finish_line(points: Sequence, min_lap_s: float = MIN_LAP_S) -> Optional[FinishLine]:
    """A line the car crosses once per lap, found from the track alone.

    Tries points spread along the session (fast ones: a straight gives a
    clean crossing) and keeps the one crossed most often at a steady
    interval. Any fixed point gives valid lap times; the official line only
    changes where each lap starts. None for a track driven only once (a
    point-to-point stage or a road drive).
    """
    t, lat, lon = _arrays(points)
    if len(t) < 50:
        return None
    lat0, lon0 = float(np.median(lat)), float(np.median(lon))
    x, y = _local_xy(lat, lon, lat0, lon0)
    dt = np.diff(t)
    speed = np.hypot(np.diff(x), np.diff(y)) / np.where(dt > 0, dt, np.inf)
    speed = np.append(speed, speed[-1] if len(speed) else 0.0)
    fast = np.nonzero(speed > np.percentile(speed, 60))[0]
    if len(fast) < 10:
        return None
    best, best_score = None, 0.0
    for i in fast[:: max(1, len(fast) // 60)]:
        k = math.cos(math.radians(lat0))
        c_lat = float(lat0 + math.degrees(y[i] / _R))
        c_lon = float(lon0 + math.degrees(x[i] / (_R * k)))
        line = FinishLine(c_lat, c_lon, _heading_at(x, y, i))
        cr = crossings(points, line, min_lap_s)
        if len(cr) < 3:
            continue
        laps = np.diff(cr)
        med = float(np.median(laps))
        steady = float(np.mean(np.abs(laps - med) < 0.25 * med))
        score = len(cr) * steady
        if score > best_score:
            best, best_score = line, score
    return best


def finish_line_from_laps(points: Sequence) -> Optional[FinishLine]:
    """The line where the logger's own lap number changes: the median place
    of its lap boundaries, and the direction of travel there. Used to time
    those boundaries between samples."""
    t, lat, lon, laps = _arrays(points, with_laps=True)
    # Boundaries into lap 1, 2, ...: the lap number going up by one (a drop
    # back to 0 is the in-lap starting, not the line).
    idx = [i for i in range(1, len(laps)) if laps[i] == laps[i - 1] + 1]
    if len(idx) < 2:
        return None
    lat0, lon0 = float(np.median(lat[idx])), float(np.median(lon[idx]))
    x, y = _local_xy(lat, lon, lat0, lon0)
    headings = np.radians([_heading_at(x, y, i) for i in idx])
    heading = math.degrees(math.atan2(np.mean(np.sin(headings)), np.mean(np.cos(headings)))) % 360.0
    spread = float(np.median(np.hypot(x[idx], y[idx])))
    return FinishLine(lat0, lon0, heading, max(HALF_WIDTH_M, 3 * spread))


def pick_line_set(points: Sequence, line_sets: Sequence[dict]) -> Optional[dict]:
    """The user line set (see AppConfig.track_lines) this session's track
    actually runs through: the one whose finish line it crosses most, at
    least twice. Sets are matched by place, not by track name, so a session
    with no or a different track name still finds its circuit's lines."""
    best, best_n = None, 1
    for ls in line_sets or []:
        try:
            line = FinishLine.from_dict(ls['finish'])
        except (KeyError, TypeError, ValueError):
            continue
        n = len(crossings(points, line))
        if n > best_n:
            best, best_n = ls, n
    return best


def sector_boundaries(lap, lines: Sequence[dict]) -> Optional[List[float]]:
    """Lap-relative times at which *lap* crosses each sector line, in order;
    None if it misses one (a lap cut short, a line on a part of the track
    this lap did not use)."""
    pts = lap.points
    if not pts:
        return None
    lap_start = pts[0].elapsed - pts[0].lap_elapsed
    out = []
    for d in lines:
        cr = crossings(pts, FinishLine.from_dict(d), min_lap_s=1e9)
        if not cr:
            return None
        out.append(cr[0] - lap_start)
    return out if out == sorted(out) else None


def line_at(points: Sequence, lat: float, lon: float) -> Optional[FinishLine]:
    """A line across the track at the point of *points* nearest (lat, lon),
    square to the direction of travel there — what a click on the map sets."""
    t, la, lo = _arrays(points)
    if len(t) < 10:
        return None
    x, y = _local_xy(la, lo, lat, lon)
    i = int(np.argmin(x * x + y * y))
    return FinishLine(float(la[i]), float(lo[i]), _heading_at(x, y, i))


def refine_boundaries(points: Sequence, starts: Sequence[float], line: FinishLine,
                      window_s: float = 1.0) -> List[float]:
    """Each lap start in *starts* (a logger's first sample of each lap) moved
    to the line crossing within *window_s* of it, when there is one."""
    cr = crossings(points, line)
    out = []
    for s in starts:
        near = [c for c in cr if abs(c - s) <= window_s]
        out.append(min(near, key=lambda c: abs(c - s)) if near else s)
    return out


def assign_laps(points: Sequence, crossing_times: Sequence[float]) -> List[float]:
    """Write lap numbers onto *points* from the crossing times: 0 before the
    first crossing, then 1, 2, ... Returns the lap start times (the first
    being the session start) for build_laps(boundaries=...)."""
    cr = list(crossing_times)
    starts = [points[0].elapsed] + cr if points else []
    j = 0
    for p in points:
        while j < len(cr) and p.elapsed >= cr[j]:
            j += 1
        p.lap = j
    return starts
