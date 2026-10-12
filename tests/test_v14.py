import json
import time
import sqlite3
from pathlib import Path
import pytest
from PIL import Image
from tracker.domain import MatchCycle
from tracker.pages import classify_lines
from tracker.journal import EvidenceSpool
from tracker.monitor import SummaryWatcher
from tracker.store import Store
from tracker.session import reconcile
from tests.test_automatic import Sequence, frames
from tests.test_discovery import frame


def lines(selection=True,language='zh'):
    classes=('突击','游击','侦察','支援','控制') if language=='zh' else ('ASSAULT','SKIRMISHER','RECON','SUPPORT','CONTROLLER')
    result=[dict(text=t,score=.99,x=50+i*150,cx=80+i*150,cy=800) for i,t in enumerate(classes)]
    result += [dict(text='现在请选择' if language=='zh' else 'SELECT YOUR LEGEND',score=.99,x=800,cy=950),
               dict(text='F 小队静音' if language=='zh' else 'F MUTE SQUAD',score=.99,x=20,cy=100)]
    return result

@pytest.mark.parametrize('language',['zh','en'])
def test_selection_requires_located_conjunction(language):
    data=lines(language=language)
    assert classify_lines(data,1000,1000)['kind']=='selection'
    for negative in (data[:5],data[:-1],data[:-2], [dict(l,cy=300) for l in data]):
        assert classify_lines(negative,1000,1000)['kind']=='unknown'
    assert classify_lines(data+[dict(text='FIRING RANGE',score=.99,x=10,cy=10)],1000,1000)['kind']=='unknown'
    assert classify_lines([dict(l,score=.84) for l in data],1000,1000)['kind']=='unknown'

@pytest.mark.parametrize('mode',['排位赛','三人','双人','外卡','RANKED','TRIOS','DUOS','WILDCARD'])
def test_loading_does_not_require_rank_or_map(mode):
    data=[dict(text=t,score=.99,x=x,cx=x,cy=y) for t,x,y in [(mode,30,80),('倒数计时',500,200),('7.1',500,250),('F 小队静音',800,950)]]
    assert classify_lines(data,1000,1000)['kind']=='loading'
    assert classify_lines(data[:2]+data[3:],1000,1000)['kind']=='unknown'


def test_cycle_reconnect_animation_and_equal_data_independent():
    cycle=MatchCycle()
    one=cycle.observe_start('loading',['countdown'])
    assert cycle.observe_start('selection',['classes']) is None
    assert cycle.observe_start('loading',['reconnect']) is None
    context=cycle.summary_context();cycle.summary_preserved(context)
    assert cycle.summary_context()['match_key'] is None
    two=cycle.observe_start('selection',['classes'])
    assert one['match_key']!=two['match_key']
    assert cycle.observe_start('selection',['animation']) is None
    assert MatchCycle.restore(cycle.snapshot()).snapshot()==cycle.snapshot()


def test_bootstrap_and_legacy_lock_review():
    c=MatchCycle();context=c.summary_context()
    assert context['attribution']=='bootstrap'
    c.summary_preserved(context)
    assert c.summary_context()['attribution']=='start_not_observed'
    w=SummaryWatcher(lambda *_:None,(0,0,32,32),initial_phase={'locked':True})
    assert w.cycle.summary_context()['match_key'] is None


def watcher(tmp_path,phase=None):
    source=Sequence();events=[];spool=EvidenceSpool(tmp_path)
    w=SummaryWatcher(lambda k,v:events.append((k,v)),(0,0,32,32),source,source.foreground,source.capture,
                     initial_phase=phase,spool=spool,session_id=1)
    return source,events,w,spool


def entry(w,kind='selection',age=0):
    token=(w.probe_generation,time.monotonic()-age);w.probe_pending=token
    w.submit_gameplay(token,dict(kind=kind,evidence=['synthetic located-page evidence']))
    w._gameplay_results()


def test_durable_evidence_not_vetoed_by_closed_cycle_or_busy(tmp_path):
    s,e,w,sp=watcher(tmp_path);w.step();w.step()
    assert len(list(sp.pending()))==1 and w.locked
    s.match=False;w.step();w.step()
    s.image=Image.new('RGB',(32,32),'white');s.match=True;w.step();w.step()
    assert len(frames(e))==2 and len(list(sp.pending()))==2
    assert frames(e)[1]['match_key'] is None
    assert frames(e)[1]['attribution']=='start_not_observed'
    assert not w.available.is_set()  # both preserved despite busy OCR
    resumed=watcher(tmp_path,w.phase())[2]
    resumed.capture=s.capture;resumed.detector=s;resumed.step();resumed.step()
    assert len(list(sp.pending()))==2


def test_distinct_next_cycle_and_stale_probe(tmp_path):
    s,e,w,sp=watcher(tmp_path);w.step();w.step();old=w.epoch
    s.match=False;w.step();w.step();entry(w,age=40)
    assert w.locked
    entry(w);assert not w.locked and w.epoch!=old
    entry(w,'loading');assert w.cycle.generation==1
    s.image.putpixel((0,0),(1,1,1));s.match=True;w.step();w.step()
    assert len(frames(e))==2 and frames(e)[0]['match_key']!=frames(e)[1]['match_key']


def test_spool_failure_does_not_close_cycle(tmp_path,monkeypatch):
    s,e,w,sp=watcher(tmp_path)
    monkeypatch.setattr(sp,'preserve',lambda *_:(_ for _ in ()).throw(OSError('disk full')))
    w.step()
    with pytest.raises(OSError):w.step()
    assert not w.locked and not frames(e) and w.first is not None


def test_focus_race_and_single_summary_departure(tmp_path):
    s,e,w,sp=watcher(tmp_path);s.change_after_capture=True;w.step()
    assert not frames(e)
    s.change_after_capture=False;w.step();s.active=None;w.step()
    assert len(frames(e))==1


def test_follow_native_target_injected_and_disconnect(tmp_path):
    s=Sequence();events=[];target=[dict(device='A',bbox=(0,0,32,32),primary=True)]
    w=SummaryWatcher(lambda k,v:events.append((k,v)),None,s,s.foreground,s.capture,resolve_display=lambda _:target[0])
    w.step();w.step();target[0]=dict(device='B',bbox=(-32,0,0,32),primary=False)
    w.step();assert w.bbox==(-32,0,0,32) and len(frames(events))==1
    target[0]=None;n=s.captures;w.step();assert s.captures==n


def test_spool_recovers_and_safe_acknowledge(tmp_path):
    sp=EvidenceSpool(tmp_path);p=sp.preserve(Image.new('RGB',(32,32)),1,dict(match_key='a'))
    assert list(EvidenceSpool(tmp_path).pending())[0]['match_key']=='a'
    with pytest.raises(ValueError):sp.acknowledge(tmp_path/'unrelated.png')
    sp.acknowledge(p['path']);assert not list(sp.pending())
    # Write intent without complete PNG isn't a valid recovery item.
    (sp.directory/'incomplete.json').write_text('{"session_id":1}')
    assert not list(sp.pending())


def observation(key,attribution='start_observed'):
    return dict(frame(('A','B','C'),(100,100,100)),match_key=key,episode_key=key,source='automatic',
                observation_version=14,attribution=attribution)


def test_matches_multiple_evidence_equal_stats_and_manual_wins(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    a=store.add_capture(sid,'a.png','a',observation('one'))
    assert reconcile(store,sid)[1]==[a]
    store.confirm(a,[dict(card=i-1,player_id=i,damage=500,kills=1,assists=2) for i in (1,2,3)],manual=True)
    b=store.add_capture(sid,'b.png','b',observation('one'));reconcile(store,sid)
    assert store.capture(b)['merged_into']==a and store.totals(sid)[0]==1
    c=store.add_capture(sid,'c.png','c',observation('two'));reconcile(store,sid)
    assert store.totals(sid)[0]==2 and all(r['damage']==600 and r['average_damage']==300 for r in store.totals(sid)[1])
    assert store.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0]==3
    assert store.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]>=1
    store.close();store=Store(tmp_path)
    assert store.totals(sid)[0]==2 and store.find_duplicate(sid,'b')==a
    store.close()


def test_unknown_capture_review_link_or_new_does_not_guess(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    a=store.add_capture(sid,'a','a',observation('one'));reconcile(store,sid)
    unknown=observation(None,'start_not_observed');unknown['episode_key']='unknown'
    b=store.add_capture(sid,'b','b',unknown);reconcile(store,sid)
    assert store.capture(b)['result']['attribution']=='needs_review' and store.totals(sid)[0]==1
    with pytest.raises(ValueError):store.confirm(b,[],manual=False)
    store.assign_match(b,store.capture(a)['match_id']);reconcile(store,sid)
    assert store.totals(sid)[0]==1
    c=store.add_capture(sid,'c','c',dict(unknown,episode_key='other'))
    store.assign_match(c,None);reconcile(store,sid)
    assert store.totals(sid)[0]==2
    store.close()


def test_migration_legacy_idempotent_and_backed_up(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    a=store.add_capture(sid,'a','a',frame(('A','B','C'),(100,200,300)));reconcile(store,sid)
    before=store.totals(sid);store.db.execute('PRAGMA user_version=13');store.db.commit();store.close()
    store=Store(tmp_path);assert store.totals(sid)==before
    assert (tmp_path/'backups'/'before-v14.sqlite3').exists()
    n=store.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0];store.close()
    store=Store(tmp_path);assert store.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0]==n
    store.close()


def test_verified_start_is_persistent_without_capture(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    event=MatchCycle().observe_start('selection',['synthetic']);store.matches.begin(sid,event)
    assert store.matches.list(sid)[0]['state']=='open'
    assert store.totals(sid)[0]==0
    assert store.matches.list(sid)[0]['state']=='open'
    store.close()

def test_manual_durable_summary_closes_cycle_and_invalidates_old_probe(tmp_path):
    s,e,w,sp=watcher(tmp_path);entry(w)
    context=w.cycle.summary_context();old=w.epoch
    token=(w.probe_generation,time.monotonic());w.probe_pending=token
    w.preserve_manual_summary(context)
    assert w.locked and w.probe_pending is None
    w.submit_gameplay(token,dict(kind='selection',evidence=['old queued page']))
    w._gameplay_results();assert w.epoch==old
    entry(w);assert w.epoch!=old

def test_manual_phase_recovers_committed_summary_after_stop(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    cycle=MatchCycle();cycle.observe_start('selection',['synthetic'])
    store.set_setting('automatic_phase_'+str(sid),dict(version=14,cycle=cycle.snapshot()))
    store.add_capture(sid,'a','a',dict(observation(cycle.match_key),source='manual',summary_candidate_preserved=True))
    assert store.automatic_phase(sid)['cycle']['completed']
    store.close()
