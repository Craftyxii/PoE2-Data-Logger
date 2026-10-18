"""Exercise responsive region persistence and matching picker/page minimums with real Qt and SQLite."""

import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui import region_select


class RegionSaveContractTests(unittest.TestCase):
    """Keep failed saves editable and ensure accepted native selections can be persisted."""

    @classmethod
    def setUpClass(cls):
        """Create or reuse the Qt application for button, timer and resize gestures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an initialized isolated SQLite profile and a drafted regions page."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-region-save-contract-")
        self.previous_directory = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        self.page = region_select.ScanRegionsPage()
        self.page.calibrate_region("inventory_region", [.45, .5, .4, .3])
        self.button = next(button for button in self.page.findChildren(QPushButton)
                           if button.text() == "Save regions")
        self.saved = []
        self.page.saved.connect(lambda: self.saved.append(True))

    def tearDown(self):
        """Dispose of the page and restore the normal profile after pending Qt events."""
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        store.DATA_DIR = self.previous_directory
        logger._READY = False
        self.directory.cleanup()

    def stored_regions(self):
        """Read both persisted region fields after the attempted transaction has ended."""
        with logger._connect() as db:
            return (logger._meta(db, "scan_region_boxes", {}),
                    logger._meta(db, "scan_region_resolution", "auto"))

    def picker(self, region, capture=(0, 0, 3840, 2160), logical=None):
        """Create a native-pixel picker with a controlled logical monitor mapping."""
        mapping = logical or QRect(*capture)
        with patch.object(region_select, "_logical_capture_bounds", return_value=mapping):
            editor = region_select.RegionEditor(region, screen_bounds=capture)
        self.addCleanup(editor.close)
        return editor

    def test_locked_save_keeps_gui_heartbeat_draft_and_retry(self):
        """A real WAL writer produces a responsive warning; releasing it allows the same draft to save."""
        before = self.stored_regions()
        draft = dict(self.page.canvas.boxes)
        ticks = [time.perf_counter()]
        heartbeat = QTimer(self.page)
        heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()
        modal_seen = []

        def dismiss_warning():
            """Close the real warning while Qt timers continue to process nested dialog events."""
            widget = self.app.activeModalWidget()
            if isinstance(widget, QMessageBox):
                modal_seen.append(widget.text())
                widget.accept()

        writer = sqlite3.connect(store.DATA_DIR / "scans.sqlite3")
        writer.execute("BEGIN IMMEDIATE")
        try:
            QTimer.singleShot(50, self.page, dismiss_warning)
            started = time.perf_counter()
            self.button.click()
            elapsed = time.perf_counter() - started
            self.app.processEvents()
            self.assertLess(elapsed, 1, "Save must not wait SQLite's ten-second GUI-thread timeout")
            self.assertGreater(len(ticks), 1, "The warning must continue processing GUI timers")
            self.assertLess(max(b - a for a, b in zip(ticks, ticks[1:])), 1)
            self.assertEqual(len(modal_seen), 1)
            self.assertIn("locked", modal_seen[0])
            self.assertEqual(self.saved, [])
            self.assertEqual(self.stored_regions(), before)
            self.assertEqual(self.page.canvas.boxes, draft)
            self.assertIn("changes are kept", self.page.status.text())
        finally:
            heartbeat.stop()
            writer.rollback()
            writer.close()
        self.button.click()
        self.assertEqual(self.saved, [True])
        self.assertEqual(self.stored_regions(), (draft, "auto"))

    def test_partial_metadata_failure_rolls_back_and_retains_both_drafts(self):
        """A disk error after writing boxes cannot leave new boxes paired with the old resolution."""
        before = self.stored_regions()
        draft = dict(self.page.canvas.boxes)
        self.page.game_resolution.setCurrentIndex(self.page.game_resolution.findData("3840x2160"))
        original = logger._set_meta

        def fail_second_write(db, key, value):
            """Raise the storage error after the region boxes have entered the open transaction."""
            if key == "scan_region_resolution":
                raise sqlite3.OperationalError("disk I/O error")
            return original(db, key, value)

        with (patch.object(logger, "_set_meta", side_effect=fail_second_write),
              patch.object(region_select.QMessageBox, "warning") as warning):
            self.button.click()
        self.assertIn("disk I/O error", warning.call_args.args[-1])
        self.assertEqual(self.stored_regions(), before)
        self.assertEqual(self.saved, [])
        self.assertEqual(self.page.canvas.boxes, draft)
        self.assertEqual(self.page.game_resolution.currentData(), "3840x2160")
        self.button.click()
        self.assertEqual(self.stored_regions(), (draft, "3840x2160"))
        self.assertEqual(self.saved, [True])

    def test_profile_io_failure_reports_error_without_losing_the_draft(self):
        """An inaccessible database folder is reported and leaves the page retryable."""
        draft = dict(self.page.canvas.boxes)
        with (patch.object(logger, "_connect", side_effect=PermissionError("profile is not writable")),
              patch.object(region_select.QMessageBox, "warning") as warning):
            self.button.click()
        self.assertIn("not writable", warning.call_args.args[-1])
        self.assertEqual(self.saved, [])
        self.assertEqual(self.page.canvas.boxes, draft)
        self.button.click()
        self.assertEqual(self.saved, [True])

    def test_4k_picker_rejects_native_200_by_100_before_page_save(self):
        """A small 4K crop remains open instead of reporting acceptance that Save regions would reject."""
        editor = self.picker({"x": 100, "y": 100, "w": 200, "h": 100})
        editor.confirm()
        self.assertNotEqual(editor.result(), QDialog.DialogCode.Accepted)
        self.assertIn("400×200", editor.hint)

    def test_kill_region_defaults_enabled_and_tight_calibration_saves_explicitly(self):
        """A narrow counter strip saves without lowering other activities' region minimums."""
        self.assertTrue(self.page.kill_count_capture.isChecked())
        box = [.7, .2, 280 / 1920, 24 / 1080]
        self.page.kill_count_capture.setChecked(False)
        self.page.save_regions()
        self.page.calibrate_region("kill_count_region", box)
        with logger._connect() as db:
            self.assertFalse(logger._meta(db, "kill_count_scan_enabled"))
        self.assertTrue(self.page.kill_count_capture.isChecked())
        with patch.object(region_select.QMessageBox, "warning") as warning:
            self.page.save_regions()
        warning.assert_not_called()
        with logger._connect() as db:
            self.assertTrue(logger._meta(db, "kill_count_scan_enabled"))
            self.assertEqual(logger._meta(db, "scan_region_boxes")["kill_count_region"], box)
        reopened = region_select.ScanRegionsPage()
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.kill_count_capture.isChecked())
        self.assertEqual(reopened.canvas.boxes["kill_count_region"], box)
        self.page.calibrate_region("inventory_region", box)
        with patch.object(region_select.QMessageBox, "warning") as warning:
            self.page.save_regions()
        self.assertIn("200×100", warning.call_args.args[-1])

    def test_kill_picker_uses_only_its_scaled_20_pixel_minimum(self):
        """Kill calibration accepts the compact skull strip while generic selections remain large."""
        for width, height, minimum in ((1920, 1080, (20, 20)), (3840, 2160, (40, 40))):
            with self.subTest(size=(width, height)):
                with patch.object(region_select, "_logical_capture_bounds", return_value=QRect(0, 0, width, height)):
                    editor = region_select.RegionEditor(
                        {"x": 100, "y": 100, "w": minimum[0], "h": minimum[1]},
                        screen_bounds=(0, 0, width, height), region_key="kill_count_region")
                self.addCleanup(editor.close)
                editor.confirm()
                self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)

    def test_accepted_picker_minimum_saves_at_1080p_1440p_4k_and_ultrawide(self):
        """The smallest accepted crop at each supported aspect and resolution persists unchanged."""
        for width, height, minimum in ((1920, 1080, (200, 100)),
                                       (2560, 1440, (267, 134)),
                                       (3840, 2160, (400, 200)),
                                       (3440, 1440, (359, 134))):
            with self.subTest(capture=(width, height)):
                editor = self.picker({"x": 100, "y": 100, "w": minimum[0], "h": minimum[1]},
                                     capture=(0, 0, width, height))
                editor.confirm()
                self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
                region = editor.region()
                box = [region["x"] / width, region["y"] / height,
                       region["w"] / width, region["h"] / height]
                self.page.calibrate_region("inventory_region", box)
                previous_saves = len(self.saved)
                with patch.object(region_select.QMessageBox, "warning") as warning:
                    self.button.click()
                warning.assert_not_called()
                self.assertEqual(len(self.saved), previous_saves + 1)
                self.assertEqual(self.stored_regions()[0]["inventory_region"], box)
                self.assertEqual(region_select.region_for("inventory_region", (0, 0, width, height)), region)
                editor.close()

    def test_scaled_4k_resize_reaches_the_shared_save_minimum(self):
        """A 200%-DPI resize stops at 400×200 native pixels and Enter accepts the resulting crop."""
        editor = self.picker({"x": 200, "y": 200, "w": 800, "h": 400},
                             logical=QRect(0, 0, 1920, 1080))
        editor.show()
        self.app.processEvents()
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=editor.selection.bottomRight())
        QTest.mouseMove(editor, editor.selection.topLeft() + QPoint(5, 5))
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=editor.selection.topLeft() + QPoint(5, 5))
        self.app.processEvents()
        region = editor.region()
        self.assertEqual((region["w"], region["h"]), (400, 200))
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
