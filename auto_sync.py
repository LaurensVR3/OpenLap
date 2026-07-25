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
from datetime import datetime, timezone
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
    from session_scanner import resolve_xrk_csv
    csv_path = resolve_xrk_csv(csv_path)
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
    if source == 'VBOX':
        from vbox_data import load_vbo
        return load_vbo(csv_path)
    if source == 'Unipro':
        from unipro_data import is_unipro_tsv, load_tsv, load_uni
        if is_unipro_tsv(csv_path):
            return load_tsv(csv_path)
        return load_uni(csv_path)
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


def _resample(pts: list, vals: np.ndarray, fps: float) -> np.ndarray:
    t = np.array([p.elapsed for p in pts], dtype=np.float64)
    out_t = np.arange(t[0], t[-1], 1.0 / fps)
    return np.interp(out_t, t, vals)


def _rpm_signal(pts: list, fps: float) -> Optional[np.ndarray]:
    """RPM channel — the sharpest signal when both files log the same
    ECU/CAN feed (gearshifts and rev-matching are very distinctive), but
    absent on GPS-only loggers (which leave it at a uniform 0.0)."""
    if not pts:
        return None
    rpm = np.array([p.rpm for p in pts], dtype=np.float64)
    if rpm.max() < 1.0:
        return None
    return _resample(pts, rpm, fps)


def _gforce_signal(pts: list, fps: float) -> Optional[np.ndarray]:
    """G-force magnitude from the accelerometer — the same signal
    run_auto_sync() correlates against video motion; braking/cornering
    events give it a sharp, distinctive shape. Unlike _load_telemetry()'s
    single-candidate video-sync path, this deliberately does *not* fall back
    to a speed-derivative approximation when accelerometer data is absent —
    Speed is already its own separate, more honestly-labeled candidate below."""
    if not pts:
        return None
    gx = np.array([p.gforce_x for p in pts], dtype=np.float64)
    gy = np.array([p.gforce_y for p in pts], dtype=np.float64)
    gmag = np.sqrt(gx**2 + gy**2)
    if gmag.max() < 0.05:
        return None
    return _resample(pts, gmag, fps)


def _speed_signal(pts: list, fps: float) -> Optional[np.ndarray]:
    """Speed channel — near-universal (GPS or wheel speed), a good fallback
    when one file has neither engine nor accelerometer data."""
    if not pts:
        return None
    speed = np.array([p.speed for p in pts], dtype=np.float64)
    if speed.max() < 1.0:
        return None
    return _resample(pts, speed, fps)


def _altitude_signal(pts: list, fps: float) -> Optional[np.ndarray]:
    """Altitude — last-resort candidate; only useful on hilly tracks, so a
    minimum-range check keeps flat circuits from matching on GPS noise."""
    if not pts:
        return None
    alt = np.array([p.alt for p in pts], dtype=np.float64)
    if (alt.max() - alt.min()) < 2.0:
        return None
    return _resample(pts, alt, fps)


# Tried in order of how distinctive a match each usually gives (RPM/G-force
# have sharp, well-defined events; speed is smoother; altitude is the
# weakest signal) — but every usable candidate is tried regardless, since
# checking one costs nothing (already-loaded points, no I/O), and whichever
# gives the best confidence wins.
_CORRELATION_CANDIDATES: List[Tuple[str, Callable[[list, float], Optional[np.ndarray]]]] = [
    ('RPM', _rpm_signal),
    ('G-Force', _gforce_signal),
    ('Speed', _speed_signal),
    ('Altitude', _altitude_signal),
]


def correlate_channels(
    primary_csv:      str,
    secondary_csv:    str,
    primary_source:   str,
    secondary_source: str,
    search_window_s:  float = 60.0,
    fps:              float = FPS,
) -> Tuple[float, float, str]:
    """
    Cross-correlate two telemetry files logged during the same run (e.g. a
    MoTeC ECU log and an AIM GPS log) to find the offset between their two
    independent clocks. Tries every channel both files actually have usable
    data for — RPM, G-force, Speed, Altitude — and keeps whichever produces
    the highest-confidence match, the same "try candidates, keep the best"
    approach run_auto_sync() uses for video-vs-telemetry sync.

    Returns (offset, confidence, channel_name) with offset in
    session_merge.py's convention: secondary_elapsed = primary_elapsed +
    offset. This is the reverse argument order from _correlate()'s own
    (vid_sig, tel_sig) convention documented at the top of this file —
    verified empirically: _correlate(secondary_sig, primary_sig, ...) is
    what yields offset in the secondary-relative-to-primary sense used here.

    Returns (0.0, 0.0, '') if no candidate channel has usable data on both
    sides.
    """
    primary_pts   = _load_session(primary_csv,   primary_source).all_points
    secondary_pts = _load_session(secondary_csv, secondary_source).all_points

    best_offset, best_conf, best_channel = 0.0, 0.0, ''
    for name, extractor in _CORRELATION_CANDIDATES:
        primary_sig   = extractor(primary_pts,   fps)
        secondary_sig = extractor(secondary_pts, fps)
        if primary_sig is None or secondary_sig is None:
            continue
        offset, conf = _correlate(secondary_sig, primary_sig, fps, search_window_s)
        if conf > best_conf:
            best_offset, best_conf, best_channel = offset, conf, name
    return best_offset, best_conf, best_channel


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


def _probe_creation_time(vpath: str) -> Optional[datetime]:
    """Best-effort read of a video's embedded creation_time (UTC), or None
    if absent/unreadable. Used only to detect real inter-segment gaps —
    never fatal to the sync pipeline if it fails."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_entries', 'format_tags=creation_time', vpath],
            capture_output=True, text=True, check=True,
            creationflags=_NO_WINDOW,
        )
        data = json.loads(result.stdout)
        ct = data.get('format', {}).get('tags', {}).get('creation_time')
        if not ct:
            return None
        dt = datetime.fromisoformat(ct.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        logger.debug('auto_sync: could not read creation_time for %s', vpath, exc_info=True)
        return None


def _video_gap_seconds(prev_path: str, prev_duration: float, cur_path: str) -> float:
    """Real elapsed-time gap (seconds) between the end of the previous video
    segment and the start of the current one, using embedded creation_time
    metadata (the same source session_scanner.group_videos uses to decide
    whether segments belong to the same recording session).

    Returns 0.0 (i.e. "assume back-to-back") if either file's creation_time
    can't be read — failing safe to the old behaviour rather than guessing.
    Never negative (overlapping/out-of-order timestamps clamp to 0).
    """
    prev_ct = _probe_creation_time(prev_path)
    cur_ct  = _probe_creation_time(cur_path)
    if prev_ct is None or cur_ct is None:
        return 0.0
    gap = (cur_ct - prev_ct).total_seconds() - prev_duration
    return max(0.0, gap)


def _append_gap_frames(all_sig: list, gap_s: float, fps: float) -> int:
    """Append neutral filler frames to *all_sig* (in place), representing
    *gap_s* seconds of real elapsed time between video segments that produced
    no frames (e.g. the camera was stopped/swapped cards mid-session).

    Without this, concatenating segment frames back-to-back silently drops
    the gap from the timeline, so any correlation dominated by a later
    segment is off by roughly the unaccounted gap. The filler uses the mean
    of the signal collected so far so it reads as a flat, unremarkable
    stretch after z-normalization rather than a false motion spike or dip.

    Returns the number of frames appended (0 if gap_s rounds to 0 frames).
    """
    n_gap_frames = max(0, int(round(gap_s * fps)))
    if n_gap_frames == 0:
        return 0
    fill_val = float(np.mean(all_sig)) if all_sig else 0.0
    all_sig.extend([fill_val] * n_gap_frames)
    return n_gap_frames


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
    prev_vpath:    Optional[str] = None
    prev_duration        = 0.0

    for vpath in video_paths:
        if cancel_event and cancel_event.is_set():
            break
        try:
            info = _probe_video(vpath)
        except Exception:
            logger.warning('auto_sync: probe failed for %s', vpath)
            continue

        # Account for any real elapsed-time gap between this segment and the
        # previous one (e.g. camera stopped/restarted) — otherwise the
        # concatenated signal silently compresses that dead time out of the
        # timeline and any correlation dominated by this segment is wrong by
        # roughly the unaccounted gap.
        if prev_vpath is not None:
            gap_s = _video_gap_seconds(prev_vpath, prev_duration, vpath)
            if gap_s > 0:
                _append_gap_frames(all_sig, gap_s, fps)
                cumulative += gap_s
                logger.debug('auto_sync: inserted %.1fs gap before %s', gap_s, vpath)

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
            prev_vpath, prev_duration = vpath, duration
        else:
            break

    # Final correlation on everything if we never hit threshold
    if all_sig and best_conf < confidence_threshold:
        vid_s = np.array(all_sig)
        best_offset, best_conf = _correlate(vid_s, tel_sig, fps, search_window_s)

    if best_conf < min_confidence:
        return None, best_conf
    return best_offset, best_conf
