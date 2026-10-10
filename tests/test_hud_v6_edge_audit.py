"""HUD edge workflows retain active work when map navigation fails validation."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class HUDEdgeAuditTests(unittest.TestCase):
    """Check rejected header navigation while a genuine file scan owns its callback."""

    @classmethod
    def setUpClass(cls):
        """Reuse the event loop needed by the desktop widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated profile and hold asynchronous scan delivery for inspection."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-hud-edge-audit-")
        self.previous_data = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.path = Path(self.tmp.name) / "remnant.png"
        Image.new("RGB", (600, 400), "tan").save(self.path)

    def tearDown(self):
        """Close this window's workers and restore profile and capture mode ownership."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_invalid_new_map_retains_pending_remnant_file_result(self):
        """A rejected New map click must still let an already queued file scan finish."""
        self.window.mode_select.setCurrentIndex(self.window.mode_select.findData("opened"))
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            self.window.scan_file()
        context = logger.scan_context()
        callback = self.jobs[-1][1]
        self.window.unique.setText("bad")
        next(button for button in self.window.findChildren(QPushButton)
             if button.text() == "+ New map").click()
        self.assertIn("Unique kills must be a whole number", self.window.statusBar().currentMessage())
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], "_target_map_id": "M0001", **context}
        callback(result)
        self.assertIs(self.window.results["opened"], result,
                      "A rejected map change must not silently discard the pending file result.")
        self.assertIsNone(self.window._remnant_reading)
        self.assertNotIn("Reading remnant image", self.window.review_summary.text())

    def test_close_retires_active_hotkey_capture_before_detaching_callbacks(self):
        """Closing the HUD must invalidate an in-flight capture and suppress its late result."""
        manager = service.HOTKEY
        revision = manager._capture_revision
        callback_owners = []
        cancel = manager.cancel_capture

        def record_cancellation(*args, **kwargs):
            """Observe whether capture preparation still belongs to the closing HUD."""
            callback_owners.append(manager.before_capture)
            return cancel(*args, **kwargs)

        self.assertTrue(manager._capture_lock.acquire(blocking=False))
        try:
            with patch.object(manager, "cancel_capture", side_effect=record_cancellation):
                self.window.close()
        finally:
            manager._finish_capture({"mode": "opened", "result": {}, "error": ""},
                                    b"late capture", revision)
        self.assertEqual(callback_owners, [self.window._prepare_overlay_capture])
        self.assertIsNone(manager.before_capture)
        self.assertIsNone(manager.on_event)
        self.assertIsNone(manager.status()["latest"],
                          "A closed window's worker must not publish a stale capture.")


if __name__ == "__main__":
    unittest.main()
