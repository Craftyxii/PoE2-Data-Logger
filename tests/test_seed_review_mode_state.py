"""Changing the displayed remnant mode retains one capture's seed review decisions."""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class SeedReviewModeStateTests(unittest.TestCase):
    """Protect rejected rows and keyboard corrections until a fresh capture replaces their queue."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication needed for actual mode-change and keyboard-edit signals."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open a fresh visible profile with two known family seed stages and polling stopped."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-seed-mode-state-")
        self.previous = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.window.mode_select.setCurrentIndex(self.window.mode_select.findData("seed"))
        with logger._connect() as db:
            stages = db.execute("SELECT * FROM seed_states WHERE family=3 ORDER BY sockets LIMIT 2").fetchall()
        self.readings = [{"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                          "seed_rune": stage["seed_rune"], "candidates": [3], "can_commit": False}
                         for stage in stages]
        self.assertEqual(len(self.readings), 2)
        self.capture()
        self.app.processEvents()

    def tearDown(self):
        """Close this GUI's workers and restore shared storage and incoming scan mode."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def capture(self):
        """Deliver a genuinely new result object at the completed-scanner boundary."""
        result = {"mode": "seed", "status": "Review visible seeds",
                  "remnants": [dict(reading) for reading in self.readings], **logger.scan_context()}
        self.window._scan_done("seed", result, None)
        return result

    def change_view_and_return(self):
        """Use the actual mode-selector signals without supplying any new OCR reading."""
        for mode in ("opened", "seed"):
            self.window.mode_select.setCurrentIndex(self.window.mode_select.findData(mode))
            self.app.processEvents()

    def edit(self, field, text):
        """Replace a seed draft through keyboard events so textEdited updates its owned reading."""
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, text)
        self.app.processEvents()

    def test_partial_rejection_survives_mode_changes_and_cannot_be_saved(self):
        """Keep a rejected row locked while a different seed from the capture remains pending."""
        self.window.seed_table.setCurrentCell(0, 2)
        self.window.reject_scan_button.click()
        self.assertTrue(self.window._seed_readings[0]["rejected"])
        self.change_view_and_return()
        self.window.seed_table.setCurrentCell(0, 2)
        self.assertTrue(self.window._seed_readings[0]["rejected"])
        self.assertEqual(self.window.seed_table.item(0, 4).text(), "Rejected")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.window.approve_scan_button.click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.window.seed_table.setCurrentCell(1, 2)
        self.window.approve_scan_button.click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertTrue(self.window._seed_readings[0]["rejected"])
        self.assertTrue(self.window._seed_readings[1].get("saved"))

    def test_keyboard_corrections_survive_mode_changes_before_any_approval(self):
        """Retain tentative socket, position and rune corrections instead of resurrecting OCR guesses."""
        self.window.seed_table.setCurrentCell(1, 2)
        self.edit(self.window.seed_sockets, "6")
        self.edit(self.window.seed_slot, "P2")
        self.edit(self.window.seed_rune, "Power")
        draft = dict(self.window._seed_readings[1])
        self.change_view_and_return()
        self.window.seed_table.setCurrentCell(1, 2)
        self.assertEqual(self.window._seed_readings[1], draft)
        self.assertEqual(self.window.seed_sockets.text(), "6")
        self.assertEqual(self.window.seed_slot.text(), "P2")
        self.assertEqual(self.window.seed_rune.text(), "Power")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_exhausted_rejection_does_not_reopen_a_retired_capture(self):
        """Retire the exhausted seed result so revisiting its mode creates no pending review or stale token."""
        for row in (0, 1):
            self.window.seed_table.setCurrentCell(row, 2)
            self.window.reject_scan_button.click()
        self.assertIsNone(self.window.results["seed"])
        self.change_view_and_return()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_fresh_seed_capture_replaces_previous_rejections_and_corrections(self):
        """A new reading of the same seed geometry starts a new review outside Both mode."""
        previous = self.window.results["seed"]
        self.window.seed_table.setCurrentCell(0, 2)
        self.window.reject_scan_button.click()
        self.window.seed_table.setCurrentCell(1, 2)
        self.edit(self.window.seed_rune, "Power")
        current = self.capture()
        self.assertIsNot(current, previous)
        self.assertFalse(any(reading.get("rejected") for reading in self.window._seed_readings))
        self.assertEqual(self.window._seed_readings[1]["seed_rune"], self.readings[1]["seed_rune"])
        self.window.seed_table.setCurrentCell(0, 2)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_manual_seed_learning_and_log_are_one_undoable_approval(self):
        """Undo one manual approval's remnant, learned scan and link together while retaining raw capture bytes."""
        image = Image.new("RGB", (96, 96), (15, 20, 45))
        ImageDraw.Draw(image).ellipse((12, 12, 84, 84), fill=(200, 100, 50))
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        self.window.images["seed"] = raw.getvalue()
        self.window._seed_readings[0]["bar_bounds"] = {"x": 0, "y": 0, "width": 96, "height": 96}
        self.window.seed_table.setCurrentCell(0, 2)
        with logger._connect() as db:
            before = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
        with patch.object(store, "_reviewed_vector", return_value=bytes(1296 * 4)):
            self.window.approve_scan_button.click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        scan = store.list_scans()[0]
        capture = store.image_for(scan["id"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], before + 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 1)
        self.assertEqual(logger.undo_last_action()["action"], "Seed approval")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(store.list_scans(), [])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reviewed_glyphs").fetchone()[0], 0)
        self.assertEqual(capture.read_bytes(), raw.getvalue())


if __name__ == "__main__":
    unittest.main()
