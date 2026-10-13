"""Windows Graphics Capture of one HWND; no video, input or process handles."""
import threading
import time
import logging
from PIL import Image
from .windows import apex_window,window_display,window_minimized,window_client_crop,physical_pixels


class GraphicsWindowSource:
    def __init__(self,hide_border=True):
        self.hwnd=None;self.target=None;self.control=None;self.capture=None
        self.latest=None;self.stamp=0.;self.lock=threading.Lock()
        self.generation=0;self.status='等待 Apex 游戏窗口';self.retry_after=0.
        self.hide_border=hide_border
        self.border_requested=None

    def close(self):
        self.generation+=1
        control,self.control=self.control,None
        self.capture=None
        with self.lock:self.latest=None;self.stamp=0.
        if control:
            # Native stop waits for its worker, so never block the sampler/UI on it.
            threading.Thread(target=control.stop,daemon=True,name='WindowCaptureStop').start()

    def _start(self,hwnd):
        from windows_capture import WindowsCapture
        generation=self.generation
        def on_frame_arrived(frame,control):
            if generation!=self.generation:control.stop();return
            if frame.width*frame.height*4>32*1024*1024:return
            now=time.monotonic()
            with self.lock:
                if now-self.stamp<.25:return
                # Copy only one bounded CPU frame. Native mapped data expires after callback.
                pixels=frame.frame_buffer[:,:,:3][:,:,::-1].copy()
                self.latest=Image.fromarray(pixels);self.stamp=now
        def on_closed():
            if generation==self.generation:
                with self.lock:self.latest=None;self.stamp=0.
        def start(options,border):
            capture=WindowsCapture(window_hwnd=hwnd,draw_border=border,**options)
            capture.event(on_frame_arrived);capture.event(on_closed)
            control=capture.start_free_threaded()
            self.border_requested=border  # Preference only, never proof of OS consent/effect.
            return capture,control
        options=dict(cursor_capture=False,secondary_window=False,minimum_update_interval=250)
        border=False if self.hide_border else None
        try:
            self.capture,self.control=start(options,border)
        except Exception as error:
            if 'not supported' not in str(error).lower():raise
            if border is False and 'border' in str(error).lower():
                # Losing only the optional border property must not also discard
                # cursor/interval settings supported by this Windows build.
                logging.info('Border preference unsupported; retaining system border')
                try:
                    self.capture,self.control=start(options,None)
                    return
                except Exception as other:
                    if 'not supported' not in str(other).lower():raise
            # Optional WGC properties require newer Windows builds. The window
            # API remains usable with system defaults on older supported builds.
            logging.info('Optional window capture properties unsupported; using WGC defaults')
            self.capture,self.control=start(dict(cursor_capture=None,secondary_window=None,minimum_update_interval=None),None)

    def read(self):
        with physical_pixels():
            hwnd=apex_window(self.hwnd)
            if hwnd!=self.hwnd:
                self.close();self.hwnd=hwnd;self.retry_after=0.
            if not hwnd:
                self.status='等待唯一的 Apex 游戏窗口';return None
            self.target=window_display(hwnd)
            if window_minimized(hwnd):
                with self.lock:self.latest=None;self.stamp=0.
                self.status='Apex 已最小化；等待恢复窗口';return None
            if self.control and self.control.is_finished():self.close();self.retry_after=time.monotonic()+2
            if not self.control and time.monotonic()>=self.retry_after:
                try:self._start(hwnd)
                except Exception:
                    logging.exception('Windows Graphics Capture initialization failed')
                    self.close();self.retry_after=time.monotonic()+10
                    self.status='窗口捕获不可用，可切换“前台屏幕”模式';return None
            with self.lock:
                image=self.latest
                if image is None or time.monotonic()-self.stamp>3:
                    self.status='等待游戏提供新画面；后台停止渲染时请切回游戏';return None
                image=image.copy()
            bounds=window_client_crop(hwnd,image.size)
            if bounds is None:
                self.status='窗口尺寸变化，等待新画面';return None
            self.status='捕获 Apex 窗口（切换其他窗口时继续检测）'
            return image.crop(bounds)
