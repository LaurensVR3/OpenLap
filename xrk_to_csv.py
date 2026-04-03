#!/usr/bin/env python3
"""
Convert an AIM .xrk (or .xrz / .drk) file to a single CSV.

Each column is a channel; the row index is time in seconds from
the start of the session.  Channels with different sample rates are
merged on an outer join so every recorded timestamp is present and
cells that have no sample at that instant are left empty (NaN).

Requirements
------------
  Python 3.8+
  pandas          pip install pandas

DLL
---
  The script will automatically download the AIM MatLabXRK DLL from:
    https://www.aim-sportline.com/aim-software-betas/DLL/TestMatLabXRK.zip
  and place it next to this script on first run.  You can also supply
  a path manually with --dll.

Usage
-----
  python xrk_to_csv.py  my_session.xrk
  python xrk_to_csv.py  my_session.xrk  output.csv
  python xrk_to_csv.py  my_session.xrk  --dll path/to/MatLabXRK-2024-64-ReleaseU.dll
"""

import argparse
import glob
import io
import os
import sys
import urllib.request
import zipfile
from ctypes import (
    CDLL, Structure, POINTER,
    c_char_p, c_double, c_int,
    byref, cdll,
)

# ---------------------------------------------------------------------------
# DLL discovery
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


DLL_ZIP_URL = "https://www.aim-sportline.com/aim-software-betas/DLL/TestMatLabXRK.zip"


def _install_dll_from_zip(data: bytes) -> str:
    """Extract all DLLs from zip bytes to SCRIPT_DIR; return path of the MatLabXRK DLL.

    All DLLs in the zip are extracted because the MatLabXRK DLL typically depends on
    libiconv-2.dll and libintl-8.dll shipped alongside it.  Files that already exist
    are skipped to avoid permission errors if a DLL is currently loaded by the process.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        all_entries = [n for n in zf.namelist() if n.lower().endswith(".dll")]

        # --- 1. Extract ALL DLLs (dependencies) ---
        for entry in all_entries:
            local_name = os.path.basename(entry)
            if not local_name:
                continue
            local_path = os.path.join(SCRIPT_DIR, local_name)
            if os.path.isfile(local_path):
                continue   # already present or locked — leave as-is
            try:
                with zf.open(entry) as src, open(local_path, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass       # locked by another process — skip

        # --- 2. Identify the MatLabXRK DLL by basename only ---
        def _is_aim(name: str) -> bool:
            base = os.path.basename(name).lower()
            return "matlabxrk" in base

        aim_entries = [n for n in all_entries if _is_aim(n) and "64" in os.path.basename(n).lower()]
        if not aim_entries:
            aim_entries = [n for n in all_entries if _is_aim(n)]
        if not aim_entries:
            names = [os.path.basename(n) for n in all_entries]
            sys.exit(f"ERROR: No MatLabXRK DLL found in the downloaded zip.\n"
                     f"DLLs found: {names}")

        aim_local = os.path.join(SCRIPT_DIR, os.path.basename(aim_entries[0]))

    print(f"DLL saved to: {aim_local}\n")
    return aim_local


def _download_dll_urllib() -> bytes | None:
    """Try a direct download with browser-like headers. Returns zip bytes or None."""
    req = urllib.request.Request(
        DLL_ZIP_URL,
        headers={
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.aim-sportline.com/",
            "Accept": "application/zip,application/octet-stream,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception:
        pass
    return None


def _download_dll_playwright() -> str:
    """Open a real browser to download the DLL zip, extract it, and return the DLL path.

    Uses Playwright (headless=False) so the AIM server accepts the request.
    If the direct URL redirects to a page instead of downloading, the browser
    stays open and the user can navigate to the download manually — the first
    download that starts is captured automatically.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit(
            "ERROR: Direct download returned 403 and playwright is not installed.\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            f"Or download manually from {DLL_ZIP_URL} and place the DLL next to xrk_to_csv.py."
        )

    print("Opening browser to download AIM MatLabXRK DLL…")
    print("If a page loads instead of downloading, click the download link yourself.")
    print("The browser will close automatically once the download starts.\n")

    tmp_zip = os.path.join(SCRIPT_DIR, "_matlabxrk_download.zip")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        try:
            with page.expect_download(timeout=120_000) as dl_info:
                try:
                    page.goto(DLL_ZIP_URL, timeout=15_000,
                              wait_until="commit")
                except PWTimeout:
                    pass  # page may not fully load for a direct ZIP — that's fine
        except PWTimeout:
            browser.close()
            sys.exit(
                "ERROR: No download started within 120 s.\n"
                f"Please download manually from {DLL_ZIP_URL}"
            )

        dl_info.value.save_as(tmp_zip)
        browser.close()

    try:
        return _install_dll_from_zip(open(tmp_zip, "rb").read())
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass


def _find_dll() -> str:
    """Return the path to the MatLabXRK DLL, downloading it if necessary."""
    candidates = sorted(
        glob.glob(os.path.join(SCRIPT_DIR, "MatLabXRK*.dll")),
        reverse=True,
    )
    if candidates:
        return candidates[0]

    print(f"MatLabXRK DLL not found.  Trying direct download from:\n  {DLL_ZIP_URL}")

    # 1. Try a plain HTTP download with browser-like headers
    data = _download_dll_urllib()
    if data:
        return _install_dll_from_zip(data)

    # 2. Direct download blocked (likely 403) — fall back to a real browser via Playwright
    print("Direct download failed (server returned 403 or connection error).")
    return _download_dll_playwright()


# ---------------------------------------------------------------------------
# DLL loader
# ---------------------------------------------------------------------------

class _TimeStruct(Structure):
    _fields_ = [
        ("tm_sec",   c_int),
        ("tm_min",   c_int),
        ("tm_hour",  c_int),
        ("tm_mday",  c_int),
        ("tm_mon",   c_int),
        ("tm_year",  c_int),
        ("tm_wday",  c_int),
        ("tm_yday",  c_int),
        ("tm_isdst", c_int),
    ]


def _load_dll(path: str) -> CDLL:
    if not os.path.isfile(path):
        sys.exit(f"ERROR: DLL not found at {path!r}.")

    dll = cdll.LoadLibrary(path)

    # Override return types for every function that returns something other
    # than int (ctypes defaults to c_int).
    for fn in (
        "get_library_date", "get_library_time",
        "get_vehicle_name", "get_track_name",
        "get_racer_name", "get_championship_name", "get_venue_type_name",
        "get_channel_name", "get_channel_units",
        "get_GPS_channel_name", "get_GPS_channel_units",
        "get_GPS_raw_channel_name", "get_GPS_raw_channel_units",
    ):
        try:
            getattr(dll, fn).restype = c_char_p
        except AttributeError:
            pass  # function absent in this DLL version — skip

    try:
        dll.get_date_and_time.restype = POINTER(_TimeStruct)
    except AttributeError:
        pass

    return dll


# ---------------------------------------------------------------------------
# Core reading helpers
# ---------------------------------------------------------------------------

def _build_lap_series(dll, idxf: int, times: list) -> "pd.Series | None":
    """
    Build a 'Lap' integer Series aligned to *times* (seconds from session start).

    Lap 0 = outlap (before lap 1 start).
    Lap N = the N-th timed lap (1-based).
    Returns None if the DLL doesn't expose lap info.
    """
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        n_laps = dll.get_laps_count(idxf)
    except AttributeError:
        return None

    if n_laps <= 0:
        return None

    # Collect (start_s, lap_number) boundaries
    boundaries: list[tuple[float, int]] = []
    for i in range(n_laps):
        start_c    = c_double(0.0)
        dur_c      = c_double(0.0)
        try:
            dll.get_lap_info(idxf, i, byref(start_c), byref(dur_c))
        except AttributeError:
            return None
        boundaries.append((start_c.value, i))   # i==0 → outlap

    boundaries.sort(key=lambda x: x[0])

    # Assign each timestamp the lap number of the most recent boundary
    import bisect
    starts = [b[0] for b in boundaries]
    lap_numbers = [b[1] for b in boundaries]

    values = []
    for t in times:
        idx = bisect.bisect_right(starts, t) - 1
        values.append(lap_numbers[idx] if idx >= 0 else 0)

    s = pd.Series(values, index=times, name="Lap", dtype=int)
    print(f"  Lap: {len(s):,} samples  ({n_laps} laps from DLL)")
    return s


def _read_channel(dll, idxf: int, idxc: int,
                  fn_count, fn_samples) -> tuple[list, list] | tuple[None, None]:
    """
    Read all samples for one channel.

    Returns (times_seconds, values) or (None, None) on failure.
    The DLL returns times in milliseconds for full-session calls.
    """
    n = fn_count(idxf, idxc)
    if n <= 0:
        return None, None

    ptimes  = (c_double * n)()
    pvalues = (c_double * n)()

    ok = fn_samples(idxf, idxc, byref(ptimes), byref(pvalues), n)
    if ok <= 0:
        return None, None

    # Full-session timestamps come back in milliseconds -> convert to seconds
    times  = [round(ptimes[i]  / 1000.0, 6) for i in range(n)]
    values = [pvalues[i] for i in range(n)]
    return times, values


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def xrk_to_csv(xrk_path: str, csv_path: str, dll_path: str) -> None:
    try:
        import pandas as pd
    except ImportError:
        sys.exit("ERROR: pandas is required.  Install with:  pip install pandas")

    dll = _load_dll(dll_path)

    abs_path = os.path.abspath(xrk_path).encode()
    idxf = dll.open_file(c_char_p(abs_path))
    if idxf <= 0:
        sys.exit(f"ERROR: Could not open {xrk_path!r}  (DLL returned {idxf})")

    # Read session date from DLL before entering the try block so we have it for the header
    session_date_str = ""
    try:
        ts_ptr = dll.get_date_and_time(idxf)
        if ts_ptr:
            ts = ts_ptr.contents
            # C tm struct: tm_year = years since 1900, tm_mon = 0-based
            from datetime import datetime as _dt
            dt = _dt(ts.tm_year + 1900, ts.tm_mon + 1, ts.tm_mday,
                     ts.tm_hour, ts.tm_min, ts.tm_sec)
            session_date_str = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        pass

    try:
        print(f"File    : {xrk_path}")
        print(f"Vehicle : {dll.get_vehicle_name(idxf).decode()}")
        print(f"Track   : {dll.get_track_name(idxf).decode()}")
        print(f"Driver  : {dll.get_racer_name(idxf).decode()}")
        if session_date_str:
            print(f"Date    : {session_date_str}")

        # Channel groups: (label, count_fn, name_fn, units_fn, count_sample_fn, sample_fn)
        groups = [
            (
                "regular",
                dll.get_channels_count,
                dll.get_channel_name,
                dll.get_channel_units,
                dll.get_channel_samples_count,
                dll.get_channel_samples,
            ),
            (
                "GPS",
                dll.get_GPS_channels_count,
                dll.get_GPS_channel_name,
                dll.get_GPS_channel_units,
                dll.get_GPS_channel_samples_count,
                dll.get_GPS_channel_samples,
            ),
            (
                "GPS raw",
                dll.get_GPS_raw_channels_count,
                dll.get_GPS_raw_channel_name,
                dll.get_GPS_raw_channel_units,
                dll.get_GPS_raw_channel_samples_count,
                dll.get_GPS_raw_channel_samples,
            ),
        ]

        series_list: list[pd.Series] = []
        seen_names: set[str] = set()

        for label, fn_count, fn_name, fn_units, fn_n_samples, fn_samples in groups:
            n = fn_count(idxf)
            print(f"\n{label.capitalize()} channels: {n}")
            for i in range(n):
                raw_name  = fn_name(idxf, i).decode("utf-8")
                raw_units = fn_units(idxf, i).decode("utf-8")
                col = f"{raw_name} [{raw_units}]" if raw_units else raw_name

                # Deduplicate column names (shouldn't happen, but be safe)
                if col in seen_names:
                    col = f"{col} ({label})"
                seen_names.add(col)

                times, values = _read_channel(dll, idxf, i, fn_n_samples, fn_samples)
                if times is None:
                    print(f"  SKIP {col!r} (no samples)")
                    continue

                series_list.append(pd.Series(values, index=times, name=col))
                print(f"  {col}: {len(values):,} samples")

        # Build lap-number column from DLL lap-boundary data
        # Use the union of all timestamps so every row gets a lap number
        print("\nBuilding Lap column from DLL lap info…")
        all_times = sorted({t for s in series_list for t in s.index})
        lap_series = _build_lap_series(dll, idxf, all_times)
        if lap_series is not None:
            series_list.insert(0, lap_series)

    finally:
        dll.close_file_i(idxf)

    if not series_list:
        sys.exit("ERROR: No channel data found in the file.")

    print(f"\nMerging {len(series_list)} channels on a common time axis …")
    df = pd.concat(series_list, axis=1)
    df.index.name = "Time (s)"
    df.sort_index(inplace=True)

    print(f"Writing {len(df):,} rows × {len(df.columns)} columns → {csv_path}")
    with open(csv_path, 'w', newline='', encoding='utf-8') as fout:
        if session_date_str:
            fout.write(f"# Session-Date: {session_date_str}\n")
        df.to_csv(fout)
    print("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert an AIM .xrk/.xrz/.drk file to a single CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("xrk",       help="Input .xrk file")
    parser.add_argument("csv",  nargs="?",
                        help="Output .csv file (default: same name/location as input)")
    parser.add_argument("--dll", default=_find_dll(),
                        help="Path to MatLabXRK DLL (auto-detected if omitted)")
    args = parser.parse_args()

    csv_out = args.csv or os.path.splitext(os.path.abspath(args.xrk))[0] + ".csv"
    xrk_to_csv(args.xrk, csv_out, args.dll)
