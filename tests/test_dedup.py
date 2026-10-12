import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from tracker.dedup import image_digest
from tracker.ocr import Recognizer
from tracker.store import Store
from tests.test_store import result, rows


def summary(marker='2:1019022:11631705:1234abcd'):
    data = result()
    data['lines'] = [dict(text=marker, score=.999, x=1, y=1158, bottom=1178, cy=1168)]
    for i, card in enumerate(data['cards']):
        x = 236+552*i
        data['lines'].extend([
            dict(text='造成伤害', score=.99, x=x, y=533, bottom=563, cy=548),
            dict(text=f"{card['kills']}/{card['assists']}/3", score=.99, x=x, y=488, bottom=522, cy=505),
            dict(text='7:28', score=.99, x=x, y=632, bottom=669, cy=650.5)])
    return data


def test_same_ocr_all_numbers_names_times_and_marker_cannot_merge_images(tmp_path):
    store = Store(tmp_path); sid = store.start_session()
    first = store.add_capture(sid, 'first.png', 'first-pixels', summary())
    second = store.add_capture(sid, 'second.png', 'other-pixels', summary())
    assert second != first
    assert store.find_duplicate(sid,'third-pixels',summary()) is None
    store.consolidate(sid)
    assert len(store.visible_captures(sid)) == 2
    store.close()


def test_automatic_epoch_merges_only_same_episode_and_survives_restart(tmp_path):
    store=Store(tmp_path);sid=store.start_session()
    data=summary();data.update(source='automatic',automatic_epoch='game-one')
    first=store.add_capture(sid,'one.png','image-one',data)
    assert store.add_capture(sid,'animation.png','image-two',data)==first
    store.set_setting('automatic_phase_'+str(sid),dict(epoch='game-one',locked=True))
    store.close();store=Store(tmp_path)
    assert store.automatic_phase(sid)==dict(epoch='game-one',locked=True)
    data=summary();data.update(source='automatic',automatic_epoch='game-two')
    second=store.add_capture(sid,'next.png','image-three',data)
    assert first!=second  # Next actual game, even with exactly equal OCR statistics.
    manual=store.add_capture(sid,'import.png','image-four',summary())
    assert manual not in (first,second)
    store.consolidate(sid)
    assert len(store.visible_captures(sid))==3
    store.close()


def test_verified_duplicate_repair_preserves_correction_and_all_originals(tmp_path):
    store=Store(tmp_path);sid=store.start_session()
    ids=[store.add_capture(sid,f'{i}.png',f'pixels-{i}',summary()) for i in range(3)]
    for cid in ids:store.confirm(cid,rows())
    corrected=rows();corrected[1]['damage']=1430
    store.confirm(ids[1],corrected,manual=True)
    assert store.totals(sid)[0]==3
    store.merge_verified_match(ids)
    assert store.totals(sid)[0]==1
    assert [c['id'] for c in store.visible_captures(sid)]==[ids[1]]
    assert len(store.captures(sid))==3 and all(store.capture(cid)['stats'] for cid in ids)
    assert store.totals(sid)[1][0]['damage']==1430
    store.update_recognition(ids[0],summary())
    store.consolidate(sid)
    store.close();store=Store(tmp_path);store.consolidate(sid)
    assert store.totals(sid)[0]==1 and store.totals(sid)[1][0]['damage']==1430
    store.close()


def test_legacy_upgrade_keeps_captured_summary_locked(tmp_path):
    store=Store(tmp_path);sid=store.start_session()
    assert not store.automatic_phase(sid)['locked']
    data=summary();data['source']='automatic'
    store.add_capture(sid,'legacy.png','old-image',data)
    assert store.automatic_phase(sid)['locked']
    store.close()


def test_verified_repair_handoff_consumed_once_and_hash_guarded(tmp_path):
    store=Store(tmp_path);sid=store.start_session()
    ids=[store.add_capture(sid,f'{i}.png',f'pixels-{i}',summary()) for i in range(3)]
    for cid in ids:store.confirm(cid,rows())
    request=dict(reason='user_verified_same_match',ids=ids,
                 hashes={str(cid):store.capture(cid)['hash'] for cid in ids})
    store.set_setting('verified_match_repairs',[request]);store.close()
    store=Store(tmp_path)
    assert store.totals(sid)[0]==1 and not store.setting('verified_match_repairs')
    store.close();store=Store(tmp_path)
    assert store.totals(sid)[0]==1 and len(store.captures(sid))==3
    request['hashes'][str(ids[0])]='changed'
    store.set_setting('verified_match_repairs',[request]);store.close()
    store=Store(tmp_path)
    assert store.setting('verified_match_repairs') and '记录已变化' in store.storage_warning
    store.close()


@pytest.mark.parametrize('status', ['pending', 'confirmed', 'discarded'])
def test_duplicate_lifecycle_restart_and_session_boundary(tmp_path, status):
    store = Store(tmp_path)
    sid = store.start_session()
    first = store.add_capture(sid, 'original.png', 'exact', summary())
    if status == 'confirmed':
        store.confirm(first, rows())
    if status == 'discarded':
        store.discard(first)
    different = store.add_capture(sid, 'animated.png', 'other-pixels', summary())
    assert different != first
    assert store.add_capture(sid, 'exact.png', 'exact', result()) == first
    assert store.capture(first)['status'] == status
    store.close()
    store = Store(tmp_path)
    assert store.add_capture(sid, 'again.png', 'exact', summary()) == first
    assert store.add_capture(sid, 'another.png', 'third-pixels', summary()) not in (first,different)
    if status == 'discarded':
        store.restore(first)
        store.confirm(first, rows())
        assert store.totals(sid)[0] == 1
    # Equal numbers and different markers remain distinct matches.
    other = store.add_capture(sid, 'newmatch.png', 'new-image', summary('2:1019022:11631705:abcd4321'))
    assert other != first
    store.end_session(sid)
    next_session = store.start_session()
    assert store.add_capture(next_session, 'original.png', 'exact', summary()) != first
    store.close()


def test_simultaneous_duplicate_insertion_is_atomic(tmp_path):
    store = Store(tmp_path)
    sid = store.start_session()
    store.close()

    def insert(_):
        store = Store(tmp_path)
        try:
            return store.add_capture(sid, 'same.png', 'exact', result())
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(insert, range(2)))
    assert ids[0] == ids[1]
    store = Store(tmp_path)
    assert len(store.captures(sid)) == 1
    store.close()


def test_legacy_confirmed_copy_is_preferred_without_deleting_history(tmp_path):
    import json
    store = Store(tmp_path)
    sid = store.start_session()
    first = store.add_capture(sid, 'first.png', 'exact', summary())
    with store.db:
        second = store.db.execute('INSERT INTO captures(session_id,created,image,hash,result,duplicate_of) VALUES (?,?,?,?,?,?)',
                                  (sid,'2026-10-03','legacy-copy.png','exact',json.dumps(summary()),first)).lastrowid
    corrected = rows()
    corrected[1]['damage'] = 1430
    store.confirm(second, corrected, allow_duplicate=True)
    assert store.add_capture(sid,'new.png','exact',summary()) == second
    assert len(store.captures(sid)) == 2 and store.capture(first)['status'] == 'pending'
    assert store.totals(sid)[1][0]['damage'] == 1430
    store.close()


def test_exact_fingerprint_ignores_file_encoding_but_not_pixels():
    image = Image.new('RGB', (20, 20), 'red')
    assert image_digest(image) == image_digest(image.convert('RGBA'))
    other = image.copy()
    other.putpixel((1, 1), (254, 0, 0))
    assert image_digest(other) != image_digest(image)


@pytest.mark.parametrize('readings,valid', [([('3/0', .96), ('3/0', .95)], True),
    ([('3/0', .96), ('3/1', .99)], False), ([('3/0', .96), ('3/0', .93)], False),
    ([('3/O', .99)], False), ([('', .99)], False)])
def test_numeric_refinement_requires_two_confident_agreeing_reads(readings, valid):
    reader = Recognizer.__new__(Recognizer)
    values = iter(readings)
    def fake_engine(image, **kwargs):
        assert kwargs == {'use_det': False, 'use_cls': False}
        text, score = next(values)
        return SimpleNamespace(txts=[text], scores=[score])
    reader.engine = fake_engine
    line = dict(text='3/0/3', score=.918, x=10, y=10, right=100, bottom=40)
    assert bool(reader.verify_kills_assists(Image.new('RGB', (120, 60)), line, (3, 0, 3))) == valid
