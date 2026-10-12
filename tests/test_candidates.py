import json
from pathlib import Path
from tracker.candidates import retain_rejected
from tracker.media import cleanup_plan,execute_cleanup


def test_rejected_candidates_bounded_readable_and_cleanup(tmp_path):
    photos=tmp_path/'screenshots';photos.mkdir()
    for i in range(10):
        image=photos/(f'{i:032x}.png');image.write_bytes(bytes([i]))
        saved=retain_rejected(tmp_path,image,{'cards':[],'lines':[{'text':'总结'}]},7,'epoch')
        assert not image.exists() and saved.is_file()
        assert json.loads(saved.with_suffix('.json').read_text('utf-8'))['session_id']==7
    candidates=tmp_path/'rejected-candidates'
    assert len(list(candidates.glob('*.png')))==8
    other=candidates/'my-notes.json';other.write_text('keep')
    plan=cleanup_plan(tmp_path,[photos],[],'duplicates')
    assert len(plan['cache'])==16 and plan['cache_bytes']>0
    result=execute_cleanup(plan)
    assert not result['failures'] and other.exists()
    assert not list(candidates.glob('*.png'))
