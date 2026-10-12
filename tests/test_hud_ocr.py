from types import SimpleNamespace
import pytest
from tracker.ocr import Recognizer


@pytest.mark.parametrize('fail',[False,True])
def test_hud_resize_policy_restored_before_full_page_read(fail):
    reader=Recognizer.__new__(Recognizer)
    reader.engine=SimpleNamespace(text_det=SimpleNamespace(limit_type='min'))
    def lines(_image):
        assert reader.engine.text_det.limit_type=='max'
        if fail:raise OSError('fixture failure')
        return [{'text':'单独发射'}]
    reader.lines=lines
    if fail:
        with pytest.raises(OSError):reader.hud_lines(None)
    else:assert reader.hud_lines(None)==[{'text':'单独发射'}]
    assert reader.engine.text_det.limit_type=='min'
