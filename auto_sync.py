"""
auto_sync.py — Automatic video-telemetry sync offset detection.

Cross-correlates video motion signal against telemetry G-force.
Streams ffmpeg frames with early exit once confidence threshold is reached.
Typical wall time: 20-60s per session.

sync_offset convention (matches OpenLap's manual Mark offset):
    session_time = video_time - sync_offset
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from scipy import signal as sp_signal

logger = logging.getLogger(__name__)

FPS                  = 5.0
CONFIDENCE_THRESHOLD = 6.0
MIN_CONFIDENCE       = 3.0
SEARCH_WINDOW_S      = 120.0
RESIZE_W             = 320
CHECK_EVERY_S        = 20.0


# ── Telemetry loading ─────────────────────────────────────────────────────────

def _load_session(csv_path: str, source: str):
    if source == 'RaceBox':
        from racebox_data import load_csv
        return load_csv(csv_path)
    if source in ('AIM Mychron', 'AIM'):
        from aim_data import load_csv
        return load_csv(csv_path)
    if source == 'GPX':
        from gpx_data import load_gpx
        return load_gpx(csv_path)
    if source == 'MoTeC':
        from motec_data import load_ld
        return load_ld(csv_path)
    raise ValueError(f'Unknown telemetry source: {source!r}')


def _load_telemetry(csv_path: str, source: str, fps: float) -> np.ndarray:
    """Return G-magnitude signal resampled to fps. Falls back to |d(speed)/dt|/g."""
    session = _load_session(csv_path, source)
    pts = session.all_points
    t    = np.array([p.elapsed  for p in pts], dtype=np.float64)
    gx   = np.array([p.gforce_x for p in pts], dtype=np.float64)
    gy   = np.array([p.gforce_y for p in pts], dtype=np.float64)
    gmag = np.sqrt(gx**2 + gy**2)
    if gmag.max() < 0.05:
        speed_ms = np.array([p.speed for p in pts]) / 3.6
        gmag = np.abs(np.gradient(speed_ms, t)) / 9.81
    out_t = np.arange(t[0], t[-1], 1.0 / fps)
    return np.interp(out_t, t, gmag)


# ── Video probing ─────────────────────────────────────────────────────────────

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _probe_video(vpath: str) -> dict:
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_streams', '-select_streams', 'v:0', vpath],
        capture_output=True, text=True, check=True,
        creationflags=_NO_WINDOW,
    )
    stream = json.loads(result.stdout)['streams'][0]
    num, den = map(int, stream['r_frame_rate'].split('/'))
    fps = num / den
    duration = float(stream.get('duration') or 0)
    if duration == 0:
        duration = int(stream.get('nb_frames', 0)) / fps
    return {
        'fps': fps,
        'width':  int(stream['width']),
        'height': int(stream['height']),
        'duration': duration,
    }


# ── Cross-correlation ─────────────────────────────────────────────────────────

def _z_normalize(x: np.ndarray) -> np.ndarray:
    std = x.std()
    return (x - x.mean()) / std if std > 1e-10 else x - x.mean()


def _parabolic_peak(xcorr: np.ndarray, idx: int) -> float:
    if idx <= 0 or idx >= len(xcorr) - 1:
        return float(idx)
    y0, y1, y2 = xcorr[idx - 1], xcorr[idx], xcorr[idx + 1]
    denom = y0 - 2 * y1 + y2
    if abs(denom) < 1e-12:
        return float(idx)
    return idx + 0.5 * (y0 - y2) / denom


def _correlate(
    vid_sig: np.ndarray,
    tel_sig: np.ndarray,
    fps: float,
    search_window_s: float,
) -> Tuple[float, float]:
    v = _z_normalize(vid_sig)
    t = _z_normalize(tel_sig)
    xcorr = sp_signal.correlate(v, t, mode='full')
    lags  = sp_signal.correlation_lags(len(v), len(t))
    lag_s = lags / fps
    mask = np.abs(lag_s) <= search_window_s
    if not mask.any():
        return 0.0, 0.0
    win_indices = np.where(mask)[0]
    best_in_win = win_indices[np.argmax(xcorr[mask])]
    sub_idx = _parabolic_peak(xcorr, best_in_win)
    offset = (sub_idx - (len(tel_sig) - 1)) / fps
    rms = float(np.sqrt(np.mean(xcorr**2)))
    confidence = float(xcorr[best_in_win]) / rms if rms > 0 else 0.0
    return float(offset), confidence


# ── Main entry point ──────────────────────────────────────────────────────────

def run_auto_sync(
    csv_path:             str,
    video_paths:          List[str],
    source:               str,
    fps:                  float = FPS,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
    min_confidence:       float = MIN_CONFIDENCE,
    search_window_s:      float = SEARCH_WINDOW_S,
    cancel_event:         Optional[threading.Event] = None,
    progress_cb:          Optional[Callable] = None,
) -> Tuple[Optional[float], float]:
    """
    Detect sync offset for one session.

    Streams ffmpeg frames and checks cross-correlation confidence every
    CHECK_EVERY_S seconds of video. Stops as soon as confidence_threshold
    is reached.

    progress_cb(vid_t, offset, confidence) — called at each confidence check.
    cancel_event — threading.Event; set to abort early.

    Returns:
        (offset, confidence) — offset is None if confidence < min_confidence.
    """
    try:
        tel_sig = _load_telemetry(csv_path, source, fps)
    except Exception:
        logger.exception('auto_sync: telemetry load failed for %s', csv_path)
        return None, 0.0

    all_sig:       list = []
    cumulative           = 0.0
    best_offset          = 0.0
    best_conf            = 0.0
    frames_per_check     = max(1, int(CHECK_EVERY_S * fps))

    for vpath in video_paths:
        if cancel_event and cancel_event.is_set():
            break
        try:
            info = _probe_video(vpath)
        except Exception:
            logger.warning('auto_sync: probe failed for %s', vpath)
            continue

        orig_h   = info['height']
        new_h    = max(2, int(orig_h * RESIZE_W / info['width']))
        new_h   += new_h % 2
        duration = info['duration']

        cmd = [
            'ffmpeg', '-i', vpath,
            '-vf', f'fps={fps},scale={RESIZE_W}:{new_h}',
            '-f', 'rawvideo', '-pix_fmt', 'gray',
            '-loglevel', 'error', 'pipe:1',
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    creationflags=_NO_WINDOW)
        except Exception:
            logger.warning('auto_sync: ffmpeg launch failed for %s', vpath)
            continue

        frame_size    = RESIZE_W * new_h
        prev          = None
        frame_idx     = 0
        stopped_early = False

        while True:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                break
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            frame = (
                np.frombuffer(raw, dtype=np.uint8)
                .reshape(new_h, RESIZE_W)
                .astype(np.float32)
            )
            motion = float(np.mean(np.abs(frame - prev))) if prev is not None else 0.0
            all_sig.append(motion)
            prev       = frame
            frame_idx += 1

            if frame_idx % frames_per_check == 0 and len(all_sig) > 10:
                vid_s  = np.array(all_sig)
                offset, conf = _correlate(vid_s, tel_sig, fps, search_window_s)
                vid_t_now = cumulative + frame_idx / fps
                if progress_cb:
                    try:
                        progress_cb(vid_t_now, offset, conf)
                    except Exception:
                        pass
                if conf >= confidence_threshold:
                    proc.kill()
                    proc.wait()
                    best_offset, best_conf = offset, conf
                    stopped_early = True
                    cumulative += frame_idx / fps
                    break
                best_offset, best_conf = offset, conf

        if not stopped_early:
            proc.wait()
            cumulative += duration
        else:
            break

    # Final correlation on everything if we never hit threshold
    if all_sig and best_conf < confidence_threshold:
        vid_s = np.array(all_sig)
        best_offset, best_conf = _correlate(vid_s, tel_sig, fps, search_window_s)

    if best_conf < min_confidence:
        return None, best_conf
    return best_offset, best_conf
