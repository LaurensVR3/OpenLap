"""
rb_downloader.py — Data source abstraction and RaceBox web downloader
======================================================================
Defines a DataSource ABC so future sources (AiM, MoTeC, RaceChronoPro,
Harry's LapTimer, etc.) can be plugged in with minimal changes.

RaceBoxSource uses Playwright for browser-based login (saves auth state)
and requests for bulk CSV downloads.

Install:
    pip install playwright requests
    playwright install chromium
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional


# ── Abstract base ─────────────────────────────────────────────────────────────

@dataclass
class RemoteSession:
    """Metadata for one session available from a remote source."""
    source_id:   str            # unique ID on the remote system
    date:        datetime
    track:       str
    session_type: str
    laps:        int
    best_lap:    Optional[float]  # seconds
    raw_meta:    dict = field(default_factory=dict)

    def label(self) -> str:
        d = self.date.strftime('%Y-%m-%d %H:%M')
        best = f"  best {self.best_lap:.3f}s" if self.best_lap else ''
        return f"{d}  {self.track}  ({self.laps} laps{best})"


class DataSource(ABC):
    """
    Abstract data source.  Subclass this to add new telemetry providers.

    A DataSource can:
      - list_sessions()  → [RemoteSession, ...]
      - download(session, dest_dir, progress_cb)  → local CSV path
      - requires_auth → bool (whether login is needed before listing)
    """

    name:        str = "Unknown"
    description: str = ""

    @property
    def requires_auth(self) -> bool:
        return True

    @abstractmethod
    def authenticate(self,
                     log_cb: Optional[Callable[[str], None]] = None) -> bool:
        """
        Perform authentication.  May open a browser window on first run.
        Returns True on success.
        """

    @abstractmethod
    def list_sessions(self,
                      log_cb: Optional[Callable[[str], None]] = None
                      ) -> List[RemoteSession]:
        """Fetch list of available sessions from the remote source."""

    @abstractmethod
    def download(self,
                 session:     RemoteSession,
                 dest_dir:    str,
                 progress_cb: Optional[Callable[[float, str], None]] = None,
                 log_cb:      Optional[Callable[[str], None]] = None,
                 ) -> Optional[str]:
        """
        Download session data to dest_dir.
        Returns local file path on success, None on failure.
        """

    def dest_path(self, session: RemoteSession, dest_dir: str,
                  ext: str = '.csv') -> str:
        return os.path.join(dest_dir, f"{session.source_id}{ext}")

    def already_downloaded(self, session: RemoteSession,
                           dest_dir: str) -> bool:
        return os.path.exists(self.dest_path(session, dest_dir))


# ── RaceBox source ────────────────────────────────────────────────────────────

RACEBOX_BASE     = "https://www.racebox.pro"
RACEBOX_SESSIONS = f"{RACEBOX_BASE}/webapp/sessions?empty=0&type=track"
RACEBOX_EXPORT   = f"{RACEBOX_BASE}/webapp/session/{{sid}}/export/csv"

EXPORT_SETTINGS = {
    "csvFormat":                   "custom",
    "timeFormat":                  "utc",
    "speedFormat":                 "kph",
    "altitudeFormat":              "m",
    "newLineFormat":               "crlf",
    "extendedHeader":              "1",
    "addLapSectorEventsInHeader":  "1",
    "includeEntryExit":            "1",
}


class RaceBoxSource(DataSource):
    """
    Downloads sessions from racebox.pro using Playwright for auth
    and requests for CSV export.

    On first run it opens a Chromium window — the user logs in manually,
    then auth state is saved to auth_file for all subsequent runs.
    """

    name        = "RaceBox"
    description = "racebox.pro — imports via browser login"

    def __init__(self, auth_file: Optional[str] = None,
                 data_dir: str = "racebox_data"):
        if auth_file is None:
            app_data = Path(os.environ.get("APPDATA", Path.home())) / "OpenLap"
            app_data.mkdir(parents=True, exist_ok=True)
            auth_file = str(app_data / "racebox_auth.json")
        self.auth_file = auth_file
        self.data_dir  = data_dir
        self._cookies: Optional[str] = None   # cached cookie header

    @property
    def requires_auth(self) -> bool:
        return True

    def is_authenticated(self) -> bool:
        return os.path.exists(self.auth_file)

    def authenticate(self,
                     log_cb: Optional[Callable[[str], None]] = None) -> bool:
        """
        If auth file exists, validate it silently (headless).
        Otherwise open a visible browser for manual login.
        """
        def log(msg):
            if log_cb: log_cb(msg)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
            return False

        with sync_playwright() as pw:
            if self.is_authenticated():
                log("Validating saved login…")
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(storage_state=self.auth_file)
                page = ctx.new_page()
                try:
                    page.goto(RACEBOX_SESSIONS, timeout=15000)
                    page.wait_for_selector("a.row", timeout=8000)
                    self._cache_cookies(ctx)
                    log("✓ Login valid")
                    browser.close()
                    return True
                except Exception:
                    log("Saved login expired — opening browser for re-login…")
                    browser.close()
                    os.remove(self.auth_file)

            # Manual login
            log("Opening browser — please log in to racebox.pro…")
            browser = pw.chromium.launch(headless=False)
            ctx     = browser.new_context()
            page    = ctx.new_page()
            page.goto(RACEBOX_SESSIONS)
            try:
                page.wait_for_selector("a.row", timeout=120_000)
                ctx.storage_state(path=self.auth_file)
                self._cache_cookies(ctx)
                log("✓ Login saved")
                browser.close()
                return True
            except Exception as e:
                log(f"Login timeout or error: {e}")
                browser.close()
                return False

    def list_sessions(self,
                      log_cb: Optional[Callable[[str], None]] = None
                      ) -> List[RemoteSession]:
        def log(msg):
            if log_cb: log_cb(msg)

        if not self.is_authenticated():
            if not self.authenticate(log_cb):
                return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("ERROR: playwright not installed")
            return []

        sessions: List[RemoteSession] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx     = browser.new_context(storage_state=self.auth_file)
            page    = ctx.new_page()

            log("Fetching session list…")
            page.goto(RACEBOX_SESSIONS)
            page.wait_for_selector("a.row", timeout=20_000)

            # Scroll to load lazy-loaded rows
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.4)

            # Extract session IDs and metadata
            rows = page.query_selector_all("a.row")
            log(f"Found {len(rows)} sessions on page")

            for row in rows:
                href = row.get_attribute("href") or ""
                sid  = href.split("/session/")[-1].split("?")[0].strip()
                if not sid:
                    continue

                # Try to extract date and track from the row text
                text = row.inner_text()
                lines = [l.strip() for l in text.splitlines() if l.strip()]

                # Date is typically first line or contains a date pattern
                date_str = ""
                track    = ""
                laps     = 0
                best     = None

                import re
                for line in lines:
                    if re.search(r'\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2}', line):
                        date_str = line
                    elif re.search(r'lap', line, re.I):
                        m = re.search(r'(\d+)\s*lap', line, re.I)
                        if m:
                            laps = int(m.group(1))
                    elif line and not date_str:
                        track = line

                # Parse date
                dt = datetime.now(tz=timezone.utc)
                for fmt in ('%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M', '%d-%m-%Y'):
                    try:
                        dt = datetime.strptime(date_str[:16], fmt)
                        dt = dt.replace(tzinfo=timezone.utc)
                        break
                    except Exception:
                        pass

                sessions.append(RemoteSession(
                    source_id    = sid,
                    date         = dt,
                    track        = track or "Unknown",
                    session_type = "Track",
                    laps         = laps,
                    best_lap     = best,
                    raw_meta     = {"text": text},
                ))

            self._cache_cookies(ctx)
            browser.close()

        sessions.sort(key=lambda s: s.date, reverse=True)
        return sessions

    def download(self,
                 session:     RemoteSession,
                 dest_dir:    str,
                 progress_cb: Optional[Callable[[float, str], None]] = None,
                 log_cb:      Optional[Callable[[str], None]] = None,
                 ) -> Optional[str]:
        def log(msg):
            if log_cb: log_cb(msg)

        import requests as _req

        dest = self.dest_path(session, dest_dir)
        if os.path.exists(dest):
            log(f"Already downloaded: {session.source_id}")
            return dest

        os.makedirs(dest_dir, exist_ok=True)

        if not self._cookies:
            if not self.authenticate(log_cb):
                return None

        url = RACEBOX_EXPORT.format(sid=session.source_id)
        hdrs = {
            "Cookie":     self._cookies,
            "User-Agent": "Mozilla/5.0",
            "Origin":     RACEBOX_BASE,
        }

        try:
            resp = _req.post(url, headers=hdrs, data=EXPORT_SETTINGS, timeout=30)
            if "text/csv" in resp.headers.get("Content-Type", ""):
                with open(dest, "wb") as f:
                    f.write(resp.content)
                log(f"✓ {session.source_id}.csv")
                return dest
            else:
                log(f"✗ {session.source_id} — unexpected response ({resp.status_code})")
                return None
        except Exception as e:
            log(f"✗ {session.source_id} — {e}")
            return None

    def download_all(self,
                     sessions:    List[RemoteSession],
                     dest_dir:    str,
                     progress_cb: Optional[Callable[[float, str], None]] = None,
                     log_cb:      Optional[Callable[[str], None]] = None,
                     skip_existing: bool = True,
                     ) -> List[str]:
        """Download multiple sessions, returning list of local paths."""
        results = []
        total   = len(sessions)
        for i, sess in enumerate(sessions):
            if skip_existing and self.already_downloaded(sess, dest_dir):
                if log_cb: log_cb(f"Skip {sess.source_id} (exists)")
                results.append(self.dest_path(sess, dest_dir))
                if progress_cb: progress_cb((i+1)/total*100, f"{i+1}/{total}")
                continue
            path = self.download(sess, dest_dir, progress_cb, log_cb)
            if path:
                results.append(path)
            if progress_cb:
                progress_cb((i+1)/total*100, f"{i+1}/{total}")
            time.sleep(0.4)
        return results

    def _cache_cookies(self, ctx) -> None:
        """Cache cookies as a header string for requests."""
        cookies = ctx.cookies()
        self._cookies = "; ".join(f"{c['name']}={c['value']}" for c in cookies)


# ── Registry ──────────────────────────────────────────────────────────────────

# Add new sources here — they'll appear automatically in the GUI dropdown
SOURCES: dict[str, type] = {
    "RaceBox": RaceBoxSource,
    # "AiM":    AiMSource,      # future
    # "MoTeC":  MoTeCSource,    # future
}


def get_source(name: str, **kwargs) -> DataSource:
    cls = SOURCES.get(name)
    if cls is None:
        raise ValueError(f"Unknown data source: {name!r}")
    return cls(**kwargs)
