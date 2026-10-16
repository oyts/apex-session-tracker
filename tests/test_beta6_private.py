"""Optional private regression, with no images or player identities redistributed."""
import json
import shutil
from pathlib import Path
import pytest
from tracker.store import Store
from tracker.session import reconcile
from tracker.summary_surface import surface_evidence

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'private-diagnostics/2026-10-17-002551'
pytestmark=pytest.mark.skipif(not (FIXTURE/'snapshot.json').is_file(),reason='private local regression not distributed')


def test_latest_real_return_migration_manual_preservation_and_idempotence(tmp_path):
    snapshot=json.loads((FIXTURE/'snapshot.json').read_text('utf-8-sig'))
    shutil.copy2(FIXTURE/'original.sqlite3',tmp_path/'tracker.sqlite3')
    store=Store(tmp_path)
    with store.db:
        for c in snapshot['captures']:
            store.db.execute('UPDATE captures SET image=? WHERE id=?',
                             (str(FIXTURE/'screenshots'/Path(c['image']).name),c['id']))
    original_stats={c['id']:store.capture(c['id'])['stats'] for c in snapshot['captures']}
    original_hashes={c['id']:c['hash'] for c in snapshot['captures']}
    assert store.totals(17)[0]==9
    assert surface_evidence(store.capture(238),store.capture(239))
    assert surface_evidence(store.capture(251),store.capture(252))
    reconcile(store,17)
    assert store.capture(252)['merged_into']==251
    # An earlier ignored-then-restored pending copy has a stale legacy flag;
    # restoring is not an explicit instruction to count another match.
    assert store.capture(239)['merged_into']==238
    assert len(store.visible_captures(17))==9
    assert store.totals(17)[0]==9
    assert [p['team_kills'] for p in store.totals(17)[1]]==[16]*3
    assert [p['participation'] for p in store.totals(17)[1]]==[11/16,11/16,6/16]
    for cid,stats in original_stats.items():
        assert store.capture(cid)['stats']==stats
        assert store.capture(cid)['hash']==original_hashes[cid]
        assert Path(store.capture(cid)['image']).is_file()
    before=store.totals(17);review_count=store.db.execute('SELECT count(*) FROM reviews').fetchone()[0]
    reconcile(store,17);assert store.db.execute('SELECT count(*) FROM reviews').fetchone()[0]==review_count
    store.close();store=Store(tmp_path);reconcile(store,17)
    assert store.totals(17)==before and store.capture(252)['merged_into']==251
    assert store.db.execute('PRAGMA user_version').fetchone()[0]==16
    assert (tmp_path/'backups/before-schema16.sqlite3').is_file()
    store.close()
