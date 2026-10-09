"""Qt correction checks retaining unknown Ritual rows until required fields are corrected or removed."""

import copy
import csv
import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_ritual_read


class RitualReviewRowsTests(unittest.TestCase):
    """Exercise unknown Ritual row evidence, explicit corrections, automatic-save holds and locked previews."""
    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by Ritual review widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated desktop window, queue scan callbacks and paint distinct reward evidence."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ritual-review-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.page = Image.new("RGB", (500, 300), "tan")
        draw = ImageDraw.Draw(self.page)
        draw.rectangle((100, 60, 139, 99), fill=(220, 30, 40))
        draw.rectangle((150, 60, 189, 99), fill=(30, 50, 220))

    def tearDown(self):
        """Close desktop workers, restore logger storage and remove temporary data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def known(self, **changes):
        """Build a confident named Ritual reward with optional field overrides."""
        return {"category": "Item", "name": "Chaos Orb", "quantity": 1, "tribute": 100,
                "source": "Chaos Orb", "score": 1, "deferred": False, **changes}

    def unknown(self, slot=14, **changes):
        """Build an unresolved reward retaining its slot, crop and review reasons."""
        x = 100 if slot == 14 else 150
        return {"category": "Item", "name": "", "quantity": 1, "tribute": None,
                "source": f"Grid slot {slot}: unreadable item artwork", "score": 0,
                "unresolved": True, "name_needs_review": True, "needs_review": True,
                "deferred": False, "box": (x, 60, x + 40, 100), "grid_slots": [slot], **changes}

    def result(self, items, **changes):
        """Build a Ritual page result with default header totals and optional overrides."""
        return {"items": items, "raw_text": "Ritual screenshot evidence", "unmatched": [],
                "tribute_available": 5430, "rerolls_remaining": 2, **changes}

    def read(self, result, live=False, auto=False):
        """Deliver a captured Ritual page and its queued recognition result under the chosen save policy."""
        self.window.state["settings"]["ocr_auto_commit"] = auto
        self.window._ritual_captured(self.page, live=live)
        self.jobs[-1][1](result)

    def saved_items(self):
        """Read the most recently saved first-map Ritual page's rewards."""
        return logger.ritual_pages_for_map("M0001")[-1]["items"]

    def correct(self, row, name="Custom rare ring", quantity=2, category="Item", deferred=False):
        """Edit the required reward fields and deferred checkbox for a review row."""
        for column, text in ((0, category), (1, name), (2, str(quantity))):
            self.window.ritual_table.item(row, column).setText(text)
        self.window.ritual_table.item(row, 5).setCheckState(
            Qt.CheckState.Checked if deferred else Qt.CheckState.Unchecked)

    def test_all_occupied_rows_remain_visible_with_unknown_fields_and_review_reasons(self):
        """Verify every occupied reward remains visible with unknown fields and appropriate review reasons."""
        items = [self.known(grid_slots=[1]), self.unknown(),
                 self.unknown(15, count_needs_review=True)]
        self.read(self.result(items, grid_detected=True, grid_reward_count=3), live=True, auto=True)

        table = self.window.ritual_table
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(table.columnCount(), 7)
        self.assertEqual(table.horizontalHeaderItem(6).text(), "Review")
        self.assertEqual(table.item(0, 1).text(), "Chaos Orb")
        for row in (1, 2):
            self.assertEqual((table.item(row, 0).text(), table.item(row, 1).text()), ("", ""))
            self.assertIn("name", table.item(row, 6).text().casefold())
        self.assertEqual(table.item(1, 2).text(), "1")
        self.assertEqual(table.item(2, 2).text(), "")
        self.assertRegex(table.item(2, 6).text().casefold(), "count|quantity")
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])

    def test_unknown_rows_cannot_save_until_critical_fields_are_corrected(self):
        """Verify unknown rewards cannot save until all critical fields are corrected."""
        self.read(self.result([self.unknown(count_needs_review=True, deferred=None)]))
        self.window.ritual_table.item(0, 1).setText("A manually identified rare ring")
        with self.assertRaisesRegex(ValueError, r"(?i)row|name|type|reward"):
            self.window.save_ritual()
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.correct(0, "A manually identified rare ring", quantity=3)
        self.window.approve_review()

        item = self.saved_items()[0]
        self.assertEqual((item["category"], item["name"], item["quantity"], item["deferred"]),
                         ("Item", "A manually identified rare ring", 3, False))
        self.assertIsNone(item["tribute"])
        self.assertIsNone(self.window.pending_review_kind)

    def test_verified_gear_footprint_keeps_item_type_and_quantity_one_until_name_is_entered(self):
        """Verify proven gear footprints preserve item type and quantity one while awaiting a name."""
        gear = self.unknown(category_verified=True, quantity=1, grid_slots=[14, 26],
                            box=(100, 60, 140, 140))
        self.read(self.result([gear], grid_detected=True, grid_reward_count=1), live=True, auto=True)
        table = self.window.ritual_table
        self.assertEqual([table.item(0, column).text() for column in (0, 1, 2)], ["Item", "", "1"])
        reason = table.item(0, 6).text().casefold()
        self.assertIn("name", reason)
        self.assertNotIn("type", reason)
        self.assertNotIn("quantity", reason)
        table.item(0, 1).setText("A manually identified rare belt")
        self.window.approve_review()

        self.assertEqual([(item["category"], item["name"], item["quantity"]) for item in self.saved_items()],
                         [("Item", "A manually identified rare belt", 1)])

    def test_removing_unknown_row_preserves_known_and_manually_corrected_rewards(self):
        """Verify removing an unknown row preserves known and manually corrected rewards."""
        self.read(self.result([self.known(), self.unknown(), self.unknown(15)]))
        self.correct(1, name="A second custom item", quantity=2)
        self.window.ritual_table.setCurrentCell(2, 1)
        self.window.ritual_remove_row_button.click()
        self.assertEqual(self.window.ritual_table.rowCount(), 2)
        self.window.approve_review()
        self.assertEqual([(item["name"], item["quantity"]) for item in self.saved_items()],
                         [("Chaos Orb", 1), ("A second custom item", 2)])

    def test_unknown_row_retains_crop_slot_and_source_evidence(self):
        """Verify an unknown row retains its screenshot crop, original slot and source evidence."""
        unknown = self.unknown()
        self.read(self.result([unknown], grid_detected=True, grid_reward_count=1))
        table = self.window.ritual_table
        cells = [table.item(0, column) for column in range(table.columnCount())]
        evidence = [cell.data(Qt.ItemDataRole.UserRole) for cell in cells
                    if isinstance(cell.data(Qt.ItemDataRole.UserRole), dict)]
        self.assertTrue(any(item.get("box") == unknown["box"] and
                            item.get("grid_slots") == [14] for item in evidence))
        self.assertEqual(table.item(0, 4).text(), unknown["source"])
        tooltips = " ".join(cell.toolTip() for cell in cells)
        self.assertIn("14", tooltips)
        self.assertIn("unreadable item artwork", tooltips)
        icons = [cell.icon() for cell in cells if not cell.icon().isNull()]
        self.assertTrue(icons)
        crop = icons[0].pixmap(32, 32).toImage()
        self.assertEqual(crop.pixelColor(crop.width() // 2, crop.height() // 2).getRgb()[:3], (220, 30, 40))

    def test_auto_commit_rejects_uncertain_names_counts_deferred_state_and_confidence(self):
        """Verify uncertain names, counts, deferred flags, confidence or price block automatic saving."""
        base = self.result([self.known()])
        self.assertTrue(clear_ritual_read(base, logger.ritual_names()))
        changes = ({"name": ""}, {"name": "Unidentified reward"}, {"name": "Deferred omen"},
                   {"name_needs_review": True}, {"count_needs_review": True},
                   {"quantity": 0}, {"quantity": 1.5}, {"quantity": True},
                   {"quantity": 1000001}, {"deferred": None}, {"deferred": "false"},
                   {"deferred_needs_review": True}, {"deferred_uncertain": True},
                   {"score": .93}, {"score": float("nan")}, {"tribute": None})
        for change in changes:
            with self.subTest(change=change):
                result = copy.deepcopy(base)
                result["items"][0].update(change)
                self.assertFalse(clear_ritual_read(result, logger.ritual_names()))

    def test_auto_commit_rejects_incomplete_grid_duplicate_slots_and_page_uncertainty(self):
        """Verify incomplete grid coverage, duplicate slots and page uncertainty block automatic saving."""
        first = self.known(grid_slots=[1])
        second = self.known(name="Regal Orb", source="Regal Orb", grid_slots=[2])
        base = self.result([first, second], grid_detected=True, grid_reward_count=2)
        self.assertTrue(clear_ritual_read(base, logger.ritual_names()))
        cases = [self.result([first], grid_detected=True, grid_reward_count=2),
                 self.result([first, self.known(grid_slots=[1])], grid_detected=True, grid_reward_count=2),
                 {**base, "grid_reward_count": None}, {**base, "reward_count": 3}]
        cases.extend({**base, key: value} for key, value in
                     (("unknown", [14]), ("unmatched", ["Unreadable text"]),
                      ("coverage_uncertain", True), ("needs_review", True), ("unresolved_count", 1),
                      ("tribute_needs_review", True), ("rerolls_needs_review", True),
                      ("totals_needs_review", True)))
        for result in cases:
            with self.subTest(result=result):
                self.assertFalse(clear_ritual_read(result, logger.ritual_names()))

    def test_save_rejects_invalid_critical_fields_without_partial_page_writes(self):
        """Verify invalid critical fields reject the save without partially writing a page."""
        cases = ((0, ""), (0, "Currency"), (1, "Unidentified reward"),
                 (1, "Deferred omen"), (2, ""), (2, "0"), (2, "-1"),
                 (2, "1.5"), (2, "1000001"), (5, Qt.CheckState.PartiallyChecked))
        for column, value in cases:
            with self.subTest(column=column, value=value):
                self.read(self.result([self.known()]))
                cell = self.window.ritual_table.item(0, column)
                cell.setCheckState(value) if column == 5 else cell.setText(value)
                with self.assertRaises(ValueError):
                    self.window.save_ritual()
                self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
                self.assertEqual(self.window.pending_review_kind, "ritual")
                self.window.reject_review()

    def test_legacy_deferred_omen_placeholder_requires_real_name_before_export(self):
        """Verify deferred omen placeholders require real names before export and count as no new find."""
        self.read(self.result([self.known(category="Omen", name="Deferred omen", tribute=None,
                                          deferred=True, needs_review=True, source="deferred marker")]),
                  live=True, auto=True)
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), "")
        self.assertEqual(self.window.ritual_table.item(0, 2).text(), "")
        name = logger.ritual_names()[0]
        self.correct(0, name=name, category="Omen", quantity=1, deferred=True)
        self.window.approve_review()
        rows = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
        self.assertEqual(rows[0]["Name"], name)
        self.assertEqual(rows[0]["New Find Quantity"], "0")
        self.assertNotIn("Deferred omen", logger.export_ritual_csv().decode("utf-8-sig"))

    def test_named_icon_without_tribute_is_held_but_explicit_approval_can_confirm_blank_price(self):
        """Verify a named icon with no price waits for approval, which can confirm a blank price."""
        name = logger.ritual_names()[0]
        item = self.known(category="Omen", name=name, tribute=None, name_match=1,
                          source="icon reference: " + name, needs_review=True)
        self.read(self.result([item]), live=True, auto=True)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), name)
        self.window.approve_review()
        self.assertEqual(self.saved_items()[0]["name"], name)
        self.assertIsNone(self.saved_items()[0]["tribute"])

    def test_existing_clear_text_fixture_still_saves_automatically_and_locks_review(self):
        """Verify a clear named reward still saves automatically and locks further row editing."""
        self.read(self.result([self.known()]), live=True, auto=True)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.saved_items()[0]["name"], "Chaos Orb")
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertTrue(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.ritual_add_row_button.isEnabled())

    def test_raw_ocr_stays_in_debug_and_is_saved_when_developer_mode_is_off(self):
        """Keep raw scan text off the review HUD while retaining evidence through approval."""
        raw_text = "FAVOURS\n6146 TRIBUTE\nChaos Orb"
        self.window.show()
        self.read(self.result([self.known()], raw_text=raw_text))
        review = self.window.tabs.widget(0).widget()
        debug = self.window.tabs.widget(5).widget()
        self.assertFalse(review.isAncestorOf(self.window.ritual_raw))
        self.assertTrue(debug.isAncestorOf(self.window.ritual_raw))
        self.assertTrue(self.window.ritual_raw.isReadOnly())
        for enabled in (False, True):
            with self.subTest(developer_mode=enabled):
                self.window.developer_mode.setChecked(enabled)
                self.window.tabs.setCurrentIndex(0)
                self.app.processEvents()
                self.assertTrue(self.window.ritual_table.isVisible())
                self.assertFalse(self.window.ritual_raw.isVisible())
                self.window.tabs.setCurrentIndex(5)
                self.app.processEvents()
                self.assertEqual(self.window.ritual_raw.isVisible(), enabled)
                self.assertEqual(self.window.ritual_raw.toPlainText(), raw_text)
        self.window.developer_mode.setChecked(False)
        self.window.tabs.setCurrentIndex(0)
        self.window.approve_scan_button.click()
        self.assertEqual(self.saved_items()[0]["name"], "Chaos Orb")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT raw_text FROM ritual_pages WHERE map_id=?",
                                        ("M0001",)).fetchone()[0], raw_text)
        exported = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
        self.assertEqual(exported[0]["Page OCR Text"], raw_text)

    def test_saved_corrected_row_is_readonly_with_evidence_and_new_scan_resets_saved_status(self):
        """Verify saved corrections retain readonly evidence and a new scan restores pending editing."""
        self.read(self.result([self.unknown(count_needs_review=True)]))
        self.correct(0, quantity=3)
        self.window.approve_review()
        table = self.window.ritual_table
        self.assertIn("Saved", table.item(0, 6).text())
        self.assertIn("14", table.item(0, 6).text())
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertTrue(table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1))
        self.assertTrue(table.item(0, 0).data(Qt.ItemDataRole.UserRole)["unresolved"])

        self.read(self.result([self.unknown(15)]))

        self.assertNotIn("Saved", table.item(0, 6).text())
        self.assertIn("Confirm", table.item(0, 6).text())
        self.assertFalse(table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1))
        self.assertNotEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)

    def test_uncertain_grid_coverage_shows_missing_reward_warning_and_holds_auto_commit(self):
        """Verify uncertain grid coverage warns about missing rewards and prevents automatic saving."""
        self.read(self.result([self.known(grid_slots=[1])], grid_detected=True,
                              grid_reward_count=1, coverage_uncertain=True), live=True, auto=True)
        self.assertIn("grid", self.window.review_summary.text().casefold())
        self.assertIn("missing", self.window.review_summary.text().casefold())
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])


if __name__ == "__main__":
    unittest.main()
