from __future__ import annotations

import sys


def game_foreground():
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    window = user32.GetForegroundWindow()
    if not window:
        return False
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(wintypes.HWND(window), title, len(title))
    return title.value.strip().casefold() == "path of exile 2"


def validate_region(region):
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
