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
from racebox_data import load_csv, Session
from video_renderer import concat_videos, video_duration
from session_scanner import (scan_csvs, scan_videos, group_videos, match_sessions,
                      MatchedSession)


# Status icons
ICON_SYNCED   = "✓"   # has video + offset
ICON_UNSYNCED = "≈"   # has video, no offset yet
ICON_NOVIDEO  = "✗"   # no matching video


class DataPage(tk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

        # Scan state
        self._sessions: List[MatchedSession] = []
        self._csv_to_match: dict = {}   # csv_path -> MatchedSession

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

        # ── Main split: tree (left) + detail (right) ──────────────────────────
        split = tk.Frame(self, bg=BG)
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
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # Selection bar under tree
        sel_bar = tk.Frame(left, bg=BG)
        sel_bar.pack(fill='x', pady=(6, 0))
        tk.Label(sel_bar, text="Select:", bg=BG, fg=TEXT2,
                 font=font(9)).pack(side='left')
        self._sel_mode = tk.StringVar(value="session")
        for lbl, val in [("All", "all"), ("This Day", "day"),
                         ("This Session", "session"), ("Fastest Lap", "fastest")]:
            tk.Radiobutton(sel_bar, text=lbl, variable=self._sel_mode,
                           value=val, bg=BG, fg=TEXT2, selectcolor=CARD,
                           activebackground=BG, font=font(9)).pack(side='left', padx=4)
        Btn(sel_bar, "Apply", command=self._apply_selection,
            small=True).pack(side='left', padx=8)

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
            info_row("Track",   sess.track or "—")
            info_row("Config",  sess.configuration or "—")
            info_row("Date",    sess.date_utc or "—")
            laps = len(sess.timed_laps)
            best = f"{sess.best_lap_time:.3f}s" if sess.best_lap_time else "—"
            info_row("Laps",    str(laps))
            info_row("Best",    best, OK)
            mode  = "🏍 Bike" if sess.is_bike else "🏎 Car"
            info_row("Mode",    mode)
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

        # ── Align section (only if video matched) ─────────────────────────────
        if not match.matched:
            tk.Label(self._detail,
                     text="No video matched for this session.\nCheck your video folder in Settings.",
                     bg=BG, fg=TEXT3, font=font(8), justify='center').pack(
                pady=12)
            return

        align_card = Card(self._detail, title="ALIGN VIDEO")
        align_card.pack(fill='both', expand=True)

        top = tk.Frame(align_card.body, bg=CARD)
        top.pack(fill='x', pady=(0, 6))
        self.btn_load_prev = Btn(top, "▶  Load Preview",
                                 command=self._load_sync_preview, accent=True)
        self.btn_load_prev.pack(side='left')
        self.lbl_sync_status = tk.Label(top,
            text="Scrub to lap-1 start, then press M.",
            bg=CARD, fg=TEXT2, font=font(8), wraplength=260, justify='left')
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
                if m.matched and offset is not None:
                    icon = ICON_SYNCED;   tag = 'synced'
                elif m.matched:
                    icon = ICON_UNSYNCED; tag = 'unsynced'
                else:
                    icon = ICON_NOVIDEO;  tag = 'novideo'

                time_str = m.csv_start.strftime('%H:%M') if m.csv_start else '—'

                # Try to get track/laps/best from already-loaded session or quick header read
                track, laps_str, best_str = self._quick_meta(csv)

                self.tree.insert(
                    day_id, 'end', iid=csv, tags=(tag,),
                    values=(icon, time_str, track, laps_str, best_str))

    def _quick_meta(self, csv_path: str):
        """Fast header-only metadata read."""
        track = laps_str = best_str = '—'
        try:
            with open(csv_path, encoding='utf-8-sig', errors='ignore') as f:
                for line in f:
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

    def _load_session_bg(self, csv_path: str):
        try:
            sess = load_csv(csv_path)
            self.app.q.put(('data_session_loaded', csv_path, sess))
        except Exception as e:
            self.app.q.put(('data_session_loaded', csv_path, None))

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
        elif mode == 'fastest' and self._sel_match and self._sel_match.matched:
            m = self._sel_match
            items.append({'csv': m.csv_path, 'videos': m.video_group.paths,
                          'offset': self.app.config.offsets.get(
                              os.path.abspath(m.csv_path)),
                          'lap_mode': 'fastest'})
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
        """Auto-scan once on first display if paths are configured."""
        if not self._sessions and self.app.config.telemetry_path:
            self.app.after(150, self._scan)

    def _scan(self):
        tel = self.app.config.telemetry_path
        vid = self.app.config.video_path
        if not tel:
            messagebox.showwarning("Scan",
                "Set the Telemetry Folder in Settings first.")
            return
        self.lbl_status.config(text="Scanning…", fg=WARN)
        threading.Thread(target=self._scan_bg, args=(tel, vid),
                         daemon=True).start()

    def _scan_bg(self, tel_path: str, vid_path: str):
        try:
            csvs   = scan_csvs(tel_path)
            if vid_path and os.path.isdir(vid_path):
                videos = scan_videos(vid_path)
                groups = group_videos(videos)
            else:
                groups = []
            matches = match_sessions(csvs, groups)
            self.app.q.put(('data_scanned', matches))
        except Exception as e:
            import traceback
            self.app.q.put(('data_scan_error', str(e)))

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
            import tempfile as _tmp
            joined = os.path.join(_tmp.gettempdir(), '_rb_align_preview.mp4')
            self.lbl_sync_status.config(
                text=f"⏳ Joining {len(videos)} clips…", fg=WARN)
            threading.Thread(target=self._join_preview_bg,
                             args=(videos, joined), daemon=True).start()

    def _join_preview_bg(self, files, out):
        try:
            concat_videos(files, out)
            self.app.q.put(('data_sync_open', out))
        except Exception as e:
            self.app.q.put(('data_sync_status', f"✗ Join failed: {e}", ERR))
            self.app.q.put(('data_enable_load_btn',))

    def _open_sync_cap(self, path: str):
        import cv2
        if self.sync_cap:
            self.sync_cap.release()
        cap = cv2.VideoCapture(path)
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
            # Refresh tree icon
            self._refresh_row_icon(self._sel_csv)

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
    #  Queue handler
    # ─────────────────────────────────────────────────────────────────────────

    def on_queue(self, kind, *args):
        if kind == 'data_scanned':
            matches: List[MatchedSession] = args[0]
            self._sessions = matches
            self._populate_tree()
            total   = len(matches)
            matched = sum(1 for m in matches if m.matched)
            synced  = sum(1 for m in matches
                          if m.matched and
                          self.app.config.offsets.get(os.path.abspath(m.csv_path)) is not None)
            self.lbl_status.config(
                text=f"{total} sessions · {matched} with video · {synced} synced",
                fg=TEXT2)

        elif kind == 'data_scan_error':
            self.lbl_status.config(text=f"Scan error: {args[0]}", fg=ERR)

        elif kind == 'data_session_loaded':
            csv_path, sess = args
            if csv_path == self._sel_csv:
                self._sel_session = sess
                # Refresh detail panel with session info
                if self._sel_match:
                    self._build_detail_session(self._sel_match)

        elif kind == 'data_sync_open':
            self._open_sync_cap(args[0])

        elif kind == 'data_sync_status':
            if hasattr(self, 'lbl_sync_status'):
                self.lbl_sync_status.config(text=args[0], fg=args[1])

        elif kind == 'data_enable_load_btn':
            if hasattr(self, 'btn_load_prev'):
                self.btn_load_prev.config(state='normal')

        elif kind == 'settings_changed':
            # Auto-rescan when paths change (only if we have a telemetry path)
            if self.app.config.telemetry_path:
                self._scan()
