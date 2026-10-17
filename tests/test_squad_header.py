import numpy as np
from PIL import Image,ImageDraw
from tracker.squad_header import verify_header
from tracker.participation import squad_kills
from tracker.summary_readiness import SummaryReadiness
from tests.test_participation import header
from tests.test_summary_readiness import page
from tracker.automatic import SummaryDetector
from tracker.ocr import Recognizer
from types import SimpleNamespace
import pytest


def fixture():
    im=Image.new('RGB',(2000,1125),'black')
    ImageDraw.Draw(im).rectangle((1830,175,1842,225),fill=(255,195,0))
    return im,header(1)[:1]


def test_anchored_digit_needs_color_and_mask_agreement_and_caption():
    im,lines=fixture();calls=[]
    def read(crop):calls.append(crop.size);return (1,.99)
    proof=verify_header(im,lines,read)
    assert len(calls)==2 and proof['value']==1
    assert squad_kills(dict(lines=lines,squad_kills_verification=proof))==(1,'summary-header')
    # The original low-confidence whole-page hallucination cannot override a
    # successful local reread; a strong conflicting observation stays reviewable.
    assert squad_kills(dict(lines=header(7,.51),squad_kills_verification=proof))==(1,'summary-header')
    assert squad_kills(dict(lines=header(7),squad_kills_verification=proof))==(None,'conflict')
    answers=iter([(1,.99),(7,.99)])
    assert verify_header(im,lines,lambda crop:next(answers)) is None
    assert verify_header(Image.new('RGB',im.size),lines,read) is None
    assert verify_header(im,[],read) is None
    assert verify_header(im,[dict(lines[0],score=.9)],read) is None
    assert verify_header(im,[dict(lines[0],cy=900)],read) is None


def test_small_ink_flicker_tolerated_but_anchor_prevents_accumulated_change():
    gate=SummaryReadiness();mask=np.ones((10,20),dtype=bool)
    assert not gate.observe(dict(header_visible=True,header_mask=mask),0)
    changed=mask.copy();changed[0,:10]=False
    assert gate.observe(dict(header_visible=True,header_mask=changed),1.5)
    # Another 5% compared with previous frame, but 10% from run's anchor.
    changed[1,:10]=False
    assert not gate.observe(dict(header_visible=True,header_mask=changed),2)


def test_dim_caption_and_golden_tint_keep_same_caption_threshold():
    detector=SummaryDetector()
    base=page()
    dim=Image.fromarray((np.asarray(base)*.8).astype('uint8'))
    assert detector.detect(dim)['header_visible']
    assert not detector.detect(page(False))['header_visible']
    # Rank/queue-like yellow decoration alone cannot stand in for a caption.
    assert not detector.detect(page(False,'222'))['header_visible']


@pytest.mark.parametrize('texts,scores',[
    (['1'],[float('nan')]),(['1'],[.93]),(['1'],[1.01]),
    (['1','7'],[.99,.99]),(['O'],[.99]),(['1'],[]),
])
def test_local_number_never_accepts_weak_invalid_or_multiple_results(texts,scores):
    reader=Recognizer.__new__(Recognizer)
    reader.engine=lambda *a,**k:SimpleNamespace(txts=texts,scores=scores)
    assert reader.agree_number(Image.new('RGB',(30,40))) is None
