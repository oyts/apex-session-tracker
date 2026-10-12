from types import SimpleNamespace
from tracker import app as module,windows


def test_selection_survives_primary_switch_unplug_and_reconnect(monkeypatch):
    a={'device':'A','bbox':(0,0,2560,1440),'primary':True}
    b={'device':'B','bbox':(-2560,0,0,1600),'primary':False}
    values={'monitor_device':'A','monitor':0,'monitor_mode':'manual'}
    obj=module.App.__new__(module.App)
    obj.store=SimpleNamespace(setting=lambda k,d=None:values.get(k,d),set_setting=lambda k,v:values.update({k:v}))
    obj.displays=[a,b]
    changes=[];obj.configure_automatic=lambda:changes.append(obj.selected_display())
    current=[dict(b,primary=True,bbox=(0,0,2560,1600)),dict(a,primary=False,bbox=(-2560,0,0,1440))]
    monkeypatch.setattr(module,'monitors',lambda:current)
    obj.refresh_displays()
    assert values['monitor']==1 and obj.selected_display()['device']=='A'
    assert obj.selected_display()['bbox']==(-2560,0,0,1440)
    obj.refresh_displays();assert len(changes)==1
    current=current[:1];obj.refresh_displays()
    assert obj.selected_display() is None and changes[-1] is None
    current=[a,b];obj.refresh_displays()
    assert obj.selected_display()==a and values['monitor']==0 and len(changes)==3


def test_capture_dpi_scope_restores_on_failure(monkeypatch):
    calls=[]
    class API:
        def __call__(self,context):
            calls.append(context.value if hasattr(context,'value') else context)
            return 123
    api=API()
    monkeypatch.setattr(windows.ctypes,'WinDLL',lambda *a,**k:SimpleNamespace(SetThreadDpiAwarenessContext=api))
    try:
        with windows.physical_pixels():raise OSError('fixture')
    except OSError:pass
    assert len(calls)==2 and calls[-1]==123


def test_follow_topology_refresh_keeps_episode_and_manual_choice(monkeypatch):
    a=dict(device='A',bbox=(0,0,2560,1440),primary=True)
    obj=module.App.__new__(module.App)
    values={'monitor_device':'A','monitor':0}  # Upgrade defaults to auto.
    obj.store=SimpleNamespace(setting=lambda k,d=None:values.get(k,d),set_setting=lambda k,v:values.update({k:v}))
    obj.displays=[a];obj.watcher=object()
    changes=[];obj.configure_automatic=lambda:changes.append(True)
    monkeypatch.setattr(module,'monitors',lambda:[])
    obj.refresh_displays()
    assert not changes and values['monitor_device']=='A'
    obj.watcher=None
    monkeypatch.setattr(module,'monitors',lambda:[a])
    obj.refresh_displays()
    assert changes==[True]
