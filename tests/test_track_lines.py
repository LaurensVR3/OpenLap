"""User start/finish and sector lines (AppConfig.track_lines): placing them,
applying them by place to every session on the circuit, and user sectors."""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_build_laps import _track_pts   # a circle driven N times at constant speed

from data_model import Session


def _session(laps=6, lap_s=30.0, offset_deg=0.0):
    pts = _track_pts(laps=laps, lap_s=lap_s)
    return Session(source='T', date_utc='', track='', configuration='', session_type='',
                   best_lap_time=0.0, all_points=pts, laps=[])


def _point_at(sess, frac):
    """(lat, lon) a fraction of the way round the first lap."""
    pts = sess.all_points
    lap_n = int(30.0 * 10)
    p = pts[int(frac * lap_n) % lap_n]
    return p.lat, p.lon


@pytest.fixture
def api_with(tmp_config_dir, monkeypatch):
    import app_config
    # set_track_line also clears the metadata cache: keep it off the real ~/.openlap
    monkeypatch.setattr(app_config, 'FILE_META_CACHE_FILE', tmp_config_dir / 'meta.json')
    monkeypatch.setattr(app_config, 'SCAN_CACHE_FILE', tmp_config_dir / 'scan.json')
    from webview_api import WebviewAPI
    api = WebviewAPI()
    sessions = {}

    def load_one(path):
        return _session() if path not in sessions else sessions[path]()
    monkeypatch.setattr(api, '_load_one_session', load_one)
    return api


def test_placing_a_finish_line_cuts_laps_there(api_with):
    base = _session()
    lat, lon = _point_at(base, 0.25)
    api_with.set_track_line('/a.csv', 'finish', lat, lon)
    s = api_with._load_session('/a.csv')
    assert s.finish_line is not None
    full = [l for l in s.laps if not l.is_outlap and not l.is_inlap]
    assert len(full) >= 4 and all(l.duration == pytest.approx(30.0, abs=0.02) for l in full)
    # lap 1 now starts a quarter of the way round (7.5 s in)
    assert s.laps[1].points[0].elapsed == pytest.approx(7.5, abs=0.11)


def test_lines_apply_to_another_session_on_the_same_circuit(api_with):
    lat, lon = _point_at(_session(), 0.5)
    api_with.set_track_line('/a.csv', 'finish', lat, lon)
    other = api_with._load_session('/b.csv')      # a different file, same place
    assert other.finish_line == api_with._config.track_lines[0]['finish']


def test_sectors_are_kept_in_driving_order(api_with):
    base = _session()
    api_with.set_track_line('/a.csv', 'finish', *_point_at(base, 0.0))
    api_with.set_track_line('/a.csv', 'sector', *_point_at(base, 0.75))
    api_with.set_track_line('/a.csv', 'sector', *_point_at(base, 0.25))
    s = api_with._load_session('/a.csv')
    from lap_detection import sector_boundaries
    lap = [l for l in s.laps if not l.is_outlap and not l.is_inlap][0]
    b = sector_boundaries(lap, s.sector_lines)
    assert b == sorted(b) and b[0] == pytest.approx(7.5, abs=0.2) and b[1] == pytest.approx(22.5, abs=0.2)


def test_user_sectors_split_the_lap_and_add_up(api_with):
    import video_renderer as vr
    base = _session()
    api_with.set_track_line('/a.csv', 'finish', *_point_at(base, 0.0))
    api_with.set_track_line('/a.csv', 'sector', *_point_at(base, 0.5))
    s = api_with._load_session('/a.csv')
    full = [l for l in s.laps if not l.is_outlap and not l.is_inlap]
    st = vr._setup_delta_time(full[0], vr.RenderJob('', full[1]), s)
    assert len(st['sectors']) == 2
    assert sum(x['cur_t'] for x in st['sectors']) == pytest.approx(full[1].duration, abs=1e-6)
    assert st['sectors'][0]['cur_t'] == pytest.approx(15.0, abs=0.1)


def test_reset_returns_to_automatic(api_with):
    api_with.set_track_line('/a.csv', 'finish', *_point_at(_session(), 0.3))
    r = api_with.set_track_line('/a.csv', 'reset')
    assert api_with._config.track_lines == [] and r['user'] is False
    assert api_with._load_session('/a.csv').finish_line is None
