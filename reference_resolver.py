"""
reference_resolver.py — Resolve a reference lap from ref_mode and session context.
"""
from __future__ import annotations
import logging
import os
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)


def _effective_track(csv_path: str, session_info: dict, session_track: str) -> str:
    """Return effective track name, preferring manual session_info overrides."""
    abs_path = os.path.abspath(csv_path)
    override = session_info.get(abs_path, {}).get('info_track', '').strip()
    return override or (session_track or '').strip()


def resolve_reference_lap(
    ref_mode:         str,
    sess,
    session_info:     dict,
    scan_cache:       dict,
    ref_lap_csv_path: str                = '',
    ref_lap_num:      int                = 0,
    current_lap_num:  Optional[int]      = None,
    load_session_fn:  Optional[Callable] = None,
) -> Tuple:
    """Return (Lap | None, description_str)."""

    if ref_mode == 'session_best':
        lap = sess.fastest_lap
        return (lap, f'session fastest ({lap.duration:.3f}s)') if lap else (None, 'no timed laps')

    if ref_mode == 'session_best_so_far':
        if current_lap_num is None:
            lap = sess.fastest_lap
        else:
            prev = [l for l in sess.timed_laps if l.lap_num < current_lap_num]
            lap  = min(prev, key=lambda l: l.duration) if prev else None
        return (lap, f'best so far ({lap.duration:.3f}s)') if lap else (None, 'no prior laps')

    if ref_mode in ('personal_best', 'day_best'):
        return _resolve_cross_session(ref_mode, sess, session_info, scan_cache, load_session_fn)

    if ref_mode == 'manual':
        return _resolve_manual(ref_lap_csv_path, ref_lap_num, load_session_fn)

    return None, 'none'


def _resolve_cross_session(ref_mode, sess, session_info, scan_cache, load_session_fn):
    entries       = scan_cache.get('sessions', [])
    current_track = _effective_track(sess.csv_path or '', session_info, sess.track or '')

    if not current_track:
        return None, f'no track name set for current session (needed for {ref_mode})'

    current_date = None
    if ref_mode == 'day_best' and sess.start_time:
        current_date = sess.start_time.strftime('%Y-%m-%d')

    best_lap = None
    best_dur = float('inf')
    checked  = 0

    logger.debug('reference_resolver: looking for %s at track "%s" in %d cache entries',
                 ref_mode, current_track, len(entries))

    for entry in entries:
        csv_path = entry.get('csv_path', '')
        if not csv_path or not os.path.exists(csv_path):
            continue

        entry_track = _effective_track(csv_path, session_info, entry.get('track', ''))
        if not entry_track or entry_track.lower() != current_track.lower():
            continue

        if ref_mode == 'day_best':
            csv_start = entry.get('csv_start')
            if not csv_start or not current_date:
                continue
            try:
                from datetime import datetime
                entry_date = datetime.fromisoformat(csv_start).strftime('%Y-%m-%d')
            except Exception:
                continue
            if entry_date != current_date:
                continue

        if load_session_fn is None:
            continue
        try:
            checked += 1
            other = load_session_fn(csv_path)
            lap   = other.fastest_lap
            if lap and lap.duration < best_dur:
                best_dur = lap.duration
                best_lap = lap
        except Exception as e:
            logger.debug('reference_resolver: could not load %s: %s', csv_path, e)

    label = 'personal best' if ref_mode == 'personal_best' else 'day best'
    logger.debug('reference_resolver: %s — %d sessions scanned, best %.3fs',
                 label, checked, best_dur if best_lap else float('nan'))

    if best_lap:
        return best_lap, f'{label} ({best_lap.duration:.3f}s, {checked} sessions scanned)'
    if checked == 0:
        return None, f'no {label} found — no sessions with track="{current_track}" in cache'
    return None, f'no {label} found ({checked} sessions scanned at "{current_track}")'


def _resolve_manual(ref_lap_csv_path, ref_lap_num, load_session_fn):
    if not ref_lap_csv_path:
        return None, 'no manual lap selected'
    if load_session_fn is None:
        return None, 'no session loader provided'
    if not os.path.exists(ref_lap_csv_path):
        return None, f'manual lap file not found: {ref_lap_csv_path}'
    try:
        sess = load_session_fn(ref_lap_csv_path)
        lap  = next((l for l in sess.timed_laps if l.lap_num == ref_lap_num), None)
        if lap:
            return lap, f'manual lap {ref_lap_num} ({lap.duration:.3f}s)'
        return None, f'manual lap {ref_lap_num} not found in session'
    except Exception as e:
        logger.debug('reference_resolver: manual load failed: %s', e)
        return None, f'manual load failed: {e}'
