import time
import pytest
from tracker.pages import classify_lines
from tests.test_v14 import watcher


def text(value,x,y):return dict(text=value,score=.99,x=x,cx=x,cy=y)


def loading(language='zh'):
    return [text('外卡' if language=='zh' else 'WILDCARD',60,80),
            text('倒数计时' if language=='zh' else 'COUNTDOWN',500,290),
            text('3.3',500,350),text('玩法说明' if language=='zh' else 'HOW TO PLAY',500,530)]


def selection(language='zh'):
    labels=('突击','游击','侦察','支援','控制') if language=='zh' else ('ASSAULT','SKIRMISHER','RECON','SUPPORT','CONTROLLER')
    return [text('外卡' if language=='zh' else 'WILDCARD',30,30),
            text('选择传奇' if language=='zh' else 'SELECT LEGEND',80,390),
            text('F 小队静音' if language=='zh' else 'F MUTE SQUAD',80,430)]+[
            text(t,120+i*160,800 if i<2 else 930) for i,t in enumerate(labels)]


@pytest.mark.parametrize('language',['zh','en'])
def test_wildcard_loading_does_not_require_mute(language):
    data=loading(language);r=classify_lines(data,1000,1000)
    assert r['kind']=='loading' and r['profile']=='wildcard-loading'
    for i in range(4):assert classify_lines(data[:i]+data[i+1:],1000,1000)['kind']=='unknown'
    assert classify_lines([dict(l,score=.84) for l in data],1000,1000)['kind']=='unknown'
    assert classify_lines([dict(l,cy=900) if i==0 else l for i,l in enumerate(data)],1000,1000)['kind']=='unknown'
    assert classify_lines([text('RANKED',60,80)]+data[1:],1000,1000)['kind']=='unknown'


@pytest.mark.parametrize('language',['zh','en'])
def test_wildcard_simultaneous_selection_no_turn_strip(language):
    data=selection(language);r=classify_lines(data,1000,1000)
    assert r['kind']=='selection' and r['profile']=='wildcard-selection'
    for i in range(3):assert classify_lines(data[:i]+data[i+1:],1000,1000)['kind']=='unknown'
    assert classify_lines(data[:3]+data[5:],1000,1000)['kind']=='unknown'  # fewer than four classes
    assert classify_lines([text('TRIOS',30,30)]+data[1:],1000,1000)['kind']=='unknown'
    assert classify_lines([dict(l,cy=700) if i==1 else l for i,l in enumerate(data)],1000,1000)['kind']=='unknown'


@pytest.mark.parametrize('page',[loading,selection])
def test_wildcard_lobby_locker_firing_range_reconnect_are_negative(page):
    data=page()
    for extra in ([text('游玩',250,40),text('通行证',600,40)],
                  [text('LEGEND LOCKER',500,40)],[text('FIRING RANGE',60,60)],
                  [text('正在重新连接',500,450)]):
        assert classify_lines(data+extra,1000,1000)['kind']=='unknown'


def test_loading_selection_animation_and_midmatch_respawn_open_one_cycle(tmp_path):
    s,e,w,_=watcher(tmp_path)
    for data in [loading(),loading(),selection(),selection(),loading()]:
        token=(w.probe_generation,time.monotonic());w.probe_pending=token
        w.submit_gameplay(token,classify_lines(data,1000,1000));w._gameplay_results()
    starts=[v for k,v in e if k=='lifecycle']
    assert len(starts)==1 and w.cycle.generation==1
    assert w.cycle.start_kind=='loading'
    assert w.cycle.game_mode=='wildcard'
    assert w.cycle.summary_context()['game_mode']=='wildcard'


def test_mode_persistence_duplicate_and_unknown_history(tmp_path):
    from tracker.store import Store
    from tracker.domain import MatchCycle
    store=Store(tmp_path);sid=store.start_session(mode=3)
    cycle=MatchCycle();event=cycle.observe_start('loading',['verified'], 'wildcard')
    store.matches.begin(sid,event);store.matches.begin(sid,event)
    rows=store.matches.list(sid)
    assert len(rows)==1 and rows[0]['game_mode']=='wildcard'
    assert MatchCycle.restore(cycle.snapshot()).game_mode=='wildcard'
    cycle.summary_preserved(cycle.summary_context())
    core=cycle.observe_start('loading',['core'])
    store.matches.begin(sid,core)
    assert store.matches.list(sid)[0]['game_mode']=='unknown'
    # Simulate a Beta 2 database, then verify backup and idempotent migration.
    store.db.execute('ALTER TABLE matches DROP COLUMN game_mode')
    store.db.execute('PRAGMA user_version=14');store.db.commit();store.close()
    store=Store(tmp_path)
    assert (tmp_path/'backups/before-schema15.sqlite3').is_file()
    assert all(r['game_mode']=='unknown' for r in store.matches.list(sid))
    store.close();store=Store(tmp_path)
    assert len(store.matches.list(sid))==2
    store.close()


def test_mode_on_spooled_context_survives_missing_begin_and_manual_correction(tmp_path):
    from tracker.store import Store
    from tracker.session import reconcile
    from tests.test_discovery import frame
    store=Store(tmp_path);sid=store.start_session(mode=3)
    data=frame(('A','B','C'),(100,200,300))
    data.update(match_key='verified-cycle',attribution='start_observed',game_mode='wildcard',observation_version=14)
    one=store.add_capture(sid,'one.png','one',data);reconcile(store,sid)
    # Two independent pixel observations of a verified cycle are one match.
    two=store.add_capture(sid,'two.png','two',dict(data));reconcile(store,sid)
    mid=store.capture(one)['match_id'];assert store.matches.mode(mid)=='wildcard'
    assert store.totals(sid)[0]==1 and len(store.visible_captures(sid))==1
    store.confirm(one,[dict(card=i,player_id=i+1,damage=450+i,kills=1,assists=2) for i in range(3)],manual=True)
    before=store.totals(sid);store.close();store=Store(tmp_path)
    assert store.matches.mode(mid)=='wildcard' and store.totals(sid)==before
    assert store.capture(two)['merged_into']==one
    store.close()
