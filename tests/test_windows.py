import ctypes
import queue
import sys
import os

import pytest
from PIL import ImageGrab

from tracker.windows import Hotkey, enable_dpi, monitors


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system APIs")
@pytest.mark.skipif(os.environ.get('APEX_RUN_DESKTOP_TESTS')!='1', reason='Opt-in interactive Windows desktop capture')
def test_system_capture():
    enable_dpi()
    displays = monitors()
    assert displays and displays[0]["primary"]
    bbox = displays[0]["bbox"]
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    assert image.size == (bbox[2]-bbox[0], bbox[3]-bbox[1])
    assert image.getbbox() is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system APIs")
def test_registered_message_loop():
    events = queue.Queue()
    # A user may be running the real Ctrl+End tracker while this test executes.
    key='Ctrl+Alt+Shift+F24'
    registered = Hotkey(key, lambda: events.put("capture"))
    registered.start()
    conflicting = Hotkey(key, lambda: None)
    try:
        with pytest.raises(RuntimeError, match="占用"):
            conflicting.start()
        # Exercise the WM_HOTKEY handling path without injecting keyboard input anywhere.
        assert ctypes.windll.user32.PostThreadMessageW(registered.thread_id, 0x0312, 1, 0)
        assert events.get(timeout=2) == "capture"
    finally:
        registered.stop()
    replacement = Hotkey(key, lambda: None)
    replacement.start()
    replacement.stop()
