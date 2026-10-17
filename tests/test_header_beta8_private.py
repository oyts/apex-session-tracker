"""Private opt-in regression; no user's pixels/names are distributed."""
import json
import os
from pathlib import Path
import pytest
from PIL import Image
from tracker.ocr import Recognizer
from tracker.automatic import SummaryDetector
from tracker.summary_readiness import SummaryReadiness

ROOT=Path(__file__).resolve().parents[1]
BACKUP=ROOT/'private-diagnostics/2026-10-17-233919'
pytestmark=pytest.mark.skipif(not BACKUP.is_dir() or os.getenv('RUN_OCR_TESTS')!='1',reason='Private opt-in OCR regression')


def test_actual_thin_ones_and_tinted_caption():
    snapshot=json.loads((BACKUP/'snapshot.json').read_text('utf-8'))
    engine=Recognizer();detector=SummaryDetector()
    for cid,expected in ((301,0),(303,1),(304,1),(309,2),(312,1),(313,1),(314,1),(315,1),(319,1)):
        capture=next(c for c in snapshot['captures'] if c['id']==cid)
        path=BACKUP/'screenshots'/Path(capture['image']).name
        result=engine.recognize(path)
        assert result['team_kills']==expected
        assert result['team_kills_source']=='summary-header'
        assert result['squad_kills_verification']['score']>=.94
        assert [(c['damage'],c['kills'],c['assists']) for c in result['cards']]==[
            (c['damage'],c['kills'],c['assists']) for c in capture['result']['cards']]
        found=detector.detect(Image.open(path).convert('RGB'))
        assert found['header_visible']
        # Replay saved frames only; this is not evidence of the user's live
        # duration on that page, nor permission to promote a single old partial.
        gate=SummaryReadiness();assert not gate.observe(found,0);assert gate.observe(found,1.5)
