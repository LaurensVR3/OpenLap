"""
gopro_data.py — Telemetry recorded by GoPro cameras (GPMF), as a Session.

HERO5 and later write GPS, accelerometer and gyro samples into a metadata
track of the video itself ("GoPro MET", codec tag gpmd), in GoPro's open
GPMF format (https://github.com/gopro/gpmf-parser). Because it is recorded on
the video's own clock, a session loaded from it is in sync with its video by
construction: the sync offset is 0, no alignment needed.

GPMF is nested KLV: a 4-byte key, a 1-byte type, a 1-byte struct size and a
2-byte big-endian repeat count, then size*repeat bytes padded to 4. Type 0 is
a nest of further KLVs. Each 1-second payload is a DEVC holding STRM streams;
a stream's samples come with its scale (SCAL) and, for GPS, the UTC time of
the payload (GPSU) and fix quality (GPSF, GPSP).

Longitudinal and lateral G come from the GPS track (as for GPX), not from the
accelerometer: accelerometer axes depend on the camera model and on how the
camera is mounted, while the track gives vehicle-frame values directly.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from data_model import DataPoint, Lap, Session, laps_from_track
from utils import _run, _popen, ffmpeg_path, ffprobe_path

logger = logging.getLogger(__name__)

# GPMF type char -> (struct format, byte size); '?' is a complex type
# described by a TYPE tag.
_TYPES = {
    'b': ('b', 1), 'B': ('B', 1), 'c': ('c', 1), 'd': ('d', 8), 'f': ('f', 4),
    'F': ('4s', 4), 'j': ('q', 8), 'J': ('Q', 8), 'l': ('l', 4), 'L': ('L', 4),
    'q': ('l', 4), 'Q': ('q', 8), 's': ('h', 2), 'S': ('H', 2),
}


def _klv(buf: bytes, start: int, end: int) -> Iterator[Tuple[str, str, int, int, object]]:
    """(key, type, struct size, repeat, payload-bytes-or-children) for each KLV."""
    off = start
    while off + 8 <= end:
        key = buf[off:off + 4].decode('latin-1')
        typ = chr(buf[off + 4])
        size = buf[off + 5]
        repeat = struct.unpack('>H', buf[off + 6:off + 8])[0]
        length = size * repeat
        body = off + 8
        if body + length > end:
            break
        if typ == '\0':
            yield key, typ, size, repeat, list(_klv(buf, body, body + length))
        else:
            yield key, typ, size, repeat, buf[body:body + length]
        off = body + ((length + 3) & ~3)


def _values(typ: str, size: int, repeat: int, data: bytes, complex_type: str = '') -> list:
    """Decode one KLV payload into a list of samples (each a tuple for
    multi-element structs)."""
    if typ == 'c':
        return [data.decode('latin-1').rstrip('\0')]
    if typ == 'U':
        return [data.decode('latin-1')]
    if typ == '?':
        fmt = '>' + ''.join(_TYPES[c][0] for c in complex_type)
    else:
        fmt_char, width = _TYPES[typ]
        n = size // width
        fmt = '>' + fmt_char * n
    step = struct.calcsize(fmt)
    out = []
    for i in range(repeat):
        chunk = data[i * step:(i + 1) * step]
        if len(chunk) < step:
            break
        v = struct.unpack(fmt, chunk)
        out.append(v if len(v) > 1 else v[0])
    return out


def _gpmf_stream_index(path: str) -> Optional[int]:
    r = _run([ffprobe_path(), '-v', 'error', '-print_format', 'json', '-show_streams', path],
             text=True, timeout=60)
    for s in json.loads(r.stdout or '{}').get('streams', []):
        if s.get('codec_tag_string') == 'gpmd':
            return int(s['index'])
    return None


def has_gpmf(path: str) -> bool:
    try:
        return _gpmf_stream_index(path) is not None
    except Exception:
        return False


def _payloads(path: str, index: int) -> List[Tuple[float, float, bytes]]:
    """(start s, duration s, bytes) of every GPMF payload, on the video clock."""
    r = _run([ffprobe_path(), '-v', 'error', '-select_streams', str(index), '-show_packets',
              '-show_entries', 'packet=pts_time,duration_time,size', '-print_format', 'json', path],
             text=True, timeout=300)
    packets = json.loads(r.stdout or '{}').get('packets', [])
    proc = _popen([ffmpeg_path(), '-v', 'error', '-i', path, '-map', f'0:{index}', '-c', 'copy',
                   '-f', 'data', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    raw, _ = proc.communicate()
    out, off = [], 0
    for p in packets:
        n = int(p['size'])
        out.append((float(p.get('pts_time') or 0.0), float(p.get('duration_time') or 1.0),
                    raw[off:off + n]))
        off += n
    return out


def _parse_gpsu(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s[:16], '%y%m%d%H%M%S.%f').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_gps(path: str) -> Tuple[Optional[datetime], np.ndarray]:
    """GPS samples of one clip: (UTC time at video time 0, array of rows
    [video_time_s, lat, lon, alt_m, speed_m_s]). Samples without a 3D fix or
    with poor precision are dropped."""
    index = _gpmf_stream_index(path)
    if index is None:
        return None, np.zeros((0, 5))
    rows: List[list] = []
    utc0: Optional[datetime] = None
    for start, dur, data in _payloads(path, index):
        for key, typ, size, rep, devc in _klv(data, 0, len(data)):
            if key != 'DEVC' or not isinstance(devc, list):
                continue
            for skey, _t, _s, _r, strm in devc:
                if skey != 'STRM' or not isinstance(strm, list):
                    continue
                tags: Dict[str, object] = {}
                samples = None
                for k, t, sz, n, v in strm:
                    if k in ('SCAL', 'GPSF', 'GPSP', 'TYPE', 'GPSU'):
                        tags[k] = _values(t, sz, n, v)
                    elif k in ('GPS5', 'GPS9'):
                        samples = (k, t, sz, n, v)
                if samples is None:
                    continue
                k, t, sz, n, v = samples
                ctype = tags.get('TYPE', [''])[0] if k == 'GPS9' else ''
                vals = _values(t, sz, n, v, ctype)
                if not vals:
                    continue
                scal = tags.get('SCAL') or [1]
                if isinstance(scal[0], tuple):
                    scal = list(scal[0])
                scal = [float(x) or 1.0 for x in (scal if len(scal) > 1 else scal * len(vals[0]))]
                if k == 'GPS5':
                    fix = (tags.get('GPSF') or [3])[0]
                    dop = (tags.get('GPSP') or [0])[0]
                    if fix < 3 or dop > 500:
                        continue
                    if utc0 is None and tags.get('GPSU'):
                        u = _parse_gpsu(str(tags['GPSU'][0]))
                        if u:
                            utc0 = u - timedelta(seconds=start)
                    for i, row in enumerate(vals):
                        lat, lon, alt, spd2d = (row[j] / scal[j] for j in range(4))
                        rows.append([start + dur * i / len(vals), lat, lon, alt, spd2d])
                else:   # GPS9: lat lon alt 2d 3d days secs dop fix, per sample
                    for i, row in enumerate(vals):
                        r = [row[j] / scal[j] for j in range(len(row))]
                        if len(r) < 9 or r[8] < 3 or r[7] > 5:
                            continue
                        if utc0 is None:
                            utc0 = (datetime(2000, 1, 1, tzinfo=timezone.utc)
                                    + timedelta(days=r[5], seconds=r[6])
                                    - timedelta(seconds=start + dur * i / len(vals)))
                        rows.append([start + dur * i / len(vals), r[0], r[1], r[2], r[3]])
    arr = np.array(rows, dtype=float) if rows else np.zeros((0, 5))
    if len(arr):
        arr = arr[np.argsort(arr[:, 0], kind='stable')]
        arr = arr[(arr[:, 1] != 0) | (arr[:, 2] != 0)]
    return utc0, arr


def _recording_clips(path: str) -> List[str]:
    """*path* and the chapters of the same recording after it (GX01…, GX02…)."""
    from session_scanner import recording_id
    rid = recording_id(path)
    if not rid:
        return [path]
    folder = os.path.dirname(os.path.abspath(path))
    chapters = {}
    for name in os.listdir(folder):
        r = recording_id(name)
        if r and r[0] == rid[0]:
            chapters[r[1]] = os.path.join(folder, name)
    out, ch = [], rid[1]
    while ch in chapters:
        out.append(chapters[ch])
        ch += 1
    return out or [path]


def load_gopro(path: str) -> Session:
    """A Session from the GPS a GoPro recorded, across all chapters of the
    recording starting at *path*. elapsed is time on the recording's own
    clock (chapters back to back), so the sync offset is exactly 0."""
    from gpx_data import track_dynamics
    from video_renderer import probe_video
    from exceptions import NoDataRowsError

    t_off, utc0, parts = 0.0, None, []
    for clip in _recording_clips(path):
        u, arr = read_gps(clip)
        if len(arr):
            arr = arr.copy()
            arr[:, 0] += t_off
            parts.append(arr)
            if utc0 is None and u is not None:
                utc0 = u - timedelta(seconds=t_off)
        t_off += probe_video(clip).duration
    gps = np.concatenate(parts) if parts else np.zeros((0, 5))
    if len(gps) < 10:
        raise NoDataRowsError(f'No GPS fix recorded in {path}')
    t, lat, lon, alt, spd = gps.T
    rate = (len(t) - 1) / max(1e-9, t[-1] - t[0])
    sigma = max(1.0, 0.4 * rate)          # ~0.4 s of smoothing at any GPS rate
    from gpx_data import _gaussian_smooth
    speed_kmh, lon_g, lat_g = track_dynamics(t, lat, lon, _gaussian_smooth(spd * 3.6, sigma), sigma)
    utc0 = utc0 or datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)

    pts = [DataPoint(record=i, time=utc0 + timedelta(seconds=float(t[i])),
                     lat=float(lat[i]), lon=float(lon[i]), alt=float(alt[i]),
                     speed=float(speed_kmh[i]), gforce_x=float(lon_g[i]), gforce_y=float(lat_g[i]),
                     gforce_z=0.0, lap=1, gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
                     elapsed=float(t[i]), lap_elapsed=float(t[i] - t[0]))
           for i in range(len(t))]
    laps = laps_from_track(pts)
    if not laps:
        for p in pts:
            p.lap = 1
        laps = [Lap(lap_num=1, points=pts, duration=float(t[-1] - t[0]))]
    timed = [l for l in laps if not l.is_outlap and not l.is_inlap]
    start = utc0 + timedelta(seconds=float(t[0]))
    return Session(source='GoPro', date_utc=start.strftime('%Y-%m-%dT%H:%M:%SZ'), track='',
                   configuration='', session_type='',
                   best_lap_time=min((l.duration for l in timed), default=float(t[-1] - t[0])),
                   all_points=pts, laps=laps, csv_path=path, source_speed_unit='kmh')
