# overlay_editor.py — Visual drag/resize overlay element editor with live style previews

from __future__ import annotations
import logging
import queue
import threading
import tkinter as tk
from typing import Callable, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

from design_tokens import CARD, CARD2, BORDER, TEXT2, TEXT3, ACC, font
from app_config import OverlayLayout, OverlayElement, GaugeConfig
from gauge_channels import GAUGE_COLOURS

HANDLE_SIZE          = 7      # px half-size of resize corner handles
MIN_NORM             = 0.04   # minimum normalized w/h
SNAP_NORM            = 0.02   # snap-to-screen-edge threshold (normalized)
SNAP_ELEM_NORM       = 0.015  # snap-to-element-edge threshold (normalized)
SNAP_SIZE_STEP       = 0.05   # size grid increment (normalized)
PREVIEW_DEBOUNCE_MS  = 250    # delay before triggering a preview re-render

MAP_COLOUR = '#4f8ef7'
GUIDE_COLOUR = '#00ffcc'


def _gauge_colour(idx: int) -> str:
    return GAUGE_COLOURS[idx % len(GAUGE_COLOURS)]


class OverlayEditor(tk.Frame):
    """
    Canvas showing a video frame (or placeholder) with draggable/resizable
    overlay element boxes.  Each element renders a live preview.

    Normalised coordinates (0..1) are relative to the VIDEO FRAME, not the
    canvas, so letterboxed video never allows elements outside the frame.
    """

    def __init__(self, parent, layout: OverlayLayout,
                 on_change: Callable[[OverlayLayout], None], **kw):
        super().__init__(parent, bg=CARD, **kw)
        self._layout    = layout
        self._on_change = on_change
        self._bg_photo  = None
        self._bg_arr    = None

        self._frame_rect: Tuple[int, int, int, int] = (0, 0, 1, 1)
        self._drag: Optional[dict] = None
        self._snap_guides: list = []   # list of ('v'|'h', norm_pos) for active snaps

        self._previews:       dict[str, np.ndarray] = {}
        self._preview_photos: dict[str, object]     = {}
        self._preview_q:      queue.Queue           = queue.Queue()
        self._debounce_ids:   dict[str, Optional[str]] = {}

        self._canvas = tk.Canvas(self, bg='#1a1d2e', highlightthickness=0,
                                 cursor='crosshair')
        self._canvas.pack(fill='both', expand=True)
        self._canvas.bind('<Configure>',       self._on_configure)
        self._canvas.bind('<Button-1>',        self._on_press)
        self._canvas.bind('<B1-Motion>',       self._on_motion)
        self._canvas.bind('<ButtonRelease-1>', self._on_release)
        self._canvas.bind('<Motion>',          self._on_hover)

        self._poll_previews()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_frame(self, bgr_frame) -> None:
        self._bg_arr = bgr_frame
        self._redraw()
        self._request_all_previews()

    def refresh(self) -> None:
        self._previews.clear()
        self._preview_photos.clear()
        self._redraw()
        self._request_all_previews()

    # ── Element access helpers ────────────────────────────────────────────────

    def _all_keys(self) -> list[str]:
        """Return all element keys: gauges first (on top), map last (underneath)."""
        keys = [f'gauge_{i}' for i in range(len(self._layout.gauges))]
        keys.append('map')
        return keys

    def _get_elem(self, key: str):
        if key == 'map':
            return self._layout.map
        i = int(key.split('_')[1])
        return self._layout.gauges[i]

    def _elem_colour(self, key: str) -> str:
        if key == 'map':
            return MAP_COLOUR
        return _gauge_colour(int(key.split('_')[1]))

    def _elem_label(self, key: str) -> str:
        if key == 'map':
            return f"Map · {self._layout.map_style}"
        i = int(key.split('_')[1])
        g = self._layout.gauges[i]
        return f"{g.channel} · {g.style}"

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _norm_to_px(self, nx: float, ny: float) -> Tuple[int, int]:
        ox, oy, dw, dh = self._frame_rect
        return int(ox + nx * dw), int(oy + ny * dh)

    def _px_to_norm(self, px: int, py: int) -> Tuple[float, float]:
        ox, oy, dw, dh = self._frame_rect
        return (px - ox) / dw, (py - oy) / dh

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _on_configure(self, event=None) -> None:
        self._redraw()
        self._request_all_previews()

    def _redraw(self, _event=None) -> None:
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw < 2 or ch < 2:
            return
        self._canvas.delete('all')

        if self._bg_arr is not None:
            self._draw_bg(cw, ch)
        else:
            self._draw_placeholder(cw, ch)

        # Draw map first (bottom), then gauges on top
        if self._layout.map.visible:
            self._draw_element('map', self._layout.map)
        for i, g in enumerate(self._layout.gauges):
            if g.visible:
                self._draw_element(f'gauge_{i}', g)

        # Snap alignment guide lines drawn on top of everything
        if self._snap_guides:
            self._draw_snap_guides()

    def _draw_bg(self, cw: int, ch: int) -> None:
        from PIL import Image, ImageTk
        import cv2
        frame = self._bg_arr
        fh, fw = frame.shape[:2]
        scale  = min(cw / fw, ch / fh)
        dw, dh = max(1, int(fw * scale)), max(1, int(fh * scale))
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        self._frame_rect = (ox, oy, dw, dh)
        rgb = cv2.cvtColor(cv2.resize(frame, (dw, dh)), cv2.COLOR_BGR2RGB)
        img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self._canvas.create_image(ox, oy, anchor='nw', image=img)
        self._bg_photo = img

    def _draw_placeholder(self, cw: int, ch: int) -> None:
        self._frame_rect = (0, 0, cw, ch)
        self._canvas.create_rectangle(0, 0, cw, ch, fill='#0d0f18', outline='')
        self._canvas.create_text(
            cw // 2, ch // 2,
            text="Load a preview frame from the Export page\n"
                 "to see your overlay on actual video",
            fill=TEXT3, font=font(9), justify='center')

    def _draw_element(self, key: str, elem) -> None:
        colour = self._elem_colour(key)
        label  = self._elem_label(key)
        x1, y1 = self._norm_to_px(elem.x, elem.y)
        x2, y2 = self._norm_to_px(elem.x + elem.w, elem.y + elem.h)
        pw, ph  = max(1, x2 - x1), max(1, y2 - y1)

        preview = self._previews.get(key)
        if preview is not None:
            try:
                from PIL import Image, ImageTk
                img = Image.fromarray(preview, 'RGBA').resize((pw, ph), Image.LANCZOS)
                bg  = Image.new('RGBA', (pw, ph), (13, 15, 24, 220))
                bg.alpha_composite(img)
                photo = ImageTk.PhotoImage(bg.convert('RGB'))
                self._canvas.create_image(x1, y1, anchor='nw', image=photo,
                                          tags=(key, f'{key}_body'))
                self._preview_photos[key] = photo
            except Exception:
                preview = None

        if preview is None:
            self._canvas.create_rectangle(x1, y1, x2, y2,
                fill=colour, stipple='gray25', outline='',
                tags=(key, f'{key}_body'))

        self._canvas.create_rectangle(x1, y1, x2, y2,
            fill='', outline=colour, width=2,
            tags=(key, f'{key}_body'))

        mid_x  = (x1 + x2) // 2
        label_y = max(y1 + 10, y2 - 12)
        self._canvas.create_text(mid_x, label_y,
            text=label, fill=colour, font=font(8),
            tags=(key, f'{key}_label'))

        for hx, hy, htag in [(x1,y1,'NW'),(x2,y1,'NE'),(x1,y2,'SW'),(x2,y2,'SE')]:
            hs = HANDLE_SIZE
            self._canvas.create_rectangle(hx-hs, hy-hs, hx+hs, hy+hs,
                fill=colour, outline='white', width=1,
                tags=(key, f'{key}_handle_{htag}'))

    def _draw_snap_guides(self) -> None:
        """Draw cyan dashed alignment guides within the frame area."""
        ox, oy, dw, dh = self._frame_rect
        for axis, pos in self._snap_guides:
            if axis == 'v':
                px = int(ox + pos * dw)
                self._canvas.create_line(px, oy, px, oy + dh,
                    fill=GUIDE_COLOUR, width=1, dash=(5, 3))
            else:
                py = int(oy + pos * dh)
                self._canvas.create_line(ox, py, ox + dw, py,
                    fill=GUIDE_COLOUR, width=1, dash=(5, 3))

    # ── Preview rendering ─────────────────────────────────────────────────────

    def _request_all_previews(self) -> None:
        if self._layout.map.visible:
            self._request_preview('map')
        for i, g in enumerate(self._layout.gauges):
            if g.visible:
                self._request_preview(f'gauge_{i}')

    def _request_preview(self, key: str) -> None:
        if key in self._debounce_ids and self._debounce_ids[key]:
            try:
                self.after_cancel(self._debounce_ids[key])
            except Exception:
                pass
        self._debounce_ids[key] = self.after(
            PREVIEW_DEBOUNCE_MS, lambda k=key: self._launch_preview_thread(k))

    def _launch_preview_thread(self, key: str) -> None:
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw < 2 or ch < 2:
            return
        elem = self._get_elem(key)
        _, _, dw, dh = self._frame_rect
        pw = max(32, int(elem.w * dw))
        ph = max(16, int(elem.h * dh))
        is_bike = self._layout.is_bike
        theme   = getattr(self._layout, 'theme', 'Dark')

        if key == 'map':
            style = self._layout.map_style
            channel = None
        else:
            i = int(key.split('_')[1])
            g = self._layout.gauges[i]
            style   = g.style
            channel = g.channel

        threading.Thread(
            target=self._render_preview_worker,
            args=(key, style, channel, pw, ph, is_bike, theme),
            daemon=True,
        ).start()

    def _render_preview_worker(self, key: str, style_name: str, channel: Optional[str],
                                pw: int, ph: int, is_bike: bool,
                                theme: str = 'Dark') -> None:
        try:
            from style_registry import render_style
            from overlay_utils  import dummy_map_data
            from gauge_channels import dummy_gauge_data

            if key == 'map':
                data = dummy_map_data()
                data['_theme'] = theme
                rgba = render_style('map', style_name, data, pw, ph)
            else:
                data = dummy_gauge_data(channel or 'speed')
                data['is_bike'] = is_bike
                data['_theme']  = theme
                rgba = render_style('gauge', style_name, data, pw, ph)

            self._preview_q.put((key, rgba))
        except Exception as exc:
            logger.warning('Preview render failed (%s/%s): %s', key, style_name, exc)

    def _poll_previews(self) -> None:
        updated = False
        try:
            while True:
                key, rgba = self._preview_q.get_nowait()
                self._previews[key] = rgba
                updated = True
        except queue.Empty:
            pass
        if updated:
            self._redraw()
        self.after(100, self._poll_previews)

    # ── Hit testing ───────────────────────────────────────────────────────────

    def _hit_test(self, px: int, py: int):
        """Return (key, mode, handle) or None.  Gauges checked before map."""
        for key in self._all_keys():
            elem = self._get_elem(key)
            if not elem.visible:
                continue
            x1, y1 = self._norm_to_px(elem.x, elem.y)
            x2, y2 = self._norm_to_px(elem.x + elem.w, elem.y + elem.h)
            hs = HANDLE_SIZE + 2
            for hx, hy, hname in [(x1,y1,'NW'),(x2,y1,'NE'),(x1,y2,'SW'),(x2,y2,'SE')]:
                if abs(px - hx) <= hs and abs(py - hy) <= hs:
                    return key, 'resize', hname
            if x1 <= px <= x2 and y1 <= py <= y2:
                return key, 'move', None
        return None

    # ── Snapping helpers ──────────────────────────────────────────────────────

    def _apply_move_snap(self, key: str, elem) -> None:
        """
        Snap elem to nearby elements' edges and centres during a move.
        Updates self._snap_guides with guide line specs for drawing.
        """
        best_dx      = None
        best_dy      = None
        best_x_guide = None
        best_y_guide = None
        dist_x       = SNAP_ELEM_NORM
        dist_y       = SNAP_ELEM_NORM

        # Snap points on the dragged element: (current_coord, offset_from_elem_x)
        e_xs = [(elem.x,              0.0),
                (elem.x + elem.w,     elem.w),
                (elem.x + elem.w / 2, elem.w / 2)]
        e_ys = [(elem.y,              0.0),
                (elem.y + elem.h,     elem.h),
                (elem.y + elem.h / 2, elem.h / 2)]

        for other_key in self._all_keys():
            if other_key == key:
                continue
            o = self._get_elem(other_key)
            if not o.visible:
                continue
            o_xs = [o.x, o.x + o.w, o.x + o.w / 2]
            o_ys = [o.y, o.y + o.h, o.y + o.h / 2]

            for ex, x_offset in e_xs:
                for ox in o_xs:
                    d = abs(ex - ox)
                    if d < dist_x:
                        dist_x       = d
                        best_dx      = ox - x_offset   # new elem.x
                        best_x_guide = ox

            for ey, y_offset in e_ys:
                for oy in o_ys:
                    d = abs(ey - oy)
                    if d < dist_y:
                        dist_y       = d
                        best_dy      = oy - y_offset   # new elem.y
                        best_y_guide = oy

        guides = []
        if best_dx is not None:
            elem.x = max(0.0, min(1.0 - elem.w, best_dx))
            guides.append(('v', best_x_guide))
        if best_dy is not None:
            elem.y = max(0.0, min(1.0 - elem.h, best_dy))
            guides.append(('h', best_y_guide))
        self._snap_guides = guides

    def _apply_size_snap(self, elem) -> None:
        """Round element size to the nearest SNAP_SIZE_STEP grid increment."""
        elem.w = max(SNAP_SIZE_STEP, round(elem.w / SNAP_SIZE_STEP) * SNAP_SIZE_STEP)
        elem.h = max(SNAP_SIZE_STEP, round(elem.h / SNAP_SIZE_STEP) * SNAP_SIZE_STEP)

    # ── Mouse events ─────────────────────────────────────────────────────────

    def _on_hover(self, event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit is None:
            self._canvas.config(cursor='crosshair')
        elif hit[1] == 'resize':
            cursors = {'NW': 'size_nw_se', 'NE': 'size_ne_sw',
                       'SW': 'size_ne_sw', 'SE': 'size_nw_se'}
            self._canvas.config(cursor=cursors.get(hit[2], 'sizing'))
        else:
            self._canvas.config(cursor='fleur')

    def _on_press(self, event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit is None:
            return
        key, mode, handle = hit
        elem = self._get_elem(key)
        _, _, dw, dh = self._frame_rect
        self._drag = {
            'key': key, 'mode': mode, 'handle': handle,
            'mx': event.x, 'my': event.y,
            'ex': elem.x,  'ey': elem.y,
            'ew': elem.w,  'eh': elem.h,
            'dw': dw,      'dh': dh,
        }

    def _on_motion(self, event) -> None:
        if not self._drag:
            return
        d  = self._drag
        dx = (event.x - d['mx']) / d['dw']
        dy = (event.y - d['my']) / d['dh']
        elem = self._get_elem(d['key'])

        self._snap_guides = []   # reset guides each frame

        if d['mode'] == 'move':
            elem.x = max(0.0, min(1.0 - d['ew'], d['ex'] + dx))
            elem.y = max(0.0, min(1.0 - d['eh'], d['ey'] + dy))
            self._apply_move_snap(d['key'], elem)
        else:
            h = d['handle']
            if 'E' in h:
                elem.w = max(MIN_NORM, min(1.0 - d['ex'], d['ew'] + dx))
            if 'W' in h:
                new_w  = max(MIN_NORM, d['ew'] - dx)
                new_x  = max(0.0, d['ex'] + (d['ew'] - new_w))
                elem.w = d['ew'] - (new_x - d['ex'])
                elem.w = max(MIN_NORM, elem.w)
                elem.x = new_x
            if 'S' in h:
                elem.h = max(MIN_NORM, min(1.0 - d['ey'], d['eh'] + dy))
            if 'N' in h:
                new_h  = max(MIN_NORM, d['eh'] - dy)
                new_y  = max(0.0, d['ey'] + (d['eh'] - new_h))
                elem.h = d['eh'] - (new_y - d['ey'])
                elem.h = max(MIN_NORM, elem.h)
                elem.y = new_y
            # Snap width and height to size grid
            self._apply_size_snap(elem)

        self._redraw()

    def _snap(self, elem) -> None:
        """Snap element to screen edges on release."""
        if elem.x < SNAP_NORM:
            elem.x = 0.0
        if elem.x + elem.w > 1.0 - SNAP_NORM:
            elem.x = 1.0 - elem.w
        if elem.y < SNAP_NORM:
            elem.y = 0.0
        if elem.y + elem.h > 1.0 - SNAP_NORM:
            elem.y = 1.0 - elem.h

    def _on_release(self, _event) -> None:
        if self._drag:
            key  = self._drag['key']
            elem = self._get_elem(key)
            self._snap(elem)
            self._snap_guides = []
            self._drag = None
            self._redraw()
            self._request_preview(key)
            self._on_change(self._layout)
