"""
video_renderer.py — Video rendering engine
===========================================
Renders one exported video: the gauges for each frame are drawn by a pool of
worker processes (overlay_worker.py), packed into one compact atlas image,
and piped to a single FFmpeg process that crops the gauges back out,
overlays each on the source video, and encodes video and audio in one pass.
An overlay-only export overlays them on a transparent frame instead and
encodes ProRes 4444 with alpha.

FFmpeg reads the source directly, as a concat list when a session spans
several clips, so there is no decoded intermediate and no joined copy of the
footage: the video's own pixels go from the source decoder to the encoder in
YUV, and the audio comes from the same seek as the frames. No GUI state —
all inputs are passed explicitly.
"""

from __future__ import annotations
import json
import logging
import math
import os
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from multiprocessing.pool import MaybeEncodingError
from fractions import Fraction
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

from utils import _run, _popen, ffmpeg_path, ffprobe_path
import numpy as np

from data_model import Session, Lap
from overlay_worker import (render_frame_worker, render_overlay, atlas_layout,
                            pack_context, scale_factor, default_layout)   # noqa: F401
from exceptions import VideoConcatError, VideoMuxError, LapOutOfRangeError
from gauge_channels import HISTORY_WINDOW_S, resample_history
import units as _units

_N_SECTORS = 3  # number of track sectors used for delta-time display

# Size and rate of the synthesized canvas for an overlay-only export that has
# no source video to take them from.
_VIRTUAL_W, _VIRTUAL_H, _VIRTUAL_FPS = 1920, 1080, Fraction(30)


# ── Probing ────────────────────────────────────────────────────────────────────

@dataclass
class VideoInfo:
    width:     int        # displayed size, i.e. after rotation metadata is applied
    height:    int
    fps:       Fraction   # exact, e.g. 30000/1001
    duration:  float      # seconds
    codec:     str = ''
    has_audio: bool = False
    color_space: str = ''
    color_range: str = ''   # 'tv' (limited) or 'pc' (full), as ffprobe reports it

    @property
    def total_frames(self) -> int:
        return int(math.floor(self.duration * float(self.fps) + 1e-6))


def _parse_rate(s: str) -> Optional[Fraction]:
    try:
        r = Fraction(s)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return r if r > 0 else None


def probe_video(path: str) -> VideoInfo:
    """Read size, frame rate, duration and audio presence with ffprobe."""
    r = _run([ffprobe_path(), '-v', 'error', '-print_format', 'json',
              '-show_streams', '-show_format', path], text=True, timeout=60)
    if r.returncode != 0:
        raise VideoMuxError(f'Could not read video {path}: '
                            f'{(r.stderr or "").strip()[-400:] or f"ffprobe exit code {r.returncode}"}')
    data = json.loads(r.stdout or '{}')
    streams = data.get('streams', [])
    v = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if v is None:
        raise VideoMuxError(f'No video stream in {path}')

    # r_frame_rate is the real cadence of a constant-rate recording; phones
    # writing variable-rate files can report a timebase like 90000/1 there,
    # in which case the measured average is the better number.
    fps = _parse_rate(v.get('r_frame_rate'))
    if fps is None or fps > 240:
        fps = _parse_rate(v.get('avg_frame_rate')) or Fraction(30)

    w, h = int(v.get('width') or 0), int(v.get('height') or 0)
    rotation = 0
    for sd in v.get('side_data_list') or []:
        if 'rotation' in sd:
            rotation = int(float(sd['rotation']))
    rotation = rotation or int(float((v.get('tags') or {}).get('rotate', 0) or 0))
    if abs(rotation) % 180 == 90:
        w, h = h, w   # FFmpeg auto-rotates when decoding, so frames arrive this way up

    duration = float(data.get('format', {}).get('duration') or v.get('duration') or 0.0)
    return VideoInfo(width=w, height=h, fps=fps, duration=duration,
                     codec=v.get('codec_name', ''),
                     has_audio=any(s.get('codec_type') == 'audio' for s in streams),
                     color_space=v.get('color_space', '') or '',
                     color_range=v.get('color_range', '') or '')


def _clips_concatenable(infos: List[VideoInfo]) -> bool:
    """FFmpeg's concat demuxer needs every clip encoded the same way —
    true for one camera's chaptered files, not for clips from different
    cameras or settings, which have to be joined by re-encoding instead."""
    first = infos[0]
    return all((i.width, i.height, i.fps, i.codec) == (first.width, first.height, first.fps, first.codec)
               for i in infos[1:])


# ── FFmpeg helpers ─────────────────────────────────────────────────────────────

def _run_ffmpeg_join(cmd_no_output: list, output: str,
                      progress_cb=None, total_s: float = 0.0,
                      stall_timeout_s: float = 120.0):
    """Run one ffmpeg join attempt, optionally reporting progress and
    guarding against a stalled read (e.g. an unresponsive network share)
    hanging the whole export forever.

    Returns an object with .returncode and .stderr, mirroring
    subprocess.CompletedProcess closely enough for the caller's needs.
    """
    import time as _time, subprocess as _sp

    class _Result:
        returncode = 0
        stderr     = b''

    if not (progress_cb and total_s > 0):
        return _run(cmd_no_output + [output])

    proc = _popen(cmd_no_output + ['-progress', 'pipe:1', '-nostats', output],
                  stdout=_sp.PIPE, stderr=_sp.PIPE)

    stderr_buf: list = []
    def _drain_stderr():
        stderr_buf.extend(proc.stderr)
    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()

    last_progress = _time.monotonic()
    def _read_progress():
        nonlocal last_progress
        for raw_line in proc.stdout:
            line = raw_line.decode(errors='replace').strip()
            if line.startswith('out_time_ms='):
                try:
                    elapsed = int(line.split('=', 1)[1]) / 1_000_000.0
                    pct = min(1.0, elapsed / total_s) * 100
                    progress_cb(pct, f"Joining clips…  {elapsed:.1f} / {total_s:.1f}s")
                    last_progress = _time.monotonic()
                except (ValueError, ZeroDivisionError):
                    pass
    t_prog = threading.Thread(target=_read_progress, daemon=True)
    t_prog.start()

    stalled = False
    while proc.poll() is None:
        if _time.monotonic() - last_progress > stall_timeout_s:
            stalled = True
            proc.kill()
            break
        _time.sleep(0.2)

    proc.wait()
    t_prog.join(timeout=2)
    t_err.join(timeout=2)

    r = _Result()
    r.stderr = b''.join(stderr_buf)
    if stalled:
        r.returncode = -1
        r.stderr = (f"No progress for over {stall_timeout_s:.0f}s while joining "
                    f"(source file may be on an unreachable/stalled network share)."
                    ).encode() + b'\n' + r.stderr
    else:
        r.returncode = proc.returncode
    return r


def _probe_duration_s(path: str) -> float:
    try:
        return probe_video(path).duration
    except Exception:
        return 0.0


def concat_videos(input_files: List[str], output: str,
                   progress_cb=None, stall_timeout_s: float = 120.0) -> None:
    """Join video files into one, re-encoding if a stream copy fails.

    Only needed for clips FFmpeg cannot read back to back as a concat list
    (see _clips_concatenable) — e.g. clips from different cameras.
    When progress_cb is given, reports progress against the summed duration
    of the inputs. If ffmpeg's progress output goes quiet for
    stall_timeout_s (e.g. an unreachable network share), the join is killed
    and raises VideoConcatError instead of hanging the export.
    """
    concat_file = _write_concat_list(input_files)
    total_s = sum(_probe_duration_s(p) for p in input_files) if progress_cb else 0.0

    try:
        cmd = [ffmpeg_path(), '-y', '-f', 'concat', '-safe', '0',
               '-i', concat_file, '-c', 'copy']
        r = _run_ffmpeg_join(cmd, output, progress_cb, total_s, stall_timeout_s)
        if r.returncode != 0:
            cmd2 = [ffmpeg_path(), '-y', '-f', 'concat', '-safe', '0',
                    '-i', concat_file,
                    '-c:v', 'libx264', '-crf', '18', '-c:a', 'aac']
            r2 = _run_ffmpeg_join(cmd2, output, progress_cb, total_s, stall_timeout_s)
            if r2.returncode != 0:
                err = r2.stderr.decode(errors='replace')
                logger.error('FFmpeg concat failed:\n%s', err)
                raise VideoConcatError(err[-600:])
    finally:
        os.unlink(concat_file)


def _write_concat_list(paths: List[str]) -> str:
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f:
        for p in paths:
            # concat-demuxer quoting: a single quote inside the path is written '\''
            f.write("file '{}'\n".format(os.path.abspath(p).replace("'", "'\\''")))
        return f.name


def quality_args(encoder: str, crf: int) -> list:
    """Map the UI's CRF setting to the constant-quality flag *encoder* expects.

    Every encoder family spells constant quality differently, and picking the
    wrong flag is not reliably an error: VideoToolbox *accepts* -qp and then
    silently ignores it, so on macOS every export came out at the encoder's
    own default quality no matter where the user put the Quality slider
    (-qp 12 and -qp 32 produce byte-identical output). Other encoders that
    fall through to -qp do honour it, so this only special-cases VideoToolbox.

    VideoToolbox instead takes -q:v on a 1-100 scale where *higher is better* —
    the inverse of CRF. Mapping linearly across H.264's 0-51 quantiser range
    (crf 0 -> 100, crf 51 -> 0) tracks libx264's output closely over the upper
    half of the UI's 12-32 slider: at crf 12 and 18 the resulting file lands
    within ~1.5% of the equivalent libx264 encode. Below roughly crf 22 the
    curve diverges and this mapping errs toward larger files, i.e. more
    quality than asked for rather than less.
    """
    if encoder == 'libx264':
        return ['-crf', str(crf)]
    if encoder == 'h264_nvenc':
        return ['-rc', 'vbr', '-cq', str(crf), '-b:v', '0']
    if encoder.endswith('_videotoolbox'):
        q = round((1.0 - crf / 51.0) * 100)
        return ['-q:v', str(max(1, min(100, q)))]
    return ['-qp', str(crf)]


def output_args(encoder: str, crf: int, bitrate_kbps: int = 0) -> list:
    """Encoder, rate control and container flags for the final MP4.

    A bitrate, when given, replaces the constant-quality setting: some
    uploads and players need a predictable file size more than a fixed
    quality. -profile:v main and a 2 s keyframe interval keep the file
    playable and seekable everywhere; +faststart puts the index up front.
    """
    rate = (['-b:v', f'{bitrate_kbps}k', '-maxrate', f'{bitrate_kbps}k',
             '-bufsize', f'{bitrate_kbps * 2}k'] if bitrate_kbps > 0
            else quality_args(encoder, crf))
    return ['-c:v', encoder] + rate + ['-profile:v', 'main', '-g', '60',
                                       '-c:a', 'aac', '-movflags', '+faststart']


_ENCODER_OK: dict = {}


def encoder_works(encoder: str, width: int, height: int) -> bool:
    """Whether *encoder* can actually encode a frame of this size here.

    Hardware encoders are compiled into most FFmpeg builds and still fail
    without the matching GPU, driver, or a free encode session, and some
    have size limits (4K+ on older NVENC). Rendering happens in one pass
    with no intermediate file to re-encode from, so find out in a fraction
    of a second before rendering rather than after.
    """
    if encoder == 'libx264':
        return True
    key = (encoder, width, height)
    if key not in _ENCODER_OK:
        w, h = width + width % 2, height + height % 2
        try:
            r = _run([ffmpeg_path(), '-hide_banner', '-loglevel', 'error',
                      '-f', 'lavfi', '-i', f'color=black:s={w}x{h}:d=0.2',
                      '-pix_fmt', 'yuv420p', '-c:v', encoder, '-f', 'null', '-'],
                     timeout=30)
            _ENCODER_OK[key] = r.returncode == 0
        except Exception:
            _ENCODER_OK[key] = False
    return _ENCODER_OK[key]


# ── Render job ────────────────────────────────────────────────────────────────

class RenderJob:
    """Describes one output video to render."""
    def __init__(self, label: str, lap: Optional[Lap]):
        self.label     = label
        self.lap       = lap
        self.gpx_start = lap.elapsed_start if lap else None
        self.gpx_end   = lap.elapsed_end   if lap else None
        self.duration  = lap.duration      if lap else 0.0


def frame_window(job: RenderJob, sync_offset: float, padding: float,
                 fps: float, total_frames: int) -> Tuple[int, int]:
    """Source-video frames [f_start, f_end) an export covers.

    This is one side of the Lap 1 start contract (see
    tests/test_lap1_start_contract.py): lap start in the video is
    sync_offset + lap.elapsed_start, the same formula the Data page Mark,
    the editor's lap seek and auto_sync use.
    """
    if job.gpx_start is None:
        return 0, total_frames
    vid_start = max(0.0, sync_offset + job.gpx_start - padding)
    vid_end   = min(total_frames / fps, sync_offset + job.gpx_end + padding)
    f_start   = max(0, int(vid_start * fps))
    f_end     = min(total_frames, int(math.ceil(vid_end * fps)))
    return f_start, f_end


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

    from delta_time import compute_lap_profile, make_delta_fn, _MIN_TRACK_LENGTH_M

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
        'gear':         _ref_arr('gear'),
    }

    # Sector splits
    sectors = []
    if job.lap is not None and cur_lap_t is not None and len(ref_dist_u) > 1:
        N_SECTORS  = _N_SECTORS
        total_dist = float(ref_dist_u[-1])
        if total_dist > _MIN_TRACK_LENGTH_M:
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
    """Assemble the session-info dict passed to the info gauge.

    Date and time are shown in the computer's local time zone, as the
    editor preview shows them — telemetry timestamps are UTC, and burning
    UTC into the video put the clock hours off from the preview.
    """
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
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(session.date_utc.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone()
            meta['info_date'] = local.strftime('%Y-%m-%d')
            meta['info_time'] = local.strftime('%H:%M')
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


def _color_matrix(info: Optional[VideoInfo]) -> str:
    """Matrix to convert the RGB gauges into the video's YUV with — a
    mismatch shifts every gauge colour. HD footage is BT.709 unless tagged."""
    if info and info.color_space in ('bt470bg', 'smpte170m', 'bt601'):
        return 'bt601'
    if info and info.height and info.height < 720 and not info.color_space:
        return 'bt601'
    return 'bt709'


def build_ffmpeg_cmd(sources: List[Tuple[str, float]], n_frames: int,
                     fps: Fraction, frame_w: int, frame_h: int,
                     overlay, encoder: str, crf: int, out_path: str,
                     overlay_only: bool, color_matrix: str = 'bt709',
                     bitrate_kbps: int = 0, color_range: str = 'tv',
                     audio: bool = True) -> List[str]:
    """The single FFmpeg command for one export.

    sources: the clips the export window covers, in order, as (path, seek);
    only the first has a non-zero seek (see clip_sources). Empty for an
    overlay-only export. Several clips are joined with the concat *filter*
    after decoding: the concat demuxer cannot seek, and reading a lap six
    minutes into a recording through it meant decoding those six minutes
    first (76 s against 0.5 s, measured on a 2.7K DJI clip). The overlay
    arrives on stdin as raw
    RGBA frames, one per output frame: *overlay* is (tiles, (atlas_w,
    atlas_h)) from overlay_worker.atlas_layout, and each frame is an atlas
    that is cropped back into tiles and overlaid in layout order — onto the
    source video, or for an overlay-only export onto a transparent frame
    (FFmpeg's overlay composites alpha when the base has it). *overlay* is
    None when no gauge is drawn. Output goes to *out_path*
    as-is; the caller renames it into place once FFmpeg succeeds.
    """
    fps_s = f'{fps.numerator}/{fps.denominator}'
    dur_s = n_frames / float(fps)
    cmd = [ffmpeg_path(), '-y', '-hide_banner', '-loglevel', 'error']

    def pipe_in(w, h):
        return ['-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{w}x{h}',
                '-framerate', fps_s, '-i', 'pipe:0']

    if overlay_only:
        # A transparent frame stands in for the source video.
        cmd += ['-f', 'lavfi', '-i',
                f'color=black@0:s={frame_w}x{frame_h}:r={fps_s},format=yuva444p']
        base = '[0:v]'
        parts = []
        if overlay:
            tiles, (aw, ah) = overlay
            cmd += pipe_in(aw, ah)
            parts, base = _tile_overlays(1, base, tiles, color_matrix, color_range,
                                         'yuva444p', ':format=yuv444')
        parts.append(f'{base}format=yuva444p10le[v]')
        return cmd + ['-filter_complex', ';'.join(parts), '-map', '[v]',
                      '-t', f'{dur_s:.6f}', '-c:v', 'prores_ks', '-profile:v', '4444',
                      '-f', 'mov', out_path]

    # -ss before -i seeks the input; when transcoding FFmpeg then decodes
    # forward to exactly that time, so the first output frame is the frame
    # at the window start and the audio is cut at the same instant.
    for path, seek in sources:
        cmd += (['-ss', f'{seek:.6f}'] if seek > 0 else []) + ['-i', path]
    parts = []
    n = len(sources)
    if n > 1:
        streams = ''.join(f'[{i}:v]' + (f'[{i}:a]' if audio else '') for i in range(n))
        parts.append(f'{streams}concat=n={n}:v=1:a={1 if audio else 0}[base]'
                     + ('[aud]' if audio else ''))
        base = '[base]'
    else:
        base = '[0:v]'
    even = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'
    if overlay:
        tiles, (aw, ah) = overlay
        cmd += pipe_in(aw, ah)
        tile_parts, base = _tile_overlays(n, base, tiles, color_matrix, color_range, 'yuva420p', '')
        parts += tile_parts
    parts.append(f'{base}{even},format=yuv420p[v]')
    audio_map = (['-map', '[aud]'] if n > 1 else ['-map', '0:a?']) if audio else []
    return cmd + ['-filter_complex', ';'.join(parts), '-map', '[v]'] + audio_map + [
                  '-t', f'{dur_s:.6f}'] + output_args(encoder, crf, bitrate_kbps) + [
                  '-f', 'mp4', out_path]


def _tile_overlays(pipe_index: int, base: str, tiles: list, color_matrix: str,
                   color_range: str, tile_fmt: str, overlay_opts: str):
    """Filter-graph steps that convert the piped atlas to the video's YUV,
    crop it into tiles and overlay them one by one onto *base*. Returns
    (steps, label of the result)."""
    rng = 'pc' if color_range == 'pc' else 'tv'
    parts = [f'[{pipe_index}:v]scale=out_color_matrix={color_matrix}:out_range={rng},'
             f'format={tile_fmt},split={len(tiles)}' + ''.join(f'[s{i}]' for i in range(len(tiles)))]
    for i, t in enumerate(tiles):
        parts.append(f"[s{i}]crop={t['w']}:{t['h']}:{t['ax']}:{t['ay']}[t{i}]")
    for i, t in enumerate(tiles):
        parts.append(f"{base}[t{i}]overlay={t['x']}:{t['y']}:eof_action=pass{overlay_opts}[m{i}]")
        base = f'[m{i}]'
    return parts, base


def clip_sources(clips: List[str], durations: List[float], vid_start: float,
                 vid_end: float) -> List[Tuple[str, float]]:
    """The clips an export window [vid_start, vid_end) overlaps, as
    (path, seek into that clip): the first is entered at the window start,
    later ones from their beginning. Clip boundaries are the cumulative
    durations: chaptered recordings are frame-contiguous (see
    auto_sync.MIN_SEGMENT_GAP_S), which the sync offset assumes too."""
    out = []
    t0 = 0.0
    for path, dur in zip(clips, durations):
        t1 = t0 + dur
        if t1 > vid_start and t0 < vid_end:
            out.append((path, max(0.0, vid_start - t0) if not out else 0.0))
        t0 = t1
    if not out and clips:       # rounding at the very end: use the last clip's tail
        out.append((clips[-1], max(0.0, vid_start - (t0 - durations[-1]))))
    return out


# ── Main render function ───────────────────────────────────────────────────────

class _Cancelled(Exception):
    pass


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
    reference_lap:      Optional[Lap] = None,   # lap to compare against for delta time
    info_overrides:     Optional[dict] = None, # manual session-info overrides {info_track, …}
    overlay_only:       bool  = False,         # render transparent overlay .mov (ProRes 4444)
    track_map_geometry: Optional[list] = None, # [{lat,lon}] OSM circuit outline, or None
    track_map_areas:    Optional[list] = None, # [{lats,lons}] OSM area polygons, or None
    speed_unit:         str = 'kmh',           # 'kmh' | 'mph' | 'ms' — already-resolved display unit
    is_cancelled:       Optional[Callable[[], bool]] = None,  # polled once per chunk
    video_paths:        Optional[List[str]] = None,  # several clips of one recording, in order
    pool=None,                                  # multiprocessing.Pool to reuse, or None
    output_height:      Optional[int] = None,   # scale to this height (width follows), or None
    output_fps:         Optional[float] = None,             # frame rate to convert to, or None
    bitrate_kbps:       int = 0,                            # 0 = constant quality (crf)
) -> None:
    """
    Render one video with telemetry overlay. Raises on any failure; the
    output file exists only if the render succeeded.
    """
    layout = overlay_layout or default_layout()

    def log(msg):
        if log_cb: log_cb(msg)
    def prog(pct, msg):
        if progress_cb: progress_cb(pct, msg)

    clips = [p for p in (video_paths or ([video_path] if video_path else [])) if p]
    sync_offset = sync_offset or 0.0

    # ── Source video ──────────────────────────────────────────────────────────
    info = None
    joined_tmp = None
    clip_durations: List[float] = []
    if clips:
        infos = [probe_video(p) for p in clips]
        if len(clips) > 1 and not _clips_concatenable(infos):
            log(f"  Clips differ in format — joining {len(clips)} clips first…")
            joined_tmp = os.path.splitext(out_path)[0] + '.join.mp4'
            concat_videos(clips, joined_tmp, progress_cb=lambda p, m: prog(p * 0.1, m))
            clips, infos = [joined_tmp], [probe_video(joined_tmp)]
        clip_durations = [i.duration for i in infos]
        info = infos[0]
        info.duration = sum(clip_durations)
        info.has_audio = all(i.has_audio for i in infos)
    elif not overlay_only:
        raise LapOutOfRangeError("No video file to render onto.")

    try:
        _render(info, clips, clip_durations, out_path, session, job, sync_offset, encoder, crf,
                n_workers, show_map, show_telemetry, padding, is_bike, layout,
                log, prog, reference_lap, info_overrides, overlay_only,
                track_map_geometry, track_map_areas, speed_unit, is_cancelled,
                pool, output_height, output_fps, bitrate_kbps)
    except _Cancelled:
        log("  Cancelled.")
    finally:
        if joined_tmp and os.path.exists(joined_tmp):
            try:
                os.remove(joined_tmp)
            except OSError:
                pass


def _render(info, clips, clip_durations, out_path, session, job, sync_offset, encoder, crf,
            n_workers, show_map, show_telemetry, padding, is_bike, layout,
            log, prog, reference_lap, info_overrides, overlay_only,
            track_map_geometry, track_map_areas, speed_unit, is_cancelled,
            pool, output_height, output_fps, bitrate_kbps):
    if info is not None:
        vw, vh, fps = info.width, info.height, info.fps
        total = info.total_frames
    else:
        # Overlay-only with no source video: synthesize a canvas spanning the
        # lap (or session) — never a fixed floor, which once produced an hour
        # of blank overlay for a short full-session export.
        vw, vh, fps = _VIRTUAL_W, _VIRTUAL_H, _VIRTUAL_FPS
        session_end = session.all_points[-1].elapsed if session.all_points else 0.0
        virtual_end = sync_offset + (job.gpx_end if job.gpx_start is not None else session_end)
        total = int(math.ceil((virtual_end + padding + 2.0) * float(fps)))
    fps_f = float(fps)

    f_start, f_end = frame_window(job, sync_offset, padding, fps_f, total)
    if job.gpx_start is not None:
        lap_t0, lap_dur = job.gpx_start, job.duration
    else:
        lap_t0 = lap_dur = 0.0
    n_frames  = f_end - f_start
    vid_start = f_start / fps_f
    vid_dur_s = total / fps_f

    log(f"  Encoder: {encoder}  |  Video: {vw}×{vh} @ {fps_f:.2f}fps  |  Duration: {vid_dur_s:.1f}s")
    if job.gpx_start is not None:
        log(f"  Lap duration: {job.duration:.2f}s  (session pos: {job.gpx_start:.1f}s → {job.gpx_end:.1f}s)")
        log(f"  Sync offset:  {sync_offset:.3f}s  →  video window: {vid_start:.1f}s → {f_end / fps_f:.1f}s  ({n_frames} frames)")

    if n_frames <= 0:
        if job.gpx_start is not None:
            need_start = sync_offset + job.gpx_start - padding
            raise LapOutOfRangeError(
                f"Lap is {job.duration:.1f}s long (at session position {job.gpx_start:.1f}s–{job.gpx_end:.1f}s), "
                f"but with sync offset {sync_offset:.1f}s this maps to video time {need_start:.1f}s–"
                f"{sync_offset + job.gpx_end + padding:.1f}s, "
                f"which is outside the video duration of {vid_dur_s:.1f}s. "
                f"Set the sync offset in the Data tab (scrub to where lap 1 starts, then click Mark)."
            )
        raise LapOutOfRangeError(
            f"Video appears empty or unreadable (0 frames). "
            f"Check that the video file is not corrupt: {clips[0] if clips else ''}"
        )

    if not overlay_only and not encoder_works(encoder, vw, vh):
        log(f"  {encoder} cannot encode {vw}×{vh} on this machine — using libx264 (software) instead.")
        encoder = 'libx264'

    # ── Per-render constants, shipped to each worker once ─────────────────────
    speed_pts = job.lap.points if job.lap else session.all_points
    if speed_pts:
        max_speed = _units.dial_ceiling(max(p.speed for p in speed_pts), speed_unit)
    else:
        max_speed = _units.DIAL_MIN_CEILING.get(speed_unit, 50.0) * 6.0

    map_lats, map_lons, map_arr = _build_map_data(job, session, show_map)

    ref_map_lats: list = []
    ref_map_lons: list = []
    ref_lap_duration = 0.0
    if reference_lap and reference_lap.points:
        step = max(1, len(reference_lap.points) // 600)
        ref_map_lats = [p.lat for p in reference_lap.points[::step]]
        ref_map_lons = [p.lon for p in reference_lap.points[::step]]
        ref_lap_duration = reference_lap.duration
        # Smooth the reference GPS track to reduce dot jitter from GPS noise.
        if len(ref_map_lats) > 9:
            _w = np.ones(9) / 9
            ref_map_lats = np.convolve(ref_map_lats, _w, mode='same').tolist()
            ref_map_lons = np.convolve(ref_map_lons, _w, mode='same').tolist()

    dt_state = _setup_delta_time(reference_lap, job, session)
    import channel_discovery
    tiles, atlas = atlas_layout(layout, vw, vh, show_map, show_telemetry)
    overlay = (tiles, atlas) if tiles else None
    ctx = {
        'vw': vw, 'vh': vh, 'tiles': tiles, 'atlas': atlas, 'layout': layout,
        'show_map': show_map, 'show_telemetry': show_telemetry, 'is_bike': is_bike,
        'speed_unit': speed_unit, 'max_speed': max_speed, 'lap_duration': lap_dur,
        'map_lats': map_lats, 'map_lons': map_lons,
        'ref_lats': ref_map_lats, 'ref_lons': ref_map_lons, 'ref_duration': ref_lap_duration,
        'track_map_lats': [g['lat'] for g in track_map_geometry or []],
        'track_map_lons': [g['lon'] for g in track_map_geometry or []],
        'track_map_areas': track_map_areas or [],
        'sectors': dt_state['sectors'],
        'session_meta': _build_session_meta(session, info_overrides),
        'extra_meta': channel_discovery.extra_channel_meta(session),
    }
    ctx_blob = pack_context(ctx)
    ctx_id   = f'{os.getpid()}:{id(ctx)}:{out_path}'

    # ── FFmpeg ────────────────────────────────────────────────────────────────
    sources = (clip_sources(clips, clip_durations, vid_start, f_end / fps_f)
               if clips and not overlay_only else [])
    if len(sources) > 1:
        log(f"  Reading {len(sources)} clips: " + ', '.join(os.path.basename(p) for p, _ in sources))

    ext = os.path.splitext(out_path)[1] or ('.mov' if overlay_only else '.mp4')
    part_path = os.path.splitext(out_path)[0] + '.part' + ext
    cmd = build_ffmpeg_cmd(sources, n_frames, fps, vw, vh, overlay,
                           encoder, crf, part_path, overlay_only,
                           _color_matrix(info), bitrate_kbps,
                           info.color_range if info else 'tv',
                           audio=bool(info and info.has_audio))
    output_size = None
    if output_height and int(output_height) != vh:
        output_size = (round(vw * int(output_height) / vh), int(output_height))
        log(f"  Output: scaled to {output_size[0] // 2 * 2}×{output_size[1] // 2 * 2}")
    if output_fps and abs(float(output_fps) - fps_f) > 0.01:
        log(f"  Output: {float(output_fps):g} fps")
    else:
        output_fps = None
    cmd = _apply_output_conversion(cmd, output_size, output_fps, overlay_only)
    logger.info('render_lap ffmpeg: %s', ' '.join(cmd))

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        raise VideoMuxError(f"Export folder does not exist: {out_dir}")

    proc = _popen(cmd, stdin=subprocess.PIPE if overlay else subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    stderr_buf: list = []
    t_err = threading.Thread(target=lambda: stderr_buf.extend(proc.stderr), daemon=True)
    t_err.start()

    def ffmpeg_error(prefix: str) -> str:
        t_err.join(timeout=5)
        detail = b''.join(stderr_buf).decode(errors='replace').strip()
        lines = [ln for ln in detail.splitlines() if ln.strip()]
        rc = proc.returncode
        if lines:
            return (f'{prefix}' + (f' (exit code {rc})' if rc not in (None, 0) else '')
                    + ':\n' + '\n'.join(lines[-8:]))
        return (f'{prefix} and gave no reason (exit code {rc}). '
                f'Check that your FFmpeg build works: Settings, then Detect Encoders.')

    own_pool = None
    if pool is None and n_workers > 1 and overlay:
        from multiprocessing import Pool
        pool = own_pool = Pool(n_workers)

    written = 0
    early_end = False
    ok = False
    try:
        if overlay:
            written, early_end = _feed_overlay_frames(
                proc, pool, ctx_id, ctx_blob, session, job, dt_state, reference_lap,
                f_start, n_frames, fps_f, sync_offset, lap_t0, lap_dur, map_arr,
                is_cancelled, prog, n_workers)
            try:
                proc.stdin.close()
            except OSError:
                pass
        else:
            # Nothing to overlay: FFmpeg just re-encodes the window.
            while True:
                if is_cancelled and is_cancelled():
                    raise _Cancelled()
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    pass
            written = n_frames

        prog(96, "Finalizing…")
        proc.wait()
        if proc.returncode != 0:
            raise VideoMuxError(ffmpeg_error('FFmpeg could not write the video'))
        if early_end:
            log(f"  Note: video only covered {written}/{n_frames} frames "
                f"of this lap — source video ended early.")
        os.replace(part_path, out_path)
        ok = True
        prog(100, "")
        log(f"  ✓ Saved: {out_path}")
    finally:
        if own_pool is not None:
            own_pool.terminate()
            own_pool.join()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if not ok and os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def _apply_output_conversion(cmd: List[str], output_size, output_fps, overlay_only) -> List[str]:
    """Add a resize and/or frame-rate conversion as the last step of the
    export's filter chain, e.g. to publish 1080p from 4K footage. Gauges are
    drawn and placed at the source size first, so they scale with the video
    instead of moving. Sizes are rounded down to even numbers, which 4:2:0
    encoders require."""
    if not output_size and not output_fps:
        return cmd
    extra = []
    if output_size:
        w, h = (max(2, int(v) // 2 * 2) for v in output_size)
        extra.append(f'scale={w}:{h}:flags=lanczos')
    if output_fps:
        extra.append(f'fps={float(output_fps):g}')
    final = 'format=yuva444p10le[v]' if overlay_only else 'format=yuv420p[v]'
    i = cmd.index('-filter_complex')
    head, tail = cmd[i + 1].rsplit(final, 1)
    cmd[i + 1] = head + ','.join(extra) + ',' + final + tail
    return cmd


def _feed_overlay_frames(proc, pool, ctx_id, ctx_blob, session, job, dt_state,
                         reference_lap, f_start, n_frames, fps, sync_offset,
                         lap_t0, lap_dur, map_arr, is_cancelled, prog, n_workers):
    """Compute each frame's telemetry, have the workers draw it, and write the
    canvases to FFmpeg in order. Workers draw the next chunk while this one
    is written. Returns (frames written, source ended early)."""
    delta_fn         = dt_state['delta_fn']
    cur_lap_t        = dt_state['cur_lap_t']
    cur_lap_d        = dt_state['cur_lap_d']
    cur_lap_profiles = dt_state['cur_lap_profiles']
    ref_dist_u       = dt_state['ref_dist_u']
    ref_channels     = dt_state['ref_channels']

    # Lap scoreboard: best completed timed lap *before* each lap started.
    total_timed = len(session.timed_laps)
    best_by_lap: dict = {}
    running_best = None
    for lap in sorted(session.timed_laps, key=lambda l: l.lap_num):
        best_by_lap[lap.lap_num] = running_best
        if running_best is None or lap.duration < running_best:
            running_best = lap.duration
    best_fallback = running_best

    hist_len  = max(2, int(math.ceil(HISTORY_WINDOW_S * fps)))
    history   = deque(maxlen=hist_len)
    ref_hist  = deque(maxlen=hist_len)

    def frame_state(i: int) -> dict:
        vid_t  = (f_start + i) / fps
        sess_t = vid_t - sync_offset
        pt = session.interpolate_at(sess_t)
        cur_map_idx = 0
        if pt:
            lap_t_display = min(sess_t - lap_t0, lap_dur) if job.gpx_start is not None else pt.lap_elapsed
            delta_val = 0.0
            cur_d = 0.0
            if delta_fn is not None:
                try:
                    if cur_lap_t is not None:
                        cur_d = float(np.interp(pt.lap_elapsed, cur_lap_t, cur_lap_d))
                    else:
                        profile = cur_lap_profiles.get(pt.lap)
                        if profile is not None:
                            cur_d = float(np.interp(pt.lap_elapsed, profile[0], profile[1]))
                    if not math.isfinite(cur_d):
                        cur_d = 0.0
                    delta_val = delta_fn(pt.lap_elapsed, cur_d)
                except Exception:
                    delta_val = 0.0
            if ref_dist_u is not None:
                try:
                    d_ref = min(cur_d, float(ref_dist_u[-1]))
                    rp = {k: float(np.interp(d_ref, ref_dist_u, ref_channels[k]))
                          for k in ('speed', 'gx', 'gy', 'lean', 'rpm', 'exhaust_temp', 'alt', 'gear')}
                    rp.update(t=0.0, delta_time=0.0)
                    ref_hist.append(rp)
                except Exception:
                    pass
            history.append({
                't': lap_t_display, 'speed': pt.speed, 'gx': pt.gforce_x, 'gy': pt.gforce_y,
                'lean': pt.lean_angle, 'rpm': pt.rpm, 'exhaust_temp': pt.exhaust_temp,
                'delta_time': delta_val, 'alt': pt.alt, 'gear': pt.gear,
                'li_lap_num': pt.lap, 'li_total_laps': total_timed,
                'li_best_so_far': (reference_lap.duration if reference_lap
                                   else best_by_lap.get(pt.lap, best_fallback)),
                **pt.extra,
            })
            if map_arr is not None:
                d2 = map_arr - np.array([pt.lat, pt.lon])
                cur_map_idx = int(np.argmin((d2 * d2).sum(axis=1)))
        return {'history': resample_history(history), 'ref_history': resample_history(ref_hist),
                'cur_map_idx': cur_map_idx}

    # Two chunks are in flight at once (one being drawn, one being written),
    # each frame an atlas of several MB, so keep chunks modest.
    chunk = max(4, n_workers * 2)
    written = 0
    # Where the time goes, logged at the end: waiting for the workers to draw,
    # FFmpeg accepting frames (i.e. decode/overlay/encode), or preparing tasks.
    import time as _time
    timing = {'draw_wait': 0.0, 'ffmpeg_write': 0.0, 'prepare': 0.0}
    t_begin = _time.perf_counter()

    def write(results) -> bool:
        nonlocal written
        for raw in results:
            t0 = _time.perf_counter()
            try:
                proc.stdin.write(raw)
            except (BrokenPipeError, OSError):
                # FFmpeg stopped reading: it either failed (reported by the
                # caller from its exit code) or the source video ran out.
                return False
            finally:
                timing['ffmpeg_write'] += _time.perf_counter() - t0
            written += 1
        prog(written / n_frames * 95, f"Frame {written}/{n_frames}")
        return True

    def collect(p):
        """Results of a submitted chunk (p = (async_result, tasks)).

        Sending a multi-MB result back through a multiprocessing pipe can
        fail transiently on Windows under memory pressure (seen as
        MaybeEncodingError: "Error sending result ... Invalid argument").
        A frame is a pure function of its task, so re-drawing the chunk is
        safe; only a failure that persists stops the export.
        """
        t0 = _time.perf_counter()
        if not pool:
            out = [render_frame_worker(t) for t in p]
        else:
            res, tasks = p
            for attempt in range(3):
                try:
                    out = res.get()
                    break
                except (MaybeEncodingError, OSError) as e:
                    if attempt == 2:
                        raise
                    logger.warning('Frame results failed to arrive (%s) — redrawing the chunk', e)
                    res = pool.map_async(render_frame_worker, tasks)
        timing['draw_wait'] += _time.perf_counter() - t0
        return out

    def done(early: bool):
        total = _time.perf_counter() - t_begin
        logger.info('render timing: %d frames in %.1fs (%.1f fps) — waiting for workers %.1fs, '
                    'FFmpeg accepting frames %.1fs, preparing frames %.1fs',
                    written, total, written / total if total else 0.0,
                    timing['draw_wait'], timing['ffmpeg_write'], timing['prepare'])
        return written, early

    pending = None
    for start in range(0, n_frames, chunk):
        if is_cancelled and is_cancelled():
            raise _Cancelled()
        t0 = _time.perf_counter()
        tasks = [(ctx_id, ctx_blob, frame_state(i))
                 for i in range(start, min(start + chunk, n_frames))]
        timing['prepare'] += _time.perf_counter() - t0
        nxt = (pool.map_async(render_frame_worker, tasks), tasks) if pool else tasks
        if pending is not None and not write(collect(pending)):
            return done(True)
        pending = nxt
    if pending is not None and not write(collect(pending)):
        return done(True)
    return done(False)
