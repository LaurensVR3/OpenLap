"""
rb_batch.py — Batch session matcher and state manager
=======================================================
Scans folders for RaceBox CSVs and video files, matches them by
timestamp proximity, and persists processing state so runs can be
resumed after interruption.

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
import os
import subprocess
import sys


def _run(cmd, **kwargs):
    """subprocess.run with no visible console window on Windows."""
    if sys.platform == 'win32':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault('startupinfo', si)
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    kwargs.setdefault('capture_output', True)
    return subprocess.run(cmd, **kwargs)
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Tuple

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.MP4', '.MOV', '.AVI', '.MKV'}
CSV_EXTENSIONS   = {'.csv', '.CSV'}
MAX_GAP          = 120.0    # seconds between consecutive segments of one recording
MATCH_WINDOW     = 3600.0   # max seconds between CSV start and video group start


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
        return None, 0.0


def scan_videos(folder: str) -> List[VideoFile]:
    """Recursively scan a folder for video files."""
    results = []
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            if Path(fname).suffix not in VIDEO_EXTENSIONS:
                continue
            path = os.path.join(root, fname)
            ct, dur = _ffprobe_creation_time(path)
            if ct is None:
                mtime = os.path.getmtime(path)
                ct    = datetime.fromtimestamp(mtime, tz=timezone.utc)
                # Subtract duration: mtime is end of recording, we want start
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


# ── CSV scanning ───────────────────────────────────────────────────────────────

def scan_csvs(folder: str) -> List[str]:
    """Recursively find all RaceBox CSV files."""
    results = []
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            if Path(fname).suffix not in CSV_EXTENSIONS:
                continue
            path = os.path.join(root, fname)
            # Quick check: must contain 'Record,Time,' header
            try:
                with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    content = f.read(2000)
                if 'Record,Time,' in content and 'RaceBox' in content:
                    results.append(path)
            except Exception:
                pass
    return results


# ── Matching ───────────────────────────────────────────────────────────────────

@dataclass
class MatchedSession:
    csv_path:     str
    video_group:  Optional[VideoGroup]
    time_delta:   float           # seconds between CSV start and video group start
    csv_start:    Optional[datetime]
    video_start:  Optional[datetime]
    matched:      bool            # True if within MATCH_WINDOW


def match_sessions(csv_paths: List[str],
                   video_groups: List[VideoGroup]) -> List[MatchedSession]:
    """
    Match each CSV to the closest video group by timestamp.
    Uses the Date UTC field from the CSV header.
    """
    from racebox_data import load_csv

    results = []
    for csv_path in csv_paths:
        try:
            # Only read metadata, not full data (fast)
            csv_start = _read_csv_start_time(csv_path)
        except Exception:
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
        ))

    # Sort by CSV start time
    results.sort(key=lambda m: m.csv_start.timestamp() if m.csv_start else 0)
    return results


def _read_csv_start_time(path: str) -> Optional[datetime]:
    """Read only the metadata block to get Date UTC."""
    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            if line.startswith('Date UTC,'):
                val = line.strip().split(',', 1)[1].strip()
                val = val.replace('Z', '+00:00')
                dt  = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            if line.startswith('Record,Time,'):
                break  # past header, not found
    return None


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
