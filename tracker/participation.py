"""Squad-level denominator, independent of the user's one/two/three-person roster."""
import re


def valid_kills(value):
    return type(value) is int and 0 <= value <= 999999


def squad_kills(result):
    """Return a value and evidence, never substitute selected players' kills.

    Old OCR payloads are supported without rewriting their lines or final stats.
    Missing headers can use all THREE reliable cards; incomplete squads cannot.
    """
    headers = []
    lines = result.get('lines', [])
    damage_y = min((l.get('cy', 1e9) for l in lines if '造成伤害' in l['text']), default=1e9)
    for line in lines:
        text = re.sub(r'\s+', '', line['text']).upper()
        label = re.match(r'^(?:小队总击杀|SQUADKILLS)[:：]?(\d{1,6})?$', text)
        if not label or not .94 <= line.get('score', 0) <= 1 or line.get('cy', 0) >= damage_y:
            continue
        if label[1] is not None:
            headers.append(int(label[1]))
            continue
        height = max(1, line.get('bottom', line.get('cy', 0)+10)-line.get('y', 0))
        right = line.get('right', line.get('x', 0))
        candidates = [l for l in lines if re.fullmatch(r'\d{1,6}', l['text'].strip())
                      and .94 <= l.get('score', 0) <= 1
                      and right-4 <= l.get('x', -999) <= right+height*4
                      and abs(l.get('cy', -999)-line.get('cy', 0)) <= height*.8]
        if len(candidates) == 1:
            headers.append(int(candidates[0]['text']))
    cards = result.get('cards', [])
    complete = (len(cards) == 3 and len({c.get('card') for c in cards}) == 3
                and all(valid_kills(c.get('kills')) and .94 <= c.get('score', 0) <= 1 for c in cards))
    total = sum(c['kills'] for c in cards) if complete else None
    values = set(headers)
    if len(values) > 1 or values and total is not None and next(iter(values)) != total:
        return None, 'conflict'
    if values:
        return next(iter(values)), 'summary-header'
    if total is not None:
        return total, 'three-complete-cards'
    return None, 'missing'


def percentage(kills, assists, team_kills):
    return (kills+assists)/team_kills if valid_kills(team_kills) and team_kills > 0 else None


def format_rate(rate):
    return f'{rate:.1%}' if rate is not None else '—'
