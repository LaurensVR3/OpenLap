"""
export_runner.py — Background export logic
==========================================
Pure rendering pipeline; all I/O callbacks are injected so this module
has no GUI imports.
"""
from __future__ import annotations
import os
import re
from typing import Callable, List, Optional


def load_any_session(path: str):
    """Load a session from any supported format (RaceBox, AIM, GPX, MoTeC)."""
    import gpx_data, aim_data, racebox_data, motec_data
    if motec_data.is_motec_ld(path):
        return motec_data.load_ld(path)
    if gpx_data.is_gpx(path):
        return gpx_data.load_gpx(path)
    if aim_data.is_aim_csv(path):
        return aim_data.load_csv(path)
    return racebox_data.load_csv(path)


def _export_stem(sess, scope_label: str) -> str:
    """Build a human-readable export filename stem: YYYY-MM-DD_HH-MM_Track_Scope."""
    dt = sess.start_time
    if dt is None and getattr(sess, 'date_utc', None):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(sess.date_utc.replace('Z', '+00:00'))
        except Exception:
            dt = None
    date_part = dt.strftime('%Y-%m-%d') if dt else 'unknown-date'
    time_part = dt.strftime('%H-%M')    if dt else ''
    track = re.sub(r'[^\w\s-]', '', sess.track or 'unknown').strip()
    track = re.sub(r'\s+', '_', track) or 'unknown'
    parts = [date_part, time_part, track, scope_label] if time_part else [date_part, track, scope_label]
    return '_'.join(parts)


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
) -> None:
    """Render one or more sessions.  Designed to be called from a background thread."""
    from video_renderer import render_lap, RenderJob, concat_videos
    from data_model import Lap
    from utils import compute_lean_angle
    from reference_resolver import resolve_reference_lap
    from app_config import load_scan_cache

    scan_cache = load_scan_cache()

    total_jobs = len(items)
    done_jobs  = 0

    def log(msg):
        log_cb(msg)

    def sess_prog(done, join_share, render_pct, msg):
        """Map per-session render progress into the overall progress bar."""
        sess_w = 100.0 / max(total_jobs, 1)
        base   = done * sess_w
        within = join_share * sess_w + (render_pct / 100) * (1 - join_share) * sess_w
        progress_cb(base + within, msg)

    errors = []

    for item in items:
        # Accept both the webview field names (csv_path / video_paths / sync_offset)
        # and the legacy Tkinter names (csv / videos / offset).
        csv_path = item.get('csv_path') or item.get('csv')
        videos   = item.get('video_paths') or item.get('videos') or []
        offset   = item.get('sync_offset') if item.get('sync_offset') is not None \
                   else (item.get('offset') or 0.0)

        if not csv_path or not os.path.exists(csv_path):
            log(f"Skipping: CSV not found: {csv_path}")
            done_jobs += 1
            continue

        log(f"\n── {os.path.basename(csv_path)}")

        try:
            sess = load_any_session(csv_path)
        except Exception as e:
            log(f"  ✗ Load failed: {e}")
            errors.append(str(e))
            done_jobs += 1
            continue

        # Apply per-session bike override, then compute lean angles when
        # the session is a bike but lean was not directly logged (e.g. AIM).
        abs_csv  = os.path.abspath(csv_path)
        override = bike_overrides.get(abs_csv)
        if override is not None:
            sess.is_bike = override
        if sess.is_bike or is_bike:
            for pt in sess.all_points:
                if pt.lean_angle == 0.0:
                    pt.lean_angle = compute_lean_angle(
                        pt.speed, pt.gyro_z, pt.gforce_y)

        if not videos and (scope != 'full' or overlay_only):
            log("  ✗ No video file — skipping")
            done_jobs += 1
            continue

        _ext = '.mov' if overlay_only else '.mp4'

        # ── Join phase ────────────────────────────────────────────────────────
        video_path = videos[0] if videos else None
        tmp_joined = None
        join_share = 0.0
        if len(videos) > 1:
            from pathlib import Path as _Path
            _vcache = _Path.home() / '.openlap' / 'video_cache'
            _vcache.mkdir(parents=True, exist_ok=True)
            join_share = 0.10
            tmp_joined = str(_vcache / f"joined_{os.path.basename(csv_path)}.mp4")
            newest_src = max(os.path.getmtime(v) for v in videos)
            if (os.path.exists(tmp_joined) and
                    os.path.getmtime(tmp_joined) >= newest_src):
                log(f"  Reusing cached joined video.")
                video_path = tmp_joined
            else:
                log(f"  Joining {len(videos)} video segments…")
                sess_prog(done_jobs, 0.0, 0, "Joining clips…")
                try:
                    concat_videos(videos, tmp_joined)
                    video_path = tmp_joined
                    sess_prog(done_jobs, join_share, 0, "")
                except Exception as e:
                    log(f"  ✗ Join failed: {e}")
                    errors.append(str(e))
                    done_jobs += 1
                    continue

        # ── Per-session info overrides (manual metadata) ─────────────────────
        info_overrides = session_info.get(abs_csv, {})

        # ── Track map geometry (OSM circuit outline + area polygons) ─────────
        _track_map_geometry = []
        _track_map_areas    = []
        if track_map_selections:
            from track_map_cache import load_geometry as _load_osm, load_areas as _load_areas
            track_name = (info_overrides.get('info_track') or
                          getattr(sess, 'track', '') or '').lower().strip()
            osm_id = track_map_selections.get(track_name, '')
            if osm_id:
                try:
                    _track_map_geometry = _load_osm(osm_id)
                except Exception:
                    pass
            # Load area polygons — derive centroid from geometry or session GPS
            if _track_map_geometry:
                try:
                    clat = sum(g['lat'] for g in _track_map_geometry) / len(_track_map_geometry)
                    clon = sum(g['lon'] for g in _track_map_geometry) / len(_track_map_geometry)
                    _track_map_areas = _load_areas(clat, clon)
                except Exception:
                    pass

        # ── Resolve reference lap ─────────────────────────────────────────────
        static_ref_lap = None
        if ref_mode in ('custom', 'track_library') and ref_lap_obj is not None:
            static_ref_lap = ref_lap_obj
            log(f"  Delta vs: {static_ref_lap.duration:.3f}s (custom)")
        elif ref_mode not in ('session_best_so_far', 'none', 'custom', 'track_library'):
            static_ref_lap, _ref_desc = resolve_reference_lap(
                ref_mode         = ref_mode,
                sess             = sess,
                session_info     = session_info,
                scan_cache       = scan_cache,
                ref_lap_csv_path = ref_lap_csv_path,
                ref_lap_num      = ref_lap_num,
                load_session_fn  = load_any_session,
            )
            if static_ref_lap:
                log(f"  Delta vs: {_ref_desc}")
            else:
                log(f"  Delta vs: {_ref_desc} — no reference lap")

        def scaled_prog(pct, msg):
            sess_prog(done_jobs, join_share, pct, msg)

        # Allow per-item scope override (set from the Overlay tab)
        item_scope = item.get('scope') or scope

        def _ref_for_lap(lap_num: Optional[int] = None):
            """Return the reference lap for a given lap number (handles session_best_so_far)."""
            if ref_mode == 'session_best_so_far':
                ref, desc = resolve_reference_lap(
                    ref_mode        = 'session_best_so_far',
                    sess            = sess,
                    session_info    = session_info,
                    scan_cache      = scan_cache,
                    current_lap_num = lap_num,
                    load_session_fn = load_any_session,
                )
                if ref:
                    log(f"  Delta vs: {desc}")
                return ref
            return static_ref_lap

        try:
            if item_scope == 'selected_lap':
                lap_idx = int(item.get('lap_idx', 0))
                if lap_idx < 0 or lap_idx >= len(sess.laps):
                    log(f"  ✗ Invalid lap index {lap_idx} (session has {len(sess.laps)} laps)")
                    done_jobs += 1
                    continue
                lap   = sess.laps[lap_idx]
                label = f"Lap{lap_idx + 1:02d}"
                out   = os.path.join(export_path, f"{_export_stem(sess, label)}{_ext}")
                log(f"  Lap {lap_idx + 1}: {lap.duration:.3f}s → {os.path.basename(out)}")
                render_lap(
                    video_path, out, sess, RenderJob(_export_stem(sess, label), lap),
                    sync_offset=offset, encoder=encoder, crf=crf,
                    n_workers=workers, show_map=show_map,
                    show_telemetry=show_tel, padding=padding,
                    is_bike=is_bike, overlay_layout=layout,
                    progress_cb=scaled_prog, log_cb=log,
                    reference_lap=_ref_for_lap(lap.lap_num),
                    info_overrides=info_overrides,
                    overlay_only=overlay_only,
                    track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                )

            elif item_scope == 'fastest':
                lap = sess.fastest_lap
                if not lap:
                    log("  ✗ No timed lap found")
                    done_jobs += 1
                    continue
                out = os.path.join(export_path, f"{_export_stem(sess, 'Fastest')}{_ext}")
                log(f"  Fastest lap: {lap.duration:.3f}s → {os.path.basename(out)}")
                render_lap(
                    video_path, out, sess, RenderJob(_export_stem(sess, 'Fastest'), lap),
                    sync_offset=offset, encoder=encoder, crf=crf,
                    n_workers=workers, show_map=show_map,
                    show_telemetry=show_tel, padding=padding,
                    is_bike=is_bike, overlay_layout=layout,
                    progress_cb=scaled_prog, log_cb=log,
                    reference_lap=_ref_for_lap(lap.lap_num),
                    info_overrides=info_overrides,
                    overlay_only=overlay_only,
                    track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                )

            elif item_scope == 'all_laps':
                laps = sess.timed_laps   # skip outlap / inlap
                if not laps:
                    log("  ✗ No timed laps found")
                    done_jobs += 1
                    continue
                for i, lap in enumerate(laps, 1):
                    label = f"Lap{i:02d}"
                    out = os.path.join(export_path, f"{_export_stem(sess, label)}{_ext}")
                    log(f"  Lap {i}/{len(laps)}: {lap.duration:.3f}s")
                    render_lap(
                        video_path, out, sess, RenderJob(_export_stem(sess, label), lap),
                        sync_offset=offset, encoder=encoder, crf=crf,
                        n_workers=workers, show_map=show_map,
                        show_telemetry=show_tel, padding=padding,
                        is_bike=is_bike, overlay_layout=layout,
                        progress_cb=scaled_prog, log_cb=log,
                        reference_lap=_ref_for_lap(lap.lap_num),
                        info_overrides=info_overrides,
                        overlay_only=overlay_only,
                        track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                    )

            elif item_scope == 'lap_range':
                timed     = sess.timed_laps
                start_num = item.get('lap_range_start')
                end_num   = item.get('lap_range_end')
                if not timed:
                    log("  ✗ No timed laps found")
                    done_jobs += 1
                    continue
                if start_num is None:
                    start_num = timed[0].lap_num
                if end_num is None:
                    end_num = timed[-1].lap_num
                start_num, end_num = int(start_num), int(end_num)
                included = [l for l in timed if start_num <= l.lap_num <= end_num]
                if not included:
                    log(f"  ✗ No timed laps in range {start_num}–{end_num}")
                    done_jobs += 1
                    continue
                range_pts = [p for l in included for p in l.points]
                range_lap = Lap(
                    lap_num  = -1,
                    points   = range_pts,
                    duration = range_pts[-1].elapsed - range_pts[0].elapsed,
                )
                first_n = included[0].lap_num
                last_n  = included[-1].lap_num
                label   = f"Laps{first_n:02d}-{last_n:02d}"
                out = os.path.join(export_path, f"{_export_stem(sess, label)}{_ext}")
                log(f"  Lap range {first_n}–{last_n} ({len(included)} laps) → {os.path.basename(out)}")
                render_lap(
                    video_path, out, sess, RenderJob(label, range_lap),
                    sync_offset=offset, encoder=encoder, crf=crf,
                    n_workers=workers, show_map=show_map,
                    show_telemetry=show_tel, padding=padding,
                    is_bike=is_bike, overlay_layout=layout,
                    progress_cb=scaled_prog, log_cb=log,
                    reference_lap=_ref_for_lap(included[0].lap_num),
                    info_overrides=info_overrides,
                    overlay_only=overlay_only,
                    track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                )

            elif item_scope == 'full':
                out = os.path.join(export_path, f"{_export_stem(sess, 'Full')}{_ext}")
                log(f"  Full session → {os.path.basename(out)}")
                render_lap(
                    video_path or '', out, sess, RenderJob(_export_stem(sess, 'Full'), None),
                    sync_offset=offset, encoder=encoder, crf=crf,
                    n_workers=workers, show_map=show_map,
                    show_telemetry=show_tel, padding=0.0,
                    is_bike=is_bike, overlay_layout=layout,
                    progress_cb=scaled_prog, log_cb=log,
                    reference_lap=static_ref_lap,
                    info_overrides=info_overrides,
                    overlay_only=overlay_only,
                    track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                )

            elif item_scope == 'clip':
                pts = sess.all_points
                if pts:
                    sess_end = pts[-1].elapsed
                    c_start  = max(0.0, min(clip_start_s, sess_end))
                    c_end    = max(c_start + 0.1, min(clip_end_s, sess_end))
                else:
                    c_start, c_end = clip_start_s, clip_end_s
                clip_pts = [p for p in pts if c_start <= p.elapsed <= c_end]
                if not clip_pts:
                    log(f"  ✗ No data points in range {c_start:.1f}–{c_end:.1f}s")
                    done_jobs += 1
                    continue
                clip_lap = Lap(
                    lap_num  = -1,
                    points   = clip_pts,
                    duration = c_end - c_start,
                )
                tag = f"Clip_{int(c_start)}s_{int(c_end)}s"
                out = os.path.join(export_path, f"{_export_stem(sess, tag)}{_ext}")
                log(f"  Clip {c_start:.1f}s–{c_end:.1f}s → {os.path.basename(out)}")
                render_lap(
                    video_path or '', out, sess, RenderJob(_export_stem(sess, tag), clip_lap),
                    sync_offset=offset, encoder=encoder, crf=crf,
                    n_workers=workers, show_map=show_map,
                    show_telemetry=show_tel, padding=padding,
                    is_bike=is_bike, overlay_layout=layout,
                    reference_lap=static_ref_lap,
                    progress_cb=scaled_prog, log_cb=log,
                    info_overrides=info_overrides,
                    overlay_only=overlay_only,
                    track_map_geometry=_track_map_geometry,
                    track_map_areas=_track_map_areas,
                )

        except Exception as e:
            log(f"  ✗ Render error: {e}")
            errors.append(str(e))
        finally:
            pass  # keep tmp_joined as cache for future exports

        done_jobs += 1
        sess_prog(done_jobs, 0, 0, "")

    if errors:
        done_cb(False, f"{len(errors)} error(s) — see log")
    else:
        done_cb(True, f"Done — {done_jobs} session(s) exported")
