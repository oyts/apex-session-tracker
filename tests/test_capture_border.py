import sys
from types import SimpleNamespace
import pytest
from tracker.window_capture import GraphicsWindowSource


def backend(monkeypatch,fail):
    attempts=[]
    class Capture:
        def __init__(self,**kwargs):self.options=kwargs;attempts.append(kwargs)
        def event(self,fn):pass
        def start_free_threaded(self):
            error=fail(self.options)
            if error:raise OSError(error)
            return SimpleNamespace(stop=lambda:None)
    monkeypatch.setitem(sys.modules,'windows_capture',SimpleNamespace(WindowsCapture=Capture))
    return attempts


@pytest.mark.parametrize('hide,border',[(True,False),(False,None)])
def test_preference_uses_official_property_only(monkeypatch,hide,border):
    calls=backend(monkeypatch,lambda _:None);source=GraphicsWindowSource(hide_border=hide)
    source._start(42)
    assert len(calls)==1 and calls[0]['draw_border'] is border
    assert calls[0]['window_hwnd']==42 and source.border_requested is border
    source.close()


def test_border_only_fallback_preserves_other_capture_options(monkeypatch):
    calls=backend(monkeypatch,lambda o:'border settings are not supported' if o['draw_border'] is False else None)
    source=GraphicsWindowSource();source._start(42)
    assert len(calls)==2 and calls[-1]['draw_border'] is None
    assert calls[-1]['minimum_update_interval']==250 and calls[-1]['cursor_capture'] is False
    source.close()


def test_border_and_other_optional_properties_fall_back_bounded(monkeypatch):
    def fail(o):
        if o['draw_border'] is False:return 'border settings are not supported'
        if o['minimum_update_interval'] is not None:return 'minimum update interval is not supported'
    calls=backend(monkeypatch,fail);source=GraphicsWindowSource();source._start(42)
    assert len(calls)==3 and all(calls[-1][k] is None for k in ('draw_border','cursor_capture','secondary_window','minimum_update_interval'))
    source.close()


def test_unrelated_capture_error_is_not_hidden(monkeypatch):
    calls=backend(monkeypatch,lambda _:'Invalid window handle')
    with pytest.raises(OSError,match='Invalid window handle'):GraphicsWindowSource()._start(42)
    assert len(calls)==1
