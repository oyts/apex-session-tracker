"""Conservative visual evidence for returning to a completed summary.

The raw image hash stays exact. This separate comparison excludes requeue UI,
animated world background and subtitles, and requires every bright pixel in all
three complete data strips plus two substantial banner-art regions to agree.
Numbers alone, OCR strings alone and perceptual hashes are never identities.
It is only used for an unassigned observation directly following an established
match with no intervening verified start or conflicting human attribution.
"""
from PIL import Image
import numpy as np


def surface_evidence(first, second):
    if first['result'].get('keep_separate') or second['result'].get('keep_separate'):
        return None
    from .participation import squad_kills
    if all(row['result'].get('summary_readiness',{}).get('state')!='incomplete' for row in (first,second)):
        totals=[squad_kills(row['result'])[0] for row in (first,second)]
        if None not in totals and totals[0]!=totals[1]:
            return None  # Explicit different totals are not a cosmetic requeue change.
    try:
        with Image.open(first['image']) as a, Image.open(second['image']) as b:
            if a.size != b.size or a.width < 800 or a.width*a.height > 12_000_000:
                return None
            width, height = a.size
            arrays = [np.asarray(im.convert('RGB')).astype(np.int16) for im in (a,b)]
    except (OSError, ValueError):
        return None
    # Use actual OCR damage-label locations rather than a single aspect ratio.
    labels = []
    for row in (first, second):
        ls = sorted([l for l in row['result'].get('lines', []) if '造成伤害' in l['text']], key=lambda l:l['x'])
        if len(ls) != 3 or len(row['result'].get('cards', [])) != 3:
            return None
        if any(not all(isinstance(l.get(k),(int,float)) for k in ('x','y','bottom')) for l in ls):
            return None
        labels.append(ls)
    scale = width/min(width, 2000)  # Recognizer's documented working resolution.
    gap=min(labels[0][i+1]['x']-labels[0][i]['x'] for i in (0,1))
    if gap <= 0:return None
    art_agreement = []
    for a,b in zip(*labels):
        if max(abs(a[k]-b[k]) for k in ('x','y','bottom')) > 2:
            return None
        lh = max(12, a['bottom']-a['y'])
        left = round((a['x']-4)*scale)
        top = max(0, round((a['y']-8*lh)*scale))
        right = round((a['x']+gap*.30)*scale)
        respawns=[]
        for row,label in ((first,a),(second,b)):
            last=[l for l in row['result'].get('lines',[]) if '给予重生次数' in l['text']
                  and abs(l.get('x',-999)-label['x'])<12 and l.get('y',0)>label['y']]
            if len(last)!=1 or not isinstance(last[0].get('bottom'),(int,float)):
                return None
            respawns.append(last[0]['bottom'])
        if abs(respawns[0]-respawns[1])>2:return None
        # Include the entire last value, not just its label or topmost pixels.
        bottom = min(height, round((max(respawns)+lh*2+4)*scale))
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return None
        strips = [ar[top:bottom,left:right] for ar in arrays]
        bright = np.maximum(strips[0].max(2),strips[1].max(2)) > 100
        if bright.sum() < 400 or np.abs(strips[0]-strips[1]).max(2)[bright].max() > 20:
            return None
        # Independent visible card artwork, beyond the text column. Requiring
        # two cards tolerates a small animation on one, never a text mismatch.
        art_left = right+round(gap*.022*scale)
        art_right = min(width, art_left+round(gap*.29*scale))
        art_top=max(top,round((a['y']-6*lh)*scale))
        art_bottom=min(height,round(min(respawns)*scale))
        art = [ar[art_top:art_bottom,art_left:art_right] for ar in arrays]
        if not art[0].size or min(float(ar.std()) for ar in art) < 20:
            art_agreement.append(False)
        else:
            art_agreement.append(float((np.abs(art[0]-art[1]).max(2)>20).mean()) <= .01)
    if sum(art_agreement) < 2:
        return None
    return dict(method='summary-surface-v1', text_strips=3, banner_regions=sum(art_agreement),
                max_text_channel_delta=20, ignored='requeue-background-subtitles')
