"""
session_scanner.py — Session matcher and state manager
=======================================================
Scans folders for telemetry files (RaceBox CSV, AIM XRK, MoTeC LD, GPX)
and video files, matches them by timestamp proximity, and persists
processing state so runs can be resumed after interruption.

Matching strategy:
  1. Parse session start time from CSV metadata (Date UTC field).
  2. Extract video creation time from:
     a. ffprobe QuickTime creation_time metadata  (most accurate)
     b. File modification time                    (fallback)
  3. Group the chapter files of each recording, per camera, into one "video
     group" (see _same_recording).
  4. Match each session to the recording that started nearest to it, within
     MATCH_WINDOW seconds (see recording_rank).
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

from utils import _run, ffprobe_path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Dict, Tuple  # noqa: F401 – Tuple used in scan_pending_xrk

SCAN_WORKERS = 8   # thread pool size for concurrent ffprobe / file-sniff I/O

# Lower-case; compare with is_video_file(), never with a raw suffix — camera
# files come as .MP4, .mp4 and .Mp4. The one list the scanner, the video
# server and the file pickers use. Camera side-files (GoPro .LRV/.THM, DJI
# .LRF low-res proxies) are deliberately absent: they duplicate real clips.
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts', '.webm'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico'}


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS
CSV_EXTENSIONS   = {'.csv', '.CSV'}
GPX_EXTENSIONS   = {'.gpx', '.GPX'}
LD_EXTENSIONS    = {'.ld',  '.LD'}
VBO_EXTENSIONS   = {'.vbo', '.VBO'}
UNI_EXTENSIONS   = {'.uni', '.UNI'}
TSV_EXTENSIONS   = {'.tsv', '.TSV'}
MAX_GAP          = 120.0    # tolerance for clips whose time came from the file date (see group_videos)
CHAPTER_GAP      = 3.0      # chapters of one recording are frame-contiguous; tags imply < 3 s
MATCH_WINDOW     = 3600.0   # max seconds between CSV start and video group start
CAMERA_OFFSET_WINDOW = 300.0
# Tolerance used only when *solving* a constant camera-clock offset (not for
# regular matching). Must be much tighter than MATCH_WINDOW: multi-session
# track days are often on an hourly-ish timetable, so a wide window lets a
# wrong offset alias onto a neighbouring session and look like a valid fit.

# Sentinel stored on MatchedSession.csv_path entries to identify source type
CSV_SOURCE_RACEBOX = 'racebox'
CSV_SOURCE_AIM     = 'aim'
CSV_SOURCE_MOTEC   = 'motec'


# ── Video file info ────────────────────────────────────────────────────────────

@dataclass
class VideoFile:
    path:          str
    creation_time: Optional[datetime]   # UTC, from metadata or mtime
    duration:      float                # seconds
    camera:        str = ''             # see camera_key(); '' = unknown
    time_from_mtime: bool = False       # no creation_time tag: estimated from the file date

    @property
    def sort_key(self) -> float:
        if self.creation_time:
            return self.creation_time.timestamp()
        return os.path.getmtime(self.path)


def camera_key(path: str, width: int, height: int, fps: str, codec: str) -> str:
    """Which camera recorded a clip, as far as the file says: its folder plus
    its format. Clips from one camera share all of these; a second camera
    recording at the same time (front and rear, helmet and chassis) differs
    in at least one, even when both sit in one folder. Camera files carry no
    reliable model or serial tag to use instead (DJI's carry neither)."""
    return f'{os.path.normcase(os.path.dirname(os.path.abspath(path)))}|{width}x{height}|{fps}|{codec}'


def _ffprobe_video_meta(path: str) -> dict:
    """{creation_time, duration, camera} for one clip via ffprobe."""
    try:
        r = _run([ffprobe_path(), '-v', 'quiet', '-print_format', 'json',
                  '-show_entries',
                  'format_tags=creation_time,com.apple.quicktime.creationdate:format=duration:'
                  'stream=codec_type,codec_name,width,height,r_frame_rate',
                  path], text=True, timeout=10)
        data = json.loads(r.stdout)
    except Exception:
        logger.debug('ffprobe failed for %s', path, exc_info=True)
        return {'creation_time': None, 'duration': 0.0, 'camera': ''}
    tags = data.get('format', {}).get('tags', {}) or {}
    ct = tags.get('creation_time') or tags.get('com.apple.quicktime.creationdate')
    dt = None
    if ct:
        try:
            dt = datetime.fromisoformat(ct.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
    try:
        dur = float(data.get('format', {}).get('duration', 0) or 0)
    except (TypeError, ValueError):
        dur = 0.0
    v = next((st for st in data.get('streams', []) if st.get('codec_type') == 'video'), {})
    cam = camera_key(path, v.get('width', 0), v.get('height', 0),
                     v.get('r_frame_rate', ''), v.get('codec_name', '')) if v else ''
    return {'creation_time': dt, 'duration': dur, 'camera': cam}


def _ffprobe_creation_time(path: str) -> Tuple[Optional[datetime], float]:
    """(creation_time, duration_seconds) from video metadata via ffprobe."""
    meta = _ffprobe_video_meta(path)
    return meta['creation_time'], meta['duration']


def _stat_size_mtime(path: str) -> Optional[Tuple[int, float]]:
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime
    except OSError:
        return None


def scan_videos(folder: str, progress_cb: Optional[Callable[[str], None]] = None,
                cache: Optional[Dict[str, dict]] = None) -> List[VideoFile]:
    """Recursively scan a folder for video files.

    *cache* is the 'videos' namespace of the file-meta cache (path -> {size, mtime,
    creation_time, duration}), mutated in place. Files whose (size, mtime) still
    match a cache entry reuse it instead of re-probing with ffprobe; cache misses
    (new/changed files) are probed concurrently.
    """
    all_paths = [
        os.path.join(root, fname)
        for root, _, files in os.walk(folder)
        for fname in sorted(files)
        if is_video_file(fname)
    ]
    total = len(all_paths)
    if cache is None:
        cache = {}

    results: List[Optional[VideoFile]] = [None] * total
    to_probe: List[Tuple[int, str]] = []

    for i, path in enumerate(all_paths):
        stat  = _stat_size_mtime(path)
        entry = cache.get(path)
        if (stat and entry and entry.get('size') == stat[0] and entry.get('mtime') == stat[1]
                and 'camera' in entry):   # entries written before cameras were told apart: re-probe once
            ct_raw = entry.get('creation_time')
            ct = datetime.fromisoformat(ct_raw) if ct_raw else None
            results[i] = VideoFile(path=path, creation_time=ct, duration=entry.get('duration', 0.0),
                                   camera=entry.get('camera', ''),
                                   time_from_mtime=bool(entry.get('time_from_mtime')))
        else:
            to_probe.append((i, path))

    progress_lock = threading.Lock()
    done_count = total - len(to_probe)

    def _probe(item: Tuple[int, str]) -> None:
        nonlocal done_count
        i, path = item
        meta = _ffprobe_video_meta(path)
        ct, dur, cam = meta['creation_time'], meta['duration'], meta['camera']
        from_mtime = ct is None
        if ct is None:
            mtime = os.path.getmtime(path)
            ct    = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if dur > 0:
                ct = ct - timedelta(seconds=dur)
        results[i] = VideoFile(path=path, creation_time=ct, duration=dur, camera=cam,
                               time_from_mtime=from_mtime)
        stat = _stat_size_mtime(path)
        if stat:
            cache[path] = {
                'size': stat[0], 'mtime': stat[1],
                'creation_time': ct.isoformat() if ct else None,
                'duration': dur, 'camera': cam, 'time_from_mtime': from_mtime,
            }
        if progress_cb:
            with progress_lock:
                done_count += 1
                n = done_count
            progress_cb(f"Reading video metadata… ({n}/{total})  {os.path.basename(path)}")

    if to_probe:
        with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
            list(ex.map(_probe, to_probe))

    results.sort(key=lambda v: v.sort_key)
    return results


# ── Video group ────────────────────────────────────────────────────────────────

@dataclass
class VideoGroup:
    """One or more consecutive video segments that form a single recording session."""
    files:      List[VideoFile]
    start_time: datetime      # UTC start of first segment
    end_time:   datetime      # UTC end of last segment
    total_dur:  float         # total duration in seconds

    @property
    def paths(self) -> List[str]:
        return [v.path for v in self.files]


_DJI_RE    = re.compile(r'^DJI_(\d{4})_(\d{3})$', re.I)          # DJI_0698_001
_GOPRO_RE  = re.compile(r'^G[HXL](\d{2})(\d{4})$', re.I)         # GX010123: chapter 01 of 0123


def recording_id(path: str) -> Optional[Tuple[str, int]]:
    """(recording, chapter) for a clip whose camera's file naming says so:
    DJI_<rec>_<chapter> and GoPro G[HXL]<chapter><rec>. None otherwise."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _DJI_RE.match(stem)
    if m:
        return f'DJI:{m.group(1)}', int(m.group(2))
    m = _GOPRO_RE.match(stem)
    if m:
        return f'GoPro:{m.group(2)}', int(m.group(1))
    return None


def _same_recording(prev: VideoFile, v: VideoFile) -> bool:
    """Whether *v* continues the recording *prev* is part of.

    Chapters of one recording (a camera splitting a long take into ~4 GB
    files) are frame-contiguous; their 1-second creation tags imply gaps
    under CHAPTER_GAP. Any longer gap means the camera was stopped. Grouping
    anything within 2 minutes instead merged separate runs whenever the
    driver restarted the camera quickly: on a real library all 8 groups with
    a gap over 3 s were two sessions' recordings joined into one, so both
    sessions were matched to video that began with the other run's footage.
    File names settle it when they carry a recording number. Times estimated
    from the file date are too rough for the chapter rule; they keep the
    looser MAX_GAP.
    """
    prev_end = prev.creation_time.timestamp() + prev.duration if prev.creation_time else 0
    gap = abs(v.sort_key - prev_end)
    rp, rv = recording_id(prev.path), recording_id(v.path)
    if rp and rv:
        # Same recording, the very next chapter, and not hours apart: a
        # missing chapter file would otherwise be skipped over silently,
        # shifting every frame after it by that chapter's length.
        return rp[0] == rv[0] and rv[1] == rp[1] + 1 and gap <= MAX_GAP
    tolerance = MAX_GAP if (prev.time_from_mtime or v.time_from_mtime) else CHAPTER_GAP
    return gap <= tolerance


def group_videos(videos: List[VideoFile]) -> List[VideoGroup]:
    """Group the clips of each recording (see _same_recording) into
    VideoGroups — per camera (VideoFile.camera). Grouping by time alone
    interleaved two cameras recording at once into one "recording", which
    export then joined front-rear-front-rear. Groups come in start order."""
    if not videos:
        return []
    open_groups: Dict[str, List[VideoFile]] = {}
    done: List[List[VideoFile]] = []
    for v in sorted(videos, key=lambda x: x.sort_key):
        cur = open_groups.get(v.camera)
        if cur is not None:
            if _same_recording(cur[-1], v):
                cur.append(v)
                continue
            done.append(cur)
        open_groups[v.camera] = [v]
    done.extend(open_groups.values())
    groups = [_make_group(g) for g in done]
    groups.sort(key=lambda g: g.start_time.timestamp() if g.start_time else 0)
    return groups


def _make_group(files: List[VideoFile]) -> VideoGroup:
    from datetime import timedelta
    start = files[0].creation_time
    total = sum(v.duration for v in files)
    end   = start + timedelta(seconds=total) if start else start
    return VideoGroup(files=files, start_time=start, end_time=end, total_dur=total)


def solve_camera_offset(video_groups: List[VideoGroup],
                        session_times: List[datetime],
                        window_s: float = CAMERA_OFFSET_WINDOW) -> Tuple[float, int]:
    """Find the constant offset (seconds) to add to every video group's start_time
    that best aligns it with one of *session_times*.

    Handles a camera whose clock is simply wrong (wrong time, or even wrong date) —
    the offset between "what the camera thinks" and "what actually happened" is the
    same for every clip from that camera, since only the absolute clock is off, not
    the relative timing between clips. Candidate offsets are every pairwise delta
    between a group and a session time, so the true offset is always among them if
    at least one (group, session) pair is a genuine match. For each candidate, count
    how many groups land within *window_s* of a distinct session (greedy nearest,
    one-to-one) and keep the candidate with the most matches, using total residual
    to break ties. Pure arithmetic over already-known timestamps — no file I/O.

    Returns (best_offset, match_count). (0.0, 0) if either input is empty.
    """
    groups = [g for g in video_groups if g.start_time]
    if not groups or not session_times:
        return 0.0, 0

    group_ts   = [g.start_time.timestamp() for g in groups]
    session_ts = [t.timestamp() for t in session_times]

    candidates = {st - gt for gt in group_ts for st in session_ts}

    best_offset   = 0.0
    best_count    = -1
    best_residual = float('inf')

    for cand in candidates:
        used = set()
        count = 0
        residual = 0.0
        for gt in group_ts:
            shifted = gt + cand
            best_i, best_dt = None, None
            for i, st in enumerate(session_ts):
                if i in used:
                    continue
                dt = abs(st - shifted)
                if dt <= window_s and (best_dt is None or dt < best_dt):
                    best_i, best_dt = i, dt
            if best_i is not None:
                used.add(best_i)
                count += 1
                residual += best_dt

        if count > best_count or (count == best_count and residual < best_residual):
            best_offset, best_count, best_residual = cand, count, residual

    return best_offset, best_count


# ── XRK conversion ─────────────────────────────────────────────────────────────

XRK_EXTENSIONS = {'.xrk', '.xrz', '.drk', '.XRK', '.XRZ', '.DRK'}


def resolve_xrk_csv(path: str) -> str:
    """If *path* is a raw AIM XRK/XRZ/DRK file, resolve it to the .csv
    sibling produced by convert_xrk_files() during a scan — XRK is a binary
    format none of the telemetry loaders read directly, only the converted
    CSV. Every session-loading entry point (WebviewAPI._load_one_session,
    auto_sync._load_session, ...) must call this before dispatching on
    extension/content, or an XRK path silently falls through to the RaceBox
    CSV loader and fails with a UTF-8 decode error on the binary data.

    Raises FileNotFoundError if the sibling hasn't been converted yet (e.g.
    an XRK reached here from outside a scanned folder). Returns *path*
    unchanged for every other extension.
    """
    if Path(path).suffix in XRK_EXTENSIONS:
        csv_path = os.path.splitext(path)[0] + '.csv'
        if not os.path.isfile(csv_path):
            raise FileNotFoundError(
                f"{path} is a raw AIM XRK file — scan its folder first so "
                f"OpenLap can convert it to {csv_path}"
            )
        return csv_path
    return path


def convert_xrk_files(folder: str, progress_cb: Optional[Callable[[str], None]] = None) -> List[str]:
    """
    Walk *folder* for AIM XRK/XRZ/DRK files.  Any file that does not yet have
    a matching .csv alongside it is converted using xrk_to_csv.py.

    The AIM MatLabXRK DLL is downloaded automatically on first use (same logic
    as running xrk_to_csv.py from the command line).

    progress_cb(msg: str) is called with status strings if provided.
    Returns the list of CSV paths that were newly created.
    """
    import contextlib
    import io as _io

    pending = []
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            if Path(fname).suffix not in XRK_EXTENSIONS:
                continue
            xrk_path = os.path.join(root, fname)
            csv_path = os.path.splitext(xrk_path)[0] + '.csv'
            if not os.path.isfile(csv_path):
                pending.append((xrk_path, csv_path))
            else:
                # Regenerate if the existing CSV is missing the Lap column
                # (produced by an older version of xrk_to_csv.py)
                try:
                    with open(csv_path, 'r', encoding='utf-8-sig', errors='ignore') as _f:
                        line1 = _f.readline()
                        # Skip leading comment line (e.g. '# Session-Date: …')
                        header = _f.readline() if line1.startswith('#') else line1
                    if ',Lap,' not in header and not header.rstrip('\n').endswith(',Lap'):
                        os.remove(csv_path)
                        pending.append((xrk_path, csv_path))
                except OSError as e:
                    logger.warning('Could not process stale CSV %s: %s', csv_path, e)

    if not pending:
        return []

    import sys as _sys

    try:
        import xrk_to_csv as _xrk
    except ImportError:
        _xrk = None

    # Pick a reader: Windows DLL first (with auto-download), falling back to
    # libxrk (cross-platform PyPI package). We only invoke _find_dll() on
    # Windows because its auto-download path can open a Playwright browser
    # — useless on macOS/Linux where AIM ships no native binary anyway.
    dll_path = None
    if _xrk is not None and _sys.platform == 'win32':
        if progress_cb:
            progress_cb("Locating AIM MatLabXRK DLL…")
        try:
            dll_path = _xrk._find_dll()
        except SystemExit as e:
            if progress_cb:
                progress_cb(f"XRK DLL unavailable, falling back to libxrk: {e}")
        except Exception as e:
            if progress_cb:
                progress_cb(f"XRK DLL error, falling back to libxrk: {e}")

    libxrk_fn = None
    if not dll_path:
        try:
            from xrk_to_csv_libxrk import xrk_to_csv_libxrk as libxrk_fn
        except ImportError:
            if progress_cb:
                progress_cb(
                    "XRK reader unavailable — install libxrk (`pip install libxrk`) "
                    "or place a MatLabXRK DLL next to OpenLap (Windows only)."
                )
            return []

    new_csvs: List[str] = []
    for i, (xrk_path, csv_path) in enumerate(pending):
        fname = os.path.basename(xrk_path)
        if progress_cb:
            progress_cb(f"Converting {fname}  ({i + 1}/{len(pending)})…")
        try:
            buf = _io.StringIO()
            with contextlib.redirect_stdout(buf):
                if dll_path:
                    _xrk.xrk_to_csv(xrk_path, csv_path, dll_path)
                else:
                    libxrk_fn(xrk_path, csv_path)
            new_csvs.append(csv_path)
        except SystemExit as e:
            if progress_cb:
                progress_cb(f"  ✗ {fname}: {e}")
        except Exception as e:
            if progress_cb:
                progress_cb(f"  ✗ {fname}: {e}")

    return new_csvs


# ── CSV scanning ───────────────────────────────────────────────────────────────

def _sniff_candidate(path: str, suffix: str) -> bool:
    """Read/parse a candidate file to decide whether it's a real telemetry file."""
    import motec_data as _motec

    if suffix in VBO_EXTENSIONS:
        try:
            with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                head = f.read(256)
            return '[header]' in head.lower()
        except Exception:
            logger.debug('Could not read VBO candidate %s', path, exc_info=True)
            return False

    if suffix in GPX_EXTENSIONS:
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                chunk = f.read(256)
            return '<gpx' in chunk.lower()
        except Exception:
            logger.debug('Could not read GPX candidate %s', path, exc_info=True)
            return False

    if suffix in LD_EXTENSIONS:
        return _motec.is_motec_ld(path)

    if suffix in UNI_EXTENSIONS:
        import unipro_data as _unipro
        return _unipro.is_unipro_uni(path)

    if suffix in TSV_EXTENSIONS:
        import unipro_data as _unipro
        return _unipro.is_unipro_tsv(path)

    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            content = f.read(2000)
        if 'Record,Time,' in content and 'RaceBox' in content:
            return True
        return content.startswith('Time (s),') or '\nTime (s),' in content
    except Exception:
        logger.debug('Could not read CSV candidate %s', path, exc_info=True)
        return False


def scan_csvs(folder: str, cache: Optional[Dict[str, dict]] = None) -> List[str]:
    """Recursively find all RaceBox, AIM Mychron CSV, GPX, MoTeC .ld, VBOX .vbo,
    and Unipro .uni/.tsv files.

    *cache* is the 'csvs' namespace of the file-meta cache (path -> {size, mtime,
    valid}), mutated in place. Files whose (size, mtime) still match a cache entry
    skip the sniff read entirely; cache misses are sniffed concurrently.
    """
    if cache is None:
        cache = {}

    candidates: List[Tuple[str, str]] = [
        (os.path.join(root, fname), Path(fname).suffix)
        for root, _, files in os.walk(folder)
        for fname in sorted(files)
        if (Path(fname).suffix in VBO_EXTENSIONS or Path(fname).suffix in GPX_EXTENSIONS or
            Path(fname).suffix in LD_EXTENSIONS or Path(fname).suffix in UNI_EXTENSIONS or
            Path(fname).suffix in TSV_EXTENSIONS or Path(fname).suffix in CSV_EXTENSIONS)
    ]

    valid: List[Optional[bool]] = [None] * len(candidates)
    to_sniff: List[Tuple[int, str, str]] = []

    for i, (path, suffix) in enumerate(candidates):
        stat  = _stat_size_mtime(path)
        entry = cache.get(path)
        if stat and entry and entry.get('size') == stat[0] and entry.get('mtime') == stat[1]:
            valid[i] = bool(entry.get('valid'))
        else:
            to_sniff.append((i, path, suffix))

    def _sniff(item: Tuple[int, str, str]) -> None:
        i, path, suffix = item
        is_valid = _sniff_candidate(path, suffix)
        valid[i] = is_valid
        stat = _stat_size_mtime(path)
        if stat:
            cache[path] = {'size': stat[0], 'mtime': stat[1], 'valid': is_valid}

    if to_sniff:
        with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
            list(ex.map(_sniff, to_sniff))

    return [path for (path, _), ok in zip(candidates, valid) if ok]


# ── Matching ───────────────────────────────────────────────────────────────────

@dataclass
class MatchedSession:
    csv_path:         str
    video_group:      Optional[VideoGroup]
    time_delta:       float            # seconds between CSV start and video group start
    csv_start:        Optional[datetime]
    video_start:      Optional[datetime]
    matched:          bool             # True if within MATCH_WINDOW
    source:           str  = 'RaceBox' # 'RaceBox' | 'AIM Mychron'
    needs_conversion: bool = False     # True for XRK files not yet converted to CSV
    xrk_path:         Optional[str] = None  # source XRK path when needs_conversion=True
    other_groups:     List[VideoGroup] = field(default_factory=list)
    # other cameras' recordings running when this session started (a second
    # angle for picture-in-picture), nearest first


def scan_pending_xrk(folder: str) -> List[Tuple[str, str]]:
    """Return (xrk_path, future_csv_path) for XRK files that have no matching CSV yet."""
    results = []
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            if Path(fname).suffix not in XRK_EXTENSIONS:
                continue
            xrk_path = os.path.join(root, fname)
            csv_path = os.path.splitext(xrk_path)[0] + '.csv'
            if not os.path.isfile(csv_path):
                results.append((xrk_path, csv_path))
    return results


def _csv_source(path: str) -> str:
    """Quick peek at a file to determine its data source."""
    suffix = Path(path).suffix.lower()
    if suffix in {'.xrk', '.xrz', '.drk'}:
        return 'AIM Mychron'
    if suffix == '.vbo':
        return 'VBOX'
    if suffix == '.gpx':
        return 'GPX'
    if suffix == '.ld':
        return 'MoTeC'
    if suffix == '.uni':
        return 'Unipro'
    if suffix == '.tsv':
        return 'Unipro'
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            head = f.read(300)
        if head.startswith('Time (s),') or '\nTime (s),' in head:
            return 'AIM Mychron'
    except Exception:
        pass
    return 'RaceBox'


def match_sessions(csv_paths: List[str],
                   video_groups: List[VideoGroup]) -> List[MatchedSession]:
    """
    Match each CSV to the closest video group by timestamp.
    Uses the Date UTC field from the CSV header.
    """
    results = []
    for csv_path in csv_paths:
        try:
            # Only read metadata, not full data (fast)
            csv_start = _read_csv_start_time(csv_path)
        except Exception:
            logger.debug('Could not read start time from %s', csv_path, exc_info=True)
            csv_start = None

        best_group  = None
        best_delta  = float('inf')
        best_vstart = None
        others: list = []

        if csv_start and video_groups:
            ranked = sorted(((recording_rank(csv_start, g), i, g)
                             for i, g in enumerate(video_groups) if g.start_time),
                            key=lambda r: (r[0], r[1]))
            if ranked:
                (_, best_delta), _, best_group = ranked[0]
                best_vstart = best_group.start_time
                # Another camera's recording around the same time: a second
                # angle for picture-in-picture.
                others = [g for (short, delta), _, g in ranked[1:]
                          if not short and g.files[0].camera != best_group.files[0].camera
                          and (delta <= CAMERA_OFFSET_WINDOW or _overlaps(csv_start, g))]

        matched = best_delta <= MATCH_WINDOW if best_group else False
        results.append(MatchedSession(
            csv_path    = csv_path,
            video_group = best_group if matched else None,
            time_delta  = best_delta,
            csv_start   = csv_start,
            video_start = best_vstart,
            matched     = matched,
            source      = _csv_source(csv_path),
            other_groups = others if matched else [],
        ))

    # Sort by CSV start time
    results.sort(key=lambda m: m.csv_start.timestamp() if m.csv_start else 0)
    return results


# A recording shorter than this is a phone snippet or an accidental press,
# not a session's onboard video; it is only matched if nothing longer is.
MIN_RECORDING_S = 30.0


def recording_rank(csv_start: datetime, group: 'VideoGroup') -> Tuple[int, float]:
    """Sort key for how likely *group* is the video of a session starting at
    *csv_start* (lower is better): (too short, seconds between the two starts).

    Start proximity, because drivers start the logger and the camera within
    seconds of each other (every user-confirmed offset in a real library was
    within ±20 s), while camera clocks are routinely minutes off. Ranking by
    "was the camera running when the session started" was tried and measured
    worse: with a clock 2.5 min fast, the previous run's recording looked as
    if it was still running and beat the session's own. A constant clock
    error does not change which recording *started* nearest.
    """
    short = 1 if group.total_dur < MIN_RECORDING_S else 0
    return short, abs((csv_start - group.start_time).total_seconds())


def recording_distance(csv_start: datetime, group: 'VideoGroup') -> float:
    """Seconds between a session's start and a recording's start."""
    return recording_rank(csv_start, group)[1]


def _overlaps(csv_start: datetime, group: 'VideoGroup') -> bool:
    end = group.end_time or group.start_time
    return group.start_time <= csv_start <= end


def _read_csv_start_time(path: str) -> Optional[datetime]:
    """Read session start time from a data file.

    VBOX:    reads date from [comments] and time from first [data] row.
    GPX:     reads the first <time> element.
    RaceBox: reads the 'Date UTC,' metadata line.
    AIM:     reads the '# Session-Date:' comment or falls back to mtime.
    MoTeC:   reads the date/time fields from the binary header.
    """
    if Path(path).suffix.lower() == '.vbo':
        try:
            import re as _re
            sections: dict = {}
            current = None
            with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                for line in f:
                    line = line.rstrip('\n\r')
                    if line.startswith('[') and line.endswith(']'):
                        current = line[1:-1].strip().lower()
                        sections[current] = []
                    elif current is not None and line.strip():
                        sections[current].append(line)
            comments = '\n'.join(sections.get('comments', []))
            dm = _re.search(r'(\d{2})/(\d{2})/(\d{4})', comments)
            session_date = (datetime(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)),
                                     tzinfo=timezone.utc) if dm else None)
            channels = [c.strip().lower() for c in sections.get('header', [])]
            idx_time = next((i for i, c in enumerate(channels) if c == 'time'), None)
            data_lines = sections.get('data', [])
            if session_date and idx_time is not None and data_lines:
                cols = data_lines[0].split()
                if idx_time < len(cols):
                    raw = float(cols[idx_time])
                    h = int(raw) // 10000
                    m = (int(raw) // 100) % 100
                    s = round(raw - h * 10000 - m * 100, 6)
                    return session_date + timedelta(hours=h, minutes=m, seconds=s)
            if session_date:
                return session_date
        except Exception:
            logger.debug('Could not read VBOX start time from %s', path, exc_info=True)
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    if Path(path).suffix.lower() == '.ld':
        import struct as _s
        try:
            with open(path, 'rb') as f:
                hdr = f.read(0x90)
            date_str = hdr[0x5E:0x68].split(b'\x00')[0].decode('ascii', errors='replace').strip()
            time_str = hdr[0x7E:0x86].split(b'\x00')[0].decode('ascii', errors='replace').strip()
            # Local time on the logger's clock (see motec_data.load_ld).
            return datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S").astimezone(timezone.utc)
        except Exception:
            pass
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    if Path(path).suffix.lower() == '.gpx':
        import re
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(4096)
            m = re.search(r'<time>([^<]+)</time>', content)
            if m:
                val = m.group(1).strip().replace('Z', '+00:00')
                dt = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
        except Exception:
            pass
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    if Path(path).suffix.lower() == '.uni':
        import unipro_data as _unipro
        try:
            with open(path, 'rb') as f:
                head = f.read(4096)  # RECRDATE is always the first chunk
            for tag, _version, pstart, length in _unipro._iter_chunks(head):
                if tag == b'RECRDATE':
                    dt = _unipro._parse_date(head[pstart:pstart + length])
                    if dt:
                        return dt
                    break
        except Exception:
            logger.debug('Could not read Unipro start time from %s', path, exc_info=True)
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    if Path(path).suffix.lower() == '.tsv':
        # Real .tsv exports (see unipro_data.py) can contain a stray extra
        # block of unrelated session data alongside the real one, so a full
        # parse (unipro_data.load_tsv) is needed to reliably tell them apart
        # — too expensive for a quick scan-time peek. Unipro's own filenames
        # encode the session as YYMMDD_HHMM_..., which is exactly the ground
        # truth load_tsv() itself uses to disambiguate, so it's a reliable
        # cheap stand-in here too.
        import unipro_data as _unipro
        m = _unipro._FILENAME_STAMP_RE.match(os.path.basename(path))
        if m:
            yy, mm, dd, hh, mi = m.groups()
            try:
                return datetime(2000 + int(yy), int(mm), int(dd), int(hh), int(mi),
                                 tzinfo=timezone.utc)
            except ValueError:
                pass
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        head = f.readline()
    if head.startswith('# Session-Date:') or head.startswith('Time (s),'):
        # AIM: its GPS clock, not the logger's local-time Log Date.
        import aim_data as _aim
        gps = _aim.gps_utc_start(path)
        if gps is not None:
            return gps

    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            # AIM CSV: embedded session date comment
            if line.startswith('# Session-Date:'):
                val = line.split(':', 1)[1].strip()
                val = val.replace('Z', '+00:00')
                try:
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    break
            # RaceBox CSV: Date UTC metadata line
            if line.startswith('Date UTC,'):
                val = line.strip().split(',', 1)[1].strip()
                val = val.replace('Z', '+00:00')
                dt  = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            if line.startswith('Record,Time,') or line.startswith('Time (s),'):
                break  # past header, not found

    # Fallback: file mtime
    mtime = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


# ── Batch state ────────────────────────────────────────────────────────────────

@dataclass
class SessionState:
    csv_path:     str
    video_paths:  List[str]
    sync_offset:  Optional[float]   # None = not yet synced
    status:       str               # 'pending' | 'synced' | 'rendering' | 'done' | 'error'
    output_files: List[str]         = field(default_factory=list)
    error_msg:    str               = ''
    lap_mode:     str               = 'fastest'  # 'all' | 'fastest' | 'selection'
    selected_laps: List[int]        = field(default_factory=list)


@dataclass
class BatchState:
    output_dir:  str
    sessions:    List[SessionState] = field(default_factory=list)
    created_at:  str = ''
    version:     int = 2

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=2, default=str)

    @staticmethod
    def load(path: str) -> 'BatchState':
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        sessions = [SessionState(**s) for s in data.get('sessions', [])]
        return BatchState(
            output_dir  = data.get('output_dir', ''),
            sessions    = sessions,
            created_at  = data.get('created_at', ''),
            version     = data.get('version', 1),
        )

    def get_session(self, csv_path: str) -> Optional[SessionState]:
        return next((s for s in self.sessions if s.csv_path == csv_path), None)

    def upsert_session(self, sess: SessionState) -> None:
        for i, s in enumerate(self.sessions):
            if s.csv_path == sess.csv_path:
                self.sessions[i] = sess
                return
        self.sessions.append(sess)

    @property
    def pending(self) -> List[SessionState]:
        return [s for s in self.sessions if s.status in ('pending', 'synced')]

    @property
    def done(self) -> List[SessionState]:
        return [s for s in self.sessions if s.status == 'done']


def build_batch_state(matches: List[MatchedSession],
                      output_dir: str) -> BatchState:
    """Create a fresh BatchState from matched sessions."""
    state = BatchState(
        output_dir  = output_dir,
        created_at  = datetime.now(tz=timezone.utc).isoformat(),
    )
    for m in matches:
        if not m.matched:
            continue
        ss = SessionState(
            csv_path    = m.csv_path,
            video_paths = m.video_group.paths if m.video_group else [],
            sync_offset = None,
            status      = 'pending',
        )
        state.sessions.append(ss)
    return state
