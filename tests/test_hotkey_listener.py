"""Mocked listener/blocked-grab checks for HUD responsiveness, reserved scans and cancellation cleanup."""

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey


class HotkeyListenerTests(unittest.TestCase):
    """Check hotkey listener responsiveness and capture reservation cleanup under delays."""
    def setUp(self):
        """Initialize temporary logger storage and enable HUD overlay handling."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-hotkey-listener-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            logger._set_meta(db, "hud_overlay", True)

    def tearDown(self):
        """Restore the original data directory and remove the isolated logger database."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_overlay_key_and_listener_shutdown_remain_responsive_during_capture(self):
        """Verify overlay key and listener shutdown remain responsive during capture."""
        grabbing = threading.Event()
        release_grab = threading.Event()
        overlay_handled = threading.Event()
        capture_finished = threading.Event()

        def grab(**kwargs):
            """Block the screenshot grab until released, then return a synthetic image."""
            grabbing.set()
            if not release_grab.wait(2):
                raise RuntimeError("Test did not release the screenshot grabber.")
            return Image.new("RGB", (400, 300))

        grabber = Mock(side_effect=grab)
        reader = Mock(return_value={"runes": ["Rage"], "positions": [1]})
        manager = hotkey.HotkeyManager(supported=False, grabber=grabber,
                                      readers={"propagation": reader}, focused=lambda: True)

        def notified():
            """Signal observed overlay handling and capture completion from manager status."""
            state = manager.status()
            if state["overlay_sequence"]:
                overlay_handled.set()
            if state["latest"]:
                capture_finished.set()

        manager.on_event = notified
        messages = queue.Queue()
        user32, kernel32 = Mock(), Mock()
        user32.RegisterHotKey.return_value = True
        user32.SetTimer.return_value = 41
        kernel32.GetCurrentThreadId.return_value = 17

        def get_message(pointer, *_):
            """Feed queued Win32 messages to the listener and stop on WM_QUIT."""
            message, ident = messages.get(timeout=2)
            pointer._obj.message = message
            pointer._obj.wParam = ident
            return int(message != hotkey.WM_QUIT)

        user32.GetMessageW.side_effect = get_message
        ready, stop = threading.Event(), threading.Event()
        outcome = {}
        listener = threading.Thread(target=manager._message_loop,
            args=({"propagation": (0, 0x77), "overlay": (0, 0x78)}, ready, outcome, stop),
            daemon=True)
        handled_while_grabbing = stopped_while_grabbing = False
        with patch("ctypes.WinDLL", create=True,
                   side_effect=lambda name, **_: user32 if name == "user32" else kernel32):
            try:
                listener.start()
                self.assertTrue(ready.wait(1), outcome)
                messages.put((hotkey.WM_HOTKEY, hotkey.HOTKEY_ID))
                self.assertTrue(grabbing.wait(1), outcome)
                messages.put((hotkey.WM_HOTKEY, hotkey.HOTKEY_ID + 1))
                messages.put((hotkey.WM_HOTKEY, hotkey.HOTKEY_ID))
                handled_while_grabbing = overlay_handled.wait(.3)
                messages.put((hotkey.WM_QUIT, 0))
                listener.join(.3)
                stopped_while_grabbing = not listener.is_alive()
            finally:
                release_grab.set()
                messages.put((hotkey.WM_QUIT, 0))
                listener.join(2)
                self.assertTrue(capture_finished.wait(2), outcome)

        self.assertTrue(handled_while_grabbing,
                        "The screenshot grab blocked the HUD shortcut listener.")
        self.assertTrue(stopped_while_grabbing,
                        "The shortcut listener could not stop until the screenshot grab finished.")
        self.assertEqual(manager.status()["overlay_sequence"], 1)
        self.assertEqual(grabber.call_count, 1)
        self.assertEqual(reader.call_count, 1)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_background_capture_reserves_slot_and_cancelled_request_never_prepares(self):
        """Verify background capture reserves slot and cancelled request never prepares."""
        manager = hotkey.HotkeyManager(supported=False, focused=lambda: True,
                                      grabber=Mock(), readers={"opened": Mock()})
        manager.before_capture = Mock()
        with patch.object(hotkey.threading, "Thread") as worker:
            manager.capture("remnant", background=True)
            manager.capture("remnant", background=True)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(manager._active_capture_mode, "opened")
            manager.cancel_capture(modes=("opened",))
            worker.call_args.kwargs["target"](*worker.call_args.kwargs["args"])
        manager.before_capture.assert_not_called()
        manager.grabber.assert_not_called()
        self.assertIsNone(manager.status()["latest"])
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_background_capture_rechecks_focus_before_grabbing_and_releases_slot(self):
        """Verify background capture rechecks focus before grabbing and releases slot."""
        prepared, release_prepare, finished = (threading.Event() for _ in range(3))
        manager = hotkey.HotkeyManager(supported=False, grabber=Mock(),
                                      readers={"opened": Mock()},
                                      focused=Mock(side_effect=[True, False]))

        def prepare():
            """Pause capture preparation so focus can change before the screenshot grab."""
            prepared.set()
            if not release_prepare.wait(2):
                raise RuntimeError("Test did not release capture preparation.")

        manager.before_capture = prepare
        original_capture = manager._capture

        def capture(*args):
            """Run capture and signal worker completion even when it fails."""
            try:
                original_capture(*args)
            finally:
                finished.set()

        with patch.object(manager, "_capture", side_effect=capture):
            try:
                manager.capture("remnant", background=True)
                self.assertTrue(prepared.wait(1))
            finally:
                release_prepare.set()
                self.assertTrue(finished.wait(2))
        manager.grabber.assert_not_called()
        self.assertIsNone(manager.status()["latest"])
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_background_worker_start_failure_reports_error_and_releases_slot(self):
        """Verify background worker start failure reports error and releases slot."""
        manager = hotkey.HotkeyManager(supported=False, focused=lambda: True)
        with patch.object(hotkey.threading.Thread, "start", side_effect=RuntimeError("No worker")):
            manager.capture("remnant", background=True)
        self.assertIn("No worker", manager.status()["latest"]["error"])
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_cancellation_during_overlay_preparation_skips_capture(self):
        """Cancel a reserved request during the GUI handoff before it takes a screenshot."""
        reader, grabber = Mock(), Mock(return_value=Image.new("RGB", (400, 300)))
        manager = hotkey.HotkeyManager(supported=False, grabber=grabber,
                                      readers={"opened": reader})
        manager.before_capture = manager.cancel_capture
        manager.capture("remnant")
        grabber.assert_not_called()
        reader.assert_not_called()
        self.assertIsNone(manager.status()["latest"])
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_cancellation_during_screenshot_skips_clipboard_and_ocr(self):
        """Finish an in-flight screenshot after cancellation without copying or recognizing."""
        copied, reader = Mock(), Mock()
        manager = hotkey.HotkeyManager(supported=True, focused=lambda: True,
            hover_reader=copied, readers={"opened": reader})

        def grab():
            """Cancel while an external capture adapter owns the request."""
            manager.cancel_capture()
            return Image.new("RGB", (400, 300))

        manager.tooltip_grabber = grab
        with patch.object(hotkey.sys, "platform", "linux"), patch.object(
                manager, "_read_capture") as read_capture:
            manager.capture("waystone")
        copied.assert_not_called()
        reader.assert_not_called()
        read_capture.assert_not_called()
        self.assertIsNone(manager.status()["latest"])
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_cancellation_during_clipboard_skips_ocr(self):
        """Skip recognition when cancellation occurs inside the bounded clipboard adapter."""
        manager = hotkey.HotkeyManager(supported=True, focused=lambda: True,
            tooltip_grabber=lambda: Image.new("RGB", (400, 300)))
        manager.hover_reader = manager.cancel_capture
        with patch.object(hotkey.sys, "platform", "linux"), patch.object(
                manager, "_read_capture") as read_capture:
            manager.capture("waystone")
        read_capture.assert_not_called()
        self.assertIsNone(manager.status()["latest"])
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_native_message_error_is_reported_and_registrations_released(self):
        """Treat GetMessageW's negative error result separately from orderly WM_QUIT."""
        manager = hotkey.HotkeyManager(supported=True, focused=lambda: True)
        user32, kernel32 = Mock(), Mock()
        user32.RegisterHotKey.return_value = True
        user32.SetTimer.return_value = 41
        user32.GetMessageW.return_value = -1
        kernel32.GetCurrentThreadId.return_value = 17
        ready, stop, outcome = threading.Event(), threading.Event(), {}
        with patch("ctypes.WinDLL", create=True,
                   side_effect=lambda name, **_: user32 if name == "user32" else kernel32):
            manager._message_loop({"overlay": (0, 0x78)}, ready, outcome, stop)
        self.assertTrue(ready.is_set())
        self.assertIn("shortcut messages", manager.status()["error"])
        self.assertIn("error", outcome)
        user32.KillTimer.assert_called_once_with(None, 41)
        user32.UnregisterHotKey.assert_called_once_with(None, hotkey.HOTKEY_ID)


if __name__ == "__main__":
    unittest.main()
