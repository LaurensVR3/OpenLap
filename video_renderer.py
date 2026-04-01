"""
rb_render.py — Video rendering engine
=======================================
Handles video joining (ffmpeg), frame rendering (multiprocessing),
and final mux. No GUI state — all inputs passed explicitly.
"""

from __future__ import annotations
import math
import os
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from typing import Callable, List, Optional, Tuple


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


import cv2
import numpy as np

from racebox_data import Session, Lap
from overlay_worker import render_frame_worker, scale_factor, default_layout


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
                raise RuntimeError(r2.stderr.decode(errors='replace')[-600:])
    finally:
        os.unlink(concat_file)


def mux_audio(raw_video: str, audio_source: str,
               output: str, encoder: str, crf: int = 18,
               audio_start: float = 0.0) -> None:
    """Re-encode raw opencv video with hardware encoder + trim audio."""
    q_map = {'h264_nvenc': '24', 'h264_amf': '24',
             'h264_qsv':   '24', 'libx264':  str(crf)}
    q     = q_map.get(encoder, str(crf))
    q_arg = ['-crf', q] if encoder == 'libx264' else ['-qp', q]

    cmd = ['ffmpeg', '-y',
           '-i', raw_video,
           '-ss', f'{audio_start:.6f}', '-i', audio_source,
           '-map', '0:v', '-map', '1:a?',
           '-c:v', encoder] + q_arg + ['-c:a', 'aac', '-shortest', output]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors='replace')[-600:])


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

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ── Frame range ────────────────────────────────────────────────────────────
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

    log(f"  Encoder: {encoder}  |  Video: {vw}×{vh} @ {fps:.2f}fps")
    if job.gpx_start is not None:
        log(f"  Lap window:   {job.gpx_start:.2f}s → {job.gpx_end:.2f}s")
        log(f"  With padding: {vid_start:.2f}s → {vid_end:.2f}s")
    log(f"  Frames: {f_start}–{f_end} ({n_frames})")

    cap.set(cv2.CAP_PROP_POS_FRAMES, f_start)

    tmp_raw = out_path.replace('.mp4', '_raw.avi')
    writer  = cv2.VideoWriter(
        tmp_raw, cv2.VideoWriter_fourcc(*'MJPG'), fps, (vw, vh))

    # ── Max speed for dynamic gauge scaling ───────────────────────────────────
    speed_pts = job.lap.points if job.lap else session.all_points
    if speed_pts:
        raw_max = max(p.speed for p in speed_pts)
        import math as _math
        padded  = raw_max * 1.10
        max_speed = max(50.0, _math.ceil(padded / 50) * 50)
    else:
        max_speed = 300.0

    # ── Map track points ────────────────────────────────────────────────────────
    if job.lap and show_map:
        lap_pts  = job.lap.points
        step     = max(1, len(lap_pts) // 600)
        ds_pts   = lap_pts[::step]
    else:
        step     = max(1, len(session.all_points) // 600)
        ds_pts   = session.all_points[::step]
    map_lats = [p.lat for p in ds_pts]
    map_lons = [p.lon for p in ds_pts]
    map_arr  = list(zip(map_lats, map_lons)) if map_lats else []

    HISTORY_SECS = 10.0
    HISTORY_MAX  = int(HISTORY_SECS * fps)
    history_buf: list = []

    chunk     = max(4, n_workers * 2)
    frame_idx = f_start
    processed = 0

    while frame_idx < f_end:
        chunk_frames, chunk_meta = [], []

        for _ in range(chunk):
            if frame_idx >= f_end:
                break
            ret, frm = cap.read()
            if not ret:
                break

            vid_t     = frame_idx / fps
            sess_t    = vid_t - sync_offset
            raw_lap_t = sess_t - lap_t0
            lap_t_display = (min(raw_lap_t, lap_dur)
                             if job.gpx_start is not None else raw_lap_t)

            pt = session.interpolate_at(sess_t)
            if pt:
                history_buf.append({
                    't':     lap_t_display,
                    'speed': pt.speed,
                    'gx':    pt.gforce_x,
                    'gy':    pt.gforce_y,
                    'lean':  pt.lean_angle,
                })
                if len(history_buf) > HISTORY_MAX:
                    history_buf.pop(0)

            cur_map_idx = 0
            if pt and map_arr:
                best_d = float('inf')
                for mi, (mlat, mlon) in enumerate(map_arr):
                    d = (mlat - pt.lat)**2 + (mlon - pt.lon)**2
                    if d < best_d:
                        best_d, cur_map_idx = d, mi

            chunk_frames.append(frm)
            chunk_meta.append((list(history_buf), cur_map_idx))
            frame_idx += 1

        if not chunk_frames:
            break

        args_list = [
            (frm.tobytes(), frm.shape, cur_map_idx,
             map_lats, map_lons,
             hist, lap_dur,
             vw, vh,
             show_map, show_telemetry,
             is_bike,
             layout,
             max_speed)
            for frm, (hist, cur_map_idx) in zip(chunk_frames, chunk_meta)
        ]

        if n_workers > 1:
            with Pool(n_workers) as pool:
                results = pool.map(render_frame_worker, args_list)
        else:
            results = [render_frame_worker(a) for a in args_list]

        shape = chunk_frames[0].shape
        for raw in results:
            writer.write(np.frombuffer(raw, dtype=np.uint8).reshape(shape))
            processed += 1

        prog(processed / n_frames * 85, f"Frame {processed}/{n_frames}")

    cap.release()
    writer.release()

    prog(87, "Muxing audio…")
    log("  Muxing audio…")
    try:
        mux_audio(tmp_raw, video_path, out_path, encoder, crf,
                  audio_start=audio_start)
        os.remove(tmp_raw)
        prog(100, "")
        log(f"  ✓ Saved: {out_path}")
    except Exception as e:
        log(f"  ✗ Mux failed: {e}")
        fallback = out_path.replace('.mp4', '_raw.avi')
        if os.path.exists(tmp_raw):
            os.rename(tmp_raw, fallback)
        log(f"  Raw saved: {fallback}")
