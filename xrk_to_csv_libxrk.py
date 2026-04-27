"""
xrk_to_csv_libxrk.py — cross-platform XRK→CSV converter using libxrk.

Produces output byte-identical in schema to xrk_to_csv.py (the Windows DLL
path), so aim_data.load_csv() can consume either without changes:

    # Session-Date: 2026-04-19T09:30:39Z
    Time (s),Lap,GPS Speed [m/s],GPS Latitude [deg],...
    0.000000,0,...

libxrk ships native wheels (Cython + Rust) for Windows, macOS, and Linux,
so this path is the only viable AIM reader on macOS/Linux where AIM only
distributes a Windows DLL.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# libxrk channel names differ slightly from the AIM MatLabXRK DLL's naming.
# aim_data.load_csv() resolves channels via fuzzy substring patterns that were
# tuned against DLL output, so we rename libxrk channels here to keep the
# downstream loader source-agnostic.
#
#   libxrk             →  DLL-style (what aim_data.py expects)
#   GPS_InlineAcc      →  GPS_LonAcc      (longitudinal G — gforce_x)
#   GPS_LateralAcc     →  GPS_LatAcc      (lateral G       — gforce_y)
#
# Channels with units 'rpm' that are NOT engine RPM ('Jackshaft' = rear-wheel
# tach on a kart) are emitted with no units, otherwise aim_data's loose 'rpm'
# pattern matches them in alphabetical order before the real RPM column.
_CHANNEL_RENAMES: dict[str, str] = {
    'GPS_InlineAcc':  'GPS_LonAcc',
    'GPS_LateralAcc': 'GPS_LatAcc',
}
_DROP_UNITS_FOR: set[str] = {'Jackshaft'}


def _parse_session_date(metadata: dict) -> Optional[str]:
    """Build an ISO8601 'Z' string from libxrk metadata Log Date / Log Time."""
    date_str = (metadata.get('Log Date') or '').strip()
    time_str = (metadata.get('Log Time') or '').strip()
    if not date_str:
        return None
    candidates = ['%m/%d/%Y %H:%M:%S', '%m/%d/%Y'] if time_str else ['%m/%d/%Y']
    combined = f'{date_str} {time_str}'.strip() if time_str else date_str
    for fmt in candidates:
        try:
            return datetime.strptime(combined, fmt).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            continue
    return None


def _build_lap_column(timecodes_ms, laps_table) -> list[int]:
    """Assign each timecode the lap number whose [start_time, end_time) covers it.

    libxrk laps table: columns num (int32), start_time (int64 ms), end_time (int64 ms).
    Convention matches the DLL path: lap 0 = outlap, 1+ = timed laps.
    """
    import bisect
    starts = laps_table.column('start_time').to_pylist()
    nums   = laps_table.column('num').to_pylist()
    pairs  = sorted(zip(starts, nums))
    sorted_starts = [p[0] for p in pairs]
    sorted_nums   = [p[1] for p in pairs]

    out: list[int] = []
    for tc in timecodes_ms:
        idx = bisect.bisect_right(sorted_starts, tc) - 1
        out.append(sorted_nums[idx] if idx >= 0 else 0)
    return out


def xrk_to_csv_libxrk(xrk_path: str, csv_path: str) -> None:
    """Convert an AIM .xrk/.xrz/.drk file to CSV using libxrk.

    Output format matches xrk_to_csv.xrk_to_csv() exactly so aim_data.load_csv()
    works against the result without modification.
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required.  pip install pandas")

    try:
        from libxrk import aim_xrk
    except ImportError as e:
        raise ImportError(
            "libxrk is required for AIM XRK conversion on non-Windows platforms.\n"
            "  pip install libxrk"
        ) from e

    log = aim_xrk(xrk_path)

    logger.info('File    : %s', xrk_path)
    logger.info('Vehicle : %s', log.metadata.get('Vehicle', ''))
    logger.info('Track   : %s', log.metadata.get('Venue', ''))
    logger.info('Driver  : %s', log.metadata.get('Driver', ''))

    session_date_str = _parse_session_date(log.metadata)
    if session_date_str:
        logger.info('Date    : %s', session_date_str)

    table = log.get_channels_as_table()
    if table.num_rows == 0:
        raise RuntimeError(f"No channel data found in {xrk_path!r}")
    if 'timecodes' not in table.column_names:
        raise RuntimeError(
            f"libxrk table missing 'timecodes' column "
            f"(got: {table.column_names[:5]}…)"
        )

    timecodes_ms = table.column('timecodes').to_pylist()
    lap_col = _build_lap_column(timecodes_ms, log.laps)

    df = table.drop(['timecodes']).to_pandas()

    # Build renamed columns: rename libxrk-specific names to DLL conventions,
    # then suffix with units (skipping units for known wheel-speed channels so
    # aim_data.py's loose `rpm` matcher binds to engine RPM only).
    new_cols = {}
    for field in table.schema:
        if field.name == 'timecodes':
            continue
        renamed = _CHANNEL_RENAMES.get(field.name, field.name)
        units = ''
        if field.metadata:
            raw = field.metadata.get(b'units', b'') or b''
            try:
                units = raw.decode('utf-8').strip()
            except (UnicodeDecodeError, AttributeError):
                units = ''
        if renamed in _DROP_UNITS_FOR:
            units = ''
        new_cols[field.name] = f'{renamed} [{units}]' if units else renamed
    df = df.rename(columns=new_cols)

    df.insert(0, 'Lap', lap_col)

    df.index = [round(tc / 1000.0, 6) for tc in timecodes_ms]
    df.index.name = 'Time (s)'
    df.sort_index(inplace=True)

    logger.info('Writing %d rows × %d columns → %s',
                len(df), len(df.columns), csv_path)
    with open(csv_path, 'w', newline='', encoding='utf-8') as fout:
        if session_date_str:
            fout.write(f'# Session-Date: {session_date_str}\n')
        df.to_csv(fout)
    logger.info('Done.')


if __name__ == '__main__':
    import argparse
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = argparse.ArgumentParser(
        description='Convert an AIM .xrk/.xrz/.drk file to CSV using libxrk.',
    )
    parser.add_argument('xrk', help='Input .xrk file')
    parser.add_argument('csv', nargs='?', help='Output .csv (default: alongside input)')
    args = parser.parse_args()

    csv_out = args.csv or os.path.splitext(os.path.abspath(args.xrk))[0] + '.csv'
    try:
        xrk_to_csv_libxrk(args.xrk, csv_out)
    except Exception as e:
        sys.exit(f'ERROR: {e}')
