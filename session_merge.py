"""
session_merge.py — Combine two telemetry Sessions covering the same video
into a single Session.

Used when a video has two telemetry files (e.g. a MoTeC .ld ECU log and an
AIM .xrk-derived GPS/accel log) that should both be usable rather than
picked between. Primary's own data (fixed fields, its own extra channels,
lap structure) is always kept exactly as-is — never overwritten. Every
channel the secondary session has — the handful of fixed telemetry fields
it captures plus its own extra channels — is added onto the merged session
as an additional channel, qualified with the secondary file's name (e.g.
"Speed (MoTeC Data.ld)") so an identically-named channel from each file
stays separately selectable rather than one silently overwriting the other.

The merged Session is a drop-in replacement everywhere a Session is used —
video_renderer.py and the gauge plugins only ever call
session.interpolate_at(t), so they need no changes to consume a merged one.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Dict, Tuple

from data_model import Session

# The subset of DataPoint's fixed fields with a direct 1:1 gauge-channel
# meaning, each mapped to (label, unit) for the qualified channel name/meta
# injected from the secondary session. Excluded on purpose:
#   - g_meter: derived from gforce_x/gforce_y together, not a stored field
#   - lap_time/delta_time: computed from lap timing, not literal per-file data
#   - lat/lon: feed the Map gauge, which always uses primary's own GPS track
QUALIFIABLE_FIELDS: Dict[str, Tuple[str, str]] = {
    'speed':        ('Speed',    'km/h'),
    'gforce_x':     ('Long G',   'G'),
    'gforce_y':     ('Lat G',    'G'),
    'lean_angle':   ('Lean',     '°'),
    'alt':          ('Altitude', 'm'),
    'rpm':          ('RPM',      'rpm'),
    'exhaust_temp': ('Exhaust Temp', '°C'),
    'gear':         ('Gear',     ''),
}


def merge_sessions(primary: Session, secondary: Session, offset: float = 0.0) -> Session:
    """
    Combine two Sessions into one: primary's own points (fixed fields, laps,
    timing) are kept exactly as-is, and every channel the secondary provides
    is added alongside primary's own extras under a name qualified with the
    secondary file's basename — see module docstring.

    offset convention: secondary_elapsed = primary_elapsed + offset (mirrors
    auto_sync.py's video/telemetry offset sign convention, extended to a
    telemetry-vs-telemetry pair).
    """
    secondary_label = os.path.basename(secondary.csv_path) or secondary.source

    def _qualify(name: str) -> str:
        return f'{name} ({secondary_label})'

    merged_points = []
    for pt in primary.all_points:
        sec_pt = secondary.interpolate_at(pt.elapsed + offset)
        if sec_pt is None:
            merged_points.append(pt)
            continue
        new_extra = dict(pt.extra)
        for attr, (label, _unit) in QUALIFIABLE_FIELDS.items():
            new_extra[_qualify(label)] = getattr(sec_pt, attr)
        for key, val in sec_pt.extra.items():
            new_extra[_qualify(key)] = val
        merged_points.append(dataclasses.replace(pt, extra=new_extra))

    # Laps keep primary's boundaries/duration/outlap/inlap — only swap in
    # the corresponding merged point objects.
    point_index = {id(p): i for i, p in enumerate(primary.all_points)}
    merged_laps = [
        dataclasses.replace(lap, points=[merged_points[point_index[id(p)]] for p in lap.points])
        for lap in primary.laps
    ]

    merged_extra_meta = dict(primary.extra_channel_meta)
    for attr, (label, unit) in QUALIFIABLE_FIELDS.items():
        qlabel = _qualify(label)
        merged_extra_meta[qlabel] = {'label': qlabel, 'unit': unit}
    for key, meta in secondary.extra_channel_meta.items():
        qkey = _qualify(key)
        merged_extra_meta[qkey] = {**meta, 'label': _qualify(meta.get('label', key))}

    sources = sorted({primary.source, secondary.source})
    merged_source = ' + '.join(sources) if len(sources) > 1 else primary.source

    return dataclasses.replace(
        primary,
        source=merged_source,
        all_points=merged_points,
        laps=merged_laps,
        extra_channel_meta=merged_extra_meta,
    )
