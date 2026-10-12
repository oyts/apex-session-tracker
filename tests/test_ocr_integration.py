"""Opt-in real-model tests: RUN_OCR_TESTS=1 python -m pytest tests/test_ocr_integration.py."""
import os
from pathlib import Path

import pytest
from PIL import Image

from tracker.ocr import Recognizer


pytestmark = pytest.mark.skipif(os.environ.get("RUN_OCR_TESTS") != "1", reason="real OCR model opt-in")
SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "match-summary.png"


@pytest.fixture(scope="module")
def engine():
    return Recognizer()


def assert_numbers(result, expected):
    assert result["warnings"] == []
    assert [card["damage"] for card in result["cards"]] == expected
    assert [card["kills"] for card in result["cards"]] == [0] * len(expected)
    assert [card["assists"] for card in result["cards"]] == [0] * len(expected)
    assert all(card["name"] for card in result["cards"])


def test_original_sample(engine):
    assert_numbers(engine.recognize(SAMPLE), [174, 430, 182])


def test_resized_sample_and_swapped_cards(engine, tmp_path):
    image = Image.open(SAMPLE).convert("RGB")
    smaller = tmp_path / "smaller.png"
    image.resize((1280, 800)).save(smaller)
    assert_numbers(engine.recognize(smaller), [174, 430, 182])
    # Swap text strips only; the screenshot remains a test fixture, not a claimed game result.
    starts = [295, 1000, 1705]
    strips = [image.crop((x, 450, x+330, 790)) for x in starts]
    switched = image.copy()
    for target, source in enumerate((2, 0, 1)):
        switched.paste(strips[source], (starts[target], 450))
    path = tmp_path / "swapped.png"
    switched.save(path)
    assert_numbers(engine.recognize(path), [182, 174, 430])


def test_unrelated_image_stays_pending(engine, tmp_path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (1920, 1080), "#192330").save(path)
    result = engine.recognize(path)
    assert result["warnings"]
    assert all(card["damage"] is None for card in result["cards"])


@pytest.mark.parametrize('visible', [(1,), (0, 2)])
def test_one_and_two_visible_cards_from_masked_sample(engine, tmp_path, visible):
    from PIL import ImageDraw
    image = Image.open(SAMPLE).convert('RGB')
    draw = ImageDraw.Draw(image)
    # Synthetic fixtures from the supplied screenshot, not claims of real solo/duo captures.
    for index, start in enumerate((295, 1000, 1705)):
        if index not in visible:
            draw.rectangle((start, 450, start+330, 790), fill='black')
    path = tmp_path / f'visible-{len(visible)}.png'
    image.save(path)
    assert_numbers(engine.recognize(path), [(174, 430, 182)[i] for i in visible])


def test_real_ocr_of_synthetic_teammate_change_resolves_duo(engine, tmp_path):
    from tracker.diagnostics import changed_summary
    from tracker.session import reconcile
    from tracker.store import Store
    original = engine.recognize(SAMPLE)
    variant = tmp_path / 'changed.png'
    changed_summary(SAMPLE, original, variant,
                    {original['cards'][2]['name']: 'NewTeammate', '174': '274', '430': '500'})
    changed = engine.recognize(variant)
    assert_numbers(changed, [274, 500, 182])
    store = Store(tmp_path / 'records')
    try:
        sid = store.start_session(mode=2)
        store.add_capture(sid, str(SAMPLE), 'original', original)
        assert not reconcile(store, sid)[0]['resolved']
        store.add_capture(sid, str(variant), 'synthetic-variant', changed)
        assert reconcile(store, sid)[0]['resolved']
        assert store.totals(sid)[0] == 2
        assert sorted(p['average_damage'] for p in store.totals(sid)[1]) == [224, 465]
    finally:
        store.close()
