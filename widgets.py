# widgets.py — reusable themed Tkinter widgets

import tkinter as tk
from tkinter import ttk, filedialog

from design_tokens import (
    BG, CARD, CARD2, BORDER, ACC, OK, ERR, TEXT, TEXT2, TEXT3, font
)


class Card(tk.Frame):
    def __init__(self, parent, title="", **kw):
        super().__init__(parent, bg=CARD, **kw)
        if title:
            tk.Label(self, text=title, bg=CARD, fg=TEXT2,
                     font=font(8, bold=True)).pack(anchor='w', padx=14, pady=(10, 0))
        self._body = tk.Frame(self, bg=CARD)
        self._body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

    @property
    def body(self):
        return self._body


class Btn(tk.Button):
    """Flat styled button."""
    def __init__(self, parent, text, command=None, accent=False, ok=False,
                 danger=False, small=False, **kw):
        bg = ACC if accent else (OK if ok else (ERR if danger else CARD2))
        fg = 'white' if (accent or ok or danger) else TEXT
        sz = 9 if small else 10
        super().__init__(parent, text=text, command=command or (lambda: None),
                         bg=bg, fg=fg, relief='flat', bd=0,
                         font=font(sz, bold=(accent or ok)),
                         padx=12, pady=5, cursor='hand2',
                         activebackground=ACC if accent else (OK if ok else CARD2),
                         activeforeground='white', **kw)
        self.bind('<Enter>', lambda e: self.config(bg=self._hover()))
        self.bind('<Leave>', lambda e: self.config(bg=bg))
        self._base_bg = bg

    def _hover(self):
        return {ACC: '#3a7be0', OK: '#16a34a', ERR: '#dc2626',
                CARD2: '#2d3252'}.get(self._base_bg, CARD2)


class Divider(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BORDER, height=1, **kw)


class Label(tk.Label):
    def __init__(self, parent, text='', muted=False, bold=False, size=10, **kw):
        super().__init__(parent, text=text, bg=kw.pop('bg', CARD),
                         fg=TEXT2 if muted else TEXT,
                         font=font(size, bold), **kw)


class BrowseRow(tk.Frame):
    """Label + entry + browse button in one row."""
    def __init__(self, parent, label, var, mode='dir', bg=CARD, **kw):
        super().__init__(parent, bg=bg, **kw)
        Label(self, text=label, bg=bg, muted=True,
              width=14, anchor='w').pack(side='left')
        e = tk.Entry(self, textvariable=var, bg=CARD2, fg=TEXT,
                     insertbackground=TEXT, relief='flat',
                     font=font(9), bd=0, highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=ACC)
        e.pack(side='left', fill='x', expand=True, padx=(6, 6), ipady=4)
        Btn(self, "Browse…", small=True,
            command=lambda: self._browse(var, mode)).pack(side='left')

    def _browse(self, var, mode):
        if mode == 'dir':
            d = filedialog.askdirectory()
            if d:
                var.set(d)
        elif mode == 'csv':
            f = filedialog.askopenfilename(
                filetypes=[("CSV", "*.csv"), ("All", "*.*")])
            if f:
                var.set(f)
        elif mode == 'video':
            f = filedialog.askopenfilenames(
                filetypes=[("Video", "*.mp4 *.mov *.avi *.mkv *.MP4 *.MOV"),
                           ("All", "*.*")])
            return f  # caller handles multi-select differently


class LogBox(tk.Frame):
    """Scrollable monospace log."""
    def __init__(self, parent, height=8, **kw):
        super().__init__(parent, bg=CARD, **kw)
        self.txt = tk.Text(self, bg="#080a14", fg="#88ff88",
                           font=font(9, mono=True), relief='flat',
                           state='disabled', wrap='word', height=height,
                           selectbackground=ACC, insertbackground=TEXT)
        vsb = tk.Scrollbar(self, orient='vertical', command=self.txt.yview,
                           bg=CARD2, troughcolor=CARD)
        self.txt.configure(yscrollcommand=vsb.set)
        self.txt.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

    def append(self, msg: str):
        self.txt.config(state='normal')
        self.txt.insert('end', msg + '\n')
        self.txt.see('end')
        self.txt.config(state='disabled')

    def clear(self):
        self.txt.config(state='normal')
        self.txt.delete('1.0', 'end')
        self.txt.config(state='disabled')


class ProgressBar(tk.Frame):
    """Slim progress bar + label."""
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=CARD, **kw)
        self._var = tk.DoubleVar(value=0)
        self._lbl = tk.Label(self, text="Ready", bg=CARD, fg=TEXT2,
                              font=font(9))
        self._lbl.pack(anchor='w', padx=2)
        track = tk.Frame(self, bg=BORDER, height=6)
        track.pack(fill='x', pady=(2, 0))
        track.pack_propagate(False)
        self._fill = tk.Frame(track, bg=ACC, height=6)
        self._fill.place(x=0, y=0, relheight=1, relwidth=0)
        track.bind('<Configure>', self._redraw)
        self._pct = 0

    def set(self, pct: float, msg: str = ""):
        self._pct = max(0, min(100, pct))
        self._fill.place(relwidth=self._pct / 100)
        if msg:
            self._lbl.config(text=msg)

    def _redraw(self, _e=None):
        self._fill.place(relwidth=self._pct / 100)

    def reset(self, msg="Ready"):
        self.set(0, msg)


class SectionHeader(tk.Frame):
    def __init__(self, parent, title, **kw):
        super().__init__(parent, bg=BG, **kw)
        tk.Label(self, text=title, bg=BG, fg=TEXT,
                 font=font(14, bold=True)).pack(side='left')
