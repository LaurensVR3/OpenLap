"""GoPro GPMF telemetry (gopro_data). Payloads are built byte for byte to the
GPMF spec (https://github.com/gopro/gpmf-parser); the parser was also checked
by hand against GoPro's own HERO5, HERO6 and MAX sample clips."""
import struct
from datetime import datetime, timezone

import pytest

import gopro_data as g


def _klv(key, typ, size, repeat, payload):
    body = payload + b'\0' * ((-len(payload)) % 4)
    return key.encode() + bytes([ord(typ) if typ != '\0' else 0, size]) + struct.pack('>H', repeat) + body


def _nest(key, *children):
    inner = b''.join(children)
    return _klv(key, '\0', 1, len(inner), inner)


def _gps_payload(samples, fix=3, dop=150, gpsu='250731090731.000'):
    """One DEVC/STRM payload of GPS5 samples: (lat, lon, alt, speed2d m/s)."""
    scal = struct.pack('>5l', 10_000_000, 10_000_000, 1000, 1000, 100)
    rows = b''.join(struct.pack('>5l', round(la * 1e7), round(lo * 1e7), round(al * 1000),
                                round(sp * 1000), round(sp * 100)) for la, lo, al, sp in samples)
    strm = _nest('STRM',
                 _klv('STNM', 'c', 1, 3, b'GPS'),
                 _klv('GPSF', 'L', 4, 1, struct.pack('>L', fix)),
                 _klv('GPSU', 'U', 16, 1, gpsu.encode()),
                 _klv('GPSP', 'S', 2, 1, struct.pack('>H', dop)),
                 _klv('SCAL', 'l', 4, 5, scal),
                 _klv('GPS5', 'l', 20, len(samples), rows))
    return _nest('DEVC', _klv('DVNM', 'c', 1, 6, b'Camera'), strm)


def _fake_clip(monkeypatch, payloads):
    monkeypatch.setattr(g, '_gpmf_stream_index', lambda path: 3)
    monkeypatch.setattr(g, '_payloads', lambda path, index: payloads)


def test_klv_nesting_and_padding():
    data = _nest('DEVC', _klv('STNM', 'c', 1, 3, b'abc'), _klv('TMPC', 'f', 4, 1, struct.pack('>f', 21.5)))
    (key, typ, _s, _r, children), = list(g._klv(data, 0, len(data)))
    assert key == 'DEVC' and [c[0] for c in children] == ['STNM', 'TMPC']
    assert g._values(*children[1][1:]) == [pytest.approx(21.5)]


def test_gps5_samples_are_scaled_and_timed_across_the_payload(monkeypatch):
    samples = [(50.0 + i * 1e-5, 5.0, 80.0, 20.0) for i in range(18)]
    _fake_clip(monkeypatch, [(10.0, 1.0, _gps_payload(samples))])
    utc0, arr = g.read_gps('clip.mp4')
    assert len(arr) == 18
    assert arr[0, 0] == pytest.approx(10.0) and arr[-1, 0] == pytest.approx(10.0 + 17 / 18)
    assert arr[3, 1] == pytest.approx(50.00003) and arr[0, 3] == pytest.approx(80.0)
    assert arr[0, 4] == pytest.approx(20.0)
    # GPSU is the time of the payload, which starts 10 s into the video
    assert utc0 == datetime(2025, 7, 31, 9, 7, 21, tzinfo=timezone.utc)


def test_samples_without_a_fix_are_dropped(monkeypatch):
    good = _gps_payload([(50.0, 5.0, 0.0, 1.0)] * 5)
    nofix = _gps_payload([(50.0, 5.0, 0.0, 1.0)] * 5, fix=0)
    poor = _gps_payload([(50.0, 5.0, 0.0, 1.0)] * 5, dop=900)
    _fake_clip(monkeypatch, [(0.0, 1.0, nofix), (1.0, 1.0, good), (2.0, 1.0, poor)])
    _, arr = g.read_gps('clip.mp4')
    assert len(arr) == 5 and set(arr[:, 0].round(3)) <= {1.0, 1.2, 1.4, 1.6, 1.8}


def test_session_is_on_the_video_clock(monkeypatch):
    """elapsed is video time, so the sync offset is exactly 0."""
    import math
    pays = []
    for sec in range(60):
        pays.append((float(sec), 1.0, _gps_payload(
            [(50.0 + 0.0008 * math.sin((sec + i / 10) / 10), 5.0 + 0.0012 * math.cos((sec + i / 10) / 10), 0.0, 25.0)
             for i in range(10)])))
    _fake_clip(monkeypatch, pays)
    monkeypatch.setattr(g, '_recording_clips', lambda p: [p])

    class _Info:
        duration = 60.0
    monkeypatch.setattr('video_renderer.probe_video', lambda p: _Info())
    s = g.load_gopro('GX010001.MP4')
    assert s.source == 'GoPro'
    assert s.all_points[0].elapsed == pytest.approx(0.0)
    assert s.all_points[-1].elapsed == pytest.approx(59.9)
    assert max(p.speed for p in s.all_points) == pytest.approx(90.0, abs=1.0)


def test_gopro_recording_with_no_logger_becomes_a_synced_session(tmp_config_dir, monkeypatch):
    import app_config, session_scanner
    from datetime import timedelta
    monkeypatch.setattr(app_config, 'FILE_META_CACHE_FILE', tmp_config_dir / 'meta.json')
    monkeypatch.setattr(app_config, 'SCAN_CACHE_FILE', tmp_config_dir / 'scan.json')
    t0 = datetime(2025, 7, 31, 9, 0, tzinfo=timezone.utc)
    clips = [session_scanner.VideoFile('/v/GX010042.MP4', t0, 500.0, camera='c', gpmf=True),
             session_scanner.VideoFile('/v/GX020042.MP4', t0 + timedelta(seconds=500.5), 100.0,
                                       camera='c', gpmf=True)]
    monkeypatch.setattr(session_scanner, 'scan_videos', lambda *a, **k: list(clips))
    monkeypatch.setattr(session_scanner, 'scan_csvs', lambda *a, **k: [])
    monkeypatch.setattr(session_scanner, 'scan_pending_xrk', lambda *a, **k: [])
    from webview_api import WebviewAPI
    api = WebviewAPI()
    api._config.video_path = '/v'
    (s,) = api.scan_all_sessions([str(tmp_config_dir)])
    assert s['source'] == 'GoPro' and s['csv_path'] == '/v/GX010042.MP4'
    assert s['video_paths'] == ['/v/GX010042.MP4', '/v/GX020042.MP4']
    assert s['sync_offset'] == 0.0 and s['sync_source'] == 'camera'
