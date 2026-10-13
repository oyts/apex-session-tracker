import time
import pytest
from PIL import Image
from tracker.pages import classify_lines
from tracker.domain import MatchCycle
from tracker.store import Store
from tracker.session import reconcile
from tests.test_v14 import watcher,entry,observation,lines
from tests.test_automatic import frames


def test_current_selection_mute_control_can_be_mid_left():
    data=lines();data[-1]['cy']=390
    assert classify_lines(data,1000,1000)['kind']=='selection'
    data[-1]['x']=800
    assert classify_lines(data,1000,1000)['kind']=='unknown'


def ship():
    return [dict(text=t,score=.99,x=x,cx=x,cy=y) for t,x,y in (
        ('某玩家是跳伞指挥官',500,400),('LCTRL 单独发射',500,550),
        ('标记所有复制器',500,340),('N',250,80),('NE',450,80),('E',650,80))]


def test_secondary_ship_requires_conjunction_and_rejects_spectator():
    data=ship();assert classify_lines(data,1000,1000)['kind']=='dropship'
    for i in range(3):assert classify_lines(data[:i]+data[i+1:],1000,1000)['kind']=='unknown'
    for word in ('观战','DEATH RECAP','FIRING RANGE','RESPAWNING'):
        assert classify_lines(data+[dict(text=word,score=.99,x=20,cy=50)],1000,1000)['kind']=='unknown'
    assert classify_lines([dict(v,score=.84) for v in data],1000,1000)['kind']=='unknown'


def test_ship_requires_two_time_separated_probes_not_generic_combat(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step();old=w.epoch
    entry(w,'dropship');assert w.epoch==old
    w.drop_first=time.monotonic()-3;entry(w,'dropship')
    assert w.epoch!=old and not w.locked
    key=w.epoch;entry(w,'dropship');assert w.epoch==key
    c=MatchCycle(completed=True);assert c.observe_start('combat',[]) is None


def test_template_dropout_does_not_split_episode(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step();key=w.episode_key
    s.match=False;w.step();w.step();s.match=True
    s.image.putpixel((0,0),(10,10,10));w.episode_last=-100
    w.step();w.step()
    assert len(frames(e))==2 and w.episode_key==key
    assert frames(e)[0]['match_key']==frames(e)[1]['match_key']


def test_rejected_false_summary_reopens_only_its_unaccepted_cycle(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step();context=frames(e)[0]
    w.reject_candidate(context['match_key']);assert not w.locked
    w.step();w.step();w.accept_summary(context)
    assert w.locked
    w.reject_candidate(context['match_key']);assert w.locked
    entry(w);current=w.epoch
    w.reject_candidate(context['match_key']);assert w.epoch==current and not w.locked


def test_template_positive_page_does_not_starve_entry_probe(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step()
    probes=[v for k,v in e if k=='probe'];assert probes
    w.submit_gameplay(probes[-1]['token'],dict(kind='selection',evidence=['located controls']))
    old=w.epoch;w.step()
    assert w.epoch==old and w.locked  # provisional false summary cannot repeatedly open matches
    w.reject_candidate(old)
    token=(w.probe_generation,time.monotonic());w.probe_pending=token
    w.submit_gameplay(token,dict(kind='selection',evidence=['located controls']))
    w._gameplay_results()
    assert w.epoch==old and not w.locked and w.cycle.start_kind=='selection'


def test_manual_attribution_updates_present_and_future_episode_copies(tmp_path):
    st=Store(tmp_path);sid=st.start_session(mode=3)
    st.add_capture(sid,'seed','seed',observation('seed'));reconcile(st,sid)
    pending=dict(observation(None,'start_not_observed'),episode_key='episode')
    a=st.add_capture(sid,'a','a',pending);b=st.add_capture(sid,'b','b',pending)
    st.assign_match(a)
    st.confirm(a,[dict(card=i-1,player_id=i,damage=500,kills=1,assists=2) for i in (1,2,3)],manual=True)
    reconcile(st,sid)
    assert st.capture(b)['merged_into']==a and st.totals(sid)[0]==2
    c=st.add_capture(sid,'c','c',pending);reconcile(st,sid)
    assert st.capture(c)['merged_into']==a and st.totals(sid)[0]==2
    d=st.add_capture(sid,'d','d',dict(pending,episode_key='different'))
    reconcile(st,sid);assert st.capture(d)['merged_into'] is None
    before=st.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0];reconcile(st,sid)
    assert st.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==before
    st.close();st=Store(tmp_path);reconcile(st,sid)
    assert st.capture(c)['merged_into']==a
    st.close()


def test_conflicting_explicit_reviews_are_not_silently_combined(tmp_path):
    st=Store(tmp_path);sid=st.start_session(mode=3)
    p=dict(observation(None,'start_not_observed'),episode_key='e')
    a=st.add_capture(sid,'a','a',p);b=st.add_capture(sid,'b','b',p)
    st.assign_match(a);st.assign_match(b)
    c=st.add_capture(sid,'c','c',p);st.consolidate(sid)
    assert st.capture(a)['match_id']!=st.capture(b)['match_id']
    assert st.capture(c)['result']['attribution']=='needs_review'
    st.close()


def test_graphics_source_minimized_stale_close_and_rgb_copy(monkeypatch):
    from tracker import window_capture as m
    from contextlib import nullcontext
    from types import SimpleNamespace
    monkeypatch.setattr(m,'physical_pixels',nullcontext)
    hwnd=[42];minimized=[False]
    monkeypatch.setattr(m,'apex_window',lambda old:hwnd[0])
    monkeypatch.setattr(m,'window_display',lambda h:dict(bbox=(0,0,32,32)))
    monkeypatch.setattr(m,'window_minimized',lambda h:minimized[0])
    monkeypatch.setattr(m,'window_client_crop',lambda h,s:(0,0,*s))
    source=m.GraphicsWindowSource()
    def start(h):
        source.control=SimpleNamespace(is_finished=lambda:False,stop=lambda:None)
        source.latest=Image.new('RGB',(32,32),'red');source.stamp=time.monotonic()
    monkeypatch.setattr(source,'_start',start)
    assert source.read().getpixel((1,1))==(255,0,0)
    source.stamp=time.monotonic()-4;assert source.read() is None
    minimized[0]=True;assert source.read() is None and source.latest is None
    hwnd[0]=None;assert source.read() is None and source.control is None


def test_crash_after_rejection_ack_does_not_leave_provisional_lock(tmp_path):
    st=Store(tmp_path);sid=st.start_session(mode=3)
    c=MatchCycle();context=c.summary_context();c.summary_preserved(context,provisional=True)
    st.set_setting('automatic_phase_'+str(sid),dict(version=14,cycle=c.snapshot()))
    recovered=st.automatic_phase(sid)['cycle']
    assert not recovered['completed'] and not recovered['summary_pending']
    from tracker.journal import EvidenceSpool
    EvidenceSpool(tmp_path).preserve(Image.new('RGB',(32,32)),sid,context)
    st.set_setting('automatic_phase_'+str(sid),dict(version=14,cycle=c.snapshot()))
    assert st.automatic_phase(sid)['cycle']['summary_pending']
    st.close()


def test_restart_cannot_reuse_stale_summary_episode(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step();w.accept_summary(frames(e)[0])
    phase=w.phase();phase['episode_seen_at']=time.time()-13
    _,_,resumed,_=watcher(tmp_path,phase)
    assert resumed.episode_key is None and resumed.episode_context is None
    assert resumed.cycle.summary_context()['attribution']=='start_not_observed'


def test_active_known_match_does_not_ocr_entry_bands_all_game(tmp_path):
    s,e,w,_=watcher(tmp_path);entry(w);s.match=False
    w.step();w.step()
    assert not [v for k,v in e if k=='probe']


def test_older_windows_optional_properties_fall_back_once(monkeypatch):
    import sys
    from types import SimpleNamespace
    from tracker.window_capture import GraphicsWindowSource
    attempts=[]
    class FakeCapture:
        def __init__(self,**options):self.options=options;attempts.append(options)
        def event(self,fn):pass
        def start_free_threaded(self):
            if self.options['minimum_update_interval']:raise OSError('minimum update interval is not supported')
            return SimpleNamespace(stop=lambda:None)
    monkeypatch.setitem(sys.modules,'windows_capture',SimpleNamespace(WindowsCapture=FakeCapture))
    s=GraphicsWindowSource();s._start(42)
    assert len(attempts)==2 and attempts[-1]['window_hwnd']==42
    assert attempts[-1]['minimum_update_interval'] is None
    s.close()


def test_manual_template_flag_alone_does_not_confirm_match_end(tmp_path):
    st=Store(tmp_path);sid=st.start_session(mode=3)
    c=MatchCycle();c.observe_start('selection',[])
    st.set_setting('automatic_phase_'+str(sid),dict(version=14,cycle=c.snapshot()))
    st.add_capture(sid,'a','a',dict(observation(c.match_key),source='manual',summary_candidate_preserved=True))
    assert not st.automatic_phase(sid)['cycle']['completed']
    st.close()


def test_rejected_animation_is_rate_limited_and_preserves_probe_generation(tmp_path):
    s,e,w,_=watcher(tmp_path);w.step();w.step();generation=w.probe_generation
    w.reject_candidate(w.epoch);s.image.putpixel((0,0),(4,4,4))
    w.step();w.step()
    assert len(frames(e))==1 and w.probe_generation==generation
    w.episode_last=time.monotonic()-9;w.step();w.step()
    assert len(frames(e))==2


def test_explicit_separate_capture_is_not_rebound_to_episode_review(tmp_path):
    st=Store(tmp_path);sid=st.start_session(mode=3)
    p=dict(observation(None,'start_not_observed'),episode_key='e')
    a=st.add_capture(sid,'a','a',p);st.assign_match(a)
    b=st.add_capture(sid,'b','b',dict(p,match_key='explicit-other',keep_separate=True))
    st.consolidate(sid)
    assert st.capture(b)['result']['match_key']=='explicit-other'
    assert st.capture(b)['match_id']!=st.capture(a)['match_id']
    st.close()
