"""Public, self-drawn fixtures; never ship player photos in the source archive."""
import json
from datetime import datetime,timedelta
from PIL import Image, ImageDraw
from tracker.summary_surface import surface_evidence
from tracker.dedup import image_digest
from tracker.store import Store
from tracker.session import reconcile
from tests.test_v14 import observation


def picture(path,queue=False,changed=None):
    im=Image.new('RGB',(1000,625),(10,12,18));draw=ImageDraw.Draw(im)
    for i,x in enumerate((115,392,670)):
        # Names, every data row and patterned artwork are independent evidence.
        for y in (200,240,280,320,360,400,440):
            draw.text((x+3,y),'SAMPLE 012345',fill='white')
        for y in range(190,490,15):
            draw.rectangle((x+90,y,x+165,y+8),fill=(40+y%210,100,180))
    if queue:draw.rectangle((730,0,999,65),fill=(100,210,10))
    if changed=='digit':draw.rectangle((118,325,125,331),fill='white')
    if changed=='name':draw.rectangle((118,205,125,211),fill='white')
    if changed=='art':
        for x in (115,392,670):draw.rectangle((x+90,190,x+165,490),fill=(190,20,10))
    im.save(path)
    result=observation('one')
    result['lines']=[dict(text='造成伤害',x=x,y=300,bottom=316,score=.99,cy=308) for x in (115,392,670)]
    result['lines'] += [dict(text='给予重生次数',x=x,y=420,bottom=436,score=.99,cy=428) for x in (115,392,670)]
    return dict(image=str(path),result=result,hash=image_digest(im))


def test_requeue_is_excluded_but_text_and_art_changes_are_not(tmp_path):
    a=picture(tmp_path/'a.png');b=picture(tmp_path/'b.png',True)
    assert a['hash']!=b['hash'] and surface_evidence(a,b)
    for change in ('digit','name','art'):
        c=picture(tmp_path/(change+'.png'),True,change)
        assert surface_evidence(a,c) is None,change
    b['result']['keep_separate']=True
    assert surface_evidence(a,b) is None


def test_two_explicit_different_squad_totals_are_not_cosmetic(tmp_path):
    from tests.test_participation import header
    a=picture(tmp_path/'a.png');b=picture(tmp_path/'b.png',True)
    a['result']['lines']+=header(1)
    b['result']['lines']+=header(2)
    assert surface_evidence(a,b) is None
    a['result']['summary_readiness']=dict(state='incomplete')
    assert surface_evidence(a,b)  # A known incomplete early header may still finish loading.


def test_return_converges_without_ocr_stat_matching_and_survives_reopen(tmp_path):
    store=Store(tmp_path/'db');sid=store.start_session(mode=3)
    a=picture(tmp_path/'a.png');b=picture(tmp_path/'b.png',True)
    cid=store.add_capture(sid,a['image'],a['hash'],a['result']);reconcile(store,sid)
    corrected=store.capture(cid)['stats']
    corrected[0]['damage']=500
    store.confirm(cid,corrected,manual=True)
    b['result'].update(match_key=None,episode_key='returned',attribution='start_not_observed')
    copy=store.add_capture(sid,b['image'],b['hash'],b['result']);reconcile(store,sid)
    assert store.capture(copy)['merged_into']==cid
    assert store.capture(copy)['result']['visual_return']['method']=='summary-surface-v1'
    assert store.totals(sid)[0]==1 and len(store.visible_captures(sid))==1
    assert store.totals(sid)[1][0]['damage']==500
    reviews=store.db.execute('SELECT count(*) FROM reviews').fetchone()[0]
    reconcile(store,sid);assert store.db.execute('SELECT count(*) FROM reviews').fetchone()[0]==reviews
    store.close();store=Store(tmp_path/'db');reconcile(store,sid)
    assert store.capture(copy)['merged_into']==cid
    # Explicit next-game evidence wins even with unchanged numbers and a very
    # similar picture. The queue panel supplies enough pixels to differ exactly.
    c=picture(tmp_path/'c.png',True);c['result'].update(match_key='two',episode_key='two')
    # b already exists pixel-exactly; use a distinct visual queue counter.
    im=Image.open(c['image']);im.putpixel((900,10),(255,255,255));im.save(c['image']);c['hash']=image_digest(im)
    new=store.add_capture(sid,c['image'],c['hash'],c['result']);reconcile(store,sid)
    assert store.capture(new)['merged_into'] is None and store.totals(sid)[0]==2
    store.close()


def test_different_surface_or_old_return_does_not_search_history(tmp_path):
    for barrier in ('different','old'):
        store=Store(tmp_path/barrier);sid=store.start_session(mode=3)
        a=picture(tmp_path/(barrier+'a.png'));b=picture(tmp_path/(barrier+'b.png'),True)
        cid=store.add_capture(sid,a['image'],a['hash'],a['result']);reconcile(store,sid)
        if barrier=='old':
            old=(datetime.now().astimezone()-timedelta(seconds=125)).isoformat(timespec='seconds')
            store.db.execute('UPDATE captures SET created=? WHERE id=?',(old,cid));store.db.commit()
        else:
            different=picture(tmp_path/'different.png',changed='art')
            different['result'].update(match_key=None,episode_key='different',attribution='start_not_observed')
            store.add_capture(sid,different['image'],different['hash'],different['result']);reconcile(store,sid)
        b['result'].update(match_key=None,episode_key='unknown',attribution='start_not_observed')
        copy=store.add_capture(sid,b['image'],b['hash'],b['result']);reconcile(store,sid)
        assert store.capture(copy)['merged_into'] is None
        assert store.capture(copy)['result']['attribution']=='needs_review'
        store.close()


def test_verified_start_without_summary_and_human_review_are_barriers(tmp_path):
    for barrier in ('start','review','keep','discard'):
        store=Store(tmp_path/barrier);sid=store.start_session(mode=3)
        a=picture(tmp_path/(barrier+'a.png'));b=picture(tmp_path/(barrier+'b.png'),True)
        cid=store.add_capture(sid,a['image'],a['hash'],a['result']);reconcile(store,sid)
        earlier=(datetime.now().astimezone()-timedelta(seconds=5)).isoformat(timespec='seconds')
        store.db.execute('UPDATE captures SET created=? WHERE id=?',(earlier,cid));store.db.commit()
        b['result'].update(match_key=None,episode_key='unknown',attribution='start_not_observed')
        copy=store.add_capture(sid,b['image'],b['hash'],b['result'])
        if barrier=='start':
            store.matches.audit(sid,'match_started',{'match_key':'missing-summary'})
        elif barrier=='review':store.assign_match(copy)
        elif barrier=='discard':store.discard(copy)
        else:
            b['result'].update(keep_separate=True);store.update_recognition(copy,b['result'])
        reconcile(store,sid)
        assert store.capture(copy)['merged_into'] is None,barrier
        if barrier=='discard':
            store.restore(copy);assert not store.capture(copy)['result'].get('manual_discarded')
            reconcile(store,sid);assert store.capture(copy)['merged_into']==cid
        store.close()
