"""Exercise worker admission failures and GUI responsiveness independently of OCR accuracy."""

from concurrent.futures import Future
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ReleaseHUDAuditTests(unittest.TestCase):
    """Keep failed or slow background tasks from freezing scan review and other HUD controls."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application needed for native widget events and worker delivery."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated shown profile with polling stopped and a reusable image file."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-release-hud-")
        self.previous = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1024, 768)
        self.window.show()
        self.app.processEvents()
        self.image = Image.new("RGB", (600, 400), (120, 130, 140))
        self.image_path = Path(self.tmp.name) / "capture.png"
        self.image.save(self.image_path)

    def tearDown(self):
        """Close workers and restore shared profile and scan-mode ownership."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def capture(self, mode):
        """Start a real UI capture path while stubbing only inventory geometry or the file picker."""
        if mode == "currency":
            with patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image):
                self.window._inventory_captured(self.image, live=False)
        elif mode == "ritual":
            self.window._ritual_captured(self.image, live=False)
        else:
            self.window.mode_select.setCurrentIndex(self.window.mode_select.findData(mode))
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.image_path), "")):
                self.window.scan_file()

    def test_enqueue_failure_retires_busy_marker_and_scan_draft_for_every_capture_path(self):
        """Rejected isolated OCR submissions must become rejectable failed scans, never permanent busy work."""
        for mode in ("currency", "ritual", "opened", "seed", "both"):
            with self.subTest(mode=mode):
                with patch.object(native_desktop.ocr_workers, "submit", side_effect=RuntimeError("Worker queue unavailable")):
                    self.capture(mode)
                self.assertEqual(self.window._pending_tasks, {})
                self.assertIsNone(self.window._inventory_reading)
                self.assertIsNone(self.window._ritual_reading)
                self.assertIsNone(self.window._remnant_reading)
                self.assertTrue(self.window._failed_review)
                self.assertIn("Worker queue unavailable", self.window.review_summary.text())
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertTrue(self.window.reject_scan_button.isEnabled())
                self.window.reject_scan_button.click()
                self.assertIsNone(self.window.pending_review_kind)
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        # A failure must leave the normal approval path usable on the next reading.
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 292}],
                                     "unknown": []}, live=False)
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 292})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_immediately_completed_work_does_not_leave_a_stale_busy_message(self):
        """A future completed before callback registration must clear its admission message and pending marker."""
        future = Future()
        future.set_result("finished")
        delivered = []
        with patch.object(self.window.pool, "submit", return_value=future):
            self.window._submit("Queued export", lambda: "unused", delivered.append)
        self.app.processEvents()
        self.assertEqual(delivered, ["finished"])
        self.assertEqual(self.window._pending_tasks, {})
        self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_scan_watchdog_clears_every_reading_and_ignores_late_worker_results(self):
        """Hold isolated OCR admission, then verify overdue readers cannot approve or overwrite another scan."""
        for mode in ("currency", "ritual", "opened", "seed", "both"):
            with self.subTest(mode=mode):
                future = Future()
                future.set_running_or_notify_cancel()
                with patch.object(native_desktop, "SCAN_TIMEOUT_MS", 10), \
                        patch.object(native_desktop.ocr_workers, "submit", return_value=future):
                    self.capture(mode)
                QTest.qWait(40)
                self.assertEqual(self.window._pending_tasks, {})
                self.assertEqual(self.window._scan_task_timers, {})
                self.assertIsNone(self.window._inventory_reading)
                self.assertIsNone(self.window._ritual_reading)
                self.assertIsNone(self.window._remnant_reading)
                self.assertTrue(self.window._failed_review)
                self.assertIn("timed out", self.window.review_summary.text())
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertTrue(self.window.reject_scan_button.isEnabled())
                future.set_result({"unexpected": "late recognition"})
                self.app.processEvents()
                self.assertTrue(self.window._failed_review)
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                self.window.reject_scan_button.click()

    def test_old_scan_watchdog_cannot_fail_a_replacement_review(self):
        """Expire isolated inventory work while retaining a newer Ritual capture's reading."""
        old, new = Future(), Future()
        with patch.object(native_desktop.ocr_workers, "submit", side_effect=[old, new]):
            self.capture("currency")
            old_key = next(iter(self.window._pending_tasks))
            self.capture("ritual")
        capture = self.window._ritual_reading
        self.window._expire_scan_task(old_key, old)
        self.app.processEvents()
        self.assertIs(self.window._ritual_reading, capture)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertFalse(self.window._failed_review)
        self.assertEqual(len(self.window._pending_tasks), 1)
        self.window._expire_scan_task(next(iter(self.window._pending_tasks)), new)
        self.app.processEvents()
        self.assertEqual(self.window._pending_tasks, {})

    def test_close_retires_scan_watchdog_callbacks(self):
        """Close with held isolated OCR admission and release timeout callbacks before the reader returns."""
        future = Future()
        future.set_running_or_notify_cancel()
        with patch.object(native_desktop.ocr_workers, "submit", return_value=future):
            self.capture("currency")
        self.assertTrue(self.window._scan_task_timers)
        self.window.close()
        self.assertEqual(self.window._scan_task_timers, {})
        self.assertEqual(self.window._pending_tasks, {})
        future.set_result({"unexpected": "closed reader"})
        self.app.processEvents()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_storage_errors_cannot_leave_remnant_review_on_reading(self):
        """Fail context reads and cleanup around isolated OCR admission/completion while retaining error review."""
        with patch.object(logger, "session_generation", side_effect=OSError("Profile unavailable")), \
                patch.object(logger, "discard_ocr_id", side_effect=OSError("Cleanup unavailable")):
            self.capture("opened")
        self.assertIsNone(self.window._remnant_reading)
        self.assertFalse(self.window._pending_tasks)
        self.assertTrue(self.window._failed_review)
        self.assertIn("reservation cleanup failed", self.window.review_summary.text())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.window.reject_scan_button.click()
        future = Future()
        future.set_running_or_notify_cancel()
        with patch.object(native_desktop.ocr_workers, "submit", return_value=future):
            self.capture("seed")
        with patch.object(logger, "session_generation", side_effect=OSError("Profile unavailable")), \
                patch.object(logger, "discard_ocr_id", side_effect=OSError("Cleanup unavailable")):
            future.set_result({"unexpected": "unverifiable context"})
            self.app.processEvents()
        self.assertIsNone(self.window._remnant_reading)
        self.assertFalse(self.window._pending_tasks)
        self.assertFalse(self.window._scan_task_timers)
        self.assertTrue(self.window._failed_review)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_slow_worker_does_not_block_navigation_or_deferred_gui_events(self):
        """Keep tabs, counter drafts and timer events responsive while background work is waiting."""
        release = threading.Event()
        started = threading.Event()
        completed = []
        ticks = []
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))

        def held_worker():
            """Wait in a worker thread until this test has exercised the active GUI."""
            started.set()
            return release.wait(30)

        try:
            self.window._submit("Background scan", held_worker, completed.append)
            self.assertTrue(started.wait(1))
            timer.start()
            self.window.normal.setText("123")
            for button in self.window.nav_buttons:
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertEqual(button.property("active"), "true")
            QTest.qWait(60)
            self.assertGreaterEqual(len(ticks), 2)
            self.assertTrue(self.window._pending_tasks)
            self.assertEqual(self.window.normal.text(), "123")
        finally:
            timer.stop()
            release.set()
        deadline = time.monotonic() + 3
        while self.window._pending_tasks and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertEqual(completed, [True])
        self.assertEqual(self.window._pending_tasks, {})


if __name__ == "__main__":
    unittest.main()
