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
  3. Group video segments that start within MAX_GAP seconds of each other
     into one "video group" — these belong to the same recording session.
  4. Match each CSV session to the video group whose start time is closest,
     within MATCH_WINDOW seconds.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

from utils import _run
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Dict, Tuple  # noqa: F401 – Tuple used in scan_pending_xrk

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.MP4', '.MOV', '.AVI', '.MKV'}
CSV_EXTENSIONS   = {'.csv', '.CSV'}
GPX_EXTENSIONS   = {'.gpx', '.GPX'}
LD_EXTENSIONS    = {'.ld',  '.LD'}
MAX_GAP          = 120.0    # seconds between consecutive segments of one recording
MATCH_WINDOW     = 3600.0   # max seconds between CSV start and video group start

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

    @property
    def sort_key(self) -> float:
        if self.creation_time:
            return self.creation_time.timestamp()
        return os.path.getmtime(self.path)


def _ffprobe_creation_time(path: str) -> Optional[datetime]:
    """Extract creation_time from video metadata via ffprobe."""
    try:
        r = _run(['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_entries', 'format_tags=creation_time:format=duration',
             path], text=True, timeout=10)
        data = json.loads(r.stdout)
        ct = (data.get('format', {}).get('tags', {}).get('creation_time') or
              data.get('format', {}).get('tags', {}).get('com.apple.quicktime.creationdate'))
        dur = float(data.get('format', {}).get('duration', 0))
        if ct:
            # Normalise timezone
            ct = ct.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ct)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt, dur
        return None, dur
    except Exception:
        logger.debug('ffprobe failed for %s', path, exc_info=True)
        return None, 0.0


def scan_videos(folder: str, progress_cb: Optional[Callable[[str], None]] = None) -> List[VideoFile]:
    """Recursively scan a folder for video files."""
    all_paths = [
        os.path.join(root, fname)
        for root, _, files in os.walk(folder)
        for fname in sorted(files)
        if Path(fname).suffix in VIDEO_EXTENSIONS
    ]
    total = len(all_paths)

    results = []
    for i, path in enumerate(all_paths, 1):
        if progress_cb:
            progress_cb(f"Reading video metadata… ({i}/{total})  {os.path.basename(path)}")
        ct, dur = _ffprobe_creation_time(path)
        if ct is None:
            mtime = os.path.getmtime(path)
            ct    = datetime.fromtimestamp(mtime, tz=timezone.utc)
            from datetime import timedelta
            if dur > 0:
                ct = ct - timedelta(seconds=dur)
        results.append(VideoFile(path=path, creation_time=ct, duration=dur))
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


def group_videos(videos: List[VideoFile]) -> List[VideoGroup]:
    """Group consecutive video segments (gap < MAX_GAP) into VideoGroups."""
    if not videos:
        return []
    groups: List[VideoGroup] = []
    cur: List[VideoFile] = [videos[0]]

    for v in videos[1:]:
        prev = cur[-1]
        prev_end = prev.creation_time.timestamp() + prev.duration if prev.creation_time else 0
        gap = v.sort_key - prev_end
        if abs(gap) <= MAX_GAP:
            cur.append(v)
        else:
            groups.append(_make_group(cur))
            cur = [v]
    groups.append(_make_group(cur))
    return groups


def _make_group(files: List[VideoFile]) -> VideoGroup:
    from datetime import timedelta
    start = files[0].creation_time
    total = sum(v.duration for v in files)
    end   = start + timedelta(seconds=total) if start else start
    return VideoGroup(files=files, start_time=start, end_time=end, total_dur=total)


# ── XRK conversion ─────────────────────────────────────────────────────────────

XRK_EXTENSIONS = {'.xrk', '.xrz', '.drk', '.XRK', '.XRZ', '.DRK'}


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

def scan_csvs(folder: str) -> List[str]:
    """Recursively find all RaceBox, AIM Mychron CSV, GPX, and MoTeC .ld files."""
    import motec_data as _motec
    results = []
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            suffix = Path(fname).suffix
            path = os.path.join(root, fname)

            if suffix in GPX_EXTENSIONS:
                # GPX files are identified by extension + quick content check
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        chunk = f.read(256)
                    if '<gpx' in chunk.lower():
                        results.append(path)
                except Exception:
                    logger.debug('Could not read GPX candidate %s', path, exc_info=True)
                continue

            if suffix in LD_EXTENSIONS:
                if _motec.is_motec_ld(path):
                    results.append(path)
                continue

            if suffix not in CSV_EXTENSIONS:
                continue

            try:
                with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    content = f.read(2000)
                if 'Record,Time,' in content and 'RaceBox' in content:
                    results.append(path)
                elif content.startswith('Time (s),') or '\nTime (s),' in content:
                    results.append(path)
            except Exception:
                logger.debug('Could not read CSV candidate %s', path, exc_info=True)
    return results


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
    if suffix == '.gpx':
        return 'GPX'
    if suffix == '.ld':
        return 'MoTeC'
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

        if csv_start and video_groups:
            for grp in video_groups:
                if grp.start_time:
                    delta = abs((csv_start - grp.start_time).total_seconds())
                    if delta < best_delta:
                        best_delta  = delta
                        best_group  = grp
                        best_vstart = grp.start_time

        matched = best_delta <= MATCH_WINDOW if best_group else False
        results.append(MatchedSession(
            csv_path    = csv_path,
            video_group = best_group if matched else None,
            time_delta  = best_delta,
            csv_start   = csv_start,
            video_start = best_vstart,
            matched     = matched,
            source      = _csv_source(csv_path),
        ))

    # Sort by CSV start time
    results.sort(key=lambda m: m.csv_start.timestamp() if m.csv_start else 0)
    return results


def _read_csv_start_time(path: str) -> Optional[datetime]:
    """Read session start time from a data file.

    GPX:     reads the first <time> element.
    RaceBox: reads the 'Date UTC,' metadata line.
    AIM:     reads the '# Session-Date:' comment or falls back to mtime.
    MoTeC:   reads the date/time fields from the binary header.
    """
    if Path(path).suffix.lower() == '.ld':
        import struct as _s
        try:
            with open(path, 'rb') as f:
                hdr = f.read(0x90)
            date_str = hdr[0x5E:0x68].split(b'\x00')[0].decode('ascii', errors='replace').strip()
            time_str = hdr[0x7E:0x86].split(b'\x00')[0].decode('ascii', errors='replace').strip()
            dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
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
