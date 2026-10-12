"""Resolve a session roster, then replay pending matches in capture order."""
from .ocr import auto_rows


def reconcile(store, session_id, auto_save=True):
    store.consolidate(session_id)
    state = store.discovery(session_id, resolve=True)
    saved = []
    if not state['resolved'] or not auto_save:
        return state, saved
    required = store.session_player_ids(session_id)
    for capture in reversed(store.visible_captures(session_id)):
        if capture['status'] != 'pending' or capture['duplicate_of']:
            continue
        result = store.capture(capture['id'])['result']
        if result.get('attribution')=='needs_review':
            continue
        rows = auto_rows(result, store, required, session_id)
        if rows:
            store.confirm(capture['id'], rows)
            saved.append(capture['id'])
    store.consolidate(session_id)
    return state, saved
