"""
Tests for unipro_data.py — the Unipro Laptimer .uni binary loader.

Uses a synthetic .uni built in-test (not a real user's telemetry — this
format's reverse-engineered chunk framing is fully known and reproducible,
see unipro_data.py's module docstring) rather than a bundled real session
fixture, since real .uni files are personal racing data.
"""
import struct

import pytest

import math

import numpy as np

from unipro_data import (
    is_unipro_uni, load_uni, _iter_chunks, _parse_date, _parse_track_name,
    _scan_gps_fixes, _reject_far_from_track, _reject_outliers, _reconstruct_elapsed,
    _scan_beacon_points, _detect_lap_crossings,
)


def _chunk(tag: bytes, payload: bytes, version: int = 1) -> bytes:
    assert len(tag) == 8
    length = len(payload)
    return tag + bytes([version]) + length.to_bytes(3, 'big') + payload


def _gps_fix(lat: float, lon: float, alt_m: float, speed_kmh: float) -> bytes:
    """4 contiguous big-endian int32 fields, matching the real format."""
    return (round(lat * 1e7).to_bytes(4, 'big', signed=True)
            + round(lon * 1e7).to_bytes(4, 'big', signed=True)
            + round(alt_m * 1000).to_bytes(4, 'big', signed=True)
            + round(speed_kmh * 100).to_bytes(4, 'big', signed=True))


def _beacon_bytes(lat: float, lon: float) -> bytes:
    """Unipro's zero-padded 64-bit beacon coordinate pair, as found in
    RECRGLOS: [lat_raw][0][lon_raw][0], same raw/1e7 scale as GPS fixes."""
    return (round(lat * 1e7).to_bytes(4, 'big', signed=True) + b'\x00' * 4
            + round(lon * 1e7).to_bytes(4, 'big', signed=True) + b'\x00' * 4)


def _build_uni(records, track_name='Test Circuit', date=(26, 7, 20, 12, 40, 56),
               gap_bytes=6, beacon=None):
    """Build a minimal-but-structurally-valid synthetic .uni file.

    records: list of (lat, lon, alt_m, speed_kmh) tuples, spaced *gap_bytes*
    of filler apart (mirrors the real file's records being interspersed with
    other undecoded per-channel data). beacon, if given, is a (lat, lon)
    embedded in RECRGLOS the same way Unipro stores its timing beacon.
    """
    header = b'UUni' + (4).to_bytes(4, 'big')
    date_payload = bytes([0]) + bytes(date)  # 1 padding byte + 6 date bytes
    glos_payload = b'UGse' + b'\x00' * 4 + track_name.encode('ascii') + b'\x00' * 8
    if beacon is not None:
        glos_payload += _beacon_bytes(*beacon)
    data_payload = b''
    for lat, lon, alt_m, speed_kmh in records:
        data_payload += b'\xaa' * gap_bytes + _gps_fix(lat, lon, alt_m, speed_kmh)
    data_payload += b'\x00' * 8  # trailing padding so the last record's speed field is in-bounds

    body = (_chunk(b'RECRDATE', date_payload)
            + _chunk(b'RECRGLOS', glos_payload)
            + _chunk(b'RECRDATA', data_payload))
    return header + body


# ── is_unipro_uni ──────────────────────────────────────────────────────────────

class TestIsUniproUni:
    def test_rejects_non_uni_extension(self, tmp_path):
        p = tmp_path / 'session.csv'
        p.write_bytes(b'UUni' + b'\x00' * 100)
        assert is_unipro_uni(str(p)) is False

    def test_rejects_wrong_magic(self, tmp_path):
        p = tmp_path / 'session.uni'
        p.write_bytes(b'NOPE' + b'\x00' * 100)
        assert is_unipro_uni(str(p)) is False

    def test_accepts_real_magic(self, tmp_path):
        p = tmp_path / 'session.uni'
        p.write_bytes(_build_uni([(50.1, 4.5, 200.0, 80.0)]))
        assert is_unipro_uni(str(p)) is True

    def test_missing_file_returns_false(self):
        assert is_unipro_uni(r'C:\nonexistent\session.uni') is False


# ── Chunk parsing ──────────────────────────────────────────────────────────────

class TestChunkParsing:
    def test_iter_chunks_finds_all_three(self):
        raw = _build_uni([(50.1, 4.5, 200.0, 80.0)])
        tags = [tag for tag, *_ in _iter_chunks(raw)]
        assert tags == [b'RECRDATE', b'RECRGLOS', b'RECRDATA']

    def test_parse_date(self):
        payload = bytes([0, 26, 7, 20, 12, 40, 56])
        dt = _parse_date(payload)
        assert dt.year == 2026 and dt.month == 7 and dt.day == 20
        assert dt.hour == 12 and dt.minute == 40 and dt.second == 56

    def test_parse_date_too_short_returns_none(self):
        assert _parse_date(b'\x00\x01\x02') is None

    def test_parse_track_name_finds_longest_printable_run(self):
        payload = b'UGse' + b'\x00\x03\x04' + b'Spa-Francorchamps' + b'\x00' * 4
        assert _parse_track_name(payload, 'fallback') == 'Spa-Francorchamps'

    def test_parse_track_name_falls_back_when_nothing_printable(self):
        assert _parse_track_name(b'\x00' * 20, 'fallback-name') == 'fallback-name'


# ── GPS fix scanning ────────────────────────────────────────────────────────────

class TestScanGpsFixes:
    def test_recovers_exact_values(self):
        raw = _gps_fix(50.093810, 4.501186, 217.369, 80.19)
        hits = _scan_gps_fixes(b'\x00' * 8 + raw + b'\x00' * 8)
        assert len(hits) == 1
        _, lat, lon, alt, spd = hits[0]
        assert lat == pytest.approx(50.093810, abs=1e-6)
        assert lon == pytest.approx(4.501186, abs=1e-6)
        assert alt == pytest.approx(217.369, abs=1e-3)
        assert spd == pytest.approx(80.19, abs=1e-2)

    def test_implausible_altitude_is_not_matched(self):
        # Altitude field wildly out of range must not be picked up AT that
        # record's own offset (a heuristic scan can't rule out some other,
        # unrelated byte window elsewhere coincidentally looking plausible —
        # that's a real, acceptable limitation of a value-range heuristic,
        # not what this test is checking).
        raw = (round(50.0 * 1e7).to_bytes(4, 'big', signed=True)
               + round(4.5 * 1e7).to_bytes(4, 'big', signed=True)
               + (50_000_000).to_bytes(4, 'big', signed=True)  # 50,000 km "altitude"
               + round(80.0 * 100).to_bytes(4, 'big', signed=True))
        hits = _scan_gps_fixes(b'\x00' * 8 + raw + b'\x00' * 8)
        assert 8 not in [h[0] for h in hits]

    def test_finds_multiple_records_at_correct_offsets(self):
        recs = [(50.10, 4.50, 200.0, 80.0), (50.11, 4.51, 201.0, 82.0)]
        blob = b'\x00' * 8
        offsets_expected = []
        for r in recs:
            blob += b'\xaa' * 6
            offsets_expected.append(len(blob))
            blob += _gps_fix(*r)
        blob += b'\x00' * 8
        hits = _scan_gps_fixes(blob)
        assert len(hits) == 2
        assert [h[0] for h in hits] == offsets_expected


# ── Outlier rejection ────────────────────────────────────────────────────────────

class TestOutlierRejection:
    def test_reject_far_from_track_drops_wrong_continent_point(self):
        hits = [
            (0,   50.10, 4.50, 200.0, 80.0),
            (100, -21.82, 100.66, 3000.0, 0.1),  # nowhere near the others
            (200, 50.11, 4.51, 201.0, 82.0),
            (300, 50.12, 4.52, 202.0, 83.0),
        ]
        cleaned = _reject_far_from_track(hits)
        assert len(cleaned) == 3
        assert all(abs(h[1] - 50.1) < 1.0 for h in cleaned)

    def test_reject_outliers_drops_physically_impossible_jump(self):
        # Point 2 implies an impossible speed to reach from its neighbours
        # (same track vicinity, but far enough given the tiny time gap).
        # Two legitimate points on either side give the algorithm enough
        # context to isolate the one bad point rather than losing everything
        # (a 3-point version of this, with the bad one in the middle, is
        # ambiguous — both its only neighbours contradict it too).
        hits = [
            (0,   50.1000, 4.5000, 200.0, 80.0),
            (100, 50.1001, 4.5001, 200.0, 80.0),
            (200, 50.4000, 4.8000, 200.0, 80.0),  # ~40km away, 0.1s later — impossible
            (300, 50.1002, 4.5002, 200.0, 80.0),
            (400, 50.1003, 4.5003, 200.0, 80.0),
        ]
        elapsed = [0.0, 0.1, 0.2, 0.3, 0.4]
        cleaned_hits, cleaned_elapsed = _reject_outliers(hits, elapsed)
        assert len(cleaned_hits) == 4
        assert 50.4000 not in [h[1] for h in cleaned_hits]
        assert cleaned_hits[1][1] == pytest.approx(50.1001)

    def test_does_not_cascade_remove_legitimate_neighbours(self):
        # A single bad point between two good clusters must not take out
        # its good neighbours too (regression: an earlier version of this
        # check compared against partially-updated state within one pass).
        hits = [(i * 100, 50.10 + i * 0.0001, 4.50 + i * 0.0001, 200.0, 80.0)
                for i in range(5)]
        elapsed = [i * 0.1 for i in range(5)]
        # Corrupt the middle point to be far away.
        hits[2] = (hits[2][0], 51.5, 5.9, 200.0, 80.0)
        cleaned_hits, cleaned_elapsed = _reject_outliers(hits, elapsed)
        assert len(cleaned_hits) == 4
        assert 51.5 not in [h[1] for h in cleaned_hits]


# ── Elapsed time reconstruction ─────────────────────────────────────────────────

class TestReconstructElapsed:
    def test_uniform_stride_gives_uniform_elapsed(self):
        offsets = [0, 210, 420, 630, 840]
        elapsed = _reconstruct_elapsed(offsets)
        assert elapsed == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])

    def test_skipped_sample_is_recovered_as_a_double_step(self):
        # A gap of ~2x the normal stride means one native sample was
        # skipped (still-undecoded delta record) — elapsed must jump 0.2s,
        # not 0.1s, to stay correct.
        offsets = [0, 210, 420, 840, 1050]  # 840 is a skipped-one gap
        elapsed = _reconstruct_elapsed(offsets)
        assert elapsed == pytest.approx([0.0, 0.1, 0.2, 0.4, 0.5])

    def test_single_offset_returns_zero(self):
        assert _reconstruct_elapsed([42]) == [0.0]


# ── Full load_uni() ──────────────────────────────────────────────────────────────

class TestLoadUni:
    def test_basic_session(self, tmp_path):
        records = [
            (50.0938102, 4.5011864, 217.369, 80.19),
            (50.0938140, 4.5012220, 217.304, 83.48),
            (50.0938150, 4.5012570, 217.472, 84.78),
            (50.0938160, 4.5012910, 217.274, 86.12),
        ]
        p = tmp_path / 'session.uni'
        p.write_bytes(_build_uni(records, track_name='Karting de Fagnes'))

        sess = load_uni(str(p))
        assert sess.source == 'Unipro'
        assert sess.track == 'Karting de Fagnes'
        assert sess.date_utc.startswith('2026-07-20T12:40:56')
        assert len(sess.all_points) == 4
        assert len(sess.laps) == 1
        assert sess.laps[0].lap_num == 1

        p0 = sess.all_points[0]
        assert p0.lat == pytest.approx(50.0938102, abs=1e-6)
        assert p0.lon == pytest.approx(4.5011864, abs=1e-6)
        assert p0.alt == pytest.approx(217.369, abs=1e-2)
        assert p0.speed == pytest.approx(80.19, abs=1e-1)
        assert p0.elapsed == 0.0
        assert p0.lap == 1

        # Elapsed increases monotonically at the expected ~0.1s native tick.
        elapsed_vals = [p.elapsed for p in sess.all_points]
        assert elapsed_vals == sorted(elapsed_vals)
        assert elapsed_vals[-1] == pytest.approx(0.3, abs=0.05)

    def test_raises_on_wrong_magic(self, tmp_path):
        from exceptions import MissingHeaderError
        p = tmp_path / 'session.uni'
        p.write_bytes(b'NOPE' + b'\x00' * 100)
        with pytest.raises(MissingHeaderError):
            load_uni(str(p))

    def test_raises_when_no_recrdata_chunk(self, tmp_path):
        from exceptions import NoDataRowsError
        p = tmp_path / 'session.uni'
        header = b'UUni' + (4).to_bytes(4, 'big')
        p.write_bytes(header + _chunk(b'RECRDATE', bytes([0, 26, 7, 20, 12, 40, 56])))
        with pytest.raises(NoDataRowsError):
            load_uni(str(p))

    def test_raises_when_no_gps_fixes_found(self, tmp_path):
        from exceptions import NoDataRowsError
        p = tmp_path / 'session.uni'
        header = b'UUni' + (4).to_bytes(4, 'big')
        body = _chunk(b'RECRDATA', b'\x00' * 200)  # no plausible GPS pattern anywhere
        p.write_bytes(header + body)
        with pytest.raises(NoDataRowsError):
            load_uni(str(p))

    def test_derives_gforce_from_gps_speed_and_heading(self, tmp_path):
        # A session with varying speed/heading should produce non-zero
        # derived longitudinal/lateral G — not the DataPoint defaults.
        # Position deltas are small and realistic for ~0.1s @ under 100 km/h
        # (a few metres each) with a deliberate direction change, so the
        # implied speed stays well under the outlier-rejection threshold —
        # only the *stated* speed field varies sharply, which is what
        # actually drives the derived longitudinal G.
        records = [
            (50.09000, 4.50000, 200.0, 50.0),
            (50.09002, 4.50002, 200.0, 90.0),
            (50.09004, 4.50001, 200.0, 60.0),
            (50.09006, 4.50003, 200.0, 40.0),
        ]
        p = tmp_path / 'session.uni'
        p.write_bytes(_build_uni(records))
        sess = load_uni(str(p))
        assert any(p.gforce_x != 0.0 for p in sess.all_points)

    def test_track_name_falls_back_to_filename(self, tmp_path):
        header = b'UUni' + (4).to_bytes(4, 'big')
        data_payload = b'\xaa' * 6 + _gps_fix(50.1, 4.5, 200.0, 80.0) + b'\x00' * 8
        body = _chunk(b'RECRDATA', data_payload)
        p = tmp_path / 'my_cool_session.uni'
        p.write_bytes(header + body)
        sess = load_uni(str(p))
        assert sess.track == 'my_cool_session'


# ── Beacon-coordinate scanning ──────────────────────────────────────────────────

class TestScanBeaconPoints:
    REF_LAT, REF_LON = 50.09, 4.50

    def test_finds_beacon_near_reference(self):
        lat, lon = 50.0938834, 4.5009860
        payload = b'\x00' * 20 + _beacon_bytes(lat, lon) + b'\x00' * 20
        pts = _scan_beacon_points(payload, self.REF_LAT, self.REF_LON)
        assert len(pts) == 1
        assert pts[0][0] == pytest.approx(lat, abs=1e-6)
        assert pts[0][1] == pytest.approx(lon, abs=1e-6)

    def test_ignores_pattern_far_from_reference(self):
        # Same zero-padded shape, but nowhere near the session's own track —
        # must not be mistaken for a beacon (e.g. an unrelated field in the
        # payload that coincidentally has zero padding on both sides).
        payload = b'\x00' * 20 + _beacon_bytes(51.5, 5.9) + b'\x00' * 20
        assert _scan_beacon_points(payload, self.REF_LAT, self.REF_LON) == []

    def test_finds_multiple_beacons_in_file_order(self):
        coords = [(50.0938, 4.5010), (50.0940, 4.5020), (50.0935, 4.4995)]
        payload = b''.join(_beacon_bytes(lat, lon) for lat, lon in coords)
        pts = _scan_beacon_points(payload, self.REF_LAT, self.REF_LON)
        assert len(pts) == 3
        for (lat, lon), (elat, elon) in zip(pts, coords):
            assert lat == pytest.approx(elat, abs=1e-6)
            assert lon == pytest.approx(elon, abs=1e-6)

    def test_no_beacon_pattern_returns_empty(self):
        assert _scan_beacon_points(b'\x00' * 64, self.REF_LAT, self.REF_LON) == []


# ── Lap-crossing detection ──────────────────────────────────────────────────────

class TestDetectLapCrossings:
    GATE_LAT, GATE_LON = 50.09, 4.50

    def _circular_track(self, laps=3, pts_per_lap=40, radius_m=50.0, dt=0.5):
        """Points evenly spaced around a circle that starts and re-crosses
        exactly at the gate coordinate every lap."""
        m_per_deg_lat = 110_540.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(self.GATE_LAT))
        n = laps * pts_per_lap + 1
        lats, lons, elapsed = [], [], []
        for i in range(n):
            angle = 2 * math.pi * (i / pts_per_lap)
            east  = radius_m * math.sin(angle)
            north = radius_m * (1 - math.cos(angle))
            lats.append(self.GATE_LAT + north / m_per_deg_lat)
            lons.append(self.GATE_LON + east / m_per_deg_lon)
            elapsed.append(i * dt)
        return elapsed, np.array(lats), np.array(lons)

    def test_detects_one_crossing_per_lap(self):
        elapsed, lats, lons = self._circular_track(laps=3, pts_per_lap=40, dt=0.5)
        crossings = _detect_lap_crossings(
            elapsed, lats, lons, self.GATE_LAT, self.GATE_LON,
            min_lap_time=5.0, gate_radius_m=30.0,
        )
        # 3 full loops starting and ending at the gate -> 3 crossings (one at
        # the end of each lap, including the final point which lands back on it).
        assert len(crossings) == 3
        expected = [40 * 0.5, 80 * 0.5, 120 * 0.5]
        for got, want in zip(crossings, expected):
            assert got == pytest.approx(want, abs=0.5)

    def test_no_crossings_when_track_never_nears_gate(self):
        elapsed, lats, lons = self._circular_track(laps=2, pts_per_lap=40, dt=0.5)
        assert _detect_lap_crossings(
            elapsed, lats, lons, gate_lat=51.5, gate_lon=5.9,
        ) == []

    def test_min_lap_time_suppresses_noise_double_trigger(self):
        # A gate period well under min_lap_time must collapse to a single
        # crossing rather than one per lap.
        elapsed, lats, lons = self._circular_track(laps=3, pts_per_lap=40, dt=0.1)
        crossings = _detect_lap_crossings(
            elapsed, lats, lons, self.GATE_LAT, self.GATE_LON,
            min_lap_time=15.0, gate_radius_m=30.0,
        )
        assert len(crossings) == 1

    def test_too_few_points_returns_empty(self):
        assert _detect_lap_crossings([0.0, 0.1], np.array([50.09, 50.10]),
                                      np.array([4.5, 4.5]), 50.09, 4.5) == []


# ── Full load_uni() lap splitting ────────────────────────────────────────────────

class TestLoadUniLapDetection:
    GATE_LAT, GATE_LON = 50.0938834, 4.5009860

    def _circular_records(self, laps, pts_per_lap, radius_m=50.0,
                           speed_kmh=50.0, alt_m=200.0):
        m_per_deg_lat = 110_540.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(self.GATE_LAT))
        records = []
        for i in range(laps * pts_per_lap + 1):
            angle = 2 * math.pi * (i / pts_per_lap)
            east  = radius_m * math.sin(angle)
            north = radius_m * (1 - math.cos(angle))
            lat = self.GATE_LAT + north / m_per_deg_lat
            lon = self.GATE_LON + east / m_per_deg_lon
            records.append((lat, lon, alt_m, speed_kmh))
        return records

    def test_splits_circular_track_into_laps_when_beacon_present(self, tmp_path):
        # Reconstructed elapsed time advances at a fixed ~0.1s/record tick
        # (see _reconstruct_elapsed), so pts_per_lap must be large enough
        # that each lap clears _detect_lap_crossings' min_lap_time floor.
        records = self._circular_records(laps=3, pts_per_lap=200)
        p = tmp_path / 'session.uni'
        p.write_bytes(_build_uni(records, beacon=(self.GATE_LAT, self.GATE_LON)))

        sess = load_uni(str(p))
        assert len(sess.laps) > 1
        assert sess.laps[0].is_outlap is True
        assert sess.laps[0].lap_num == 0
        timed = [l for l in sess.laps if not l.is_outlap and not l.is_inlap]
        assert len(timed) >= 1
        # Every point still belongs to exactly one lap and lap_elapsed
        # restarts from 0 at each lap boundary.
        for lap in sess.laps:
            assert lap.points[0].lap_elapsed == pytest.approx(0.0, abs=1e-6)

    def test_falls_back_to_single_lap_without_beacon(self, tmp_path):
        records = self._circular_records(laps=3, pts_per_lap=200)
        p = tmp_path / 'session.uni'
        p.write_bytes(_build_uni(records))  # no beacon configured
        sess = load_uni(str(p))
        assert len(sess.laps) == 1
        assert sess.laps[0].lap_num == 1
        assert sess.laps[0].is_outlap is False
