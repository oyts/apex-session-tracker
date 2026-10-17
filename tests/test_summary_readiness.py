"""Self-constructed UI fixtures; no player screenshot redistribution."""
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from tracker.automatic import SummaryDetector
from tracker.summary_readiness import SummaryReadiness
from tracker.monitor import SummaryWatcher
from tracker.journal import EvidenceSpool
from tracker.store import Store
from tracker.session import reconcile
from tests.test_automatic import Sequence,frames
from tests.test_v14 import observation
from tests.test_participation import header


def page(header_visible=True,number='0'):
    d=SummaryDetector();image=Image.new('RGB',(1000,562),'black')
    for name,position in [('heading',(531,12)),('damage_native16x9',(116,234)),('triple_native16x9',(117,198))]:
        image.paste(Image.fromarray(d.sources[name]).convert('RGB'),position)
    if header_visible:
        image.paste(Image.fromarray(d.sources['squad_kills_native16x9']).convert('RGB'),(850,90))
    if number is not None:
        ImageDraw.Draw(image).text((925,82),number,font=ImageFont.load_default(size=36),fill=(255,190,0))
    return image


def test_caption_and_number_required_and_zero_stabilizes():
    d=SummaryDetector();gate=SummaryReadiness()
    assert not d.detect(page(False))['header_visible']
    assert not d.detect(page(True,None))['header_visible']
    found=d.detect(page());assert found['header_visible']
    assert not gate.observe(found,0)
    assert not gate.observe(found,.5)
    assert gate.observe(found,1.5)
    changed=d.detect(page(number='8'));assert not gate.observe(changed,2)
    assert not gate.observe(changed,2.5)
    assert gate.observe(changed,3)
    changed_image=page(number='8')
    ImageDraw.Draw(changed_image).rectangle((735,0,999,56),fill='green')
    assert gate.observe(d.detect(changed_image),3.5)
    assert not gate.observe(d.detect(page(False)),4)


def test_loading_does_not_dispatch_ocr_or_consume_slots_then_full_page_saved(tmp_path,monkeypatch):
    source=Sequence();source.image=page(False);events=[];spool=EvidenceSpool(tmp_path)
    watcher=SummaryWatcher(lambda k,v:events.append((k,v)),(0,0,1000,562),SummaryDetector(),
                           source.foreground,source.capture,spool=spool,session_id=1)
    now=[100.];monkeypatch.setattr('tracker.monitor.time.monotonic',lambda:now[0])
    for i in range(6):
        now[0]=100+i*.5;watcher.step()
    assert not frames(events) and watcher.episode_count==0 and not watcher.locked
    assert not list(spool.pending())
    source.image=page()
    for i in range(4):now[0]=103+i*.5;watcher.step()
    assert len(frames(events))==1 and watcher.episode_count==1
    payload=frames(events)[0]
    assert payload['summary_readiness']['state']=='complete'
    assert payload['summary_readiness']['min_header_stable_seconds']==1
    assert list(spool.pending()) and watcher.locked


def test_leaving_or_stopping_early_retains_one_incomplete_candidate(tmp_path,monkeypatch):
    source=Sequence();source.image=page(False);events=[]
    watcher=SummaryWatcher(lambda k,v:events.append((k,v)),(0,0,1000,562),SummaryDetector(),
                           source.foreground,source.capture,spool=EvidenceSpool(tmp_path),session_id=1)
    now=[100.];monkeypatch.setattr('tracker.monitor.time.monotonic',lambda:now[0])
    watcher.step();now[0]+=1;watcher.step()
    assert not frames(events)
    source.image=Image.new('RGB',(1000,562));watcher.step();watcher.step()
    assert len(frames(events))==1 and frames(events)[0]['summary_readiness']['state']=='incomplete'
    watcher.stop();assert len(frames(events))==1


def test_explicit_header_admission_and_later_evidence_for_same_match(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    early=observation('one');early.update(recognition_version=9,team_kills_policy='summary-header-only')
    cid=store.add_capture(sid,'early','early',early)
    assert not reconcile(store,sid)[1] and store.totals(sid)[0]==0
    import pytest
    with pytest.raises(ValueError,match='小队总击杀'):
        store.confirm(cid,[])
    complete=dict(early,lines=header(9))  # Cards sum to 3; explicit 9 is authoritative.
    other=store.add_capture(sid,'full','full',complete);reconcile(store,sid)
    assert store.totals(sid)[0]==1
    assert store.totals(sid)[1][0]['team_kills']==9
    assert store.totals(sid)[1][0]['participation']==1/9
    assert len(store.visible_captures(sid))==1
    store.close()


def test_incomplete_header_is_not_final_and_complete_header_wins(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    early=dict(observation('one'),recognition_version=9,team_kills_policy='summary-header-only',
               lines=header(0),summary_readiness=dict(state='incomplete'))
    cid=store.add_capture(sid,'early','early',early)
    assert not reconcile(store,sid)[1] and store.totals(sid)[0]==0
    final=dict(early,lines=header(9),summary_readiness=dict(state='complete'))
    full=store.add_capture(sid,'full','full',final);reconcile(store,sid)
    assert store.capture(cid)['merged_into']==full
    assert store.totals(sid)[0]==1 and store.totals(sid)[1][0]['team_kills']==9
    store.close()


def test_exact_copy_later_stable_upgrades_readiness_without_new_ocr(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    data=dict(observation('one'),recognition_version=9,team_kills_policy='summary-header-only',
              lines=header(0),summary_readiness=dict(state='incomplete'))
    cid=store.add_capture(sid,'image','same-pixels',data);reconcile(store,sid)
    ocr_count=store.db.execute('SELECT count(*) FROM ocr_results').fetchone()[0]
    assert not store.promote_readiness(cid,dict(match_key='another',summary_readiness=dict(state='complete')))
    assert store.promote_readiness(cid,dict(match_key='one',summary_readiness=dict(state='complete')))
    reconcile(store,sid)
    assert store.totals(sid)[0]==1 and store.totals(sid)[1][0]['team_kills']==0
    assert store.db.execute('SELECT count(*) FROM ocr_results').fetchone()[0]==ocr_count
    assert len(store.captures(sid))==1
    store.close()


def test_sampler_reemits_exact_partial_as_complete_after_continuous_stability(tmp_path,monkeypatch):
    source=Sequence();source.image=page();events=[]
    watcher=SummaryWatcher(lambda k,v:events.append((k,v)),(0,0,1000,562),SummaryDetector(),
                           source.foreground,source.capture,spool=EvidenceSpool(tmp_path),session_id=1)
    now=[100.];monkeypatch.setattr('tracker.monitor.time.monotonic',lambda:now[0])
    watcher.step()  # visible, but not yet stable
    source.image=Image.new('RGB',(1000,562))
    now[0]=100.5;watcher.step();now[0]=101.;watcher.step()
    assert len(frames(events))==1 and watcher.episode_count==0 and watcher.episode_partial_saved
    source.image=page()
    for t in (101.5,102.,102.5):now[0]=t;watcher.step()
    assert len(frames(events))==1  # absence cannot count as stable time
    for t in (103.,103.5):now[0]=t;watcher.step()
    assert len(frames(events))==2
    assert frames(events)[1]['summary_readiness']['state']=='complete'
    assert watcher.episode_count==1 and len(watcher.episode_hashes)==1


def test_next_entry_never_reuses_incomplete_bootstrap_match_key(tmp_path,monkeypatch):
    source=Sequence();source.image=page(False);events=[]
    watcher=SummaryWatcher(lambda k,v:events.append((k,v)),(0,0,1000,562),SummaryDetector(),
                           source.foreground,source.capture,spool=EvidenceSpool(tmp_path),session_id=1)
    now=[100.];monkeypatch.setattr('tracker.monitor.time.monotonic',lambda:now[0])
    watcher.step();old=watcher.epoch
    token=(watcher.probe_generation,100.);watcher.probe_pending=token
    watcher.submit_gameplay(token,dict(kind='selection',evidence=['located entry']))
    watcher._gameplay_results()
    assert len(frames(events))==1 and frames(events)[0]['match_key']==old
    assert watcher.epoch==old and watcher.cycle.summary_pending
    watcher.accept_summary(frames(events)[0])
    token=(watcher.probe_generation,100.);watcher.probe_pending=token
    watcher.submit_gameplay(token,dict(kind='selection',evidence=['located entry']))
    watcher._gameplay_results()
    assert watcher.epoch!=old and not watcher.locked


def test_beta6_phase_keeps_match_but_does_not_exhaust_complete_attempts():
    import time
    from tracker.domain import MatchCycle
    cycle=MatchCycle();cycle.observe_start('selection',['entry']);context=cycle.summary_context()
    cycle.summary_preserved(context)
    phase=dict(version=14,cycle=cycle.snapshot(),episode_key='old-episode',episode_context=context,
               episode_hashes=['early-one','early-two','early-three'],episode_seen_at=time.time(),
               recent_hashes=['early-one','early-two','early-three'])
    watcher=SummaryWatcher(lambda *_:None,(0,0,1000,562),SummaryDetector(),initial_phase=phase)
    assert watcher.epoch==cycle.match_key and watcher.episode_context==context
    assert watcher.episode_count==0 and watcher.episode_hashes==set(phase['episode_hashes'])
    assert watcher.phase()['summary_readiness_version']==1


def test_ocr_retry_cannot_erase_readiness_provenance(tmp_path):
    store=Store(tmp_path);sid=store.start_session(mode=3)
    data=dict(observation('one'),recognition_version=9,team_kills_policy='summary-header-only',
              lines=header(0),summary_readiness=dict(state='incomplete'))
    cid=store.add_capture(sid,'same-image','same-hash',data)
    fresh=dict(data);fresh.pop('summary_readiness');fresh.pop('match_key')
    store.update_recognition(cid,dict(fresh));reconcile(store,sid)
    assert store.capture(cid)['result']['summary_readiness']['state']=='incomplete'
    assert store.totals(sid)[0]==0
    assert store.promote_readiness(cid,dict(match_key='one',summary_readiness=dict(state='complete')))
    store.update_recognition(cid,dict(fresh));reconcile(store,sid)
    assert store.capture(cid)['result']['summary_readiness']['state']=='complete'
    assert store.totals(sid)[0]==1
    store.close()
