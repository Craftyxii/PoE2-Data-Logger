"""Use real native-reader stalls to check desktop rejection, replacement and reset ownership."""

from concurrent.futures import Future
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers, service, store
from PoE2_Data_Logger.ui import native_desktop
from tests.test_hotkey_ocr_workers import _blocked_reader


def _inventory_reader():
    """Return a normal editable inventory result from the replacement child."""
    return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 292}], "unknown": []}


class DesktopOCRWorkerTests(unittest.TestCase):
    """Verify discarded scan work is reaped without cancelling unrelated save or export work."""

    @classmethod
    def setUpClass(cls):
        """Share the application required by actual native review controls."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger and valid screenshot for file, inventory and Ritual capture paths."""
        ocr_workers.shutdown(wait=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-desktop-worker-")
        self.previous = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = native_desktop.LoggerWindow()
        self.window._poll.stop()
        self.image = Image.new("RGB", (600, 400), (80, 90, 100))
        self.path = Path(self.tmp.name) / "capture.png"
        self.image.save(self.path)
        self.sequence = 0

    def tearDown(self):
        """Close callbacks, reap test readers and restore the caller's profile and mode."""
        self.window.close()
        ocr_workers.shutdown(wait=True)
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def wait_until(self, predicate):
        """Process UI signals while bounding child startup, cleanup and review completion."""
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate())

    def capture(self, mode, work):
        """Run the actual UI capture path while substituting only its isolated recognizer descriptor."""
        with patch.object(ocr_workers, "request", return_value=work):
            if mode == "currency":
                with patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image):
                    self.window._inventory_captured(self.image, live=False)
            elif mode == "ritual":
                self.window._ritual_captured(self.image, live=False)
            else:
                self.window.mode = mode
                with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
                    self.window.scan_file()

    def blocked_capture(self, mode):
        """Admit a genuinely stuck child through a capture's production callback and watchdog registration."""
        self.sequence += 1
        pid_file = Path(self.tmp.name) / f"reader-{self.sequence}.pid"
        work = ocr_workers.Request(_blocked_reader, (str(pid_file),), {}, str(store.DATA_DIR),
                                   ocr_runtime.active_threads(), time.monotonic() + 90)
        self.capture(mode, work)
        future = next(reversed(self.window._scan_task_futures.values()))
        self.wait_until(lambda: pid_file.exists() and bool(pid_file.read_text(encoding="ascii")))
        return future, int(pid_file.read_text(encoding="ascii"))

    def assert_retired(self, future, pid):
        """Require immediate callback retirement and actual native-child reaping without any saved activity."""
        self.assertTrue(future.cancelled())
        self.assertEqual(self.window._scan_task_futures, {})
        self.assertEqual(self.window._scan_task_timers, {})
        self.wait_until(lambda: pid not in {p.pid for p in multiprocessing.active_children()})
        self.app.processEvents()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_reject_reaps_every_desktop_reader_and_leaves_unrelated_export_pending(self):
        """Repeated reject/retry across every desktop scan mode frees native slots without touching I/O futures."""
        export = Future()
        with patch.object(self.window.pool, "submit", return_value=export):
            self.window._submit("Writing export…", lambda: b"export", lambda result: None)
        export_key = next(iter(self.window._pending_tasks))
        for mode in ("currency", "ritual", "opened", "seed", "both"):
            with self.subTest(mode=mode):
                future, pid = self.blocked_capture(mode)
                self.window.reject_review()
                self.assert_retired(future, pid)
                self.assertIsNone(self.window.pending_review_kind)
                self.assertEqual(set(self.window._pending_tasks), {export_key})
                self.assertFalse(export.cancelled())
        work = ocr_workers.Request(_inventory_reader, (), {}, str(store.DATA_DIR),
                                   ocr_runtime.active_threads(), time.monotonic() + 90)
        self.capture("currency", work)
        self.wait_until(lambda: self.window._inventory_reading is None)
        self.assertEqual(self.window.inventory_table.rowCount(), 1)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(self.window.inventory_table.item(0, 2).text(), "292")
        export.set_result(b"export")
        self.app.processEvents()

    def test_new_capture_retires_older_same_mode_and_other_mode_readers(self):
        """Replacing the capture changes its identity, cancels only old work and preserves the new Reading task."""
        for first_mode, second_mode in (("currency", "currency"), ("currency", "ritual")):
            with self.subTest(first=first_mode, second=second_mode):
                old, old_pid = self.blocked_capture(first_mode)
                current, current_pid = self.blocked_capture(second_mode)
                self.assertTrue(old.cancelled())
                self.wait_until(lambda: old_pid not in {p.pid for p in multiprocessing.active_children()})
                self.assertFalse(current.done())
                self.assertEqual(list(self.window._scan_task_futures.values()), [current])
                self.assertEqual(len(self.window._pending_tasks), 1)
                self.window.reject_review()
                self.assert_retired(current, current_pid)

    def test_remnant_review_reaps_old_inventory_and_ritual_readers_and_ignores_late_callbacks(self):
        """A new remnant preserves its reservation, preview and drafts after real child cancellation and late replies."""
        for mode in ("currency", "ritual"):
            with self.subTest(mode=mode):
                future, pid = self.blocked_capture(mode)
                callback, _ = next(iter(self.window._pending_tasks.values()))
                result = {"mode": "opened", "status": "Review this new remnant.", "can_use": False,
                          "first_recipe": None, "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}],
                          **logger.scan_context()}
                self.window.show_result("opened", result, self.path.read_bytes())
                self.window.first_recipe.setText("Keep this recipe draft")
                snapshot = (logger.get_state()["ocr_pending"], self.window.review_summary.text(),
                            self.window.preview.pixmap().cacheKey(), self.window.first_recipe.text())
                try:
                    self.assert_retired(future, pid)
                    self.assertEqual(self.window._pending_tasks, {})
                    callback({"items": [], "unknown": [], "raw_text": "obsolete"})
                    self.assertFalse(callback._scan_failure(RuntimeError("late cancelled reader")))
                    self.assertEqual(self.window.pending_review_kind, "remnant")
                    self.assertEqual((logger.get_state()["ocr_pending"], self.window.review_summary.text(),
                                      self.window.preview.pixmap().cacheKey(), self.window.first_recipe.text()), snapshot)
                finally:
                    self.window.reject_review()

    def test_session_reset_reaps_pending_reader_before_retiring_callback_state(self):
        """A confirmed reset cancels the old session's reader and prevents stale results from reaching new logs."""
        future, pid = self.blocked_capture("currency")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.assert_retired(future, pid)
        self.assertEqual(self.window._pending_tasks, {})
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")

    def test_discard_reaps_every_remnant_reader_and_allows_same_mode_retry(self):
        """Discarding a remnant releases its actual reader and admission so a retry can start immediately."""
        for mode in ("opened", "seed", "both"):
            with self.subTest(mode=mode):
                for attempt in range(2):
                    with self.subTest(attempt=attempt):
                        future, pid = self.blocked_capture(mode)
                        self.assertFalse(future.done())
                        self.assertEqual(list(self.window._scan_task_futures.values()), [future])
                        self.window.discard_scan()
                        self.assert_retired(future, pid)
                        self.assertEqual(self.window._pending_tasks, {})
                        self.assertIsNone(self.window.pending_review_kind)
                        self.assertIsNone(self.window._remnant_reading)
                        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_failed_discard_preserves_live_reader_and_watchdog_until_retry(self):
        """A database lock preserves the owned Reading task; retry cancels and reaps it after storage succeeds."""
        for mode in ("opened", "seed", "both"):
            with self.subTest(mode=mode):
                future, pid = self.blocked_capture(mode)
                capture = self.window._remnant_reading
                key = next(iter(self.window._scan_task_futures))
                timer = self.window._scan_task_timers[key]
                summary = self.window.review_summary.text()
                kind = self.window.pending_review_kind
                with patch.object(logger, "discard_ocr_id", side_effect=sqlite3.OperationalError("database is locked")), \
                        patch.object(service.HOTKEY, "cancel_capture") as cancel:
                    with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                        self.window.discard_scan()
                cancel.assert_not_called()
                self.assertFalse(future.done())
                self.assertIs(self.window._remnant_reading, capture)
                self.assertIs(self.window._scan_task_futures[key], future)
                self.assertIn(key, self.window._pending_tasks)
                self.assertIs(self.window._scan_task_timers[key], timer)
                self.assertTrue(timer.isActive())
                self.assertEqual(self.window.review_summary.text(), summary)
                self.assertEqual(self.window.pending_review_kind, kind)
                self.assertFalse(self.window._failed_review)
                self.assertIn(pid, {child.pid for child in multiprocessing.active_children()})
                self.window.discard_scan()
                self.assert_retired(future, pid)
                self.assertEqual(self.window._pending_tasks, {})
                self.assertIsNone(self.window.pending_review_kind)
                self.assertIsNone(self.window._remnant_reading)


if __name__ == "__main__":
    unittest.main()
