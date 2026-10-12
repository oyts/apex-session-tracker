"""Entry-page classification using located text, never map or damage identity.

    Five classes also exist in the locker. Require match selection controls and
    the bottom team-turn strip. Loading artwork alone is deliberately unknown.
"""
import re
from .domain import POLICY


def compact(text):
    return re.sub(r'\s+', '', text).upper()


CLASSES = (
    ('突击','突擊','ASSAULT'), ('游击','遊擊','SKIRMISHER'),
    ('侦察','偵察','RECON'), ('支援','SUPPORT'), ('控制','CONTROLLER'),
)


def classify_lines(lines, width, height):
    valid=[{**line,'t':compact(line['text'])} for line in lines
           if line.get('score',0)>=POLICY.start_text_score]
    low=[l for l in valid if l['cy']>=height*.62]
    top=[l for l in valid if l['cy']<height*.25]
    def has(items,cues):
        return any(any(c in l['t'] for c in cues) for l in items)
    groups=sum(any(l['t'] in group for l in low) for group in CLASSES)
    # Mode-independent controls/turn strip. The locker lacks a team pick order.
    turn=has([l for l in low if l['cy']>=height*.90],
             ('正在选择','现在请选择','现在选择','等待选择','等待轮到','NOWSELECTING',
              'WAITINGFORTURN','SELECTYOURLEGEND','选择英雄','選擇英雄'))
    controls=has(top,('小队静音','小隊靜音','MUTESQUAD','取消选择','取消選擇','选择英雄',
                     'SELECTLEGEND','CANCELSELECTION'))
    excluded=has(top,('LEGENDLOCKER','英雄储物柜','英雄儲物櫃','传奇储物柜','FIRINGRANGE','靶场','靶場'))
    if groups>=4 and turn and controls and not excluded:
        return dict(kind='selection', evidence=['four-or-five-positioned-classes',
                    'bottom-team-selection', 'selection-controls'], confidence=1., texts=valid)
    mode=has(top,('排位','積分','匹配','三人','双人','雙人','外卡',
                  'RANKED','TRIOS','DUOS','WILDCARD','大逃杀','大逃殺','BATTLE ROYALE','BATTLEROYALE'))
    countdown=has([l for l in valid if height*.14<l['cy']<height*.43],
                  ('倒数计时','倒數計時','COUNTDOWN','STARTINGIN','比赛开始倒计时'))
    timer=any(re.fullmatch(r'\d{1,2}(?:[.:]\d{1,2})?',l['t']) for l in valid
              if height*.14<l['cy']<height*.43 and width*.35<l.get('cx',l['x'])<width*.65)
    mute=has(low,('小队静音','小隊靜音','MUTESQUAD'))
    if mode and countdown and timer and mute and not excluded:
        return dict(kind='loading', evidence=['mode-at-top','center-countdown-and-number',
                    'bottom-mute-squad'], confidence=1., texts=valid)
    return dict(kind='unknown', evidence=[], confidence=0., texts=valid,
                partial=dict(classes=groups, turn=turn, controls=controls,
                             mode=mode,countdown=countdown,timer=timer,mute=mute))


def classify_image(engine, image):
    # Two bounded bands plus central timer: no full-screen OCR per sample.
    width=min(1800,image.width)
    small=image.resize((width,round(image.height*width/image.width)))
    lines=[]
    for x1,y1,x2,y2 in ((0,0,.48,.24),(0,.60,1,1),(.32,.14,.68,.43)):
        bounds=tuple(round(v) for v in (small.width*x1,small.height*y1,small.width*x2,small.height*y2))
        crop=small.crop(bounds)
        for line in engine.hud_lines(crop):
            lines.append({**line,**{key:line[key]+bounds[0] for key in ('x','right','cx')},
                          **{key:line[key]+bounds[1] for key in ('y','bottom','cy')}})
    return dict(classify_lines(lines,small.width,small.height),
                frame_size=list(small.size),original_size=list(image.size),ocr_lines=lines)
