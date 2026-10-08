"""Check external Windows foreground ownership and validate bounded screen-capture rectangles."""

from __future__ import annotations

import os
import sys


def external_window_title(user32, window):
    """Read a Windows title only for a valid external process; own-process or unreadable
    windows return an empty string.
    """
    import ctypes
    from ctypes import wintypes

    if not window:
        return ""
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    process_id = wintypes.DWORD()
    if (not user32.GetWindowThreadProcessId(wintypes.HWND(window), ctypes.byref(process_id)) or
            not process_id.value or process_id.value == os.getpid()):
        return ""
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    title = ctypes.create_unicode_buffer(256)
    if not user32.GetWindowTextW(wintypes.HWND(window), title, len(title)):
        return ""
    return title.value


def game_foreground():
    """Require Windows and an external foreground title exactly matching Path of Exile 2."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    window = user32.GetForegroundWindow()
    return external_window_title(user32, window).strip().casefold() == "path of exile 2"


def validate_region(region):
    """Coerce capture coordinates to integers and reject undersized, oversized or out-of-range
    screen rectangles.
    """
    if not isinstance(region, dict):
        raise ValueError("Select the opened remnant region first.")
    try:
        x, y, w, h = (int(region[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Select a valid screen region.") from error
    if w < 200 or h < 100 or w > 4096 or h > 4096 or w * h > 12_000_000:
        raise ValueError("The region must be at least 200×100 and at most 12 megapixels.")
    if abs(x) > 32768 or abs(y) > 32768:
        raise ValueError("The screen region is out of range.")
    return {"x": x, "y": y, "w": w, "h": h}
