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
  SPEED    unit read from channel header; m/s → km/h (* 3.6) unless the
           unit tag says 'km/h' or 'mph'
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

import numpy as np

from data_model import DataPoint, Lap, Session
from exceptions import NoDataRowsError

logger = logging.getLogger(__name__)

_G = 9.80665  # m/s² per G

# ─────────────────────────────────────────────────────────────────────────────
# Channel name patterns for fuzzy matching (checked in order, first wins)
# ─────────────────────────────────────────────────────────────────────────────
_CHANNEL_ALIASES: Dict[str, List[str]] = {
    'speed':    ['SPEED', 'VEHICLE SPEED'],
    'g_lat':    ['G_LAT', 'GLAT', 'LATG', 'VEHICLE ACCELERATION LATERAL'],
    'g_lon':    ['G_LON', 'GLON', 'LONG', 'VEHICLE ACCELERATION LONGITUDINAL'],
    'rpm':      ['RPMS', 'RPM', 'ENGINE_RPM', 'ENGINE SPEED'],
    'time':     ['TIME'],
    'lap':      ['LAP_BEACON', 'LAPBEACON', 'LAP', 'LAP BEACON NUMBER'],
    'throttle': ['THROTTLE', 'THROTTLE POSITION', 'THROTTLE PEDAL'],
    'brake':    ['BRAKE', 'BRAKE STATE'],
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
    """Return the first channel that matches one of the aliases for *field*.

    Matching is case/whitespace-insensitive: ACC sim exports use upper-case
    names like 'G_LAT', real M1 hardware exports use title-case names with
    spaces like 'Vehicle Acceleration Lateral' — normalise both sides so one
    alias list covers both without needing every literal casing.
    """
    aliases = _CHANNEL_ALIASES.get(field, [field.upper()])
    norm_channels = {name.upper().strip(): ch for name, ch in channels.items()}
    for alias in aliases:
        ch = norm_channels.get(alias.upper().strip())
        if ch is not None:
            return ch
    return None


def _classify_speed_unit(unit_str: str) -> Tuple[str, float]:
    """Classify a SPEED channel's unit tag into (source_speed_unit, factor_to_kmh).
    Falls back to m/s (today's implicit default) for empty/unrecognised tags."""
    low = (unit_str or '').lower()
    if 'mph' in low:
        return 'mph', 1.60934
    if 'km' in low:
        return 'kmh', 1.0
    return 'ms', 3.6


_DTYPE_FORMATS: Dict[int, str] = {
    4: 'f',   # float32 — used by all channels in ACC sim exports
    2: 'h',   # int16   — used by state/discrete channels (Gear, Brake State, ...)
              # in real M1 hardware exports; shift/mul/scale/dec were confirmed
              # identity (0,1,1,0) on real files seen so far, so no scaling is
              # applied here beyond decoding the raw samples.
}


def _read_samples(data: bytes, ch: dict) -> List[float]:
    """Read all samples for a channel, decoding per its dtype (float32 or int16)."""
    offset = ch['data_ptr']
    n = ch['n_data']
    fmt = _DTYPE_FORMATS.get(ch['dtype'])
    if n == 0 or fmt is None:
        return []
    size = struct.calcsize(fmt)
    if offset + n * size > len(data):
        return []
    try:
        return list(struct.unpack_from(f'<{n}{fmt}', data, offset))
    except struct.error:
        return []


def _read_float32(data: bytes, ch: dict) -> List[float]:
    """Read all float32 samples for a channel."""
    return _read_samples(data, ch)


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


def _interp_step(
    target_times: List[float],
    src_times:    List[float],
    src_values:   List[float],
    default: float = 0.0,
) -> List[float]:
    """
    Nearest-below ("step-hold") resampling of *src_values* onto *target_times*.
    Unlike _interp, this never blends between samples — used for discrete
    channels (gear, lap number) where a fractional value between two states
    would be meaningless.
    """
    if not src_times or not src_values:
        return [default] * len(target_times)

    st = src_times
    sv = src_values
    result: List[float] = []

    for t in target_times:
        if t < st[0]:
            result.append(default)
            continue
        if t >= st[-1]:
            result.append(sv[-1])
            continue
        lo, hi = 0, len(st) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if st[mid] <= t:
                lo = mid
            else:
                hi = mid
        result.append(sv[lo])

    return result


def _extract_extras(
    data: bytes,
    channels: Dict[str, dict],
    consumed_ids: set,
    abs_times: List[float],
    ch_times_fn,
) -> Tuple[List[Dict[str, float]], Dict[str, dict]]:
    """
    Interpolate every channel *not* in *consumed_ids* (i.e. not already
    mapped to a fixed DataPoint field) onto abs_times, for the generic
    DataPoint.extra bag. Vectorised with numpy — real hardware exports can
    have 100+ extra channels, and a naive per-sample Python loop (mirroring
    _interp's manual binary search) took 25+ seconds to load a single file;
    this brings that down to a fraction of a second.

    Step-hold for discrete (dtype=2) channels, linear otherwise — same rule
    already used for the named fields — with the same out-of-range
    convention as _interp/_interp_step (0.0 outside the channel's own
    recorded time range, except step-hold's late side which holds the last
    sample, matching _interp_step exactly).

    Returns (per_sample_extras, extra_channel_meta):
      per_sample_extras[i] is a {channel_name: value} dict for abs_times[i].
      extra_channel_meta[name] is {'label': str, 'unit': str}.
    """
    n = len(abs_times)
    if n == 0:
        return [], {}

    target = np.asarray(abs_times, dtype=np.float64)
    names: List[str] = []
    meta: Dict[str, dict] = {}
    columns: List[np.ndarray] = []

    for name, ch in channels.items():
        if id(ch) in consumed_ids:
            continue
        vals = _read_samples(data, ch)
        if not vals:
            continue
        times = np.asarray(ch_times_fn(ch), dtype=np.float64)
        vals_np = np.asarray(vals, dtype=np.float64)

        if ch['dtype'] == 2:
            idx = np.searchsorted(times, target, side='right') - 1
            idx = np.clip(idx, 0, len(vals_np) - 1)
            series = vals_np[idx]
            series = np.where(target < times[0], 0.0, series)
        else:
            series = np.interp(target, times, vals_np, left=0.0, right=0.0)

        names.append(name)
        meta[name] = {'label': name, 'unit': ch['unit'] or ''}
        columns.append(series)

    if not columns:
        return [dict() for _ in range(n)], {}

    matrix = np.stack(columns, axis=0)  # (n_channels, n_points)
    per_sample = [dict(zip(names, row)) for row in matrix.T.tolist()]
    return per_sample, meta


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _load_sim_points(
    data: bytes,
    channels: Dict[str, dict],
    time_ch: dict,
    dt: datetime,
    path: str,
) -> Tuple[List[DataPoint], str, Dict[str, dict]]:
    """Build DataPoints for MoTeC i2 ACC sim exports (lap-relative TIME channel present).

    Returns (points, source_speed_unit, extra_channel_meta).
    """
    raw_time = _read_float32(data, time_ch)
    if not raw_time:
        raise NoDataRowsError(f"TIME channel is empty in {path}")

    abs_times, lap_nums = _build_abs_time(raw_time)
    session_duration = abs_times[-1]

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

    speed_t,    speed_v    = _ch_vals('speed')     # m/s (default; see unit sniff below)

    speed_ch = _find_channel(channels, 'speed')
    source_speed_unit, speed_to_kmh = _classify_speed_unit(speed_ch['unit'] if speed_ch else '')

    g_lat_t,    g_lat_v    = _ch_vals('g_lat')     # m/s²
    g_lon_t,    g_lon_v    = _ch_vals('g_lon')     # m/s²
    rpm_t,      rpm_v      = _ch_vals('rpm')
    throttle_t, throttle_v = _ch_vals('throttle')  # %
    brake_t,    brake_v    = _ch_vals('brake')     # %
    gear_t,     gear_v     = _ch_vals('gear')

    # Every channel not mapped to one of the fixed fields above becomes a
    # generic DataPoint.extra entry (see channel_discovery.py for how the UI
    # filters/lists these).
    consumed_ids = {
        id(ch) for ch in (
            time_ch, speed_ch,
            _find_channel(channels, 'g_lat'), _find_channel(channels, 'g_lon'),
            _find_channel(channels, 'rpm'), _find_channel(channels, 'throttle'),
            _find_channel(channels, 'brake'), _find_channel(channels, 'gear'),
        ) if ch is not None
    }
    extras, extra_meta = _extract_extras(data, channels, consumed_ids, abs_times, _ch_times)

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
        speed_kmh  = s_speed[i] * speed_to_kmh
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
            extra       = extras[i],
        )
        all_pts.append(pt)

    return all_pts, source_speed_unit, extra_meta


def _load_hardware_points(
    data: bytes,
    channels: Dict[str, dict],
    dt: datetime,
    path: str,
) -> Tuple[List[DataPoint], str, Dict[str, dict]]:
    """Build DataPoints for real M1 hardware exports (M150 etc — no TIME channel).

    Returns (points, source_speed_unit, extra_channel_meta).

    Every channel is sampled at its own fixed frequency for the whole session
    (no circular-buffer truncation like ACC sim exports), so there's no
    lap-relative clock to reconstruct absolute time from. Instead, the
    highest-frequency full-length channel is used as the time base and every
    other channel is resampled onto it — linear interpolation for continuous
    channels, step-hold for discrete ones (gear, lap number).
    """
    freq_channels = [ch for ch in channels.values() if ch['freq']]
    if not freq_channels:
        raise NoDataRowsError(f"No usable channels found in {path}")

    session_duration = max((ch['n_data'] - 1) / ch['freq'] for ch in freq_channels)

    base_candidates = [
        ch for ch in freq_channels
        if abs((ch['n_data'] - 1) / ch['freq'] - session_duration) < 1.0
    ]
    base_ch = max(base_candidates, key=lambda c: c['freq'])
    abs_times = [i / base_ch['freq'] for i in range(base_ch['n_data'])]

    def _ch_times_from_zero(ch: dict) -> List[float]:
        """Time axis for a channel spanning the full session, starting at t=0."""
        freq = ch['freq'] or 1
        return [i / freq for i in range(ch['n_data'])]

    def _ch_vals(field: str) -> Tuple[Optional[List[float]], Optional[List[float]]]:
        ch = _find_channel(channels, field)
        if ch is None:
            return None, None
        vals = _read_samples(data, ch)   # dtype-aware: handles float32 and int16
        if not vals:
            return None, None
        return _ch_times_from_zero(ch), vals

    speed_t, speed_v = _ch_vals('speed')

    speed_ch = _find_channel(channels, 'speed')
    speed_unit_tag = speed_ch['unit'] if speed_ch else ''
    if speed_unit_tag:
        source_speed_unit, speed_to_kmh = _classify_speed_unit(speed_unit_tag)
    else:
        # Real hardware exports don't tag channel units the way ACC sim
        # exports do. Empirically, real M150 'Vehicle Speed' CAN data is
        # already in km/h — confirmed against a full session's sample
        # distribution (median/p90/p99, not just the max) — unlike the sim
        # path, which defaults untagged speed to m/s.
        source_speed_unit, speed_to_kmh = 'kmh', 1.0

    g_lat_t,    g_lat_v    = _ch_vals('g_lat')
    g_lon_t,    g_lon_v    = _ch_vals('g_lon')
    rpm_t,      rpm_v      = _ch_vals('rpm')
    gear_t,     gear_v     = _ch_vals('gear')
    lap_t,      lap_v      = _ch_vals('lap')

    consumed_ids = {
        id(ch) for ch in (
            speed_ch,
            _find_channel(channels, 'g_lat'), _find_channel(channels, 'g_lon'),
            _find_channel(channels, 'rpm'), _find_channel(channels, 'gear'),
            _find_channel(channels, 'lap'),
        ) if ch is not None
    }
    extras, extra_meta = _extract_extras(data, channels, consumed_ids, abs_times, _ch_times_from_zero)

    s_speed = _interp(abs_times, speed_t, speed_v) if speed_v else [0.0] * len(abs_times)
    s_g_lat = _interp(abs_times, g_lat_t, g_lat_v) if g_lat_v else [0.0] * len(abs_times)
    s_g_lon = _interp(abs_times, g_lon_t, g_lon_v) if g_lon_v else [0.0] * len(abs_times)
    s_rpm   = _interp(abs_times, rpm_t,   rpm_v)   if rpm_v   else [0.0] * len(abs_times)
    # Discrete channels: step-hold, never blend between states.
    s_gear  = _interp_step(abs_times, gear_t, gear_v) if gear_v else [0.0] * len(abs_times)
    s_lap   = _interp_step(abs_times, lap_t,  lap_v)  if lap_v  else [0.0] * len(abs_times)

    n = len(abs_times)
    all_pts: List[DataPoint] = []
    for i in range(n):
        speed_kmh = s_speed[i] * speed_to_kmh
        gx        = s_g_lon[i] / _G   # longitudinal G — 0.0 when no such channel exists
        gy        = s_g_lat[i] / _G   # lateral G — 0.0 when no such channel exists
        elapsed   = abs_times[i]
        lap_num   = max(0, round(s_lap[i]))
        gear      = int(round(s_gear[i]))

        lean = -math.degrees(math.atan(gy))

        pt = DataPoint(
            record      = i,
            time        = dt,             # session-start datetime, constant
            lat         = 0.0,            # no GPS on the ECU
            lon         = 0.0,
            alt         = 0.0,
            speed       = speed_kmh,
            gforce_x    = gx,
            gforce_y    = gy,
            gforce_z    = 0.0,
            lap         = lap_num,
            gyro_x      = 0.0, gyro_y = 0.0, gyro_z = 0.0,
            lean_angle  = lean,
            elapsed     = elapsed,
            lap_elapsed = 0.0,             # filled in below, per lap
            rpm         = s_rpm[i],
            exhaust_temp= 0.0,
            gear        = gear,
            extra       = extras[i],
        )
        all_pts.append(pt)

    # No lap-relative clock on hardware exports — derive lap_elapsed the same
    # way aim_data.py does: measure each point against its lap's first sample.
    by_lap: Dict[int, List[DataPoint]] = defaultdict(list)
    for pt in all_pts:
        by_lap[pt.lap].append(pt)
    for pts in by_lap.values():
        lap_t0 = pts[0].elapsed
        for pt in pts:
            pt.lap_elapsed = pt.elapsed - lap_t0

    return all_pts, source_speed_unit, extra_meta


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

    Two export flavors are supported, detected by the presence of a TIME
    channel:
      - ACC sim exports (TIME channel present): lap-relative time with
        circular channel buffers (~1 lap of detail in typical exports).
      - Real M1 hardware exports, e.g. M150 (no TIME channel): every channel
        spans the full session at its own fixed frequency instead.
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

    # ── Detect flavor: ACC sim exports have a lap-relative TIME channel;   ──
    # ── real M1 hardware exports (M150 etc.) don't — every channel is just ──
    # ── sampled at its own fixed frequency for the whole session instead.  ──
    time_ch = _find_channel(channels, 'time')

    if time_ch is not None:
        all_pts, source_speed_unit, extra_channel_meta = _load_sim_points(data, channels, time_ch, dt, path)
    else:
        all_pts, source_speed_unit, extra_channel_meta = _load_hardware_points(data, channels, dt, path)

    # ── Group into laps ────────────────────────────────────────────────────
    buckets: Dict[int, List[DataPoint]] = defaultdict(list)
    for pt in all_pts:
        buckets[pt.lap].append(pt)

    laps: List[Lap] = []
    for lap_num in sorted(buckets.keys()):
        pts = buckets[lap_num]
        # lap_elapsed was set above, lap-relative (from raw_time on sim exports,
        # from elapsed-minus-lap-start on hardware exports)
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
        source_speed_unit = source_speed_unit,
        extra_channel_meta = extra_channel_meta,
    )
