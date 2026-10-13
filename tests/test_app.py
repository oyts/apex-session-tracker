import time
import gc
import pytest
import tkinter as tk
from pathlib import Path

from tracker import app as app_module
from tracker.app import App, Review, Settings, DataManagement


SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "match-summary.png"


class FakeRecognizer:
    def __init__(self):
        self.calls = 0

    def recognize(self, _path):
        self.calls += 1
        return {"warnings": [], "elapsed": 0.01, "cards": [
            {"card": i, "name": name, "damage": damage, "kills": 0, "assists": 0,
             "score": 0.99, "name_score": 0.99}
            for i, (name, damage) in enumerate(zip(("左昵称", "示例缺□", "右昵称"), (174, 430, 182)))]}


def until(root, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert condition()


@pytest.fixture
def tk_cleanup():
    yield
    # Tk variables must finalize on the main thread after fixture teardown,
    # before the following concurrent SQLite test starts worker-side GC.
    gc.collect()


def test_startup_reconciles_ended_partial_roster_without_rereading(tk_cleanup, tmp_path, monkeypatch, tk_host):
    from tracker.store import Store
    from tests.test_discovery import frame
    store = Store(tmp_path)
    with store.db:
        store.bind_alias('A', 1)
    sid = store.start_session(mode=1)
    data = frame(('A', 'B', 'C'), (40, 50, 60))
    data['recognition_version'] = 6
    data['cards'][2]['name_score'] = .5
    store.add_capture(sid, str(SAMPLE), 'startup-first', data)
    data = frame(('D', 'A', 'E'), (70, 80, 90))
    data['recognition_version'] = 6
    store.add_capture(sid, str(SAMPLE), 'startup-second', data)
    store.end_session(sid)
    store.close()
    monkeypatch.setattr(app_module, 'Recognizer', FakeRecognizer)
    root = tk.Toplevel(tk_host)
    root.withdraw()
    app = App(root, tmp_path)
    try:
        until(root, lambda: app.engine_ready)
        assert app.store.totals(sid)[0] == 2
        assert app.store.totals(sid)[1][0]['damage'] == 120
        assert all('已记录' in app.history.item(i)['values'] for i in app.history.get_children())
        assert app.engine.calls == 0
    finally:
        app.close()


def test_gui_import_bind_correct_history_and_resume(tk_cleanup, tmp_path, monkeypatch, tk_host):
    from tracker.store import Store
    settings = Store(tmp_path)
    settings.set_setting('automatic', False)
    settings.close()
    monkeypatch.setattr(app_module, "Recognizer", FakeRecognizer)
    monkeypatch.setattr(app_module.messagebox,'showerror',lambda *args,**kwargs:pytest.fail(str(args)))
    root = tk.Toplevel(tk_host)
    root.withdraw()
    app = App(root, tmp_path)
    try:
        until(root, lambda: app.engine_ready)
        assert app.hotkey is None
        assert app.store.setting('hotkey') == 'Ctrl+End'
        # Keep the test independent of a user's concurrently running tracker.
        app.store.set_setting('hotkey','Ctrl+Alt+F24')
        app.start([1, 2, 3])
        sid = app.selected_session
        app._submit(sid, SAMPLE)
        until(root, lambda: not app.busy)
        cid = app.store.captures(sid)[0]["id"]
        assert app.store.capture(cid)["status"] == "pending"
        assert app.store.totals(sid)[0] == 0
        app._submit(sid, SAMPLE)
        until(root, lambda: not app.busy)
        assert app.engine.calls == 1
        assert len(app.store.captures(sid)) == 1
        assert '待确认' in app.status.get() and '未重复计入' in app.status.get()
        review = Review(app, app.store.capture(cid))
        for i, (selection, _values) in enumerate(review.rows):
            selection.set(review.options[(2, 0, 1)[i]])
        review.save()
        assert app.store.totals(sid)[0] == 1
        assert [r["damage"] for r in app.store.totals(sid)[1]] == [430, 182, 174]
        assert app.store.match_player("示例缺□") == 1
        review = Review(app, app.store.capture(cid))
        review.rows[1][1]["damage"].set("1430")
        review.save()
        assert app.store.totals(sid)[0] == 1
        assert app.store.totals(sid)[1][0]["damage"] == 1430
        app._submit(sid, SAMPLE)
        until(root, lambda: not app.busy)
        assert app.engine.calls == 1 and app.store.totals(sid)[1][0]['damage'] == 1430
        assert len(list(app.store.screenshots.glob('*.png'))) == 1
        app.store.discard(cid)
        app._submit(sid, SAMPLE)
        until(root, lambda: not app.busy)
        assert '已忽略' in app.status.get() and app.engine.calls == 1
        review = Review(app, app.store.capture(cid))
        assert str(review.save_button['state']) == 'disabled'
        review.restore()
        review.save()
        assert app.store.totals(sid)[0] == 1 and app.store.totals(sid)[1][0]['damage'] == 1430
        settings = Settings(app)
        settings.players[0][1].set("示例完整字")
        settings.save()
        assert "示例完整字" in app.player_labels[0]["text"]
        # This legacy hotkey routing test fixes a manual screen explicitly.
        app.store.set_setting('monitor_mode','manual')
        submitted = []
        monkeypatch.setattr(app, "_submit", lambda *_args: submitted.append(_args))
        app.busy = True
        app.capture_screen()
        app.capture_screen()
        assert submitted == []
        app.busy = False
        app.capture_screen()
        assert len(submitted) == 1
        app.end()
        # Exercise auto -> saved original -> OCR -> reconciliation through the GUI,
        # including queued work and end flushing. The foreground/capture fixture is
        # explicit; this does not claim actual Apex gameplay was tested.
        from tracker.monitor import SummaryWatcher
        from tests.test_automatic import Sequence
        app.store.set_setting('capture_mode','screen')  # injected fixed-screen capture fixture
        source = Sequence()
        source.image = app_module.open_image(SAMPLE)
        source.match = False
        def watcher_factory(emit,bbox,**kwargs):
            watcher = SummaryWatcher(emit,bbox,source,source.foreground,source.capture,**kwargs)
            watcher.start = lambda: None
            return watcher
        monkeypatch.setattr(app_module,'SummaryWatcher',watcher_factory)
        original_recognize = app.engine.recognize
        def recognize_summary(path):
            result = original_recognize(path)
            result['lines'] = [{'text':'总结','x':1075,'cy':44},
                               {'text':'击杀/助攻/击倒','x':239,'cy':475},
                               {'text':'造成伤害','x':235,'cy':548}]
            return result
        monkeypatch.setattr(app.engine,'recognize',recognize_summary)
        app.automatic.set(True)
        app.mode.set('3 人模式')
        app.start([1,2,3])
        sid = app.selected_session
        source.match = True
        app.watcher.step(); app.watcher.step()
        until(root,lambda: len(app.store.captures(sid)) == 1 and not app.busy)
        assert app.store.capture(app.store.captures(sid)[0]['id'])['result']['source'] == 'automatic'
        calls = app.engine.calls
        for _ in range(5):
            app.watcher.step()
        root.update()
        assert len(app.store.captures(sid)) == 1 and app.engine.calls == calls
        epoch = app.watcher.epoch
        app.configure_automatic()
        assert app.watcher.locked and app.watcher.epoch==epoch
        app.watcher.step();app.watcher.step();root.update()
        assert len(app.store.captures(sid))==1 and app.engine.calls==calls
        source.match = False
        app.watcher.step(); app.watcher.step()
        source.match = True
        app.watcher.step()  # Reopening the same summary after tabs remains locked.
        app.end()
        until(root,lambda: app.store.active_session() is None)
        assert len(app.store.captures(sid)) == 1 and app.engine.calls == calls
        assert not app.busy and not app.pending_auto
        # A template candidate without full OCR page evidence must not create a
        # history record or affect roster discovery. Archive its evidence locally.
        monkeypatch.setattr(app.engine,'recognize',original_recognize)
        app.start([1,2,3])
        sid = app.selected_session
        before = set(app.store.screenshots.glob('*.png'))
        calls = app.engine.calls
        app.watcher.step(); app.watcher.step()
        until(root,lambda: app.engine.calls > calls and not app.busy)
        assert not app.store.captures(sid)
        assert set(app.store.screenshots.glob('*.png')) == before
        assert len(list((tmp_path/'rejected-candidates').glob('*.png')))==1
        assert len(list((tmp_path/'rejected-candidates').glob('*.json')))==1
        assert app.watcher.available.is_set()
        app.end()
        assert app.hotkey is None
        assert app.store.active_session() is None
        assert len(app.history.get_children()) == 0  # Selected rejected-candidate session is empty.
        # Exercise one/two-person review with real widget options, excluding random teammates.
        app.automatic.set(False)
        monkeypatch.undo()
        monkeypatch.setattr(app_module, "Recognizer", FakeRecognizer)
        for count, participants in ((1, [2]), (2, [1, 3])):
            app.mode.set(f'{count} 人模式')
            app.change_mode()
            app.start(participants)
            sid = app.selected_session
            cid = app.store.add_capture(sid, str(SAMPLE), str(count), FakeRecognizer().recognize(SAMPLE))
            review = Review(app, app.store.capture(cid))
            assert [p['id'] for p in review.players] == participants
            for (selection, _values), pid in zip(review.rows, (3, 1, 2)):
                selection.set(next((label for label, identity in review.option_ids.items() if identity == pid), review.ignore))
            review.save()
            assert app.store.totals(sid)[0] == 1
            assert len(app.store.totals(sid)[1]) == count
            assert sum(label.winfo_manager() == 'pack' for label in app.player_labels) == count
            settings = Settings(app)
            settings.hotkey.set('Ctrl+Alt+F22')
            settings.save()
            assert app.hotkey.value == 'Ctrl+Alt+F22'
            app.end()
            assert app.hotkey is None
        settings = Settings(app)
        settings.hotkey.set('Shift+Home')
        settings.save()
        assert app.store.setting('hotkey') == 'Shift+Home'
        assert app.hotkey is None
        # Default user flow: mode only, no preselected identities; replay both matches.
        app.mode.set('2 人模式')
        app.change_mode()
        app.start()
        sid = app.selected_session
        data = FakeRecognizer().recognize(SAMPLE)
        app.events.put(('result', (sid, str(SAMPLE), 'gui-first', data)))
        until(root, lambda: len(app.store.captures(sid)) == 1)
        assert app.store.session_player_ids(sid) == []
        assert '首局已保留' in app.roster_caption.get()
        assert '—' in app.average_labels[0]['text']
        data = FakeRecognizer().recognize(SAMPLE)
        for card, name, damage in zip(data['cards'], ('路人二', '示例缺□', '左昵称'), (50, 500, 200)):
            card.update(name=name, damage=damage)
        app.events.put(('result', (sid, str(SAMPLE), 'gui-second', data)))
        until(root, lambda: app.store.totals(sid)[0] == 2)
        assert app.store.session_player_ids(sid) == [1, 3]
        assert app.average_labels[0]['text'] == '465.0'
        assert str(app.average_labels[0]['style']).endswith('Average.TLabel')
        assert root.tk.call('ttk::style', 'lookup', 'Average.TLabel', '-foreground') != root.tk.call('ttk::style', 'lookup', 'Panel.TLabel', '-foreground')
        app.end()
        # Exact pixels reuse a manual correction; even one differing pixel
        # survives identical OCR names/numbers/time/footer as a separate record.
        from tests.test_dedup import summary
        app.mode.set('1 人模式')
        app.start([1])
        sid = app.selected_session
        data = summary()
        first_digest=app_module.image_digest(app_module.open_image(SAMPLE))
        app.events.put(('result',(sid,str(SAMPLE),first_digest,data)))
        until(root,lambda: app.store.totals(sid)[0] == 1)
        cid = app.store.captures(sid)[0]['id']
        revised = app.store.capture(cid)['stats']
        revised[0]['damage'] = 1430
        app.store.confirm(cid,revised)
        extra = app.store.screenshots/'job-owned-new-file.png'
        extra.write_bytes(SAMPLE.read_bytes())
        app.busy = True
        app.events.put(('result',(sid,str(extra),first_digest,data)))
        until(root,lambda: not app.busy)
        assert len(app.store.captures(sid)) == 1 and not extra.exists()
        assert app.store.totals(sid)[1][0]['damage'] == 1430
        image=app_module.open_image(SAMPLE)
        r,g,b=image.getpixel((0,0));image.putpixel((0,0),(r^1,g,b))
        image.save(extra)
        app.events.put(('result',(sid,str(extra),app_module.image_digest(image),data)))
        until(root,lambda: len(app.store.captures(sid)) == 2)
        assert app.store.totals(sid)[0] == 2
        assert extra.is_file() and app.store.capture(cid)['stats'][0]['damage']==1430
        # Equal manually corrected numbers also save without a duplicate prompt.
        monkeypatch.setattr(app_module.messagebox,'askyesno',lambda *a,**k:pytest.fail('Numbers must not ask for duplicate confirmation'))
        second=app.store.captures(sid)[0]['id']
        review=Review(app,app.store.capture(second))
        review.rows[1][1]['damage'].set('1430');review.save()
        assert app.store.totals(sid)[1][0]['damage']==2860
        app.end()
        # Storage settings affect the actual saved screenshot, without moving old
        # originals. Cleanup runs off the UI thread and keeps totals reviewable.
        settings=Settings(app)
        chosen=tmp_path/'photo drive'/'ApexTrackerScreenshots'
        settings.image_directory.set(str(chosen));settings.save()
        assert app.store.screenshots==chosen
        app.mode.set('1 人模式');app.start([1])
        sid=app.selected_session
        app._submit(sid,SAMPLE)
        until(root,lambda:not app.busy)
        cid=app.store.captures(sid)[0]['id']
        path=Path(app.store.capture(cid)['image'])
        assert path.is_file() and path.parent==chosen
        app.store.confirm(cid,[{'card':1,'player_id':1,'damage':430,'kills':0,'assists':0}])
        app.end()
        cleanup=DataManagement(app)
        cleanup.kind.set('photos')
        monkeypatch.setattr(app_module.messagebox,'askyesno',lambda *a,**k:True)
        cleanup.clean()
        until(root,lambda:not app.busy)
        assert not path.exists() and app.store.totals(sid)[0]==1
        cleanup.window.destroy()
        review=Review(app,app.store.capture(cid))
        review.save()
        assert app.store.totals(sid)[1][0]['damage']==430
        cleanup=DataManagement(app)
        cleanup.kind.set('history');cleanup.clean()
        until(root,lambda:not app.busy)
        assert not app.store.sessions() and app.store.screenshots==chosen
        cleanup.window.destroy()
    finally:
        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        app.close()


def test_monitor_auto_manual_settings_persist_and_hotkey_target(tk_cleanup,tmp_path,monkeypatch,tk_host):
    from tracker.store import Store
    a=dict(device='A',bbox=(0,0,2560,1440),primary=True)
    b=dict(device='B',bbox=(-2560,0,0,1600),primary=False)
    current=[a,b]
    monkeypatch.setattr(app_module,'monitors',lambda:current)
    monkeypatch.setattr(app_module,'Recognizer',FakeRecognizer)
    monkeypatch.setattr(app_module.messagebox,'showerror',lambda *a,**k:pytest.fail(str(a)))
    class Hotkey:
        def __init__(self,value,callback):self.value=value
        def start(self):pass
        def stop(self):pass
    monkeypatch.setattr(app_module,'Hotkey',Hotkey)
    root=tk.Toplevel(tk_host);root.withdraw()
    app=App(root,tmp_path)
    try:
        until(root,lambda:app.engine_ready)
        dialog=Settings(app)
        assert dialog.monitor.current()==0  # First install/old settings upgrade.
        dialog.monitor.current(2);dialog.save()
        assert app.store.setting('monitor_mode')=='manual'
        assert app.selected_display()==b
        dialog=Settings(app);assert dialog.monitor.current()==2
        dialog.monitor.current(0);dialog.save()
        assert app.store.setting('monitor_mode')=='auto'
        assert app.store.setting('monitor_device')=='B'  # Manual preference retained.
        app.automatic.set(False);app.mode.set('1 人模式');app.change_mode();app.start([1])
        submitted=[];monkeypatch.setattr(app,'_submit',lambda *a:submitted.append(a))
        monkeypatch.setattr(app_module,'apex_foreground',lambda:None)
        app.capture_screen();assert not submitted
        assert 'Apex 位于前台' in app.status.get()
        monkeypatch.setattr(app_module,'apex_foreground',lambda:42)
        monkeypatch.setattr(app_module,'window_display',lambda hwnd:a)
        app.capture_screen();assert len(submitted)==1
        app.end()
        current.clear();app.refresh_displays()
        dialog=Settings(app);dialog.save()  # Auto can save without attached screens.
        assert app.store.setting('monitor_mode')=='auto'
    finally:app.close()
    store=Store(tmp_path)
    try:
        assert store.setting('monitor_mode')=='auto' and store.setting('monitor_device')=='B'
    finally:store.close()


@pytest.mark.parametrize('condition',['absent','stable','moved'])
def test_manual_worker_auto_target_and_race_do_not_save_wrong_screen(tmp_path,monkeypatch,condition):
    import queue
    from PIL import Image
    from tracker import windows
    app=App.__new__(App)
    app.store=type('Storage',(),{'screenshots':tmp_path,'directory':tmp_path})()
    from tracker.journal import EvidenceSpool
    app.spool=EvidenceSpool(tmp_path);app.processing_spool=None
    app.events=queue.Queue();app.engine=FakeRecognizer()
    a=dict(device='A',bbox=(0,0,100,100),primary=True)
    b=dict(device='B',bbox=(-100,0,0,100),primary=False)
    target=[a];captured=[]
    monkeypatch.setattr(app_module,'apex_foreground',lambda:None if condition=='absent' else 42)
    monkeypatch.setattr(app_module,'window_display',lambda hwnd:target[0])
    monkeypatch.setattr(windows,'apex_capture_bbox',lambda hwnd,bbox:(10,10,90,90))
    def grab(**kwargs):
        captured.append(kwargs)
        if condition=='moved':target[0]=b
        return Image.new('RGB',(80,80),'white')
    monkeypatch.setattr(app_module.ImageGrab,'grab',grab)
    app._process(1,None,None,({},[]))
    events=[]
    while not app.events.empty():events.append(app.events.get())
    if condition=='stable':
        assert captured==[dict(bbox=(10,10,90,90),all_screens=True)]
        assert app.engine.calls==1 and list(tmp_path.glob('*.png'))
        assert any(kind=='result' for kind,value in events)
    else:
        assert app.engine.calls==0 and not list(tmp_path.glob('*.png'))
        assert any(kind=='error' for kind,value in events)
        if condition=='absent':assert not captured
