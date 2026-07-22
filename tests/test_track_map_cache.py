"""
Tests for track_map_cache.py's pure logic: haversine distance, centroid,
downsampling, and auto_select's 3km match threshold. Network (Overpass) and
disk-cache I/O are mocked/redirected — these tests never hit the real API.
"""
import math
import pytest

from track_map_cache import _haversine_m, _centroid, _downsample, auto_select, _osm_label


class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine_m(50.0, 5.0, 50.0, 5.0) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_spa_to_zolder(self):
        # Spa-Francorchamps to Circuit Zolder is roughly 80-90 km apart.
        spa   = (50.4372, 5.9714)
        zolder = (50.9903, 5.2569)
        dist = _haversine_m(*spa, *zolder)
        assert 70_000 < dist < 100_000

    def test_one_degree_latitude_is_about_111km(self):
        dist = _haversine_m(0.0, 0.0, 1.0, 0.0)
        assert dist == pytest.approx(111_195, rel=0.01)


class TestCentroid:
    def test_simple_average(self):
        lat, lon = _centroid([0.0, 2.0], [0.0, 4.0])
        assert lat == pytest.approx(1.0)
        assert lon == pytest.approx(2.0)

    def test_single_point(self):
        assert _centroid([5.0], [10.0]) == (5.0, 10.0)

    def test_empty_lists_do_not_divide_by_zero(self):
        # max(len(lats), 1) guards against ZeroDivisionError on empty input.
        assert _centroid([], []) == (0.0, 0.0)


class TestDownsample:
    def test_under_limit_returns_unchanged(self):
        pts = list(range(10))
        assert _downsample(pts, max_pts=500) == pts

    def test_over_limit_downsamples_to_max(self):
        pts = list(range(1000))
        result = _downsample(pts, max_pts=500)
        assert len(result) == 500

    def test_downsample_preserves_order_and_endpoints_roughly(self):
        pts = list(range(1000))
        result = _downsample(pts, max_pts=100)
        assert result == sorted(result)
        assert result[0] == 0


class TestAutoSelect:
    def _candidate(self, osm_id, lat, lon):
        return {'osm_id': osm_id, 'geometry': [{'lat': lat, 'lon': lon}]}

    def test_no_candidates_returns_none(self):
        assert auto_select([], [50.0], [5.0]) is None

    def test_no_gps_returns_none(self):
        candidates = [self._candidate('1', 50.0, 5.0)]
        assert auto_select(candidates, [], []) is None

    def test_picks_closest_candidate_within_threshold(self):
        candidates = [
            self._candidate('far',   51.0, 6.0),
            self._candidate('close', 50.001, 5.001),   # ~130m away
        ]
        result = auto_select(candidates, [50.0], [5.0])
        assert result == 'close'

    def test_rejects_match_beyond_3km_threshold(self):
        # Nearest candidate is still >3km from the GPS trace — must return None
        # rather than confidently picking the wrong circuit.
        candidates = [self._candidate('distant', 50.05, 5.05)]  # ~6-7km away
        assert auto_select(candidates, [50.0], [5.0]) is None

    def test_candidate_with_no_geometry_is_skipped(self):
        candidates = [
            {'osm_id': 'no-geom', 'geometry': []},
            self._candidate('has-geom', 50.001, 5.001),
        ]
        assert auto_select(candidates, [50.0], [5.0]) == 'has-geom'


class TestOsmLabel:
    def test_raceway_with_sport(self):
        label = _osm_label({'highway': 'raceway', 'sport': 'motor_racing'}, '123')
        assert label == 'Motor Racing Raceway (way 123)'

    def test_sports_centre_without_sport(self):
        label = _osm_label({'leisure': 'sports_centre'}, '456')
        assert label == 'Sports Centre (way 456)'

    def test_plain_track_fallback(self):
        label = _osm_label({}, '789')
        assert label == 'Track (way 789)'
