import json
import pytest
from tracker.participation import squad_kills, percentage
from tracker.store import Store
from tracker.session import reconcile
from tests.test_v14 import observation


def header(value,score=.99):
    return [dict(text='小队总击杀',score=.99,x=1700,right=1815,y=180,bottom=213,cy=197),
            dict(text=str(value),score=score,x=1820,right=1870,y=170,bottom=230,cy=200)]


def data(key,team,kills=(1,2,3),assists=(1,1,0)):
    result=observation(key)
    result['lines']=header(team)
    for c,k,a in zip(result['cards'],kills,assists):c.update(kills=k,assists=a)
    return result


def test_located_header_conflicts_and_complete_squad_fallback():
    result=data('a',6)
    assert squad_kills(result)==(6,'summary-header')
    result['lines']=header(2)
    assert squad_kills(result)==(None,'conflict')
    result['lines']=[]
    assert squad_kills(result)==(6,'three-complete-cards')
    result['cards']=result['cards'][:2]
    assert squad_kills(result)==(None,'missing')
    result['lines']=header(20)
    assert squad_kills(result)==(20,'summary-header')
    result['lines'][1]['cy']=900
    assert squad_kills(result)==(None,'missing')
    result['lines']=header(20,.90)
    assert squad_kills(result)==(None,'missing')
    assert percentage(1,1,0) is None and percentage(0,0,6)==0
    assert percentage(5,3,6)==8/6  # retain the requested formula, never clamp.


@pytest.mark.parametrize('mode',[1,2,3])
def test_weighted_total_roster_independence_correction_reopen_and_migration(tmp_path,mode):
    store=Store(tmp_path);sid=store.start_session(player_ids=list(range(1,mode+1)),mode=mode)
    with store.db:
        for i,name in enumerate(('A','B','C'),1):store.bind_alias(name,i)
    captures=[]
    for key,team,kills,assists in [('one',6,(1,2,3),(1,1,0)),('two',20,(10,5,5),(2,1,0))]:
        cid=store.add_capture(sid,key,key,data(key,team,kills,assists));reconcile(store,sid);captures.append(cid)
    count,totals=store.totals(sid)
    assert count==2 and len(totals)==mode
    assert totals[0]['participation']==14/26 and totals[0]['team_kills']==26
    assert totals[0]['participation'] != ((2/6)+(12/20))/2
    first=store.capture(captures[0]);rows=first['stats'];rows[0]['assists']=3
    store.confirm(first['id'],rows,manual=True,team_kills=8)
    assert store.totals(sid)[1][0]['participation']==16/28
    before=store.totals(sid)
    store.db.execute('PRAGMA user_version=15');store.db.commit();store.close()
    store=Store(tmp_path)
    assert (tmp_path/'backups/before-schema16.sqlite3').is_file()
    assert store.totals(sid)==before
    store.close();store=Store(tmp_path);assert store.totals(sid)==before
    # Denominator is a separate reviewed field, and unknown does not masquerade
    # as zero nor change the existing recorded match count/personal totals.
    store.confirm(first['id'],rows,manual=True,team_kills=None)
    assert store.totals(sid)[0]==2
    assert store.totals(sid)[1][0]['participation'] is None
    assert store.totals(sid)[1][0]['participation_missing']==1
    store.confirm(first['id'],rows,manual=True,team_kills='automatic')
    assert store.totals(sid)[1][0]['participation']==16/26
    store.discard(captures[1]);assert store.totals(sid)[1][0]['participation']==4/6
    store.close()


def test_zero_denominator_missing_and_conflicting_observations(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    a=store.add_capture(sid,'a','a',data('one',0,(0,0,0),(0,0,0)));reconcile(store,sid)
    assert store.totals(sid)[1][0]['participation'] is None
    assert store.totals(sid)[1][0]['participation_missing']==0
    b=store.add_capture(sid,'b','b',data('one',3,(1,1,1),(0,0,0)));reconcile(store,sid)
    assert store.totals(sid)[0]==1
    assert store.totals(sid)[1][0]['participation_missing']==1
    original=store.capture(a)
    store.confirm(a,original['stats'],manual=True,team_kills=3)
    assert store.totals(sid)[1][0]['participation']==0
    assert store.matches.team_kills(original['match_id'])['team_kills_source']=='manual'
    with pytest.raises(ValueError):store.confirm(a,original['stats'],manual=True,team_kills=-1)
    assert store.matches.team_kills(original['match_id'])['team_kills']==3
    store.close()
