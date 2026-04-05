# app_shell.py — Thin App shell: sidebar navigation, shared state, queue dispatcher

import queue
import threading
import tkinter as tk
from tkinter import ttk
from multiprocessing import cpu_count

from design_tokens import BG, SIDEBAR, CARD, CARD2, BORDER, ACC, ACC2, OK, WARN, TEXT, TEXT2, TEXT3, font
from widgets import Divider
from video_renderer import detect_encoder
from app_config import AppConfig

# Page imports
from page_data     import DataPage
from page_export   import ExportPage
from page_settings import SettingsPage

NAV_ITEMS = [
    ("📂", "Data",     "data_page"),
    ("🎬", "Export",   "export_page"),
    ("⚙️", "Settings", "settings_page"),
]

# Which page handles each queue message type
QUEUE_OWNER = {
    # Data page
    'data_scanned':          'data_page',
    'data_scan_error':       'data_page',
    'data_scan_progress':        'data_page',
    'data_xrk_status':           'data_page',
    'data_xrk_converted':        'data_page',
    'data_xrk_convert_failed':   'data_page',
    'data_session_loaded':   'data_page',
    'data_sync_open':        'data_page',
    'data_sync_status':      'data_page',
    'data_enable_load_btn':  'data_page',
    'settings_changed':      'data_page',   # triggers rescan
    'rb_auto_done':          'data_page',   # startup auto-download result

    # Export page
    'export_log':            'export_page',
    'export_prog':           'export_page',
    'export_done':           'export_page',
    'export_preview_frame':  'export_page',

    # Settings page
    'rb_dl_auth':            'settings_page',
    'rb_dl_log':             'settings_page',
    'rb_dl_prog':            'settings_page',
    'rb_dl_done':            'settings_page',
    'encoder':               None,   # handled directly by App
}


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("OpenLap")
        self.configure(bg=BG)
        self.geometry("1280x840")
        self.minsize(960, 640)

        # ── Persistent config ─────────────────────────────────────────────────
        self.config: AppConfig = AppConfig.load()

        # ── Shared state ──────────────────────────────────────────────────────
        self.selected_items: list = []   # set by DataPage, read by ExportPage
        self.detected_enc:   str  = 'libx264'

        # Shared Tk vars (read/written by Export page)
        self.quality_crf   = tk.IntVar(value=18)
        self.gpu_encoder   = tk.StringVar(value=self.detected_enc)
        self.worker_count  = tk.IntVar(value=max(1, cpu_count() - 1))
        self.padding_secs  = tk.DoubleVar(value=5.0)

        self.q: queue.Queue = queue.Queue()

        self._active_page = 'data_page'
        self._build_layout()
        self._poll()
        threading.Thread(target=self._detect_encoder_bg, daemon=True).start()
        threading.Thread(target=self._rb_auto_download_bg, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    #  Layout
    # ─────────────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Sidebar
        self.sidebar = tk.Frame(self, bg=SIDEBAR, width=180)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)

        logo = tk.Frame(self.sidebar, bg=SIDEBAR, height=60)
        logo.pack(fill='x')
        logo.pack_propagate(False)
        tk.Label(logo, text="🏎 OpenLap", bg=SIDEBAR, fg=ACC2,
                 font=font(13, bold=True)).pack(side='left', padx=16, pady=18)

        Divider(self.sidebar).pack(fill='x', padx=12)

        self._nav_btns = {}
        for icon, label, key in NAV_ITEMS:
            btn = self._nav_btn(icon, label, key)
            btn.pack(fill='x', padx=8, pady=2)
            self._nav_btns[key] = btn

        tk.Frame(self.sidebar, bg=SIDEBAR).pack(fill='y', expand=True)

        # Content area
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(side='left', fill='both', expand=True)

        # Instantiate pages
        self.pages: dict[str, tk.Frame] = {
            'data_page':     DataPage(self.content, self),
            'export_page':   ExportPage(self.content, self),
            'settings_page': SettingsPage(self.content, self),
        }

        self.show_page('data_page')

    def _nav_btn(self, icon, label, key):
        f = tk.Frame(self.sidebar, bg=SIDEBAR, cursor='hand2')
        f.bind('<Button-1>', lambda e, k=key: self.show_page(k))
        f.bind('<Enter>',    lambda e: f.config(bg=CARD))
        f.bind('<Leave>',    lambda e: f.config(
            bg=SIDEBAR if self._active_page != key else CARD2))
        lbl = tk.Label(f, text=f"  {icon}  {label}", bg=SIDEBAR,
                       fg=TEXT2, font=font(10), anchor='w', pady=9)
        lbl.pack(fill='x', padx=4)
        lbl.bind('<Button-1>', lambda e, k=key: self.show_page(k))
        lbl.bind('<Enter>',    lambda e: (f.config(bg=CARD), lbl.config(bg=CARD)))
        lbl.bind('<Leave>',    lambda e: (
            f.config(bg=SIDEBAR if self._active_page != key else CARD2),
            lbl.config(bg=SIDEBAR if self._active_page != key else CARD2)))
        f._lbl = lbl
        return f

    def show_page(self, key):
        for page in self.pages.values():
            page.pack_forget()
        page = self.pages[key]
        page.pack(fill='both', expand=True)
        self._active_page = key
        for k, btn in self._nav_btns.items():
            active = (k == key)
            btn.config(bg=CARD2 if active else SIDEBAR)
            btn._lbl.config(bg=CARD2 if active else SIDEBAR,
                            fg=TEXT if active else TEXT2)
        # Notify page it became visible
        if hasattr(page, 'on_show'):
            page.on_show()

    # ─────────────────────────────────────────────────────────────────────────
    #  Queue dispatcher
    # ─────────────────────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                args = item[1:]

                if kind == 'encoder':
                    enc = args[0]
                    self.detected_enc = enc
                    self.gpu_encoder.set(enc)
                    self.pages['settings_page'].on_queue('encoder', enc)
                    continue

                owner_key = QUEUE_OWNER.get(kind)
                if owner_key and owner_key in self.pages:
                    self.pages[owner_key].on_queue(kind, *args)

        except queue.Empty:
            pass
        self.after(50, self._poll)

    # ─────────────────────────────────────────────────────────────────────────
    #  Encoder detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_encoder_bg(self):
        enc = detect_encoder()
        self.q.put(('encoder', enc))

    # ─────────────────────────────────────────────────────────────────────────
    #  RaceBox auto-download on startup
    # ─────────────────────────────────────────────────────────────────────────

    def _rb_auto_download_bg(self):
        """
        Check for new RaceBox sessions on startup.
        Posts rb_auto_done(n_downloaded, error_msg) to the queue.
        n_downloaded == -1  →  not authenticated (prompt to login).
        n_downloaded == -2  →  exception (error_msg has detail).
        n_downloaded >= 0   →  success (0 = already up to date).
        """
        dest = self.config.telemetry_path
        if not dest:
            return   # no folder configured; nothing to do
        try:
            from racebox_downloader import RaceBoxSource
            src = RaceBoxSource(data_dir=dest)
            if not src.is_authenticated():
                self.q.put(('rb_auto_done', -1, ''))
                return
            sessions = src.list_sessions()
            new = [s for s in sessions if not src.already_downloaded(s, dest)]
            if not new:
                self.q.put(('rb_auto_done', 0, ''))
                return
            results = src.download_all(new, dest)
            self.q.put(('rb_auto_done', len(results), ''))
            if results:
                self.q.put(('settings_changed',))   # trigger rescan
        except Exception as exc:
            self.q.put(('rb_auto_done', -2, str(exc)))

    # ─────────────────────────────────────────────────────────────────────────
    #  TTK styling
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_ttk_style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('.', background=BG, foreground=TEXT, font=font())
        s.configure('TFrame', background=BG)
        s.configure('Treeview', background=CARD, foreground=TEXT,
                    fieldbackground=CARD, rowheight=26, font=font(9))
        s.configure('Treeview.Heading', background=CARD2, foreground=TEXT2,
                    font=font(8, bold=True), relief='flat')
        s.map('Treeview', background=[('selected', ACC)])
        s.configure('TCombobox', fieldbackground=CARD2, foreground=TEXT,
                    background=CARD2, selectbackground=ACC,
                    arrowcolor=TEXT2)
        s.map('TCombobox', fieldbackground=[('readonly', CARD2)])
        s.configure('TScrollbar', background=CARD2, troughcolor=CARD,
                    arrowcolor=TEXT2, relief='flat', borderwidth=0)
        s.configure('TSpinbox', fieldbackground=CARD2, foreground=TEXT,
                    background=CARD2, insertcolor=TEXT)
        s.configure('Horizontal.TProgressbar', background=ACC,
                    troughcolor=CARD2)
