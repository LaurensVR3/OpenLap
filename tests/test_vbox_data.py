import pytest
from vbox_data import load_vbo, _parse_hhmmss
from exceptions import MissingHeaderError, NoDataRowsError, CSVParseError


def _write_vbo(tmp_path, channel_lines, data_row, unit_lines=None, name='session.vbo'):
    lines = ['[header]']
    lines += channel_lines
    lines.append('')
    if unit_lines is not None:
        lines.append('[channel units]')
        lines += unit_lines
        lines.append('')
    lines.append('[comments]')
    lines.append('File created on 15/06/2024 at 14:32:00 by VBOX Tools')
    lines.append('')
    lines.append('[data]')
    lines.append(data_row)
    path = tmp_path / name
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


def test_load_vbo_velocity_kmh(tmp_path):
    path = _write_vbo(
        tmp_path,
        ['time', 'latitude north', 'longitude east', 'velocity kmh'],
        '120000.00 5130.0000 00400.0000 100.0',
    )
    session = load_vbo(path)
    assert session.source_speed_unit == 'kmh'
    assert session.all_points[0].speed == pytest.approx(100.0)


def test_load_vbo_velocity_mph(tmp_path):
    path = _write_vbo(
        tmp_path,
        ['time', 'latitude north', 'longitude east', 'velocity mph'],
        '120000.00 5130.0000 00400.0000 100.0',
    )
    session = load_vbo(path)
    assert session.source_speed_unit == 'mph'
    assert session.all_points[0].speed == pytest.approx(100.0 * 1.60934)


def test_load_vbo_velocity_ms_via_channel_units(tmp_path):
    path = _write_vbo(
        tmp_path,
        ['time', 'latitude north', 'longitude east', 'velocity'],
        '120000.00 5130.0000 00400.0000 10.0',
        unit_lines=['s', 'deg', 'deg', 'm/s'],
    )
    session = load_vbo(path)
    assert session.source_speed_unit == 'ms'
    assert session.all_points[0].speed == pytest.approx(36.0)


def test_load_vbo_bare_velocity_defaults_kmh(tmp_path):
    # Bare 'velocity' with no unit tag is treated as knots internally, but
    # 'knots' isn't a selectable display unit, so source_speed_unit falls
    # back to 'kmh' (a known limitation — see units.py plan notes).
    path = _write_vbo(
        tmp_path,
        ['time', 'latitude north', 'longitude east', 'velocity'],
        '120000.00 5130.0000 00400.0000 100.0',
    )
    session = load_vbo(path)
    assert session.source_speed_unit == 'kmh'


# ── Exception types ────────────────────────────────────────────────────────────

def test_load_vbo_missing_header_section_raises_typed(tmp_path):
    path = tmp_path / "no_header.vbo"
    path.write_text('[comments]\nFile created on 15/06/2024 at 14:32:00 by VBOX Tools\n\n[data]\n1.0\n')
    with pytest.raises(MissingHeaderError):
        load_vbo(str(path))


def test_load_vbo_missing_required_channels_raises_typed(tmp_path):
    # Header present but no time/lat/lon channels
    path = _write_vbo(
        tmp_path,
        ['velocity kmh'],
        '100.0',
    )
    with pytest.raises(MissingHeaderError):
        load_vbo(path)


def test_load_vbo_missing_data_section_raises_typed(tmp_path):
    lines = ['[header]', 'time', 'latitude north', 'longitude east', 'velocity kmh', '',
             '[comments]', 'File created on 15/06/2024 at 14:32:00 by VBOX Tools', '']
    path = tmp_path / "no_data.vbo"
    path.write_text('\n'.join(lines), encoding='utf-8')
    with pytest.raises(NoDataRowsError):
        load_vbo(str(path))


def test_load_vbo_no_valid_data_rows_raises_typed(tmp_path):
    # Data section present, but the only row is shorter than the required columns
    path = _write_vbo(
        tmp_path,
        ['time', 'latitude north', 'longitude east', 'velocity kmh'],
        '120000.00',   # missing lat/lon columns -> row skipped
    )
    with pytest.raises(NoDataRowsError):
        load_vbo(path)


# ── _parse_hhmmss bounds checking ──────────────────────────────────────────────

def test_parse_hhmmss_valid():
    h, m, s = _parse_hhmmss(123456.5)
    assert (h, m, s) == (12, 34, 56.5)


def test_parse_hhmmss_invalid_minutes_raises_typed():
    # Minutes field of 60 or more is impossible
    with pytest.raises(CSVParseError):
        _parse_hhmmss(126000.0)  # h=12, m=60 -> invalid


def test_parse_hhmmss_invalid_hour_raises_typed():
    with pytest.raises(CSVParseError):
        _parse_hhmmss(250000.0)  # h=25 -> invalid


def test_parse_hhmmss_seconds_rounds_to_60_raises_typed():
    # A value whose seconds component rounds up to exactly 60.0
    with pytest.raises(CSVParseError):
        _parse_hhmmss(120059.9999996)


def test_load_vbo_garbled_time_row_skipped_not_crashed(tmp_path):
    # First data row has a garbled/out-of-range HHMMSS value; second is valid.
    # Loading must skip the bad row rather than raising an uncaught ValueError.
    lines = [
        '[header]', 'time', 'latitude north', 'longitude east', 'velocity kmh', '',
        '[comments]', 'File created on 15/06/2024 at 14:32:00 by VBOX Tools', '',
        '[data]',
        '999999.00 5130.0000 00400.0000 100.0',
        '120000.00 5130.0000 00400.0000 110.0',
    ]
    path = tmp_path / "garbled_time.vbo"
    path.write_text('\n'.join(lines), encoding='utf-8')
    session = load_vbo(str(path))
    assert len(session.all_points) == 1
    assert session.all_points[0].speed == pytest.approx(110.0)
