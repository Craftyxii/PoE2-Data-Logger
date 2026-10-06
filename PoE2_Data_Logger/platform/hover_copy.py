from __future__ import annotations

import ctypes
import sys
import time
import threading
from ctypes import wintypes


CF_UNICODETEXT = 13
VK_CONTROL = 0x11
VK_C = 0x43
KEYUP = 0x0002
MAX_BYTES = 60002
_CLIPBOARD_LOCK = threading.Lock()


def _clipboard_text(user32, kernel32):
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        size = kernel32.GlobalSize(handle)
        if size < 2 or size > MAX_BYTES:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr, size // ctypes.sizeof(ctypes.c_wchar)).split("\0", 1)[0]
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def read_hovered_text(timeout=.48):
    if sys.platform != "win32":
        return None
    from PoE2_Data_Logger.platform.live_watch import game_foreground

    if not game_foreground():
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)
    with _CLIPBOARD_LOCK:
        if not game_foreground():
            return None
        previous = user32.GetClipboardSequenceNumber()
        held_ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        if not held_ctrl:
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
        try:
            user32.keybd_event(VK_C, 0, 0, 0)
            user32.keybd_event(VK_C, 0, KEYUP, 0)
        finally:
            if not held_ctrl:
                user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
        deadline = time.monotonic() + max(0.0, min(float(timeout), .48))
        while True:
            if not game_foreground():
                return None
            if user32.GetClipboardSequenceNumber() != previous:
                text = _clipboard_text(user32, kernel32)
                if text.startswith("Item Class:"):
                    return text
                if text:
                    return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(.02, remaining))
    return None


def _cursor_position():
    if sys.platform != "win32":
        raise ValueError("Hovered-item screen capture is available on Windows.")
    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    point = Point()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = (ctypes.POINTER(Point),)
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ValueError("Could not locate the mouse cursor.")
    return point.x, point.y


def _tooltip_bounds():
    if sys.platform != "win32":
        raise ValueError("Hovered-item screen capture is available on Windows.")
    from PoE2_Data_Logger.platform.live_watch import external_window_title

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    window = user32.GetForegroundWindow()
    if "path of exile 2" in external_window_title(user32, window).casefold():
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if user32.GetClientRect(wintypes.HWND(window), ctypes.byref(rect)) and user32.ClientToScreen(
                wintypes.HWND(window), ctypes.byref(origin)):
            box = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
            if rect.right > 0 and rect.bottom > 0:
                if rect.right * rect.bottom > 12_000_000:
                    raise ValueError("Game capture exceeds 12 megapixels. Use a smaller game resolution.")
                return box
    x, y = _cursor_position()
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ValueError("Could not locate the game display.")
    rect = info.rcMonitor
    if not 0 < (rect.right - rect.left) * (rect.bottom - rect.top) <= 12_000_000:
        raise ValueError("Game display exceeds 12 megapixels. Use a smaller game resolution.")
    return rect.left, rect.top, rect.right, rect.bottom


def capture_near_cursor():
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=_tooltip_bounds(),
                          all_screens=True).convert("RGB")


def capture_remnant_context(region=None, grabber=None):
    from PIL import ImageGrab
    tooltip = _tooltip_bounds()
    if region:
        panel = (region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"])
    else:
        panel = tooltip
    bounds = (min(panel[0], tooltip[0]), min(panel[1], tooltip[1]),
              max(panel[2], tooltip[2]), max(panel[3], tooltip[3]))
    if not 0 < (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) <= 12_000_000:
        raise ValueError("Combined capture exceeds 12 megapixels. Select the remnant region again.")
    frame = (grabber or ImageGrab.grab)(bbox=bounds, all_screens=True)
    def crop(box):
        return frame.crop((box[0] - bounds[0], box[1] - bounds[1],
                           box[2] - bounds[0], box[3] - bounds[1]))
    return crop(panel), crop(tooltip)
