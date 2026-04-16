"""
rb_render.py — Video rendering engine
=======================================
Handles video joining (ffmpeg), frame rendering (multiprocessing),
and final mux. No GUI state — all inputs passed explicitly.
"""

from __future__ import annotations
import logging
import math
import os
import tempfile
from collections import deque
from multiprocessing import Pool
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

from utils import _run, _popen
import cv2
import numpy as np

from racebox_data import Session, Lap
from overlay_worker import render_frame_worker, scale_factor, default_layout
from exceptions import VideoConcatError, VideoMuxError, LapOutOfRangeError


# ── FFmpeg helpers ─────────────────────────────────────────────────────────────

def detect_encoder() -> str:
    """Detect best available hardware encoder, fall back to libx264."""
    tests = [
        (['ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'nullsrc',
          '-t', '0.1', '-c:v', 'h264_nvenc', '-f', 'null', '-'], 'h264_nvenc'),
        (['ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'nullsrc',
          '-t', '0.1', '-c:v', 'h264_amf',   '-f', 'null', '-'], 'h264_amf'),
        (['ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'nullsrc',
          '-t', '0.1', '-c:v', 'h264_qsv',   '-f', 'null', '-'], 'h264_qsv'),
    ]
    for cmd, enc in tests:
        try:
            r = _run(cmd, timeout=5)
            if r.returncode == 0:
                return enc
        except Exception:
            pass
    return 'libx264'


def concat_videos(input_files: List[str], output: str) -> None:
    """Join video files using ffmpeg concat demuxer (no re-encode)."""
    with tempfile.NamedTemporaryFile('w', suffix='.txt',
                                     delete=False, encoding='utf-8') as f:
        for p in input_files:
            f.write(f"file '{os.path.abspath(p)}'\n")
        concat_file = f.name
    try:
        cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
               '-i', concat_file, '-c', 'copy', output]
        r = _run(cmd)
        if r.returncode != 0:
            cmd2 = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                    '-i', concat_file,
                    '-c:v', 'libx264', '-crf', '18', '-c:a', 'aac', output]
            r2 = _run(cmd2)
            if r2.returncode != 0:
                raise VideoConcatError(r2.stderr.decode(errors='replace')[-600:])
    finally:
        os.unlink(concat_file)


class MultiCap:
    """
    Virtual VideoCapture over multiple files.
    Exposes the same .get()/.set()/.read()/.release() interface as a
    single cv2.VideoCapture, so callers need no special-casing.
    Frame indices are global across all clips; seeks are O(1).
    """

    def __init__(self, paths: List[str]):
        self._caps: List[cv2.VideoCapture] = []
        self._offsets: List[int] = []   # global start frame of each clip
        self._counts:  List[int] = []   # frame count of each clip
        self._fps: float = 30.0
        self._total: int = 0
        self._cur_global: int = 0

        offset = 0
        for p in paths:
            cap = cv2.VideoCapture(p)
            if not cap.isOpened():
                raise IOError(f"Cannot open video: {p}")
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            cnt = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._caps.append(cap)
            self._offsets.append(offset)
            self._counts.append(cnt)
            self._fps = fps          # assume homogeneous; last wins
            offset += cnt

        self._total = offset

    # ── cv2.VideoCapture-compatible interface ──────────────────────────────

    def isOpened(self) -> bool:
        return bool(self._caps)

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FPS:
            return self._fps
        if prop_id == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._total)
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        if prop_id == cv2.CAP_PROP_POS_FRAMES:
            self._cur_global = int(value)
            return True
        return False

    def read(self):
        fidx = self._cur_global
        if fidx < 0 or fidx >= self._total:
            return False, None

        # Find which clip owns this global frame
        clip_idx = 0
        for i, (off, cnt) in enumerate(zip(self._offsets, self._counts)):
            if off + cnt > fidx:
                clip_idx = i
                break

        local_frame = fidx - self._offsets[clip_idx]
        cap = self._caps[clip_idx]
        cap.set(cv2.CAP_PROP_POS_FRAMES, local_frame)
        ret, frame = cap.read()
        self._cur_global = fidx + 1
        return ret, frame

    def release(self):
        for cap in self._caps:
            cap.release()
        self._caps.clear()


def mux_audio(raw_video: str, audio_source: str,
               output: str, encoder: str, crf: int = 18,
               audio_start: float = 0.0,
               total_s: float = 0.0,
               prog_start: float = 87.0,
               prog_end: float = 100.0,
               progress_cb=None) -> None:
    """Re-encode raw opencv video with hardware encoder + trim audio.

    When *progress_cb* and *total_s* are provided the function parses ffmpeg's
    machine-readable progress output and calls progress_cb(pct, msg) as the
    mux advances, interpolating between prog_start and prog_end.
    """
    import threading, subprocess as _sp

    # Quality args — nvenc uses -cq (constant quality, like CRF) not -qp (fixed QP)
    if encoder == 'libx264':
        q_arg = ['-crf', str(crf)]
    elif encoder == 'h264_nvenc':
        q_arg = ['-rc', 'vbr', '-cq', str(crf), '-b:v', '0']
    else:
        q_arg = ['-qp', str(crf)]

    # Force yuv420p: MJPG from OpenCV is yuvj420p (full-range) which hardware
    # encoders (nvenc/amf/qsv) reject.  Also ensure even dimensions.
    vf = 'scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p'

    # -profile:v main + -g 60: broad player compatibility + regular keyframes
    # -movflags +faststart: moov atom at file start, required for proper seeking
    base_cmd = ['ffmpeg', '-y', '-hide_banner',
                '-i', raw_video,
                '-ss', f'{audio_start:.6f}', '-i', audio_source,
                '-map', '0:v', '-map', '1:a?',
                '-vf', vf,
                '-c:v', encoder] + q_arg + [
                '-profile:v', 'main', '-g', '60',
                '-c:a', 'aac', '-shortest',
                '-movflags', '+faststart']

    if progress_cb and total_s > 0:
        # Send machine-readable progress to stdout; suppress normal stats on stderr.
        cmd = base_cmd + ['-progress', 'pipe:1', '-nostats', output]
        proc = _popen(cmd,
                      stdout=_sp.PIPE, stderr=_sp.PIPE)

        # Drain stderr in a background thread so it never blocks the process.
        stderr_buf: list[bytes] = []
        def _drain():
            stderr_buf.extend(proc.stderr)
        t = threading.Thread(target=_drain, daemon=True)
        t.start()

        pct_range = prog_end - prog_start
        for raw_line in proc.stdout:
            line = raw_line.decode(errors='replace').strip()
            if line.startswith('out_time_ms='):
                try:
                    us = int(line.split('=', 1)[1])   # value is microseconds despite the name
                    elapsed = us / 1_000_000.0
                    frac = min(1.0, elapsed / total_s)
                    pct = prog_start + frac * pct_range
                    progress_cb(pct, f"Muxing audio…  {elapsed:.1f} / {total_s:.1f}s")
                except (ValueError, ZeroDivisionError):
                    pass

        proc.wait()
        t.join()
        if proc.returncode != 0:
            err = b''.join(stderr_buf).decode(errors='replace')
            raise VideoMuxError(err[-600:])
    else:
        cmd = base_cmd + [output]
        r = _run(cmd)
        if r.returncode != 0:
            raise VideoMuxError(r.stderr.decode(errors='replace')[-600:])


def video_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    try:
        r = _run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path], text=True)
        return float(r.stdout.strip())
    except Exception:
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        fc  = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        return fc / fps if fps else 0.0


# ── Render job ────────────────────────────────────────────────────────────────

class RenderJob:
    """Describes one output video to render."""
    def __init__(self, label: str, lap: Optional[Lap]):
        self.label     = label
        self.lap       = lap
        self.gpx_start = lap.elapsed_start if lap else None
        self.gpx_end   = lap.elapsed_end   if lap else None
        self.duration  = lap.duration      if lap else 0.0


# ── Render helpers ────────────────────────────────────────────────────────────

def _setup_delta_time(reference_lap, job, session):
    """Pre-compute all delta-time state needed before the frame loop.

    Returns a dict with keys:
        delta_fn, cur_lap_t, cur_lap_d, cur_lap_profiles,
        ref_dist_u, ref_channels, sectors
    Returns None for all keys when reference_lap is None.
    """
    if reference_lap is None:
        return dict(delta_fn=None, cur_lap_t=None, cur_lap_d=None,
                    cur_lap_profiles={}, ref_dist_u=None,
                    ref_channels={}, sectors=[])

    import numpy as np
    from delta_time import compute_lap_profile, make_delta_fn

    delta_fn = make_delta_fn(reference_lap, current_lap_duration=job.duration)

    if job.lap is not None:
        cur_lap_t, cur_lap_d = compute_lap_profile(job.lap)
        cur_lap_profiles     = {}
    else:
        cur_lap_t, cur_lap_d = None, None
        # Only build profiles for timed laps — outlap/inlap have meaningless
        # distance profiles and using them corrupts delta for the real laps.
        cur_lap_profiles     = {lap.lap_num: compute_lap_profile(lap)
                                 for lap in session.timed_laps}

    # Reference channel arrays (indexed by unique distance)
    ref_elapsed_full, ref_dist_full = compute_lap_profile(reference_lap)
    _, ref_u_idx = np.unique(ref_dist_full, return_index=True)
    ref_dist_u   = ref_dist_full[ref_u_idx]
    ref_pts      = reference_lap.points

    def _ref_arr(attr):
        return np.array([getattr(p, attr, 0.0) for p in ref_pts], dtype=float)[ref_u_idx]

    ref_channels = {
        'speed':        _ref_arr('speed'),
        'gx':           _ref_arr('gforce_x'),
        'gy':           _ref_arr('gforce_y'),
        'lean':         _ref_arr('lean_angle'),
        'rpm':          _ref_arr('rpm'),
        'exhaust_temp': _ref_arr('exhaust_temp'),
        'alt':          _ref_arr('alt'),
    }

    # Sector splits
    sectors = []
    if job.lap is not None and cur_lap_t is not None and len(ref_dist_u) > 1:
        N_SECTORS  = 3
        total_dist = float(ref_dist_u[-1])
        if total_dist > 50.0:
            ref_elapsed_u = ref_elapsed_full[ref_u_idx]
            _, cur_u_idx  = np.unique(cur_lap_d, return_index=True)
            cur_dist_u    = cur_lap_d[cur_u_idx]
            cur_elapsed_u = cur_lap_t[cur_u_idx]
            max_cur_dist  = float(cur_dist_u[-1])
            boundaries    = [total_dist * i / N_SECTORS for i in range(1, N_SECTORS + 1)]

            for i, b in enumerate(boundaries):
                prev_b    = boundaries[i - 1] if i > 0 else 0.0
                ref_entry = float(np.interp(prev_b, ref_dist_u, ref_elapsed_u))
                ref_exit  = float(np.interp(b,      ref_dist_u, ref_elapsed_u))
                ref_sec_t = ref_exit - ref_entry

                if b <= max_cur_dist:
                    cur_entry        = float(np.interp(prev_b, cur_dist_u, cur_elapsed_u))
                    cur_exit         = float(np.interp(b,      cur_dist_u, cur_elapsed_u))
                    cur_sec_t        = cur_exit - cur_entry
                    delta            = cur_sec_t - ref_sec_t
                    done             = True
                    boundary_elapsed = cur_exit
                else:
                    cur_sec_t        = None
                    delta            = None
                    done             = False
                    boundary_elapsed = float('inf')

                sectors.append({
                    'num':              i + 1,
                    'ref_t':            ref_sec_t,
                    'cur_t':            cur_sec_t,
                    'delta':            delta,
                    'done':             done,
                    'boundary_elapsed': boundary_elapsed,
                })

    return dict(delta_fn=delta_fn, cur_lap_t=cur_lap_t, cur_lap_d=cur_lap_d,
                cur_lap_profiles=cur_lap_profiles, ref_dist_u=ref_dist_u,
                ref_channels=ref_channels, sectors=sectors)


def _build_session_meta(session, info_overrides: dict = None) -> dict:
    """Assemble the session-info dict passed to the info gauge."""
    meta: dict = {
        'info_track':   session.track        or '',
        'info_vehicle': getattr(session, 'vehicle', '') or '',
        'info_session': session.session_type or '',
        'info_source':  session.source       or '',
        'info_date':    '',
        'info_time':    '',
        'info_weather': '',
        'info_wind':    '',
    }
    if session.date_utc:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(session.date_utc.replace('Z', '+00:00'))
            meta['info_date'] = dt.strftime('%Y-%m-%d')
            meta['info_time'] = dt.strftime('%H:%M')
        except Exception:
            pass

    # Apply manual per-session overrides (non-empty values only)
    for key in ('info_track', 'info_vehicle', 'info_session'):
        if info_overrides and info_overrides.get(key):
            meta[key] = info_overrides[key]

    # Fetch weather from Open-Meteo when GPS and date are available
    if session.date_utc:
        try:
            first_gps = next(
                (p for p in session.all_points
                 if getattr(p, 'lat', 0.0) and getattr(p, 'lon', 0.0)),
                None)
            if first_gps:
                from weather import fetch_weather
                meta['info_weather'], meta['info_wind'] = fetch_weather(
                    first_gps.lat, first_gps.lon, session.date_utc)
        except Exception:
            pass

    return meta


def _build_map_data(job, session, show_map):
    """Downsample GPS track and build a numpy array for fast nearest-point lookup.

    Returns (map_lats, map_lons, map_arr_np) where map_arr_np is shape (N, 2)
    or None when show_map is False / no GPS data is available.
    """
    import numpy as np

    if not show_map:
        return [], [], None

    pts  = job.lap.points if job.lap else session.all_points
    step = max(1, len(pts) // 600)
    ds   = pts[::step]

    lats = [p.lat for p in ds]
    lons = [p.lon for p in ds]
    if not lats:
        return [], [], None

    arr = np.array(list(zip(lats, lons)), dtype=np.float64)
    return lats, lons, arr


# ── Main render function ───────────────────────────────────────────────────────

def render_lap(
    video_path:     str,
    out_path:       str,
    session:        Session,
    job:            RenderJob,
    sync_offset:    float,
    encoder:        str,
    crf:            int,
    n_workers:      int,
    show_map:       bool,
    show_telemetry: bool,
    padding:        float = 5.0,
    is_bike:        bool  = False,
    overlay_layout: Optional[dict] = None,   # normalized positions/sizes
    progress_cb:    Optional[Callable[[float, str], None]] = None,
    log_cb:         Optional[Callable[[str], None]] = None,
    reference_lap:  Optional[Lap] = None,    # lap to compare against for delta time
    info_overrides: Optional[dict] = None,  # manual session-info overrides {info_track, …}
    overlay_only:   bool  = False,           # render transparent overlay .mov (ProRes 4444)
) -> None:
    """
    Render one video with telemetry overlay.

    overlay_layout: dict with 'map' and 'telemetry' keys, each containing
                    {visible, x, y, w, h} normalized 0..1.
                    Defaults to default_layout() if None.
    """
    layout = overlay_layout or default_layout()

    def log(msg):
        if log_cb: log_cb(msg)
    def prog(pct, msg):
        if progress_cb: progress_cb(pct, msg)

    # ── Delta time setup ───────────────────────────────────────────────────────
    dt_state = _setup_delta_time(reference_lap, job, session)
    _delta_fn         = dt_state['delta_fn']
    _cur_lap_t        = dt_state['cur_lap_t']
    _cur_lap_d        = dt_state['cur_lap_d']
    _cur_lap_profiles = dt_state['cur_lap_profiles']
    _ref_dist_u       = dt_state['ref_dist_u']
    _ref_channels     = dt_state['ref_channels']
    _sectors          = dt_state['sectors']

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ── Frame range ────────────────────────────────────────────────────────────
    sync_offset = sync_offset or 0.0

    if job.gpx_start is not None:
        vid_lap_start = sync_offset + job.gpx_start
        vid_lap_end   = sync_offset + job.gpx_end
        vid_start     = max(0.0, vid_lap_start - padding)
        vid_end       = min(total / fps, vid_lap_end + padding)
        f_start       = max(0, int(vid_start * fps))
        f_end         = min(total, int(math.ceil(vid_end * fps)))
        lap_t0        = job.gpx_start
        lap_dur       = job.duration
    else:
        f_start = 0; f_end = total
        vid_start = 0.0
        lap_t0 = 0.0; lap_dur = 0.0; padding = 0.0

    n_frames    = f_end - f_start
    audio_start = vid_start

    vid_dur_s = total / fps if fps else 0.0
    log(f"  Encoder: {encoder}  |  Video: {vw}×{vh} @ {fps:.2f}fps  |  Duration: {vid_dur_s:.1f}s")
    if job.gpx_start is not None:
        log(f"  Lap duration: {job.duration:.2f}s  (session pos: {job.gpx_start:.1f}s → {job.gpx_end:.1f}s)")
        log(f"  Sync offset:  {sync_offset:.3f}s  →  video window: {vid_start:.1f}s → {vid_end:.1f}s  ({n_frames} frames)")

    if n_frames <= 0:
        cap.release()
        need_start = vid_lap_start - padding
        raise LapOutOfRangeError(
            f"Lap is {job.duration:.1f}s long (at session position {job.gpx_start:.1f}s–{job.gpx_end:.1f}s), "
            f"but with sync offset {sync_offset:.1f}s this maps to video time {need_start:.1f}s–{vid_lap_end+padding:.1f}s, "
            f"which is outside the video duration of {vid_dur_s:.1f}s. "
            f"Set the sync offset in the Data tab (scrub to where lap 1 starts, then click Mark)."
        )

    if overlay_only:
        import subprocess as _sp, threading as _th, queue as _q_mod
        _ov_blank = np.zeros((vh, vw, 4), dtype=np.uint8)
        _ov_proc  = _popen(
            ['ffmpeg', '-y', '-hide_banner',
             '-f', 'rawvideo', '-vcodec', 'rawvideo',
             '-s', f'{vw}x{vh}', '-r', str(fps),
             '-pix_fmt', 'rgba', '-i', 'pipe:0',
             '-vcodec', 'prores_ks', '-profile:v', '4444',
             '-pix_fmt', 'yuva444p10le',
             out_path],
            stdin=_sp.PIPE, stderr=_sp.PIPE,
        )
        _ov_stderr: list = []
        _ov_queue: _q_mod.Queue = _q_mod.Queue(maxsize=n_workers * 4)

        def _ov_write_loop():
            while True:
                item = _ov_queue.get()
                if item is None:
                    _ov_proc.stdin.close()
                    break
                _ov_proc.stdin.write(item)

        _th.Thread(target=lambda: _ov_stderr.extend(_ov_proc.stderr), daemon=True).start()
        _ov_writer = _th.Thread(target=_ov_write_loop, daemon=False)
        _ov_writer.start()
        writer  = None
        tmp_raw = None
    else:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_start)
        tmp_raw = out_path.replace('.mp4', '_raw.avi')
        writer  = cv2.VideoWriter(
            tmp_raw, cv2.VideoWriter_fourcc(*'MJPG'), fps, (vw, vh))

    # ── Session metadata + max speed + map data ───────────────────────────────
    _session_meta = _build_session_meta(session, info_overrides)

    speed_pts = job.lap.points if job.lap else session.all_points
    if speed_pts:
        raw_max   = max(p.speed for p in speed_pts)
        max_speed = max(50.0, math.ceil(raw_max * 1.10 / 50) * 50)
    else:
        max_speed = 300.0

    map_lats, map_lons, _map_arr_np = _build_map_data(job, session, show_map)

    # Reference lap GPS (downsampled) — used by the Zoomed map style
    _ref_map_lats: list = []
    _ref_map_lons: list = []
    _ref_lap_duration: float = 0.0
    if reference_lap and reference_lap.points:
        step = max(1, len(reference_lap.points) // 600)
        _ref_map_lats = [p.lat for p in reference_lap.points[::step]]
        _ref_map_lons = [p.lon for p in reference_lap.points[::step]]
        _ref_lap_duration = reference_lap.duration
        # Smooth the reference GPS track to reduce dot jitter caused by GPS noise.
        # A window of 9 samples over ~600 points gives gentle smoothing without
        # distorting the track shape.
        if len(_ref_map_lats) > 9:
            _w = np.ones(9) / 9
            _ref_map_lats = np.convolve(_ref_map_lats, _w, mode='same').tolist()
            _ref_map_lons = np.convolve(_ref_map_lons, _w, mode='same').tolist()

    # ── Lap-scoreboard pre-computation ────────────────────────────────────────
    # For each lap number, store the best completed timed-lap duration seen
    # BEFORE that lap started (lap 1 → None, lap 2 → lap-1 time, etc.)
    _total_timed = len(session.timed_laps)
    _best_by_lap: dict = {}
    _running_best = None
    for _lap in sorted(session.timed_laps, key=lambda l: l.lap_num):
        _best_by_lap[_lap.lap_num] = _running_best
        if _running_best is None or _lap.duration < _running_best:
            _running_best = _lap.duration
    _best_fallback = _running_best   # used for outlap / inlap / beyond last timed lap

    # ── History buffers (deque gives O(1) eviction, no manual trimming) ───────
    HISTORY_MAX   = int(10.0 * fps)
    history_buf   = deque(maxlen=HISTORY_MAX)
    _ref_hist_buf = deque(maxlen=HISTORY_MAX)

    chunk     = max(4, n_workers * 2)
    frame_idx = f_start
    processed = 0

    pool = Pool(n_workers) if n_workers > 1 else None
    try:
        while frame_idx < f_end:
            chunk_frames, chunk_meta = [], []

            for _ in range(chunk):
                if frame_idx >= f_end:
                    break
                if overlay_only:
                    frm = _ov_blank
                else:
                    ret, frm = cap.read()
                    if not ret:
                        break

                vid_t     = frame_idx / fps
                sess_t    = vid_t - sync_offset
                raw_lap_t = sess_t - lap_t0

                pt = session.interpolate_at(sess_t)
                if pt:
                    # For per-lap export: clamp to [0, lap_dur] so the timer
                    # stays sane during padding.  For full-session export: use
                    # pt.lap_elapsed directly — it resets to 0 at each lap
                    # boundary as recorded in the telemetry.
                    if job.gpx_start is not None:
                        lap_t_display = min(raw_lap_t, lap_dur)
                    else:
                        lap_t_display = pt.lap_elapsed

                    # ── Delta time ─────────────────────────────────────────────
                    delta_val = 0.0
                    cur_d     = 0.0
                    if _delta_fn is not None:
                        try:
                            if _cur_lap_t is not None:
                                cur_d = float(np.interp(
                                    pt.lap_elapsed, _cur_lap_t, _cur_lap_d))
                            else:
                                profile = _cur_lap_profiles.get(pt.lap)
                                if profile is not None:
                                    cur_d = float(np.interp(
                                        pt.lap_elapsed, profile[0], profile[1]))
                            delta_val = _delta_fn(pt.lap_elapsed, cur_d)
                        except Exception:
                            delta_val = 0.0

                    # ── Reference history ──────────────────────────────────────
                    if _ref_dist_u is not None:
                        try:
                            d_ref = min(cur_d, float(_ref_dist_u[-1]))
                            _ref_hist_buf.append({
                                'speed':        float(np.interp(d_ref, _ref_dist_u, _ref_channels['speed'])),
                                'gx':           float(np.interp(d_ref, _ref_dist_u, _ref_channels['gx'])),
                                'gy':           float(np.interp(d_ref, _ref_dist_u, _ref_channels['gy'])),
                                'lean':         float(np.interp(d_ref, _ref_dist_u, _ref_channels['lean'])),
                                'rpm':          float(np.interp(d_ref, _ref_dist_u, _ref_channels['rpm'])),
                                'exhaust_temp': float(np.interp(d_ref, _ref_dist_u, _ref_channels['exhaust_temp'])),
                                't':            0.0,
                                'delta_time':   0.0,
                                'alt':          float(np.interp(d_ref, _ref_dist_u, _ref_channels.get('alt', [0.0]*len(_ref_dist_u)))),
                            })
                        except Exception:
                            pass

                    history_buf.append({
                        't':              lap_t_display,
                        'speed':          pt.speed,
                        'gx':             pt.gforce_x,
                        'gy':             pt.gforce_y,
                        'lean':           pt.lean_angle,
                        'rpm':            pt.rpm,
                        'exhaust_temp':   pt.exhaust_temp,
                        'delta_time':     delta_val,
                        'alt':            pt.alt,
                        # Lap-scoreboard fields
                        'li_lap_num':     pt.lap,
                        'li_total_laps':  _total_timed,
                        'li_best_so_far': _best_by_lap.get(pt.lap, _best_fallback),
                    })

                # ── Map nearest-point (vectorised numpy, one call per frame) ───
                cur_map_idx = 0
                if pt and _map_arr_np is not None:
                    q   = np.array([pt.lat, pt.lon])
                    d2  = _map_arr_np - q
                    cur_map_idx = int(np.argmin((d2 * d2).sum(axis=1)))

                chunk_frames.append(frm)
                chunk_meta.append((list(history_buf), list(_ref_hist_buf), cur_map_idx))
                frame_idx += 1

            if not chunk_frames:
                break

            args_list = [
                (b'' if overlay_only else frm.tobytes(),
                 (vh, vw, 4) if overlay_only else frm.shape,
                 cur_map_idx,
                 map_lats, map_lons,
                 hist, ref_hist, lap_dur,
                 vw, vh,
                 show_map, show_telemetry,
                 is_bike,
                 layout,
                 max_speed,
                 _sectors,
                 _session_meta,
                 _ref_map_lats,
                 _ref_map_lons,
                 _ref_lap_duration,
                 overlay_only)
                for frm, (hist, ref_hist, cur_map_idx) in zip(chunk_frames, chunk_meta)
            ]

            results = pool.map(render_frame_worker, args_list) if pool else \
                      [render_frame_worker(a) for a in args_list]

            if overlay_only:
                for raw in results:
                    _ov_queue.put(raw)   # writer thread feeds ffmpeg; never blocks main loop
                    processed += 1
            else:
                shape = chunk_frames[0].shape
                for raw in results:
                    writer.write(np.frombuffer(raw, dtype=np.uint8).reshape(shape))
                    processed += 1

            prog(processed / n_frames * 85, f"Frame {processed}/{n_frames}")
    finally:
        if pool:
            pool.terminate()
            pool.join()

    if overlay_only:
        cap.release()
        _ov_queue.put(None)   # signal writer thread to close stdin and exit
        _ov_writer.join()
        _ov_proc.wait()
        if _ov_proc.returncode != 0:
            err = b''.join(_ov_stderr).decode(errors='replace')
            raise VideoMuxError(err[-600:])
        prog(100, "")
        log(f"  ✓ Saved: {out_path}")
    else:
        cap.release()
        writer.release()

        prog(87, "Muxing audio…")
        log("  Muxing audio…")
        mux_dur_s = n_frames / fps if fps else 0.0
        try:
            mux_audio(tmp_raw, video_path, out_path, encoder, crf,
                      audio_start=audio_start,
                      total_s=mux_dur_s,
                      prog_start=87.0, prog_end=100.0,
                      progress_cb=progress_cb)
            os.remove(tmp_raw)
            prog(100, "")
            log(f"  ✓ Saved: {out_path}")
        except Exception as e:
            log(f"  ✗ Mux failed: {e}")
            fallback = out_path.replace('.mp4', '_raw.avi')
            if os.path.exists(tmp_raw):
                os.rename(tmp_raw, fallback)
            log(f"  Raw saved: {fallback}")
