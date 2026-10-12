"""Pure roster evidence and numeric admission policies."""
from .domain import POLICY


def normalize_name(value):
    return ''.join(value.split()).casefold()


def infer_roster(mode,observations,evidence):
    common=set.intersection(*observations) if observations else set()
    enough=len(observations)>=(1 if mode==3 else 2)
    partial_count=len(evidence)-len(observations)
    bound=[frame for frame in evidence if frame in observations or
           sum(key.startswith('p:') for key in frame)>=mode]
    repeated=set.intersection(*bound) if bound else set()
    bound_repeat=bool(mode<3 and partial_count and len(bound)>=2 and len(repeated)==mode
        and all(key.startswith('p:') for key in repeated) and not (enough and len(common)>mode))
    if bound_repeat:common,enough=repeated,True
    return common,enough,partial_count,bound_repeat


def admitted_rows(result,match_player,required):
    required=set(required)
    if not required:return None
    rows=[]
    for card in result['cards']:
        pid=match_player(card['name'])
        if pid not in required:continue
        if (card['score']<POLICY.number_score or card['name_score']<POLICY.identity_score or
            any(card[k] is None for k in ('damage','kills','assists'))):return None
        rows.append(dict(card=card['card'],player_id=pid,
                         **{k:card[k] for k in ('damage','kills','assists')}))
    return rows if len(rows)==len(required) and {r['player_id'] for r in rows}==required else None
