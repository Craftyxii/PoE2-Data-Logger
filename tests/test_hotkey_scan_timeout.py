"""Keep shortcut capture reservations bounded without allowing late workers to corrupt newer scans."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager


class HotkeyScanTimeoutTests(unittest.TestCase):
    """Exercise timeouts, cancellation and lock ownership for every shortcut scan mode."""

    def setUp(self):
        """Initialize an isolated profile used to freeze the scanner's saved preferences."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.manager = HotkeyManager(supported=False)

    def tearDown(self):
        """Release any simulated reservation and restore the incoming profile."""
        if self.manager._capture_owner is not None:
            self.manager._finish_capture(None, None, self.manager._capture_owner)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def reserve(self, mode):
        """Hold recognition at its boundary without starting a real screenshot or OCR adapter."""
        self.manager.mode = mode if mode in ("seed", "opened", "both") else "opened"
        with patch.object(self.manager, "_capture"):
            self.manager.capture("default" if mode in ("seed", "opened", "both") else mode)
        return self.manager._capture_owner

    def test_timeout_unblocks_all_scan_modes_and_late_completion_cannot_release_new_lock(self):
        """Retire every overdue mode, retry immediately and reject the original worker's completion."""
        for mode in ("seed", "opened", "both", "propagation", "currency", "ritual", "item", "waystone", "tablet"):
            with self.subTest(mode=mode):
                old = self.reserve(mode)
                self.assertTrue(self.manager._capture_lock.locked())
                self.manager._expire_capture(mode, old)
                event = self.manager.status()["latest"]
                self.assertEqual(event["mode"], mode)
                self.assertIn("timed out", event["error"])
                self.assertFalse(self.manager._capture_lock.locked())
                self.manager.ack_event(event["id"])
                current = self.reserve(mode)
                self.assertNotEqual(current, old)
                self.manager._finish_capture({"mode": mode, "error": "", "result": {"late": True}}, b"late", old)
                self.assertTrue(self.manager._capture_lock.locked())
                self.assertEqual(self.manager.status()["latest"], event)
                self.manager._expire_capture(mode, old)
                self.assertEqual(self.manager._capture_owner, current)
                self.manager._finish_capture({"mode": mode, "error": "", "result": {"new": True}}, b"new", current)
                self.assertFalse(self.manager._capture_lock.locked())
                self.assertEqual(self.manager.status()["latest"]["result"], {"new": True})
                self.manager.ack_event(self.manager.status()["latest"]["id"])

    def test_cancelled_capture_timeout_releases_reservation_without_new_error(self):
        """Free a cancelled hung reader without showing a timeout for an intentionally abandoned scan."""
        revision = self.reserve("opened")
        self.manager.cancel_capture()
        self.manager._expire_capture("opened", revision)
        self.assertFalse(self.manager._capture_lock.locked())
        self.assertIsNone(self.manager.status()["latest"])

    def test_background_capture_watchdog_fires_without_ui_polling(self):
        """Verify the actual timer reports a stalled adapter even when the GUI consumes no events."""
        from PoE2_Data_Logger.platform import hotkey
        import threading
        notified = threading.Event()
        self.manager.on_event = notified.set
        with patch.object(hotkey, "CAPTURE_TIMEOUT_SECONDS", .01):
            self.reserve("opened")
        self.assertTrue(notified.wait(2))
        self.assertFalse(self.manager._capture_lock.locked())
        self.assertIn("timed out", self.manager.status()["latest"]["error"])
