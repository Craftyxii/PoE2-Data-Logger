"""Qt propagation review checks for recipe selection, manual corrections, append/completion and context-owned persistence."""

import csv
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PropagationUITests(unittest.TestCase):
    """Check propagation recipe review, manual rune corrections, ordered chain persistence and retained remnant context."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger window with polling stopped and a reusable propagation screenshot."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.raw = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(self.raw, format="PNG")

    def tearDown(self):
        """Close the logger window, stop workers and restore the original data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def scan(self, runes, recipe="Medved's Saga", clear=True, context=None, draft=False):
        """Deliver a scan or stage an Expedition draft for its separate editor checks."""
        result = {"mode": "propagation", "runes": runes, "positions": list(range(1, len(runes) + 1)),
                  "selected_recipe": recipe, "can_use": clear, "status": "Propagation read",
                  **(logger.scan_context() if context is None else context)}
        if draft:
            with logger._connect() as db:
                entry = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (recipe,)).fetchone()
                original = [rune.strip() for rune in entry["combo"].split("+")]
                ordered = [*runes, *original[len(runes):]]
                db.execute("UPDATE recipes SET combo=? WHERE name=?", (" + ".join(ordered), recipe))
            result.update(can_use=False, choices=[{"selected_recipe": recipe,
                                                   "runes": runes, "positions": result["positions"],
                                                   "can_use": True}])
        self.window._propagation_read(result, self.raw.getvalue())
        if draft:
            result["can_use"] = True
            self.window._propagation_reading = result
            self.window._append_propagation(result, preserve_remnant_review=
                self.window.pending_review_kind in ("remnant", "seed"))
            self.window._clear_manual_propagation()

    def saved_parts(self):
        """Return the persisted rune pairs for the currently selected chain."""
        return [(part["rune1"], part["rune2"]) for part in logger.get_state()["chain"]]

    def draft(self):
        """Return displayed chain part numbers and rune names from the review table."""
        table = self.window.chain_review_table
        return [(table.item(row, 0).text(), table.item(row, 1).text()) for row in range(table.rowCount())]

    def review_state(self):
        """Snapshot review controls, images, recipes and chain draft/context for preservation assertions."""
        window = self.window
        return {
            "kind": window.pending_review_kind,
            "heading": window.review_kind.text(),
            "summary": window.review_summary.text(),
            "preview": window.preview.pixmap().toImage(),
            "preview_hidden": window.preview.isHidden(),
            "review_hidden": window.review_group.isHidden(),
            "remnant_hidden": window.remnant_log_group.isHidden(),
            "approve": (window.approve_scan_button.isHidden(), window.approve_scan_button.isEnabled()),
            "reject": (window.reject_scan_button.isHidden(), window.reject_scan_button.isEnabled()),
            "recipes": (window.first_recipe.text(), window.next_recipe.text(),
                        window.recipe_family.currentData()),
            "recipe_rows": [[window.recipe_table.item(row, column).text() for column in range(3)]
                            for row in range(window.recipe_table.rowCount())],
            "recipe_hidden": window.recipe_table.isHidden(),
            "results": copy.deepcopy(window.results),
            "images": dict(window.images),
            "resolved": copy.deepcopy(window.resolved),
            "runes": [(field.text(), field.property("chainPart"), field.property("chainRecipe"))
                      for field in window.rune_inputs],
            "chain_rows": self.draft(),
            "chain_drafts": copy.deepcopy(window._chain_drafts),
            "chain_context": copy.deepcopy(window._chain_context),
            "propagation": copy.deepcopy(window._propagation_reading),
            "overlay_token": window._overlay_review_token,
        }

    def assert_remnant_review_preserved(self, before):
        """Assert propagation displays its own controls while retaining the pending remnant data and corrections."""
        after = self.review_state()
        # Propagation has its own display while the captured remnant and typed
        # corrections remain pending. Compare the retained data independently
        # of the currently displayed heading, preview, and review controls.
        for key in ("kind", "recipes", "recipe_rows", "recipe_hidden", "results", "images",
                    "resolved", "chain_context", "propagation", "overlay_token"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.window.review_kind.text(), "Propagation scan")
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertTrue(self.window.review_group.isHidden())
        self.assertTrue(self.window.remnant_log_group.isHidden())
        self.assertTrue(self.window.approve_scan_button.isHidden())
        self.assertTrue(self.window.reject_scan_button.isHidden())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertFalse(self.window.manual_remnant_button.isHidden())
        self.assertTrue(self.window.manual_remnant_button.isEnabled())
        self.assertEqual(self.window.manual_remnant_button.text(), "Return to remnant review")

    def return_to_remnant_review(self, before):
        """Return to retained remnant review and assert its controls restore without altering propagation drafts."""
        pending = copy.deepcopy(logger.get_state()["ocr_pending"])
        propagation = self.review_state()
        self.assertEqual(self.window.manual_remnant_button.text(), "Return to remnant review")
        self.window.manual_remnant_button.click()
        after = self.review_state()
        for key in ("kind", "heading", "summary", "review_hidden",
                    "remnant_hidden", "approve", "reject", "recipes", "recipe_rows",
                    "recipe_hidden", "results", "images", "resolved"):
            self.assertEqual(after[key], before[key], key)
        image = before["images"]["opened" if before["kind"] == "remnant" else "seed"]
        if image:
            self.assertEqual(after["preview"], before["preview"])
            self.assertEqual(after["preview_hidden"], before["preview_hidden"])
        else:
            # Manual review has no captured remnant image; a prior propagation
            # screenshot must not become its preview when returning.
            self.assertTrue(after["preview"].isNull())
            self.assertTrue(after["preview_hidden"])
        for key in ("runes", "chain_rows", "chain_drafts", "chain_context"):
            self.assertEqual(after[key], propagation[key], key)
        self.assertEqual(after["overlay_token"], propagation["overlay_token"] + 1)
        self.assertEqual(self.window.review_kind.property("scanKind"), before["kind"])
        self.assertIsNone(self.window._held_remnant_review)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)

    def test_scans_append_in_scan_order_with_left_to_right_pairs(self):
        """Verify scans append in scan order with left to right pairs."""
        before = logger.get_state()["scan_commit_count"]
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.window._chain_steps(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Power"), ("Opulent", "")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.review_complete_chain_button.isHidden())
        self.assertFalse(self.window.expedition_commit_chain_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], before + 4)
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["next_remnant_id"], "R0001")
        self.assertIsNone(self.window.pending_review_kind)
        with self.assertRaises(ValueError):
            self.window.approve_review()
        self.assertEqual(self.saved_parts(), [("Death", "Power"), ("Opulent", "")])

    def test_review_completion_advances_only_after_confident_pairs_are_saved(self):
        """Verify review completion advances only after confident pairs are saved."""
        self.scan(["Rage", "Time"])
        self.scan(["Opulent"])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.review_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.window.header_expedition.currentData(), 2)
        self.assertEqual(self.window.expedition.currentData(), 2)
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Rage", "Time"), ("Opulent", "")])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.scan(["Death"])
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(self.window.header_expedition.currentData(), 3)
        self.assertEqual(self.window.expedition.currentData(), 3)

    def test_keyboard_correction_preserves_pair_in_review_and_export(self):
        """Verify keyboard correction preserves pair in review and export."""
        self.scan(["Death", "Power"], "Divine Orb x2", draft=True)
        self.scan(["Opulent"], "Greater Regal Orb x3", draft=True)
        field = self.window.rune_inputs[1]
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        self.assertEqual(field.text(), "")
        with self.assertRaisesRegex(ValueError, "Fill runes in order"):
            self.window.commit_chain()
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        QTest.keyClicks(field, "Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Time"), ("2", "Opulent")])
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "Divine Orb x2")
        self.window.expedition_commit_chain_button.click()
        rows = list(csv.reader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual([(row[26], row[27], row[32]) for row in rows[1:]],
                         [("Death", "Time", "M0001-E01"), ("Opulent", "", "M0001-E01")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_empty_paired_correction_survives_expedition_switch(self):
        """Verify empty paired correction survives expedition switch."""
        self.scan(["Death", "Power"], "Divine Orb x2", draft=True)
        self.window.rune_inputs[1].clear()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        QTest.keyClicks(self.window.rune_inputs[1], "Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Time")])
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "Divine Orb x2")

    def test_clearing_all_runes_discards_old_scan_grouping(self):
        """Verify clearing all runes discards old scan grouping."""
        self.scan(["Death", "Power"], "Divine Orb x2", draft=True)
        for field in self.window.rune_inputs:
            field.clear()
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("2", "Time")])
        self.assertEqual(self.window.chain_review_table.item(0, 2).text(), "")
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "")

    def test_new_scan_saves_independently_of_cleared_trailing_pair_member(self):
        """Keep the corrected manual part unchanged when confident OCR saves its own part."""
        self.scan(["Death", "Power"], "Divine Orb x2", draft=True)
        self.window.rune_inputs[1].clear()
        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assertEqual(self.draft(), [("1", "Death")])
        self.assertEqual(self.window.chain_review_table.item(0, 2).text(), "Divine Orb x2")
        self.assertEqual(self.window._chain_steps(), [{"rune1": "Death", "rune2": ""}])
        self.assertEqual(self.saved_parts(), [("Opulent", "")])
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.window.commit_chain()
        self.assertEqual(self.saved_parts(), [("Opulent", ""), ("Death", "")])
        self.assertEqual(self.draft(), [])
        self.assertEqual(logger.get_state()["detonated"], 2)

    def test_switching_expedition_preserves_separate_drafts(self):
        """Verify switching expedition preserves separate drafts."""
        self.scan(["Rage", "Time"], draft=True)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [])
        self.scan(["Death"], draft=True)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(self.draft(), [("1", "Rage"), ("1", "Time")])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [("1", "Death")])

    def test_stale_scan_does_not_append_after_completion_or_new_map(self):
        """Verify stale scan does not append after completion or new map."""
        context = logger.scan_context()
        self.scan(["Rage"])
        self.window.complete_chain()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.scan(["Time"], context=context)
        self.assertEqual(self.draft(), [])
        context = logger.scan_context()
        self.window.finish_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            self.scan(["Time"], context=context)
        self.assertEqual(self.draft(), [])

    def test_unclear_scan_stays_reviewable_without_adding_a_part(self):
        """Verify unclear scan stays reviewable without adding a part."""
        self.scan([], clear=False)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(self.draft(), [])
        self.window.reject_review()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def enter_manual(self, *runes):
        """Fill the manual propagation rune inputs and click Add."""
        if not self.window._propagation_manual_requested:
            self.window.start_manual_propagation()
        for index, field in enumerate(self.window.propagation_rune_inputs):
            field.setEditText(runes[index] if index < len(runes) else "")
        self.window.propagation_add_button.click()

    def test_recipe_approval_saves_selected_expedition_once_and_completion_advances(self):
        """Approve a held recipe directly, persist it through restart and advance only on completion."""
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.hold_recipes({"selected_recipe": "Swift Alloy", "runes": [], "can_use": False})
        self.select_row_slots(0, 1, 3)
        action = self.recipe_action(0)
        action.click()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rebirth", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(0)
        logger._READY = False
        logger.initialize()
        self.window.refresh()
        self.assertEqual(self.saved_parts(), [("Rebirth", "Rebirth")])
        self.assertTrue(self.window.review_complete_chain_button.isEnabled())
        self.window.review_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertTrue(logger.get_state()["chain_completed"])

    def test_recipe_approval_updates_expedition_saved_fields_and_header_completion(self):
        """Show approved runes in Expedition and complete the same chain through the global header."""
        self.window.show()
        self.window.tabs.setCurrentIndex(0)
        self.hold_recipes({"selected_recipe": "Swift Alloy", "runes": [], "can_use": False})
        self.select_row_slots(0, 1, 3)
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())
        self.recipe_action(0).click()
        self.assertEqual(self.saved_parts(), [("Rebirth", "Rebirth")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertTrue(self.window.header_complete_chain_button.isEnabled())
        self.window.tabs.setCurrentIndex(1)
        self.app.processEvents()
        table = self.window.expedition_chain_table
        self.assertTrue(table.isVisibleTo(self.window))
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertEqual(table.cellWidget(0, 1).currentText(), "Rebirth")
        self.assertEqual(table.cellWidget(0, 2).currentText(), "Rebirth")
        self.assertTrue(self.window.chain_note.isVisibleTo(self.window))
        self.assertIn("1 saved parts", self.window.chain_note.text())
        self.assertTrue(all(field.text() == "" for field in self.window.rune_inputs))
        self.assertFalse(self.window.expedition_commit_chain_button.isVisibleTo(self.window))
        self.assertTrue(self.window.header_complete_chain_button.isVisibleTo(self.window))
        self.window.header_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.saved_parts(), [])
        self.assertEqual(table.rowCount(), 0)
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertTrue(logger.get_state()["chain_completed"])
        self.assertEqual(self.saved_parts(), [("Rebirth", "Rebirth")])
        self.assertFalse(table.cellWidget(0, 1).isEnabled())
        self.assertFalse(table.cellWidget(0, 2).isEnabled())
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())

    def test_pending_currency_blocks_header_completion_until_review_is_rejected(self):
        """Keep a currency capture's expedition stable while its review remains pending."""
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.assertTrue(self.window.header_complete_chain_button.isEnabled())
        before = logger.get_state()["scan_commit_count"]
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 4}],
                                     "unknown": [{"slot": 2, "candidate": "Divine Orb"}],
                                     "_ocr_strictness": 100}, live=True, expected_map_id="M0001")
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())
        self.assertFalse(self.window.review_complete_chain_button.isEnabled())
        self.assertFalse(self.window.expedition_complete_chain_button.isEnabled())
        self.window.header_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertEqual(self.saved_parts(), [("Death", "Power")])
        self.window.reject_review()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertTrue(self.window.header_complete_chain_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.window.header_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)

    def test_normal_propagation_review_exposes_only_recipe_actions_and_completion(self):
        """Keep manual fallback and Expedition draft controls out of the OCR review."""
        self.held_recipe_list()
        self.window.show()
        self.app.processEvents()
        labels = {button.text() for button in self.window.chain_review_group.findChildren(QPushButton)
                  if button.isVisibleTo(self.window)}
        self.assertIn("Complete chain", labels)
        self.assertNotIn("Commit to chain", labels)
        self.assertNotIn("Edit chain", labels)
        self.assertNotIn("Add chain part", labels)
        self.assertTrue(self.window.propagation_manual_group.isHidden())
        self.assertTrue(all(field.isHidden() or not field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))

    def test_missing_family_scan_requires_explicit_manual_entry_and_saves_directly(self):
        """An unresolved scan offers a manual action without opening the separate form automatically."""
        self.window._propagation_read({"mode": "propagation", "can_use": False, "runes": [],
            "status": "Recipe not identified", **logger.scan_context()}, self.raw.getvalue())
        self.assertTrue(self.window.propagation_manual_group.isHidden())
        self.assertFalse(self.window.manual_propagation_button.isHidden())
        self.window.manual_propagation_button.click()
        self.assertFalse(self.window.propagation_manual_group.isHidden())
        self.enter_manual("Death", "Power")
        self.assertEqual(self.saved_parts(), [("Death", "Power")])
        self.assertEqual(self.draft(), [])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_maximum_strictness_holds_clear_propagation_for_recipe_approval(self):
        """A perfect OCR reading still needs manual approval at maximum captured strictness."""
        self.window._propagation_read({"mode": "propagation", "selected_recipe": "Swift Alloy",
            "runes": ["Rebirth"], "positions": [2], "can_use": True,
            "_ocr_strictness": 100, "status": "Clear marks", **logger.scan_context()}, self.raw.getvalue())
        self.assertEqual(self.saved_parts(), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertEqual(self.row_fields(0)[0].currentText(), "Rebirth")
        self.recipe_action(0).click()
        self.assertEqual(self.saved_parts(), [("Rebirth", "")])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_recipe_approval_saves_immediately_with_separate_expedition_draft(self):
        """An Expedition draft cannot turn recipe approval into another unsaved part."""
        self.window.rune_inputs[0].setText("Death")
        self.hold_recipes({"selected_recipe": "Swift Alloy", "runes": [], "can_use": False})
        self.select_row_slots(0, 2)
        self.recipe_action(0).click()
        self.assertEqual(self.saved_parts(), [("Soul", "")])
        self.assertEqual(self.window.rune_inputs[0].text(), "Death")
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertFalse(self.window.review_complete_chain_button.isEnabled())

    def test_failed_scan_manual_pair_counts_once_and_commits_in_order(self):
        """Verify failed scan manual pair counts once and commits in order."""
        self.scan([], clear=False)
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.enter_manual("Death", "Rebirth")
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.window.propagation_add_button.click()
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.enter_manual("Power")
        self.assertEqual(self.saved_parts(), [("Death", "Rebirth"), ("Power", "")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertTrue(any("Death" in row.values() and "Rebirth" in row.values() for row in rows))
        self.assertTrue(any("Power" in row.values() for row in rows))

    def test_manual_pair_preserves_pending_remnant_and_its_expedition(self):
        """Verify manual pair preserves pending remnant and its expedition."""
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        pending = logger.get_state()["ocr_pending"]
        self.scan([], clear=False)
        self.enter_manual("Rage", "Time")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.saved_parts(), [("Rage", "Time")])
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_manual_fields_clear_on_map_or_expedition_change(self):
        """Verify manual fields clear on map or expedition change."""
        self.scan([], clear=False)
        self.window.propagation_rune_inputs[0].setEditText("Death")
        stale = dict(self.window._manual_propagation_context)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.assertIsNone(self.window._manual_propagation_context)
        self.window._manual_propagation_context = stale
        self.window.propagation_rune_inputs[0].setEditText("Power")
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.window.add_manual_propagation()
        self.assertEqual(logger.get_state()["detonated"], None)
        self.window.finish_map()
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertEqual(self.window.propagation_rune_inputs[0].currentText(), "")

    def test_review_table_correction_preserves_pair_without_extra_detonation(self):
        """Verify review table correction preserves pair without extra detonation."""
        self.scan(["Death", "Power"], draft=True)
        self.window.chain_review_table.item(1, 1).setText("Rebirth")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.window._chain_steps(), [{"rune1": "Death", "rune2": "Rebirth"}])

    def test_manual_entry_is_accessible_without_a_scan_and_typo_cannot_be_committed(self):
        """Verify manual entry is accessible without a scan and typo cannot be committed."""
        self.window.manual_propagation_button.click()
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.window.propagation_rune_inputs[0].setEditText("Deth")
        before = logger.get_state()["scan_commit_count"]
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "Choose a rune name"):
            self.window.add_manual_propagation()
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.enter_manual("death", "Rebirth")
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Death")

    def test_new_map_commits_header_kills_to_finished_map_and_clears_next_map(self):
        """Verify new map commits header kills to finished map and clears next map."""
        from PySide6.QtWidgets import QPushButton
        for field, text in zip((self.window.normal, self.window.magic, self.window.rare, self.window.unique),
                               ("11", "22", "33", "44")):
            field.setText(text)
        actions = self.window.findChildren(QPushButton)
        self.assertFalse(any(action.text() == "Save counts" for action in actions))
        next(action for action in actions if action.text() == "+ New map").click()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        rows = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        finished = next(row for row in rows if row["Map ID"] == "M0001")
        self.assertEqual((finished["Normal Kills"], finished["Magic Kills"], finished["Rare Kills"],
                          finished["Unique Kills"], finished["Total Kills"]), ("11", "22", "33", "44", "110"))
        self.assertTrue(all(not field.text() for field in
                            (self.window.normal, self.window.magic, self.window.rare, self.window.unique)))

    def test_invalid_kill_count_keeps_current_map_and_typed_counts(self):
        """Verify invalid kill count keeps current map and typed counts."""
        self.window.normal.setText("10")
        self.window.unique.setText("not a number")
        before = logger.get_state()["scan_commit_count"]
        with self.assertRaises(ValueError):
            self.window.finish_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.assertEqual(self.window.normal.text(), "10")
        self.assertEqual(self.window.unique.text(), "not a number")

    def test_missing_arrow_recipe_approve_and_deny_choose_one_part(self):
        """Verify missing arrow recipe approve and deny choose one part."""
        for name, runes in (("Recipe A", ["Death", "Power"]), ("Recipe B", ["Rage", "Time"])):
            logger.save_recipe({"name": name, "sockets": 2, "combo": " + ".join(runes)})
        result = {"mode": "propagation", "can_use": False, "runes": [], "status": "Select a recipe",
                  "choices": [
                      {"selected_recipe": "Recipe A", "runes": ["Death", "Power"], "can_use": True},
                      {"selected_recipe": "Recipe B", "runes": ["Rage", "Time"], "can_use": True}],
                  **logger.scan_context()}
        self.window._propagation_read(result, self.raw.getvalue())
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 2)
        self.window.deny_propagation_recipe(0)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(0)
        self.window.approve_propagation_recipe(1)
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", "Time")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT reference FROM commits WHERE kind='Propagation'").fetchone()[0], "Recipe B")
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(1)
        self.assertEqual(logger.get_state()["detonated"], 1)

    def held_recipe_list(self):
        """Display three recipe candidates with unclear runes and one readable Swift Alloy selection."""
        self.window._propagation_read({
            "mode": "propagation", "can_use": False, "runes": [],
            "status": "Marked rune positions were not clear",
            "choices": [
                {"selected_recipe": "Mystic Alloy", "runes": [], "can_use": False},
                {"selected_recipe": "Prismatic Alloy", "runes": [], "can_use": False},
                {"selected_recipe": "Swift Alloy", "runes": ["Soul"], "positions": [3], "can_use": True}],
            **logger.scan_context()}, self.raw.getvalue())
        return self.window.propagation_recipe_table

    def recipe_action(self, row):
        """Return a recipe row approve button for click and enablement assertions."""
        return self.window.propagation_recipe_table.cellWidget(row, 2).findChild(
            QPushButton, "approvePropagationRecipe")

    def row_fields(self, row):
        """Return the recipe row rune-slot selectors."""
        return self.window._propagation_row_inputs[row]

    def select_row_slots(self, row, first, second=None):
        """Select optional zero-based rune slots in a recipe row."""
        for field, slot in zip(self.row_fields(row), (first, second)):
            field.setCurrentIndex(0 if slot is None else field.findData(slot))

    def hold_recipes(self, *choices):
        """Display the supplied propagation recipe choices as a pending review."""
        self.window._propagation_read({
            "mode": "propagation", "can_use": False, "runes": [],
            "status": "Check the marked runes", "choices": list(choices),
            **logger.scan_context()}, self.raw.getvalue())

    def test_unclear_recipe_inline_entry_approves_only_its_recipe(self):
        """Verify unclear recipe inline entry approves only its recipe."""
        table = self.held_recipe_list()
        self.window.resize(1400, 700)
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.manual_propagation_button.isHidden())
        self.window._review_controls("propagation")
        self.assertTrue(self.window.manual_propagation_button.isHidden())
        action = self.recipe_action(1)
        self.assertFalse(action.isEnabled())
        self.assertEqual(action.text(), "Approve")
        table.setCurrentCell(1, 0)
        self.app.processEvents()
        self.assertEqual(table.currentRow(), 1)
        first, second = self.row_fields(1)
        self.assertIsInstance(first, QComboBox)
        self.assertTrue(first.isVisibleTo(self.window))
        self.assertEqual(second.currentText(), "")
        self.assertTrue(all(not field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))
        self.assertFalse(self.window.propagation_add_button.isVisibleTo(self.window))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.select_row_slots(1, 3)
        self.assertEqual(action.text(), "Approve")
        self.assertTrue(action.isEnabled())
        action.click()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Opulent", "")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(1)
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.saved_parts(), [("Opulent", "")])
        self.assertTrue(self.window.review_complete_chain_button.isEnabled())

    def test_inline_runes_stay_with_their_recipe_when_selection_changes(self):
        """Verify inline runes stay with their recipe when selection changes."""
        table = self.held_recipe_list()
        table.setCurrentCell(1, 0)
        self.select_row_slots(1, 3)
        table.setCurrentCell(0, 0)
        self.assertEqual(self.row_fields(1)[0].currentText(), "Opulent")
        self.assertEqual(self.row_fields(0)[0].currentText(), "")
        self.assertEqual(self.recipe_action(0).text(), "Approve")
        self.assertFalse(self.recipe_action(0).isEnabled())
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.select_row_slots(0, 2)
        self.recipe_action(2).click()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Soul", "")])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_inline_recipe_entry_requires_first_rune_and_cannot_approve_denied_row(self):
        """Verify inline recipe entry requires first rune and cannot approve denied row."""
        table = self.held_recipe_list()
        self.assertEqual(table.currentRow(), -1)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "Select a waiting propagation recipe"):
            self.window.add_manual_propagation()
        table.setCurrentCell(1, 0)
        self.select_row_slots(1, None, 3)
        self.assertFalse(self.row_fields(1)[0].isEditable())
        self.assertEqual(self.row_fields(1)[0].findText("Opulent typo"), -1)
        self.assertFalse(self.recipe_action(1).isEnabled())
        self.recipe_action(1).click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.select_row_slots(1, 3)
        self.window.deny_propagation_recipe(1)
        self.assertTrue(all(not field.isEnabled() for field in self.row_fields(1)))
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(1)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_farrul_dropdowns_contain_only_each_recipe_in_database_order(self):
        """Verify Farrul's dropdowns contain only each recipe in database order."""
        names = ("Farrul's Rune of Grace", "Farrul's Rune of the Hunt")
        orders = (("Momentum", "Bloodletting", "Adaptive", "Time", "Life"),
                  ("Vision", "Bloodletting", "Bond", "Time", "Rage"))
        self.hold_recipes(*({"selected_recipe": name, "runes": [], "can_use": False}
                            for name in names))
        self.window.show()
        self.app.processEvents()
        table = self.window.propagation_recipe_table
        for row, expected in enumerate(orders):
            with self.subTest(recipe=names[row]):
                fields = self.row_fields(row)
                self.assertEqual(self.recipe_action(row).text(), "Approve")
                self.assertFalse(self.recipe_action(row).isEnabled())
                self.assertEqual(table.cellWidget(row, 1).findChildren(QComboBox), list(fields))
                for field in fields:
                    self.assertFalse(field.isEditable())
                    self.assertTrue(field.itemIcon(0).isNull())
                    self.assertTrue(all(not field.itemIcon(index).isNull()
                                        for index in range(1, field.count())))
                    self.assertEqual([field.itemText(index) for index in range(field.count())],
                                     ["", *expected])
                    self.assertEqual([field.itemData(index) for index in range(field.count())],
                                     [None, *range(5)])
                    self.assertEqual(field.currentText(), "")
                    self.assertEqual(field.findText("Power"), -1)
                self.assertEqual(fields[0].findText(orders[1 - row][0]), -1)
        self.assertTrue(all(not field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_inline_correction_is_saved_and_exported_with_its_recipe(self):
        """Verify inline correction is saved and exported with its recipe."""
        recipe = "Farrul's Rune of Grace"
        self.hold_recipes({"selected_recipe": recipe, "runes": ["Momentum", "Time"],
                           "positions": [1, 4], "can_use": True})
        self.assertEqual([field.currentText() for field in self.row_fields(0)], ["Momentum", "Time"])
        self.select_row_slots(0, 1, 4)
        self.recipe_action(0).click()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Bloodletting", "Life")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        self.assertEqual(self.saved_parts(), [("Bloodletting", "Life")])
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        chain_rows = [row for row in rows if row["Chain Step #"]]
        self.assertEqual([(row["Propagation Rune 1"], row["Propagation Rune 2"])
                          for row in chain_rows], [("Bloodletting", "Life")])
        with logger._connect() as db:
            commit = db.execute("SELECT reference FROM commits WHERE kind='Propagation'").fetchone()
        self.assertEqual(commit["reference"], recipe)
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertTrue(self.window.review_complete_chain_button.isEnabled())

    def test_duplicate_rune_names_keep_distinct_slots_and_require_left_to_right_order(self):
        """Verify duplicate rune names keep distinct slots and require left to right order."""
        self.hold_recipes({"selected_recipe": "Swift Alloy", "runes": [], "can_use": False})
        first, second = self.row_fields(0)
        self.assertEqual([first.itemText(index) for index in range(first.count())],
                         ["", "Adaptive", "Rebirth", "Soul", "Rebirth"])
        self.assertEqual([first.itemData(index) for index in range(first.count())],
                         [None, 0, 1, 2, 3])
        for slots in ((3, 1), (1, 1)):
            with self.subTest(slots=slots):
                self.select_row_slots(0, *slots)
                self.assertFalse(self.recipe_action(0).isEnabled())
                self.recipe_action(0).click()
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                self.assertEqual(self.draft(), [])
        self.select_row_slots(0, 1, 3)
        self.assertEqual((first.currentText(), second.currentText()), ("Rebirth", "Rebirth"))
        self.assertEqual((first.currentData(), second.currentData()), (1, 3))
        self.assertTrue(self.recipe_action(0).isEnabled())
        self.recipe_action(0).click()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rebirth", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_ocr_preselects_only_compatible_recipe_runes_and_keeps_unclear_rows_blank(self):
        """Verify OCR preselects only compatible recipe runes and keeps unclear rows blank."""
        self.hold_recipes(
            {"selected_recipe": "Farrul's Rune of Grace", "runes": ["Momentum", "Life"],
             "positions": [1, 5], "can_use": True},
            {"selected_recipe": "Farrul's Rune of the Hunt", "runes": ["Momentum"],
             "positions": [1], "can_use": True},
            {"selected_recipe": "Farrul's Rune of Grace", "runes": ["Time"],
             "positions": [4], "can_use": False})
        self.assertEqual([field.currentData() for field in self.row_fields(0)], [0, 4])
        for row in (1, 2):
            self.assertEqual([field.currentText() for field in self.row_fields(row)], ["", ""])
            self.assertEqual(self.recipe_action(row).text(), "Approve")
            self.assertFalse(self.recipe_action(row).isEnabled())
            self.recipe_action(row).click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(self.draft(), [])

    def test_unavailable_recipe_order_keeps_manual_fallback_outside_recipe_row(self):
        """Verify unavailable recipe order keeps manual fallback outside recipe row."""
        self.hold_recipes(
            {"selected_recipe": "Farrul's Rune of Grace", "runes": [], "can_use": False},
            {"selected_recipe": "Missing recipe", "runes": [], "can_use": False})
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.manual_propagation_button.isHidden())
        self.assertIsNone(self.row_fields(1))
        cell = self.window.propagation_recipe_table.cellWidget(1, 1)
        self.assertTrue(cell is None or not cell.findChildren(QComboBox))
        self.window.propagation_recipe_table.setCurrentCell(0, 0)
        self.assertTrue(all(not field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))
        self.window.propagation_recipe_table.setCurrentCell(1, 0)
        self.assertTrue(all(not field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))
        self.window.manual_propagation_button.click()
        self.assertTrue(all(field.isVisibleTo(self.window)
                            for field in self.window.propagation_rune_inputs))
        self.enter_manual("Death", "Rebirth")
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Rebirth")])

    def test_replacing_recipe_scan_closes_manual_popup_before_its_selection_can_transfer(self):
        """Retire an open fallback popup with its scan while allowing fresh input for the replacement."""
        def review(recipe):
            """Deliver an unclear unknown-recipe reading and process events before popup interaction."""
            self.window._propagation_read({
                "mode": "propagation", "can_use": False, "runes": [],
                "selected_recipe": recipe, "status": "Check the marked runes",
                "choices": [{"selected_recipe": recipe, "runes": [], "can_use": False}],
                **logger.scan_context()}, self.raw.getvalue())
            self.app.processEvents()

        review("Missing recipe A")
        self.window.manual_propagation_button.click()
        self.window.resize(1400, 700)
        self.window.show()
        field = self.window.propagation_rune_inputs[0]
        page = self.window.tabs.widget(0)
        page.ensureWidgetVisible(field, 12, 24)
        self.app.processEvents()
        arrow = QPoint(field.width() - 8, field.height() // 2)
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=arrow)
        self.app.processEvents()
        view = field.view()
        self.assertTrue(view.isVisible())
        target = field.model().index(field.findText("Adaptive"), 0)
        view.scrollTo(target)
        self.app.processEvents()
        position = view.visualRect(target).center()
        release = self.window.mapFromGlobal(view.viewport().mapToGlobal(position))
        QTest.mouseMove(view.viewport(), position)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=position)

        review("Missing recipe B")
        self.assertFalse(view.isVisible())
        self.assertIsNone(QApplication.activePopupWidget())
        QTest.mouseRelease(self.window, Qt.MouseButton.LeftButton, pos=release)
        self.app.processEvents()
        self.assertEqual(field.currentText(), "")
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.assertEqual(self.draft(), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

        self.window.manual_propagation_button.click()
        page.ensureWidgetVisible(field, 12, 24)
        self.app.processEvents()
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=arrow)
        QTest.keyClick(field, Qt.Key.Key_Home)
        QTest.keyClick(field, Qt.Key.Key_Down)
        QTest.keyClick(field, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(field.currentText(), "Adaptive")
        action = self.recipe_action(0)
        page.ensureWidgetVisible(action, 12, 24)
        self.app.processEvents()
        QTest.mouseClick(action, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Adaptive", "")])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_clear_rescan_replaces_unclear_review_and_enables_chain_completion(self):
        """Verify clear rescan replaces unclear review and enables chain completion."""
        self.scan(["Death"])
        self.scan(["Tidal"])
        table = self.held_recipe_list()
        table.setCurrentCell(1, 0)
        self.select_row_slots(1, 3)
        self.scan(["Opulent"], "Prismatic Alloy")
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.saved_parts(), [("Death", ""), ("Tidal", ""), ("Opulent", "")])
        self.assertEqual(logger.get_state()["detonated"], 3)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 0)
        self.assertTrue(self.window.review_complete_chain_button.isEnabled())
        self.assertTrue(self.window.expedition_complete_chain_button.isEnabled())
        self.window.review_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.saved_parts(), [])

    def test_real_scaled_capture_saves_correct_rune_and_supports_manual_correction(self):
        """Verify real scaled capture saves correct rune and supports manual correction."""
        from PIL import ImageDraw, ImageEnhance
        from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr

        path = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg"
        with Image.open(path) as opened:
            source = runehelper_ocr.default_frame(opened.convert("RGB"))
        for scale in (.72, .75):
            image = Image.new("RGB", source.size, (176, 161, 130))
            image.paste(source.resize((round(source.width * scale), round(source.height * scale)),
                                      Image.Resampling.LANCZOS))
            image = ImageEnhance.Brightness(image).enhance(1.2)
            y = round(167 * scale)
            ImageDraw.Draw(image).polygon([(1, y), (15, y - 10), (35, y), (15, y + 10)],
                                         fill=(242, 209, 124))
            reading = {**propagation_scan.scan_propagation(image), **logger.scan_context()}
            raw = io.BytesIO()
            image.save(raw, format="PNG")
            self.window._propagation_read(reading, raw.getvalue())
            if scale == .72:
                self.assertTrue(reading["can_use"])
                self.assertEqual(self.saved_parts(), [("Tidal", "")])
                self.assertEqual(logger.get_state()["detonated"], 1)
                self.assertIsNone(self.window.pending_review_kind)
            else:
                self.assertFalse(reading["can_use"])
                self.assertEqual(logger.get_state()["detonated"], 1)
                row = self.window.propagation_recipe_table.currentRow()
                self.assertEqual(self.window.propagation_recipe_table.item(row, 0).text(), "Regal Orb x3")
                self.assertFalse(self.recipe_action(row).isEnabled())
                self.select_row_slots(row, 2)
                self.assertTrue(self.recipe_action(row).isEnabled())
                self.recipe_action(row).click()
        self.assertEqual(self.saved_parts(), [("Tidal", ""), ("Tidal", "")])
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        chain_rows = [row for row in rows if row["Chain Step #"]]
        self.assertEqual([row["Propagation Rune 1"] for row in chain_rows], ["Tidal", "Tidal"])
        self.assertTrue(self.window.review_complete_chain_button.isEnabled())
        self.window.review_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_review_reject_remnant_button_keeps_inflight_propagation_result(self):
        """Verify review reject remnant button keeps inflight propagation result."""
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.pending_review_kind, "remnant")

        def read(image):
            """Reject pending remnant review during propagation reading and return a valid rune pair."""
            self.assertEqual(manager._active_capture_mode, "propagation")
            self.window.reject_scan_button.click()
            self.assertIsNone(logger.get_state()["ocr_pending"])
            return {"runes": ["Death", "Rebirth"], "positions": [1, 2],
                    "selected_recipe": "Medved's Saga", "can_use": True}

        manager = HotkeyManager(readers={"propagation": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager):
            manager.capture("propagation")
            event = manager.status()["latest"]
            self.assertIsNotNone(event)
            self.assertEqual(event["mode"], "propagation")
            self.window.poll()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_review_reject_propagation_button_keeps_inflight_opened_result(self):
        """Verify review reject propagation button keeps inflight opened result."""
        self.scan([], clear=False)
        self.assertEqual(self.window.pending_review_kind, "propagation")

        def read(path):
            """Reject pending propagation review during opened reading and return a reviewable remnant."""
            self.assertEqual(manager._active_capture_mode, "opened")
            self.window.reject_scan_button.click()
            self.assertIsNone(self.window.pending_review_kind)
            return {"status": "Reward needs review.", "can_use": False,
                    "first_recipe": None, "opened_recipes": []}

        manager = HotkeyManager(readers={"opened": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager):
            manager.capture("remnant")
            event = manager.status()["latest"]
            self.assertIsNotNone(event)
            self.assertEqual(event["mode"], "opened")
            self.window.poll()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], "R0001")
        self.assertEqual(self.draft(), [])

    def test_starting_remnant_file_review_keeps_inflight_propagation_result(self):
        """Verify starting remnant file review keeps inflight propagation result."""
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(self.raw.getvalue())
        self.window.mode = "opened"
        jobs = []

        def read(image):
            """Start a queued remnant file scan during propagation reading and return a valid rune."""
            self.assertEqual(manager._active_capture_mode, "propagation")
            self.window.scan_file()
            self.assertEqual(self.window.pending_review_kind, "remnant")
            self.assertIsNotNone(self.window._remnant_reading)
            return {"runes": ["Rage"], "positions": [1],
                    "selected_recipe": "Medved's Saga", "can_use": True}

        manager = HotkeyManager(readers={"propagation": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager), patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(path), "Images")), patch.object(
                self.window, "_submit", side_effect=lambda label, work, done: jobs.append((work, done))):
            manager.capture("propagation")
            self.assertIsNotNone(manager.status()["latest"])
            pending_capture = self.window._remnant_reading
            self.window.poll()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", "")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIs(self.window._remnant_reading, pending_capture)

    def test_propagation_restores_hidden_hud_with_pending_remnant_review(self):
        """Verify propagation restores hidden HUD with pending remnant review."""
        self.window._overlay_enabled = True
        for clear in (True, False):
            with self.subTest(clear=clear):
                self.window.manual_remnant_button.click()
                self.window.first_recipe.setText("A reward being corrected")
                self.app.processEvents()
                self.assertTrue(self.window.isVisible())
                # Hotkey capture hides the HUD before freezing its screenshot.
                self.window.hide()
                self.app.processEvents()
                self.assertFalse(self.window.isVisible())
                self.scan(["Rage"] if clear else [], clear=clear)
                self.app.processEvents()
                self.assertTrue(self.window.isVisible())
                self.assertEqual(self.window.pending_review_kind, "remnant")
                self.assertEqual(self.window.first_recipe.text(), "A reward being corrected")

    def test_pending_opened_remnant_allows_saved_propagation_without_losing_previous_parts(self):
        """Verify pending opened remnant allows saved propagation without losing previous parts."""
        self.scan(["Death", "Power"], "Divine Orb x2")
        result = {"mode": "opened", "status": "The first reward needs review.",
                  "can_use": False, "first_recipe": None,
                  "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}],
                  **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.assertEqual(pending["remnant_id"], "R0001")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.draft(), [])
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.assertFalse(self.window.remnant_log_group.isHidden())
        self.assertFalse(self.window.review_group.isHidden())
        self.assertFalse(self.window.preview.isHidden())
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        self.assertGreater(self.window.recipe_table.rowCount(), 0)
        before = self.review_state()

        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 4)
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Power"), ("Opulent", "")])
        self.return_to_remnant_review(before)
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.draft(), [])
        self.scan(["Rage"], "Greater Regal Orb x3")
        self.return_to_remnant_review(before)
        self.window.reject_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", "")])
        self.window.review_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Death", "Power"), ("Opulent", "")])

    def test_manual_remnant_keeps_original_expedition_while_propagation_advances(self):
        """Verify manual remnant keeps original expedition while propagation advances."""
        self.scan(["Rage", "Time"])
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        self.window.next_recipe.setText("Next reward being corrected")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.draft(), [])
        pending = logger.get_state()["ocr_pending"]
        self.assertEqual(pending["expedition_id"], "M0001-E01")
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        before = self.review_state()

        self.scan(["Death"])
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 4)
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", "Time"), ("Death", "")])
        self.return_to_remnant_review(before)
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.window.approve_review()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT expedition_id FROM commits WHERE kind='Remnant'").fetchone()[0],
                             "M0001-E01")

    def test_pending_seed_review_without_database_token_allows_propagation(self):
        """Verify pending seed review without database token allows propagation."""
        self.scan(["Rage"])
        self.window._review_pending("seed", "Visible remnant still needs review.", False)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        before = self.review_state()

        self.scan(["Time"])
        self.assert_remnant_review_preserved(before)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", ""), ("Time", "")])
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.return_to_remnant_review(before)

    def test_same_chain_orphan_database_remnant_token_is_preserved_during_propagation(self):
        """Verify same chain orphan database remnant token is preserved during propagation."""
        self.scan(["Death", "Power"])
        pending = logger.assign_ocr_id("opened")
        self.assertIsNone(self.window.pending_review_kind)
        before = self.review_state()

        self.scan(["Opulent"])
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._held_remnant_review)
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertIn("Opulent", self.window.review_summary.text())
        for key in ("recipes", "recipe_rows", "results", "images", "resolved"):
            self.assertEqual(self.review_state()[key], before[key], key)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 4)
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Death", "Power"), ("Opulent", "")])
        # There was no displayed remnant to restore. The control opens review
        # of the retained database token without allocating another remnant.
        self.assertFalse(self.window.manual_remnant_button.isHidden())
        self.assertTrue(self.window.manual_remnant_button.isEnabled())
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.review_kind.property("scanKind"), "remnant")
        self.assertFalse(self.window.remnant_log_group.isHidden())
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 4)
        self.assertEqual(self.saved_parts(), [("Death", "Power"), ("Opulent", "")])

    def test_unclear_propagation_keeps_pending_remnant_edits_and_reports_problem(self):
        """Verify unclear propagation keeps pending remnant edits and reports problem."""
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("A reward being corrected")
        before = self.review_state()
        pending = copy.deepcopy(logger.get_state()["ocr_pending"])
        self.scan([], clear=False)
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertIsNotNone(self.window._manual_propagation_context)
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.propagation_rune_inputs[0].isHidden())
        self.assertIn("pending remnant remains open", self.window.chain_review_status.text())
        self.assertIn("pending remnant remains open", self.window.statusBar().currentMessage())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.return_to_remnant_review(before)

    def test_pending_opened_remnant_can_still_save_after_propagation(self):
        """Verify pending opened remnant can still save after propagation."""
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.scan(["Rage", "Time"])
        self.window.approve_review()
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.draft(), [])
        self.assertTrue(self.window.chain_review_group.isHidden())
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertTrue(any(pending["remnant_id"] in row.values() for row in rows))
        with self.assertRaisesRegex(ValueError, "Fill runes in order"):
            self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")

    def test_pending_opened_review_survives_chain_commit_and_next_chain_scan(self):
        """Verify pending opened review survives chain commit and next chain scan."""
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.window.first_recipe.setText("Farrul's Rune of Grace")
        self.window.next_recipe.setText("Farrul's Rune of the Hunt")
        self.window.resolve_recipe(preserve_review=True)
        self.scan(["Death", "Rebirth"])
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), "Farrul's Rune of Grace")
        self.scan(["Power"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.window.approve_review()
        self.assertEqual(self.draft(), [])
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertIsNone(logger.get_state()["ocr_pending"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT expedition_id FROM commits WHERE kind='Remnant'").fetchone()[0],
                             "M0001-E01")
            self.assertEqual([tuple(row) for row in db.execute(
                "SELECT expedition_id,detonated FROM expeditions ORDER BY expedition_id")],
                [("M0001-E01", 1), ("M0001-E02", 1)])

    def test_delayed_remnant_result_arrives_after_chain_advance(self):
        """Verify delayed remnant result arrives after chain advance."""
        capture = object()
        context = logger.scan_context()
        self.window._remnant_reading = capture
        self.window._review_pending("remnant", "Reading remnant image…", False)
        self.scan(["Rage"])
        self.window.complete_chain()
        self.assertIs(self.window._remnant_reading, capture)
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], "_target_map_id": "M0001", **context}
        self.window._scan_done("opened", result, self.raw.getvalue(), capture)
        self.assertEqual(result["expedition_id"], "M0001-E01")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_remnant_file_scan_can_finish_while_service_chain_advances(self):
        """Verify remnant file scan can finish while service chain advances."""
        context = logger.scan_context()

        def slow_read(path):
            """Advance the chain during opened-remnant reading before returning a reviewable result."""
            logger.commit_chain_draft([{"rune1": "Rage"}], context)
            logger.complete_chain(context)
            return {"status": "Reward needs review.", "opened_recipes": [], "first_recipe": None}

        import base64
        with patch.object(service, "scan_opened", side_effect=slow_read):
            result = service.dispatch("/api/scan?mode=opened", {
                "image": base64.b64encode(self.raw.getvalue()).decode("ascii"),
                "map_id": "M0001", "scan_context": context})
        self.assertEqual(result["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_manual_runes_and_scans_keep_existing_order(self):
        """Persist OCR separately and append valid manual drafts in their original rune order."""
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Power")
        self.scan(["Opulent"])
        self.assertEqual(self.draft(), [("1", "Death"), ("2", "Power")])
        self.assertEqual(self.saved_parts(), [("Opulent", "")])
        self.window.rune_inputs[0].clear()
        with self.assertRaisesRegex(ValueError, "Fill runes in order"):
            self.window.commit_chain()
        self.assertEqual(self.saved_parts(), [("Opulent", "")])
        self.window.rune_inputs[0].setText("Death")
        self.window.commit_chain()
        self.assertEqual(self.saved_parts(), [("Opulent", ""), ("Death", ""), ("Power", "")])
        self.assertEqual(self.draft(), [])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")

    def test_poll_routes_propagation_without_remnant_assignment(self):
        """Verify poll routes propagation without remnant assignment."""
        event = {"id": 101, "mode": "propagation", "error": None,
                 "result": {"runes": ["Rage"], "selected_recipe": "Medved's Saga",
                            "can_use": True, **logger.scan_context()}}
        status = service.HOTKEY.status()
        with patch.object(service.HOTKEY, "status", return_value={**status, "latest": event}), \
             patch.object(service.HOTKEY, "image", return_value=self.raw.getvalue()), \
             patch.object(logger, "assign_ocr_id", side_effect=AssertionError("remnant path")):
            self.window.poll()
            self.window.poll()
        self.assertEqual(self.draft(), [])
        self.assertEqual(self.saved_parts(), [("Rage", "")])
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)

    def test_propagation_binding_is_in_settings_and_ocr_indicator(self):
        """Verify propagation binding is in settings and OCR indicator."""
        self.assertIn("propagation", self.window.direct_hotkey_labels)
        old_combo, old_combos = service.HOTKEY.combo, dict(service.HOTKEY.combos)
        try:
            with patch.object(service.HOTKEY, "supported", True), \
                 patch.object(service.HOTKEY, "_register"), patch.object(service.HOTKEY, "_unregister"):
                service.HOTKEY.configure_for("propagation", "Ctrl+Shift+F8")
                self.window.refresh_hotkey()
                self.assertEqual(self.window.direct_hotkey_labels["propagation"].text(), "Ctrl+Shift+F8")
                self.window.clear_hotkey("propagation")
                self.assertEqual(self.window.direct_hotkey_labels["propagation"].text(), "Off")
        finally:
            service.HOTKEY.combo, service.HOTKEY.combos = old_combo, old_combos


if __name__ == "__main__":
    unittest.main()
