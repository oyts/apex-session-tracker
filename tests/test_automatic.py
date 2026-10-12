import json
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tracker.automatic import SummaryDetector, is_summary_result
from tracker.monitor import SummaryWatcher

SAMPLE = Path(__file__).resolve().parents[1]/'samples'/'match-summary.png'


def test_detector_summary_sizes_partial_and_non_summary():
    detector = SummaryDetector()
    original = Image.open(SAMPLE).convert('RGB')
    for size in ((2560,1600),(2000,1250),(1920,1200),(1280,800),
                 (1920,1080),(2560,1440),(1280,720)):
        found = detector.detect(original.resize(size))
        assert found and found['labels'] >= 1, size
    for count in (1,2):
        image = original.copy()
        draw = ImageDraw.Draw(image)
        # Synthetic empty teammate cards; summary header and first card remain.
        draw.rectangle((round((760 if count == 1 else 1310)*1.28),320,image.width,image.height),fill='black')
        assert detector.detect(image)
    for image in (Image.new('RGB',original.size,'black'),Image.new('RGB',original.size,'white')):
        assert detector.detect(image) is None
    hidden = original.copy()
    ImageDraw.Draw(hidden).rectangle((0,0,hidden.width,200),fill='black')
    assert detector.detect(hidden) is None
    # Fixed labels alone do not turn arbitrary gameplay into a summary candidate.
    ImageDraw.Draw(original).rectangle((0,450,original.width,800),fill='black')
    assert detector.detect(original) is None


def test_complete_ocr_page_validator():
    lines = [{'text':'总结','x':1075,'cy':44},
             {'text':'击杀/助攻/击倒','x':239,'cy':475},
             {'text':'造成伤害','x':235,'cy':548}]
    assert is_summary_result({'lines':lines})
    for i in range(3):
        assert not is_summary_result({'lines':lines[:i]+lines[i+1:]})
    assert not is_summary_result({'lines':[dict(line,cy=600) if line['text']=='总结' else line for line in lines]})


def test_native_label_bank_antialias_and_missing_words():
    detector=SummaryDetector()
    image=Image.new('L',(1000,562),0)
    for key,position in [('heading',(531,12)),('damage_native16x9',(116,234)),('triple_native16x9',(117,198))]:
        import numpy as np
        image.paste(Image.fromarray(detector.sources[key]),position)
    # Native UI rasterization differs from stretching a 16:10 screenshot.
    for size in ((1000,562),(2560,1440),(1920,1080)):
        assert detector.detect(image.resize(size,Image.Resampling.BILINEAR).convert('RGB'))
    for bounds in ((0,0,1000,90),(100,230,200,260),(100,190,220,220)):
        negative=image.copy();ImageDraw.Draw(negative).rectangle(bounds,fill=0)
        assert detector.detect(negative.convert('RGB')) is None


class Sequence:
    def __init__(self):
        self.match = True
        self.active = 42
        self.image = Image.new('RGB',(32,32))
        self.change_after_capture = False
        self.captures = 0
    def detect(self,image):
        return {'confidence':.99} if self.match else None
    def foreground(self,_bbox):
        return self.active
    def capture(self):
        self.captures += 1
        if self.change_after_capture:
            self.active = 43
        return self.image.copy()


def fixture_watcher():
    source = Sequence()
    events = []
    watcher = SummaryWatcher(lambda k,v: events.append((k,v)),(0,0,32,32),source,
                             source.foreground,source.capture)
    return source,events,watcher


def frames(events):
    return [value for kind,value in events if kind == 'frame']
