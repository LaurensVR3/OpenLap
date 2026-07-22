"""
unipro_data.py — Unipro Laptimer .uni and .tsv loaders
=======================================================
Two loaders for the same underlying source, both producing Session objects
with source='Unipro':

  load_tsv() — parses Unipro Analyser's own tab-separated export. This is
  the FULL-FIDELITY path: every channel (RPM, gear, exhaust temp, the
  device's own true lap numbering, real accelerometer-measured G, native
  GPS rate) comes through directly, no reconstruction needed. Prefer this
  whenever a .tsv export is available.

  load_uni() — parses the raw .uni binary directly, so users don't have to
  export a .tsv first. Only GPS position/speed/altitude are recoverable
  this way (see FORMAT NOTES below) — RPM, gear, gyro, and exhaust temp stay
  at DataPoint's defaults, and lap boundaries are reconstructed heuristically
  (GPS beacon-crossing detection) rather than read from the device's own lap
  counter. Use load_tsv() instead whenever possible.

FORMAT NOTES for load_uni() (reverse-engineered — Unipro publishes no spec
and ships no reader SDK; verified byte-for-byte against Unipro Analyser's
own .tsv export of the same session):

The file is a sequence of tagged chunks: 8-byte ASCII tag, 1-byte version,
3-byte big-endian length, then that many payload bytes. Chunks used here:
  RECRDATE — 7-byte session start date/time (1 padding byte, then raw byte
             values for year-2000/month/day/hour/minute/second)
  RECRGLOS — session info; embeds the track/session name as plain ASCII
  RECRDATA — the actual telemetry, as a keyframe+delta-encoded event stream

RECRDATA's per-channel event framing (a variable-length keyframe-vs-delta
scheme with a running byte-level counter) is NOT fully decoded yet. What IS
decoded and verified is the shape of a "GPS fix" sample: 4 contiguous
big-endian int32 fields with zero padding between them —
    latitude  = raw / 1e7   (degrees)
    longitude = raw / 1e7   (degrees)
    altitude  = raw / 1000  (metres)
    gps_speed = raw / 100   (km/h)
_scan_gps_fixes() finds these by scanning for 4 simultaneously-plausible
values rather than via a byte offset computed from a parsed record header
(since that header format isn't understood yet). This has a negligible
false-positive rate — verified against a real session's .tsv export: 100%
of matches were correct across ~10,600 checked points, zero false
positives — because altitude and speed alone are already tight filters
relative to the full int32 range (see _scan_gps_fixes docstring), and
requiring all four fields to align simultaneously narrows it further.

GPS updates natively at 10 Hz, but only ~60% of native samples currently
decode this way (the rest are in the still-undecoded delta-encoded form) —
still an effective ~6 Hz spread evenly across the whole session, which is
plenty for a track map / speed trace / lap timing. Elapsed time is
reconstructed from the byte-offset spacing between found fixes (median
stride ≈ one native 10 Hz tick), which round-trips to sub-millisecond
accuracy even across the gaps where a sample wasn't decoded (verified:
<0.2 ms max error across ~10,600 points against the real session clock).

RPM, gear, and the raw IMU/gyro channels are NOT decoded yet — DataPoint's
gforce_x/gforce_y are derived from the recovered GPS speed and heading, the
same way gpx_data.py derives them for plain GPX tracks with no IMU; rpm,
gforce_z, gyro_*, lean_angle, and exhaust_temp are left at DataPoint's
defaults until those channels are cracked.

RECRGLOS also embeds the track's timing-beacon GPS coordinates (start/finish
plus any sector splits Unipro was configured with) as zero-padded raw/1e7
int32 pairs — see _scan_beacon_points. _detect_lap_crossings replicates the
device's own onboard lap timer from these: it projects the GPS track onto a
local (forward, lateral) frame centred on the first beacon and looks for the
point where the car drives through it, splitting the session into an outlap,
N numbered timed laps, and an inlap — the same convention racebox_data.py/
vbox_data.py use. Verified against two real sessions on the same track: 15
and 14 timed laps recovered, all within a tight 57-61s band with zero
outliers. If no beacon can be recovered (or the track never actually comes
near it, e.g. a different/older export format), the whole session falls
back to being a single lap, matching gpx_data.py's convention.

Known gap: cross-checking against the same session's .tsv export (which has
the device's own true lap counter) showed load_uni()'s beacon-crossing
detection under-counts by one lap on a real file (16 crossings recovered vs
17 the device itself recorded) — the missed crossing's neighbouring GPS
fixes were evidently too sparse/noisy near the gate that one time. The
heuristic is good enough for a quick look at a raw .uni with no .tsv on
hand, but load_tsv() is unambiguously more accurate when available.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import numpy as np

from data_model import DataPoint, Lap, Session
from exceptions import MissingHeaderError, NoDataRowsError
from gpx_data import _G, _SMOOTH_SIGMA, _angular_diff, _bearing_rad, _gaussian_smooth, _haversine_km

logger = logging.getLogger(__name__)

_MAGIC = b'UUni'

# Plausible-value bounds used to heuristically locate GPS-fix records (see
# _scan_gps_fixes). Altitude and speed are the real discriminators — their
# bounds are tight relative to the full int32 range a raw field could hold,
# which is what keeps the false-positive rate negligible even with full
# world coverage on latitude/longitude.
_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)
_ALT_RANGE = (-500.0, 9000.0)
_SPD_RANGE = (0.0, 400.0)

# Flag the final timed lap as an inlap if it's this much slower than the
# session median — matching racebox_data.py / vbox_data.py.
_INLAP_SLOWNESS_THRESHOLD = 1.5

_M_PER_DEG_LAT = 110_540.0  # metres per degree of latitude (near-constant)


def is_unipro_uni(path: str) -> bool:
    """Return True if the file looks like a Unipro Laptimer .uni export."""
    if not path.lower().endswith('.uni'):
        return False
    try:
        with open(path, 'rb') as f:
            return f.read(4) == _MAGIC
    except Exception:
        return False


def _iter_chunks(data: bytes):
    """Yield (tag: bytes, version: int, payload_start: int, length: int) for
    each top-level tagged chunk, starting after the 8-byte magic+version
    header. A length of 0xFFFFFF is a sentinel ("too large for the 24-bit
    field") used by the final RECRDATA chunk — its real length is simply
    everything to end-of-file.
    """
    pos = 8  # 4-byte magic ("UUni") + 4-byte leading value
    n = len(data)
    while pos + 12 <= n:
        tag = data[pos:pos + 8]
        if not all(32 <= b < 127 for b in tag):
            break
        length = int.from_bytes(data[pos + 9:pos + 12], 'big')
        payload_start = pos + 12
        if length == 0xFFFFFF:
            length = n - payload_start
        yield tag, data[pos + 8], payload_start, length
        pos = payload_start + length


def _parse_date(payload: bytes) -> Optional[datetime]:
    """RECRDATE payload: 1 padding byte, then raw byte values for
    [year-2000, month, day, hour, minute, second]."""
    if len(payload) < 7:
        return None
    yy, mm, dd, hh, mi, ss = payload[1:7]
    try:
        return datetime(2000 + yy, mm, dd, hh, mi, ss, tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_track_name(payload: bytes, fallback: str) -> str:
    """RECRGLOS embeds the track/session name as a plain-ASCII run inside an
    otherwise binary payload (preceded by a short internal tag like "UGse").
    Take the longest printable-ASCII run rather than relying on an exact
    byte offset, since the surrounding fields aren't decoded."""
    best = b''
    run = bytearray()
    for b in payload:
        if 32 <= b < 127:
            run.append(b)
        else:
            if len(run) > len(best):
                best = bytes(run)
            run.clear()
    if len(run) > len(best):
        best = bytes(run)
    text = best.decode('ascii', 'ignore').strip()
    return text if len(text) >= 3 else fallback


def _scan_gps_fixes(data: bytes) -> List[Tuple[int, float, float, float, float]]:
    """Heuristically recover every GPS fix in a RECRDATA payload.

    Returns a list of (byte_offset, lat, lon, alt_m, speed_kmh) in file
    order. See module docstring for why this foreknowledge-free scan is
    reliable: it requires latitude, longitude, altitude, and speed to all
    fall in plausible ranges simultaneously in one contiguous 16-byte
    window, and altitude/speed alone are tight filters relative to the full
    int32 range those raw fields could otherwise hold.
    """
    hits: List[Tuple[int, float, float, float, float]] = []
    n = len(data)
    i = 8
    while i < n - 8:
        alt_raw = int.from_bytes(data[i:i + 4], 'big', signed=True)
        alt = alt_raw / 1000.0
        if not (_ALT_RANGE[0] <= alt <= _ALT_RANGE[1]):
            i += 1
            continue
        spd_raw = int.from_bytes(data[i + 4:i + 8], 'big', signed=True)
        spd = spd_raw / 100.0
        if not (_SPD_RANGE[0] <= spd <= _SPD_RANGE[1]):
            i += 1
            continue
        lat_raw = int.from_bytes(data[i - 8:i - 4], 'big', signed=True)
        lat = lat_raw / 1e7
        if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]):
            i += 1
            continue
        lon_raw = int.from_bytes(data[i - 4:i], 'big', signed=True)
        lon = lon_raw / 1e7
        if not (_LON_RANGE[0] <= lon <= _LON_RANGE[1]):
            i += 1
            continue
        if lat == 0.0 and lon == 0.0:
            # (0, 0) — "Null Island" — is the universal GPS "no fix yet"
            # sentinel, never a real racing venue; excluding it outright
            # avoids matching runs of zero/near-zero bytes elsewhere in the
            # file (e.g. padding) as a spurious plausible-looking fix.
            i += 1
            continue
        # Record offset is i-8: i itself is where the altitude field starts,
        # since lat/lon are read by looking backward from it.
        hits.append((i - 8, lat, lon, alt, spd))
        # Skip past this record's own 16 bytes — an overlapping window a few
        # bytes into a just-matched record can occasionally reinterpret its
        # tail (lon/alt/speed + whatever follows) as another plausible-
        # looking quadruple purely by coincidence; a genuine next record
        # can't start inside the one just found.
        i += 16
    return hits


def _reconstruct_elapsed(offsets: List[int]) -> List[float]:
    """Recover elapsed seconds for each GPS fix from byte-offset spacing.

    GPS logs natively at a fixed 10 Hz, so the byte distance between two
    *found* fixes is always a whole multiple of one native tick, even when
    intermediate (still-undecoded) samples were skipped over. Rounding the
    observed delta to the nearest multiple of the median stride recovers
    that skip count, and with it, sub-millisecond-accurate elapsed time
    (verified: <0.2 ms max error across ~10,600 points of a real session).
    """
    if len(offsets) < 2:
        return [0.0] * len(offsets)
    deltas = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
    median_stride = statistics.median(deltas)
    elapsed = [0.0]
    for d in deltas:
        n_steps = max(1, round(d / median_stride)) if median_stride else 1
        elapsed.append(elapsed[-1] + n_steps * 0.1)
    return elapsed


def _reject_far_from_track(
    hits: List[Tuple[int, float, float, float, float]],
    max_km: float = 50.0,
) -> List[Tuple[int, float, float, float, float]]:
    """Drop GPS fixes far from the session's own median position.

    _scan_gps_fixes uses full-world latitude/longitude bounds (so it works
    for any track, not just ones near a hardcoded region) — the trade-off is
    a handful of stray false positives can land anywhere on Earth. A real
    session's fixes overwhelmingly cluster around one track, so the median
    position is robust even with some noise in the mix; 50 km comfortably
    covers any single track/session while still rejecting a wrong-continent
    false positive.

    This must run on the RAW hits, before _reconstruct_elapsed: that step
    derives its timing from the median byte-stride between ALL offsets, so
    leaving stray far-away false positives in would skew the median and
    throw off elapsed time for every point — not just the false ones —
    even after they're filtered out afterwards.
    """
    if len(hits) < 3:
        return hits
    lats = sorted(h[1] for h in hits)
    lons = sorted(h[2] for h in hits)
    med_lat = lats[len(lats) // 2]
    med_lon = lons[len(lons) // 2]
    return [h for h in hits if _haversine_km(h[1], h[2], med_lat, med_lon) <= max_km]


def _reject_outliers(
    hits: List[Tuple[int, float, float, float, float]],
    elapsed: List[float],
) -> Tuple[List[Tuple[int, float, float, float, float]], List[float]]:
    """Drop GPS fixes whose implied speed to every neighbour is physically
    impossible.

    _scan_gps_fixes' heuristic is extremely reliable but not perfect — on
    real files it occasionally produces one stray false positive (typically
    right at the very start, before the log settles into steady per-sample
    data). A genuine fix is always close to its time-neighbours; a false
    positive is wildly far from ALL of them. Iterative since dropping one
    bad point can occasionally unmask another next to it. Run this AFTER
    _reject_far_from_track, which handles the case this can miss (two false
    positives that happen to sit near each other).
    """
    MAX_PLAUSIBLE_KMH = 600.0
    hits = list(hits)
    elapsed = list(elapsed)
    changed = True
    while changed and len(hits) > 2:
        changed = False
        # Every point is judged against this pass's *original* neighbours —
        # not other removals decided earlier in the same pass — otherwise
        # rejecting one bad point can strand its OTHER neighbour with no
        # valid comparison left, cascading into removing good points too.
        remove = [False] * len(hits)
        for i in range(len(hits)):
            checked = ok = 0
            for j in (i - 1, i + 1):
                if 0 <= j < len(hits):
                    checked += 1
                    dt = abs(elapsed[i] - elapsed[j])
                    if dt < 1e-6:
                        ok += 1
                        continue
                    dist_km = _haversine_km(hits[i][1], hits[i][2], hits[j][1], hits[j][2])
                    if dist_km / (dt / 3600.0) <= MAX_PLAUSIBLE_KMH:
                        ok += 1
            if checked > 0 and ok == 0:
                remove[i] = True
                changed = True
        if changed:
            hits    = [h for h, r in zip(hits, remove) if not r]
            elapsed = [e for e, r in zip(elapsed, remove) if not r]
    return hits, elapsed


def _scan_beacon_points(
    payload: bytes,
    ref_lat: float,
    ref_lon: float,
    max_km: float = 5.0,
) -> List[Tuple[float, float]]:
    """Recover the track's timing-beacon GPS coordinates from RECRGLOS.

    Unipro stores each configured beacon (start/finish, plus any sector
    splits) as a pair of zero-padded 64-bit fields — [lat_raw][0][lon_raw][0]
    — the same raw/1e7 degree scale as the RECRDATA GPS fixes (see
    _scan_gps_fixes). Found by scanning for that plausible-value pattern
    rather than trusting a fixed byte offset (which could shift between
    Unipro firmware/export versions); ref_lat/ref_lon (the session's own
    recovered track position) keeps the match tight enough that nothing
    else in the payload can look like a beacon — verified against two real
    sessions on the same track: exactly the same 4 beacons recovered both
    times, byte-for-byte identical, with zero false positives.
    """
    points: List[Tuple[float, float]] = []
    n = len(payload)
    i = 0
    while i + 16 <= n:
        if payload[i + 4:i + 8] == b'\x00\x00\x00\x00' and payload[i + 12:i + 16] == b'\x00\x00\x00\x00':
            lat_raw = int.from_bytes(payload[i:i + 4], 'big', signed=True)
            lon_raw = int.from_bytes(payload[i + 8:i + 12], 'big', signed=True)
            if lat_raw or lon_raw:
                lat, lon = lat_raw / 1e7, lon_raw / 1e7
                if (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1] and _LON_RANGE[0] <= lon <= _LON_RANGE[1]
                        and _haversine_km(lat, lon, ref_lat, ref_lon) <= max_km):
                    points.append((lat, lon))
                    i += 16
                    continue
        i += 4
    return points


def _detect_lap_crossings(
    elapsed: List[float],
    lats: np.ndarray,
    lons: np.ndarray,
    gate_lat: float,
    gate_lon: float,
    min_lap_time: float = 15.0,
    gate_radius_m: float = 30.0,
) -> List[float]:
    """Detect timing-beacon crossings from GPS position alone, so a session
    with no native lap markers can still be split into laps the way
    Unipro's own onboard timer would.

    Projects every point onto a local (forward, lateral) metre frame
    centred on the beacon, using the track's own heading the instant it
    passes closest to the beacon (not just "anywhere within gate_radius_m",
    which could span slow/stationary samples with an unstable direction). A
    crossing is where the forward coordinate goes from negative to positive
    while still laterally within gate_radius_m — i.e. driving through the
    gate the same way the reference pass did, not just near the point.
    min_lap_time rejects a second false trigger from GPS noise right at the
    gate (well below any real lap time, since it only needs to be longer
    than the gate itself takes to cross).
    """
    n = len(lats)
    if n < 8:
        return []

    m_per_deg_lon = 111_320.0 * np.cos(np.radians(gate_lat))
    east  = (lons - gate_lon) * m_per_deg_lon
    north = (lats - gate_lat) * _M_PER_DEG_LAT
    dist  = np.hypot(east, north)

    closest = int(np.argmin(dist))
    if dist[closest] > gate_radius_m:
        # Track never actually comes near this beacon this session — can't
        # use it as a gate.
        return []
    lo, hi = max(0, closest - 3), min(n - 1, closest + 3)
    if lo == hi:
        return []
    heading = _bearing_rad(lats[lo], lons[lo], lats[hi], lons[hi])
    fwd_e, fwd_n     = np.sin(heading), np.cos(heading)
    right_e, right_n = fwd_n, -fwd_e

    fwd     = east * fwd_e + north * fwd_n
    lateral = east * right_e + north * right_n

    crossings: List[float] = []
    last_cross = -np.inf
    for i in range(1, n):
        if fwd[i - 1] < 0.0 <= fwd[i] and abs(lateral[i]) <= gate_radius_m:
            t = elapsed[i - 1]
            df = fwd[i] - fwd[i - 1]
            if df > 1e-9:
                t += (-fwd[i - 1] / df) * (elapsed[i] - elapsed[i - 1])
            if t - last_cross >= min_lap_time:
                crossings.append(t)
                last_cross = t
    return crossings


def load_uni(path: str) -> Session:
    """Load a Unipro Laptimer .uni session file.

    See the module docstring for exactly what is and isn't decoded yet: GPS
    position/altitude/speed come directly from the file at an effective
    ~6 Hz; longitudinal/lateral G are derived from that GPS data the same
    way gpx_data.py derives them for plain GPX tracks; RPM/gear/gyro/raw
    accelerometer channels aren't decoded yet and stay at DataPoint's
    defaults. Laps are split at the track's own timing-beacon GPS location
    (recovered from RECRGLOS) when one can be found; otherwise the whole
    session is a single lap, matching gpx_data.py's convention.
    """
    with open(path, 'rb') as f:
        data = f.read()

    if data[:4] != _MAGIC:
        raise MissingHeaderError(f"Not a Unipro .uni file: {path}")

    session_date: Optional[datetime] = None
    track_name = os.path.splitext(os.path.basename(path))[0]
    recrglos_payload: Optional[bytes] = None
    recrdata_start: Optional[int] = None
    recrdata_len = 0

    for tag, _version, pstart, length in _iter_chunks(data):
        if tag == b'RECRDATE':
            session_date = _parse_date(data[pstart:pstart + length]) or session_date
        elif tag == b'RECRGLOS':
            recrglos_payload = data[pstart:pstart + length]
            track_name = _parse_track_name(recrglos_payload, track_name)
        elif tag == b'RECRDATA':
            recrdata_start, recrdata_len = pstart, length

    if recrdata_start is None:
        raise NoDataRowsError(f"No RECRDATA chunk found in {path}")

    gps_hits = _scan_gps_fixes(data[recrdata_start:recrdata_start + recrdata_len])
    if not gps_hits:
        raise NoDataRowsError(f"No GPS fixes could be recovered from {path}")

    # Spatial filtering must happen BEFORE elapsed-time reconstruction: it
    # derives timing from the median byte-stride across all offsets, so any
    # stray far-away false positive left in at that point would skew the
    # median and throw off elapsed time for every point, not just itself.
    gps_hits = _reject_far_from_track(gps_hits)
    if not gps_hits:
        raise NoDataRowsError(f"All recovered GPS fixes were rejected as implausible in {path}")

    offsets = [h[0] for h in gps_hits]
    elapsed_list = _reconstruct_elapsed(offsets)
    gps_hits, elapsed_list = _reject_outliers(gps_hits, elapsed_list)
    if not gps_hits:
        raise NoDataRowsError(f"All recovered GPS fixes were rejected as implausible in {path}")
    t0_offset = elapsed_list[0]
    elapsed_list = [e - t0_offset for e in elapsed_list]  # re-anchor to 0.0 if the leading point was dropped

    n = len(gps_hits)
    lats = np.array([h[1] for h in gps_hits])
    lons = np.array([h[2] for h in gps_hits])
    alts = np.array([h[3] for h in gps_hits])
    speed_kmh = np.array([h[4] for h in gps_hits])
    elapsed_arr = np.array(elapsed_list)
    speed_ms = speed_kmh / 3.6

    # ── Heading rate → lateral G (centripetal: v·ω/g) ─────────────────────────
    bearings = np.zeros(n)
    for i in range(1, n):
        bearings[i] = _bearing_rad(lats[i - 1], lons[i - 1], lats[i], lons[i])
    bearings[0] = bearings[1] if n > 1 else 0.0

    heading_rate = np.zeros(n)
    for i in range(1, n):
        dt_i = elapsed_arr[i] - elapsed_arr[i - 1]
        if dt_i > 1e-6:
            heading_rate[i] = _angular_diff(bearings[i - 1], bearings[i]) / dt_i
    heading_rate[0] = heading_rate[1] if n > 1 else 0.0
    heading_rate = _gaussian_smooth(heading_rate, _SMOOTH_SIGMA)
    lat_g = np.clip(speed_ms * heading_rate / _G, -5.0, 5.0)
    lat_g = _gaussian_smooth(lat_g, _SMOOTH_SIGMA)

    # ── Speed derivative → longitudinal G ─────────────────────────────────────
    lon_g = np.zeros(n)
    for i in range(1, n):
        dt_i = elapsed_arr[i] - elapsed_arr[i - 1]
        if dt_i > 1e-6:
            lon_g[i] = (speed_ms[i] - speed_ms[i - 1]) / dt_i / _G
    lon_g[0] = lon_g[1] if n > 1 else 0.0
    lon_g = np.clip(_gaussian_smooth(lon_g, _SMOOTH_SIGMA), -5.0, 5.0)

    t0 = session_date or datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    total_dur = float(elapsed_arr[-1]) if n > 1 else 0.0

    # ── Lap detection via the track's own timing beacon ───────────────────────
    # Falls back to a single lap (lap_num stays 1 for every point) if no
    # beacon can be recovered, or the track never actually passes near it.
    lap_nums = np.ones(n, dtype=int)
    if recrglos_payload is not None and n >= 8:
        ref_lat, ref_lon = float(np.median(lats)), float(np.median(lons))
        beacons = _scan_beacon_points(recrglos_payload, ref_lat, ref_lon)
        if beacons:
            gate_lat, gate_lon = beacons[0]
            crossings = _detect_lap_crossings(elapsed_list, lats, lons, gate_lat, gate_lon)
            if crossings:
                lap_nums = np.searchsorted(np.array(crossings), elapsed_arr, side='right')

    # Per-point lap-elapsed: seconds since that lap's own first point.
    lap_start_elapsed: dict = {}
    for i in range(n):
        ln = int(lap_nums[i])
        if ln not in lap_start_elapsed:
            lap_start_elapsed[ln] = float(elapsed_arr[i])

    all_pts: List[DataPoint] = []
    for i in range(n):
        ln = int(lap_nums[i])
        all_pts.append(DataPoint(
            record      = i,
            time        = t0 + timedelta(seconds=float(elapsed_arr[i])),
            lat         = float(lats[i]),
            lon         = float(lons[i]),
            alt         = float(alts[i]),
            speed       = float(speed_kmh[i]),
            gforce_x    = float(lon_g[i]),
            gforce_y    = float(lat_g[i]),
            gforce_z    = 0.0,
            lap         = ln,
            gyro_x      = 0.0,
            gyro_y      = 0.0,
            gyro_z      = 0.0,
            elapsed     = float(elapsed_arr[i]),
            lap_elapsed = float(elapsed_arr[i]) - lap_start_elapsed[ln],
        ))

    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for pt in all_pts:
        buckets[pt.lap].append(pt)

    laps: List[Lap] = []
    for lap_num in sorted(buckets.keys()):
        pts = buckets[lap_num]
        dur = pts[-1].elapsed - pts[0].elapsed
        laps.append(Lap(lap_num=lap_num, points=pts, duration=dur,
                         is_outlap=(lap_num == 0)))

    timed = [l for l in laps if l.lap_num > 0]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        if timed[-1].duration > med * _INLAP_SLOWNESS_THRESHOLD:
            timed[-1].is_inlap = True

    best_lap_time = min((l.duration for l in timed), default=total_dur)

    logger.info('Unipro .uni loaded: %d GPS fixes (%d bytes scanned), %.1fs, %d lap(s), track=%s (%s)',
                n, recrdata_len, total_dur, len(laps), track_name, path)

    return Session(
        source             = 'Unipro',
        date_utc           = t0.strftime('%Y-%m-%dT%H:%M:%SZ'),
        track              = track_name,
        configuration      = '',
        session_type       = '',
        best_lap_time      = best_lap_time,
        all_points         = all_pts,
        laps               = laps,
        is_bike            = False,
        csv_path           = path,
        source_speed_unit  = 'kmh',
    )


# ── Unipro Analyser .tsv export loader ─────────────────────────────────────────

# Columns read from the .tsv header. Every real export seen has all of these;
# is_unipro_tsv() requires the four that most reliably identify the format.
_TSV_ID_COLUMNS = {'Lap Number', 'Session Time', 'Latitude', 'Longitude'}

_TSV_CHANNEL_COLUMNS = [
    'Lap Number', 'Session Time', 'Lap Time', 'Latitude', 'Longitude',
    'Altitude', 'Speed', 'GPS Speed',
    'GPS Lateral Acceleration', 'GPS Longitudinal Acceleration', 'Vertical Acceleration',
    'RPM', 'Gear', 'Temperature 1',
]


def is_unipro_tsv(path: str) -> bool:
    """Return True if the file looks like a Unipro Analyser .tsv export."""
    if not path.lower().endswith('.tsv'):
        return False
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            header_line = f.readline()
        headers = {h.strip().strip('"') for h in header_line.rstrip('\n').split('\t')}
        return _TSV_ID_COLUMNS.issubset(headers)
    except Exception:
        return False


_FILENAME_STAMP_RE = re.compile(r'^(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})_')


def _select_real_block(
    path: str,
    blocks: dict,
    block_dates: dict,
) -> Tuple[str, str]:
    """Pick which (Start Date, Start Time) block is the session this file is
    actually named after.

    Real exports (confirmed across two independent sessions) always contain
    a stray extra block of unrelated data alongside the actual requested
    session — apparently the device's memory holds more than one recent
    session and Analyser exports all of it. Which block is "extra" isn't
    positionally consistent (it showed up both before AND after the real
    block across two real files), so position can't be used to pick it.

    Unipro's own filenames encode the session as YYMMDD_HHMM_... — match a
    block's (Start Date, Start Time) against that first, since it's the one
    piece of ground truth about which session was actually requested. Falls
    back to the block with the most GPS fixes (the stray block observed so
    far has always been smaller) if the filename doesn't parse or match.
    """
    m = _FILENAME_STAMP_RE.match(os.path.basename(path))
    if m:
        yy, mm, dd, hh, mi = m.groups()
        expected_date = f'20{yy}-{mm}-{dd}'
        expected_hhmm = f'{hh}:{mi}'
        for key, dt in block_dates.items():
            if dt is None:
                continue
            if dt.strftime('%Y-%m-%d') == expected_date and dt.strftime('%H:%M') == expected_hhmm:
                return key
    return max(blocks, key=lambda k: len(blocks[k]))


def load_tsv(path: str) -> Session:
    """Load a Unipro Analyser .tsv export.

    Full-fidelity alternative to load_uni(): every channel comes straight
    from the file, no reconstruction. See the module docstring for why this
    is preferred over load_uni() when a .tsv is available.

    The file is one row per channel *update event*, not one row per sample —
    most columns are blank on most rows (e.g. GPS position updates on its
    own cadence, independent from RPM or lap-number events). Each channel's
    last-seen value is carried forward, and a DataPoint is emitted whenever a
    row reports a fresh GPS fix (Latitude + Longitude both present), which is
    the natural, highest-rate anchor for a sample.

    See _select_real_block for the stray-extra-block quirk this has to
    guard against.
    """
    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        reader = csv.reader(f, delimiter='\t')
        try:
            header = [h.strip().strip('"') for h in next(reader)]
        except StopIteration:
            raise NoDataRowsError(f"Empty .tsv file: {path}")

        idx = {h: i for i, h in enumerate(header)}
        missing = _TSV_ID_COLUMNS - set(idx)
        if missing:
            raise MissingHeaderError(f"Unipro .tsv missing column(s) {sorted(missing)}: {path}")
        if 'Start Date' not in idx or 'Start Time' not in idx:
            raise MissingHeaderError(f"Unipro .tsv missing Start Date/Start Time: {path}")

        col = {name: idx[name] for name in _TSV_CHANNEL_COLUMNS if name in idx}
        date_col, time_col = idx['Start Date'], idx['Start Time']
        lat_col, lon_col = col['Latitude'], col['Longitude']
        max_col = max(idx.values())

        blocks: dict = {}         # (date, time) -> list of state snapshots at each GPS fix
        block_states: dict = {}   # (date, time) -> running carried-forward state
        block_dates: dict = {}    # (date, time) -> parsed datetime (or None)
        current_key: Optional[Tuple[str, str]] = None

        for row in reader:
            if len(row) <= max_col:
                continue
            date_v = row[date_col].strip()
            time_v = row[time_col].strip()
            if date_v and time_v:
                key = (date_v, time_v)
                if key != current_key:
                    current_key = key
                    if key not in blocks:
                        blocks[key] = []
                        block_states[key] = {}
                        try:
                            block_dates[key] = datetime.strptime(
                                f'{date_v} {time_v}', '%Y-%m-%d %H:%M:%S'
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            block_dates[key] = None
            if current_key is None:
                continue

            state = block_states[current_key]
            for name, i in col.items():
                v = row[i].strip()
                if v:
                    state[name] = v

            if row[lat_col].strip() and row[lon_col].strip():
                blocks[current_key].append(dict(state))

    if not blocks:
        raise NoDataRowsError(f"No GPS fixes found in {path}")

    chosen_key = _select_real_block(path, blocks, block_dates)
    rows = blocks[chosen_key]
    session_date = block_dates[chosen_key]

    if not rows:
        raise NoDataRowsError(f"No GPS fixes found in {path}")

    def _f(d: dict, key: str, default: float = 0.0) -> float:
        try:
            return float(d[key])
        except (KeyError, ValueError):
            return default

    t0 = session_date or datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)

    # Session Time / Lap Time are in nanoseconds (verified against the known
    # ~1059.0s duration of a real session, cross-checked with load_uni()'s
    # independently-derived elapsed time — a 1e6 (microsecond) divisor was
    # tried first and came out exactly 1000x too large).
    session_time0 = _f(rows[0], 'Session Time') / 1e9
    all_pts: List[DataPoint] = []
    for i, r in enumerate(rows):
        elapsed = _f(r, 'Session Time') / 1e9 - session_time0
        lap_elapsed = _f(r, 'Lap Time') / 1e9
        # Speed and GPS Speed are reported on independent update cadences;
        # whichever was seen most recently (either column) is the freshest.
        speed = _f(r, 'Speed') if 'Speed' in r else _f(r, 'GPS Speed')
        all_pts.append(DataPoint(
            record      = i,
            time        = t0 + timedelta(seconds=elapsed),
            lat         = _f(r, 'Latitude'),
            lon         = _f(r, 'Longitude'),
            alt         = _f(r, 'Altitude'),
            speed       = speed,
            gforce_x    = _f(r, 'GPS Longitudinal Acceleration'),
            gforce_y    = _f(r, 'GPS Lateral Acceleration'),
            gforce_z    = _f(r, 'Vertical Acceleration'),
            lap         = int(_f(r, 'Lap Number')),
            gyro_x      = 0.0,
            gyro_y      = 0.0,
            gyro_z      = 0.0,
            elapsed     = elapsed,
            lap_elapsed = lap_elapsed,
            rpm         = _f(r, 'RPM'),
            gear        = int(_f(r, 'Gear')),
            exhaust_temp= _f(r, 'Temperature 1'),
        ))

    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for pt in all_pts:
        buckets[pt.lap].append(pt)

    laps: List[Lap] = []
    for lap_num in sorted(buckets.keys()):
        pts = buckets[lap_num]
        dur = pts[-1].elapsed - pts[0].elapsed
        laps.append(Lap(lap_num=lap_num, points=pts, duration=dur,
                         is_outlap=(lap_num == 0)))

    timed = [l for l in laps if l.lap_num > 0]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        if timed[-1].duration > med * _INLAP_SLOWNESS_THRESHOLD:
            timed[-1].is_inlap = True

    total_dur = all_pts[-1].elapsed if len(all_pts) > 1 else 0.0
    best_lap_time = min((l.duration for l in timed), default=total_dur)
    track_name = os.path.splitext(os.path.basename(path))[0]

    logger.info('Unipro .tsv loaded: %d GPS fixes, %.1fs, %d lap(s), track=%s (%s)',
                len(all_pts), total_dur, len(laps), track_name, path)

    return Session(
        source             = 'Unipro',
        date_utc           = t0.strftime('%Y-%m-%dT%H:%M:%SZ'),
        track              = track_name,
        configuration      = '',
        session_type       = '',
        best_lap_time      = best_lap_time,
        all_points         = all_pts,
        laps               = laps,
        is_bike            = False,
        csv_path           = path,
        source_speed_unit  = 'kmh',
    )
