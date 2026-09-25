"""
export_runner.py — Background export logic
==========================================
Pure rendering pipeline; all I/O callbacks are injected so this module
has no GUI imports.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple


def load_any_session(path: str):
    """Load a session from any supported format (see session_loader.load_file)."""
    from session_loader import load_file
    return load_file(path)


def _export_stem(sess, scope_label: str) -> str:
    """Build a human-readable export filename stem: YYYY-MM-DD_HH-MM_Track_Scope.
    Date and time are local, matching what the Info gauge shows."""
    dt = sess.start_time
    if dt is None and getattr(sess, 'date_utc', None):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(sess.date_utc.replace('Z', '+00:00'))
        except Exception:
            dt = None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone()
    date_part = dt.strftime('%Y-%m-%d') if dt else 'unknown-date'
    time_part = dt.strftime('%H-%M')    if dt else ''
    track = re.sub(r'[^\w\s-]', '', sess.track or 'unknown').strip()
    track = re.sub(r'\s+', '_', track) or 'unknown'
    parts = [date_part, time_part, track, scope_label] if time_part else [date_part, track, scope_label]
    return '_'.join(parts)


def lap_label(lap) -> str:
    """Filename label for one lap, the same whichever scope exported it.

    Scopes used to number differently — 'This Lap' by list position
    (counting the outlap), 'All Laps' by position among timed laps — so
    'Lap03' meant a different lap depending on how it was exported, and
    one silently overwrote the other. The lap's own number is unambiguous.
    """
    if lap.is_outlap:
        return 'Outlap'
    if lap.is_inlap:
        return 'Inlap'
    return f'Lap{lap.lap_num:02d}'


def unique_path(path: str) -> str:
    """*path*, or *path* with ' (2)', ' (3)', … before the extension if a file
    by that name exists — an export never overwrites an earlier one."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f'{stem} ({n}){ext}'):
        n += 1
    return f'{stem} ({n}){ext}'


def _clear_legacy_join_cache(log) -> None:
    """Exports used to join multi-clip sessions into ~/.openlap/video_cache
    and never deleted the result — often several GB per session. Clips are
    now read in place, so remove what earlier versions left behind."""
    cache = Path.home() / '.openlap' / 'video_cache'
    if not cache.is_dir():
        return
    freed = 0
    for f in cache.glob('joined_*.mp4'):
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    try:
        cache.rmdir()
    except OSError:
        pass
    if freed:
        log(f"Removed {freed / 1e9:.1f} GB of joined-video cache left by an earlier version.")


def _jobs_for(item: dict, scope: str, sess, clip_start_s: float, clip_end_s: float
              ) -> Tuple[List[tuple], Optional[str]]:
    """The (label, lap-or-None, ref_lap_num) renders one queue item asks for,
    or ([], reason) when there is nothing to render."""
    from data_model import Lap

    if scope == 'selected_lap':
        lap_idx = int(item.get('lap_idx', 0))
        if lap_idx < 0 or lap_idx >= len(sess.laps):
            return [], f"Invalid lap index {lap_idx} (session has {len(sess.laps)} laps)"
        lap = sess.laps[lap_idx]
        return [(lap_label(lap), lap, lap.lap_num)], None

    if scope == 'fastest':
        lap = sess.fastest_lap
        if not lap:
            return [], "No timed lap found"
        return [('Fastest', lap, lap.lap_num)], None

    if scope == 'all_laps':
        laps = sess.timed_laps   # skip outlap / inlap
        if not laps:
            return [], "No timed laps found"
        return [(lap_label(l), l, l.lap_num) for l in laps], None

    if scope == 'lap_range':
        timed = sess.timed_laps
        if not timed:
            return [], "No timed laps found"
        start_num = item.get('lap_range_start')
        end_num   = item.get('lap_range_end')
        start_num = int(timed[0].lap_num if start_num is None else start_num)
        end_num   = int(timed[-1].lap_num if end_num is None else end_num)
        included = [l for l in timed if start_num <= l.lap_num <= end_num]
        if not included:
            return [], f"No timed laps in range {start_num}–{end_num}"
        pts = [p for l in included for p in l.points]
        range_lap = Lap(lap_num=-1, points=pts,
                        duration=sum(l.duration for l in included))
        label = f"Laps{included[0].lap_num:02d}-{included[-1].lap_num:02d}"
        return [(label, range_lap, included[0].lap_num)], None

    if scope == 'full':
        return [('Full', None, None)], None

    if scope == 'clip':
        pts = sess.all_points
        c_start, c_end = clip_start_s, clip_end_s
        if pts:
            sess_end = pts[-1].elapsed
            c_start  = max(0.0, min(clip_start_s, sess_end))
            c_end    = max(c_start + 0.1, min(clip_end_s, sess_end))
        clip_pts = [p for p in pts if c_start <= p.elapsed <= c_end]
        if not clip_pts:
            return [], f"No data points in range {c_start:.1f}–{c_end:.1f}s"
        clip_lap = Lap(lap_num=-1, points=clip_pts, duration=c_end - c_start)
        return [(f"Clip_{int(c_start)}s_{int(c_end)}s", clip_lap, None)], None

    return [], f"Unknown export scope {scope!r}"


def run_export(
    items:            List[dict],
    scope:            str,
    export_path:      str,
    encoder:          str,
    crf:              int,
    workers:          int,
    padding:          float,
    is_bike:          bool,
    show_map:         bool,
    show_tel:         bool,
    layout:           dict,
    clip_start_s:     float,
    clip_end_s:       float,
    ref_mode:         str,
    ref_lap_obj,
    bike_overrides:   dict,
    session_info:     dict,
    log_cb:               Callable[[str], None],
    progress_cb:          Callable[[float, str], None],
    done_cb:              Callable[[bool, str], None],
    overlay_only:         bool = False,
    ref_lap_csv_path:     str  = '',
    ref_lap_num:          int  = 0,
    track_map_selections: dict = None,
    speed_unit_pref:      str  = 'auto',
    is_cancelled:         Optional[Callable[[], bool]] = None,
    secondary_source:     Optional[dict] = None,
    secondary_offsets:    Optional[dict] = None,
    output_height:        Optional[int] = None,
    output_fps:           Optional[float] = None,
    bitrate_kbps:         int = 0,
) -> None:
    """Render one or more sessions.  Designed to be called from a background thread.

    Every item either produces its file(s) or is counted as a failure with
    the reason logged — a skipped item used to count as exported, so a run
    could report "Done" having written nothing.
    """
    from video_renderer import render_lap, RenderJob
    from utils import compute_lean_angle
    from reference_resolver import resolve_reference_lap
    from app_config import load_scan_cache
    from units import resolve_speed_unit
    from session_loader import load_merged

    total_jobs = len(items)
    done_jobs  = 0
    exported   = 0
    failures: List[str] = []
    cancelled  = False

    def log(msg):
        log_cb(msg)

    def fail(name: str, reason: str) -> None:
        log(f"  ✗ {reason}")
        failures.append(f"{name}: {reason}")

    def finish() -> None:
        if cancelled:
            done_cb(False, f"Cancelled — {exported} of {total_jobs} exported")
        elif failures:
            done_cb(False, f"{exported} of {total_jobs} exported, "
                           f"{len(failures)} failed — see log")
        else:
            done_cb(True, f"Done — {exported} of {total_jobs} exported")

    if not export_path:
        failures.append('Export folder is not set')
        log("✗ Export folder is not set — choose one in Settings.")
        return finish()
    try:
        os.makedirs(export_path, exist_ok=True)
    except OSError as e:
        failures.append(f'Export folder unavailable: {e}')
        log(f"✗ Export folder {export_path} is not available: {e}")
        return finish()

    scan_cache = load_scan_cache()
    _clear_legacy_join_cache(log)

    # One worker pool for the whole export: each pool start re-imports
    # numpy/matplotlib in every worker, a couple of seconds per lap on Windows.
    pool = None
    if workers > 1:
        from multiprocessing import Pool
        pool = Pool(workers)

    try:
        for item in items:
            if is_cancelled and is_cancelled():
                cancelled = True
                log("\nExport cancelled.")
                break

            # Accept both the webview field names (csv_path / video_paths /
            # sync_offset) and the legacy Tkinter names (csv / videos / offset).
            csv_path = item.get('csv_path') or item.get('csv')
            videos   = item.get('video_paths') or item.get('videos') or []
            offset   = item.get('sync_offset') if item.get('sync_offset') is not None \
                       else item.get('offset')
            name     = os.path.basename(csv_path or '?')

            # Per-item overrides (set at queue time, from the Overlay tab) —
            # fall back to the call-level defaults for items that predate them.
            item_scope        = item.get('scope') or scope
            item_padding      = item.get('padding') if item.get('padding') is not None else padding
            item_overlay_only = item.get('overlay_only') if item.get('overlay_only') is not None else overlay_only

            done_jobs += 1
            if not csv_path or not os.path.exists(csv_path):
                log(f"\n── {name}")
                fail(name, f"Telemetry file not found: {csv_path}")
                continue

            log(f"\n── {name}")
            try:
                sess = load_merged(csv_path,
                                   (secondary_source or {}).get(csv_path),
                                   (secondary_offsets or {}).get(csv_path, 0.0),
                                   loader=load_any_session)
            except Exception as e:
                fail(name, f"Load failed: {e}")
                continue

            if not videos and not item_overlay_only:
                fail(name, "No video file linked to this session")
                continue
            if offset is None:
                offset = 0.0
                if videos:
                    log("  Warning: no sync offset set for this session — assuming the "
                        "video and telemetry start together. Set it in the Data tab.")

            resolved_speed_unit = resolve_speed_unit(
                speed_unit_pref, getattr(sess, 'source_speed_unit', 'kmh'))

            # Per-session bike override, then derive lean angle where the
            # session is a bike but lean was not logged (e.g. AIM).
            abs_csv  = os.path.abspath(csv_path)
            override = bike_overrides.get(abs_csv)
            if override is not None:
                sess.is_bike = override
            if sess.is_bike or is_bike:
                for pt in sess.all_points:
                    if pt.lean_angle == 0.0:
                        pt.lean_angle = compute_lean_angle(pt.speed, pt.gyro_z, pt.gforce_y)

            info_overrides = session_info.get(abs_csv, {})

            # ── Track map geometry (OSM circuit outline + area polygons) ─────
            track_geom, track_areas = [], []
            if track_map_selections:
                from track_map_cache import load_geometry, load_areas
                track_name = (info_overrides.get('info_track') or
                              getattr(sess, 'track', '') or '').lower().strip()
                osm_id = track_map_selections.get(track_name, '')
                if osm_id:
                    try:
                        track_geom = load_geometry(osm_id) or []
                    except Exception:
                        track_geom = []
                if track_geom:
                    try:
                        clat = sum(g['lat'] for g in track_geom) / len(track_geom)
                        clon = sum(g['lon'] for g in track_geom) / len(track_geom)
                        track_areas = load_areas(clat, clon)
                    except Exception:
                        pass

            # ── Reference lap ─────────────────────────────────────────────────
            static_ref = None
            if ref_mode not in ('session_best_so_far', 'none'):
                static_ref, ref_desc = resolve_reference_lap(
                    ref_mode=ref_mode, sess=sess, session_info=session_info,
                    scan_cache=scan_cache, ref_lap_csv_path=ref_lap_csv_path,
                    ref_lap_num=ref_lap_num, load_session_fn=load_any_session)
                log(f"  Delta vs: {ref_desc}" + ("" if static_ref else " — no reference lap"))

            def ref_for(lap_num):
                if ref_mode != 'session_best_so_far' or lap_num is None:
                    return static_ref
                ref, desc = resolve_reference_lap(
                    ref_mode='session_best_so_far', sess=sess, session_info=session_info,
                    scan_cache=scan_cache, current_lap_num=lap_num,
                    load_session_fn=load_any_session)
                if ref:
                    log(f"  Delta vs: {desc}")
                return ref

            jobs, reason = _jobs_for(item, item_scope, sess, clip_start_s, clip_end_s)
            if not jobs:
                fail(name, reason)
                continue

            ext = '.mov' if item_overlay_only else '.mp4'
            item_ok = True
            base = done_jobs - 1
            for j, (label, lap, ref_num) in enumerate(jobs):
                if is_cancelled and is_cancelled():
                    cancelled = True
                    log(f"  Cancelled (after {j}/{len(jobs)}).")
                    break
                stem = _export_stem(sess, label)
                out  = unique_path(os.path.join(export_path, stem + ext))
                if lap is None:
                    log(f"  Full session → {os.path.basename(out)}")
                else:
                    log(f"  {label}: {lap.duration:.3f}s → {os.path.basename(out)}")

                def prog(pct, msg, _j=j, _n=len(jobs), _base=base):
                    progress_cb((_base + (_j + pct / 100.0) / _n) / max(total_jobs, 1) * 100, msg)

                try:
                    render_lap(
                        videos[0] if videos else '', out, sess, RenderJob(stem, lap),
                        sync_offset=offset, encoder=encoder, crf=crf,
                        n_workers=workers, show_map=show_map, show_telemetry=show_tel,
                        padding=0.0 if lap is None else item_padding,
                        is_bike=is_bike, overlay_layout=layout,
                        progress_cb=prog, log_cb=log,
                        reference_lap=ref_for(ref_num),
                        info_overrides=info_overrides,
                        overlay_only=item_overlay_only,
                        track_map_geometry=track_geom, track_map_areas=track_areas,
                        speed_unit=resolved_speed_unit, is_cancelled=is_cancelled,
                        video_paths=videos, pool=pool,
                        output_height=output_height, output_fps=output_fps,
                        bitrate_kbps=bitrate_kbps,
                    )
                except Exception as e:
                    item_ok = False
                    fail(f"{name} {label}", f"Render error: {e}")
                    continue
                if is_cancelled and is_cancelled():
                    cancelled = True
                    item_ok = False
                    break

            if item_ok and not cancelled:
                exported += 1
            progress_cb(done_jobs / max(total_jobs, 1) * 100, "")
            if cancelled:
                break
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()

    finish()
