"""
channel_discovery.py — Discover and classify a session's available gauge
channels: the fixed set in gauge_channels.py plus whatever extra channels a
loader captured into DataPoint.extra / Session.extra_channel_meta.
"""
from __future__ import annotations

from typing import List

from data_model import Session
from gauge_channels import GAUGE_CHANNELS

_NOISY_NAME_SUBSTRINGS = [
    'diagnostic', 'diagnos', ' state', 'pin ', 'can bus', 'internal',
    'cut count', 'uptime', 'cpu usage', 'warning', 'launch state',
]

_MIN_DISTINCT_VALUES = 8

# Bound the cardinality check to a sampled subset of points rather than the
# whole session — exact distinct-value counts aren't needed, just "is this
# a flag/state channel", and real hardware exports can have 100k+ points.
_MAX_SAMPLE_POINTS = 500


def is_noisy_channel(name: str, values: List[float]) -> bool:
    """
    Heuristic: MoTeC/AIM-style diagnostic, state, or pin-status channels vs
    real sensor data. Either signal is enough to flag a channel:
      - name looks like an internal/diagnostic/state channel
      - the channel is effectively low-cardinality (a flag/state, not a
        continuous measurement) — catches diagnostics that don't match the
        name heuristic, robust across different ECU firmware/naming.
    """
    low = name.lower()
    if any(sub in low for sub in _NOISY_NAME_SUBSTRINGS):
        return True
    if values and len(set(values)) < _MIN_DISTINCT_VALUES:
        return True
    return False


def list_channels(session: Session) -> List[dict]:
    """
    Return every gauge-selectable channel for *session*: the fixed
    GAUGE_CHANNELS set (always included, never filtered — these are
    curated, not raw dumps) plus whatever extra channels the loader
    captured, each tagged with a 'noisy' flag so the UI can filter/toggle.
    """
    result: List[dict] = [
        {'key': key, 'label': meta['label'], 'unit': meta['unit'], 'noisy': False}
        for key, meta in GAUGE_CHANNELS.items()
    ]

    pts = session.all_points
    step = max(1, len(pts) // _MAX_SAMPLE_POINTS) if pts else 1
    sample_pts = pts[::step]

    for name, meta in session.extra_channel_meta.items():
        sample_vals = [p.extra.get(name, 0.0) for p in sample_pts]
        result.append({
            'key':   name,
            'label': meta.get('label', name),
            'unit':  meta.get('unit', ''),
            'noisy': is_noisy_channel(name, sample_vals),
        })
    return result
