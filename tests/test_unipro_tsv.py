"""
Tests for unipro_data.py's Unipro Analyser .tsv export loader (load_tsv /
is_unipro_tsv / _select_real_block).

Uses a synthetic .tsv built in-test (not a real user's telemetry) — the
column layout is fully documented by the file's own header row, so this is
reproducible without needing a real export.
"""
import pytest

from unipro_data import is_unipro_tsv, load_tsv, _select_real_block, _FILENAME_STAMP_RE


_HEADER = [
    'Start Date', 'Start Time', 'Lap Number', 'Session Time', 'Lap Time',
    'Latitude', 'Longitude', 'Altitude', 'Speed', 'GPS Speed',
    'GPS Lateral Acceleration', 'GPS Longitudinal Acceleration',
    'Vertical Acceleration', 'RPM', 'Gear', 'Temperature 1',
]


def _row(fields: dict) -> str:
    return '\t'.join(str(fields.get(h, '')) for h in _HEADER)


def _build_tsv(blocks) -> str:
    """blocks: list of (date, time, [row-field-dicts]). Each row dict need
    only specify the fields it updates (sparse, like a real export) — Start
    Date/Start Time are filled in automatically for every row."""
    lines = ['\t'.join(f'"{h}"' for h in _HEADER)]
    for date, time, rows in blocks:
        for r in rows:
            full = {'Start Date': date, 'Start Time': time, **r}
            lines.append(_row(full))
    return '\n'.join(lines) + '\n'


def _gps_row(lat, lon, session_time_ns, lap=0, lap_time_ns=None, **extra):
    return {
        'Lap Number': lap,
        'Session Time': session_time_ns,
        'Lap Time': lap_time_ns if lap_time_ns is not None else session_time_ns,
        'Latitude': lat,
        'Longitude': lon,
        **extra,
    }


# ── is_unipro_tsv ────────────────────────────────────────────────────────────

class TestIsUniproTsv:
    def test_accepts_real_header(self, tmp_path):
        p = tmp_path / 'session.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56',
                                   [_gps_row(50.1, 4.5, 0)])]), encoding='utf-8')
        assert is_unipro_tsv(str(p)) is True

    def test_rejects_wrong_extension(self, tmp_path):
        p = tmp_path / 'session.csv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56',
                                   [_gps_row(50.1, 4.5, 0)])]), encoding='utf-8')
        assert is_unipro_tsv(str(p)) is False

    def test_rejects_missing_required_columns(self, tmp_path):
        p = tmp_path / 'session.tsv'
        p.write_text('"Foo"\t"Bar"\n1\t2\n', encoding='utf-8')
        assert is_unipro_tsv(str(p)) is False

    def test_missing_file_returns_false(self):
        assert is_unipro_tsv(r'C:\nonexistent\session.tsv') is False


# ── load_tsv — basic parsing ──────────────────────────────────────────────────

class TestLoadTsvBasic:
    def test_recovers_gps_points_and_units(self, tmp_path):
        # Session Time / Lap Time are nanoseconds: 1_000_000_000 = 1.0s.
        rows = [
            _gps_row(50.0900, 4.5000, 0),
            _gps_row(50.0901, 4.5001, 1_000_000_000),
            _gps_row(50.0902, 4.5002, 2_000_000_000),
        ]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56', rows)]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert sess.source == 'Unipro'
        assert len(sess.all_points) == 3
        assert sess.all_points[0].lat == pytest.approx(50.0900)
        assert sess.all_points[0].lon == pytest.approx(4.5000)
        assert sess.all_points[1].elapsed == pytest.approx(1.0)
        assert sess.all_points[2].elapsed == pytest.approx(2.0)

    def test_carries_forward_sparse_channels(self, tmp_path):
        """RPM/Gear/Temperature update on their own rows, independent of the
        GPS fix rows — a later GPS row must pick up the last-seen value."""
        rows = [
            {'Session Time': 0, 'RPM': 9000, 'Gear': 2, 'Temperature 1': 55.0},
            _gps_row(50.0900, 4.5000, 100_000_000),
            {'Session Time': 200_000_000, 'RPM': 11000},
            _gps_row(50.0901, 4.5001, 300_000_000),
        ]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56', rows)]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert len(sess.all_points) == 2
        assert sess.all_points[0].rpm == pytest.approx(9000)
        assert sess.all_points[0].gear == 2
        assert sess.all_points[0].exhaust_temp == pytest.approx(55.0)
        # Second GPS point picks up the RPM update that happened in between,
        # but gear/temp are unchanged since they weren't updated again.
        assert sess.all_points[1].rpm == pytest.approx(11000)
        assert sess.all_points[1].gear == 2
        assert sess.all_points[1].exhaust_temp == pytest.approx(55.0)

    def test_gforce_columns_map_correctly(self, tmp_path):
        rows = [_gps_row(50.09, 4.50, 0,
                          **{'GPS Longitudinal Acceleration': 0.42,
                             'GPS Lateral Acceleration': -0.31,
                             'Vertical Acceleration': 0.15})]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56', rows)]), encoding='utf-8')

        pt = load_tsv(str(p)).all_points[0]
        assert pt.gforce_x == pytest.approx(0.42)
        assert pt.gforce_y == pytest.approx(-0.31)
        assert pt.gforce_z == pytest.approx(0.15)

    def test_raises_on_missing_required_columns(self, tmp_path):
        from exceptions import MissingHeaderError
        p = tmp_path / 'session.tsv'
        p.write_text('"Foo"\t"Bar"\n1\t2\n', encoding='utf-8')
        with pytest.raises(MissingHeaderError):
            load_tsv(str(p))

    def test_raises_when_no_gps_fixes(self, tmp_path):
        from exceptions import NoDataRowsError
        rows = [{'Session Time': 0, 'RPM': 9000}]  # never has Latitude+Longitude
        p = tmp_path / 'session.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56', rows)]), encoding='utf-8')
        with pytest.raises(NoDataRowsError):
            load_tsv(str(p))


# ── load_tsv — lap building ────────────────────────────────────────────────────

class TestLoadTsvLaps:
    def test_splits_into_laps_by_lap_number(self, tmp_path):
        rows = [
            _gps_row(50.090, 4.500, 0,                lap=0),
            _gps_row(50.091, 4.501, 20_000_000_000,   lap=0),
            _gps_row(50.092, 4.502, 21_000_000_000,   lap=1, lap_time_ns=0),
            _gps_row(50.093, 4.503, 60_000_000_000,   lap=1, lap_time_ns=39_000_000_000),
            _gps_row(50.094, 4.504, 61_000_000_000,   lap=2, lap_time_ns=0),
            _gps_row(50.095, 4.505, 100_000_000_000,  lap=2, lap_time_ns=39_000_000_000),
        ]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([('2026-07-20', '12:40:56', rows)]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert len(sess.laps) == 3
        assert sess.laps[0].is_outlap is True
        assert sess.laps[0].lap_num == 0
        assert sess.laps[1].lap_num == 1
        assert sess.laps[1].duration == pytest.approx(39.0, abs=0.1)


# ── Stray-extra-block handling (_select_real_block) ───────────────────────────

class TestSelectRealBlock:
    """
    Real Unipro Analyser exports have been observed to always contain an
    extra block of unrelated session data (apparently leftover in device
    memory) alongside the actual requested session — and which position it
    lands in isn't consistent, so it can't be assumed to always be first or
    always be last. The one reliable signal is the filename, which Unipro
    encodes as YYMMDD_HHMM_....
    """

    def test_picks_block_matching_filename_when_stray_block_is_first(self, tmp_path):
        stray = [_gps_row(57.0, 18.0, t) for t in range(0, 5_000_000_000, 1_000_000_000)]
        real = [_gps_row(50.09, 4.50, t) for t in range(0, 3_000_000_000, 1_000_000_000)]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([
            ('2015-06-28', '21:35:47', stray),   # unrelated, comes first
            ('2026-07-20', '12:40:56', real),    # matches the filename
        ]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert all(pt.lat == pytest.approx(50.09, abs=0.01) for pt in sess.all_points)

    def test_picks_block_matching_filename_when_stray_block_is_last(self, tmp_path):
        # Same session data as the "stray block first" test, but with the
        # (smaller) real block first and a (larger) stray block appended
        # after it — proves selection follows the filename match, not
        # position or block size, in either direction.
        real = [_gps_row(50.09, 4.50, t) for t in range(0, 3_000_000_000, 1_000_000_000)]
        stray = [_gps_row(57.0, 18.0, t) for t in range(0, 5_000_000_000, 1_000_000_000)]
        p = tmp_path / '260720_1240_Test Track_Driver.tsv'
        p.write_text(_build_tsv([
            ('2026-07-20', '12:40:56', real),    # matches the filename
            ('2015-06-28', '21:35:47', stray),   # unrelated, comes last, and is bigger
        ]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert all(pt.lat == pytest.approx(50.09, abs=0.01) for pt in sess.all_points)

    def test_falls_back_to_largest_block_without_filename_match(self, tmp_path):
        small = [_gps_row(57.0, 18.0, t) for t in range(0, 2_000_000_000, 1_000_000_000)]
        large = [_gps_row(50.09, 4.50, t) for t in range(0, 5_000_000_000, 1_000_000_000)]
        p = tmp_path / 'not_a_recognised_filename_pattern.tsv'
        p.write_text(_build_tsv([
            ('2015-06-28', '21:35:47', small),
            ('2026-07-20', '12:40:56', large),
        ]), encoding='utf-8')

        sess = load_tsv(str(p))
        assert len(sess.all_points) == len(large)
        assert all(pt.lat == pytest.approx(50.09, abs=0.01) for pt in sess.all_points)

    def test_filename_stamp_regex_matches_real_naming_convention(self):
        m = _FILENAME_STAMP_RE.match('260720_1240_Marienbourg GPS_Lowie.tsv')
        assert m is not None
        assert m.groups() == ('26', '07', '20', '12', '40')
