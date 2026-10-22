"""Desktop saved-chain actions preserve stable parts and allow completed-chain correction."""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class ChainDiscardUITests(unittest.TestCase):
    """Exercise Expedition-only discard controls and the saved historical rune editor."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication needed to deliver button and selection signals."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open a clean isolated logger window with background polling paused."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-discard-ui-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close desktop workers and restore the previous database directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def save_parts(self, complete=False):
        """Populate three durable parts and optionally reselect their completed expedition."""
        logger.commit_chain_draft([{"rune1": "Death"}, {"rune1": "Power"}, {"rune1": "Time"}])
        if complete:
            logger.complete_chain(logger.scan_context())
            logger.save_settings({"expedition": 1})
        self.window.refresh()
        index = next(index for index in range(self.window.tabs.count())
                     if self.window.tabs.tabText(index) == "Expedition")
        self.window.tabs.setCurrentIndex(index)
        self.app.processEvents()

    def test_only_two_expedition_discard_buttons_dispatch_to_window_actions(self):
        """Keep exactly two Expedition discard actions and dispatch last-step removal without a selection."""
        self.save_parts()
        page = self.window.tabs.currentWidget()
        actions = (self.window.expedition_discard_chain_button,
                   self.window.expedition_discard_chain_step_button)
        for action in actions:
            self.assertTrue(page.isAncestorOf(action))
        labels = {action.text() for action in actions}
        self.assertEqual(labels, {"Discard chain", "Discard last chain step"})
        self.assertEqual({button for button in self.window.findChildren(QPushButton) if button.text() in labels},
                         set(actions))
        self.window.expedition_chain_table.clearSelection()
        self.window.expedition_chain_table.setCurrentCell(-1, -1)
        self.app.processEvents()
        self.assertEqual(self.window.expedition_chain_table.currentRow(), -1)
        for action, method in zip(actions, ("discard_chain", "discard_last_chain_step")):
            self.assertTrue(action.isEnabled())
            with patch.object(self.window, method) as handler:
                action.click()
                self.app.processEvents()
                handler.assert_called_once_with()

    def test_last_saved_step_is_deleted_despite_earlier_selection_then_ui_undo_restores_it(self):
        """Remove the final completed part regardless of selection and recover every saved part through Undo."""
        self.save_parts(complete=True)
        logger.save_detonated(7)
        self.window.refresh()
        table = self.window.expedition_chain_table
        table.selectRow(0)
        self.app.processEvents()
        button = self.window.expedition_discard_chain_step_button
        self.assertTrue(button.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            button.click()
        self.app.processEvents()
        self.assertEqual([step["step"] for step in logger.get_state()["chain"]], [1, 2])
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], ["1", "2"])
        self.assertTrue(logger.get_state()["chain_completed"])
        self.assertEqual(logger.get_state()["detonated"], 7)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertTrue(self.window.undo_button.isEnabled())
        self.window.undo_button.click()
        self.app.processEvents()
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], ["1", "2", "3"])
        self.assertEqual(logger.get_state()["detonated"], 7)

    def test_last_step_discard_handles_gaps_and_needs_no_selected_row(self):
        """Remove the greatest surviving step ID after an earlier arbitrary discard without renumbering."""
        self.save_parts()
        logger.discard_chain_step(2, logger.scan_context())
        self.window.refresh()
        table = self.window.expedition_chain_table
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], ["1", "3"])
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        self.app.processEvents()
        button = self.window.expedition_discard_chain_step_button
        self.assertTrue(button.isEnabled())

        button.click()
        self.app.processEvents()

        self.assertEqual(logger.get_state()["chain"], [{"step": 1, "rune1": "Death", "rune2": ""}])
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], ["1"])
        self.assertTrue(button.isEnabled())
        self.window.undo_button.click()
        self.app.processEvents()
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], ["1", "3"])

    def test_removing_only_completed_step_disables_both_actions_and_undo_restores_them(self):
        """Remove the sole completed part without changing actual counts and restore it with Undo."""
        logger.commit_chain("Death")
        logger.save_detonated(4)
        logger.complete_chain(logger.scan_context())
        logger.save_settings({"expedition": 1})
        self.window.refresh()
        self.window.tabs.setCurrentIndex(1)
        table = self.window.expedition_chain_table
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        self.app.processEvents()
        button = self.window.expedition_discard_chain_step_button
        self.assertTrue(button.isEnabled())

        button.click()
        self.app.processEvents()

        state = logger.get_state()
        self.assertEqual((state["chain"], state["chain_completed"], state["detonated"]), ([], False, 4))
        self.assertFalse(button.isEnabled())
        self.assertFalse(self.window.expedition_discard_chain_button.isEnabled())
        self.window.undo_button.click()
        self.app.processEvents()
        state = logger.get_state()
        self.assertEqual((state["chain"], state["chain_completed"], state["detonated"]),
                         ([{"step": 1, "rune1": "Death", "rune2": ""}], True, 4))
        self.assertTrue(button.isEnabled())
        self.assertTrue(self.window.expedition_discard_chain_button.isEnabled())

    def test_completed_chain_can_save_rune_corrections_and_discard_without_advancing(self):
        """Correct completed saved runes, then discard the chain while retaining its selected expedition."""
        self.save_parts(complete=True)
        table = self.window.expedition_chain_table
        self.assertTrue(table.cellWidget(0, 1).isEnabled())
        table.cellWidget(0, 1).setCurrentText("Rebirth")
        self.app.processEvents()
        self.assertTrue(self.window.expedition_save_chain_button.isEnabled())
        self.window.expedition_save_chain_button.click()
        self.app.processEvents()
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Rebirth")
        self.assertTrue(logger.get_state()["chain_completed"])
        self.assertTrue(self.window.expedition_discard_chain_button.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.expedition_discard_chain_button.click()
        self.app.processEvents()
        state = logger.get_state()
        self.assertEqual((state["chain"], state["chain_completed"], state["current_expedition_id"]),
                         ([], False, "M0001-E01"))
        self.assertEqual(table.rowCount(), 0)

    def test_pending_remnant_survives_discard_and_saves_with_original_capture(self):
        """Keep an independent opened-remnant review saveable after its captured chain is discarded."""
        self.save_parts()
        with logger._connect() as db:
            names = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:3]
        resolved = logger.resolve(*names, 26)
        result = {"mode": "opened", "status": "Review opened rewards.", "can_use": True,
                  "family": "Family 26", "candidates": [26],
                  "sockets": resolved["rows"][0]["sockets"],
                  "first_recipe": names[0], "next_recipe": names[1],
                  "opened_recipes": [{"recipe": name, "raw": name} for name in names],
                  **logger.scan_context()}
        self.window.show_result("opened", result)
        pending = logger.get_state()["ocr_pending"]
        before_fields = self.window.first_recipe.text(), self.window.next_recipe.text()

        self.window.discard_chain()

        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIs(self.window.results["opened"], result)
        self.assertEqual((self.window.first_recipe.text(), self.window.next_recipe.text()), before_fields)
        self.assertNotEqual(result["_capture_chain_revision"], logger.scan_context()["_capture_chain_revision"])
        logger.validate_remnant_context(result)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.approve_scan_button.click()
        self.app.processEvents()
        state = logger.get_state()
        self.assertIsNone(state["ocr_pending"])
        self.assertEqual((state["chain"], state["current_expedition_id"]), ([], pending["expedition_id"]))
        with logger._connect() as db:
            saved = db.execute("SELECT reference,expedition_id FROM commits WHERE kind='Remnant'").fetchone()
        self.assertEqual(tuple(saved), (pending["remnant_id"], pending["expedition_id"]))

    def test_last_discard_preserves_pending_currency_and_ritual_edits_until_approval(self):
        """Retain independent activity drafts and valid capture context while removing the latest saved part."""
        self.save_parts()
        logger.save_detonated(8)
        self.window.refresh()
        for kind in ("currency", "ritual"):
            with self.subTest(review=kind):
                context = logger.scan_context()
                expected_chain = logger.get_state()["chain"][:-1]
                if kind == "currency":
                    select(self.window.inventory_phase, "end")
                    self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb",
                        "quantity": 7}], "unknown": []}, live=False)
                    table = self.window.inventory_table
                else:
                    self.window._ritual_read({"items": [{"category": "Item", "name": "Chaos Orb",
                        "quantity": 7, "tribute": 100, "deferred": False}],
                        "raw_text": "Chaos Orb", "tribute_available": 1000}, live=False)
                    table = self.window.ritual_table
                table.item(0, 2).setText("9")
                if kind == "currency":
                    self.window.review_currency_row(table.cellWidget(0, 3), True)
                else:
                    # Ritual now shares Currency's local row approval; only
                    # final page approval persists this independent draft.
                    self.window.review_ritual_row(table.cellWidget(0, 6), True)
                    self.assertEqual(table.cellWidget(0, 6).property("reviewStatus"), "approved")
                summary = self.window.review_summary.text()
                self.window.tabs.setCurrentIndex(1)

                self.window.expedition_discard_chain_step_button.click()
                self.app.processEvents()

                self.assertEqual(logger.get_state()["chain"], expected_chain)
                self.assertEqual(logger.get_state()["detonated"], 8)
                self.assertEqual(self.window.pending_review_kind, kind)
                self.assertEqual(self.window.review_kind.property("scanKind"), kind)
                self.assertEqual(self.window.review_summary.text(), summary)
                self.assertEqual(table.item(0, 2).text(), "9")
                if kind == "ritual":
                    self.assertEqual(table.cellWidget(0, 6).property("reviewStatus"), "approved")
                self.assertEqual(self.window.tabs.currentIndex(), 1)
                self.assertTrue(self.window.approve_scan_button.isEnabled())
                logger.validate_scan_context(context)
                self.window.approve_scan_button.click()
                self.app.processEvents()
                self.assertIsNone(self.window.pending_review_kind)
                if kind == "currency":
                    self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 9})
                else:
                    with logger._connect() as db:
                        items = json.loads(db.execute("SELECT items_json FROM ritual_pages").fetchone()[0])
                    self.assertEqual(items[0]["quantity"], 9)

    def test_last_discard_cancels_waiting_approved_part_and_old_retry_cannot_restore_it(self):
        """Retire an explicitly approved but contended save before it can reappend after last-step discard."""
        self.save_parts()
        result = {"mode": "propagation", "runes": ["Death"], "positions": [1],
                  "selected_recipe": "Greater Jeweller's Orb", "can_use": True,
                  "status": "Review propagation.", **logger.scan_context()}
        self.window._propagation_read(result)
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(logger, "accept_propagation_part", side_effect=busy):
            self.window._accept_manual_propagation(result)
        pending = self.window._chain_save_pending
        self.assertIsNotNone(pending)

        self.window.discard_last_chain_step()
        self.app.processEvents()

        self.assertIsNone(self.window._chain_save_pending)
        before = logger.get_state()
        self.assertEqual([entry["step"] for entry in before["chain"]], [1, 2])
        with patch.object(logger, "accept_propagation_part") as accept:
            self.window._retry_approved_chain_save(pending)
            self.app.processEvents()
            accept.assert_not_called()
        with self.assertRaises(ValueError):
            self.window._propagation_read(result)
        self.assertEqual(logger.get_state(), before)
        self.assertIsNone(self.window.pending_review_kind)

    def test_stale_propagation_result_cannot_reopen_discarded_review_or_change_saved_state(self):
        """Reject a delayed propagation result before it can recreate review rows, chain parts or counts."""
        self.save_parts()
        result = {"mode": "propagation", "runes": ["Death"], "positions": [1],
                  "selected_recipe": "Greater Jeweller's Orb", "can_use": True,
                  "status": "Review propagation.", **logger.scan_context()}
        self.window._propagation_read(result)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.window.discard_chain()
        self.app.processEvents()
        before = logger.get_state()

        with self.assertRaises(ValueError):
            self.window._propagation_read(result)

        self.assertEqual(logger.get_state(), before)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertIsNone(self.window._propagation_reading)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 0)
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.approve_scan_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
