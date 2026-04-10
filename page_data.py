# page_data.py — Data Selection: session tree + inline video alignment

from __future__ import annotations
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import List, Optional

from design_tokens import BG, CARD, CARD2, BORDER, ACC, ACC2, OK, WARN, ERR, TEXT, TEXT2, TEXT3, font
from widgets import Card, Btn, Divider
from racebox_data import Session
import racebox_data
import aim_data
import gpx_data
import motec_data
from video_renderer import concat_videos, video_duration, MultiCap
from session_scanner import (scan_csvs, scan_videos, group_videos, match_sessions,
                      convert_xrk_files, scan_pending_xrk, MatchedSession,
                      VideoFile, VideoGroup)


def _load_session(path: str):
    """Load a telemetry session from a CSV, GPX, or MoTeC .ld file."""
    if motec_data.is_motec_ld(path):
        return motec_data.load_ld(path)
    if gpx_data.is_gpx(path):
        return gpx_data.load_gpx(path)
    if aim_data.is_aim_csv(path):
        return aim_data.load_csv(path)
    return racebox_data.load_csv(path)


# Status icons
ICON_SYNCED   = "✓"   # has video + offset
ICON_UNSYNCED = "≈"   # has video, no offset yet
ICON_NOVIDEO  = "✗"   # no matching video
ICON_PENDING  = "⟳"   # XRK not yet converted


class DataPage(tk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

        # Scan state
        self._sessions: List[MatchedSession] = []
        self._csv_to_match: dict = {}   # csv_path -> MatchedSession
        self._session_meta: dict = {}   # csv_path -> {track, laps, best} (from lazy load)

        # Currently selected session (for align panel)
        self._sel_csv: Optional[str] = None
        self._sel_match: Optional[MatchedSession] = None
        self._sel_session: Optional[Session] = None

        # Sync panel state
        self.sync_cap    = None
        self.sync_fps    = 30.0
        self.sync_total  = 0
        self.sync_cur    = 0.0
        self._scrubbing  = False
        self.sync_offset_var = tk.DoubleVar(value=0.0)

        self._build()

        # Keyboard bindings (on root window, only active when sync panel visible)
        self.app.bind('<m>',           lambda e: self._sync_mark())
        self.app.bind('<M>',           lambda e: self._sync_mark())
        self.app.bind('<Left>',        lambda e: self._sync_step(-1))
        self.app.bind('<Right>',       lambda e: self._sync_step(1))
        self.app.bind('<Shift-Left>',  lambda e: self._sync_step(-int(self.sync_fps)))
        self.app.bind('<Shift-Right>', lambda e: self._sync_step(int(self.sync_fps)))

    # ─────────────────────────────────────────────────────────────────────────
    #  Build UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill='x', padx=24, pady=(16, 8))
        tk.Label(toolbar, text="Data Selection", bg=BG, fg=TEXT,
                 font=font(15, bold=True)).pack(side='left')
        Btn(toolbar, "↺  Scan", command=self._scan,
            accent=True).pack(side='right')
        self.lbl_status = tk.Label(toolbar, text="No data scanned yet.",
                                   bg=BG, fg=TEXT2, font=font(9))
        self.lbl_status.pack(side='right', padx=12)

        Divider(self).pack(fill='x', padx=24, pady=(0, 8))

        # ── RaceBox banner (hidden by default, shown on auto-download result) ──
        self._rb_banner = tk.Frame(self, bg=CARD2)
        # not packed yet; revealed by _show_rb_banner

        # ── Main split: tree (left) + detail (right) ──────────────────────────
        self._main_split = tk.Frame(self, bg=BG)
        split = self._main_split
        split.pack(fill='both', expand=True, padx=24, pady=(0, 8))

        left  = tk.Frame(split, bg=BG)
        right = tk.Frame(split, bg=BG)
        left.pack(side='left', fill='both', expand=True, padx=(0, 8))
        right.pack(side='right', fill='both', padx=(8, 0), ipadx=0)
        right.config(width=380)
        right.pack_propagate(False)

        # ── Session tree ──────────────────────────────────────────────────────
        tree_card = Card(left, title="ALL SESSIONS")
        tree_card.pack(fill='both', expand=True)

        cols = ('status', 'time', 'track', 'laps', 'best')
        self.tree = ttk.Treeview(tree_card.body, columns=cols,
                                 show='tree headings', selectmode='browse',
                                 height=16)
        self.tree.column('#0',       width=0, stretch=False)   # hidden tree col
        self.tree.heading('status',  text='')
        self.tree.heading('time',    text='Session')
        self.tree.heading('track',   text='Track')
        self.tree.heading('laps',    text='Laps')
        self.tree.heading('best',    text='Best')
        self.tree.column('status',   width=28,  anchor='center', stretch=False)
        self.tree.column('time',     width=120, anchor='w')
        self.tree.column('track',    width=130, anchor='w')
        self.tree.column('laps',     width=45,  anchor='center', stretch=False)
        self.tree.column('best',     width=85,  anchor='center', stretch=False)
        vsb = ttk.Scrollbar(tree_card.body, orient='vertical',
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tree.tag_configure('day',      foreground=ACC2, font=font(9, bold=True))
        self.tree.tag_configure('synced',   foreground=OK)
        self.tree.tag_configure('unsynced', foreground=WARN)
        self.tree.tag_configure('novideo',  foreground=TEXT3)
        self.tree.tag_configure('pending',  foreground=ACC)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        self._sel_mode = tk.StringVar(value="session")

        # ── Detail panel ──────────────────────────────────────────────────────
        self._detail = tk.Frame(right, bg=BG)
        self._detail.pack(fill='both', expand=True)
        self._build_detail_empty()

    def _build_detail_empty(self):
        for w in self._detail.winfo_children():
            w.destroy()
        tk.Label(self._detail,
                 text="Select a session\nto see details and align video.",
                 bg=BG, fg=TEXT3, font=font(9), justify='center').pack(
            expand=True)

    def _build_detail_session(self, match: MatchedSession):
        for w in self._detail.winfo_children():
            w.destroy()

        # ── Pending XRK: show convert panel and return early ──────────────────
        if match.needs_conversion:
            conv_card = Card(self._detail, title="AIM MYCHRON — CONVERT")
            conv_card.pack(fill='x', pady=(0, 8))
            xrk_name = os.path.basename(match.xrk_path)
            tk.Label(conv_card.body, text=xrk_name, bg=CARD, fg=TEXT,
                     font=font(9)).pack(anchor='w', pady=(0, 6))
            tk.Label(conv_card.body,
                     text="This file has not been converted yet.\n"
                          "Click Convert to generate the CSV.\n"
                          "The AIM MatLabXRK DLL will be downloaded if needed.",
                     bg=CARD, fg=TEXT3, font=font(8), justify='left').pack(anchor='w')
            self.lbl_conv_status = tk.Label(conv_card.body, text="", bg=CARD,
                                            fg=WARN, font=font(8))
            self.lbl_conv_status.pack(anchor='w', pady=(6, 0))
            self.btn_convert = Btn(conv_card.body, "⟳  Convert to CSV",
                                   command=self._convert_session, accent=True)
            self.btn_convert.pack(anchor='w', pady=(8, 0))
            return

        # ── Info card ─────────────────────────────────────────────────────────
        info = Card(self._detail, title="SESSION INFO")
        info.pack(fill='x', pady=(0, 8))

        def info_row(label, value, col=TEXT):
            row = tk.Frame(info.body, bg=CARD)
            row.pack(fill='x', pady=1)
            tk.Label(row, text=label, bg=CARD, fg=TEXT3,
                     font=font(8), width=10, anchor='w').pack(side='left')
            tk.Label(row, text=value, bg=CARD, fg=col,
                     font=font(9)).pack(side='left')

        csv = match.csv_path
        sess = self._sel_session
        if sess:
            info_row("Source",  sess.source or "—")
            info_row("Track",   sess.track or "—")
            info_row("Config",  sess.configuration or "—")
            info_row("Date",    sess.date_utc or "—")
            laps = len(sess.timed_laps)
            best = f"{sess.best_lap_time:.3f}s" if sess.best_lap_time else "—"
            info_row("Laps",    str(laps))
            info_row("Best",    best, OK)
            # Mode row — inline CAR ●── BIKE toggle
            mode_row = tk.Frame(info.body, bg=CARD)
            mode_row.pack(fill='x', pady=1)
            tk.Label(mode_row, text="Mode", bg=CARD, fg=TEXT3,
                     font=font(8), width=10, anchor='w').pack(side='left')

            def _toggle_bike_mode(s=sess, m=match):
                import math as _math
                s.is_bike = not s.is_bike
                if s.is_bike:
                    # Compute lean for all points — prefer gyro (RaceBox),
                    # fall back to lateral G (MoTeC / AIM / GPX).
                    for pt in s.all_points:
                        pt.lean_angle = 0.0  # reset so we recompute cleanly
                    for pt in s.all_points:
                        if abs(pt.gyro_z) > 1e-6:
                            v = pt.speed / 3.6
                            w = pt.gyro_z * _math.pi / 180.0
                            pt.lean_angle = _math.degrees(_math.atan2(v * w, 9.81))
                        elif abs(pt.gforce_y) > 1e-6:
                            pt.lean_angle = _math.degrees(_math.atan(pt.gforce_y))
                else:
                    for pt in s.all_points:
                        pt.lean_angle = 0.0
                abs_path = os.path.abspath(m.csv_path)
                self.app.config.bike_overrides[abs_path] = s.is_bike
                self.app.config.overlay.is_bike = s.is_bike
                self.app.config.save()
                # Force the export tab to reload history with updated lean data
                export_page = self.app.pages.get('export_page')
                if export_page:
                    export_page._last_preview_csv = None
                self._build_detail_session(m)

            # Inline toggle: CAR ●── BIKE  or  CAR ──● BIKE
            is_bike = sess.is_bike
            car_col  = TEXT  if not is_bike else TEXT3
            bike_col = TEXT  if is_bike     else TEXT3
            pip      = "──●" if is_bike     else "●──"
            tk.Label(mode_row, text="CAR", bg=CARD, fg=car_col,
                     font=font(9, bold=not is_bike)).pack(side='left', padx=(6, 2))
            tk.Label(mode_row, text=pip, bg=CARD, fg=ACC,
                     font=font(9, mono=True)).pack(side='left')
            tk.Label(mode_row, text="BIKE", bg=CARD, fg=bike_col,
                     font=font(9, bold=is_bike)).pack(side='left', padx=(2, 0))
            Btn(mode_row, "⇄", command=_toggle_bike_mode,
                small=True).pack(side='left', padx=(6, 0))
        else:
            info_row("CSV",     os.path.basename(csv))

        # Video match status
        if match.matched and match.video_group:
            n_clips = len(match.video_group.files)
            delta   = abs(match.time_delta)
            vstat   = f"✓ {n_clips} clip(s)  Δ{delta:.0f}s"
            info_row("Video", vstat, OK)
        else:
            info_row("Video", "✗ No match", ERR)

        # Offset
        offset = self.app.config.offsets.get(os.path.abspath(csv))
        if offset is not None:
            info_row("Offset", f"{offset:.3f}s  ✓ saved", OK)
        else:
            info_row("Offset", "not set", WARN)

        # Always allow changing / assigning video
        Btn(info.body, "📂  Assign video…",
            command=lambda: self._manual_assign_video(match),
            small=True).pack(anchor='w', pady=(6, 0))

        # ── Align section (only if video matched) ─────────────────────────────
        if not match.matched:
            return

        align_card = Card(self._detail, title="ALIGN VIDEO")
        align_card.pack(fill='both', expand=True)

        top = tk.Frame(align_card.body, bg=CARD)
        top.pack(fill='x', pady=(0, 6))
        self.btn_load_prev = Btn(top, "▶  Reload Preview",
                                 command=self._load_sync_preview)
        self.btn_load_prev.pack(side='left', padx=(0, 4))
        self.lbl_sync_status = tk.Label(top,
            text="Scrub to the start line and press M to mark it.",
            bg=CARD, fg=TEXT2, font=font(8), wraplength=300, justify='left')
        self.lbl_sync_status.pack(side='left', padx=8)

        self.sync_canvas = tk.Canvas(align_card.body, bg='#060810',
                                     highlightthickness=0, height=160)
        self.sync_canvas.pack(fill='x', pady=(0, 4))
        self.sync_canvas.bind('<Configure>', self._sync_canvas_resize)

        ctrl = tk.Frame(align_card.body, bg=CARD)
        ctrl.pack(fill='x', pady=(0, 4))
        self.lbl_sync_time = tk.Label(ctrl, text="00:00.000", fg=ACC2,
                                      bg=CARD, font=font(11, bold=True, mono=True))
        self.lbl_sync_time.pack(side='left')
        self.lbl_sync_mark = tk.Label(ctrl, text="—",
                                      fg=WARN, bg=CARD, font=font(8))
        self.lbl_sync_mark.pack(side='right')

        self.sync_scrub_var = tk.IntVar(value=0)
        self.sync_scrub = tk.Scale(
            align_card.body, from_=0, to=1000, orient='horizontal',
            variable=self.sync_scrub_var, command=self._on_scrub,
            bg=CARD, fg=TEXT2, troughcolor=CARD2,
            activebackground=ACC, highlightthickness=0,
            sliderrelief='flat', showvalue=False, bd=0)
        self.sync_scrub.pack(fill='x', pady=(0, 4))

        brow = tk.Frame(align_card.body, bg=CARD)
        brow.pack(pady=4)
        for lbl, cmd in [("◀◀ -1s", lambda: self._sync_step(-1.0)),
                          ("◀ -1f",  lambda: self._sync_step(-1)),
                          ("▶ +1f",  lambda: self._sync_step(1)),
                          ("▶▶ +1s", lambda: self._sync_step(1.0))]:
            Btn(brow, lbl, command=cmd, small=True).pack(side='left', padx=2)

        Btn(brow, "🏁 MARK LAP 1  (M)",
            command=self._sync_mark, ok=True).pack(side='left', padx=8)

    # ─────────────────────────────────────────────────────────────────────────
    #  Tree population
    # ─────────────────────────────────────────────────────────────────────────

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._csv_to_match = {}

        # Group by date
        by_day: dict = {}
        for m in self._sessions:
            day = m.csv_start.strftime('%Y-%m-%d') if m.csv_start else 'Unknown'
            by_day.setdefault(day, []).append(m)

        for day in sorted(by_day.keys(), reverse=True):
            day_id = self.tree.insert(
                '', 'end', iid=f'day:{day}',
                values=('', day, '', '', ''), tags=('day',), open=True)

            for m in sorted(by_day[day],
                            key=lambda x: x.csv_start or datetime.min,
                            reverse=True):
                csv = m.csv_path
                self._csv_to_match[csv] = m

                offset = self.app.config.offsets.get(os.path.abspath(csv))
                if m.needs_conversion:
                    icon = ICON_PENDING;  tag = 'pending'
                elif m.matched and offset is not None:
                    icon = ICON_SYNCED;   tag = 'synced'
                elif m.matched:
                    icon = ICON_UNSYNCED; tag = 'unsynced'
                else:
                    icon = ICON_NOVIDEO;  tag = 'novideo'

                time_str = m.csv_start.strftime('%H:%M') if m.csv_start else '—'

                if m.needs_conversion:
                    xrk_name = os.path.splitext(os.path.basename(m.xrk_path))[0]
                    track, laps_str, best_str = f'[AIM] {xrk_name}', '—', '—'
                else:
                    track, laps_str, best_str = self._quick_meta(csv)

                self.tree.insert(
                    day_id, 'end', iid=csv, tags=(tag,),
                    values=(icon, time_str, track, laps_str, best_str))

    def _quick_meta(self, csv_path: str):
        """Fast header-only metadata read."""
        track = laps_str = best_str = '—'
        try:
            with open(csv_path, encoding='utf-8-sig', errors='ignore') as f:
                first_line = f.readline()
                if first_line.startswith('Time (s),'):
                    # AIM CSV — no metadata block; show source tag + filename
                    track = '[AIM] ' + os.path.splitext(os.path.basename(csv_path))[0]
                    return track, laps_str, best_str
                # RaceBox CSV — read key-value metadata header
                from itertools import chain
                for line in chain([first_line], f):
                    if line.startswith('Track,'):
                        track = line.strip().split(',', 1)[1]
                    elif line.startswith('Laps,'):
                        laps_str = line.strip().split(',', 1)[1]
                    elif line.startswith('Best Lap Time,'):
                        raw = line.strip().split(',', 1)[1]
                        try:
                            best_str = f"{float(raw):.3f}s"
                        except Exception:
                            best_str = raw
                    elif line.startswith('Record,'):
                        break
        except Exception:
            pass
        return track, laps_str, best_str

    # ─────────────────────────────────────────────────────────────────────────
    #  Selection
    # ─────────────────────────────────────────────────────────────────────────

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith('day:'):
            return
        match = self._csv_to_match.get(iid)
        if not match:
            return
        self._sel_csv   = iid
        self._sel_match = match

        # Release old cap
        if self.sync_cap:
            self.sync_cap.release()
            self.sync_cap = None

        # Load CSV in background to get session details
        self._sel_session = None
        self._build_detail_session(match)
        threading.Thread(target=self._load_session_bg,
                         args=(iid,), daemon=True).start()

        # Update app selection
        self._apply_selection(silent=True)

        # Auto-load video preview if this session has video
        if match.video_group:
            self._load_sync_preview()

    def _load_session_bg(self, csv_path: str):
        try:
            sess = _load_session(csv_path)
            self.app.q.put(('data_session_loaded', csv_path, sess, None))
        except Exception as e:
            self.app.q.put(('data_session_loaded', csv_path, None, str(e)))

    # ─────────────────────────────────────────────────────────────────────────
    #  XRK on-demand conversion
    # ─────────────────────────────────────────────────────────────────────────

    def _convert_session(self):
        if not self._sel_match or not self._sel_match.needs_conversion:
            return
        self.btn_convert.config(state='disabled', text='Converting…')
        self.lbl_conv_status.config(text='Starting…', fg=WARN)
        threading.Thread(
            target=self._convert_bg,
            args=(self._sel_match.xrk_path, self._sel_match.csv_path),
            daemon=True,
        ).start()

    def _convert_bg(self, xrk_path: str, csv_path: str):
        import contextlib, io as _io
        def _status(msg: str):
            self.app.q.put(('data_xrk_status', msg))
        try:
            import xrk_to_csv as _xrk
            _status('Locating AIM MatLabXRK DLL…')
            dll_path = _xrk._find_dll()
            _status(f'Converting {os.path.basename(xrk_path)}…')
            buf = _io.StringIO()
            with contextlib.redirect_stdout(buf):
                _xrk.xrk_to_csv(xrk_path, csv_path, dll_path)
            self.app.q.put(('data_xrk_converted', xrk_path, csv_path))
        except SystemExit as e:
            self.app.q.put(('data_xrk_convert_failed', str(e)))
        except Exception as e:
            self.app.q.put(('data_xrk_convert_failed', str(e)))

    def _apply_selection(self, silent=False):
        """Build app.selected_items from current tree selection + mode."""
        mode = self._sel_mode.get()
        items = []

        if mode == 'all':
            for m in self._sessions:
                if m.matched:
                    items.append({'csv': m.csv_path,
                                  'videos': m.video_group.paths,
                                  'offset': self.app.config.offsets.get(
                                      os.path.abspath(m.csv_path))})
        elif mode == 'day' and self._sel_csv:
            day = self._tree_day_of(self._sel_csv)
            for m in self._sessions:
                if not m.matched:
                    continue
                d = m.csv_start.strftime('%Y-%m-%d') if m.csv_start else 'Unknown'
                if d == day:
                    items.append({'csv': m.csv_path,
                                  'videos': m.video_group.paths,
                                  'offset': self.app.config.offsets.get(
                                      os.path.abspath(m.csv_path))})
        else:  # 'session'
            if self._sel_match and self._sel_match.matched:
                m = self._sel_match
                items.append({'csv': m.csv_path, 'videos': m.video_group.paths,
                              'offset': self.app.config.offsets.get(
                                  os.path.abspath(m.csv_path))})

        self.app.selected_items = items
        if not silent:
            n = len(items)
            self.lbl_status.config(
                text=f"{n} session(s) selected for export", fg=ACC)

    def _tree_day_of(self, csv_iid: str) -> str:
        parent = self.tree.parent(csv_iid)
        if parent.startswith('day:'):
            return parent[4:]
        return 'Unknown'

    # ─────────────────────────────────────────────────────────────────────────
    #  Scan
    # ─────────────────────────────────────────────────────────────────────────

    def on_show(self) -> None:
        """Load cached sessions immediately, then rescan in the background."""
        if not self._sessions and self.app.config.all_telemetry_paths():
            self._load_from_cache()
            self.app.after(150, self._scan)

    def _load_from_cache(self) -> None:
        """Populate the tree from the last saved scan so the UI is usable immediately."""
        from app_config import load_scan_cache
        from datetime import datetime, timezone
        data = load_scan_cache()
        if not data:
            return
        cached_paths = data.get('tel_paths') or data.get('tel_path', '')
        if cached_paths != '|'.join(self.app.config.all_telemetry_paths()):
            return   # paths changed — cache is stale

        sessions = []
        for e in data.get('sessions', []):
            csv_start = None
            if e.get('csv_start'):
                try:
                    csv_start = datetime.fromisoformat(e['csv_start'])
                    if csv_start.tzinfo is None:
                        csv_start = csv_start.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            video_paths = e.get('video_paths', [])
            if video_paths:
                files = [VideoFile(path=p, creation_time=None, duration=0.0)
                         for p in video_paths]
                vg = VideoGroup(files=files, start_time=None, end_time=None,
                                total_dur=e.get('video_total_dur', 0.0))
            else:
                vg = None

            m = MatchedSession(
                csv_path         = e['csv_path'],
                video_group      = vg,
                time_delta       = 0.0,
                csv_start        = csv_start,
                video_start      = None,
                matched          = e.get('matched', False),
                source           = e.get('source', 'RaceBox'),
                needs_conversion = e.get('needs_conversion', False),
                xrk_path         = e.get('xrk_path'),
            )
            sessions.append(m)
            # Restore any previously loaded session metadata
            meta = {k: e[k] for k in ('track', 'laps', 'best') if e.get(k)}
            if meta:
                self._session_meta[m.csv_path] = meta

        if sessions:
            self._sessions = sessions
            self._populate_tree()
            # Fill in rich metadata for rows that have it cached
            for m in sessions:
                meta = self._session_meta.get(m.csv_path, {})
                if meta:
                    try:
                        vals = list(self.tree.item(m.csv_path, 'values'))
                        if meta.get('track'):
                            vals[2] = meta['track']
                        if meta.get('laps'):
                            vals[3] = meta['laps']
                        if meta.get('best'):
                            vals[4] = meta['best']
                        self.tree.item(m.csv_path, values=vals)
                    except Exception:
                        pass
            n = len(sessions)
            self.lbl_status.config(
                text=f"Showing {n} cached session(s) — rescanning…", fg=TEXT3)

    def _scan(self):
        tel_paths = self.app.config.all_telemetry_paths()
        vid = self.app.config.video_path
        if not tel_paths:
            messagebox.showwarning("Scan",
                "Set at least one telemetry folder in Settings first.")
            return
        self.lbl_status.config(text="Scanning…", fg=WARN)
        threading.Thread(target=self._scan_bg, args=(tel_paths, vid),
                         daemon=True).start()

    def _scan_bg(self, tel_paths: list, vid_path: str):
        try:
            from datetime import datetime, timezone

            def _progress(msg: str):
                self.app.q.put(('data_scan_progress', msg))

            # XRK conversion: run on all configured paths
            for p in tel_paths:
                if os.path.isdir(p):
                    convert_xrk_files(p, progress_cb=_progress)

            _progress("Scanning telemetry files…")
            seen: set = set()
            csvs: list = []
            for p in tel_paths:
                if os.path.isdir(p):
                    for f in scan_csvs(p):
                        if f not in seen:
                            seen.add(f)
                            csvs.append(f)
            _progress(f"Found {len(csvs)} telemetry file(s).")

            if vid_path and os.path.isdir(vid_path):
                videos = scan_videos(vid_path, progress_cb=_progress)
                _progress(f"Found {len(videos)} video file(s) — matching sessions…")
                groups = group_videos(videos)
            else:
                groups = []
                _progress("Matching sessions…")

            matches = match_sessions(csvs, groups)

            # Any XRK files still without a CSV (conversion failed or first run)
            # appear as pending items so the user can see and retry them.
            pending_xrk_seen: set = set()
            for p in tel_paths:
                if not os.path.isdir(p):
                    continue
                for xrk_path, csv_path in scan_pending_xrk(p):
                    if xrk_path in pending_xrk_seen:
                        continue
                    pending_xrk_seen.add(xrk_path)
                    mtime = os.path.getmtime(xrk_path)
                    matches.append(MatchedSession(
                        csv_path         = csv_path,
                        video_group      = None,
                        time_delta       = float('inf'),
                        csv_start        = datetime.fromtimestamp(mtime, tz=timezone.utc),
                        video_start      = None,
                        matched          = False,
                        source           = 'AIM Mychron',
                        needs_conversion = True,
                        xrk_path         = xrk_path,
                    ))

            matches.sort(key=lambda m: m.csv_start.timestamp() if m.csv_start else 0)
            self.app.q.put(('data_scanned', matches))
        except Exception as e:
            import traceback
            self.app.q.put(('data_scan_error', str(e)))

    def _manual_assign_video(self, match: MatchedSession):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title="Select video file(s) for this session — select multiple for multi-clip sessions",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.MP4 *.MOV *.AVI *.MKV"),
                       ("All files", "*.*")])
        if not paths:
            return
        files = []
        total_dur = 0.0
        for path in sorted(paths):   # sort by filename so DJI_001/002/003 are in order
            dur = 0.0
            try:
                dur = video_duration(path)
            except Exception:
                pass
            files.append(VideoFile(path=path, creation_time=None, duration=dur))
            total_dur += dur
        match.video_group = VideoGroup(files=files, start_time=None,
                                       end_time=None, total_dur=total_dur)
        match.matched    = True
        match.time_delta = 0.0
        # Persist so the assignment survives a restart
        from app_config import save_scan_cache
        save_scan_cache(
            '|'.join(self.app.config.all_telemetry_paths()),
            self.app.config.video_path,
            self._sessions,
            self._session_meta,
        )
        # Rebuild detail panel with video now assigned
        self._build_detail_session(match)

    # ─────────────────────────────────────────────────────────────────────────
    #  Sync panel — video scrubbing (same logic as old SyncPage)
    # ─────────────────────────────────────────────────────────────────────────

    def _load_sync_preview(self):
        if not self._sel_match or not self._sel_match.video_group:
            messagebox.showwarning("Align", "No video matched for this session.")
            return
        videos = self._sel_match.video_group.paths
        self.btn_load_prev.config(state='disabled')
        if len(videos) == 1:
            self._open_sync_cap(videos[0])
        else:
            threading.Thread(target=self._open_multi_cap_bg,
                             args=(videos,), daemon=True).start()

    def _open_multi_cap_bg(self, files):
        try:
            cap = MultiCap(files)
            self.app.q.put(('data_sync_open_cap', cap))
        except Exception as e:
            self.app.q.put(('data_sync_status', f"✗ Cannot open clips: {e}", ERR))
            self.app.q.put(('data_enable_load_btn',))

    def _open_sync_cap(self, path_or_cap):
        import cv2
        if self.sync_cap:
            self.sync_cap.release()
        if isinstance(path_or_cap, str):
            cap = cv2.VideoCapture(path_or_cap)
            path = path_or_cap
        else:
            cap = path_or_cap
            path = '<multi-clip>'
        if not cap.isOpened():
            self.lbl_sync_status.config(text=f"✗ Cannot open: {path}", fg=ERR)
            self.btn_load_prev.config(state='normal')
            return
        self.sync_cap   = cap
        self.sync_fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.sync_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.sync_cur   = 0.0
        dur = self.sync_total / self.sync_fps
        m, s = int(dur // 60), dur % 60
        self.lbl_sync_status.config(
            text=f"✓ {self.sync_total} frames @ {self.sync_fps:.2f}fps · "
                 f"{m}m{s:.1f}s  |  ← → navigate, M mark",
            fg=OK)
        self.btn_load_prev.config(state='normal')
        self._scrubbing = True
        self.sync_scrub.config(to=max(1, self.sync_total - 1))
        self.sync_scrub_var.set(0)
        self._scrubbing = False
        self._draw_sync_frame(0)

    def _draw_sync_frame(self, fidx: int):
        import cv2
        from PIL import Image, ImageTk
        if not self.sync_cap:
            return
        fidx = max(0, min(fidx, self.sync_total - 1))
        self.sync_cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ret, frame = self.sync_cap.read()
        if not ret:
            return
        self.sync_cur = fidx / self.sync_fps

        cw = self.sync_canvas.winfo_width()  or 360
        ch = self.sync_canvas.winfo_height() or 160
        fh, fw = frame.shape[:2]
        scale  = min(cw / fw, ch / fh)
        dw, dh = max(1, int(fw * scale)), max(1, int(fh * scale))
        rgb    = cv2.cvtColor(cv2.resize(frame, (dw, dh)), cv2.COLOR_BGR2RGB)
        t      = self.sync_cur
        m, s   = int(t // 60), t % 60
        cv2.putText(rgb, f"{m:02d}:{s:06.3f}", (8, dh - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.sync_canvas.delete('all')
        self.sync_canvas.create_image(cw // 2, ch // 2, anchor='center', image=img)
        self.sync_canvas.image = img
        self.lbl_sync_time.config(text=f"{m:02d}:{s:06.3f}")
        self._scrubbing = True
        self.sync_scrub_var.set(fidx)
        self._scrubbing = False

    def _sync_canvas_resize(self, _e=None):
        if self.sync_cap:
            self._draw_sync_frame(int(self.sync_cur * self.sync_fps))

    def _on_scrub(self, val):
        if self._scrubbing:
            return
        if self.sync_cap:
            self._draw_sync_frame(int(float(val)))

    def _sync_step(self, amount):
        if not self.sync_cap:
            return
        cur = round(self.sync_cur * self.sync_fps)
        delta = round(amount * self.sync_fps) if isinstance(amount, float) else amount
        self._draw_sync_frame(cur + delta)

    def _sync_mark(self):
        if not self.sync_cap:
            return
        outlap_dur = 0.0
        sess = self._sel_session
        if sess:
            lap1 = next((l for l in sess.laps if l.lap_num == 1), None)
            if lap1 and lap1.points:
                outlap_dur = lap1.points[0].elapsed

        offset = self.sync_cur - outlap_dur
        self.sync_offset_var.set(offset)

        t = self.sync_cur
        m, s = int(t // 60), t % 60
        if outlap_dur > 0:
            self.lbl_sync_mark.config(
                text=f"✓ {m:02d}:{s:06.3f} · offset {offset:.3f}s", fg=OK)
        else:
            self.lbl_sync_mark.config(
                text=f"✓ {m:02d}:{s:06.3f} · offset {offset:.3f}s", fg=OK)

        # Persist offset immediately
        if self._sel_csv:
            abs_csv = os.path.abspath(self._sel_csv)
            self.app.config.offsets[abs_csv] = offset
            self.app.config.save()
            # Refresh tree icon and propagate to Export tab
            self._refresh_row_icon(self._sel_csv)
            self._apply_selection(silent=True)

    def _refresh_row_icon(self, csv_iid: str):
        m      = self._csv_to_match.get(csv_iid)
        offset = self.app.config.offsets.get(os.path.abspath(csv_iid))
        if m and m.matched and offset is not None:
            icon = ICON_SYNCED; tag = 'synced'
        elif m and m.matched:
            icon = ICON_UNSYNCED; tag = 'unsynced'
        else:
            icon = ICON_NOVIDEO; tag = 'novideo'
        try:
            vals = list(self.tree.item(csv_iid, 'values'))
            vals[0] = icon
            self.tree.item(csv_iid, values=vals, tags=(tag,))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Auto-detect start line
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    #  Queue handler
    # ─────────────────────────────────────────────────────────────────────────

    def on_queue(self, kind, *args):
        if kind == 'data_scanned':
            matches: List[MatchedSession] = args[0]
            # Carry over manually assigned videos — the auto-scan doesn't know
            # about them and would silently drop them.
            old_by_csv = {m.csv_path: m for m in self._sessions}
            for m in matches:
                if not m.matched:
                    old = old_by_csv.get(m.csv_path)
                    if old and old.matched and old.video_group:
                        m.video_group = old.video_group
                        m.matched     = True
                        m.time_delta  = old.time_delta
            self._sessions = matches
            self._populate_tree()
            # Re-apply any cached rich metadata into the refreshed tree rows
            for m in matches:
                meta = self._session_meta.get(m.csv_path, {})
                if meta:
                    try:
                        vals = list(self.tree.item(m.csv_path, 'values'))
                        if meta.get('track'):
                            vals[2] = meta['track']
                        if meta.get('laps'):
                            vals[3] = meta['laps']
                        if meta.get('best'):
                            vals[4] = meta['best']
                        self.tree.item(m.csv_path, values=vals)
                    except Exception:
                        pass
            total   = len(matches)
            matched = sum(1 for m in matches if m.matched)
            synced  = sum(1 for m in matches
                          if m.matched and
                          self.app.config.offsets.get(os.path.abspath(m.csv_path)) is not None)
            by_src: dict = {}
            for m in matches:
                by_src[m.source] = by_src.get(m.source, 0) + 1
            src_str = '  ·  '.join(f"{v} {k}" for k, v in sorted(by_src.items()))
            if total == 0:
                status = "No sessions found — check your Telemetry Folder in Settings."
                col    = WARN
            else:
                status = f"{src_str}  ·  {matched} with video  ·  {synced} synced"
                col    = TEXT2
            self.lbl_status.config(text=status, fg=col)
            # Persist results so the next launch can show them immediately
            from app_config import save_scan_cache
            save_scan_cache(
                '|'.join(self.app.config.all_telemetry_paths()),
                self.app.config.video_path,
                matches,
                self._session_meta,
            )

        elif kind == 'data_scan_progress':
            self.lbl_status.config(text=args[0], fg=WARN)

        elif kind == 'data_scan_error':
            self.lbl_status.config(text=f"Scan error: {args[0]}", fg=ERR)

        elif kind == 'data_session_loaded':
            csv_path, sess, err = args
            if sess is not None:
                # Apply any saved bike/car override for this file
                import math as _math
                abs_path = os.path.abspath(csv_path)
                override = self.app.config.bike_overrides.get(abs_path)
                if override is not None:
                    sess.is_bike = override
                if sess.is_bike:
                    for pt in sess.all_points:
                        if pt.lean_angle == 0.0:
                            if abs(pt.gyro_z) > 1e-6:
                                v = pt.speed / 3.6
                                w = pt.gyro_z * _math.pi / 180.0
                                pt.lean_angle = _math.degrees(_math.atan2(v * w, 9.81))
                            elif abs(pt.gforce_y) > 1e-6:
                                pt.lean_angle = _math.degrees(_math.atan(pt.gforce_y))
            if csv_path == self._sel_csv:
                self._sel_session = sess
                if self._sel_match:
                    self._build_detail_session(self._sel_match)

            # Update tree row with real metadata now that the session is loaded
            if sess is not None:
                try:
                    laps_str  = str(len(sess.timed_laps)) if sess.timed_laps else '0'
                    best_str  = f"{sess.best_lap_time:.3f}s" if sess.best_lap_time else '—'
                    track_str = sess.track or os.path.splitext(os.path.basename(csv_path))[0]
                    if sess.source == 'AIM Mychron':
                        track_str = '[AIM] ' + track_str
                    vals = list(self.tree.item(csv_path, 'values'))
                    vals[2] = track_str
                    vals[3] = laps_str
                    vals[4] = best_str
                    self.tree.item(csv_path, values=vals)
                    # Cache for next launch
                    self._session_meta[csv_path] = {
                        'track': track_str, 'laps': laps_str, 'best': best_str}
                except Exception:
                    pass
            elif err:
                self.lbl_status.config(text=f"Load error: {err}", fg=ERR)

        elif kind == 'data_sync_open':
            self._open_sync_cap(args[0])

        elif kind == 'data_sync_open_cap':
            self._open_sync_cap(args[0])

        elif kind == 'data_sync_status':
            if hasattr(self, 'lbl_sync_status'):
                self.lbl_sync_status.config(text=args[0], fg=args[1])

        elif kind == 'data_enable_load_btn':
            if hasattr(self, 'btn_load_prev'):
                self.btn_load_prev.config(state='normal')

        elif kind == 'data_xrk_status':
            if hasattr(self, 'lbl_conv_status'):
                self.lbl_conv_status.config(text=args[0], fg=WARN)

        elif kind == 'data_xrk_converted':
            _xrk_path, csv_path = args
            if hasattr(self, 'lbl_conv_status'):
                self.lbl_conv_status.config(text='✓ Converted — rescanning…', fg=OK)
            # Rescan so the new CSV appears as a proper session
            self._scan()

        elif kind == 'data_xrk_convert_failed':
            msg = args[0]
            if hasattr(self, 'lbl_conv_status'):
                self.lbl_conv_status.config(text=f'✗ {msg}', fg=ERR)
            if hasattr(self, 'btn_convert'):
                self.btn_convert.config(state='normal', text='⟳  Retry Convert')

        elif kind == 'rb_auto_done':
            n, err = args[0], args[1]
            self._show_rb_banner(n, err)

        elif kind == 'rb_auto_done':
            n, err = args[0], args[1]
            self._show_rb_banner(n, err)

        elif kind == 'settings_changed':
            # Auto-rescan when paths change (only if we have at least one path)
            if self.app.config.all_telemetry_paths():
                self._scan()

    # ─────────────────────────────────────────────────────────────────────────
    #  RaceBox auto-download banner
    # ─────────────────────────────────────────────────────────────────────────

    def _show_rb_banner(self, n: int, err: str) -> None:
        """Populate and reveal the RaceBox status banner below the toolbar."""
        for w in self._rb_banner.winfo_children():
            w.destroy()

        if n == 0:
            return   # up to date — nothing to show

        if n > 0:
            bg  = CARD2
            fg  = OK
            msg = f"✓ Downloaded {n} new RaceBox session{'s' if n != 1 else ''}."
        elif n == -1:
            bg  = CARD2
            fg  = WARN
            msg = "RaceBox: not logged in — new sessions may be available."
        else:
            bg  = CARD2
            fg  = ERR
            msg = f"RaceBox download error: {err[:100]}"

        self._rb_banner.config(bg=bg)
        inner = tk.Frame(self._rb_banner, bg=bg)
        inner.pack(fill='x', padx=24, pady=6)

        tk.Label(inner, text=msg, bg=bg, fg=fg, font=font(9),
                 anchor='w').pack(side='left', fill='x', expand=True)

        if n == -1 or n == -2:
            Btn(inner, "🔐  Login to RaceBox", small=True,
                command=self._rb_banner_login).pack(side='left', padx=(8, 4))

        Btn(inner, "✕", small=True,
            command=self._dismiss_rb_banner).pack(side='left')

        # Insert banner below the divider, above the main split
        self._rb_banner.pack(fill='x', before=self._main_split)

    def _dismiss_rb_banner(self) -> None:
        self._rb_banner.pack_forget()

    def _rb_banner_login(self) -> None:
        """Switch to Settings and trigger the RaceBox login flow."""
        self.app.show_page('settings_page')
        settings = self.app.pages['settings_page']
        settings._rb_login()
