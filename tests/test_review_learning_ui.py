"""Qt review checks learning only approved named items and preserving atomic validation on corrected inventory/Ritual rows."""

import csv
import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ReviewLearningUITests(unittest.TestCase):
    """Check reviewed inventory and Ritual rows teach only committed approved labels."""
    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application for review-learning window tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated window with queued OCR callbacks and synthetic captured artwork."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-review-learning-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        pixels = np.random.default_rng(71).integers(25, 230, size=(54, 54, 3), dtype=np.uint8)
        self.tile = Image.fromarray(pixels)
        self.grid = Image.new("RGB", (648, 270), "black")
        self.grid.paste(self.tile, (0, 0))
        self.grid.info["poe2_inventory_aligned"] = True
        self.page = Image.new("RGB", (648, 600), "black")
        self.page.paste(self.tile, (50, 50))

    def tearDown(self):
        """Close the window and workers, then restore and remove temporary data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def inventory(self, unknown=True):
        """Deliver a controlled known or unknown inventory result for a captured grid."""
        self.window._inventory_captured(self.grid, live=False)
        self.jobs[-1][1]({"items": [] if unknown else [{"slot": 1, "name": "Chaos Orb", "quantity": 1}],
                          "unknown": [{"slot": 1, "candidate": ""}] if unknown else []})

    def ritual(self, items):
        """Deliver captured Ritual rewards with controlled available tribute and rerolls."""
        self.window._ritual_captured(self.page, live=False)
        self.jobs[-1][1]({"items": items, "raw_text": "Evidence", "unmatched": [],
                          "tribute_available": 18956, "rerolls_remaining": 0})

    def unknown_reward(self, **changes):
        """Construct an unresolved Ritual reward with configurable review fields."""
        return {"name": "", "category": "Item", "category_verified": True,
                "quantity": 1, "tribute": None, "deferred": False,
                "grid_slots": [1], "box": (50, 50, 104, 104), "unresolved": True,
                "source": "Grid slot 1: unidentified artwork", **changes}

    def export_rows(self):
        """Parse all exported scan-history records as dictionaries."""
        return list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))

    def approve_currency_row(self, row=0):
        """Click a currency row's approval control and verify its review state."""
        controls = self.window.inventory_table.cellWidget(row, 3)
        controls.findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")

    def test_currency_new_name_approval_learns_and_exports_the_captured_item(self):
        """Verify committed approval stores a new token's quantity, captured icon and export column."""
        self.inventory()
        self.window.inventory_table.item(0, 1).setText("My newly identified token")
        self.window.inventory_table.item(0, 2).setText("5")
        self.assertNotIn("My newly identified token", logger.inventory_names())
        self.assertEqual(logger.review_icons(), [])
        self.approve_currency_row()
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"My newly identified token": 5})
        self.assertEqual([(ref["name"], ref["kind"]) for ref in logger.review_icons()],
                         [("My newly identified token", "currency")])
        currency = [row for row in self.export_rows() if row["Currency"] == "My newly identified token"]
        self.assertEqual(currency[-1]["Currency: My newly identified token"], "5")
        self.assertEqual(self.window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "approved")

    def test_unnamed_inventory_rows_are_rejected_without_count_or_name_validation(self):
        """Verify blank inventory labels are rejected without validating counts or teaching artwork."""
        self.inventory()
        self.window.inventory_table.item(0, 2).setText("not a count")
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual(self.window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "rejected")
        self.assertFalse(any(header in ("Currency: ", "Item: ", "Omen: ")
                             for header in self.export_rows()[0]))

    def test_explicitly_rejected_named_inventory_row_never_teaches(self):
        """Verify explicitly rejected named rows add neither catalog labels nor learned icons."""
        self.inventory()
        self.window.inventory_table.item(0, 1).setText("A rejected token")
        self.window.inventory_table.item(0, 2).setText("5")
        self.window._set_currency_review(self.window.inventory_table.cellWidget(0, 3), "rejected")
        self.window.approve_review()
        self.assertNotIn("A rejected token", logger.inventory_names())
        self.assertEqual(logger.review_icons(), [])

    def test_failed_row_count_validation_has_no_partial_learning_then_corrected_save_works(self):
        """Verify invalid row counts teach nothing and corrected approval can later save."""
        self.inventory()
        self.window.inventory_table.item(0, 1).setText("A validated token")
        self.window.inventory_table.item(0, 2).setText("-1")
        controls = self.window.inventory_table.cellWidget(0, 3)
        with self.assertRaises(ValueError):
            self.window.review_currency_row(controls, True)
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.assertNotIn("A validated token", logger.inventory_names())
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.window.inventory_table.item(0, 2).setText("4")
        self.approve_currency_row()
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"A validated token": 4})

    def test_naming_a_row_after_unnamed_row_approval_recovers_rejection(self):
        """Verify naming a previously blank rejected row restores its pending decision."""
        self.inventory()
        controls = self.window.inventory_table.cellWidget(0, 3)
        self.window.review_currency_row(controls, True)
        self.assertEqual(controls.property("reviewStatus"), "rejected")
        self.window.add_inventory_row({"name": "Second reviewed token", "quantity": 2})
        self.window.inventory_table.item(0, 1).setText("First reviewed token")
        self.window.inventory_table.item(0, 2).setText("1")
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.approve_currency_row()
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"],
                         {"First reviewed token": 1, "Second reviewed token": 2})

    def test_individual_currency_row_approval_waits_for_bottom_commit_before_learning(self):
        """Verify row approval defers storage and learning until whole-scan commitment."""
        self.inventory()
        self.window.inventory_table.item(0, 1).setText("Individually reviewed token")
        self.window.inventory_table.item(0, 2).setText("2")
        controls = self.window.inventory_table.cellWidget(0, 3)
        controls.findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(logger.review_icons(), [])
        self.assertNotIn("Individually reviewed token", logger.currency_names())
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Individually reviewed token": 2})
        self.assertEqual([reference["name"] for reference in logger.review_icons()],
                         ["Individually reviewed token"])

    def test_rejecting_last_pending_row_does_not_submit_other_approved_rows(self):
        """Verify rejecting the final pending row does not commit other approved rows."""
        self.window._inventory_captured(self.grid, live=False)
        self.jobs[-1][1]({"items": [{"slot": 2, "name": "Chaos Orb", "quantity": 7}],
                          "unknown": [{"slot": 1}]})
        controls = self.window.inventory_table.cellWidget(1, 3)
        controls.findChild(QPushButton, "rejectCurrency").click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_rejecting_every_row_keeps_whole_scan_decision_pending(self):
        """Verify rejecting all rows still leaves the whole scan pending until rejection."""
        self.inventory()
        self.window.inventory_table.cellWidget(0, 3).findChild(QPushButton, "rejectCurrency").click()
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertTrue(self.window.reject_scan_button.isEnabled())
        self.window.reject_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_editing_a_matched_row_requires_a_fresh_decision(self):
        """Verify edits to matched quantities or names reset the row's approval decision."""
        self.inventory(unknown=False)
        controls = self.window.inventory_table.cellWidget(0, 3)
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.window.inventory_table.item(0, 2).setText("4")
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.assertTrue(controls.findChild(QPushButton, "approveCurrency").isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.approve_currency_row()
        self.window.inventory_table.item(0, 1).setText("Renamed matched token")
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.approve_currency_row()
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Renamed matched token": 4})

    def test_final_approval_rejects_edited_match_without_fresh_row_approval(self):
        """Verify whole-scan approval rejects edited matches lacking renewed row approval."""
        self.inventory(unknown=False)
        table = self.window.inventory_table
        controls = table.cellWidget(0, 3)
        self.assertEqual(controls.property("reviewStatus"), "approved")
        table.item(0, 1).setText("Unconfirmed renamed token")
        table.item(0, 2).setText("4")
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.window.approve_scan_button.click()
        self.assertEqual(controls.property("reviewStatus"), "rejected")
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertNotIn("Unconfirmed renamed token", logger.inventory_names())
        self.assertEqual(logger.review_icons(), [])

    def test_final_approval_and_direct_save_reject_pending_invalid_rows_without_learning(self):
        """Verify both save paths omit pending invalid rows and commit only approved items."""
        for direct_save in (False, True):
            with self.subTest(direct_save=direct_save):
                self.window._inventory_captured(self.grid, live=False)
                self.jobs[-1][1]({"items": [
                    {"slot": 9, "name": "Chaos Orb", "quantity": 7},
                    {"slot": 4, "name": "Unconfirmed count token", "quantity": "bad count",
                     "count_needs_review": True}],
                    "unknown": [{"slot": 1, "candidate": "Regal Orb"}, {"slot": 2}]})
                table = self.window.inventory_table
                table.item(1, 1).setText("Invalid\nreview label")
                table.item(1, 2).setText("not a count")
                table.item(2, 1).setText("Unconfirmed named token")
                table.item(2, 2).setText("5")
                table.item(3, 2).setText("not a count")
                self.assertEqual([table.cellWidget(row, 3).property("reviewStatus")
                                  for row in range(4)], ["approved", "pending", "pending", "pending"])
                if direct_save:
                    self.window.save_inventory()
                else:
                    self.window.approve_scan_button.click()
                self.assertIsNone(self.window.pending_review_kind)
                self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
                self.assertEqual([table.cellWidget(row, 3).property("reviewStatus")
                                  for row in range(4)], ["approved", "rejected", "rejected", "rejected"])
                self.assertNotIn("Unconfirmed named token", logger.inventory_names())
                self.assertNotIn("Unconfirmed count token", logger.inventory_names())
                self.assertEqual(logger.review_icons(), [])
                self.assertNotIn("Currency: Unconfirmed named token", self.export_rows()[0])

    def test_approved_reordered_candidate_learns_its_original_slot_only(self):
        """Verify reordered candidate rows learn artwork from their original captured slot."""
        slot = 14
        self.grid.paste(self.tile, (54, 54))
        other = Image.fromarray(np.random.default_rng(99).integers(
            25, 230, (54, 54, 3), dtype=np.uint8))
        self.grid.paste(other, (0, 0))
        self.window._inventory_captured(self.grid, live=False)
        self.jobs[-1][1]({"items": [
            {"slot": 8, "name": "Chaos Orb", "quantity": 7}],
            "unknown": [{"slot": 1}, {"slot": slot, "candidate": "Regal Orb",
                                          "score": .68, "reason": "shared icon; check tier"}]})
        table = self.window.inventory_table
        self.assertEqual([table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(3)],
                         [8, slot, 1])
        table.item(1, 1).setText("Approved original-slot token")
        table.item(1, 2).setText("3")
        table.item(2, 1).setText("Unconfirmed other-slot token")
        table.item(2, 2).setText("2")
        self.approve_currency_row(1)
        self.assertEqual(logger.review_icons(), [])
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"],
                         {"Chaos Orb": 7, "Approved original-slot token": 3})
        references = logger.review_icons()
        self.assertEqual([reference["name"] for reference in references], ["Approved original-slot token"])
        expected = item_ocr.inventory_cell(self.grid, slot).resize((96, 96), Image.Resampling.LANCZOS)
        with Image.open(io.BytesIO(references[0]["image"])) as learned:
            self.assertEqual(learned.convert("RGB").tobytes(), expected.tobytes())
        self.assertEqual(table.cellWidget(2, 3).property("reviewStatus"), "rejected")
        self.assertNotIn("Unconfirmed other-slot token", logger.inventory_names())

    def test_uncertain_summary_includes_named_count_uncertainty_and_unknown_icons(self):
        """Verify the review summary counts uncertain named quantities and unknown artwork."""
        self.window._inventory_captured(self.grid, live=False)
        self.jobs[-1][1]({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7,
                                    "count_needs_review": True}], "unknown": [{"slot": 2}]})
        self.assertIn("2 uncertain slots", self.window.review_summary.text())
        self.assertIn("2 uncertain slots", self.window.inventory_status.text())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_currency_review_hides_unrelated_propagation_controls_before_and_after_commit(self):
        """Verify currency review and saved status hide propagation controls until explicitly opened."""
        self.inventory(unknown=False)
        self.assertTrue(self.window.manual_propagation_button.isHidden())
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.window.approve_scan_button.click()
        self.assertTrue(self.window.manual_propagation_button.isHidden())
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertIn("Currency inventory saved", self.window.review_summary.text())
        with logger._connect() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT kind FROM commits")], ["Currency"])
        self.window.expedition_manual_propagation_button.click()
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertEqual(self.window.review_kind.text(), "Propagation scan")
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertTrue(self.window.currency_review_group.isHidden())

    def test_other_activity_reviews_hide_manual_propagation_shortcut(self):
        """Keep manual propagation entry in its own review instead of other scan forms."""
        self.window._review_pending("propagation", "Review this activity")
        self.assertFalse(self.window.manual_propagation_button.isHidden())
        for kind in ("tablet", "waystone", "ritual", "remnant", "seed"):
            with self.subTest(kind=kind):
                self.window._review_pending(kind, "Review this activity")
                self.assertTrue(self.window.manual_propagation_button.isHidden())

    def test_unknown_ritual_name_is_learned_and_header_values_saved_in_export(self):
        """Verify a named Ritual reward teaches its Omen icon and exports header values."""
        self.ritual([self.unknown_reward(category="", category_verified=False)])
        table = self.window.ritual_table
        table.item(0, 1).setText("Omen of a custom outcome")
        self.assertEqual(table.item(0, 0).text(), "Omen")
        self.window.approve_review()
        name = "Omen of a custom outcome"
        self.assertIn(name, logger.ritual_names())
        self.assertIn(name, logger.currency_names())
        self.assertEqual([(ref["name"], ref["kind"]) for ref in logger.review_icons()], [(name, "omen")])
        exported = [row for row in self.export_rows() if row["Ritual Page"] == "1"][-1]
        self.assertEqual(exported["Omen: " + name], "1")
        self.assertEqual(exported["Ritual Tribute Available"], "18956")
        self.assertEqual(exported["Ritual Rerolls Remaining"], "0")

    def test_unnamed_ritual_rows_are_omitted_and_never_marked_saved(self):
        """Verify blank Ritual rewards are omitted, rejected and never learned."""
        known = {"category": "Item", "name": "Chaos Orb", "quantity": 2,
                 "tribute": None, "deferred": False, "source": "verified"}
        self.ritual([known, self.unknown_reward(quantity=None, deferred=None)])
        self.window.approve_review()
        items = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual([(item["name"], item["quantity"]) for item in items], [("Chaos Orb", 2)])
        self.assertEqual(logger.review_icons(), [])
        self.assertIn("Rejected", self.window.ritual_table.item(1, 6).text())
        self.assertFalse(self.window.ritual_table.item(1, 0).data(Qt.ItemDataRole.UserRole + 1))

    def test_all_unnamed_ritual_rewards_reject_the_scan_without_a_commit(self):
        """Verify an entirely unnamed Ritual page is rejected without creating a commit."""
        self.ritual([self.unknown_reward()])
        self.window.approve_review()
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertIsNone(self.window.pending_review_kind)

    def test_ritual_blank_rejection_recovers_when_named_after_another_row_validation_error(self):
        """Verify a blank Ritual row can recover after another row's validation failure."""
        known = {"category": "Item", "name": "Chaos Orb", "quantity": 0,
                 "tribute": None, "deferred": False, "source": "manual"}
        self.ritual([self.unknown_reward(), known])
        with self.assertRaises(ValueError):
            self.window.approve_review()
        self.assertIn("Rejected", self.window.ritual_table.item(0, 6).text())
        self.window.ritual_table.item(0, 1).setText("A recovered Ritual token")
        self.assertNotIn("Rejected", self.window.ritual_table.item(0, 6).text())
        self.window.ritual_table.item(1, 2).setText("2")
        self.window.approve_review()
        self.assertEqual([item["name"] for item in logger.ritual_pages_for_map("M0001")[0]["items"]],
                         ["A recovered Ritual token", "Chaos Orb"])

    def test_captureless_manual_name_registers_without_reusing_an_older_icon(self):
        """Verify manual inventory labels register without learning stale captured artwork."""
        self.inventory(unknown=False)
        self.window.reject_review()
        self.window._inventory_read({"items": [{"slot": 1, "name": "Manual custom token", "quantity": 3}],
                                     "unknown": []}, live=False)
        self.window.approve_review()
        self.assertIn("Manual custom token", logger.currency_names())
        self.assertEqual(logger.review_icons(), [])

    def test_deleting_learned_reference_keeps_legacy_icon_and_recorded_export_column(self):
        """Verify removing learned artwork preserves legacy icons and historical export columns."""
        logger.save_currency_icon("Chaos Orb", self.tile)
        self.inventory()
        self.window.inventory_table.item(0, 1).setText("A removable learned token")
        self.window.inventory_table.item(0, 2).setText("1")
        self.approve_currency_row()
        self.window.approve_review()
        listing = self.window.reference_list
        for index in range(listing.count()):
            if listing.item(index).data(Qt.ItemDataRole.UserRole)[0] == "review":
                listing.setCurrentRow(index)
                break
        else:
            self.fail("Learned artwork was not listed for removal")
        self.window.remove_reference_example()
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual([entry["name"] for entry in logger.currency_icons()], ["Chaos Orb"])
        self.assertIn("Currency: A removable learned token", self.export_rows()[0])


if __name__ == "__main__":
    unittest.main()
