"""Real supplied screenshots stay private and outside all public packages."""
import json
import os
import shutil
import sqlite3
from pathlib import Path
import pytest
from PIL import Image
from tracker.automatic import SummaryDetector
from tracker.ocr import Recognizer
from tracker.summary_readiness import SummaryReadiness
from tracker.store import Store
from tracker.session import reconcile

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'private-diagnostics/summary-header-beta7'
SNAPSHOT=ROOT/'private-diagnostics/2026-10-17-211412'
pytestmark=pytest.mark.skipif(not (PRIVATE/'user-complete.png').is_file(),reason='private actual header screenshots not distributed')


@pytest.mark.skipif(os.environ.get('RUN_OCR_TESTS')!='1',reason='real OCR model opt-in')
def test_actual_fully_loaded_zero_and_faded_header_never_derive_cards():
    engine=Recognizer();detector=SummaryDetector()
    full=PRIVATE/'user-complete.png'
    faded=SNAPSHOT/'screenshots/65363d99983c4385b650d828a847599b.png'
    result=engine.recognize(full)
    assert [c['damage'] for c in result['cards']]==[189,317,156]
    assert result['team_kills']==0 and result['team_kills_source']=='summary-header'
    assert result['team_kills_policy']=='summary-header-only'
    f=detector.detect(Image.open(full).convert('RGB'));gate=SummaryReadiness()
    assert not gate.observe(f,0);assert gate.observe(f,1.5)
    f=detector.detect(Image.open(faded).convert('RGB'));assert not f['header_visible']
    result=engine.recognize(faded)
    assert [c['damage'] for c in result['cards']]==[166,75,91]
    assert result['team_kills'] is None and result['team_kills_source']=='missing'


def test_schema17_removes_derived_values_but_keeps_actual_stats(tmp_path):
    shutil.copy2(SNAPSHOT/'original.sqlite3',tmp_path/'tracker.sqlite3')
    with sqlite3.connect(tmp_path/'tracker.sqlite3') as db:
        stats=list(db.execute('SELECT * FROM stats ORDER BY capture_id,player_id'))
        captures=list(db.execute('SELECT id,hash,status FROM captures ORDER BY id'))
    store=Store(tmp_path);reconcile(store,18)
    assert [tuple(r) for r in store.db.execute('SELECT * FROM stats ORDER BY capture_id,player_id')]==stats
    assert [tuple(r) for r in store.db.execute('SELECT id,hash,status FROM captures ORDER BY id')]==captures
    assert store.totals(18)[0]==6
    assert all(p['participation'] is None and p['participation_missing']==1 for p in store.totals(18)[1])
    assert not store.db.execute("SELECT 1 FROM matches WHERE team_kills_source='three-complete-cards'").fetchone()
    assert (tmp_path/'backups/before-schema17.sqlite3').is_file()
    before=store.totals(18);store.close();store=Store(tmp_path)
    assert store.totals(18)==before;store.close()
