"""Consume retryable physical-adapter errors through the real HUD hotkey event path."""

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class CaptureAdmissionUITests(unittest.TestCase):
    """Verify busy adapter retries display feedback without admitting a permanently reading task."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application for the actual desktop event consumer."""
        cls.app = QApplication.instance() or QApplication([])

    def test_busy_capture_error_is_visible_and_leaves_no_reading_task(self):
        """Poll a cancelled screenshot's Currency retry error without requiring missing capture context."""
        previous = store.DATA_DIR
        entered, release, finished = (threading.Event() for _ in range(3))
        threads = []
        window = None
        with tempfile.TemporaryDirectory(prefix="poe2-capture-admission-ui-") as directory:
            store.DATA_DIR = Path(directory)
            logger._READY = False
            logger.initialize()
            logger.start_map()

            def grab(**kwargs):
                """Block only the physical screenshot adapter while the test continues pumping Qt."""
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test did not release the screenshot adapter.")
                return Image.new("RGB", (400, 300))

            manager = HotkeyManager(supported=False, grabber=Mock(side_effect=grab),
                                    readers={"opened": Mock()})
            original = manager._capture

            def capture(*args):
                """Track physical worker exit before restoring the database profile."""
                threads.append(threading.current_thread())
                try:
                    original(*args)
                finally:
                    finished.set()

            with patch.object(service, "HOTKEY", manager), \
                    patch.object(manager, "_capture", side_effect=capture):
                try:
                    window = LoggerWindow()
                    window._poll.stop()
                    manager.before_capture = None
                    manager.capture("remnant", background=True)
                    deadline = time.monotonic() + 3
                    while not entered.is_set() and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.01)
                    self.assertTrue(entered.is_set())
                    manager.cancel_capture(modes=("opened",))
                    manager.capture("currency", background=True)
                    event = manager.status()["latest"]
                    self.assertIsNone(event["result"])
                    window.poll()
                    self.assertEqual(window._latest_hotkey, event["id"])
                    self.assertIn("adapter is still busy", window.statusBar().currentMessage())
                    self.assertIsNone(window._remnant_reading)
                    self.assertIsNone(window._inventory_reading)
                    self.assertEqual(window._pending_tasks, {})
                    self.assertEqual(window._scan_task_futures, {})
                    self.assertIsNone(manager._active_capture_mode)
                    self.assertFalse(manager._capture_lock.locked())
                    self.assertEqual(len(threads), 1)
                    release.set()
                    self.assertTrue(finished.wait(2))
                    window.poll()
                    self.assertIs(manager.status()["latest"], event)
                    manager.readers["opened"].assert_not_called()
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(2)
                    if window is not None:
                        window.close()
                        window.pool.shutdown(wait=True, cancel_futures=True)
                    self.app.processEvents()
                    store.DATA_DIR = previous
                    logger._READY = False


if __name__ == "__main__":
    unittest.main()
