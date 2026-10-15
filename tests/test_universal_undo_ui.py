"""Desktop undo checks for saved actions, restored forms and retired asynchronous scan work."""

import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class UniversalUndoUITests(unittest.TestCase):
    """Exercise the shared Undo button against persisted session changes and pending reviews."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication required for desktop input and deferred callback tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated logger window with polling paused and a reusable capture image."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-universal-undo-ui-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1280, 720)
        self.window.show()
        self.app.processEvents()
        self.image = Image.new("RGB", (600, 400), (120, 130, 140))
        raw = io.BytesIO()
        self.image.save(raw, format="PNG")
        self.raw = raw.getvalue()

    def tearDown(self):
        """Close desktop workers and restore storage after each isolated undo scenario."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def click(self, action):
        """Deliver a real mouse click after exposing an action inside its active scroll page."""
        page = self.window.tabs.currentWidget()
        if page.isAncestorOf(action) and hasattr(page, "ensureWidgetVisible"):
            page.ensureWidgetVisible(action)
            self.app.processEvents()
        self.assertTrue(action.isEnabled())
        QTest.mouseClick(action, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def save_inventory(self, phase, quantity):
        """Review a clear inventory row and save it through the desktop approval control."""
        select(self.window.inventory_phase, phase)
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": quantity}],
                                     "unknown": []}, live=False)
        self.click(self.window.approve_scan_button)

    def stage_propagation(self):
        """Stage the first database rune of a known recipe for explicit desktop approval."""
        recipe = "Unique Belt"
        with logger._connect() as db:
            combo = db.execute("SELECT combo FROM recipes WHERE name=?", (recipe,)).fetchone()[0]
        rune = combo.split("+")[0].strip()
        result = {"mode": "propagation", "runes": [rune], "positions": [1],
                  "selected_recipe": recipe, "can_use": True, "status": "Review marked rune",
                  **logger.scan_context()}
        self.window._propagation_read(result, self.raw)
        approve = self.window.propagation_recipe_table.cellWidget(0, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        return result, approve

    def test_currency_undo_restores_previous_snapshot_and_preserves_other_snapshots(self):
        """Undo a repeated end snapshot without removing its baseline or earlier approved scan."""
        self.save_inventory("start", 7)
        self.save_inventory("end", 11)
        self.save_inventory("end", 13)
        count = logger.get_state()["scan_commit_count"]
        action = logger.undo_action()
        self.assertEqual(self.window.undo_button.text(), "Undo")
        self.assertIn(action, self.window.undo_button.toolTip())
        self.click(self.window.undo_button)
        totals = logger.currency_for_map("M0001")
        self.assertEqual(totals["start"], {"Chaos Orb": 7})
        self.assertEqual(totals["end"], {"Chaos Orb": 11})
        self.assertEqual(totals["net"], {"Chaos Orb": 4})
        self.assertIn("Chaos Orb: +4", self.window.currency_saved.text())
        self.assertEqual(self.window.state["scan_commit_count"], count - 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertIsNone(self.window._saved_badge)

    def test_new_map_undo_restores_saved_setup_counts_and_currency(self):
        """Return to the previous map with its saved settings and finalized kill values intact."""
        self.window.waystone.setText("85")
        self.window.save_map_settings()
        self.save_inventory("start", 7)
        for field, text in zip((self.window.normal, self.window.magic, self.window.rare, self.window.unique),
                               ("123", "9", "2", "1")):
            field.setText(text)
        self.window.finish_map()
        self.assertEqual(self.window.state["current_map_id"], "M0002")
        self.window.waystone.setText("99")
        self.window.normal.setText("999")
        self.click(self.window.undo_button)
        self.assertEqual(self.window.state["current_map_id"], "M0001")
        self.assertEqual(float(self.window.waystone.text()), 85)
        self.assertEqual([field.text() for field in
                          (self.window.normal, self.window.magic, self.window.rare, self.window.unique)],
                         ["123", "9", "2", "1"])
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertFalse(logger.get_state()["pending_new_map"])

    def test_new_map_after_undo_finalizes_the_corrected_previous_map(self):
        """Save corrected previous-map totals before advancing again after undoing New map."""
        self.window.normal.setText("12")
        self.window.finish_map()
        self.click(self.window.undo_button)
        self.window.normal.setText("77")
        self.window.finish_map()
        with logger._connect() as db:
            saved = json.loads(db.execute("SELECT kills_json FROM maps WHERE map_id='M0001'").fetchone()[0])
        self.assertEqual(saved[0], 77)
        self.assertEqual(self.window.state["current_map_id"], "M0002")
        self.click(self.window.undo_button)
        self.assertEqual(self.window.normal.text(), "77")

    def test_new_map_after_undo_saves_waystone_correction_to_map_export(self):
        """Reopen through the shared Undo button, save setup edits and export the corrected map."""
        self.window.waystone.setText("40")
        self.window.save_map_settings()
        self.window.normal.setText("10")
        self.window.finish_map()
        self.click(self.window.undo_button)
        self.window.waystone.setText("99")
        self.window.save_map_settings()
        self.assertNotIn("recorded setup is unchanged", self.window.map_scan_status.text())
        self.window.finish_map()
        rows = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        first = next(row for row in rows if row["Map ID"] == "M0001")
        self.assertEqual((first["Waystone %"], first["Normal Kills"]), ("99", "10"))
        self.assertEqual(self.window.state["current_map_id"], "M0002")

    def test_undo_chain_completion_restores_the_saved_chain_and_expedition(self):
        """Reopen the completed expedition while preserving its approved rune part and remnant count."""
        result, approve = self.stage_propagation()
        self.click(approve)
        chain = logger.get_state()["chain"]
        remnant = self.window.header_remnant_id.text()
        self.assertEqual(len(chain), 1)
        self.click(self.window.header_complete_chain_button)
        self.assertEqual(self.window.header_expedition.currentData(), 2)
        self.click(self.window.undo_button)
        self.assertEqual(self.window.header_expedition.currentData(), 1)
        self.assertEqual(self.window.header_remnant_id.text(), remnant)
        self.assertEqual(logger.get_state()["chain"], chain)
        self.assertEqual(self.window.expedition_chain_table.rowCount(), 1)
        self.assertTrue(self.window.header_complete_chain_button.isEnabled())
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 0)
        self.assertNotIn(result["_chain_accept_request"], self.window._accepted_propagation_requests)

    def test_undo_approved_propagation_removes_its_part_and_count(self):
        """Undo one explicitly approved scan as one action, retiring its rune and detonation together."""
        _result, approve = self.stage_propagation()
        self.click(approve)
        self.assertEqual(len(logger.get_state()["chain"]), 1)
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.click(self.window.undo_button)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertIsNone(logger.get_state()["detonated"])
        self.assertEqual(self.window.expedition_chain_table.rowCount(), 0)
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())

    def test_undo_retires_delayed_capture_worker_and_overlay_callbacks(self):
        """Prevent pre-undo OCR completions and queued overlay reveals from reopening a cleared review."""
        self.save_inventory("start", 7)
        jobs = []
        with patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image), \
                patch.object(self.window, "_submit", side_effect=lambda label, work, done: jobs.append(done)):
            self.window._inventory_captured(self.image, live=False)
        generation = logger.session_generation()
        token = self.window._overlay_review_token
        callback = Mock()
        self.window._pending_tasks["retired"] = (callback, generation)
        self.window._held_remnant_review = ("remnant", "Old remnant", True, False)
        with patch.object(service.HOTKEY, "cancel_capture") as cancel:
            self.click(self.window.undo_button)
        cancel.assert_called_once_with()
        self.assertGreater(logger.session_generation(), generation)
        jobs[0]({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 99}], "unknown": []})
        self.window._task_done("retired", {"unexpected": True}, None)
        with patch.object(self.window, "show_overlay") as show:
            self.window._reveal_review_overlay(token)
        callback.assert_not_called()
        show.assert_not_called()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._held_remnant_review)
        self.assertIsNone(self.window._inventory_reading)
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})

    def test_undo_cancels_waiting_chain_retry_without_resaving_an_old_capture(self):
        """Retire a deferred approval when undo removes the previous saved chain part."""
        _result, approve = self.stage_propagation()
        self.click(approve)
        _result, approve = self.stage_propagation()
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(logger, "accept_propagation_part", side_effect=busy) as save:
            self.click(approve)
            pending = self.window._chain_save_pending
            self.assertIsNotNone(pending)
            attempts = save.call_count
            self.click(self.window.undo_button)
            self.window._retry_approved_chain_save(pending)
            QTest.qWait(80)
            self.assertEqual(save.call_count, attempts)
        self.assertIsNone(self.window._chain_save_pending)
        self.assertTrue(self.window.propagation_recipe_table.isEnabled())
        self.assertEqual(logger.get_state()["chain"], [])

    def test_undo_retains_the_active_page_and_its_scroll_position(self):
        """Restore saved values without moving an unrelated scroll page to the Review tab or its top."""
        self.save_inventory("start", 7)
        self.window.resize(1280, 600)
        self.window.tabs.setCurrentIndex(2)
        self.app.processEvents()
        page = self.window.tabs.currentWidget()
        bar = page.verticalScrollBar()
        self.assertGreater(bar.maximum(), 100)
        bar.setValue(100)
        self.click(self.window.undo_button)
        self.assertIs(self.window.tabs.currentWidget(), page)
        self.assertEqual(bar.value(), 100)

    def test_failed_undo_and_empty_history_preserve_unsaved_fields(self):
        """Leave drafts intact on failure and disable the shared button when all saved actions are undone."""
        self.window._review_pending("waystone", "Review waystone")
        self.window.waystone.setText("87")
        self.window.normal.setText("123")
        token = self.window._overlay_review_token
        with patch.object(logger, "undo_last_action", side_effect=ValueError("No saved action to undo")), \
                patch.object(service.HOTKEY, "cancel_capture") as cancel:
            with self.assertRaisesRegex(ValueError, "No saved action"):
                self.window.undo_map()
        cancel.assert_not_called()
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.assertEqual(self.window.waystone.text(), "87")
        self.assertEqual(self.window.normal.text(), "123")
        self.assertEqual(self.window._overlay_review_token, token)
        for _ in range(20):
            if not logger.undo_action():
                break
            self.window.undo_map()
        self.assertFalse(logger.undo_action())
        self.window.waystone.setText("99")
        self.window.normal.setText("77")
        self.window.refresh()
        self.assertFalse(self.window.undo_button.isEnabled())
        self.assertIn("no saved action", self.window.undo_button.toolTip())
        self.window.undo_button.click()
        self.assertEqual(self.window.waystone.text(), "99")
        self.assertEqual(self.window.normal.text(), "77")


if __name__ == "__main__":
    unittest.main()
