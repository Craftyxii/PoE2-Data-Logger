"""Real Windows input and desktop evidence for the native HUD acceptance check.

This helper never substitutes Qt test events for OS input. Its optional fixture
window is a separate process displaying a bundled capture, not a running game.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time


class NativeWindowsInput:
    """Send input to visible HWNDs and retain foreground/hit-test evidence."""

    def __init__(self, app, artifact_dir):
        """Require the Windows Qt plugin and accessible input desktop before showing test windows."""
        if sys.platform != "win32" or app.platformName().lower() != "windows":
            raise RuntimeError("The native HUD check requires Windows and Qt's windows plugin.")
        self.app = app
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.events = []
        self.screenshots = []
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.WindowFromPoint.argtypes = (wintypes.POINT,)
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        self.user.SetForegroundWindow.argtypes = (wintypes.HWND,)
        self.user.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        self.user.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.CloseDesktop.argtypes = (wintypes.HANDLE,)
        self.user.GetUserObjectInformationW.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        get_style = self.user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.user.GetWindowLongW
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        self.get_style = get_style

        class MouseInput(ctypes.Structure):
            """Match the native MOUSEINPUT ABI, including pointer-sized extra info."""
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KeyboardInput(ctypes.Structure):
            """Match the native KEYBDINPUT ABI on both Windows architectures."""
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HardwareInput(ctypes.Structure):
            """Retain the remaining INPUT union member for correct structure alignment."""
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class InputUnion(ctypes.Union):
            """Use Windows' complete input union rather than a keyboard-only approximation."""
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            """Provide the exact structure consumed by user32.SendInput."""
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        self.Input = Input
        self.InputUnion = InputUnion
        self.KeyboardInput = KeyboardInput
        self.MouseInput = MouseInput
        self.user.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.user.SendInput.restype = wintypes.UINT
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            raise RuntimeError(f"No accessible interactive input desktop: {ctypes.get_last_error()}")
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if not self.user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
                raise RuntimeError("Cannot identify the interactive Windows input desktop.")
            self.desktop_name = name.value
        finally:
            self.user.CloseDesktop(desktop)

    def wait(self, predicate, label, timeout=10):
        """Pump the real GUI loop until a bounded observable state transition completes."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError(f"Timed out waiting for {label}; foreground HWND={self.foreground()}")

    def pump(self, seconds=.08):
        """Allow native messages and paint events to settle without an unbounded wait."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)

    def foreground(self):
        """Read OS foreground ownership without relying on Qt activation state."""
        return int(self.user.GetForegroundWindow() or 0)

    def focus_setup(self, hwnd):
        """Verify native foreground ownership after showing an initial or fixture window."""
        self.user.ShowWindow(hwnd, 5)
        self.user.SetForegroundWindow(hwnd)
        self.wait(lambda: self.foreground() == int(hwnd), "initial foreground window")

    def rect(self, hwnd, client=False):
        """Return physical screen bounds used by Win32 hit tests and desktop captures."""
        rect = wintypes.RECT()
        call = self.user.GetClientRect if client else self.user.GetWindowRect
        if not call(hwnd, ctypes.byref(rect)):
            raise AssertionError("Cannot read native window bounds.")
        return rect.left, rect.top, rect.right, rect.bottom

    def expose(self, widget):
        """Settle queued layouts, then scroll nested pages from inside out before native input."""
        from PySide6.QtWidgets import QScrollArea
        # A queued scan callback creates table rows before Qt has recomputed the
        # enclosing pages' scroll ranges. Scrolling at that point is a no-op.
        self.pump()
        parent = widget.parentWidget()
        while parent:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 12, 12)
                # Inner scrolling can change the target's position relative to
                # the outer workspace; settle it before inspecting that ancestor.
                self.pump()
            parent = parent.parentWidget()

    def point(self, widget, point=None):
        """Translate Qt client coordinates into physical desktop pixels, including DPI scaling."""
        root = widget.window()
        hwnd = int(root.winId())
        origin = wintypes.POINT(0, 0)
        self.user.ClientToScreen(hwnd, ctypes.byref(origin))
        _, _, width, height = self.rect(hwnd, client=True)
        local = widget.mapTo(root, point or widget.rect().center())
        return (origin.x + round(local.x() * width / max(1, root.width())),
                origin.y + round(local.y() * height / max(1, root.height())), hwnd)

    def assert_target(self, widget, point=None):
        """Reject hidden, disabled, click-through, unfocused or obstructed native controls."""
        self.expose(widget)
        if not widget.isVisible() or not widget.isEnabled():
            raise AssertionError(f"Native target is hidden/disabled: {widget.objectName() or widget.accessibleName()}")
        x, y, hwnd = self.point(widget, point)
        hit = self.user.GetAncestor(self.user.WindowFromPoint(wintypes.POINT(x, y)), 2)
        if int(hit or 0) != hwnd:
            raise AssertionError(f"OS hit target mismatch at {(x,y)}: {int(hit or 0)} != {hwnd}")
        root = widget.window()
        local = widget.mapTo(root, point or widget.rect().center())
        child = root.childAt(local)
        if child is not widget and not (child is not None and widget.isAncestorOf(child)):
            if not (widget is root and child is None):
                raise AssertionError("Another Qt child obstructs the native control target.")
        owner = int(root.parentWidget().window().winId()) if root.parentWidget() else hwnd
        style = self.get_style(hwnd, -20)
        if style & 0x20 or (owner == hwnd and style & 0x08000000):
            raise AssertionError("HUD has WS_EX_TRANSPARENT or WS_EX_NOACTIVATE set.")
        foreground = self.foreground()
        # Native Qt popup HWNDs retain the owning main window as foreground.
        if foreground not in (hwnd, owner):
            raise AssertionError(f"HUD is not OS foreground: {foreground} != {hwnd}/{owner}")
        self.events.append({"action":"hit_test", "hwnd":hwnd, "foreground":foreground,
                            "hit_hwnd":int(hit), "point":[x,y], "target":widget.accessibleName() or widget.objectName()})
        return x, y

    def _send(self, entries, label):
        """Submit actual OS input and fail if Windows rejects any event."""
        batch = (self.Input * len(entries))(*entries)
        sent = self.user.SendInput(len(entries), batch, ctypes.sizeof(self.Input))
        if sent != len(entries):
            raise AssertionError(f"SendInput sent {sent}/{len(entries)} for {label}: {ctypes.get_last_error()}")
        self.events.append({"action":"SendInput", "kind":label, "count":int(sent), "foreground":self.foreground()})
        self.pump()

    def click(self, widget, point=None, double=False):
        """Click a verified HWND target using native cursor movement and mouse input."""
        x, y = self.assert_target(widget, point)
        if not self.user.SetCursorPos(x, y):
            raise AssertionError("Windows rejected cursor movement.")
        entries = []
        for _ in range(2 if double else 1):
            entries.extend((self.Input(0, self.InputUnion(mi=self.MouseInput(0,0,0,2,0,0))),
                            self.Input(0, self.InputUnion(mi=self.MouseInput(0,0,0,4,0,0)))))
        self._send(entries, "mouse double click" if double else "mouse click")

    def key(self, vk, modifiers=()):
        """Send a physical virtual-key chord through the Windows input queue."""
        entries = []
        for code in (*modifiers, vk):
            item = self.Input()
            item.type = 1
            item.value.ki = self.KeyboardInput(code, 0, 0, 0, 0)
            entries.append(item)
        for code in (vk, *reversed(modifiers)):
            item = self.Input()
            item.type = 1
            item.value.ki = self.KeyboardInput(code, 0, 2, 0, 0)
            entries.append(item)
        self._send(entries, f"key {vk} modifiers {tuple(modifiers)}")

    def type_text(self, text):
        """Enter Unicode text via native keyboard input rather than changing widget values."""
        units = text.encode("utf-16-le")
        entries = []
        for offset in range(0, len(units), 2):
            code = int.from_bytes(units[offset:offset+2], "little")
            for flags in (4, 6):
                item = self.Input()
                item.type = 1
                item.value.ki = self.KeyboardInput(0, code, flags, 0, 0)
                entries.append(item)
        if entries:
            self._send(entries, "Unicode text")

    def edit(self, widget, text):
        """Replace a visible text field using actual Windows mouse and keyboard events."""
        self.click(widget)
        self.key(ord("A"), (0x11,))
        self.type_text(text) if text else self.key(8)
        self.key(9)
        if widget.text() != text:
            raise AssertionError(f"Native edit did not reach its field: {widget.text()!r} != {text!r}")

    def choose(self, combo, data):
        """Open and navigate a real combo popup using OS input."""
        target = combo.findData(data)
        if target < 0:
            raise AssertionError(f"Missing dropdown choice {data!r}")
        self.click(combo)
        self.wait(lambda: combo.view().isVisible(), "native dropdown popup")
        self.assert_target(combo.view().viewport())
        self.key(0x24)
        for _ in range(target):
            self.key(0x28)
        self.key(0x0D)
        self.wait(lambda: combo.currentData() == data and not combo.view().isVisible(), "native dropdown selection")

    def screenshot(self, name, hwnd=None):
        """Save pixels from the real screen, never a synthetic QWidget render."""
        from PIL import ImageGrab, ImageStat
        self.pump()
        image = ImageGrab.grab(bbox=self.rect(hwnd) if hwnd else None, all_screens=True)
        if image.width < 100 or image.height < 100 or max(ImageStat.Stat(image.convert("RGB")).var) < 1:
            raise AssertionError("Desktop screenshot is empty or blank.")
        path = self.artifact_dir / f"{name}.png"
        image.save(path)
        self.screenshots.append(path.name)
        return image


def fixture_window(image_path, ready_file):
    """Show a separate-process simulated game view for honest focus and capture evidence."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
    app = QApplication([])
    if app.platformName().lower() != "windows":
        raise RuntimeError("Fixture window must use the real Windows Qt plugin.")
    window = QMainWindow()
    window.setWindowTitle("Path of Exile 2")
    label = QLabel()
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("background-color:rgb(37,72,95)")
    label.setPixmap(QPixmap(str(image_path)).scaled(600, 680, Qt.AspectRatioMode.KeepAspectRatio))
    window.setCentralWidget(label)
    window.showMaximized()
    app.processEvents()
    Path(ready_file).write_text(json.dumps({"hwnd":int(window.winId()), "pid":__import__("os").getpid(),
                                          "qt_platform":app.platformName()}), encoding="utf-8")
    return app.exec()


def main():
    """Launch only the external capture-fixture host requested by the native test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-window", required=True)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    return fixture_window(args.fixture_window, args.ready_file)


if __name__ == "__main__":
    raise SystemExit(main())
