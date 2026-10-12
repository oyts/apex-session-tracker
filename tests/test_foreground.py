from types import SimpleNamespace

from tracker import windows


class Call:
    def __init__(self, function):
        self.function = function
    def __call__(self,*args):
        return self.function(*args)


def test_foreground_title_monitor_and_client_capture_boundary(monkeypatch):
    state = {'title':'Apex Legends','iconic':False,'rect':(90,90,1290,900),'origin':(100,120)}
    def title(hwnd,buffer,length):
        buffer.value = state['title']
        return len(buffer.value)
    def rect(hwnd,pointer):
        r = pointer._obj
        r.left,r.top,r.right,r.bottom = state['rect']
        return True
    def client(hwnd,pointer):
        r = pointer._obj
        r.left,r.top,r.right,r.bottom = 0,0,1180,780
        return True
    def origin(hwnd,pointer):
        pointer._obj.x,pointer._obj.y = state['origin']
        return True
    user = SimpleNamespace(GetForegroundWindow=Call(lambda:42),GetWindowTextW=Call(title),
                           GetWindowRect=Call(rect),IsIconic=Call(lambda h:state['iconic']),
                           GetClientRect=Call(client),ClientToScreen=Call(origin))
    monkeypatch.setattr(windows.ctypes,'WinDLL',lambda *a,**k:user)
    assert windows.apex_foreground((0,0,1920,1080)) == 42
    assert windows.apex_foreground((1920,0,3840,1080)) is None
    assert windows.apex_capture_bbox(42,(0,0,1920,1080)) == (100,120,1280,900)
    assert windows.apex_capture_bbox(42,(0,0,1000,800)) == (100,120,1000,800)
    state.update(rect=(-2450,90,-1250,900),origin=(-2440,120))
    assert windows.apex_foreground((-2560,0,0,1600))==42
    assert windows.apex_foreground((0,0,2560,1440)) is None
    assert windows.apex_capture_bbox(42,(-2560,0,0,1600))==(-2440,120,-1260,900)
    for value in ('Apex Legends - YouTube - Google Chrome','Apex 战绩记录器','Desktop',''):
        state['title'] = value
        assert windows.apex_foreground() is None
    state.update(title='Apex Legends',iconic=True)
    assert windows.apex_foreground() is None


def test_native_window_display_uses_intersection_no_nearest_fallback(monkeypatch):
    calls=[]; state={'present':True}
    def monitor(hwnd,flags):
        calls.append((hwnd,flags))
        return 99 if state['present'] else 0
    def info(handle,pointer):
        value=pointer._obj
        value.monitor.left,value.monitor.top,value.monitor.right,value.monitor.bottom=-2560,0,0,1600
        value.flags=0;value.device='DISPLAY1'
        return True
    user=SimpleNamespace(MonitorFromWindow=Call(monitor),GetMonitorInfoW=Call(info))
    monkeypatch.setattr(windows.ctypes,'WinDLL',lambda *a,**k:user)
    assert windows.window_display(42)==dict(device='DISPLAY1',bbox=(-2560,0,0,1600),primary=False)
    assert calls==[(42,0)]
    state['present']=False
    assert windows.window_display(42) is None
    assert windows.window_display(None) is None
