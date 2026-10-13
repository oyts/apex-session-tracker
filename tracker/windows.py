from __future__ import annotations

import ctypes
import os
import threading
from contextlib import contextmanager
from ctypes import wintypes


@contextmanager
def physical_pixels():
    """Use physical pixels on capture threads, restoring their previous DPI context."""
    previous=None; api=None
    if os.name=='nt':
        try:
            api=ctypes.WinDLL('user32',use_last_error=True).SetThreadDpiAwarenessContext
            api.argtypes=[wintypes.HANDLE]; api.restype=wintypes.HANDLE
            previous=api(ctypes.c_void_p(-4))
        except (AttributeError,OSError):
            pass
    try:
        yield
    finally:
        if previous and api: api(previous)


def monitor_identity(display):
    return display.get('device') or str(tuple(display['bbox']))


def window_handle(window):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    return user.GetAncestor(window.winfo_id(), 2)


def window_metrics(window):
    """DPI and usable area of this application's window, independent of game selection."""
    fallback = {'dpi': 96, 'work': (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())}
    if os.name != 'nt':
        return fallback
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        hwnd = window_handle(window)
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.GetDpiForWindow.restype = wintypes.UINT
        dpi = user.GetDpiForWindow(hwnd)
        user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HMONITOR
        class Info(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(Info)]
        info = Info(); info.size = ctypes.sizeof(info)
        with physical_pixels():
            if user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
                r = info.work
                return {'dpi': dpi or 96, 'work': (r.left, r.top, r.right, r.bottom)}
    except (AttributeError, OSError):
        pass
    return fallback


def place_window(window, x, y):
    """Move only our own window, accepting negative desktop coordinates without activation."""
    if os.name != 'nt':
        window.geometry(f'+{x}+{y}')
        return
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    with physical_pixels():
        user.SetWindowPos(window_handle(window), None, x, y, 0, 0, 0x1 | 0x4 | 0x10)


def apex_foreground(bbox=None):
    """Read only public window metadata; return HWND only for the Apex foreground.

    Exact title matching avoids capturing browsers whose titles merely mention Apex.
    No process handle, memory access, keyboard hooks or foreground changes.
    """
    if os.name != 'nt':
        return None
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.IsIconic.argtypes = [wintypes.HWND]
    hwnd = user.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(512)
    user.GetWindowTextW(hwnd, title, len(title))
    if title.value.strip().casefold() not in {'apex legends', 'apex legends™',
                                               'apex legends - dx12', 'apex legends (dx12)'} or user.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if bbox and not (bbox[0] <= (rect.left+rect.right)/2 < bbox[2]
                     and bbox[1] <= (rect.top+rect.bottom)/2 < bbox[3]):
        return None
    return hwnd


APEX_TITLES = {'apex legends', 'apex legends™', 'apex legends - dx12', 'apex legends (dx12)'}


def apex_window(previous=None):
    """Bind a top-level HWND by exact public title, even when another app is front.

    Multiple matches are ambiguous; never select an arbitrary desktop surface.
    Minimized windows remain bound but are not sampled by the capture adapter.
    """
    if os.name != 'nt': return None
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindow.argtypes=[wintypes.HWND]
    def valid(hwnd):
        if not hwnd or not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd):return False
        title=ctypes.create_unicode_buffer(512);user.GetWindowTextW(hwnd,title,len(title))
        return title.value.strip().casefold() in APEX_TITLES
    if valid(previous):return previous
    found=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,_):
        if valid(hwnd):found.append(hwnd)
        return True
    callback=callback_type(visit);user.EnumWindows(callback,0)
    return found[0] if len(found)==1 else None


def window_minimized(hwnd):
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.IsIconic.argtypes=[wintypes.HWND]
    return bool(user.IsIconic(hwnd))


def window_client_crop(hwnd, size):
    """Translate WGC's extended window frame to physical client coordinates."""
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
    dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
    dwm.DwmGetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
    rect,client,origin=wintypes.RECT(),wintypes.RECT(),wintypes.POINT(0,0)
    if dwm.DwmGetWindowAttribute(hwnd,9,ctypes.byref(rect),ctypes.sizeof(rect))!=0:
        raise OSError('无法获取窗口捕获边界')
    if not user.GetClientRect(hwnd,ctypes.byref(client)) or not user.ClientToScreen(hwnd,ctypes.byref(origin)):
        raise OSError('无法获取游戏客户区')
    # Never use a frame acquired before a resize/move changed its coordinate space.
    if size != (rect.right-rect.left,rect.bottom-rect.top):return None
    bounds=(max(0,origin.x-rect.left),max(0,origin.y-rect.top),
            min(size[0],origin.x-rect.left+client.right),min(size[1],origin.y-rect.top+client.bottom))
    return bounds if bounds[2]>bounds[0] and bounds[3]>bounds[1] else None


def apex_capture_bbox(hwnd, monitor):
    """Capture the visible game client area, excluding a windowed game's desktop."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    rect, origin = wintypes.RECT(), wintypes.POINT(0,0)
    if not user.GetClientRect(hwnd,ctypes.byref(rect)) or not user.ClientToScreen(hwnd,ctypes.byref(origin)):
        raise OSError('无法获取 Apex 窗口区域')
    bounds = (max(monitor[0],origin.x),max(monitor[1],origin.y),
              min(monitor[2],origin.x+rect.right),min(monitor[3],origin.y+rect.bottom))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise OSError('Apex 窗口不在所选屏幕上')
    return bounds


def window_display(hwnd):
    """Public monitor metadata for a window; no process access or activation.

    Use the monitor with the largest window intersection; never fall back to a
    different monitor when a disconnected/off-screen window has no intersection.
    """
    if os.name != 'nt' or not hwnd:
        return None
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HMONITOR
    class Info(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD),
                    ('device', wintypes.WCHAR * 32)]
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(Info)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    with physical_pixels():
        handle = user.MonitorFromWindow(hwnd, 0)
        info = Info(); info.size = ctypes.sizeof(info)
        if not handle or not user.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        r = info.monitor
        return {'bbox': (r.left, r.top, r.right, r.bottom),
                'primary': bool(info.flags & 1), 'device': info.device}


def display_label(display):
    bounds = display['bbox']
    return f"{display.get('device', '显示器')} · {bounds[2]-bounds[0]}×{bounds[3]-bounds[1]}" + (
        '（主屏）' if display.get('primary') else '')


def enable_dpi():
    if os.name == "nt":
        try:
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
            user.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
            if not user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                # A manifest/host can have already fixed process awareness.
                # Set the UI thread before any Tk windows are created.
                user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
                user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
                user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def window_awareness(window):
    if os.name != 'nt':
        return None
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    user.GetWindowDpiAwarenessContext.restype = wintypes.HANDLE
    user.GetAwarenessFromDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    return user.GetAwarenessFromDpiAwarenessContext(user.GetWindowDpiAwarenessContext(window_handle(window)))


def monitors():
    if os.name != "nt":
        return []
    results = []
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ('szDevice',wintypes.WCHAR*32)]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    def callback(handle, _dc, _rect, _param):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            results.append({"bbox": (rect.left, rect.top, rect.right, rect.bottom),
                            "primary": bool(info.dwFlags & 1),'device':info.szDevice})
        return True
    callback_fn = callback_type(callback)
    with physical_pixels():
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_fn, 0)
    return sorted(results, key=lambda item: not item["primary"])


def parse_hotkey(value):
    parts = [part.strip().upper() for part in value.split("+")]
    flags = 0x4000  # MOD_NOREPEAT: held keys produce one capture.
    modifiers = {"CTRL": 2, "ALT": 1, "SHIFT": 4, "WIN": 8}
    if len(parts) < 2 or len(set(parts)) != len(parts):
        raise ValueError("请使用组合键，例如 Ctrl+End 或 Ctrl+Alt+F8。")
    for part in parts[:-1]:
        if part not in modifiers:
            raise ValueError("修饰键支持 Ctrl、Alt、Shift、Win。")
        flags |= modifiers[part]
    key = parts[-1]
    named_keys = {"END": 0x23, "HOME": 0x24, "PAGEUP": 0x21, "PGUP": 0x21,
                  "PAGEDOWN": 0x22, "PGDN": 0x22, "INSERT": 0x2D, "DELETE": 0x2E,
                  "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
                  "SPACE": 0x20, "PAUSE": 0x13, "SCROLLLOCK": 0x91}
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        if key == "F12":
            raise ValueError("F12 由系统保留，请换一个键。")
        vk = 0x70 + int(key[1:]) - 1
    elif key in named_keys:
        vk = named_keys[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key)
    else:
        raise ValueError("主键支持 End、Home、PageUp、PageDown、方向键、Insert、Delete、Space、Pause、ScrollLock、F1–F24（除F12）、字母和数字。")
    return flags, vk


class Hotkey:
    """RegisterHotKey message loop; no keyboard hooks or game process access."""
    def __init__(self, value, callback):
        self.value = value
        self.callback = callback
        self.thread_id = None
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        parse_hotkey(self.value)
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError("快捷键注册超时。")
        if self.error:
            raise RuntimeError(self.error)

    def _run(self):
        if os.name != "nt":
            self.error = "系统快捷键只支持 Windows。"
            self.ready.set()
            return
        user = ctypes.WinDLL("user32", use_last_error=True)
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        flags, vk = parse_hotkey(self.value)
        if not user.RegisterHotKey(None, 1, flags, vk):
            self.error = f"{self.value} 已被占用或无法注册，请在设置中更换。"
            self.ready.set()
            return
        self.ready.set()
        try:
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:
                    self.callback()
        finally:
            user.UnregisterHotKey(None, 1)

    def stop(self):
        if self.thread_id and self.thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=3)
