"""Windows frame-source adapter; page/domain rules do not call native APIs."""
from PIL import ImageGrab
from .windows import apex_foreground, apex_capture_bbox, window_display, physical_pixels


class WindowsFrameSource:
    def __init__(self, bbox=None):
        self.bbox=bbox
        self.target=None

    def read(self):
        with physical_pixels():
            hwnd=apex_foreground(self.bbox)
            if not hwnd:
                return None
            target=window_display(hwnd) if self.bbox is None else {'bbox':self.bbox}
            if not target:
                return None
            image=ImageGrab.grab(bbox=apex_capture_bbox(hwnd,target['bbox']),all_screens=True).convert('RGB')
            if apex_foreground(self.bbox)!=hwnd or (self.bbox is None and window_display(hwnd)!=target):
                return None
            if image.width*image.height*3>32*1024*1024:
                raise OSError('截图超过32MB，请使用单个常规分辨率屏幕')
            self.target=target
            return image
