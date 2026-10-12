import pytest
from contextlib import nullcontext
from PIL import Image
from tracker import capture

@pytest.mark.parametrize('mode',['auto','manual'])
@pytest.mark.parametrize('race',[False,True])
def test_windows_adapter_rechecks_front_and_monitor(monkeypatch,mode,race):
    target=[dict(device='A',bbox=(0,0,100,100))];front=[42];calls=[]
    monkeypatch.setattr(capture,'physical_pixels',nullcontext)
    monkeypatch.setattr(capture,'apex_foreground',lambda bbox:front[0])
    monkeypatch.setattr(capture,'window_display',lambda hwnd:target[0])
    monkeypatch.setattr(capture,'apex_capture_bbox',lambda hwnd,bbox:(10,10,90,90))
    def grab(**kwargs):
        calls.append(kwargs)
        if race:
            if mode=='auto':target[0]=dict(device='B',bbox=(-100,0,0,100))
            else:front[0]=43
        return Image.new('RGB',(80,80))
    monkeypatch.setattr(capture.ImageGrab,'grab',grab)
    source=capture.WindowsFrameSource(None if mode=='auto' else (0,0,100,100))
    result=source.read()
    assert calls==[dict(bbox=(10,10,90,90),all_screens=True)]
    assert (result is None)==race
    front[0]=None;n=len(calls);assert source.read() is None and len(calls)==n
