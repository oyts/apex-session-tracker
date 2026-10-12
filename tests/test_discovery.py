"""Session inference scenarios use explicit synthetic OCR results, not model accuracy claims."""
import pytest

from tracker.session import reconcile
from tracker.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path)
    yield store
    store.close()


def frame(names, damages):
    return {'warnings': [], 'cards': [
        {'card': i, 'name': name, 'name_score': .99, 'score': .99,
         'damage': damage, 'kills': i, 'assists': i+1}
        for i, (name, damage) in enumerate(zip(names, damages))]}


def add(store, sid, names, damages, digest=None):
    data = frame(names, damages)
    cid = store.add_capture(sid, 'synthetic.png', digest or str(len(store.captures(sid))), data)
    return cid


def by_name(store, sid):
    return {p['name']: p for p in store.totals(sid)[1]}


def test_duo_two_matches_intersection_replays_both_and_averages(store):
    sid = store.start_session(mode=2)
    first = add(store, sid, ('A', '示例缺□', 'C'), (100, 200, 300))
    state, saved = reconcile(store, sid)
    assert not state['resolved'] and saved == [] and store.totals(sid)[0] == 0
    second = add(store, sid, ('D', '示例缺□', 'A'), (400, 50, 30))
    state, saved = reconcile(store, sid)
    assert state['resolved'] and saved == [first, second]
    assert store.totals(sid)[0] == 2
    assert set(by_name(store, sid)) == {'A', '示例缺□'}
    assert by_name(store, sid)['A']['damage'] == 130
    assert by_name(store, sid)['A']['average_damage'] == 65
    assert by_name(store, sid)['示例缺□']['average_damage'] == 125
    assert reconcile(store, sid)[1] == []
    # Automatic discovery stays session-local and never guesses the missing glyph.
    assert store.match_player('示例完整字', sid) is None
    assert store.match_player('示例缺□') is None


def test_same_three_wait_for_third_match_and_resume(store):
    sid = store.start_session(mode=2)
    add(store, sid, ('A', 'B', 'C'), (10, 20, 30))
    add(store, sid, ('C', 'A', 'B'), (35, 15, 25))
    state, saved = reconcile(store, sid)
    assert not state['resolved'] and len(state['candidates']) == 3 and saved == []
    reloaded = Store(store.directory)
    assert not reconcile(reloaded, sid)[0]['resolved']
    add(reloaded, sid, ('B', 'D', 'A'), (40, 50, 60))
    assert reconcile(reloaded, sid)[0]['resolved']
    assert reloaded.totals(sid)[0] == 3
    assert by_name(reloaded, sid)['A']['damage'] == 85
    assert by_name(reloaded, sid)['A']['average_damage'] == pytest.approx(85/3)
    reloaded.close()


def test_solo_waits_for_fourth_match(store):
    sid = store.start_session(mode=1)
    for names, damages in [(('A', 'B', 'C'), (10, 20, 30)),
                            (('A', 'B', 'C'), (15, 25, 35)),
                            (('B', 'D', 'A'), (40, 50, 60))]:
        add(store, sid, names, damages)
        assert not reconcile(store, sid)[0]['resolved']
    add(store, sid, ('E', 'A', 'F'), (20, 70, 30))
    assert reconcile(store, sid)[0]['resolved']
    assert store.totals(sid)[0] == 4
    assert by_name(store, sid)['A']['damage'] == 155
    assert by_name(store, sid)['A']['average_damage'] == 38.75


def test_duplicate_frame_never_provides_second_match_evidence(store):
    sid = store.start_session(mode=2)
    add(store, sid, ('A', 'B', 'C'), (10, 20, 30), digest='same-image')
    add(store, sid, ('A', 'B', 'D'), (40, 50, 60), digest='same-image')
    state, saved = reconcile(store, sid)
    assert not state['resolved'] and state['observations'] == 1 and saved == []


def test_low_confidence_or_colliding_nicknames_do_not_eliminate_candidates(store):
    sid = store.start_session(mode=2)
    add(store, sid, ('A', 'B', 'C'), (10, 20, 30))
    bad = frame(('A', 'B', 'D'), (40, 50, 60))
    bad['cards'][2]['name_score'] = .50
    store.add_capture(sid, 'synthetic.png', 'low', bad)
    add(store, sid, ('A', 'A', 'B'), (70, 80, 90))
    state, _ = reconcile(store, sid)
    assert not state['resolved'] and state['observations'] == 1
    with store.db:
        store.bind_alias('B', 1)
        store.bind_alias('B', 2)
    assert reconcile(store, sid)[0]['observations'] == 0


def test_manual_fallback_replays_history_and_invalid_input_never_locks(store):
    sid = store.start_session(mode=2)
    first = add(store, sid, ('A', 'B', 'C'), (10, 20, 30))
    second = add(store, sid, ('A', 'B', 'C'), (40, 50, 60))
    assert not reconcile(store, sid)[0]['resolved']
    rows = [{'card': 0, 'player_id': 1, 'damage': 40, 'kills': 0, 'assists': 1},
            {'card': 1, 'player_id': 2, 'damage': -50, 'kills': 1, 'assists': 2}]
    with pytest.raises(ValueError):
        store.confirm(second, rows, manual=True)
    assert store.session_player_ids(sid) == []
    rows[1]['damage'] = 50
    store.confirm(second, rows, manual=True)
    assert reconcile(store, sid)[1] == [first]
    assert store.totals(sid)[0] == 2
    assert by_name(store, sid)['B']['average_damage'] == 35


def test_average_recalculates_after_correction_and_discard(store):
    sid = store.start_session(mode=2)
    first = add(store, sid, ('A', 'B', 'C'), (100, 200, 300))
    second = add(store, sid, ('D', 'B', 'A'), (400, 50, 30))
    reconcile(store, sid)
    rows = store.capture(first)['stats']
    next(r for r in rows if r['damage'] == 100)['damage'] = 300
    store.confirm(first, rows)
    assert by_name(store, sid)['A']['average_damage'] == 165
    store.discard(second)
    assert by_name(store, sid)['A']['average_damage'] == 300
    store.discard(first)
    assert all(p['average_damage'] is None for p in store.totals(sid)[1])


def test_auto_save_toggle_and_new_team_names_do_not_overwrite_old_history(store):
    store.save_players([(1, '原玩家甲'), (2, '原玩家乙'), (3, '原玩家丙')])
    old = store.start_session([1, 2])
    store.end_session(old)
    sid = store.start_session(mode=2)
    add(store, sid, ('NewA', 'NewB', 'C'), (10, 20, 30))
    add(store, sid, ('NewB', 'D', 'NewA'), (40, 50, 60))
    state, saved = reconcile(store, sid, auto_save=False)
    assert state['resolved'] and saved == [] and store.totals(sid)[0] == 0
    assert set(by_name(store, sid)) == {'NewA', 'NewB'}
    assert set(by_name(store, old)) == {'原玩家甲', '原玩家乙'}
    assert len(reconcile(store, sid)[1]) == 2


def test_equal_statistics_in_different_images_auto_count_both(store):
    sid = store.start_session(mode=2)
    add(store, sid, ('A', 'B', 'C'), (10, 20, 30))
    second = add(store, sid, ('A', 'B', 'D'), (10, 20, 40))
    assert reconcile(store, sid)[0]['resolved']
    assert store.totals(sid)[0] == 2
    assert store.capture(second)['status'] == 'confirmed'
    assert {p['name']:p['damage'] for p in store.totals(sid)[1]} == {'A':20,'B':40}
    assert {p['name']:p['average_damage'] for p in store.totals(sid)[1]} == {'A':10,'B':20}


def test_bound_solo_replays_partial_stranger_frame_and_survives_restart(store):
    store.save_players([(1, '完整昵称'), (2, '玩家2'), (3, '玩家3')])
    with store.db:
        store.bind_alias('缺字□', 1)
    sid = store.start_session(mode=1)
    first = frame(('路人', '缺字□', 'you'), (108, 229, 368))
    first['cards'][0]['name_score'] = .53462
    first['cards'][1].update(kills=1, assists=0)
    cid = store.add_capture(sid, 'synthetic.png', 'first', first)
    state, saved = reconcile(store, sid)
    assert not state['resolved'] and not saved
    assert state['partial_observations'] == 1 and '昵称未读全' in state['message']
    second = frame(('其他路人', 'dwdwdd222', '缺字□'), (510, 50, 227))
    second['cards'][2].update(kills=0, assists=0)
    second_id = store.add_capture(sid, 'synthetic.png', 'second', second)
    reloaded = Store(store.directory)
    try:
        state, saved = reconcile(reloaded, sid)
        assert state['resolved'] and saved == [cid, second_id]
        assert reloaded.totals(sid)[0] == 2
        assert by_name(reloaded, sid)['完整昵称']['damage'] == 456
        assert by_name(reloaded, sid)['完整昵称']['kills'] == 1
        assert by_name(reloaded, sid)['完整昵称']['assists'] == 0
        assert by_name(reloaded, sid)['完整昵称']['average_damage'] == 228
        assert reconcile(reloaded, sid)[1] == []
    finally:
        reloaded.close()


def test_partial_names_cannot_narrow_unbound_or_complete_ambiguous_roster(store):
    sid = store.start_session(mode=1)
    add(store, sid, ('A', 'B', 'C'), (10, 20, 30))
    partial = frame(('A', 'D', 'E'), (40, 50, 60))
    partial['cards'][2]['name_score'] = .5
    store.add_capture(sid, 'synthetic.png', 'partial', partial)
    state, _ = reconcile(store, sid)
    assert not state['resolved'] and len(state['candidates']) == 3
    with store.db:
        store.bind_alias('A', 1)
    add(store, sid, ('C', 'A', 'B'), (70, 80, 90))
    state, _ = reconcile(store, sid)
    assert not state['resolved'] and len(state['candidates']) == 3


def test_bound_duo_needs_both_players_repeated_and_preserves_numeric_review(store):
    with store.db:
        store.bind_alias('A', 1)
        store.bind_alias('B', 2)
    sid = store.start_session(mode=2)
    first = frame(('A', 'B', 'C'), (100, 200, 300))
    first['cards'][2]['name_score'] = .5
    first['cards'][0]['score'] = .8
    cid = store.add_capture(sid, 'synthetic.png', 'first', first)
    add(store, sid, ('A', 'D', 'E'), (10, 20, 30))
    assert not reconcile(store, sid)[0]['resolved']
    # Discard a frame with a genuinely different lineup before retrying.
    store.discard(store.captures(sid)[0]['id'])
    second = add(store, sid, ('B', 'F', 'A'), (20, 30, 40))
    state, saved = reconcile(store, sid)
    assert state['resolved'] and saved == [second]
    assert store.capture(cid)['status'] == 'pending'
    assert store.totals(sid)[0] == 1


def test_bound_partial_duplicate_and_ambiguous_identity_never_resolve(store):
    with store.db:
        store.bind_alias('A', 1)
    sid = store.start_session(mode=1)
    partial = frame(('A', 'B', 'C'), (10, 20, 30))
    partial['cards'][2]['name_score'] = .5
    store.add_capture(sid, 'synthetic.png', 'same', partial)
    store.add_capture(sid, 'synthetic.png', 'same', partial)
    assert not reconcile(store, sid)[0]['resolved']
    repeated = frame(('A', 'A', 'C'), (40, 50, 60))
    store.add_capture(sid, 'synthetic.png', 'collision', repeated)
    assert not reconcile(store, sid)[0]['resolved']
    with store.db:
        store.bind_alias('B', 1)
        store.bind_alias('B', 2)
    add(store, sid, ('A', 'B', 'D'), (70, 80, 90))
    assert not reconcile(store, sid)[0]['resolved']


def test_unreadable_target_frame_is_not_negative_evidence_for_bound_players(store):
    with store.db:
        store.bind_alias('A', 1)
    sid = store.start_session(mode=1)
    first = frame(('A', 'B', 'C'), (10, 20, 30))
    first['cards'][2]['name_score'] = .5
    store.add_capture(sid, 'synthetic.png', 'first', first)
    unreadable = frame(('???', 'D', 'E'), (40, 50, 60))
    unreadable['cards'][0]['name_score'] = .5
    failed = store.add_capture(sid, 'synthetic.png', 'missing-target', unreadable)
    add(store, sid, ('F', 'A', 'G'), (70, 80, 90))
    assert reconcile(store, sid)[0]['resolved']
    assert store.totals(sid)[0] == 2
    assert store.capture(failed)['status'] == 'pending'
