"""
motec_data.py — MoTeC i2 .ld binary data model
================================================
Parses MoTeC .ld files (produced by the MoTeC i2 ACC plugin or similar)
and returns the same Session/Lap/DataPoint objects used by the rest of
OpenLap.

File structure recap (little-endian):
  Offset 0x00  uint32  header_size (always 0x40)
  Offset 0x08  uint32  channel_list_ptr   → first channel header
  Offset 0x24  uint32  meta_ptr           → track name (null-padded, 64 bytes)
  Offset 0x5E  char[16]  date  "dd/mm/yyyy"
  Offset 0x7E  char[16]  time  "hh:mm:ss"
  Offset 0x6E2 char[32]  vehicle name

Each channel header (0x7C bytes):
  +0x00  uint32  prev_ptr
  +0x04  uint32  next_ptr
  +0x08  uint32  data_ptr
  +0x0C  uint32  n_data        (sample count in this block)
  +0x10  uint32  chan_id       (monotonic ID, ignored)
  +0x14  uint16  dtype         (4 = float32 in all observed files)
  +0x16  uint16  freq          (Hz)
  +0x18..+0x1F  shift/mul/scale/dec (all 1 in ACC files — raw floats need no transform)
  +0x20  char[32] name
  +0x40  char[8]  short_name
  +0x48  char[12] unit

Data encoding: dtype=4 → packed IEEE-754 float32, no further scaling needed.

Key channels used by this module:
  SPEED    m/s    → km/h (* 3.6)
  G_LAT    m/s²   → lateral G (/ 9.81)
  G_LON    m/s²   → longitudinal G (/ 9.81)
  RPMS     1/min  → rpm
  TIME            → lap-relative elapsed time (s); resets at each lap beacon
  LAP_BEACON      → (usually all-zero in ACC exports; TIME resets used instead)

Buffer sizes in ACC exports:
  • Most channels (60/100/200 Hz): circular buffer ~136 s → last ~1 lap
  • 50 Hz channels incl. TIME: full session buffer

Lap detection: monotonic absolute time is reconstructed from the TIME channel
by accumulating values between resets (large negative diffs).
"""

from __future__ import annotations

import logging
import math
import os
import struct
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from data_model import DataPoint, Lap, Session
from exceptions import NoDataRowsError

logger = logging.getLogger(__name__)

_G = 9.80665  # m/s² per G

# ─────────────────────────────────────────────────────────────────────────────
# Channel name patterns for fuzzy matching (checked in order, first wins)
# ─────────────────────────────────────────────────────────────────────────────
_CHANNEL_ALIASES: Dict[str, List[str]] = {
    'speed':    ['SPEED'],
    'g_lat':    ['G_LAT', 'GLAT', 'LATG'],
    'g_lon':    ['G_LON', 'GLON', 'LONG'],
    'rpm':      ['RPMS', 'RPM', 'ENGINE_RPM'],
    'time':     ['TIME'],
    'lap':      ['LAP_BEACON', 'LAPBEACON', 'LAP'],
    'throttle': ['THROTTLE'],
    'brake':    ['BRAKE'],
    'gear':     ['GEAR'],
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cstr(buf: bytes, offset: int, max_len: int = 64) -> str:
    """Read a null-terminated ASCII string from a byte buffer."""
    raw = buf[offset: offset + max_len]
    return raw.split(b'\x00')[0].decode('ascii', errors='replace').strip()


def _parse_channels(data: bytes, list_ptr: int) -> Dict[str, dict]:
    """Walk the channel linked list and return a dict keyed by channel name."""
    channels: Dict[str, dict] = {}
    visited: set = set()
    ptr = list_ptr
    while ptr and ptr not in visited:
        if ptr + 0x60 > len(data):
            break
        visited.add(ptr)
        prev, nxt, data_ptr, n_data, chan_id = struct.unpack_from('<IIIII', data, ptr)
        dtype, freq = struct.unpack_from('<HH', data, ptr + 0x14)
        shift, mul, scale, dec = struct.unpack_from('<hhhh', data, ptr + 0x18)
        name = _cstr(data, ptr + 0x20, 32)
        short = _cstr(data, ptr + 0x40, 8)
        unit = _cstr(data, ptr + 0x48, 12)
        ch = dict(
            prev=prev, next=nxt, data_ptr=data_ptr, n_data=n_data,
            chan_id=chan_id, dtype=dtype, freq=freq,
            shift=shift, mul=mul, scale=scale, dec=dec,
            name=name, short=short, unit=unit,
        )
        if name and name not in channels:
            channels[name] = ch
        ptr = nxt
    return channels


def _find_channel(channels: Dict[str, dict], field: str) -> Optional[dict]:
    """Return the first channel that matches one of the aliases for *field*."""
    aliases = _CHANNEL_ALIASES.get(field, [field.upper()])
    for alias in aliases:
        if alias in channels:
            return channels[alias]
    return None


def _read_float32(data: bytes, ch: dict) -> List[float]:
    """Read all float32 samples for a channel."""
    offset = ch['data_ptr']
    n = ch['n_data']
    if n == 0 or offset + n * 4 > len(data):
        return []
    try:
        import struct as _s
        return list(_s.unpack_from(f'<{n}f', data, offset))
    except struct.error:
        return []


def _build_abs_time(raw_time: List[float]) -> Tuple[List[float], List[int]]:
    """
    Convert lap-relative TIME channel values into absolute elapsed times
    and assign lap numbers.

    Returns (abs_times, lap_nums) each of length len(raw_time).
    Lap number 0 is the first (possibly partial) lap; subsequent laps start
    at 1, 2, …
    """
    n = len(raw_time)
    abs_times: List[float] = [0.0] * n
    lap_nums:  List[int]   = [0] * n

    offset = 0.0
    lap = 0
    abs_times[0] = raw_time[0]
    lap_nums[0] = lap

    for i in range(1, n):
        if raw_time[i] < raw_time[i - 1] - 5.0:   # lap reset
            offset += raw_time[i - 1]
            lap += 1
        abs_times[i] = offset + raw_time[i]
        lap_nums[i] = lap

    return abs_times, lap_nums


def _interp(
    target_times: List[float],
    src_times:    List[float],
    src_values:   List[float],
    default: float = 0.0,
) -> List[float]:
    """
    Linear interpolation of *src_values* (sampled at *src_times*) onto
    *target_times*.  Values outside the src range are filled with *default*.
    """
    if not src_times or not src_values:
        return [default] * len(target_times)

    st = src_times
    sv = src_values
    result: List[float] = []

    for t in target_times:
        if t <= st[0]:
            result.append(default if t < st[0] else sv[0])
            continue
        if t >= st[-1]:
            result.append(default if t > st[-1] else sv[-1])
            continue
        # Binary search for interval
        lo, hi = 0, len(st) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if st[mid] <= t:
                lo = mid
            else:
                hi = mid
        p0, p1 = st[lo], st[hi]
        dt = p1 - p0
        a = (t - p0) / dt if dt else 0.0
        result.append(sv[lo] + (sv[hi] - sv[lo]) * a)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_motec_ld(path: str) -> bool:
    """Return True if the file looks like a MoTeC .ld binary file."""
    if not path.lower().endswith('.ld'):
        return False
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        if len(header) < 16:
            return False
        # First uint32 = 0x40 (file header size = 64), second = 0
        h0 = struct.unpack_from('<I', header, 0)[0]
        h1 = struct.unpack_from('<I', header, 4)[0]
        return h0 == 0x40 and h1 == 0
    except Exception:
        return False


def load_ld(path: str) -> Session:
    """
    Load a MoTeC .ld file and return a Session.

    The session always contains at least lap-timing data from the TIME channel.
    Detailed telemetry (speed, G-forces, RPM) is available for the portion of
    the session covered by the circular channel buffers (~1 lap in typical
    ACC exports).
    """
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 0x200:
        raise NoDataRowsError(f"File too small to be a valid MoTeC .ld: {path}")

    # ── Parse file header ──────────────────────────────────────────────────
    chan_list_ptr = struct.unpack_from('<I', data, 0x08)[0]
    meta_ptr      = struct.unpack_from('<I', data, 0x24)[0]

    date_str = _cstr(data, 0x5E, 16)   # "dd/mm/yyyy"
    time_str = _cstr(data, 0x7E, 16)   # "hh:mm:ss"
    track    = _cstr(data, meta_ptr, 64) if meta_ptr else ''
    vehicle  = _cstr(data, 0x6E2, 32)

    # Build a UTC ISO timestamp from header date/time
    date_utc = ''
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        date_utc = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        mtime = os.path.getmtime(path)
        dt    = datetime.fromtimestamp(mtime, tz=timezone.utc)
        date_utc = dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Fallback: derive track from filename (e.g. "Spa-porsche_992_gt3_r-7-…")
    if not track:
        stem  = os.path.splitext(os.path.basename(path))[0]
        track = stem.split('-')[0] if '-' in stem else stem

    # ── Parse channel list ─────────────────────────────────────────────────
    channels = _parse_channels(data, chan_list_ptr)
    if not channels:
        raise NoDataRowsError(f"No channels found in {path}")

    # ── Read TIME channel (50 Hz, full session) ────────────────────────────
    time_ch = _find_channel(channels, 'time')
    if time_ch is None:
        raise NoDataRowsError(f"TIME channel not found in {path}")

    raw_time = _read_float32(data, time_ch)
    if not raw_time:
        raise NoDataRowsError(f"TIME channel is empty in {path}")

    time_freq = time_ch['freq'] or 50
    abs_times, lap_nums = _build_abs_time(raw_time)
    session_duration = abs_times[-1]

    # ── Read telemetry channels ────────────────────────────────────────────
    def _ch_times(ch: dict) -> List[float]:
        """Build the absolute time axis for a short (circular-buffer) channel."""
        n = ch['n_data']
        freq = ch['freq'] or 1
        # The channel data ends at the same point as the session.
        t_start = session_duration - (n - 1) / freq
        return [t_start + i / freq for i in range(n)]

    def _ch_vals(field: str) -> Tuple[Optional[List[float]], Optional[List[float]]]:
        ch = _find_channel(channels, field)
        if ch is None:
            return None, None
        vals = _read_float32(data, ch)
        if not vals:
            return None, None
        return _ch_times(ch), vals

    speed_t,    speed_v    = _ch_vals('speed')     # m/s
    g_lat_t,    g_lat_v    = _ch_vals('g_lat')     # m/s²
    g_lon_t,    g_lon_v    = _ch_vals('g_lon')     # m/s²
    rpm_t,      rpm_v      = _ch_vals('rpm')
    throttle_t, throttle_v = _ch_vals('throttle')  # %
    brake_t,    brake_v    = _ch_vals('brake')     # %
    gear_t,     gear_v     = _ch_vals('gear')

    # ── Interpolate channels onto the TIME base ────────────────────────────
    s_speed    = _interp(abs_times, speed_t,    speed_v)    if speed_v    else [0.0] * len(abs_times)
    s_g_lat    = _interp(abs_times, g_lat_t,    g_lat_v)    if g_lat_v   else [0.0] * len(abs_times)
    s_g_lon    = _interp(abs_times, g_lon_t,    g_lon_v)    if g_lon_v   else [0.0] * len(abs_times)
    s_rpm      = _interp(abs_times, rpm_t,      rpm_v)      if rpm_v     else [0.0] * len(abs_times)
    s_throttle = _interp(abs_times, throttle_t, throttle_v) if throttle_v else [0.0] * len(abs_times)
    s_brake    = _interp(abs_times, brake_t,    brake_v)    if brake_v   else [0.0] * len(abs_times)
    s_gear     = _interp(abs_times, gear_t,     gear_v)     if gear_v    else [0.0] * len(abs_times)

    # ── Build DataPoints ───────────────────────────────────────────────────
    n = len(abs_times)
    all_pts: List[DataPoint] = []
    for i in range(n):
        speed_kmh  = s_speed[i] * 3.6
        gx         = s_g_lon[i] / _G    # longitudinal G
        gy         = s_g_lat[i] / _G    # lateral G
        elapsed    = abs_times[i]
        lap_rel    = raw_time[i]

        # Derive lean angle from lateral G (valid for both bikes and cars,
        # but only meaningful as a lean proxy on two-wheelers).
        # Negate: lateral G positive=left gives lean positive=left; we store positive=right.
        lean = -math.degrees(math.atan(gy))

        pt = DataPoint(
            record      = i,
            time        = dt,            # session-start datetime, constant
            lat         = 0.0,           # no GPS in MoTeC sim data
            lon         = 0.0,
            alt         = 0.0,
            speed       = speed_kmh,
            gforce_x    = gx,
            gforce_y    = gy,
            gforce_z    = 0.0,
            lap         = lap_nums[i],
            gyro_x      = 0.0, gyro_y = 0.0, gyro_z = 0.0,
            lean_angle  = lean,
            elapsed     = elapsed,
            lap_elapsed = lap_rel,
            rpm         = s_rpm[i],
            exhaust_temp= 0.0,
        )
        all_pts.append(pt)

    # ── Group into laps ────────────────────────────────────────────────────
    buckets: Dict[int, List[DataPoint]] = defaultdict(list)
    for pt in all_pts:
        buckets[pt.lap].append(pt)

    laps: List[Lap] = []
    for lap_num in sorted(buckets.keys()):
        pts = buckets[lap_num]
        # lap_elapsed was set above as raw_time (lap-relative)
        dur = pts[-1].lap_elapsed - pts[0].lap_elapsed
        # If the first sample already has lap_elapsed > 0 the lap was started
        # before recording began; add that offset back for the true duration.
        dur += pts[0].lap_elapsed
        laps.append(Lap(
            lap_num   = lap_num,
            points    = pts,
            duration  = max(dur, 0.0),
            is_outlap = (lap_num == 0),
        ))

    # Classify laps:
    # 1. Determine a "typical" lap length from the longest laps (ignoring very
    #    short segments that are sector markers or aborted triggers in the sim).
    # 2. Any lap shorter than 30% of the typical lap is treated as an outlap
    #    (a beacon artifact, not a real timed lap).
    # 3. The last long lap is marked as inlap if it is > 1.5× the median.
    timed = [l for l in laps if not l.is_outlap]
    if timed:
        long_laps = [l for l in timed if l.duration > 60.0]
        if len(long_laps) >= 1:
            ref_dur = sorted(l.duration for l in long_laps)[len(long_laps) // 2]
            min_valid = ref_dur * 0.85
            for lap in timed:
                if lap.duration < min_valid:
                    lap.is_outlap = True   # reclassify as beacon artifact

    timed = [l for l in laps if not l.is_outlap]
    if len(timed) >= 3:
        med = sorted(l.duration for l in timed)[len(timed) // 2]
        if timed[-1].duration > med * 1.5:
            timed[-1].is_inlap = True

    best_timed = [l for l in timed if not l.is_inlap]
    best_lap_time = min((l.duration for l in best_timed), default=0.0)

    return Session(
        source        = 'MoTeC',
        date_utc      = date_utc,
        track         = track,
        configuration = '',
        session_type  = '',
        best_lap_time = best_lap_time,
        all_points    = all_pts,
        laps          = laps,
        is_bike       = False,
        csv_path      = path,
    )
