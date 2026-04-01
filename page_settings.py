# page_settings.py — Settings: paths, RaceBox sync, encoder info

import threading
import tkinter as tk
from tkinter import ttk, filedialog

from design_tokens import BG, CARD, CARD2, BORDER, ACC, ACC2, OK, WARN, ERR, TEXT, TEXT2, TEXT3, font
from widgets import Card, Btn, Divider, Label
from racebox_downloader import RaceBoxSource


class SettingsPage(tk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._rb_source: RaceBoxSource | None = None
        self._build()

    # ─────────────────────────────────────────────────────────────────────────
    #  Build UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self):
        # Scrollable container
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        vsb    = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        inner = tk.Frame(canvas, bg=BG)
        win   = canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfig(win, width=e.width))

        p = inner  # alias for layout code below

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(p, bg=BG)
        hdr.pack(fill='x', padx=24, pady=(20, 4))
        tk.Label(hdr, text="Settings", bg=BG, fg=TEXT,
                 font=font(15, bold=True)).pack(side='left')

        # ── Telemetry Sources ─────────────────────────────────────────────────
        tk.Label(p, text="IMPORT PATHS", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', padx=24, pady=(16, 2))

        tel_card = Card(p, title="TELEMETRY FOLDER")
        tel_card.pack(fill='x', padx=24, pady=(0, 8))
        self._path_row(tel_card.body, "CSVs are read from this folder (including subfolders).",
                       self.app.config.telemetry_path,
                       self._set_telemetry_path)

        # RaceBox
        rb_card = Card(p, title="RACEBOX")
        rb_card.pack(fill='x', padx=24, pady=(0, 8))
        self.lbl_rb_status = tk.Label(rb_card.body, text="Not authenticated",
                                      bg=CARD, fg=TEXT3, font=font(9))
        self.lbl_rb_status.pack(anchor='w', pady=(0, 6))
        btn_row = tk.Frame(rb_card.body, bg=CARD)
        btn_row.pack(fill='x')
        Btn(btn_row, "🔐  Login to RaceBox",
            command=self._rb_login, accent=True).pack(side='left', padx=(0, 8))
        self.btn_rb_dl = Btn(btn_row, "⬇  Download new sessions",
                             command=self._rb_download)
        self.btn_rb_dl.pack(side='left')
        self.lbl_rb_last = tk.Label(rb_card.body, text="",
                                    bg=CARD, fg=TEXT3, font=font(8))
        self.lbl_rb_last.pack(anchor='w', pady=(6, 0))

        # MoTeC placeholder
        motec_card = Card(p, title="MOTEC")
        motec_card.pack(fill='x', padx=24, pady=(0, 8))
        tk.Label(motec_card.body, text="Coming soon", bg=CARD, fg=TEXT3,
                 font=font(9)).pack(anchor='w')

        # ── Video Sources ─────────────────────────────────────────────────────
        tk.Label(p, text="VIDEO SOURCES", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', padx=24, pady=(8, 2))

        vid_card = Card(p, title="VIDEO FOLDER")
        vid_card.pack(fill='x', padx=24, pady=(0, 8))
        self._path_row(vid_card.body, "Videos are scanned recursively from this folder.",
                       self.app.config.video_path,
                       self._set_video_path)

        # ── Export Path ───────────────────────────────────────────────────────
        tk.Label(p, text="EXPORT PATH", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', padx=24, pady=(8, 2))

        exp_card = Card(p, title="EXPORT FOLDER")
        exp_card.pack(fill='x', padx=24, pady=(0, 8))
        self._path_row(exp_card.body, "Rendered overlay videos are saved here.",
                       self.app.config.export_path,
                       self._set_export_path)

        # ── Encoder info ──────────────────────────────────────────────────────
        tk.Label(p, text="SYSTEM", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', padx=24, pady=(8, 2))

        enc_card = Card(p, title="VIDEO ENCODER")
        enc_card.pack(fill='x', padx=24, pady=(0, 24))
        self.lbl_encoder = tk.Label(enc_card.body, text="Detecting…",
                                    bg=CARD, fg=TEXT2, font=font(9))
        self.lbl_encoder.pack(anchor='w')
        tk.Label(enc_card.body,
                 text="GPU encoders (nvenc/amf/qsv) are much faster than libx264.",
                 bg=CARD, fg=TEXT3, font=font(8)).pack(anchor='w', pady=(4, 0))

        # Update encoder label if already detected
        if self.app.detected_enc:
            self._update_encoder_label(self.app.detected_enc)

        # Check RaceBox auth state
        if self.app.config.telemetry_path:
            self._init_rb_source()

    # ─────────────────────────────────────────────────────────────────────────
    #  Path row helper
    # ─────────────────────────────────────────────────────────────────────────

    def _path_row(self, parent, hint: str, current: str,
                  on_change_fn) -> tk.Entry:
        tk.Label(parent, text=hint, bg=CARD, fg=TEXT3,
                 font=font(8)).pack(anchor='w', pady=(0, 4))
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill='x')
        var = tk.StringVar(value=current)
        entry = tk.Entry(row, textvariable=var, bg=CARD2, fg=TEXT,
                         insertbackground=TEXT, relief='flat', font=font(9),
                         bd=0, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACC)
        entry.pack(side='left', fill='x', expand=True, padx=(0, 8), ipady=4)
        Btn(row, "Browse…", small=True,
            command=lambda: self._browse(var, on_change_fn)).pack(side='left')
        var.trace_add('write', lambda *_: on_change_fn(var.get()))
        return entry

    def _browse(self, var: tk.StringVar, on_change_fn) -> None:
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    # ─────────────────────────────────────────────────────────────────────────
    #  Path change handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _set_telemetry_path(self, path: str) -> None:
        self.app.config.telemetry_path = path
        self.app.config.save()
        self.app.q.put(('settings_changed',))

    def _set_video_path(self, path: str) -> None:
        self.app.config.video_path = path
        self.app.config.save()
        self.app.q.put(('settings_changed',))

    def _set_export_path(self, path: str) -> None:
        self.app.config.export_path = path
        self.app.config.save()

    # ─────────────────────────────────────────────────────────────────────────
    #  RaceBox actions
    # ─────────────────────────────────────────────────────────────────────────

    def _init_rb_source(self) -> None:
        dest = self.app.config.telemetry_path or '.'
        self._rb_source = RaceBoxSource(
            auth_file='racebox_auth.json', data_dir=dest)
        if self._rb_source.is_authenticated():
            self.lbl_rb_status.config(text="✓ Saved login found", fg=OK)

    def _rb_login(self) -> None:
        dest = self.app.config.telemetry_path
        if not dest:
            from tkinter import messagebox
            messagebox.showwarning("RaceBox",
                "Set the Telemetry Folder first so downloaded CSVs have a destination.")
            return
        self._rb_source = RaceBoxSource(
            auth_file='racebox_auth.json', data_dir=dest)
        self.lbl_rb_status.config(text="Opening browser…", fg=WARN)
        threading.Thread(
            target=lambda: self.app.q.put((
                'rb_dl_auth',
                self._rb_source.authenticate(
                    log_cb=lambda m: self.app.q.put(('rb_dl_log', m))))),
            daemon=True).start()

    def _rb_download(self) -> None:
        dest = self.app.config.telemetry_path
        if not dest:
            from tkinter import messagebox
            messagebox.showwarning("RaceBox",
                "Set the Telemetry Folder first.")
            return
        if not self._rb_source:
            self._init_rb_source()
        self.btn_rb_dl.config(state='disabled')
        self.lbl_rb_last.config(text="Fetching session list…", fg=WARN)
        threading.Thread(target=self._rb_download_bg, args=(dest,),
                         daemon=True).start()

    def _rb_download_bg(self, dest: str) -> None:
        try:
            sessions = self._rb_source.list_sessions(
                log_cb=lambda m: self.app.q.put(('rb_dl_log', m)))
            new = [s for s in sessions
                   if not self._rb_source.already_downloaded(s, dest)]
            if not new:
                self.app.q.put(('rb_dl_log', "All sessions already downloaded."))
                self.app.q.put(('rb_dl_done', 0))
                return
            self.app.q.put(('rb_dl_log', f"Downloading {len(new)} new sessions…"))
            results = self._rb_source.download_all(
                new, dest,
                progress_cb=lambda p, m: self.app.q.put(('rb_dl_prog', p, m)),
                log_cb=lambda m: self.app.q.put(('rb_dl_log', m)))
            self.app.q.put(('rb_dl_done', len(results)))
            # Trigger rescan in Data page
            self.app.q.put(('settings_changed',))
        except Exception as e:
            self.app.q.put(('rb_dl_log', f"Error: {e}"))
            self.app.q.put(('rb_dl_done', -1))

    def _update_encoder_label(self, enc: str) -> None:
        names = {
            'h264_nvenc': 'h264_nvenc  (NVIDIA GPU)',
            'h264_amf':   'h264_amf  (AMD GPU)',
            'h264_qsv':   'h264_qsv  (Intel Quick Sync)',
            'libx264':    'libx264  (CPU — no GPU encoder found)',
        }
        col  = OK if enc != 'libx264' else WARN
        self.lbl_encoder.config(text=names.get(enc, enc), fg=col)

    # ─────────────────────────────────────────────────────────────────────────
    #  Queue handler
    # ─────────────────────────────────────────────────────────────────────────

    def on_queue(self, kind, *args):
        if kind == 'rb_dl_auth':
            ok = args[0]
            self.lbl_rb_status.config(
                text="✓ Authenticated" if ok else "✗ Login failed",
                fg=OK if ok else ERR)
        elif kind == 'rb_dl_log':
            self.lbl_rb_last.config(text=args[0][:120], fg=TEXT2)
        elif kind == 'rb_dl_prog':
            pass   # could add a progress bar later
        elif kind == 'rb_dl_done':
            self.btn_rb_dl.config(state='normal')
            n = args[0]
            if n >= 0:
                self.lbl_rb_last.config(
                    text=f"✓ {n} session(s) downloaded" if n else "Already up to date",
                    fg=OK)
            else:
                self.lbl_rb_last.config(text="Download failed — see log", fg=ERR)
        elif kind == 'encoder':
            self._update_encoder_label(args[0])
