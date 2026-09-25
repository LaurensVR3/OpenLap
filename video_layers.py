"""
video_layers.py — Second videos shown inside the overlay ("Video" gauges).

A Video gauge shows another recording in its box, kept in step with the main
video:
  * source 'camera'    — another camera's recording of the same run (front
                         and rear, helmet and chassis), synced by audio
                         (auto_sync.audio_offset; the cameras' clocks cannot
                         be trusted);
  * source 'reference' — the reference lap's own video, started at that
                         lap's start, so both laps run side by side from the
                         line.

Every layer reduces to clips plus one number: layer_time = main_time + offset,
used identically by the export (video_renderer) and the editor preview.
"""
from __future__ import annotations

from typing import List, Optional


def lap_start(lap) -> float:
    """Session time of a lap's start line crossing (not its first sample)."""
    p = lap.points[0]
    return p.elapsed - p.lap_elapsed


def layers_for(csv_path: str, lap, layout: dict, sync_offset: float,
               second_camera: dict, offsets: dict, scan_cache: dict,
               reference_lap=None) -> List[dict]:
    """[{gauge_idx, source, clips, offset}] for the layout's Video gauges that
    have something to show for this session and lap."""
    out = []
    for idx, g in enumerate((layout or {}).get('gauges', [])):
        if g.get('type') != 'Video' or not g.get('visible', True):
            continue
        source = g.get('video_source', 'camera')
        if source == 'camera':
            cam = (second_camera or {}).get(csv_path) or {}
            if cam.get('paths') and cam.get('offset') is not None:
                # audio_offset: the main video's time at which the second one starts
                out.append({'gauge_idx': idx, 'source': source, 'clips': list(cam['paths']),
                            'offset': -float(cam['offset'])})
        elif source == 'reference' and reference_lap is not None and lap is not None and lap.points:
            ref_csv = getattr(reference_lap, 'session_csv', '') or ''
            entry = next((s for s in (scan_cache or {}).get('sessions', [])
                          if s.get('csv_path') == ref_csv), None)
            clips = (entry or {}).get('video_paths') or []
            ref_off = (offsets or {}).get(ref_csv)
            if clips and ref_off is not None and reference_lap.points:
                out.append({'gauge_idx': idx, 'source': source, 'clips': list(clips),
                            'offset': (float(ref_off) + lap_start(reference_lap))
                                      - (float(sync_offset or 0.0) + lap_start(lap))})
    return out
