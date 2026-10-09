"""Mocked Win32 ownership, foreground title and monitor/client-bound checks; no native game window is exercised."""

import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PoE2_Data_Logger.platform import hover_copy, live_watch


class ForegroundWindowTests(unittest.TestCase):
    """Exercise mocked foreground ownership checks and game-client or monitor tooltip bounds."""
    def native_window(self, title, process_id=202, thread_id=303):
        """Build a user32 mock with configurable foreground title, process and thread ownership."""
        user32 = Mock()
        user32.GetForegroundWindow.return_value = 17

        def owner(window, output):
            """Write the configured process ID to the Win32 output pointer and return its thread ID."""
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = process_id
            return thread_id

        def text(window, output, size):
            """Fill the title output buffer and return the configured title length."""
            output.value = title
            return len(title)

        user32.GetWindowThreadProcessId.side_effect = owner
        user32.GetWindowTextW.side_effect = text
        return user32

    def foreground(self, user32):
        """Run game-foreground detection with the supplied mocked Windows APIs."""
        with patch.object(live_watch.sys, "platform", "win32"), patch.object(
                live_watch.os, "getpid", return_value=101), patch(
                "ctypes.WinDLL", create=True, return_value=user32):
            return live_watch.game_foreground()

    def test_same_process_window_never_reads_title(self):
        """Verify logger-owned foreground windows skip synchronous title requests."""
        user32 = self.native_window("Path of Exile 2", process_id=101)
        user32.GetWindowTextW.side_effect = AssertionError("Synchronous GUI title request")
        self.assertFalse(self.foreground(user32))
        user32.GetWindowThreadProcessId.assert_called_once()
        user32.GetWindowTextW.assert_not_called()

    def test_external_game_window_is_detected(self):
        """Verify trimmed, case-insensitive game titles identify an external game window."""
        user32 = self.native_window("  PATH OF EXILE 2  ")
        self.assertTrue(self.foreground(user32))
        user32.GetWindowThreadProcessId.assert_called_once()
        user32.GetWindowTextW.assert_called_once()

    def test_external_non_game_window_is_rejected(self):
        """Verify an external logger-titled window is rejected as game foreground."""
        user32 = self.native_window("PoE2 Data Logger")
        self.assertFalse(self.foreground(user32))
        user32.GetWindowTextW.assert_called_once()

    def test_similarly_named_external_window_is_rejected(self):
        """Verify a title merely prefixed with the game name is rejected."""
        user32 = self.native_window("Path of Exile 2 - Other window")
        self.assertFalse(self.foreground(user32))

    def test_absent_foreground_window_skips_owner_and_title(self):
        """Verify missing foreground handles bypass owner and title queries."""
        user32 = self.native_window("Path of Exile 2")
        user32.GetForegroundWindow.return_value = None
        self.assertFalse(self.foreground(user32))
        user32.GetWindowThreadProcessId.assert_not_called()
        user32.GetWindowTextW.assert_not_called()

    def test_failed_owner_query_skips_title(self):
        """Verify failed window ownership queries bypass title reads."""
        user32 = self.native_window("Path of Exile 2", thread_id=0)
        self.assertFalse(self.foreground(user32))
        user32.GetWindowTextW.assert_not_called()

    def test_unknown_process_skips_title(self):
        """Verify unknown process ownership bypasses title reads."""
        user32 = self.native_window("Path of Exile 2", process_id=0)
        self.assertFalse(self.foreground(user32))
        user32.GetWindowTextW.assert_not_called()

    def test_failed_title_query_is_rejected(self):
        """Verify unsuccessful title reads reject foreground detection even when the buffer contains a game title."""
        user32 = self.native_window("Path of Exile 2")

        def failed_text(window, output, size):
            """Fill a game title but return failure to simulate an unsuccessful native title query."""
            output.value = "Path of Exile 2"
            return 0

        user32.GetWindowTextW.side_effect = failed_text
        self.assertFalse(self.foreground(user32))

    def test_non_windows_skips_native_api(self):
        """Verify non-Windows foreground detection never loads native APIs."""
        with patch.object(live_watch.sys, "platform", "linux"), patch(
                "ctypes.WinDLL", create=True) as load:
            self.assertFalse(live_watch.game_foreground())
        load.assert_not_called()

    def tooltip_bounds(self, user32):
        """Compute tooltip bounds with mocked Windows APIs and a fixed cursor position."""
        with patch.object(live_watch.sys, "platform", "win32"), patch.object(
                live_watch.os, "getpid", return_value=101), patch(
                "ctypes.WinDLL", create=True, return_value=user32), patch.object(
                hover_copy, "_cursor_position", return_value=(50, 60)):
            return hover_copy._tooltip_bounds()

    def monitor_window(self, title, process_id=202, thread_id=303):
        """Extend the foreground mock with a monitor covering a negative screen origin."""
        user32 = self.native_window(title, process_id=process_id, thread_id=thread_id)
        user32.MonitorFromPoint.return_value = 71

        def monitor_info(monitor, output):
            """Populate the mocked monitor rectangle for monitor-bound tooltip capture."""
            rect = output._obj.rcMonitor
            rect.left, rect.top, rect.right, rect.bottom = -1920, 0, 0, 1080
            return 1

        user32.GetMonitorInfoW.side_effect = monitor_info
        return user32

    def test_same_process_tooltip_uses_monitor_without_title_request(self):
        """Verify logger-owned tooltips use monitor bounds without requesting their title."""
        user32 = self.monitor_window("Path of Exile 2", process_id=101)
        user32.GetWindowTextW.side_effect = AssertionError("Synchronous GUI title request")
        self.assertEqual(self.tooltip_bounds(user32), (-1920, 0, 0, 1080))
        user32.GetWindowTextW.assert_not_called()
        user32.GetClientRect.assert_not_called()
        user32.GetMonitorInfoW.assert_called_once()

    def test_external_game_tooltip_uses_client_bounds(self):
        """Verify an external game window uses client bounds translated to screen coordinates."""
        user32 = self.native_window("Path of Exile 2")

        def client_rect(window, output):
            """Populate a 1920 by 1080 game client rectangle."""
            rect = ctypes.cast(output, ctypes.POINTER(wintypes.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1920, 1080
            return 1

        def client_origin(window, output):
            """Populate the game's screen-space client origin."""
            point = ctypes.cast(output, ctypes.POINTER(wintypes.POINT)).contents
            point.x, point.y = 100, 200
            return 1

        user32.GetClientRect.side_effect = client_rect
        user32.ClientToScreen.side_effect = client_origin
        self.assertEqual(self.tooltip_bounds(user32), (100, 200, 2020, 1280))
        user32.GetWindowTextW.assert_called_once()
        user32.GetMonitorInfoW.assert_not_called()

    def test_external_non_game_tooltip_uses_monitor(self):
        """Verify external non-game tooltips use the cursor's monitor bounds."""
        user32 = self.monitor_window("Other application")
        self.assertEqual(self.tooltip_bounds(user32), (-1920, 0, 0, 1080))
        user32.GetClientRect.assert_not_called()
        user32.GetMonitorInfoW.assert_called_once()

    def test_failed_owner_query_tooltip_uses_monitor_without_title(self):
        """Verify failed ownership queries fall back to monitor bounds without reading a title."""
        user32 = self.monitor_window("Path of Exile 2", thread_id=0)
        self.assertEqual(self.tooltip_bounds(user32), (-1920, 0, 0, 1080))
        user32.GetWindowTextW.assert_not_called()
        user32.GetClientRect.assert_not_called()

    def test_absent_foreground_tooltip_uses_monitor(self):
        """Verify absent foreground windows fall back to monitor bounds."""
        user32 = self.monitor_window("Path of Exile 2")
        user32.GetForegroundWindow.return_value = None
        self.assertEqual(self.tooltip_bounds(user32), (-1920, 0, 0, 1080))
        user32.GetWindowThreadProcessId.assert_not_called()
        user32.GetWindowTextW.assert_not_called()

    def test_failed_monitor_query_retains_capture_error(self):
        """Verify failed monitor lookup retains the actionable capture error."""
        user32 = self.monitor_window("Other application")
        user32.GetMonitorInfoW.side_effect = None
        user32.GetMonitorInfoW.return_value = 0
        with self.assertRaisesRegex(ValueError, "Could not locate the game display"):
            self.tooltip_bounds(user32)


class NativeForegroundListenerTests(unittest.TestCase):
    """Exercise native Windows hotkey listener lifecycle while the logger owns the foreground window."""
    @unittest.skipUnless(sys.platform == "win32", "Requires native Windows window and hotkey APIs")
    def test_same_process_window_allows_listener_start_replace_clear_and_stop(self):
        """Verify native listener registration, replacement, clearing and stop finish promptly under own-window focus."""
        from PoE2_Data_Logger.platform.hotkey import HotkeyManager, WM_QUIT

        native_load = ctypes.WinDLL
        user32 = native_load("user32", use_last_error=True)
        kernel32 = native_load("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        window = user32.CreateWindowExW(
            0, "STATIC", "PoE2 Data Logger", 0x80000000, 0, 0, 16, 16,
            None, None, kernel32.GetModuleHandleW(None), None)
        self.assertTrue(window, ctypes.get_last_error())
        manager = HotkeyManager(supported=True, focused=live_watch.game_foreground)
        timer_checked = threading.Event()
        owner_queries = 0

        def owner(window_handle, process_id):
            """Count real ownership queries and signal when the listener's timer has checked foreground."""
            nonlocal owner_queries
            result = user32.GetWindowThreadProcessId(window_handle, process_id)
            owner_queries += 1
            if owner_queries >= 2:
                timer_checked.set()
            return result

        class ForegroundUser32:
            """Override foreground ownership calls while forwarding other APIs to native user32."""
            GetForegroundWindow = Mock(return_value=window)
            GetWindowThreadProcessId = Mock(side_effect=owner)

            def __getattr__(self, name):
                """Forward unmocked attributes to native user32."""
                return getattr(user32, name)

        foreground_user32 = ForegroundUser32()

        def load(name, *args, **kwargs):
            """Supply the foreground wrapper for user32 and load other DLLs normally."""
            return foreground_user32 if name == "user32" else native_load(name, *args, **kwargs)

        def quickly(action):
            """Assert that a listener lifecycle action completes within the responsiveness limit."""
            started = time.monotonic()
            action()
            self.assertLess(time.monotonic() - started, 1.5)

        try:
            process_id = wintypes.DWORD()
            self.assertTrue(user32.GetWindowThreadProcessId(window, ctypes.byref(process_id)))
            self.assertEqual(process_id.value, os.getpid())
            with patch("ctypes.WinDLL", side_effect=load):
                quickly(lambda: manager.configure("Ctrl+Alt+Shift+F24", persist=False))
                self.assertTrue(manager.status()["registered"])
                self.assertTrue(timer_checked.wait(1.5))
                quickly(lambda: manager.configure("Ctrl+Alt+Shift+F23", persist=False))
                self.assertEqual(manager.status()["combo"], "Ctrl+Alt+Shift+F23")
                self.assertTrue(manager.status()["registered"])
                quickly(lambda: manager.configure("", persist=False))
                self.assertEqual(manager.status()["combo"], "")
                self.assertFalse(manager.status()["registered"])
                quickly(lambda: manager.configure("Ctrl+Alt+Shift+F24", persist=False))
                quickly(manager._unregister)
                self.assertFalse(manager.status()["registered"])
        finally:
            if manager._listener_stop is not None:
                manager._listener_stop.set()
            if manager._thread_id is not None:
                user32.PostThreadMessageW(manager._thread_id, WM_QUIT, 0, 0)
            message = wintypes.MSG()
            deadline = time.monotonic() + 3
            while manager._thread and manager._thread.is_alive() and time.monotonic() < deadline:
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                manager._thread.join(.01)
            user32.DestroyWindow(window)


if __name__ == "__main__":
    unittest.main()
