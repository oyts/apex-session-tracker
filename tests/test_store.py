import pytest

from tracker.ocr import auto_rows, parse_damage, parse_triple
from tracker.store import Store
from tracker.windows import parse_hotkey


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path)
    yield instance
    instance.close()


def result(names=("甲", "示例缺□", "丙")):
    return {"warnings": [], "cards": [
        {"card": i, "name": name, "damage": damage, "kills": i, "assists": i+1,
         "score": 0.99, "name_score": 0.99}
        for i, (name, damage) in enumerate(zip(names, (174, 430, 182)))]}


def rows():
    # Card order differs from player order.
    return [{"card": i, "player_id": pid, "damage": damage, "kills": i, "assists": i+1}
            for i, (pid, damage) in enumerate(zip((3, 1, 2), (174, 430, 182)))]


def test_identity_follows_cards_and_survives_reopening(store):
    sid = store.start_session()
    cid = store.add_capture(sid, "sample.png", "hash", result())
    store.confirm(cid, rows(), remember=True)
    assert store.match_player(" 示例缺□ ") == 1
    count, totals = store.totals(sid)
    assert count == 1
    assert [r["damage"] for r in totals] == [430, 182, 174]
    reloaded = Store(store.directory)
    assert reloaded.active_session()["id"] == sid
    assert reloaded.totals(sid) == (count, totals)
    assert reloaded.match_player("示例缺□") == 1
    reloaded.close()


def test_correction_replaces_old_values_and_ignore_removes_totals(store):
    sid = store.start_session()
    cid = store.add_capture(sid, "sample.png", "hash", result())
    store.confirm(cid, rows())
    revised = rows()
    revised[1]["damage"] = 1430
    store.confirm(cid, revised)
    assert store.totals(sid)[0] == 1
    assert store.totals(sid)[1][0]["damage"] == 1430
    store.discard(cid)
    assert store.totals(sid)[0] == 0
    assert sum(p["damage"] for p in store.totals(sid)[1]) == 0


def test_identical_statistics_different_games_can_both_count(store):
    sid = store.start_session()
    first = store.add_capture(sid, "first.png", "image1", result())
    second = store.add_capture(sid, "second.png", "image2", result())
    store.confirm(first, rows())
    assert store.capture(second)["duplicate_of"] is None
    store.confirm(second, rows())
    assert store.totals(sid)[0] == 2


def test_exact_same_image_reuses_record_and_preserves_correction(store):
    sid = store.start_session()
    first = store.add_capture(sid, "first.png", "image1", result())
    store.confirm(first, rows())
    corrected = rows()
    corrected[1]['damage'] = 1430
    store.confirm(first, corrected)
    second = store.add_capture(sid, "second.png", "image1", result())
    assert second == first
    assert store.totals(sid)[0] == 1
    assert store.totals(sid)[1][0]['damage'] == 1430
    assert len(store.captures(sid)) == 1


def test_bad_correction_preserves_previous_stats(store):
    sid = store.start_session()
    cid = store.add_capture(sid, "sample.png", "hash", result())
    store.confirm(cid, rows())
    previous = store.totals(sid)
    invalid = rows()
    invalid[1]["player_id"] = invalid[0]["player_id"]
    with pytest.raises(ValueError):
        store.confirm(cid, invalid)
    invalid = rows()
    invalid[1]["damage"] = -1
    with pytest.raises(ValueError):
        store.confirm(cid, invalid)
    assert store.totals(sid) == previous


def test_pending_missing_numbers_never_become_zero_or_count(store):
    sid = store.start_session()
    incomplete = result()
    incomplete["cards"][1]["damage"] = None
    cid = store.add_capture(sid, "sample.png", "hash", incomplete)
    assert store.capture(cid)["result"]["cards"][1]["damage"] is None
    assert auto_rows(incomplete, store) is None
    assert store.totals(sid)[0] == 0


def test_glyph_box_and_ambiguous_names_require_confirmation(store):
    with store.db:
        for pid, name in enumerate(("甲", "示例缺□", "丙"), 1):
            store.bind_alias(name, pid)
    assert [r["player_id"] for r in auto_rows(result(), store)] == [1, 2, 3]
    swapped = result(("丙", "甲", "示例缺□"))
    assert [r["player_id"] for r in auto_rows(swapped, store)] == [3, 1, 2]
    with store.db:
        store.bind_alias("示例缺□", 1)
    assert store.match_player("示例缺□") is None
    assert auto_rows(result(), store) is None
    assert store.match_player("示例完整字") is None


def test_session_boundary_and_resume(store):
    sid = store.start_session()
    with pytest.raises(ValueError):
        store.start_session()
    store.end_session(sid)
    assert store.active_session() is None
    second = store.start_session()
    assert second != sid
    assert store.totals(second)[0] == 0


def test_hotkey_validation_and_numeric_parsing():
    assert parse_hotkey("CTRL + END") == (0x4002, 0x23)
    assert parse_hotkey("Shift+Home") == (0x4004, 0x24)
    assert parse_hotkey("Ctrl+Alt+F8") == (0x4003, 0x77)
    for value in ("F8", "Ctrl+F12", "Ctrl+Ctrl+F8", "Alt+Unknown"):
        with pytest.raises(ValueError):
            parse_hotkey(value)
    assert parse_triple("0 / 2 / 3") == (0, 2, 3)
    assert parse_triple("0/2") is None
    assert parse_triple("0/O/3") is None
    assert parse_damage("1,430") == 1430
    assert parse_damage("43O") is None


@pytest.mark.parametrize("participants", [[1], [1, 3]])
def test_small_roster_ignores_random_teammates_and_follows_identity(store, participants):
    with store.db:
        for card, row in zip(result()["cards"], rows()):
            store.bind_alias(card['name'], row['player_id'])
    data = result()
    # Untracked random teammates need not have readable numbers.
    for card, row in zip(data['cards'], rows()):
        if row['player_id'] not in participants:
            card['name'] = '路人'
            card['damage'] = None
    data['warnings'] = ['路人卡片数据不完整']
    sid = store.start_session(participants)
    auto = auto_rows(data, store, participants)
    assert auto and {r['player_id'] for r in auto} == set(participants)
    cid = store.add_capture(sid, 'sample.png', 'hash', data)
    store.confirm(cid, auto, remember=True)
    count, totals = store.totals(sid)
    assert count == 1 and [p['id'] for p in totals] == participants
    assert next(p for p in totals if p['id'] == 1)['damage'] == 430
    revised = [dict(row) for row in auto]
    next(row for row in revised if row['player_id'] == 1)['damage'] = 1430
    store.confirm(cid, revised)
    assert store.totals(sid)[0] == 1
    reloaded = Store(store.directory)
    assert reloaded.session_player_ids(sid) == participants
    assert reloaded.totals(sid) == store.totals(sid)
    reloaded.close()
    with pytest.raises(ValueError):
        store.confirm(cid, rows())


def test_old_history_migrates_and_custom_hotkey_survives(tmp_path):
    import sqlite3
    import json
    database = sqlite3.connect(tmp_path / 'tracker.sqlite3')
    database.executescript('''CREATE TABLE sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                            started TEXT NOT NULL, ended TEXT);
                            INSERT INTO sessions VALUES(1,'旧记录','2026-10-03',NULL);
                            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
    database.execute('INSERT INTO settings VALUES (?,?)', ('hotkey', json.dumps('Shift+Home')))
    database.commit()
    database.close()
    store = Store(tmp_path)
    assert store.session_player_ids(1) == [1, 2, 3]
    assert store.active_session()['title'] == '旧记录'
    assert store.setting('hotkey') == 'Shift+Home'
    store.close()


def test_ambiguous_small_roster_never_auto_assigns(store):
    with store.db:
        store.bind_alias('示例缺□', 1)
        store.bind_alias('示例缺□', 2)
    assert auto_rows(result(), store, [1]) is None

def test_session_title_works_with_c_time_locale(store):
    import locale
    previous=locale.setlocale(locale.LC_TIME)
    try:
        locale.setlocale(locale.LC_TIME,'C')
        sid=store.start_session(mode=3)
        title=next(s['title'] for s in store.sessions() if s['id']==sid)
        assert '月' in title and '日' in title and title.endswith('3人统计')
    finally:
        locale.setlocale(locale.LC_TIME,previous)
