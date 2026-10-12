import copy,json,uuid
from pathlib import Path
import pytest
from PIL import Image
from tracker.media import MANAGED_NAME,execute_cleanup
from tracker.store import Store
from tracker.session import reconcile
from tests.test_dedup import summary as base_summary
from tests.test_store import rows


def summary(marker='2:1019022:11631705:1234abcd'):
    data=base_summary(marker)
    for i,card in enumerate(data['cards']):
        data['lines'].append(dict(text=str(card['damage']),score=.99,x=236+552*i,y=568,bottom=596,cy=582))
    return data


def legacy(store,sid,data,hash='copy',path='copy.png'):
    with store.db:
        return store.db.execute('INSERT INTO captures(session_id,created,image,hash,result) VALUES (?,?,?,?,?)',
                                (sid,'2026-10-06',path,hash,json.dumps(data))).lastrowid


def test_partial_or_complete_ocr_agreement_never_proves_identical_pixels(tmp_path):
    a=summary(); b=copy.deepcopy(a)
    b['cards'][2].update(name='occluded',name_score=.4)
    b['cards'][0].update(kills=None,assists=None,score=0)
    store=Store(tmp_path);sid=store.start_session()
    first=store.add_capture(sid,'a.png','a-pixels',a)
    second=store.add_capture(sid,'b.png','b-pixels',b)
    third=store.add_capture(sid,'c.png','c-pixels',a)
    store.consolidate(sid)
    assert len({first,second,third})==3 and len(store.visible_captures(sid))==3
    store.close()


def test_manual_correction_merges_existing_pending_copies_and_restart(tmp_path):
    store=Store(tmp_path); sid=store.start_session()
    a=store.add_capture(sid,'original.png','first',summary())
    b=legacy(store,sid,summary(),'first')
    c=legacy(store,sid,summary(),'first')
    revised=rows(); revised[1]['damage']=1430
    store.confirm(a,revised,manual=True)
    reconcile(store,sid)
    assert len(store.captures(sid))==3 and len(store.visible_captures(sid))==1
    assert store.canonical_id(b)==store.canonical_id(c)==a
    assert store.totals(sid)[0]==1 and store.totals(sid)[1][0]['damage']==1430
    # A review opened before grouping can still save its own corrected card mapping.
    revised[1]['damage']=1530
    store.confirm(b,revised,manual=True)
    reconcile(store,sid)
    assert store.canonical_id(a)==b and store.totals(sid)[1][0]['damage']==1530
    store.close(); store=Store(tmp_path)
    assert len(store.visible_captures(sid))==1 and store.totals(sid)[0]==1
    store.discard(b); reconcile(store,sid)
    assert store.totals(sid)[0]==0
    store.restore(b); store.confirm(b,revised,manual=True); reconcile(store,sid)
    assert store.totals(sid)[0]==1
    store.close()


def test_explicit_distinct_match_and_different_marker_are_not_merged(tmp_path):
    store=Store(tmp_path); sid=store.start_session()
    first=store.add_capture(sid,'first','h1',summary())
    second=legacy(store,sid,dict(summary(),keep_separate=True),'h2')
    third=store.add_capture(sid,'third','h3',summary('2:1019022:11631705:8765abcd'))
    store.consolidate(sid)
    assert len(store.visible_captures(sid))==3
    assert all(not store.capture(cid)['merged_into'] for cid in (first,second,third))
    store.close()


def test_legacy_false_merge_and_duplicate_hint_recover_without_losing_manual_stats(tmp_path):
    store=Store(tmp_path);sid=store.start_session()
    first=store.add_capture(sid,'first.png','first-pixels',summary())
    store.confirm(first,rows(),remember=True,manual=True)
    baseline=store.capture(first)['stats']
    second=legacy(store,sid,summary(),'different-pixels')
    with store.db:
        store.db.execute('UPDATE captures SET merged_into=?,duplicate_of=? WHERE id=?',(first,first,second))
    assert len(store.visible_captures(sid))==1
    _,saved=reconcile(store,sid)
    assert saved==[second] and store.totals(sid)[0]==2
    assert store.capture(second)['merged_into'] is None and store.capture(second)['duplicate_of'] is None
    assert store.capture(first)['stats']==baseline
    assert reconcile(store,sid)[1]==[]
    store.close();store=Store(tmp_path)
    assert store.totals(sid)[0]==2 and len(store.visible_captures(sid))==2
    store.close()


def photo(root):
    path=root/f'{uuid.uuid4().hex}.png'
    Image.new('RGB',(100,100),'red').save(path)
    return str(path)


def test_storage_switch_cleanup_duplicates_and_keep_history(tmp_path):
    store=Store(tmp_path/'data');sid=store.start_session()
    original=photo(store.screenshots)
    first=store.add_capture(sid,original,'first',summary());store.confirm(first,rows())
    copy_path=photo(store.screenshots);legacy(store,sid,summary(),'first',path=copy_path)
    store.consolidate(sid);store.end_session(sid)
    target=tmp_path/'new location'/MANAGED_NAME
    store.set_image_directory(target)
    orphan=photo(target)
    unrelated=target/'family-photo.png';unrelated.write_bytes(b'keep')
    log=store.directory/'app.log';log.write_text('old errors',encoding='utf-8')
    plan=store.cleanup_plan('duplicates')
    assert set(plan['photos'])=={copy_path,orphan}
    result=execute_cleanup(plan);store.finish_cleanup(result)
    assert not Path(copy_path).exists() and not Path(orphan).exists()
    assert Path(original).exists() and unrelated.read_bytes()==b'keep'
    assert store.totals(sid)[0]==1 and log.stat().st_size==0
    assert store.capture(first)['image']==original
    store.close();store=Store(tmp_path/'data')
    assert store.screenshots==target
    result=execute_cleanup(store.cleanup_plan('photos'));store.finish_cleanup(result)
    assert not Path(original).exists() and store.capture(first)['image']==''
    assert store.totals(sid)[0]==1 and len(store.sessions())==1
    assert unrelated.exists()
    result=execute_cleanup(store.cleanup_plan('history'));store.finish_cleanup(result)
    assert not store.sessions() and not store.captures(sid) and len(store.players())==3
    assert store.setting('image_directory')==str(target)
    assert store.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    store.close()


def test_cleanup_never_deletes_external_photo_and_handles_failure(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');sid=store.start_session()
    external=tmp_path/f'{uuid.uuid4().hex}.png';external.write_bytes(b'external')
    owned=photo(store.screenshots)
    store.add_capture(sid,str(external),'outside',summary())
    legacy(store,sid,summary(),'inside',owned)
    store.end_session(sid)
    plan=store.cleanup_plan('photos')
    assert str(external) not in plan['photos'] and plan['skipped']==1
    original_unlink=Path.unlink
    def fail(self,*a,**k):
        if str(self)==owned: raise PermissionError('locked fixture')
        return original_unlink(self,*a,**k)
    monkeypatch.setattr(Path,'unlink',fail)
    result=execute_cleanup(plan);store.finish_cleanup(result)
    assert len(result['failures'])==1 and Path(owned).exists() and external.exists()
    assert store.capture(2)['image']==owned
    store.close()
