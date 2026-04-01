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
from app_config import AppConfig, OverlayLayout
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
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        # Toggle + mode row
        ctrl = tk.Frame(left, bg=BG)
        ctrl.grid(row=0, column=0, sticky='ew', pady=(0, 6))

        self.var_show_map = tk.BooleanVar(value=self.app.config.overlay.map.visible)
        self.var_show_tel = tk.BooleanVar(value=self.app.config.overlay.telemetry.visible)

        tk.Checkbutton(ctrl, text="Map", variable=self.var_show_map,
                       bg=BG, fg=TEXT, selectcolor=CARD2, activebackground=BG,
                       font=font(9), command=self._on_toggle_map).pack(side='left', padx=(0, 12))
        tk.Checkbutton(ctrl, text="Telemetry", variable=self.var_show_tel,
                       bg=BG, fg=TEXT, selectcolor=CARD2, activebackground=BG,
                       font=font(9), command=self._on_toggle_tel).pack(side='left', padx=(0, 20))

        self.var_is_bike = tk.BooleanVar(value=self.app.config.overlay.is_bike)
        tk.Checkbutton(ctrl, text="Bike mode", variable=self.var_is_bike,
                       bg=BG, fg=TEXT2, selectcolor=CARD2, activebackground=BG,
                       font=font(9), command=self._on_toggle_bike).pack(side='left', padx=(0, 12))

        Btn(ctrl, "Load preview frame", small=True,
            command=self._load_preview).pack(side='right')

        # Style picker row
        style_row = tk.Frame(left, bg=BG)
        style_row.grid(row=1, column=0, sticky='ew', pady=(0, 4))
        self._build_style_row(style_row)

        # Editor canvas
        editor_frame = tk.Frame(left, bg=BORDER, bd=1)
        editor_frame.grid(row=2, column=0, sticky='nsew')
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)

        self.editor = OverlayEditor(
            editor_frame,
            layout=self.app.config.overlay,
            on_change=self._on_layout_change,
        )
        self.editor.grid(row=0, column=0, sticky='nsew', padx=1, pady=1)

    def _build_style_row(self, parent) -> None:
        from style_registry import available_styles, default_style
        from tkinter import ttk

        tel_styles = available_styles('telemetry') or ['Strip']
        map_styles = available_styles('map')        or ['Circuit']

        cur_tel = self.app.config.overlay.telemetry_style or default_style('telemetry') or tel_styles[0]
        cur_map = self.app.config.overlay.map_style        or default_style('map')        or map_styles[0]

        tk.Label(parent, text="Telemetry style:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(0, 4))
        self.var_tel_style = tk.StringVar(value=cur_tel)
        cb_tel = ttk.Combobox(parent, textvariable=self.var_tel_style,
                              values=tel_styles, state='readonly',
                              font=font(9), width=18)
        cb_tel.pack(side='left', padx=(0, 16))
        self.var_tel_style.trace_add('write', lambda *_: self._on_style_change('telemetry'))

        tk.Label(parent, text="Map style:", bg=BG, fg=TEXT3,
                 font=font(8)).pack(side='left', padx=(0, 4))
        self.var_map_style = tk.StringVar(value=cur_map)
        cb_map = ttk.Combobox(parent, textvariable=self.var_map_style,
                              values=map_styles, state='readonly',
                              font=font(9), width=14)
        cb_map.pack(side='left')
        self.var_map_style.trace_add('write', lambda *_: self._on_style_change('map'))

    def _on_style_change(self, key: str) -> None:
        if key == 'telemetry':
            self.app.config.overlay.telemetry_style = self.var_tel_style.get()
        else:
            self.app.config.overlay.map_style = self.var_map_style.get()
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
        ]
        for val, lbl in scopes:
            tk.Radiobutton(scope_card.body, text=lbl, variable=self.var_scope,
                           value=val, bg=CARD, fg=TEXT, selectcolor=CARD2,
                           activebackground=CARD, font=font(9)).pack(anchor='w', pady=1)

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

    def _on_toggle_map(self):
        self.app.config.overlay.map.visible = self.var_show_map.get()
        self.app.config.save()
        self.editor.refresh()

    def _on_toggle_tel(self):
        self.app.config.overlay.telemetry.visible = self.var_show_tel.get()
        self.app.config.save()
        self.editor.refresh()

    def _on_toggle_bike(self):
        self.app.config.overlay.is_bike = self.var_is_bike.get()
        self.app.config.save()

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

        scope    = self.var_scope.get()
        encoder  = self.app.gpu_encoder.get()
        crf      = self.app.quality_crf.get()
        workers  = self.app.worker_count.get()
        padding  = self.app.padding_secs.get()
        is_bike  = self.app.config.overlay.is_bike
        show_map = self.app.config.overlay.map.visible
        show_tel = self.app.config.overlay.telemetry.visible
        layout   = asdict(self.app.config.overlay)

        threading.Thread(
            target=self._export_bg,
            args=(items, scope, export_path, encoder, crf, workers,
                  padding, is_bike, show_map, show_tel, layout),
            daemon=True,
        ).start()

    def _export_bg(self, items, scope, export_path, encoder, crf, workers,
                   padding, is_bike, show_map, show_tel, layout) -> None:
        try:
            self._export_bg_inner(items, scope, export_path, encoder, crf, workers,
                                  padding, is_bike, show_map, show_tel, layout)
        except Exception as e:
            import traceback
            self.app.q.put(('export_log', f"\n✗ Unexpected error:\n{traceback.format_exc()}"))
            self.app.q.put(('export_done', False, f"Crashed: {e}"))

    def _export_bg_inner(self, items, scope, export_path, encoder, crf, workers,
                         padding, is_bike, show_map, show_tel, layout) -> None:
        from racebox_data import load_csv
        from video_renderer import render_lap, RenderJob, concat_videos

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
            offset   = item.get('offset', 0.0)

            if not csv_path or not os.path.exists(csv_path):
                log(f"Skipping: CSV not found: {csv_path}")
                done_jobs += 1
                continue

            log(f"\n── {os.path.basename(csv_path)}")

            try:
                sess = load_csv(csv_path)
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
        if kind == 'export_log':
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
