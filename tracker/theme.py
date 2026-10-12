"""Native, code-drawn panels. No game artwork or screenshot assets required."""
import tkinter as tk
from tkinter import ttk

BG = '#0B0C0A'
PANEL = '#191A19'
TEXT = '#F2F0EC'
MUTED = '#ABA9A5'
ACCENT = '#DC3D42'
AMBER = '#FFB800'
BORDER = '#42433F'
FIELD = '#262725'


class ScrollBody(ttk.Frame):
    """Keep every action reachable on small displays and at high Windows scaling."""
    def __init__(self, window, padding=20):
        super().__init__(window)
        self.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0, width=900, height=650)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.slot = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self.canvas.bind('<Configure>', self.layout)
        self.content.bind('<Configure>', self.layout)
        window.bind('<MouseWheel>', self.wheel, add=True)
        window.layout_frame = self.content
        self.layout_state = None
        self.timer = self.after(100, self.track_requests)
        self.bind('<Destroy>', self.cancel_timer, add=True)

    def track_requests(self):
        # Embedded frames with an assigned height do not emit Configure when only
        # their requested height changes (e.g. a nickname starts wrapping).
        state = (self.content.winfo_reqheight(), self.canvas.winfo_width(), self.canvas.winfo_height())
        if state != self.layout_state:
            self.layout_state = state
            self.layout()
        self.timer = self.after(100, self.track_requests)

    def cancel_timer(self, event):
        if event.widget == self:
            self.after_cancel(self.timer)

    def layout(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        required = self.content.winfo_reqheight()
        overflow = required > height
        if overflow and not self.scroll.winfo_manager():
            self.scroll.pack(side='right', fill='y')
        elif not overflow and self.scroll.winfo_manager():
            self.scroll.pack_forget()
        self.canvas.itemconfigure(self.slot, width=width, height=max(height, required))
        self.canvas.configure(scrollregion=(0, 0, width, max(height, required)))

    def wheel(self, event):
        if isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox, ttk.Entry)):
            return
        if self.scroll.winfo_manager():
            self.canvas.yview_scroll(-int(event.delta/120), 'units')


class Brand(tk.Canvas):
    def __init__(self, master):
        super().__init__(master, bg=PANEL, bd=0, highlightthickness=0)
        self.pack(side='left', padx=(0, 22))
        self.set_dpi(96)

    def set_dpi(self, dpi):
        s = dpi/96
        self.configure(width=round(170*s), height=round(68*s))
        self.delete('all')
        self.create_polygon(*[n*s for n in (0, 0, 170, 0, 150, 68, 0, 68)], fill=ACCENT, outline='')
        self.create_line(*[n*s for n in (154, 68, 174, 0)], fill=ACCENT, width=2*s)
        self.create_text(72*s, 34*s, text='APEX', fill=TEXT,
                         font=('Bahnschrift SemiCondensed', -round(27*dpi/72), 'bold'))


def brand(master):
    return Brand(master)


class AngularPanel(tk.Canvas):
    def __init__(self, master, *, signal=ACCENT, padding=16, **kwargs):
        super().__init__(master, bg=BG, highlightthickness=0, bd=0, height=86, **kwargs)
        self.signal, self.padding = signal, padding
        self.base_padding, self.scale = padding, 1
        self.content = ttk.Frame(self, style='Panel.TFrame')
        self.slot = self.create_window(padding, padding, anchor='nw', window=self.content)
        self.bind('<Configure>', self.draw)
        self.content.bind('<Configure>', self.fit)

    def set_dpi(self, dpi):
        self.scale = dpi/96
        self.padding = round(self.base_padding*self.scale)
        self.coords(self.slot, self.padding, self.padding)
        self.fit()
        self.draw(type('Size', (), {'width': self.winfo_width(), 'height': self.winfo_height()})())

    def fit(self, _event=None):
        required = self.content.winfo_reqheight()+2*self.padding
        if int(self['height']) != required:
            self.configure(height=required)

    def draw(self, event):
        w, h, cut = event.width, event.height, round(14*self.scale)
        self.delete('surface')
        self.create_polygon(1, 1, w-cut, 1, w-1, cut, w-1, h-1, cut, h-1, 1, h-cut,
                            fill=PANEL, outline=BORDER, width=1, tags='surface')
        self.create_line(1, 1, min(90*self.scale, w/3), 1, fill=self.signal, width=3*self.scale, tags='surface')
        self.create_line(w-cut, 1, w-1, cut, fill=self.signal, width=2*self.scale, tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.slot, width=max(1, w-2*self.padding))


def install(root):
    root.configure(bg=BG)
    root.option_add('*Font', ('Microsoft YaHei UI', 10))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10))
    style.configure('TFrame', background=BG)
    style.configure('Panel.TFrame', background=PANEL)
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Panel.TLabel', background=PANEL, foreground=TEXT)
    style.configure('Metric.TLabel', background=PANEL, foreground=TEXT, font=('Consolas', 20, 'bold'))
    style.configure('MetricHeading.TLabel', background=PANEL, foreground=MUTED, font=('Microsoft YaHei UI', 9))
    style.configure('Average.TLabel', background=PANEL, foreground=AMBER, font=('Consolas', 20, 'bold'))
    style.configure('TButton', padding=(14, 8), background=FIELD, foreground=TEXT,
                    bordercolor=BORDER, borderwidth=1, relief='flat', focusthickness=1, focuscolor=TEXT)
    style.map('TButton', background=[('disabled', PANEL), ('active', '#3C3D39')],
              foreground=[('disabled', '#6F706B')])
    style.configure('Accent.TButton', background=ACCENT, foreground=TEXT, bordercolor=ACCENT)
    style.map('Accent.TButton', background=[('disabled', PANEL), ('active', '#F05155')])
    for widget in ('TEntry', 'TCombobox'):
        style.configure(widget, fieldbackground=FIELD, foreground=TEXT, padding=6,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        insertcolor=TEXT, arrowcolor=TEXT)
    style.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)])
    style.configure('TCheckbutton', background=BG, foreground=TEXT, indicatorcolor=FIELD)
    style.map('TCheckbutton', background=[('active', BG)], indicatorcolor=[('selected', ACCENT)])
    style.configure('TRadiobutton', background=BG, foreground=TEXT, indicatorcolor=FIELD,
                    font=('Microsoft YaHei UI', 10))
    style.map('TRadiobutton', background=[('active', BG)], indicatorcolor=[('selected', ACCENT)])
    style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    rowheight=36, borderwidth=0, font=('Microsoft YaHei UI', 10))
    style.configure('Treeview.Heading', background=FIELD, foreground=MUTED, padding=(10, 8),
                    font=('Microsoft YaHei UI', 9), relief='flat')
    style.map('Treeview', background=[('selected', '#54262A')], foreground=[('selected', TEXT)])
    style.configure('TScrollbar', background=BORDER, troughcolor=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, borderwidth=0, arrowcolor=MUTED)


def section(master, text):
    frame = ttk.Frame(master)
    frame.pack(fill='x', pady=(0, 8))
    tk.Frame(frame, bg=ACCENT, width=4).pack(side='left', fill='y', padx=(0, 10))
    ttk.Label(frame, text=text, font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left')
    return frame
