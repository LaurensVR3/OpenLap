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

# A winning correlation peak must beat the best rival peak (at least
# PEAK_EXCLUSION_S away from it) by this factor. Confidence alone measures the
# peak against the noise floor, which does not notice that several offsets fit
# about equally well - the normal situation on circuit footage, where every lap
# resembles every other lap one lap time away. See _correlate_full.
#
# Measured over five real karting sessions, comparing each detected offset
# against the one the driver had confirmed by hand:
#
#     session     error      confidence   margin
#     ----------  ---------  ----------   ------
#     69959e4b      0.115s      6.33       1.84
#     6995a5f8      0.006s      6.45       2.85
#     69d10475      0.080s      6.40       1.78
#     6a86eaea      0.092s      6.29       1.40
#     6a86c23f     78.115s      6.14       1.16   <- wrong, and accepted
#
# Confidence spans 6.14-6.45 across both the correct and the wrong results, so
# it cannot separate them at any threshold. The margin puts the wrong one below
# every correct one. 1.25 sits in that gap. The costs are asymmetric: a
# rejected match sends the user to the manual Mark button and says so, while an
# accepted wrong one is stored as 'auto' and silently misplaces every exported
# lap - so err toward rejecting. Re-measure rather than nudging this number.
MIN_PEAK_MARGIN      = 1.25
PEAK_EXCLUSION_S     = 5.0

# Smallest inter-clip gap (seconds) treated as a real recording stop. Video
# creation_time tags have 1-second resolution and chaptered recordings
# (GoPro/DJI split one continuous recording into ~4 GB files) are frame-
# contiguous, yet their tags routinely imply 0.5-2 s "gaps". Inserting those
# shifts every offset found after the chapter boundary — and with it the
# Lap 1 start position — by that amount. Anything below this is timestamp
# noise; a camera the driver actually stopped and restarted is far longer.
MIN_SEGMENT_GAP_S    = 3.0


# ── Telemetry loading ─────────────────────────────────────────────────────────

def _load_session(csv_path: str, source: str = ''):
    """Load a telemetry file for correlation. *source* is accepted for the
    callers' sake but not used: the format is detected from the file itself
    (session_loader.load_file), so a newly supported source cannot be
    missing from a list here the way 'Unipro' once was."""
    from session_loader import load_file
    return load_file(csv_path)


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
    sides, or if every candidate is ambiguous: the same peak-margin test as
    run_auto_sync (MIN_PEAK_MARGIN) applies here, and matters more — speed
    and RPM repeat every lap, so on laps shorter than the search window the
    offset one lap away can fit almost as well as the true one.
    """
    primary_pts   = _load_session(primary_csv,   primary_source).all_points
    secondary_pts = _load_session(secondary_csv, secondary_source).all_points

    best_offset, best_conf, best_channel = 0.0, 0.0, ''
    for name, extractor in _CORRELATION_CANDIDATES:
        primary_sig   = extractor(primary_pts,   fps)
        secondary_sig = extractor(secondary_pts, fps)
        if primary_sig is None or secondary_sig is None:
            continue
        offset, conf, margin = _correlate_full(secondary_sig, primary_sig, fps, search_window_s)
        if margin < MIN_PEAK_MARGIN:
            logger.info('channel sync: %s is ambiguous (confidence %.2f, peak margin %.2f < %.2f)',
                        name, conf, margin, MIN_PEAK_MARGIN)
            continue
        if conf > best_conf:
            best_offset, best_conf, best_channel = offset, conf, name
    return best_offset, best_conf, best_channel


# ── Video probing ─────────────────────────────────────────────────────────────

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _ffmpeg() -> str:
    from utils import ffmpeg_path
    return ffmpeg_path()


def _ffprobe() -> str:
    from utils import ffprobe_path
    return ffprobe_path()


def _probe_video(vpath: str) -> dict:
    result = subprocess.run(
        [_ffprobe(), '-v', 'quiet', '-print_format', 'json',
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
            [_ffprobe(), '-v', 'quiet', '-print_format', 'json',
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
    Never negative (overlapping/out-of-order timestamps clamp to 0), and
    anything shorter than MIN_SEGMENT_GAP_S is treated as a chapter boundary
    (0.0): see the constant's comment for why sub-second tag noise must not
    be inserted into the timeline.
    """
    prev_ct = _probe_creation_time(prev_path)
    cur_ct  = _probe_creation_time(cur_path)
    if prev_ct is None or cur_ct is None:
        return 0.0
    gap = (cur_ct - prev_ct).total_seconds() - prev_duration
    if gap < MIN_SEGMENT_GAP_S:
        return 0.0
    return gap


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


def _correlate_full(
    vid_sig: np.ndarray,
    tel_sig: np.ndarray,
    fps: float,
    search_window_s: float,
) -> Tuple[float, float, float]:
    """Cross-correlate and return (offset, confidence, margin).

    *confidence* is the winning peak measured against the correlation's own
    RMS. It says the peak stands out from the noise floor, but says nothing
    about whether a *rival* peak is nearly as good — and on circuit footage
    rivals are the normal case, because every lap looks roughly like every
    other lap, one lap time apart.

    *margin* is the winning peak over the best rival at least
    PEAK_EXCLUSION_S away from it. Near 1.0 means "several places fit about
    equally well", which is how a confidently wrong offset gets produced: on
    a real session that landed 78s from the hand-set offset, confidence was
    6.14 (comfortably over the acceptance threshold) while the margin was
    1.16, against 1.40 and 1.78 for sessions that resolved correctly.
    """
    v = _z_normalize(vid_sig)
    t = _z_normalize(tel_sig)
    xcorr = sp_signal.correlate(v, t, mode='full')
    lags  = sp_signal.correlation_lags(len(v), len(t))
    lag_s = lags / fps
    mask = np.abs(lag_s) <= search_window_s
    if not mask.any():
        return 0.0, 0.0, 0.0
    win_indices = np.where(mask)[0]
    best_in_win = win_indices[np.argmax(xcorr[mask])]
    sub_idx = _parabolic_peak(xcorr, best_in_win)
    offset = (sub_idx - (len(tel_sig) - 1)) / fps
    rms = float(np.sqrt(np.mean(xcorr**2)))
    peak = float(xcorr[best_in_win])
    confidence = peak / rms if rms > 0 else 0.0

    # Best rival peak, excluding the shoulder of the winner itself.
    rivals = win_indices[np.abs(lag_s[win_indices] - lag_s[best_in_win]) > PEAK_EXCLUSION_S]
    if rivals.size and peak > 0:
        runner_up = float(np.max(xcorr[rivals]))
        margin = peak / runner_up if runner_up > 0 else float('inf')
    else:
        margin = float('inf')   # nothing else competes within the window
    return float(offset), confidence, margin


def _correlate(
    vid_sig: np.ndarray,
    tel_sig: np.ndarray,
    fps: float,
    search_window_s: float,
) -> Tuple[float, float]:
    """(offset, confidence) — see _correlate_full for the ambiguity measure."""
    offset, confidence, _margin = _correlate_full(vid_sig, tel_sig, fps, search_window_s)
    return offset, confidence


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
    best_margin          = 0.0
    frames_per_check     = max(1, int(CHECK_EVERY_S * fps))
    prev_vpath:    Optional[str] = None
    prev_duration        = 0.0

    for vpath in video_paths:
        if cancel_event and cancel_event.is_set():
            break
        try:
            info = _probe_video(vpath)
        except Exception:
            # Skipping the clip would drop its real duration from the
            # timeline while every later clip kept contributing frames, so
            # any offset drawn from those clips would be wrong by an unknown
            # amount — the same failure the inter-clip gap handling exists to
            # prevent. Correlate on the contiguous prefix collected so far
            # instead, and if that is nothing, report no match rather than a
            # confidently wrong offset. Reachable in practice: probing fails
            # on unreachable network shares.
            logger.warning('auto_sync: probe failed for %s — ignoring it and every '
                           'later clip, since its duration is unknown', vpath)
            break

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
            _ffmpeg(), '-i', vpath,
            '-vf', f'fps={fps},scale={RESIZE_W}:{new_h}',
            '-f', 'rawvideo', '-pix_fmt', 'gray',
            '-loglevel', 'error', 'pipe:1',
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    creationflags=_NO_WINDOW)
        except Exception:
            # Same reasoning as a failed probe: this clip contributes no
            # frames, so keeping later ones would shift the timeline.
            logger.warning('auto_sync: ffmpeg launch failed for %s — ignoring it and '
                           'every later clip', vpath)
            break

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
                offset, conf, margin = _correlate_full(vid_s, tel_sig, fps, search_window_s)
                vid_t_now = cumulative + frame_idx / fps
                if progress_cb:
                    try:
                        progress_cb(vid_t_now, offset, conf)
                    except Exception:
                        pass
                # Deliberately *not* gated on the margin. Letting an
                # ambiguous match decode further sounds better, but measured
                # on a real session it simply found a different peak that
                # passed both gates, replacing one unverified answer with
                # another. Stopping at the same point as before and rejecting
                # at the end keeps this change strictly conservative: an
                # ambiguous session can only turn into "set it manually",
                # never into a different automatic answer.
                if conf >= confidence_threshold:
                    proc.kill()
                    proc.wait()
                    best_offset, best_conf, best_margin = offset, conf, margin
                    stopped_early = True
                    cumulative += frame_idx / fps
                    break
                best_offset, best_conf, best_margin = offset, conf, margin

        if not stopped_early:
            proc.wait()
            cumulative += duration
            prev_vpath, prev_duration = vpath, duration
        else:
            break

    # Final correlation on everything if we never hit threshold
    if all_sig and best_conf < confidence_threshold:
        vid_s = np.array(all_sig)
        best_offset, best_conf, best_margin = _correlate_full(
            vid_s, tel_sig, fps, search_window_s)

    if best_conf < min_confidence:
        return None, best_conf
    if best_margin < MIN_PEAK_MARGIN:
        # Several offsets fit about equally well. Reporting no match sends the
        # user to the manual Mark button, which is far cheaper than an offset
        # that looks confident, gets stored as 'auto', and silently puts every
        # exported lap in the wrong place.
        logger.info('auto_sync: rejecting ambiguous match for %s '
                    '(confidence %.2f, peak margin %.2f < %.2f)',
                    csv_path, best_conf, best_margin, MIN_PEAK_MARGIN)
        return None, best_conf
    return best_offset, best_conf
