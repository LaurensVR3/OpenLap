# overlay_editor.py — Visual drag/resize overlay element editor with live style previews

from __future__ import annotations
import queue
import threading
import tkinter as tk
from typing import Callable, Optional, Tuple
import numpy as np

from design_tokens import CARD, CARD2, BORDER, TEXT2, TEXT3, ACC, font
from app_config import OverlayLayout, OverlayElement

# Element display config: {key: (hex_colour, label)}
ELEM_STYLE = {
    'map':       ('#4f8ef7', 'Map'),
    'telemetry': ('#00d4ff', 'Telemetry'),
}
HANDLE_SIZE  = 7     # px half-size of resize corner handles
MIN_NORM     = 0.05  # minimum normalized w/h
SNAP_NORM    = 0.02  # snap-to-edge threshold (normalized)
PREVIEW_DEBOUNCE_MS = 250   # delay before triggering a preview re-render


class OverlayEditor(tk.Frame):
    """
    Canvas showing a video frame (or placeholder) with draggable/resizable
    overlay element boxes.  Each element renders a live preview of its
    current style using dummy data so users see the actual visualisation,
    not just a coloured rectangle.

    Normalised coordinates (0..1) are relative to the VIDEO FRAME, not the
    canvas, so letterboxed video never allows elements outside the frame.
    """

    def __init__(self, parent, layout: OverlayLayout,
                 on_change: Callable[[OverlayLayout], None], **kw):
        super().__init__(parent, bg=CARD, **kw)
        self._layout    = layout
        self._on_change = on_change
        self._bg_photo  = None   # PhotoImage — prevents GC
        self._bg_arr    = None   # raw numpy BGR for resizing

        # Video frame rect within canvas (ox, oy, dw, dh)
        self._frame_rect: Tuple[int, int, int, int] = (0, 0, 1, 1)

        # Drag state
        self._drag: Optional[dict] = None

        # Preview system
        self._previews:       dict[str, np.ndarray]          = {}
        self._preview_photos: dict[str, object]              = {}  # tk PhotoImage refs
        self._preview_q:      queue.Queue                    = queue.Queue()
        self._debounce_ids:   dict[str, Optional[str]]       = {}  # after() ids

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
        """Set background from a cv2 BGR numpy array (or None for placeholder)."""
        self._bg_arr = bgr_frame
        self._redraw()
        self._request_all_previews()

    def refresh(self) -> None:
        """Call when layout style/visibility changes — redraws and re-renders previews."""
        self._previews.clear()
        self._preview_photos.clear()
        self._redraw()
        self._request_all_previews()

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

        for key in ('map', 'telemetry'):
            elem = getattr(self._layout, key)
            if elem.visible:
                self._draw_element(key, elem)

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

    def _draw_element(self, key: str, elem: OverlayElement) -> None:
        colour, _label = ELEM_STYLE[key]
        x1, y1 = self._norm_to_px(elem.x, elem.y)
        x2, y2 = self._norm_to_px(elem.x + elem.w, elem.y + elem.h)
        pw, ph  = max(1, x2 - x1), max(1, y2 - y1)

        # ── Preview image (or fallback stipple) ───────────────────────────────
        preview = self._previews.get(key)
        if preview is not None:
            try:
                from PIL import Image, ImageTk
                img = Image.fromarray(preview, 'RGBA').resize(
                    (pw, ph), Image.LANCZOS)
                # Composite onto dark background so transparency looks right
                bg = Image.new('RGBA', (pw, ph), (13, 15, 24, 220))
                bg.alpha_composite(img)
                photo = ImageTk.PhotoImage(bg.convert('RGB'))
                self._canvas.create_image(x1, y1, anchor='nw', image=photo,
                                          tags=(key, f'{key}_body'))
                self._preview_photos[key] = photo
            except Exception:
                preview = None   # fall through to stipple

        if preview is None:
            self._canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=colour, stipple='gray25', outline='',
                tags=(key, f'{key}_body'))

        # ── Border + style name ───────────────────────────────────────────────
        self._canvas.create_rectangle(
            x1, y1, x2, y2,
            fill='', outline=colour, width=2,
            tags=(key, f'{key}_body'))

        style_name = getattr(self._layout, f'{key}_style', '')
        mid_x = (x1 + x2) // 2
        # Show style name near the bottom of the box so it doesn't obscure content
        label_y = max(y1 + 10, y2 - 12)
        self._canvas.create_text(
            mid_x, label_y,
            text=style_name, fill=colour, font=font(8),
            tags=(key, f'{key}_label'))

        # ── Resize handles ────────────────────────────────────────────────────
        for hx, hy, htag in [
            (x1, y1, 'NW'), (x2, y1, 'NE'),
            (x1, y2, 'SW'), (x2, y2, 'SE'),
        ]:
            hs = HANDLE_SIZE
            self._canvas.create_rectangle(
                hx - hs, hy - hs, hx + hs, hy + hs,
                fill=colour, outline='white', width=1,
                tags=(key, f'{key}_handle_{htag}'))

    # ── Preview rendering ─────────────────────────────────────────────────────

    def _request_all_previews(self) -> None:
        for key in ('map', 'telemetry'):
            elem = getattr(self._layout, key)
            if elem.visible:
                self._request_preview(key)

    def _request_preview(self, key: str) -> None:
        """Debounced: schedule a preview render PREVIEW_DEBOUNCE_MS from now."""
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
        elem  = getattr(self._layout, key)
        _, _, dw, dh = self._frame_rect
        pw = max(32, int(elem.w * dw))
        ph = max(16, int(elem.h * dh))
        style_name = getattr(self._layout, f'{key}_style', '')
        if not style_name:
            return
        is_bike = self._layout.is_bike
        threading.Thread(
            target=self._render_preview_worker,
            args=(key, style_name, pw, ph, is_bike),
            daemon=True,
        ).start()

    def _render_preview_worker(self, key: str, style_name: str,
                                pw: int, ph: int, is_bike: bool) -> None:
        try:
            from style_registry import render_style
            from overlay_utils  import dummy_telemetry_data, dummy_map_data
            if key == 'telemetry':
                data = dummy_telemetry_data(is_bike=is_bike)
                et   = 'telemetry'
            else:
                data = dummy_map_data()
                et   = 'map'
            rgba = render_style(et, style_name, data, pw, ph)
            self._preview_q.put((key, rgba))
        except Exception as exc:
            # Don't crash the editor if a style fails to render
            print(f'[OverlayEditor] Preview render failed ({key}/{style_name}): {exc}')

    def _poll_previews(self) -> None:
        """Drain the preview queue and redraw when new previews arrive."""
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
        """Return (key, mode, handle) or None."""
        for key in ('map', 'telemetry'):
            elem = getattr(self._layout, key)
            if not elem.visible:
                continue
            x1, y1 = self._norm_to_px(elem.x, elem.y)
            x2, y2 = self._norm_to_px(elem.x + elem.w, elem.y + elem.h)
            hs = HANDLE_SIZE + 2
            for hx, hy, hname in [
                (x1, y1, 'NW'), (x2, y1, 'NE'),
                (x1, y2, 'SW'), (x2, y2, 'SE'),
            ]:
                if abs(px - hx) <= hs and abs(py - hy) <= hs:
                    return key, 'resize', hname
            if x1 <= px <= x2 and y1 <= py <= y2:
                return key, 'move', None
        return None

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
        elem = getattr(self._layout, key)
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
        elem = getattr(self._layout, d['key'])

        if d['mode'] == 'move':
            elem.x = max(0.0, min(1.0 - d['ew'], d['ex'] + dx))
            elem.y = max(0.0, min(1.0 - d['eh'], d['ey'] + dy))
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

        self._redraw()

    def _snap(self, elem: OverlayElement) -> None:
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
            key = self._drag['key']
            self._snap(getattr(self._layout, key))
            self._drag = None
            self._redraw()
            self._request_preview(key)   # re-render at new size
            self._on_change(self._layout)
