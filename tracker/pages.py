"""Located entry evidence with separate Core and simultaneous Wildcard layouts.

    Five classes also exist in the locker. Require mode-specific controls and
    exclude navigation/reconnect pages. Loading artwork alone remains unknown.
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
    wildcard=has([l for l in top if l['x']<width*.48],('外卡','WILDCARD'))
    # A mode tile in a lobby isn't entry evidence. Top navigation and reconnect
    # notices veto both profiles, including when class labels are visible.
    navigation=sum(has([l for l in top if l['cy']<height*.14],(word,))
                   for word in ('游玩','挑战','通行证','传奇储物柜','商店','PLAY','CHALLENGES','BATTLEPASS','STORE'))
    # Mode-independent controls/turn strip. The locker lacks a team pick order.
    turn=has([l for l in low if l['cy']>=height*.90],
             ('正在选择','现在请选择','现在选择','等待选择','等待轮到','NOWSELECTING',
              'WAITINGFORTURN','SELECTYOURLEGEND','选择英雄','選擇英雄'))
    controls=has([l for l in valid if l['x']<width*.48 and l['cy']<height*.52],('小队静音','小隊靜音','MUTESQUAD','取消选择','取消選擇','选择英雄',
                     'SELECTLEGEND','CANCELSELECTION'))
    excluded=navigation>=2 or has(top,('LEGENDLOCKER','英雄储物柜','英雄儲物櫃','传奇储物柜','FIRINGRANGE','靶场','靶場')) or has(valid,('正在重新连接','正在重新連線','尝试重新连接','RECONNECTING'))
    choose=has([l for l in valid if l['x']<width*.48 and height*.25<l['cy']<height*.55],
               ('选择传奇','選擇傳奇','SELECTLEGEND','SELECTALEGEND'))
    squad_control=has([l for l in valid if l['x']<width*.48 and height*.25<l['cy']<height*.55],
                      ('小队静音','小隊靜音','MUTESQUAD','选择成为跳伞指挥官','選擇成為跳傘指揮官','VOLUNTEERASJUMPMASTER'))
    if wildcard and groups>=4 and choose and squad_control and not excluded:
        return dict(kind='selection',profile='wildcard-selection',evidence=['wildcard-mode-at-top',
                    'four-or-five-positioned-classes','located-select-legend','independent-squad-control'],confidence=1.,texts=valid)
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
    howto=has([l for l in valid if height*.45<l['cy']<height*.68 and width*.30<l.get('cx',l['x'])<width*.70],
              ('玩法说明','玩法說明','HOWTOPLAY'))
    if wildcard and countdown and timer and howto and not excluded:
        return dict(kind='loading',profile='wildcard-loading',evidence=['wildcard-mode-at-top',
                    'center-countdown-and-number','located-how-to-play'],confidence=1.,texts=valid)
    if mode and countdown and timer and mute and not excluded:
        return dict(kind='loading', evidence=['mode-at-top','center-countdown-and-number',
                    'bottom-mute-squad'], confidence=1., texts=valid)
    # Secondary start evidence is an initial dropship conjunction, never a
    # generic combat HUD (which also exists in spectator/respawn/reconnect).
    center=[l for l in valid if width*.23<l.get('cx',l['x'])<width*.78 and height*.15<l['cy']<height*.80]
    compass=[l for l in top if width*.20<l.get('cx',l['x'])<width*.80]
    directions=sum(any(l['t']==t for l in compass) for t in ('N','NE','E','SE','S','SW','W','NW'))
    jumpmaster=has(center,('是跳伞指挥官','是跳傘指揮官','ISTHEJUMPMASTER','YOUAREJUMPMASTER'))
    launch=has(center,('单独发射','單獨發射','单独跳伞','LAUNCHSOLO','RELINQUISH','放弃'))
    ship=has(center,('标记所有复制器','標記所有複製器','MARKALLREPLICATORS','运输船中的玩家','PLAYERSINDROPSHIP'))
    spectator=has(valid,('观战','觀戰','SPECTATING','返回大厅','返回大廳','死亡回放','DEATHRECAP','重生倒计时','RESPAWNING'))
    if jumpmaster and launch and ship and directions>=3 and not excluded and not spectator:
        return dict(kind='dropship',evidence=['located-jumpmaster','launch-control','initial-ship-control','compass'],confidence=1.,texts=valid)
    return dict(kind='unknown', evidence=[], confidence=0., texts=valid,
                partial=dict(classes=groups, turn=turn, controls=controls,
                             mode=mode,countdown=countdown,timer=timer,mute=mute,
                             wildcard=wildcard,choose=choose,howto=howto,excluded=excluded))


def classify_image(engine, image):
    # Four bounded bands; the center includes Wildcard's how-to-play heading.
    width=min(1800,image.width)
    small=image.resize((width,round(image.height*width/image.width)))
    lines=[]
    for x1,y1,x2,y2 in ((0,0,.48,.52),(0,.60,1,1),(.23,.14,.78,.80),(.20,0,.80,.14)):
        bounds=tuple(round(v) for v in (small.width*x1,small.height*y1,small.width*x2,small.height*y2))
        crop=small.crop(bounds)
        for line in engine.hud_lines(crop):
            lines.append({**line,**{key:line[key]+bounds[0] for key in ('x','right','cx')},
                          **{key:line[key]+bounds[1] for key in ('y','bottom','cy')}})
    return dict(classify_lines(lines,small.width,small.height),
                frame_size=list(small.size),original_size=list(image.size),ocr_lines=lines)
