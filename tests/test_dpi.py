import gc
import tkinter as tk
from tkinter import font as tkfont, ttk
import pytest

from tracker import app as module, dpi as dpi_module
from tracker.app import App, Settings
from tracker.theme import Brand


@pytest.fixture(autouse=True)
def release_tk_after_test():
    yield
    gc.collect()


def test_scale_round_trip_fonts_geometry_and_independent_dialog(tmp_path, monkeypatch, tk_host):
    current = {'root': 96, 'dialog': 144}
    root = tk.Toplevel(tk_host); root.withdraw()
    def metrics(window):
        return {'dpi': current['root' if window is root else 'dialog'], 'work': (0, 0, 2560, 1600)}
    monkeypatch.setattr(dpi_module, 'window_metrics', metrics)
    monkeypatch.setattr(module, 'Recognizer', lambda: None)
    app = App(root, tmp_path)
    try:
        root.update()
        initial_size = (root.winfo_width(), root.winfo_height())
        label = app.player_labels[0]
        original_font = tkfont.Font(root=root, font=label['font']).actual()
        font_size = lambda: int(root.tk.call('font', 'configure', label['font'], '-size'))
        global_scale = root.tk.call('tk', 'scaling')
        for value in (120, 144, 192, 144, 120, 96):
            current['root'] = value
            app.dpi.refresh(); root.update_idletasks()
            actual = tkfont.Font(root=root, font=label['font']).actual()
            assert font_size() == -round(12*value/72)
            assert actual['family'] == original_font['family']
            assert int(ttk.Style(root).lookup(app.history['style'], 'rowheight')) == round(36*value/96)
            for row in app.player_rows:
                assert row.padding == round(row.base_padding*value/96)
            assert root.tk.call('tk', 'scaling') == global_scale
        root.update()
        assert (root.winfo_width(), root.winfo_height()) == initial_size
        settings = Settings(app)
        settings.window.withdraw()
        settings.window.update_idletasks()
        assert settings.window.dpi_controller.dpi == 144
        assert font_size() == -16
        entry = settings.players[0][1]  # Values persist through per-window scaling.
        entry.set('缩放后昵称')
        current['dialog'] = 192
        settings.window.dpi_controller.refresh(); root.update_idletasks()
        assert entry.get() == '缩放后昵称'
        assert font_size() == -16
        settings.window.destroy()
    finally:
        app.close()
        gc.collect()


def test_small_negative_monitor_clamps_and_keeps_controls_reachable(tmp_path, monkeypatch, tk_host):
    from tracker.store import Store
    store = Store(tmp_path)
    sid = store.start_session([1, 2, 3])
    store.end_session(sid)
    store.close()
    root = tk.Toplevel(tk_host); root.withdraw()
    current = {'dpi': 192, 'work': (-1280, 0, 0, 1000)}
    positions = []
    monkeypatch.setattr(dpi_module, 'window_metrics', lambda window: current)
    monkeypatch.setattr(dpi_module, 'place_window', lambda window, x, y: positions.append((x, y)))
    monkeypatch.setattr(module, 'Recognizer', lambda: None)
    app = App(root, tmp_path)
    try:
        root.update()
        root.winfo_children()[0].layout()
        assert positions[-1][0] < 0
        assert root.winfo_width() <= 1200 and root.winfo_height() <= 880
        surface = root.winfo_children()[0]
        assert surface.scroll.winfo_manager() == 'pack'
        # Resize and scale cannot change model data or trigger game capture.
        assert not app.store.captures(sid) and app.watcher is None
        brand = next(w for w in app.dpi.widgets(root) if isinstance(w, Brand))
        assert int(brand.cget('height')) == 136
        assert all(row.widgets[1].winfo_reqwidth() < 1200-2*row.padding for row in app.player_rows)
    finally:
        app.close()
        gc.collect()
