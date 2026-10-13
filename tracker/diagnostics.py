"""Explicit packaged-build smoke test; uses temporary data, never the user's history."""
import ctypes
import json
import tempfile
import time
import tkinter as tk
from pathlib import Path


def changed_summary(sample, result, output, changes):
    """Create an explicitly synthetic fixture by replacing measured text boxes."""
    from PIL import ImageDraw, ImageFont
    from .ocr import open_image
    image = open_image(sample)
    scale = image.width / min(2000, image.width)
    draw = ImageDraw.Draw(image)
    for old_text, new_text in changes.items():
        line = next(line for line in result['lines'] if line['text'] == old_text)
        x, y, right, bottom = [round(line[key]*scale) for key in ('x', 'y', 'right', 'bottom')]
        draw.rectangle((x-4, y-4, right+4, bottom+4), fill='black')
        font = ImageFont.truetype('arial.ttf', max(16, round((bottom-y)*.90)))
        draw.text((x, y), new_text, font=font, fill='white', anchor='lt')
    image.save(output)


def run_roster_smoke(output: Path):
    """Hidden UI with synthetic saved OCR; no foreground changes or screen capture."""
    from .app import App
    from .store import Store
    report = {'ok': False, 'synthetic_ocr_fixture': True, 'screen_capture_requested': False}
    try:
        with tempfile.TemporaryDirectory(prefix='apex-roster-diagnostic-') as folder:
            directory = Path(folder)
            store = Store(directory)
            store.set_setting('automatic', False)
            store.set_setting('mode', 1)
            with store.db:
                store.bind_alias('BoundPlayer', 1)
            def data(names, damages):
                return {'cards': [dict(card=i, name=name, name_score=.99, score=.99,
                                      damage=damage, kills=0, assists=0)
                                  for i, (name, damage) in enumerate(zip(names, damages))],
                        'warnings': [], 'recognition_version': 6}
            previous = store.start_session([1])
            cid = store.add_capture(previous, '', 'old', data(['BoundPlayer'], [1430]))
            store.confirm(cid, [dict(card=0, player_id=1, damage=1430, kills=1, assists=0)], manual=True)
            baseline = store.capture(cid)['stats']
            store.end_session(previous)
            sid = store.start_session(mode=1)
            first = data(['UnclearStranger', 'BoundPlayer', 'FirstStranger'], [108, 229, 368])
            first['cards'][0]['name_score'] = .53
            first['cards'][1]['kills'] = 1
            store.add_capture(sid, '', 'first', first)
            store.add_capture(sid, '', 'second', data(['SecondStranger', 'ThirdStranger', 'BoundPlayer'], [510, 50, 227]))
            store.end_session(sid)
            repair_sid=store.start_session([1])
            repair_ids=[]
            for i in range(3):
                rid=store.add_capture(repair_sid,'',f'animated-copy-{i}',data(['BoundPlayer'],[11]))
                store.confirm(rid,[dict(card=0,player_id=1,damage=11,kills=1,assists=0)])
                repair_ids.append(rid)
            repair_stats={rid:store.capture(rid)['stats'] for rid in repair_ids}
            store.set_setting('verified_match_repairs',[dict(reason='user_verified_same_match',ids=repair_ids,
                                hashes={str(rid):store.capture(rid)['hash'] for rid in repair_ids})])
            store.end_session(repair_sid)
            zero_sid=store.start_session([1])
            store.add_capture(zero_sid,'','different-zero-image-1',data(['FirstZeroStranger','BoundPlayer','AnotherZeroStranger'],[65,0,150]))
            store.add_capture(zero_sid,'','different-zero-image-2',data(['NewZeroStranger','BoundPlayer','LastZeroStranger'],[239,0,126]))
            store.end_session(zero_sid)
            store.close()
            root = tk.Tk()
            root.withdraw()
            app = App(root, directory)
            try:
                deadline = time.monotonic()+60
                while not app.engine_ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                assert app.engine_ready
                count, totals = app.store.totals(sid)
                assert count == 2 and len(totals) == 1
                assert (totals[0]['damage'], totals[0]['kills'], totals[0]['assists'], totals[0]['average_damage']) == (456, 1, 0, 228)
                assert app.store.capture(cid)['stats'] == baseline
                assert app.store.totals(repair_sid)[0]==1
                assert len(app.store.captures(repair_sid))==3
                assert all(app.store.capture(rid)['stats']==repair_stats[rid] for rid in repair_ids)
                assert not app.store.setting('verified_match_repairs')
                assert app.store.totals(zero_sid)[0]==2
                assert app.store.totals(zero_sid)[1][0]['damage']==0
                assert len(app.history.get_children()) == 2
                assert all(app.history.item(i)['values'][1] == '已记录' for i in app.history.get_children())
                assert app.watcher is None and app.hotkey is None
                from .windows import window_awareness
                awareness = window_awareness(root)
                assert awareness in (None, 2), awareness
                from . import dpi as dpi_module
                native_metrics = dpi_module.window_metrics
                profile = {'dpi': 96, 'work': (0, 0, 2560, 1600)}
                dpi_module.window_metrics = lambda window: profile
                verified = []
                try:
                    for dpi in (96, 120, 144, 192, 96):
                        profile['dpi'] = dpi
                        app.dpi.refresh(); root.update_idletasks()
                        size = int(root.tk.call('font', 'configure', app.player_labels[0]['font'], '-size'))
                        assert size == -round(12*dpi/72)
                        verified.append(dpi)
                    assert app.store.capture(cid)['stats'] == baseline
                finally:
                    dpi_module.window_metrics = native_metrics
                following = monitor_follow_fixture(root)
                report.update(ok=True, hidden_ui=True, ended_history_replayed=True,
                              card_positions_changed=True, games=2, damage=456, kills=1,
                              assists=0, average_damage=228, manual_stats_preserved=True,
                              local_model_loaded=True, simulated_dpi_profiles=verified,
                              per_window_font_dpi_verified=True, native_dpi_awareness=awareness,
                              monitor_follow=following, equal_numbers_distinct_images_counted=True,
                              equal_zero_games=2, verified_duplicate_handoff=True,
                              duplicate_original_stats_preserved=True)
            finally:
                app.close()
    except Exception as error:
        import traceback
        report.update(ok=False, error=str(error), traceback=traceback.format_exc())
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report['ok']


def monitor_follow_fixture(root):
    """Real own HWND monitor metadata; synthetic frames, no screen sampling."""
    from PIL import Image
    from .windows import monitors, window_handle, window_display, place_window
    from .monitor import SummaryWatcher
    displays = monitors()
    assert displays
    hwnd = window_handle(root)
    active = [hwnd]
    events = []
    class Detector:
        match = True
        def detect(self, image):
            return {'confidence': .99} if self.match else None
    detector = Detector()
    watcher = SummaryWatcher(lambda k,v: events.append((k,v)), None, detector,
                             foreground=lambda _: active[0],
                             capture=lambda: Image.new('RGB', (32,32), 'white' if watcher.cycle.generation==0 else 'grey'))
    visited = []
    count = lambda: sum(k=='frame' for k,v in events)
    for display in displays + displays[:1]:
        x,y,_,_ = display['bbox']
        place_window(root,x+40,y+40)
        root.update_idletasks()
        assert window_display(hwnd)['device'] == display['device']
        watcher.step(); watcher.step()
        assert watcher.bbox == display['bbox']
        assert count() == 1
        watcher.available.set()
        visited.append(display['device'])
    active[0] = None; watcher.step(); watcher.step()
    active[0] = hwnd; watcher.step()
    assert count() == 1
    detector.match = False; watcher.step(); watcher.step()
    assert watcher.locked and count()==1
    detector.match = True; watcher.step(); watcher.step()
    assert count()==1  # Summary reopened after a menu/tab, not a new match.
    synthetic_gameplay_transition(watcher)
    detector.match = True; watcher.step(); watcher.step()
    assert count() == 2
    return dict(ok=True, native_own_window_monitor_metadata=True,
                synthetic_frames_and_detector=True, foreground_gate_substituted=True,
                devices=visited, same_page_once_across_moves=True, next_page_captured=True,
                tab_reopen_does_not_rearm=True, positive_gameplay_gate_synthetic=True,
                screen_capture_requested=False, actual_apex_gameplay_tested=False)


def synthetic_gameplay_transition(watcher):
    """Explicit test observations only, never called by normal application flow."""
    watcher.accept_summary(dict(match_key=watcher.epoch))  # synthetic successful full OCR
    for stamp in (time.monotonic()-3,time.monotonic()-1):
        token=(watcher.probe_generation,stamp)
        watcher.probe_pending=token
        watcher.submit_gameplay(token,dict(kind='selection',evidence=['synthetic fixture selection']))
        watcher._gameplay_results()


def run_window_capture_smoke(output):
    """Only create/capture our own non-activating native STATIC window.

    The packaged WGC extension must produce actual white/black client pixels.
    This is intentionally opt-in, never run by normal startup or headless CI.
    """
    import ctypes
    from ctypes import wintypes
    from .window_capture import GraphicsWindowSource
    from .windows import window_client_crop
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetForegroundWindow.restype=wintypes.HWND
    user.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
                                  ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,
                                  wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
    user.CreateWindowExW.restype=wintypes.HWND
    user.DestroyWindow.argtypes=[wintypes.HWND];user.UpdateWindow.argtypes=[wintypes.HWND]
    before=user.GetForegroundWindow();hwnd=None;source=GraphicsWindowSource();report={'ok':False}
    try:
        hwnd=user.CreateWindowExW(0x08000080,'STATIC','Apex tracker own capture diagnostic',
                                  0x80000000|0x10000000|6,40,40,180,100,None,None,None,None)
        if not hwnd:raise OSError('Cannot create own non-activating test window')
        child=user.CreateWindowExW(0,'STATIC','',0x40000000|0x10000000|4,40,20,60,40,hwnd,None,None,None)
        if not child:raise OSError('Cannot create diagnostic color patch')
        user.UpdateWindow(hwnd);user.UpdateWindow(child)
        assert user.GetForegroundWindow()==before,'Own test window unexpectedly changed foreground'
        source._start(hwnd);deadline=time.monotonic()+8;image=None
        while time.monotonic()<deadline:
            with source.lock:image=source.latest.copy() if source.latest else None
            if image is not None:break
            time.sleep(.05)
        assert image is not None,'No actual WGC frame received'
        bounds=window_client_crop(hwnd,image.size);assert bounds,'Invalid client coordinates'
        image=image.crop(bounds)
        report['observed']=[image.getpixel((10,10)),image.getpixel((60,40))]
        report['system_colors']=[(i,user.GetSysColor(i)) for i in (5,6,20,21)]
        assert image.size==(180,100),image.size
        def system_rgb(index):
            color=user.GetSysColor(index)
            return tuple((color>>shift)&255 for shift in (0,8,16))
        # STATIC brushes follow Windows theme/system colors, including high contrast.
        assert image.getpixel((10,10))==system_rgb(5),image.getpixel((10,10))
        # BLACKRECT's themed dark brush can use 3DDKSHADOW instead of WINDOWFRAME.
        assert image.getpixel((60,40)) in (system_rgb(6),system_rgb(21)),image.getpixel((60,40))
        assert image.getpixel((10,10))!=image.getpixel((60,40))
        report.update(own_nonactivating_window=True,actual_wgc_pixels=True,
                      client_size=list(image.size),game_window_sampled=False,foreground_preserved=True,
                      border_requested=source.border_requested)
        # Desktop edge observation is opt-in with this own-window diagnostic;
        # production capture never samples another surface to inspect its border.
        from PIL import ImageGrab
        time.sleep(.5)
        edge=ImageGrab.grab(bbox=(40,40,220,140))
        observed=[edge.getpixel((0,50)),edge.getpixel((90,0))]
        report['own_window_edge_pixels']=[list(p) for p in observed]
        report['own_window_edges_match_client']=all(p==system_rgb(5) for p in observed)
        report['ok']=True
        # Not an assertion: Windows policy/other capturers may retain a border.
    except Exception as error:report['error']=str(error)
    finally:
        source.close()
        if hwnd:user.DestroyWindow(hwnd)
    Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['ok']


def run_smoke(sample: Path, output: Path):
    from .app import App
    from .store import Store
    report = {"ok": False}
    try:
        with tempfile.TemporaryDirectory(prefix="apex-diagnostic-") as folder:
            directory = Path(folder)
            settings = Store(directory)
            settings.set_setting("hotkey", "Ctrl+Alt+F24")
            settings.set_setting("sound", False)
            settings.set_setting('automatic',False)
            settings.set_setting('monitor_mode','manual')  # Own hotkey capture must not depend on user's current foreground.
            settings.close()
            root = tk.Tk()
            root.withdraw()
            app = App(root, directory)
            def until(condition):
                deadline = time.monotonic() + 30
                while not condition() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                if not condition():
                    phase=app.watcher.phase() if app.watcher else None
                    observation=getattr(app.watcher,'last_observation',None)
                    raise RuntimeError("诊断等待超时：" + app.status.get()+f'; phase={phase}; HUD={observation}')
            try:
                until(lambda: app.engine_ready)
                app.start([1, 2, 3])
                first_session = app.selected_session
                app._submit(first_session, str(sample))
                until(lambda: not app.busy)
                first_id = app.store.captures(first_session)[0]["id"]
                first = app.store.capture(first_id)
                assert [c["damage"] for c in first["result"]["cards"]] == [174, 430, 182]
                rows = [{"card": i, "player_id": pid,
                         **{k: card[k] for k in ("damage", "kills", "assists")}}
                        for i, (pid, card) in enumerate(zip((2, 1, 3), first["result"]["cards"]))]
                app.store.confirm(first_id, rows, remember=True)
                corrected = [dict(row) for row in rows]
                corrected[1]['damage'] = 1430
                app.store.confirm(first_id, corrected)
                app._submit(first_session, str(sample))
                until(lambda: not app.busy)
                assert len(app.store.captures(first_session)) == 1
                assert app.store.totals(first_session)[1][0]['damage'] == 1430
                assert '未重复计入' in app.status.get()
                app.store.confirm(first_id, rows)
                app.end()
                app.start()
                second_session = app.selected_session
                app._submit(second_session, str(sample))
                until(lambda: not app.busy)
                assert app.store.totals(second_session)[0] == 1
                assert [p["damage"] for p in app.store.totals(second_session)[1]] == [430, 174, 182]
                second = app.store.capture(app.store.captures(second_session)[0]["id"])
                assert second["status"] == "confirmed"
                assert app.hotkey is not None
                # Deliver the registered message to our own application, without keyboard injection.
                ctypes.windll.user32.PostThreadMessageW(app.hotkey.thread_id, 0x0312, 1, 0)
                until(lambda: len(app.store.captures(second_session)) == 2)
                last = app.store.capture(app.store.captures(second_session)[0]["id"])
                assert Path(last["image"]).exists()
                app.end()
                assert app.hotkey is None
                modes = []
                for ids in ([1], [1, 3]):
                    app.mode.set(f'{len(ids)} 人模式')
                    app.change_mode()
                    app.start(ids)
                    sid = app.selected_session
                    app._submit(sid, str(sample))
                    until(lambda: not app.busy)
                    assert app.store.totals(sid)[0] == 1
                    assert [p['id'] for p in app.store.totals(sid)[1]] == ids
                    modes.append(len(ids))
                    app.end()
                    assert app.hotkey is None
                variant = directory / 'synthetic-duo.png'
                changed_summary(sample, first['result'], variant,
                                {first['result']['cards'][2]['name']: 'NewTeammate', '174': '274', '430': '500'})
                app.mode.set('2 人模式')
                app.change_mode()
                app.start()
                duo = app.selected_session
                app._submit(duo, str(sample))
                until(lambda: not app.busy)
                assert app.store.session_player_ids(duo) == []
                app._submit(duo, str(sample))
                until(lambda: not app.busy)
                assert len(app.store.captures(duo)) == 1
                assert not app.store.discovery(duo)['resolved']
                app._submit(duo, str(variant))
                until(lambda: not app.busy)
                assert app.store.totals(duo)[0] == 2
                averages = {p['id']: p['average_damage'] for p in app.store.totals(duo)[1]}
                assert averages == {1: 465, 2: 224}
                app.end()
                report = {"ok": True, "gui_started": True, "first_import_pending": first["status"] == "pending",
                          "identity_swap_totals": [430, 174, 182], "bound_identity_auto_save": True,
                          "system_capture_seconds": last["result"]["capture_elapsed"],
                          "ocr_seconds": second["result"]["elapsed"], "data_isolated": True,
                          "small_roster_modes": modes, "hotkey_released_after_end": app.hotkey is None}
                report.update(cross_match_duo_replayed=2, average_damage_by_player=averages,
                              second_match_is_synthetic_fixture=True,
                              exact_duplicate_preserves_correction=True,
                              repeated_first_frame_keeps_discovery_waiting=True)
                report['automatic_fixture'] = automatic_memory_fixture(app,sample,variant,until)
                report['partial_bound_roster_fixture'] = partial_bound_fixture(app, sample, first['result'], until)
                # Portable image path and cleanup are tested on temporary data only.
                from .media import execute_cleanup
                app.store.set_image_directory(directory/'chosen images'/'ApexTrackerScreenshots')
                app.mode.set('1 人模式')
                app.change_mode()
                app.start([1])
                sid=app.selected_session
                app._submit(sid,str(sample));until(lambda:not app.busy)
                capture=app.store.capture(app.store.captures(sid)[0]['id'])
                assert Path(capture['image']).parent==app.store.screenshots
                app.end()
                before=app.store.totals(sid)
                app.store.finish_cleanup(execute_cleanup(app.store.cleanup_plan('photos')))
                assert app.store.totals(sid)==before and not Path(capture['image']).exists()
                report.update(custom_image_directory=True,photo_cleanup_keeps_totals=True)
            finally:
                app._stop_automatic()
                until(lambda: not app.busy and not app.pending_auto)
                app.close()
    except Exception as error:
        import traceback
        report["ok"] = False
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report["ok"]


def partial_bound_fixture(app, sample, result, until):
    """Synthetic OCR events exercise the production GUI and bound-roster replay."""
    from copy import deepcopy
    app.mode.set('1 人模式')
    app.change_mode()
    app.start()
    sid = app.selected_session
    target = deepcopy(result['cards'][1])
    target.update(card=1, damage=229, kills=1, assists=0)
    stranger = lambda card, name, score: dict(card=card, name=name, name_score=score,
                                             damage=100+card, kills=0, assists=0, score=.99)
    first = {'cards': [stranger(0, 'SyntheticUnclear', .53), target,
                       stranger(2, 'SyntheticFirstStranger', .99)],
             'warnings': [], 'recognition_version': 6}
    app.events.put(('result', (sid, str(sample), 'partial-bound-first', first)))
    until(lambda: len(app.store.captures(sid)) == 1)
    assert not app.store.session_player_ids(sid)
    target = deepcopy(target)
    target.update(card=2, damage=227, kills=0)
    second = {'cards': [stranger(0, 'SyntheticSecondStranger', .99),
                        stranger(1, 'SyntheticThirdStranger', .99), target],
              'warnings': [], 'recognition_version': 6}
    app.events.put(('result', (sid, str(sample), 'partial-bound-second', second)))
    until(lambda: app.store.totals(sid)[0] == 2)
    count, totals = app.store.totals(sid)
    assert len(totals) == 1
    assert (totals[0]['damage'], totals[0]['kills'], totals[0]['assists'], totals[0]['average_damage']) == (456, 1, 0, 228)
    app.end()
    return {'ok': True, 'synthetic_ocr_events': True, 'card_positions_changed': True,
            'games': count, 'damage': 456, 'kills': 1, 'assists': 0, 'average_damage': 228}


def automatic_memory_fixture(app,sample,variant,until):
    """Full production queue/OCR/lifecycle, injected frames; no focus or capture."""
    from PIL import Image,ImageDraw,ImageFont
    from . import app as module
    from .monitor import SummaryWatcher
    from .ocr import open_image
    current=[open_image(sample)]
    original=module.SummaryWatcher
    def factory(emit,bbox,**kwargs):
        return SummaryWatcher(emit,bbox,foreground=lambda _:42,capture=lambda:current[0].copy(),
                              resolve_display=lambda _:app.displays[0],**kwargs)
    def show(image,seconds=1.5):
        current[0]=image
        started=time.monotonic()
        until(lambda:time.monotonic()-started>seconds)
    try:
        module.SummaryWatcher=factory
        app.automatic.set(True);app.mode.set('2 人模式');app.start()
        sid=app.selected_session
        until(lambda:len(app.store.captures(sid))==1 and not app.busy)
        first=app.store.capture(app.store.captures(sid)[0]['id'])
        assert [c['damage'] for c in first['result']['cards']]==[174,430,182]
        show(current[0]);assert len(app.store.captures(sid))==1
        blank=Image.new('RGB',(1600,1000),'black')
        show(blank);assert app.watcher.locked
        show(open_image(sample));assert len(app.store.captures(sid))==1
        epoch=app.watcher.epoch
        app.configure_automatic()
        assert app.watcher.locked and app.watcher.epoch==epoch
        show(open_image(sample));assert len(app.store.captures(sid))==1
        hud=blank.copy();draw=ImageDraw.Draw(hud);font=ImageFont.truetype('msyh.ttc',32)
        draw.text((1150,40),'剩余小队 19',font=font,fill='white')
        # A spectator-like counter alone cannot unlock.
        show(hud,5);assert app.watcher.locked
        draw.text((20,100),'F 小队静音',font=font,fill='white')
        for x,text in zip((100,400,700,1000,1300),('突击','游击','侦察','支援','控制')):
            draw.text((x,820),text,font=font,fill='white')
        draw.text((1100,940),'现在请选择',font=font,fill='white')
        current[0]=hud
        until(lambda:not app.watcher.locked)
        current[0]=open_image(variant)
        until(lambda:len(app.store.captures(sid))==2 and not app.busy)
        averages={p['id']:p['average_damage'] for p in app.store.totals(sid)[1]}
        assert app.store.totals(sid)[0]==2 and averages=={1:465,2:224}
        hud_seconds=app.watcher.last_observation['elapsed']
        assert app.engine.engine.text_det.limit_type=='min'
        app.end();until(lambda:app.store.active_session() is None)
        return dict(ok=True,real_windows_capture=False,screen_capture_requested=False,
                    source='in-memory sample frames; foreground gate substituted to preserve game focus',
                    automatic_captures=2,same_page_once=True,duo_replayed=2,
                    average_damage_by_player=averages,tab_reopen_does_not_rearm=True,
                    configure_preserves_epoch=True,spectator_counter_does_not_rearm=True,
                    entry_probe_seconds=hud_seconds,summary_ocr_policy_restored=True,
                    entry_page_synthetic_real_ocr=True,actual_apex_gameplay_tested=False)
    finally:
        app._stop_automatic();app.automatic.set(False);module.SummaryWatcher=original
