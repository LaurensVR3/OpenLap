# page_export.py — Export: visual overlay editor + quality settings + render

from __future__ import annotations
import os
import re
import threading
from dataclasses import asdict
from typing import List

import tkinter as tk
from tkinter import ttk, messagebox

def _export_stem(sess, scope_label: str) -> str:
    """Build a human-readable export filename stem: YYYY-MM-DD_HH-MM_Track_Scope."""
    dt = sess.start_time
    if dt is None and sess.date_utc:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(sess.date_utc.replace('Z', '+00:00'))
        except Exception:
            dt = None
    date_part = dt.strftime('%Y-%m-%d') if dt else 'unknown-date'
    time_part = dt.strftime('%H-%M')    if dt else ''
    track = re.sub(r'[^\w\s-]', '', sess.track or 'unknown').strip()
    track = re.sub(r'\s+', '_', track) or 'unknown'
    parts = [date_part, time_part, track, scope_label] if time_part else [date_part, track, scope_label]
    return '_'.join(parts)


from utils import compute_lean_angle
from design_tokens import BG, CARD, CARD2, BORDER, ACC, ACC2, OK, WARN, ERR, TEXT, TEXT2, TEXT3, font
from widgets import Card, Btn, Divider, Label
from app_config import AppConfig, OverlayLayout, overlay_from_dict
from overlay_editor import OverlayEditor


class ExportPage(tk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._rendering = False
        self._available_channels: set | None = None  # None = no session loaded, show all
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
        Btn(hdr, "Edit Info…", self._edit_session_info,
            width=10).pack(side='right', padx=(0, 4))

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
        from overlay_themes import theme_names
        from tkinter import ttk

        tk.Label(parent, text="Theme:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(0, 4))
        self.var_theme = tk.StringVar(value=getattr(self.app.config.overlay, 'theme', 'Dark'))
        cb_theme = ttk.Combobox(parent, textvariable=self.var_theme,
                                values=theme_names(), state='readonly',
                                font=font(9), width=11)
        cb_theme.pack(side='left', padx=(0, 16))
        self.var_theme.trace_add('write', lambda *_: self._on_theme_change())

        Btn(parent, "+ Add gauge", small=True,
            command=self._add_gauge).pack(side='left', padx=(0, 4))

        # Element list panel — row 3 in left frame (below style_row at row 2)
        self._gauge_list_frame = tk.Frame(parent.master, bg=BG)
        self._gauge_list_frame.grid(row=3, column=0, sticky='ew', pady=(0, 2))
        self._rebuild_gauge_list()

    def _make_eye_btn(self, parent, get_vis, set_vis) -> tk.Button:
        """Return a ●/○ toggle button wired to get_vis/set_vis."""
        vis = get_vis()
        btn = tk.Button(parent,
                        text='●' if vis else '○',
                        fg=ACC if vis else TEXT3,
                        bg=BG, activebackground=BG, activeforeground=ACC,
                        relief='flat', bd=0, font=font(9), width=2,
                        cursor='hand2')
        def _toggle(b=btn):
            new_vis = not get_vis()
            set_vis(new_vis)
            b.config(text='●' if new_vis else '○',
                     fg=ACC if new_vis else TEXT3)
            self.app.config.save()
            self.editor.refresh()
        btn.config(command=_toggle)
        btn.pack(side='left', padx=(0, 2))
        return btn

    def _rebuild_gauge_list(self) -> None:
        """Rebuild element list in a 2-column grid — map first, then gauges."""
        from tkinter import ttk
        from style_registry import available_styles
        from gauge_channels import GAUGE_CHANNELS, get_channel_styles

        for w in self._gauge_list_frame.winfo_children():
            w.destroy()

        f = self._gauge_list_frame
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        is_bike = self.app.config.overlay.is_bike
        overlay = self.app.config.overlay

        avail = self._available_channels  # None = no filter (no session loaded)
        all_chan_keys = list(GAUGE_CHANNELS.keys()) + ['map', 'multi', 'info', 'lap_info']
        chan_keys = [k for k in all_chan_keys if avail is None or k in avail]
        n_elems  = len(overlay.gauges)

        for idx, g in enumerate(overlay.gauges):
            grid_row = idx // 2
            grid_col = idx % 2
            cell = tk.Frame(f, bg=BG)
            cell.grid(row=grid_row, column=grid_col, sticky='ew',
                      padx=(0, 4) if grid_col == 0 else (0, 0), pady=1)

            styles    = get_channel_styles(g.channel, is_bike)
            cur_style = g.style if g.style in styles else styles[0]
            if g.style != cur_style:
                g.style = cur_style

            chan_var  = tk.StringVar(value=g.channel)
            style_var = tk.StringVar(value=cur_style)

            cb_chan = ttk.Combobox(cell, textvariable=chan_var,
                                   values=chan_keys, state='readonly',
                                   font=font(8), width=10)
            cb_chan.pack(side='left', padx=(0, 2))

            if g.channel == 'multi':
                # Multi-Line: style is fixed, show channel picker instead
                tk.Label(cell, text='Multi-Line', bg=BG, fg=TEXT2,
                         font=font(8)).pack(side='left', padx=(0, 2))
                Btn(cell, 'Edit channels…', small=True,
                    command=lambda iv=idx: self._edit_multi_channels(iv)
                    ).pack(side='left', padx=(0, 2))
            elif g.channel == 'info':
                # Info: style is fixed, show field picker instead
                tk.Label(cell, text='Info', bg=BG, fg=TEXT2,
                         font=font(8)).pack(side='left', padx=(0, 2))
                Btn(cell, 'Edit fields…', small=True,
                    command=lambda iv=idx: self._edit_info_fields(iv)
                    ).pack(side='left', padx=(0, 2))
            else:
                cb_style = ttk.Combobox(cell, textvariable=style_var,
                                        values=styles, state='readonly',
                                        font=font(8), width=8)
                cb_style.pack(side='left', padx=(0, 2))

            _has_style_cb = g.channel not in ('multi', 'info')
            chan_var.trace_add('write',
                lambda *_, iv=idx, cv=chan_var, sv=style_var,
                        cs=(cb_style if _has_style_cb else None):
                    self._on_gauge_channel_ctx(iv, cv.get(), sv, cs))

            if _has_style_cb:
                style_var.trace_add('write',
                    lambda *_, iv=idx, sv=style_var:
                        self._on_gauge_style(iv, sv.get()))

            self._make_eye_btn(cell,
                get_vis=lambda iv=idx: overlay.gauges[iv].visible,
                set_vis=lambda v, iv=idx: self._set_gauge_vis(iv, v))

            Btn(cell, '✕', small=True,
                command=lambda iv=idx: self._remove_gauge(iv)
                ).pack(side='left', padx=(2, 0))

        # "+ Add gauge" in the next free cell
        add_row = n_elems // 2
        add_col = n_elems % 2
        Btn(f, '+ Add gauge', small=True, command=self._add_gauge).grid(
            row=add_row, column=add_col, sticky='w', pady=(3, 0))

    def _on_theme_change(self) -> None:
        self.app.config.overlay.theme = self.var_theme.get()
        self.app.config.schedule_save()
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

    def _edit_multi_channels(self, idx: int) -> None:
        """Open a channel picker dialog for a Multi-Line gauge."""
        from gauge_channels import GAUGE_CHANNELS
        gauges = self.app.config.overlay.gauges
        if not (0 <= idx < len(gauges)):
            return
        g = gauges[idx]

        win = tk.Toplevel(self)
        win.title('Select channels')
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text='Choose channels to overlay:',
                 bg=CARD, fg=TEXT2, font=font(9)).pack(anchor='w', padx=12, pady=(10, 4))

        current = set(g.channels)
        vars_map = {}  # channel_key -> BooleanVar

        scroll_frame = tk.Frame(win, bg=CARD)
        scroll_frame.pack(fill='x', padx=12, pady=4)

        avail = getattr(self, '_available_channels', None)
        for ch_key, meta in GAUGE_CHANNELS.items():
            if avail is not None and ch_key not in avail:
                continue
            var = tk.BooleanVar(value=(ch_key in current))
            vars_map[ch_key] = var
            row = tk.Frame(scroll_frame, bg=CARD)
            row.pack(fill='x', pady=1)
            tk.Checkbutton(row, text=f"{meta['label']}  ({meta['unit']})" if meta['unit'] else meta['label'],
                           variable=var, bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9)).pack(side='left')

        btn_row = tk.Frame(win, bg=CARD)
        btn_row.pack(fill='x', padx=12, pady=(4, 10))

        def _apply():
            selected = [k for k, v in vars_map.items() if v.get()]
            g.channels = selected
            if not g.style:
                g.style = 'Multi-Line'
            self.app.config.save()
            self._rebuild_gauge_list()
            self.editor.refresh()
            win.destroy()

        Btn(btn_row, 'OK',     accent=True, command=_apply).pack(side='left', padx=(0, 6))
        Btn(btn_row, 'Cancel', command=win.destroy).pack(side='left')

        win.update_idletasks()
        # Centre over parent
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        ww, wh = win.winfo_width(), win.winfo_height()
        win.geometry(f'+{px + pw//2 - ww//2}+{py + ph//2 - wh//2}')

    def _edit_info_fields(self, idx: int) -> None:
        """Open a field picker dialog for an Info gauge (mirrors _edit_multi_channels)."""
        from gauge_channels import INFO_FIELDS, INFO_FIELDS_DEFAULT
        gauges = self.app.config.overlay.gauges
        if not (0 <= idx < len(gauges)):
            return
        g = gauges[idx]

        win = tk.Toplevel(self)
        win.title('Select info fields')
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text='Choose fields to display:',
                 bg=CARD, fg=TEXT2, font=font(9)).pack(anchor='w', padx=12, pady=(10, 4))

        current = set(g.channels) if g.channels else set(INFO_FIELDS_DEFAULT)
        vars_map = {}

        for field_key, field_label in INFO_FIELDS.items():
            var = tk.BooleanVar(value=(field_key in current))
            vars_map[field_key] = var
            row = tk.Frame(win, bg=CARD)
            row.pack(fill='x', padx=12, pady=1)
            tk.Checkbutton(row, text=field_label, variable=var,
                           bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9)).pack(side='left')

        btn_row = tk.Frame(win, bg=CARD)
        btn_row.pack(fill='x', padx=12, pady=(4, 10))

        def _apply():
            selected = [k for k in INFO_FIELDS if vars_map[k].get()]
            g.channels = selected
            g.style = 'Info'
            self.app.config.save()
            self._rebuild_gauge_list()
            self.editor.refresh()
            win.destroy()

        Btn(btn_row, 'OK',     accent=True, command=_apply).pack(side='left', padx=(0, 6))
        Btn(btn_row, 'Cancel', command=win.destroy).pack(side='left')

        win.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        ww, wh = win.winfo_width(), win.winfo_height()
        win.geometry(f'+{px + pw//2 - ww//2}+{py + ph//2 - wh//2}')

    def _on_gauge_channel_ctx(self, idx: int, channel: str,
                               style_var: tk.StringVar, cb_style) -> None:
        """Channel changed: update style list and coerce stale style."""
        from gauge_channels import get_channel_styles
        gauges = self.app.config.overlay.gauges
        if not (0 <= idx < len(gauges)):
            return
        gauges[idx].channel = channel
        # For multi/info, style is fixed and cb_style is None
        if channel == 'multi':
            gauges[idx].style = 'Multi-Line'
            gauges[idx].channels = []
            self.app.config.save()
            self._rebuild_gauge_list()
            self.editor.refresh()
            return
        if channel == 'info':
            gauges[idx].style = 'Info'
            gauges[idx].channels = []
            self.app.config.save()
            self._rebuild_gauge_list()
            self.editor.refresh()
            return
        is_bike = self.app.config.overlay.is_bike
        new_styles = get_channel_styles(channel, is_bike)
        if cb_style is not None:
            cb_style.config(values=new_styles)
        if style_var.get() not in new_styles:
            style_var.set(new_styles[0])   # triggers _on_gauge_style via trace
        else:
            self.app.config.save()
            self.editor.refresh()

    def _set_gauge_vis(self, idx: int, visible: bool) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges[idx].visible = visible

    def _on_gauge_style(self, idx: int, style: str) -> None:
        gauges = self.app.config.overlay.gauges
        if 0 <= idx < len(gauges):
            gauges[idx].style = style
            self.app.config.schedule_save()
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
            ('none',          'None'),
            ('session_best',  'Fastest lap in session'),
            ('track_library', 'Pick from track history…'),
            ('custom',        'Custom lap…'),
        ]
        for val, lbl in ref_modes:
            tk.Radiobutton(ref_card.body, text=lbl, variable=self.var_ref_mode,
                           value=val, bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9),
                           command=self._on_ref_mode_change).pack(anchor='w', pady=1)

        # ── Track-library picker — shown when 'track_library' is selected ────
        self._lib_laps: list = []   # parallel list of lap objects, index = treeview row
        self._lib_track: str = ''   # track name currently loaded in the treeview

        self._ref_library_frame = tk.Frame(ref_card.body, bg=CARD)

        self._lbl_lib_status = tk.Label(self._ref_library_frame, text='',
                                        bg=CARD, fg=TEXT3, font=font(8), anchor='w')
        self._lbl_lib_status.pack(anchor='w', pady=(0, 4))

        lib_tree_frame = tk.Frame(self._ref_library_frame, bg=CARD)
        lib_tree_frame.pack(fill='x')

        lib_cols = ('date', 'lap', 'time')
        self._lib_tree = ttk.Treeview(lib_tree_frame, columns=lib_cols,
                                      show='headings', height=6,
                                      selectmode='browse')
        self._lib_tree.heading('date', text='Date')
        self._lib_tree.heading('lap',  text='Lap')
        self._lib_tree.heading('time', text='Time')
        self._lib_tree.column('date', width=100, anchor='w', stretch=True)
        self._lib_tree.column('lap',  width=35,  anchor='center', stretch=False)
        self._lib_tree.column('time', width=80,  anchor='e', stretch=False)

        lib_scroll = ttk.Scrollbar(lib_tree_frame, orient='vertical',
                                   command=self._lib_tree.yview)
        self._lib_tree.configure(yscrollcommand=lib_scroll.set)
        self._lib_tree.pack(side='left', fill='x', expand=True)
        lib_scroll.pack(side='right', fill='y')

        self._ref_library_frame.pack_forget()   # hidden until mode selected

        # ── Custom file controls — shown only when 'custom' is selected ───────
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
        mode = self.var_ref_mode.get()
        if mode == 'custom':
            self._ref_custom_frame.pack(fill='x', pady=(4, 0))
            if self._ref_custom_laps:
                self._ref_lap_cb.pack(anchor='w', pady=(4, 0))
        else:
            self._ref_custom_frame.pack_forget()
            self._ref_lap_cb.pack_forget()

        if mode == 'track_library':
            self._ref_library_frame.pack(fill='x', pady=(4, 0))
            self._refresh_library()
        else:
            self._ref_library_frame.pack_forget()

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

    # ── Track library ─────────────────────────────────────────────────────────

    def _refresh_library(self) -> None:
        """Determine track from selected sessions and kick off async lap load."""
        for row in self._lib_tree.get_children():
            self._lib_tree.delete(row)
        self._lib_laps = []
        self._lib_track = ''

        items = getattr(self.app, 'selected_items', [])
        if not items:
            self._lbl_lib_status.config(
                text='Select sessions in the Data tab first.', fg=TEXT3)
            return

        data_page = self.app.pages.get('data_page')
        if not data_page:
            return

        csv_path = items[0].get('csv', '')
        track, _, _ = data_page.get_track_meta(csv_path)
        if not track or track in ('—', ''):
            self._lbl_lib_status.config(
                text='Could not determine track for selected session.', fg=TEXT3)
            return

        self._lib_track = track
        self._lbl_lib_status.config(
            text=f'Loading laps for: {track}…', fg=TEXT3)

        all_sessions = data_page.get_all_sessions()
        threading.Thread(
            target=self._load_library_bg,
            args=(track, all_sessions, data_page),
            daemon=True,
        ).start()

    def _load_library_bg(self, track: str, all_sessions: list,
                         data_page) -> None:
        """Background: load all sessions matching *track* and collect timed laps."""
        import racebox_data, aim_data, gpx_data, motec_data

        def load_sess(path):
            if motec_data.is_motec_ld(path):
                return motec_data.load_ld(path)
            if gpx_data.is_gpx(path):
                return gpx_data.load_gpx(path)
            if aim_data.is_aim_csv(path):
                return aim_data.load_csv(path)
            return racebox_data.load_csv(path)

        results = []   # list of (date_str, lap_index, lap_duration, lap_obj)
        for m in all_sessions:
            csv = getattr(m, 'csv_path', None)
            if not csv or not os.path.exists(csv):
                continue
            t, _, _ = data_page.get_track_meta(csv)
            if t != track:
                continue
            try:
                sess = load_sess(csv)
                date_str = (m.csv_start.strftime('%Y-%m-%d')
                            if getattr(m, 'csv_start', None) else '?')
                for i, lap in enumerate(sess.timed_laps, 1):
                    results.append((date_str, i, lap.duration, lap))
            except Exception:
                pass

        results.sort(key=lambda x: x[2])
        self.app.q.put(('export_library_loaded', track, results))

    def _on_layout_change(self, layout: OverlayLayout) -> None:
        self.app.config.schedule_save()

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

    def _load_preview_history_bg(self, csv_path: str) -> None:
        """Load real telemetry from the best timed lap and feed to the overlay editor."""
        try:
            from page_data import _load_session
            sess = _load_session(csv_path)
            if not sess or not sess.laps:
                return
            # Apply bike override and compute lean if session is a bike.
            abs_csv = os.path.abspath(csv_path)
            override = self.app.config.bike_overrides.get(abs_csv)
            if override is not None:
                sess.is_bike = override
            if sess.is_bike:
                for pt in sess.all_points:
                    if pt.lean_angle == 0.0:
                        pt.lean_angle = compute_lean_angle(
                            pt.speed, pt.gyro_z, pt.gforce_y)
            # Pick the fastest timed lap
            timed = [l for l in sess.laps if not l.is_outlap and not l.is_inlap
                     and l.duration > 10]
            lap = min(timed, key=lambda l: l.duration) if timed else sess.laps[0]
            pts = lap.points
            if not pts:
                return
            history = [{
                't':            i / max(len(pts) - 1, 1) * lap.duration,
                'speed':        getattr(p, 'speed',       0.0),
                'gx':           getattr(p, 'gforce_x',    0.0),
                'gy':           getattr(p, 'gforce_y',    0.0),
                'lean':         getattr(p, 'lean_angle',  0.0),
                'rpm':          getattr(p, 'rpm',         0.0),
                'exhaust_temp': getattr(p, 'exhaust_temp',0.0),
                'delta_time':   0.0,
                'alt':          getattr(p, 'alt',         0.0),
            } for i, p in enumerate(pts)]
            # Compute which channels have actual data in this session
            from gauge_channels import GAUGE_CHANNELS
            avail: set = set()
            for ch_key, meta in GAUGE_CHANNELS.items():
                hk = meta['hist_key']
                if any(abs(pt.get(hk, 0.0)) > 1e-6 for pt in history):
                    avail.add(ch_key)
            # delta_time is computed at render time — always offer it
            avail.add('delta_time')
            # multi-line, session info, and lap scoreboard are always available
            avail.add('multi')
            avail.add('info')
            avail.add('lap_info')
            # map requires GPS coordinates on the lap points
            if any(getattr(p, 'lat', None) for p in pts):
                avail.add('map')
            # Build real session meta for the info gauge preview
            abs_csv = os.path.abspath(csv_path)
            info_ov = self.app.config.session_info.get(abs_csv, {})
            session_meta = {
                'info_track':   info_ov.get('info_track')   or sess.track or '',
                'info_vehicle': info_ov.get('info_vehicle') or getattr(sess, 'vehicle', '') or '',
                'info_session': info_ov.get('info_session') or getattr(sess, 'session_type', '') or '',
                'info_source':  getattr(sess, 'source', '') or '',
                'info_date':    '',
                'info_time':    '',
                'info_weather': '',
                'info_wind':    '',
            }
            if getattr(sess, 'date_utc', None):
                try:
                    from datetime import datetime as _dt
                    _d = _dt.fromisoformat(sess.date_utc.replace('Z', '+00:00'))
                    session_meta['info_date'] = _d.strftime('%Y-%m-%d')
                    session_meta['info_time'] = _d.strftime('%H:%M')
                except Exception:
                    pass
                # Fetch weather in background (cached after first hit)
                try:
                    first_gps = next(
                        (p for p in pts
                         if getattr(p, 'lat', 0.0) and getattr(p, 'lon', 0.0)), None)
                    if first_gps:
                        from weather import fetch_weather
                        session_meta['info_weather'], session_meta['info_wind'] = fetch_weather(
                            first_gps.lat, first_gps.lon, sess.date_utc)
                except Exception:
                    pass
            self.app.q.put(('export_preview_history', history, avail, session_meta))
        except Exception:
            pass

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
        gauges   = self.app.config.overlay.gauges
        show_map = any(g.visible and g.channel == 'map' for g in gauges)
        show_tel = any(g.visible and g.channel != 'map' for g in gauges)
        layout      = asdict(self.app.config.overlay)
        clip_start  = self.var_clip_start.get()
        clip_end    = self.var_clip_end.get()
        ref_mode    = self.var_ref_mode.get()
        ref_path    = self._ref_custom_path
        # Resolve lap object now (on UI thread) to avoid races
        ref_lap_obj = None
        if ref_mode == 'custom' and self._ref_custom_laps:
            idx = self._ref_lap_cb.current()
            if 0 <= idx < len(self._ref_custom_laps):
                ref_lap_obj = self._ref_custom_laps[idx][1]
        elif ref_mode == 'track_library' and self._lib_laps:
            sel = self._lib_tree.selection()
            idx = int(sel[0]) if sel else 0
            if 0 <= idx < len(self._lib_laps):
                ref_lap_obj = self._lib_laps[idx]

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
            from export_runner import run_export
            run_export(
                items=items, scope=scope, export_path=export_path,
                encoder=encoder, crf=crf, workers=workers, padding=padding,
                is_bike=is_bike, show_map=show_map, show_tel=show_tel,
                layout=layout, clip_start_s=clip_start_s, clip_end_s=clip_end_s,
                ref_mode=ref_mode, ref_lap_obj=ref_lap_obj,
                bike_overrides=self.app.config.bike_overrides,
                session_info=self.app.config.session_info,
                log_cb=lambda msg: self.app.q.put(('export_log', msg)),
                progress_cb=lambda pct, msg: self.app.q.put(('export_prog', pct, msg)),
                done_cb=lambda ok, msg: self.app.q.put(('export_done', ok, msg)),
            )
        except Exception as e:
            import traceback
            self.app.q.put(('export_log', f"\n✗ Unexpected error:\n{traceback.format_exc()}"))
            self.app.q.put(('export_done', False, f"Crashed: {e}"))

    # ─────────────────────────────────────────────────────────────────────────
    #  Session info editor dialog
    # ─────────────────────────────────────────────────────────────────────────

    def _edit_session_info(self) -> None:
        """Open a small dialog to manually set/override session metadata."""
        items = getattr(self.app, 'selected_items', [])
        if not items:
            return
        csv_path = os.path.abspath(items[0].get('csv', ''))
        if not csv_path:
            return

        existing = self.app.config.session_info.get(csv_path, {})

        dlg = tk.Toplevel(self)
        dlg.title("Edit Session Info")
        dlg.resizable(False, False)
        dlg.configure(bg=BG)
        dlg.grab_set()

        def row(parent, label, default):
            fr = tk.Frame(parent, bg=BG)
            fr.pack(fill='x', pady=3)
            tk.Label(fr, text=label, bg=BG, fg=TEXT3, font=font(9),
                     width=14, anchor='w').pack(side='left')
            var = tk.StringVar(value=default)
            tk.Entry(fr, textvariable=var, bg=CARD, fg=TEXT,
                     insertbackground=TEXT, relief='flat',
                     font=font(9), width=28).pack(side='left', padx=(4, 0))
            return var

        body = tk.Frame(dlg, bg=BG, padx=16, pady=12)
        body.pack(fill='x')
        tk.Label(body, text=os.path.basename(csv_path), bg=BG, fg=TEXT2,
                 font=font(8), wraplength=340, justify='left').pack(anchor='w', pady=(0, 8))

        v_track   = row(body, "Track",        existing.get('info_track',   ''))
        v_vehicle = row(body, "Vehicle",      existing.get('info_vehicle', ''))
        v_session = row(body, "Session type", existing.get('info_session', ''))

        tk.Label(body, text="Leave blank to use values parsed from the telemetry file.",
                 bg=BG, fg=TEXT3, font=font(7), wraplength=340).pack(anchor='w', pady=(6, 0))

        def save():
            entry = {}
            if v_track.get().strip():
                entry['info_track']   = v_track.get().strip()
            if v_vehicle.get().strip():
                entry['info_vehicle'] = v_vehicle.get().strip()
            if v_session.get().strip():
                entry['info_session'] = v_session.get().strip()
            if entry:
                self.app.config.session_info[csv_path] = entry
            else:
                self.app.config.session_info.pop(csv_path, None)
            self.app.config.schedule_save()
            dlg.destroy()
            # Reload preview so info gauge reflects the updated values
            csv = items[0].get('csv')
            if csv:
                self._last_preview_csv = None   # force reload
                threading.Thread(
                    target=self._load_preview_history_bg, args=(csv,),
                    daemon=True).start()

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(fill='x', padx=16, pady=(0, 12))
        Btn(btn_row, "Save", save).pack(side='right', padx=(4, 0))
        Btn(btn_row, "Cancel", dlg.destroy).pack(side='right')

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
        elif kind == 'export_library_loaded':
            track, results = args[0], args[1]
            if track != self._lib_track:
                return   # stale response
            for row in self._lib_tree.get_children():
                self._lib_tree.delete(row)
            self._lib_laps = []
            if not results:
                self._lbl_lib_status.config(
                    text=f'No laps found for: {track}', fg=TEXT3)
                return
            def fmt_time(secs):
                m, s = divmod(secs, 60)
                return f'{int(m)}:{s:06.3f}'
            for date_str, lap_idx, duration, lap_obj in results:
                iid = str(len(self._lib_laps))
                self._lib_tree.insert('', 'end', iid=iid,
                    values=(date_str, lap_idx, fmt_time(duration)))
                self._lib_laps.append(lap_obj)
            self._lib_tree.selection_set('0')
            self._lbl_lib_status.config(
                text=f'{len(results)} lap(s) on {track} — sorted by time',
                fg=TEXT3)
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
        elif kind == 'export_preview_history':
            session_meta = args[2] if len(args) > 2 else None
            self.editor.set_history(args[0], session_meta=session_meta)
            self._available_channels = args[1] if len(args) > 1 else None
            self._rebuild_gauge_list()
        elif kind == 'export_invalidate_preview':
            self._last_preview_csv = None

    # ─────────────────────────────────────────────────────────────────────────
    #  Called when user navigates to this tab
    # ─────────────────────────────────────────────────────────────────────────

    def on_show(self) -> None:
        """Refresh selection count label and auto-load preview frame."""
        items = getattr(self.app, 'selected_items', [])
        n = len(items)
        if n == 0:
            self.lbl_selection.config(text="No sessions selected", fg=TEXT3)
            if self._available_channels is not None:
                self._available_channels = None
                self._rebuild_gauge_list()
        elif n == 1:
            self.lbl_selection.config(text="1 session selected", fg=TEXT2)
        else:
            self.lbl_selection.config(text=f"{n} sessions selected", fg=TEXT2)

        # Auto-load preview frame from first available video
        video = None
        for item in items:
            vids = item.get('videos', [])
            if vids:
                video = vids[0]
                break
        if video and video != getattr(self, '_last_preview_video', None):
            self._last_preview_video = video
            threading.Thread(target=self._grab_frame_bg, args=(video,),
                             daemon=True).start()

        # Auto-load real telemetry for gauge previews
        csv = items[0].get('csv') if items else None
        if csv and csv != getattr(self, '_last_preview_csv', None):
            self._last_preview_csv = csv
            threading.Thread(target=self._load_preview_history_bg, args=(csv,),
                             daemon=True).start()
