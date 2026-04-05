# page_export.py — Export: visual overlay editor + quality settings + render

from __future__ import annotations
import os
import threading
from dataclasses import asdict
from typing import List

import tkinter as tk
from tkinter import ttk, messagebox

from design_tokens import BG, CARD, CARD2, BORDER, ACC, ACC2, OK, WARN, ERR, TEXT, TEXT2, TEXT3, font
from widgets import Card, Btn, Divider, Label
from app_config import AppConfig, OverlayLayout, overlay_from_dict
from overlay_editor import OverlayEditor


class ExportPage(tk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._rendering = False
        self._build()

    # ─────────────────────────────────────────────────────────────────────────
    #  Build UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill='x', padx=24, pady=(20, 4))
        tk.Label(hdr, text="Export", bg=BG, fg=TEXT,
                 font=font(15, bold=True)).pack(side='left')
        self.lbl_selection = tk.Label(hdr, text="No sessions selected",
                                      bg=BG, fg=TEXT3, font=font(9))
        self.lbl_selection.pack(side='left', padx=(16, 0))

        # ── Two-column body ───────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True, padx=24, pady=(0, 16))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

    # ── Left: Overlay Editor ──────────────────────────────────────────────────

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        left.rowconfigure(4, weight=1)
        left.columnconfigure(0, weight=1)

        # row 0: Toggle + mode row
        ctrl = tk.Frame(left, bg=BG)
        ctrl.grid(row=0, column=0, sticky='ew', pady=(0, 6))

        self.var_show_map = tk.BooleanVar(value=self.app.config.overlay.map.visible)

        tk.Checkbutton(ctrl, text="Map", variable=self.var_show_map,
                       bg=BG, fg=TEXT, selectcolor=CARD2, activebackground=BG,
                       font=font(9), command=self._on_toggle_map).pack(side='left', padx=(0, 12))

        self.var_is_bike = tk.BooleanVar(value=self.app.config.overlay.is_bike)
        tk.Checkbutton(ctrl, text="Bike mode", variable=self.var_is_bike,
                       bg=BG, fg=TEXT2, selectcolor=CARD2, activebackground=BG,
                       font=font(9), command=self._on_toggle_bike).pack(side='left', padx=(0, 12))

        Btn(ctrl, "Load preview frame", small=True,
            command=self._load_preview).pack(side='right')

        # row 1: Preset row
        preset_row = tk.Frame(left, bg=BG)
        preset_row.grid(row=1, column=0, sticky='ew', pady=(0, 4))
        self._build_preset_row(preset_row)

        # row 2: Map style + gauge management
        style_row = tk.Frame(left, bg=BG)
        style_row.grid(row=2, column=0, sticky='ew', pady=(0, 4))
        self._build_style_row(style_row)

        # row 4: Editor canvas  (row 3 = gauge list, built inside _build_style_row)
        editor_frame = tk.Frame(left, bg=BORDER, bd=1)
        editor_frame.grid(row=4, column=0, sticky='nsew')
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)

        self.editor = OverlayEditor(
            editor_frame,
            layout=self.app.config.overlay,
            on_change=self._on_layout_change,
        )
        self.editor.grid(row=0, column=0, sticky='nsew', padx=1, pady=1)

    def _build_preset_row(self, parent) -> None:
        tk.Label(parent, text="Preset:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(0, 4))

        names = list(self.app.config.presets.keys())
        cur   = self.app.config.active_preset if self.app.config.active_preset in self.app.config.presets else ''
        display_names = names if names else ['(no presets)']

        self.var_preset = tk.StringVar(value=cur or ('(no presets)' if not names else ''))
        self._preset_cb = ttk.Combobox(parent, textvariable=self.var_preset,
                                       values=display_names, state='readonly',
                                       font=font(9), width=18)
        self._preset_cb.pack(side='left', padx=(0, 8))
        self.var_preset.trace_add('write', lambda *_: self._on_preset_select())

        Btn(parent, "Save", small=True,
            command=self._save_preset).pack(side='left', padx=(0, 4))
        Btn(parent, "Save As…", small=True,
            command=self._save_preset_as).pack(side='left', padx=(0, 4))
        Btn(parent, "Delete", small=True,
            command=self._delete_preset).pack(side='left')

    def _refresh_preset_dropdown(self) -> None:
        names = list(self.app.config.presets.keys())
        display = names if names else ['(no presets)']
        self._preset_cb.config(values=display)
        cur = self.app.config.active_preset
        self.var_preset.set(cur if cur in names else (names[0] if names else ''))

    def _on_preset_select(self) -> None:
        name = self.var_preset.get()
        if name not in self.app.config.presets:
            return
        self.app.config.active_preset = name
        self.app.config.overlay = overlay_from_dict(self.app.config.presets[name])
        self.app.config.save()
        # Sync UI controls to loaded layout (guards: may not exist yet during init)
        if hasattr(self, 'var_show_map'):
            self.var_show_map.set(self.app.config.overlay.map.visible)
        if hasattr(self, 'var_is_bike'):
            self.var_is_bike.set(self.app.config.overlay.is_bike)
        if hasattr(self, 'var_map_style'):
            self.var_map_style.set(self.app.config.overlay.map_style)
        if hasattr(self, 'var_theme'):
            self.var_theme.set(getattr(self.app.config.overlay, 'theme', 'Dark'))
        if hasattr(self, '_gauge_list_frame'):
            self._rebuild_gauge_list()
        if hasattr(self, 'editor'):
            self.editor._layout = self.app.config.overlay
            self.editor.refresh()

    def _save_preset(self) -> None:
        name = self.app.config.active_preset
        if not name:
            self._save_preset_as()
            return
        from dataclasses import asdict
        self.app.config.presets[name] = asdict(self.app.config.overlay)
        self.app.config.save()
        self._refresh_preset_dropdown()

    def _save_preset_as(self) -> None:
        from tkinter.simpledialog import askstring
        name = askstring("Save Preset", "Preset name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        from dataclasses import asdict
        self.app.config.presets[name] = asdict(self.app.config.overlay)
        self.app.config.active_preset = name
        self.app.config.save()
        self._refresh_preset_dropdown()
        self.var_preset.set(name)

    def _delete_preset(self) -> None:
        name = self.var_preset.get()
        if name not in self.app.config.presets:
            return
        if not messagebox.askyesno("Delete Preset",
                                   f"Delete preset '{name}'?", parent=self):
            return
        del self.app.config.presets[name]
        if self.app.config.active_preset == name:
            self.app.config.active_preset = ''
        self.app.config.save()
        self._refresh_preset_dropdown()

    def _build_style_row(self, parent) -> None:
        from style_registry import available_styles, default_style
        from overlay_themes import theme_names
        from tkinter import ttk

        map_styles = available_styles('map') or ['Circuit']
        cur_map    = self.app.config.overlay.map_style or default_style('map') or map_styles[0]

        tk.Label(parent, text="Map style:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(0, 4))
        self.var_map_style = tk.StringVar(value=cur_map)
        cb_map = ttk.Combobox(parent, textvariable=self.var_map_style,
                              values=map_styles, state='readonly',
                              font=font(9), width=12)
        cb_map.pack(side='left', padx=(0, 8))
        self.var_map_style.trace_add('write', lambda *_: self._on_map_style_change())

        tk.Label(parent, text="Theme:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(8, 4))
        self.var_theme = tk.StringVar(value=getattr(self.app.config.overlay, 'theme', 'Dark'))
        cb_theme = ttk.Combobox(parent, textvariable=self.var_theme,
                                values=theme_names(), state='readonly',
                                font=font(9), width=11)
        cb_theme.pack(side='left', padx=(0, 16))
        self.var_theme.trace_add('write', lambda *_: self._on_theme_change())

        Btn(parent, "+ Add gauge", small=True,
            command=self._add_gauge).pack(side='left', padx=(0, 4))

        # Gauge list panel — row 3 in left frame (below style_row at row 2)
        self._gauge_list_frame = tk.Frame(parent.master, bg=BG)
        self._gauge_list_frame.grid(row=3, column=0, sticky='ew', pady=(0, 2))
        self._rebuild_gauge_list()

    def _rebuild_gauge_list(self) -> None:
        """Rebuild the scrollable gauge row list."""
        from tkinter import ttk
        from gauge_channels import GAUGE_CHANNELS, GAUGE_STYLES_BIKE, GAUGE_STYLES_CAR

        for w in self._gauge_list_frame.winfo_children():
            w.destroy()

        is_bike    = self.app.config.overlay.is_bike
        avail_styles = GAUGE_STYLES_BIKE if is_bike else GAUGE_STYLES_CAR

        for i, g in enumerate(self.app.config.overlay.gauges):
            row = tk.Frame(self._gauge_list_frame, bg=BG)
            row.pack(fill='x', pady=1)

            tk.Label(row, text=f"#{i+1}", bg=BG, fg=TEXT3,
                     font=font(8), width=3).pack(side='left')

            # Channel dropdown
            chan_var = tk.StringVar(value=g.channel)
            cb_chan  = ttk.Combobox(row, textvariable=chan_var,
                                    values=list(GAUGE_CHANNELS.keys()),
                                    state='readonly', font=font(8), width=13)
            cb_chan.pack(side='left', padx=(0, 4))
            chan_var.trace_add('write', lambda *_, iv=i, v=chan_var: self._on_gauge_channel(iv, v.get()))

            # Style dropdown
            style_var = tk.StringVar(value=g.style if g.style in avail_styles else avail_styles[0])
            cb_style  = ttk.Combobox(row, textvariable=style_var,
                                     values=avail_styles,
                                     state='readonly', font=font(8), width=9)
            cb_style.pack(side='left', padx=(0, 4))
            style_var.trace_add('write', lambda *_, iv=i, v=style_var: self._on_gauge_style(iv, v.get()))

            # Visible toggle
            vis_var = tk.BooleanVar(value=g.visible)
            tk.Checkbutton(row, text="", variable=vis_var, bg=BG,
                           selectcolor=CARD2, activebackground=BG,
                           command=lambda iv=i, v=vis_var: self._on_gauge_visible(iv, v.get())
                           ).pack(side='left')

            # Remove button
            Btn(row, "✕", small=True,
                command=lambda iv=i: self._remove_gauge(iv)).pack(side='left', padx=(2, 0))

    def _on_map_style_change(self) -> None:
        self.app.config.overlay.map_style = self.var_map_style.get()
        self.app.config.save()
        self.editor.refresh()

    def _on_theme_change(self) -> None:
        self.app.config.overlay.theme = self.var_theme.get()
        self.app.config.save()
        self.editor.refresh()

    def _add_gauge(self) -> None:
        from app_config import GaugeConfig
        # Place new gauge in bottom-left, non-overlapping
        n = len(self.app.config.overlay.gauges)
        x = 0.01 + (n % 8) * 0.12
        y = 0.74 + (n // 8) * 0.24
        self.app.config.overlay.gauges.append(
            GaugeConfig(channel='speed', style='Dial', x=min(x, 0.87), y=min(y, 0.76)))
        self.app.config.save()
        self._rebuild_gauge_list()
        self.editor.refresh()

    def _remove_gauge(self, idx: int) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges.pop(idx)
            self.app.config.save()
            self._rebuild_gauge_list()
            self.editor.refresh()

    def _on_gauge_channel(self, idx: int, channel: str) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges[idx].channel = channel
            self.app.config.save()
            self.editor.refresh()

    def _on_gauge_style(self, idx: int, style: str) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges[idx].style = style
            self.app.config.save()
            self.editor.refresh()

    def _on_gauge_visible(self, idx: int, visible: bool) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges[idx].visible = visible
            self.app.config.save()
            self.editor.refresh()

    # ── Right: Settings + Export ──────────────────────────────────────────────

    def _build_right(self, parent):
        # Scrollable right panel
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, width=320)
        vsb    = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        vsb.grid(row=0, column=2, sticky='ns')
        canvas.grid(row=0, column=1, sticky='nsew')

        inner = tk.Frame(canvas, bg=BG)
        win   = canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfig(win, width=e.width))

        p = inner

        # ── Export scope ──────────────────────────────────────────────────────
        tk.Label(p, text="EXPORT SCOPE", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', pady=(0, 4))

        scope_card = Card(p, title="WHAT TO RENDER")
        scope_card.pack(fill='x', pady=(0, 12))

        self.var_scope = tk.StringVar(value='fastest')
        scopes = [
            ('fastest',  'Fastest lap (per session)'),
            ('all_laps', 'All laps (one file per lap)'),
            ('full',     'Full session'),
            ('clip',     'Custom clip (point to point)'),
        ]
        for val, lbl in scopes:
            tk.Radiobutton(scope_card.body, text=lbl, variable=self.var_scope,
                           value=val, bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9),
                           command=self._on_scope_change).pack(anchor='w', pady=1)

        # Clip range controls — shown only when scope='clip'
        self.var_clip_start = tk.DoubleVar(value=0.0)
        self.var_clip_end   = tk.DoubleVar(value=300.0)
        self._clip_frame = tk.Frame(scope_card.body, bg=CARD)
        self._clip_frame.pack(fill='x', pady=(4, 0))

        tk.Label(self._clip_frame, text="Start (s):", bg=CARD, fg=TEXT3,
                 font=font(8)).grid(row=0, column=0, sticky='w', padx=(0, 4))
        tk.Spinbox(self._clip_frame, textvariable=self.var_clip_start,
                   from_=0, to=86400, increment=1.0, width=7, format='%.1f',
                   font=font(9), bg=CARD2, fg=TEXT, insertbackground=TEXT,
                   buttonbackground=CARD2, relief='flat',
                   bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACC,
                   ).grid(row=0, column=1, sticky='w')

        tk.Label(self._clip_frame, text="End (s):", bg=CARD, fg=TEXT3,
                 font=font(8)).grid(row=1, column=0, sticky='w', padx=(0, 4), pady=(2, 0))
        tk.Spinbox(self._clip_frame, textvariable=self.var_clip_end,
                   from_=0, to=86400, increment=1.0, width=7, format='%.1f',
                   font=font(9), bg=CARD2, fg=TEXT, insertbackground=TEXT,
                   buttonbackground=CARD2, relief='flat',
                   bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACC,
                   ).grid(row=1, column=1, sticky='w', pady=(2, 0))

        self._clip_frame.pack_forget()   # hidden until 'clip' is selected

        # ── Quality ───────────────────────────────────────────────────────────
        tk.Label(p, text="QUALITY", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', pady=(0, 4))

        q_card = Card(p, title="ENCODING SETTINGS")
        q_card.pack(fill='x', pady=(0, 12))

        # Encoder
        self._setting_row(q_card.body, "Encoder")
        enc_var = self.app.gpu_encoder
        enc_cb  = ttk.Combobox(q_card.body, textvariable=enc_var, width=20,
                               values=['h264_nvenc', 'h264_amf', 'h264_qsv', 'libx264'],
                               state='readonly', font=font(9))
        enc_cb.pack(anchor='w', pady=(0, 8))

        # CRF
        self._setting_row(q_card.body, "Quality (CRF — lower = better)")
        crf_row = tk.Frame(q_card.body, bg=CARD)
        crf_row.pack(fill='x', pady=(0, 8))
        self.lbl_crf = tk.Label(crf_row, text=str(self.app.quality_crf.get()),
                                bg=CARD, fg=TEXT, font=font(9), width=3)
        self.lbl_crf.pack(side='right')
        tk.Scale(crf_row, variable=self.app.quality_crf,
                 from_=12, to=32, orient='horizontal',
                 bg=CARD, fg=TEXT2, troughcolor=CARD2,
                 highlightthickness=0, showvalue=False,
                 command=lambda v: self.lbl_crf.config(text=str(int(float(v))))
                 ).pack(side='left', fill='x', expand=True)

        # Workers
        self._setting_row(q_card.body, "CPU workers")
        tk.Spinbox(q_card.body, textvariable=self.app.worker_count,
                   from_=1, to=16, width=5, font=font(9),
                   bg=CARD2, fg=TEXT, insertbackground=TEXT,
                   buttonbackground=CARD2, relief='flat',
                   bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACC
                   ).pack(anchor='w', pady=(0, 8))

        # Padding
        self._setting_row(q_card.body, "Pre/post lap padding (s)")
        tk.Spinbox(q_card.body, textvariable=self.app.padding_secs,
                   from_=0, to=30, increment=0.5, width=5, format='%.1f',
                   font=font(9), bg=CARD2, fg=TEXT, insertbackground=TEXT,
                   buttonbackground=CARD2, relief='flat',
                   bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACC
                   ).pack(anchor='w', pady=(0, 4))

        # ── Delta time reference ──────────────────────────────────────────────
        tk.Label(p, text="DELTA TIME", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', pady=(0, 4))

        ref_card = Card(p, title="REFERENCE LAP")
        ref_card.pack(fill='x', pady=(0, 12))

        self.var_ref_mode = tk.StringVar(value='none')
        ref_modes = [
            ('none',         'None'),
            ('session_best', 'Fastest lap in session'),
            ('custom',       'Custom lap…'),
        ]
        for val, lbl in ref_modes:
            tk.Radiobutton(ref_card.body, text=lbl, variable=self.var_ref_mode,
                           value=val, bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9),
                           command=self._on_ref_mode_change).pack(anchor='w', pady=1)

        # Custom file controls — shown only when 'custom' is selected
        self._ref_custom_path: str = ''
        self._ref_custom_laps: list = []   # list of (display_str, lap_obj)

        self._ref_custom_frame = tk.Frame(ref_card.body, bg=CARD)
        self._ref_custom_frame.pack(fill='x', pady=(4, 0))

        Btn(self._ref_custom_frame, "Browse…", small=True,
            command=self._browse_ref_file).pack(side='left', padx=(0, 6))

        self._lbl_ref_file = tk.Label(self._ref_custom_frame,
                                      text='(no file selected)',
                                      bg=CARD, fg=TEXT3, font=font(8),
                                      anchor='w', wraplength=180)
        self._lbl_ref_file.pack(side='left', fill='x', expand=True)

        self.var_ref_lap_display = tk.StringVar(value='')
        self._ref_lap_cb = ttk.Combobox(ref_card.body,
                                        textvariable=self.var_ref_lap_display,
                                        state='readonly', font=font(8), width=26)
        self._ref_lap_cb.pack(anchor='w', pady=(4, 0))
        self._ref_lap_cb.pack_forget()   # hidden until file is loaded
        self._ref_custom_frame.pack_forget()

        # ── Export button ─────────────────────────────────────────────────────
        tk.Label(p, text="RENDER", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', pady=(0, 4))

        exp_card = Card(p, title="START EXPORT")
        exp_card.pack(fill='x', pady=(0, 12))

        self.btn_export = Btn(exp_card.body, "▶▶  EXPORT",
                              command=self._start_export, accent=True)
        self.btn_export.pack(fill='x', pady=(0, 8))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(exp_card.body,
                                            variable=self.progress_var,
                                            maximum=100)
        self.progress_bar.pack(fill='x', pady=(0, 6))

        self.lbl_prog = tk.Label(exp_card.body, text="",
                                 bg=CARD, fg=TEXT3, font=font(8))
        self.lbl_prog.pack(anchor='w', pady=(0, 6))

        # Log
        tk.Label(p, text="LOG", bg=BG, fg=TEXT2,
                 font=font(8, bold=True)).pack(anchor='w', pady=(0, 4))

        log_card = Card(p, title="OUTPUT")
        log_card.pack(fill='x', pady=(0, 16))

        self.txt_log = tk.Text(log_card.body, height=12, bg=CARD2, fg=TEXT2,
                               font=font(8, mono=True), relief='flat',
                               bd=0, highlightthickness=0,
                               wrap='word', state='disabled')
        self.txt_log.pack(fill='both', expand=True)

    def _setting_row(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=TEXT3,
                 font=font(8)).pack(anchor='w', pady=(0, 2))

    # ─────────────────────────────────────────────────────────────────────────
    #  Overlay toggle callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _on_scope_change(self) -> None:
        if self.var_scope.get() == 'clip':
            self._clip_frame.pack(fill='x', pady=(4, 0))
        else:
            self._clip_frame.pack_forget()

    def _on_ref_mode_change(self) -> None:
        if self.var_ref_mode.get() == 'custom':
            self._ref_custom_frame.pack(fill='x', pady=(4, 0))
            if self._ref_custom_laps:
                self._ref_lap_cb.pack(anchor='w', pady=(4, 0))
        else:
            self._ref_custom_frame.pack_forget()
            self._ref_lap_cb.pack_forget()

    def _browse_ref_file(self) -> None:
        from tkinter.filedialog import askopenfilename
        path = askopenfilename(
            title='Select reference telemetry file',
            filetypes=[
                ('Telemetry files', '*.csv *.gpx *.CSV *.GPX'),
                ('CSV files', '*.csv'),
                ('GPX files', '*.gpx'),
                ('All files', '*.*'),
            ],
            parent=self,
        )
        if not path:
            return
        self._ref_custom_path = path
        short = os.path.basename(path)
        self._lbl_ref_file.config(text=short, fg=TEXT2)
        # Load reference session in background to populate lap list
        threading.Thread(target=self._load_ref_session_bg, args=(path,),
                         daemon=True).start()

    def _load_ref_session_bg(self, path: str) -> None:
        try:
            import gpx_data, aim_data, racebox_data
            if gpx_data.is_gpx(path):
                sess = gpx_data.load_gpx(path)
            elif aim_data.is_aim_csv(path):
                sess = aim_data.load_csv(path)
            else:
                sess = racebox_data.load_csv(path)
            self.app.q.put(('export_ref_loaded', path, sess, None))
        except Exception as e:
            self.app.q.put(('export_ref_loaded', path, None, str(e)))

    def _on_toggle_map(self):
        self.app.config.overlay.map.visible = self.var_show_map.get()
        self.app.config.save()
        self.editor.refresh()

    def _on_toggle_bike(self):
        is_bike = self.var_is_bike.get()
        self.app.config.overlay.is_bike = is_bike
        if not is_bike:
            for g in self.app.config.overlay.gauges:
                if g.style == 'Lean':
                    g.style = 'Bar'
        self.app.config.save()
        self._rebuild_gauge_list()
        self.editor.refresh()

    def _on_layout_change(self, layout: OverlayLayout) -> None:
        self.app.config.save()

    # ─────────────────────────────────────────────────────────────────────────
    #  Preview frame
    # ─────────────────────────────────────────────────────────────────────────

    def _load_preview(self) -> None:
        items = getattr(self.app, 'selected_items', [])
        video = None
        for item in items:
            vids = item.get('videos', [])
            if vids:
                video = vids[0]
                break

        if not video:
            messagebox.showinfo("Preview",
                "No video available. Select sessions in the Data tab first.")
            return

        threading.Thread(target=self._grab_frame_bg, args=(video,),
                         daemon=True).start()

    def _grab_frame_bg(self, video_path: str) -> None:
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            mid   = max(0, total // 10)   # 10% in to skip black intro frames
            cap.set(cv2.CAP_PROP_POS_FRAMES, mid)
            ret, frame = cap.read()
            cap.release()
            if ret:
                self.app.q.put(('export_preview_frame', frame))
        except Exception as e:
            self.app.q.put(('export_log', f"Preview failed: {e}"))

    # ─────────────────────────────────────────────────────────────────────────
    #  Export
    # ─────────────────────────────────────────────────────────────────────────

    def _start_export(self) -> None:
        if self._rendering:
            return

        items = getattr(self.app, 'selected_items', [])
        if not items:
            messagebox.showwarning("Export", "No sessions selected.\n"
                "Go to the Data tab, select sessions, then click Apply.")
            return

        export_path = self.app.config.export_path
        if not export_path:
            messagebox.showwarning("Export",
                "Set an Export Folder in Settings first.")
            return

        self._rendering = True
        self.btn_export.config(state='disabled')
        self.progress_var.set(0)
        self._log_clear()
        self._log("Starting export…\n")

        scope       = self.var_scope.get()
        encoder     = self.app.gpu_encoder.get()
        crf         = self.app.quality_crf.get()
        workers     = self.app.worker_count.get()
        padding     = self.app.padding_secs.get()
        is_bike     = self.app.config.overlay.is_bike
        show_map    = self.app.config.overlay.map.visible
        show_tel    = any(g.visible for g in self.app.config.overlay.gauges)
        layout      = asdict(self.app.config.overlay)
        clip_start  = self.var_clip_start.get()
        clip_end    = self.var_clip_end.get()
        ref_mode    = self.var_ref_mode.get()
        ref_path    = self._ref_custom_path
        # Resolve custom lap object now (on UI thread) to avoid races
        ref_lap_obj = None
        if ref_mode == 'custom' and self._ref_custom_laps:
            idx = self._ref_lap_cb.current()
            if 0 <= idx < len(self._ref_custom_laps):
                ref_lap_obj = self._ref_custom_laps[idx][1]

        threading.Thread(
            target=self._export_bg,
            args=(items, scope, export_path, encoder, crf, workers,
                  padding, is_bike, show_map, show_tel, layout,
                  clip_start, clip_end, ref_mode, ref_lap_obj),
            daemon=True,
        ).start()

    def _export_bg(self, items, scope, export_path, encoder, crf, workers,
                   padding, is_bike, show_map, show_tel, layout,
                   clip_start_s: float = 0.0, clip_end_s: float = 300.0,
                   ref_mode: str = 'none', ref_lap_obj=None) -> None:
        try:
            self._export_bg_inner(items, scope, export_path, encoder, crf, workers,
                                  padding, is_bike, show_map, show_tel, layout,
                                  clip_start_s, clip_end_s, ref_mode, ref_lap_obj)
        except Exception as e:
            import traceback
            self.app.q.put(('export_log', f"\n✗ Unexpected error:\n{traceback.format_exc()}"))
            self.app.q.put(('export_done', False, f"Crashed: {e}"))

    def _export_bg_inner(self, items, scope, export_path, encoder, crf, workers,
                         padding, is_bike, show_map, show_tel, layout,
                         clip_start_s: float = 0.0, clip_end_s: float = 300.0,
                         ref_mode: str = 'none', ref_lap_obj=None) -> None:
        import racebox_data, aim_data, gpx_data
        from video_renderer import render_lap, RenderJob, concat_videos
        from racebox_data import Lap

        def load_session(path):
            if gpx_data.is_gpx(path):
                return gpx_data.load_gpx(path)
            if aim_data.is_aim_csv(path):
                return aim_data.load_csv(path)
            return racebox_data.load_csv(path)

        total_jobs = len(items)
        done_jobs  = 0

        def log(msg): self.app.q.put(('export_log', msg))

        def sess_prog(done, join_share, render_pct, msg):
            """Map per-session progress into the overall bar.

            done       = sessions already fully finished
            join_share = fraction of this session's slice used by joining (0 or 0.1)
            render_pct = render_lap's 0-100 progress value
            """
            sess_w   = 100.0 / max(total_jobs, 1)
            base     = done * sess_w
            within   = join_share * sess_w + (render_pct / 100) * (1 - join_share) * sess_w
            self.app.q.put(('export_prog', base + within, msg))

        errors = []

        for item in items:
            csv_path = item.get('csv')
            videos   = item.get('videos', [])
            offset   = item.get('offset') or 0.0   # None (not set) → 0.0

            if not csv_path or not os.path.exists(csv_path):
                log(f"Skipping: CSV not found: {csv_path}")
                done_jobs += 1
                continue

            log(f"\n── {os.path.basename(csv_path)}")

            try:
                sess = load_session(csv_path)
            except Exception as e:
                log(f"  ✗ Load failed: {e}")
                errors.append(str(e))
                done_jobs += 1
                continue

            if not videos and scope != 'full':
                log("  ✗ No video file — skipping")
                done_jobs += 1
                continue

            # ── Join phase ────────────────────────────────────────────────────
            video_path  = videos[0] if videos else None
            tmp_joined  = None
            join_share  = 0.0
            if len(videos) > 1:
                join_share = 0.10
                tmp_joined = os.path.join(export_path,
                    f"_tmp_joined_{os.path.basename(csv_path)}.mp4")
                log(f"  Joining {len(videos)} video segments…")
                sess_prog(done_jobs, 0.0, 0, "Joining clips…")
                try:
                    concat_videos(videos, tmp_joined)
                    video_path = tmp_joined
                    sess_prog(done_jobs, join_share, 0, "")
                except Exception as e:
                    log(f"  ✗ Join failed: {e}")
                    errors.append(str(e))
                    done_jobs += 1
                    continue

            # ── Resolve reference lap for this session ────────────────────────
            reference_lap = None
            if ref_mode == 'session_best':
                reference_lap = sess.fastest_lap
                if reference_lap:
                    log(f"  Delta vs: session fastest ({reference_lap.duration:.3f}s)")
            elif ref_mode == 'custom' and ref_lap_obj is not None:
                reference_lap = ref_lap_obj
                log(f"  Delta vs: custom lap ({reference_lap.duration:.3f}s)")

            # ── Render phase ──────────────────────────────────────────────────
            stem = os.path.splitext(os.path.basename(csv_path))[0]

            def scaled_prog(pct, msg):
                sess_prog(done_jobs, join_share, pct, msg)

            try:
                if scope == 'fastest':
                    lap = sess.fastest_lap
                    if not lap:
                        log("  ✗ No timed lap found")
                        done_jobs += 1
                        continue
                    out = os.path.join(export_path, f"{stem}_fastest.mp4")
                    log(f"  Fastest lap: {lap.duration:.3f}s → {os.path.basename(out)}")
                    render_lap(
                        video_path, out, sess, RenderJob(f"{stem}_fastest", lap),
                        sync_offset=offset, encoder=encoder, crf=crf,
                        n_workers=workers, show_map=show_map,
                        show_telemetry=show_tel, padding=padding,
                        is_bike=is_bike, overlay_layout=layout,
                        progress_cb=scaled_prog, log_cb=log,
                        reference_lap=reference_lap,
                    )

                elif scope == 'all_laps':
                    laps = sess.laps
                    if not laps:
                        log("  ✗ No laps found")
                        done_jobs += 1
                        continue
                    for i, lap in enumerate(laps, 1):
                        out = os.path.join(export_path, f"{stem}_lap{i:02d}.mp4")
                        log(f"  Lap {i}/{len(laps)}: {lap.duration:.3f}s")
                        render_lap(
                            video_path, out, sess, RenderJob(f"{stem}_lap{i:02d}", lap),
                            sync_offset=offset, encoder=encoder, crf=crf,
                            n_workers=workers, show_map=show_map,
                            show_telemetry=show_tel, padding=padding,
                            is_bike=is_bike, overlay_layout=layout,
                            progress_cb=scaled_prog, log_cb=log,
                            reference_lap=reference_lap,
                        )

                elif scope == 'full':
                    out = os.path.join(export_path, f"{stem}_full.mp4")
                    log(f"  Full session → {os.path.basename(out)}")
                    render_lap(
                        video_path or '', out, sess, RenderJob(f"{stem}_full", None),
                        sync_offset=offset, encoder=encoder, crf=crf,
                        n_workers=workers, show_map=show_map,
                        show_telemetry=show_tel, padding=0.0,
                        is_bike=is_bike, overlay_layout=layout,
                        progress_cb=scaled_prog, log_cb=log,
                        reference_lap=reference_lap,
                    )

                elif scope == 'clip':
                    pts = sess.all_points
                    if pts:
                        sess_end = pts[-1].elapsed
                        c_start  = max(0.0, min(clip_start_s, sess_end))
                        c_end    = max(c_start + 0.1, min(clip_end_s, sess_end))
                    else:
                        c_start, c_end = clip_start_s, clip_end_s
                    clip_pts = [p for p in pts if c_start <= p.elapsed <= c_end]
                    if not clip_pts:
                        log(f"  ✗ No data points in range {c_start:.1f}–{c_end:.1f}s")
                        done_jobs += 1
                        continue
                    clip_lap = Lap(
                        lap_num  = -1,
                        points   = clip_pts,
                        duration = c_end - c_start,
                    )
                    tag = f"{int(c_start)}s_{int(c_end)}s"
                    out = os.path.join(export_path, f"{stem}_clip_{tag}.mp4")
                    log(f"  Clip {c_start:.1f}s–{c_end:.1f}s → {os.path.basename(out)}")
                    render_lap(
                        video_path or '', out, sess, RenderJob(f"{stem}_clip", clip_lap),
                        sync_offset=offset, encoder=encoder, crf=crf,
                        n_workers=workers, show_map=show_map,
                        show_telemetry=show_tel, padding=padding,
                        is_bike=is_bike, overlay_layout=layout,
                        reference_lap=reference_lap,
                        progress_cb=scaled_prog, log_cb=log,
                    )

            except Exception as e:
                log(f"  ✗ Render error: {e}")
                errors.append(str(e))
            finally:
                if tmp_joined and os.path.exists(tmp_joined):
                    try: os.remove(tmp_joined)
                    except Exception: pass

            done_jobs += 1
            sess_prog(done_jobs, 0, 0, "")   # snap to next session boundary

        if errors:
            self.app.q.put(('export_done', False,
                             f"{len(errors)} error(s) — see log"))
        else:
            self.app.q.put(('export_done', True,
                             f"Done — {done_jobs} session(s) exported"))

    # ─────────────────────────────────────────────────────────────────────────
    #  Log helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.txt_log.config(state='normal')
        self.txt_log.insert('end', msg + '\n')
        self.txt_log.see('end')
        self.txt_log.config(state='disabled')

    def _log_clear(self) -> None:
        self.txt_log.config(state='normal')
        self.txt_log.delete('1.0', 'end')
        self.txt_log.config(state='disabled')

    # ─────────────────────────────────────────────────────────────────────────
    #  Queue handler
    # ─────────────────────────────────────────────────────────────────────────

    def on_queue(self, kind, *args):
        if kind == 'export_ref_loaded':
            path, sess, err = args[0], args[1], args[2]
            if path != self._ref_custom_path:
                return   # stale response
            if err or sess is None:
                self._lbl_ref_file.config(
                    text=f'Load error: {err}', fg=ERR)
                return
            # Populate lap combobox
            entries = []
            for lap in sess.laps:
                tag = 'outlap' if lap.is_outlap else ('inlap' if lap.is_inlap else f'{lap.duration:.3f}s')
                entries.append((f'Lap {lap.lap_num}  ({tag})', lap))
            self._ref_custom_laps = entries
            self._ref_lap_cb['values'] = [e[0] for e in entries]
            if entries:
                self._ref_lap_cb.current(0)
            self._ref_lap_cb.pack(anchor='w', pady=(4, 0))
        elif kind == 'export_log':
            self._log(args[0])
        elif kind == 'export_prog':
            pct, msg = args[0], args[1]
            self.progress_var.set(pct)
            if msg:
                self.lbl_prog.config(text=msg, fg=TEXT2)
        elif kind == 'export_done':
            ok, msg = args[0], args[1]
            self._rendering = False
            self.btn_export.config(state='normal')
            self.progress_var.set(100 if ok else 0)
            self.lbl_prog.config(text=msg, fg=OK if ok else ERR)
            self._log(f"\n{'✓' if ok else '✗'} {msg}")
        elif kind == 'export_preview_frame':
            self.editor.set_frame(args[0])

    # ─────────────────────────────────────────────────────────────────────────
    #  Called when user navigates to this tab
    # ─────────────────────────────────────────────────────────────────────────

    def on_show(self) -> None:
        """Refresh selection count label from app.selected_items."""
        items = getattr(self.app, 'selected_items', [])
        n = len(items)
        if n == 0:
            self.lbl_selection.config(text="No sessions selected", fg=TEXT3)
        elif n == 1:
            self.lbl_selection.config(text="1 session selected", fg=TEXT2)
        else:
            self.lbl_selection.config(text=f"{n} sessions selected", fg=TEXT2)
