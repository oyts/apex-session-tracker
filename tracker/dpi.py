"""Per-window DPI for Tk 8.6; pixel fonts avoid interpreter-global scaling races."""
import logging
import time
from itertools import count
import tkinter as tk
from tkinter import font as tkfont, ttk

from .windows import window_metrics, place_window
_windows = count(1)


class DpiController:
    def __init__(self, window, on_change=None):
        self.window, self.on_change = window, on_change
        self.namespace = f'Window{next(_windows)}'
        window.dpi_controller = self
        self.dpi = int(window_metrics(window)['dpi'])
        self.fonts, self.records, self.specs = {}, {}, {}
        self.last_size = None
        self.logical_size = None
        self.resizing_until = 0
        self.timer = None
        self.stopped = False
        window.bind('<Destroy>', self.destroy, add=True)
        self.apply()
        self.timer = window.after(250, self.poll)

    def px(self, value):
        return round(value*self.dpi/96)

    def font(self, spec):
        key = str(spec)
        if key not in self.fonts:
            source = tkfont.Font(root=self.window, font=spec)
            base = source.actual()
            # Negative sizes already represent physical pixels, e.g. Tk fixed fonts.
            points = base['size'] if base['size'] > 0 else -base['size']*72/96
            self.fonts[key] = (tkfont.Font(root=self.window, **base), points)
            self.specs[str(self.fonts[key][0])] = spec
        result, points = self.fonts[key]
        pixels = -max(1, round(points*self.dpi/72))
        if result.cget('size') != pixels:
            result.configure(size=pixels)
        return result

    def widgets(self, parent):
        yield parent
        for child in parent.winfo_children():
            if not isinstance(child, tk.Toplevel):
                yield from self.widgets(child)

    def lengths(self, value):
        values = value if isinstance(value, (tuple, list)) else self.window.tk.splitlist(str(value))
        return tuple(self.px(float(str(n))) for n in values)

    def apply(self):
        style = ttk.Style(self.window)
        for widget in self.widgets(self.window):
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                continue
            record = self.records.setdefault(str(widget), {})
            keys = widget.keys()
            if 'font' in keys:
                current = str(widget.cget('font'))
                if current != record.get('scaled_font'):
                    record['font'] = self.specs.get(current, current or ('Microsoft YaHei UI', 10))
                font = self.font(record['font'])
                widget.configure(font=font)
                record['scaled_font'] = str(font)
            if 'style' in keys:
                default = widget.winfo_class()
                if isinstance(widget, ttk.Scrollbar):
                    default = str(widget.cget('orient')).title()+'.TScrollbar'
                base = record.setdefault('style', widget.cget('style') or default)
                target = f'{self.namespace}.Dpi{self.dpi}.{base}'
                style.configure(target, font=self.font(style.lookup(base, 'font') or ('Microsoft YaHei UI', 10)))
                padding = style.lookup(base, 'padding')
                if padding:
                    style.configure(target, padding=self.lengths(padding))
                if isinstance(widget, ttk.Treeview):
                    style.configure(target, rowheight=self.px(36))
                    style.configure(target+'.Heading', font=self.font(('Microsoft YaHei UI', 9)),
                                    padding=(self.px(10), self.px(8)))
                    columns = record.setdefault('columns', {c: (widget.column(c, 'width'), widget.column(c, 'minwidth'))
                                                            for c in widget['columns']})
                    for column, (width, minimum) in columns.items():
                        widget.column(column, width=self.px(width), minwidth=self.px(minimum))
                widget.configure(style=target)
            spacing = record.setdefault('spacing', {})
            for option in ('padding', 'padx', 'pady', 'insertwidth'):
                if option in keys:
                    base = spacing.setdefault(option, widget.cget(option))
                    if str(base):
                        widget.configure(**{option: self.lengths(base)})
            if 'wraplength' in keys and not getattr(widget, '_dpi_dynamic_wrap', False):
                base = spacing.setdefault('wraplength', widget.cget('wraplength'))
                if str(base):
                    widget.configure(wraplength=self.px(float(str(base))))
            manager = widget.winfo_manager()
            if manager in ('pack', 'grid'):
                info = widget.pack_info() if manager == 'pack' else widget.grid_info()
                base = record.setdefault('layout', {k: v for k, v in info.items() if k in ('padx', 'pady', 'ipadx', 'ipady')})
                options = {k: self.lengths(v) for k, v in base.items()}
                if manager == 'pack': widget.pack_configure(**options)
                else: widget.grid_configure(**options)
            if hasattr(widget, 'set_dpi'):
                widget.set_dpi(self.dpi)
            elif isinstance(widget, tk.Frame) and 'width' in keys:
                width = spacing.setdefault('width', widget.cget('width'))
                widget.configure(width=self.px(float(str(width))))
            if isinstance(widget, ttk.Combobox) and not record.get('popdown'):
                widget.bind('<ButtonPress-1>', lambda e, w=widget: self.window.after_idle(self.popdown, w), add=True)
                record['popdown'] = True
        if self.on_change:
            self.on_change()

    def popdown(self, widget):
        try:
            path = widget.tk.call('ttk::combobox::PopdownWindow', widget)
            widget.tk.call(str(path)+'.f.l', 'configure', '-font', widget.cget('font'))
        except tk.TclError:
            pass

    def fit(self, width, height, grow=True):
        window = self.window
        window.update_idletasks()
        left, top, right, bottom = window_metrics(window)['work']
        max_width = max(200, right-left-self.px(40))
        max_height = max(180, bottom-top-self.px(60))
        layout = getattr(window, 'layout_frame', window)
        if grow:
            width = max(width, layout.winfo_reqwidth()+self.px(30))
            height = max(height, layout.winfo_reqheight()+self.px(30))
        width, height = min(max_width, round(width)), min(max_height, round(height))
        if grow or self.logical_size is None:
            self.logical_size = (width*96/self.dpi, height*96/self.dpi)
        window.minsize(min(self.px(940), max_width), min(self.px(620), max_height))
        if window.state() not in ('zoomed', 'iconic'):
            self.resizing_until = time.monotonic()+.8
            window.geometry(f'{width}x{height}')
            x = max(left+self.px(10), min(window.winfo_x(), right-width-self.px(20)))
            y = max(top+self.px(10), min(window.winfo_y(), bottom-height-self.px(50)))
            place_window(window, x, y)
        self.last_size = (width, height)

    def refresh(self):
        dpi = int(window_metrics(self.window)['dpi'])
        if dpi != self.dpi:
            old = self.dpi
            logical = self.logical_size or (self.window.winfo_width()*96/old, self.window.winfo_height()*96/old)
            self.dpi = dpi
            self.apply()
            self.fit(logical[0]*dpi/96, logical[1]*dpi/96, grow=False)
            logging.info('UI DPI changed %s -> %s', old, dpi)
        elif self.window.winfo_width() > 1:
            size = (self.window.winfo_width(), self.window.winfo_height())
            if size != self.last_size and self.window.state() == 'normal' and time.monotonic() >= self.resizing_until:
                self.logical_size = (size[0]*96/dpi, size[1]*96/dpi)
            self.last_size = size

    def poll(self):
        try:
            self.refresh()
        except tk.TclError:
            return
        if not self.stopped:
            self.timer = self.window.after(250, self.poll)

    def destroy(self, event):
        if event.widget == self.window:
            self.stopped = True
            if self.timer:
                self.window.after_cancel(self.timer)
            # Release Tcl-backed values and named fonts on the owning UI thread.
            self.records.clear()
            self.fonts.clear()
            self.specs.clear()
            self.on_change = None
