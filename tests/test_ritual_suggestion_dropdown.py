"""Exercise Ritual alternatives while keeping selection and explicit row approval separate."""

import copy
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QTableWidget

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class RitualSuggestionDropdownTests(unittest.TestCase):
    """Keep candidate names, captured evidence and final page approval independent."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ritual-suggestions-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1366, 900)
        self.window.show()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.page = Image.fromarray(np.random.default_rng(91).integers(
            35, 220, size=(300, 500, 3), dtype=np.uint8))
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def unknown(self, slot=14, **changes):
        x = 100 + (slot - 14) * 50
        return {"category": "Omen", "name": "", "quantity": 1, "tribute": None,
                "source": f"Grid slot {slot}: shared Omen artwork", "score": .7,
                "unresolved": True, "name_needs_review": True, "needs_review": True,
                "deferred": False, "box": (x, 60, x + 40, 100), "grid_slots": [slot],
                "candidate_names": ["Omen of Whittling", "Omen of Refreshment", "Omen of Sinistral Alchemy",
                                    "Omen of Greater Annulment", "Omen of Sinistral Coronation",
                                    "Omen of Greater Exaltation"], **changes}

    def capture(self):
        self.window._ritual_captured(self.page, live=False)
        return self.jobs[-1][1]

    def read(self, items=None, *, auto=False):
        self.window.state["settings"]["ocr_auto_commit"] = auto
        self.window._ritual_captured(self.page, live=auto)
        self.jobs[-1][1]({"items": [self.unknown()] if items is None else items,
                         "raw_text": "Captured Ritual alternatives", "unmatched": [],
                         "tribute_available": 0, "rerolls_remaining": 2})
        self.app.processEvents()
        return self.window.ritual_table.cellWidget(0, 1)

    def choose(self, selector, index=0):
        self.assertIsInstance(selector, QComboBox)
        selector.showPopup()
        self.app.processEvents()
        model_index = selector.model().index(index, 0)
        QTest.mouseClick(selector.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=selector.view().visualRect(model_index).center())
        self.app.processEvents()

    def approve_row(self, row=0):
        """Approve one corrected draft without committing its page or learned artwork."""
        controls = self.window.ritual_table.cellWidget(row, 6)
        self.window.review_ritual_row(controls, True)
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def approve(self):
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        QTest.mouseClick(self.window.approve_scan_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def assert_crop_visible(self, row=0):
        """Check rendered capture pixels beside the selector, beyond its underlying item icon."""
        table = self.window.ritual_table
        selector = table.cellWidget(row, 1)
        self.window.tabs.widget(0).ensureWidgetVisible(table)
        self.app.processEvents()
        cell = table.item(row, 1)
        rect = table.visualItemRect(cell)
        editor = selector.geometry()
        self.assertGreater(editor.left(), rect.left())
        rendered = table.viewport().grab().toImage()
        visible = {rendered.pixel(x, y) for x in range(rect.left(), editor.left())
                   for y in range(rect.top(), rect.bottom() + 1)}
        icon = cell.icon().pixmap(table.iconSize()).toImage()
        for x, y in ((icon.width() // 3, icon.height() // 3),
                     (icon.width() // 2, icon.height() // 2),
                     (icon.width() * 2 // 3, icon.height() * 2 // 3)):
            self.assertIn(icon.pixel(x, y), visible)

    def test_captured_alternatives_split_dedupe_and_start_blank_without_auto_save(self):
        candidates = [" Omen of Whittling / Omen of Refreshment ", "omen of whittling",
                      "", " Unknown omen / Unrecognized item / Unidentified reward ", "Audit token/A"]
        original = self.unknown(candidate_names=candidates)
        selector = self.read([original], auto=True)
        self.assertIsInstance(selector, QComboBox)
        self.assertEqual(selector.objectName(), "ritualNameSuggestion")
        self.assertTrue(selector.isEditable())
        self.assertTrue(selector.isEnabled())
        self.assertEqual(selector.currentIndex(), -1)
        self.assertEqual(selector.currentText(), "")
        self.assertEqual([selector.itemText(i) for i in range(selector.count())],
                         ["Omen of Whittling", "Omen of Refreshment", "Audit token/A"])
        self.assertIn("Omen of Refreshment", selector.lineEdit().placeholderText())
        table = self.window.ritual_table
        self.assertEqual([table.item(0, i).text() for i in (0, 1, 2, 3)], ["", "", "1", ""])
        self.assertIn("Confirm type / name", table.item(0, 6).text())
        self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole), original)
        self.assertEqual(self.window.ritual_tribute.text(), "0")
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.review_icons(), [])

    def test_choice_updates_name_and_missing_type_without_changing_evidence_or_approving(self):
        selector = self.read()
        table = self.window.ritual_table
        metadata = copy.deepcopy(table.item(0, 0).data(Qt.ItemDataRole.UserRole))
        self.assertEqual([selector.itemText(i) for i in range(selector.count())], metadata["candidate_names"])
        fields = [table.item(0, i).text() for i in (2, 3, 4)]
        icon_key = table.item(0, 1).icon().cacheKey()
        self.assertFalse(table.item(0, 1).icon().isNull())
        self.choose(selector, 1)
        self.assertEqual([table.item(0, i).text() for i in (0, 1)], ["Omen", "Omen of Refreshment"])
        self.assertEqual([table.item(0, i).text() for i in (2, 3, 4)], fields)
        self.assertEqual(table.item(0, 5).checkState(), Qt.CheckState.Unchecked)
        self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole), metadata)
        self.assertEqual(table.item(0, 1).icon().cacheKey(), icon_key)
        self.assertIn("Confirm row", table.item(0, 6).text())
        self.assertEqual(table.cellWidget(0, 6).property("reviewStatus"), "pending")
        self.assertFalse(table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.review_icons(), [])
        self.assert_crop_visible()

    def test_row_then_global_approval_saves_choice_and_locks_preview(self):
        selector = self.read()
        self.choose(selector)
        self.approve_row()
        self.approve()
        table = self.window.ritual_table
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual(len(pages), 1)
        item = pages[0]["items"][0]
        self.assertEqual((item["category"], item["name"], item["quantity"], item["deferred"]),
                         ("Omen", "Omen of Whittling", 1, False))
        self.assertIsNone(item["tribute"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(selector.isEnabled())
        self.assertEqual(selector.currentText(), "Omen of Whittling")
        self.assertIn("Saved", table.item(0, 6).text())
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertFalse(table.item(0, 1).icon().isNull())
        self.assertTrue(table.item(0, 0).data(Qt.ItemDataRole.UserRole)["unresolved"])
        self.assert_crop_visible()

    def test_free_typing_and_canonical_item_edits_synchronize_without_recursion(self):
        selector = self.read()
        self.assertIsInstance(selector, QComboBox)
        QTest.keyClicks(selector.lineEdit(), "Omen of a Manual Audit")
        table = self.window.ritual_table
        self.assertEqual(table.item(0, 1).text(), "Omen of a Manual Audit")
        self.assertEqual(table.item(0, 0).text(), "Omen")
        self.assertEqual(selector.count(), 6)
        spy = QSignalSpy(selector.currentTextChanged)
        table.item(0, 1).setText("Omen of Another Audit")
        self.assertEqual(selector.currentText(), "Omen of Another Audit")
        self.assertEqual(spy.count(), 0)
        table.item(0, 0).setText("Item")
        selector.lineEdit().selectAll()
        QTest.keyClicks(selector.lineEdit(), "Omen of a Confirmed Type Audit")
        self.assertEqual(table.item(0, 0).text(), "Item")
        self.assertEqual(table.cellWidget(0, 6).property("reviewStatus"), "pending")
        self.approve_row()
        self.approve()
        saved = logger.ritual_pages_for_map("M0001")[0]["items"][0]
        self.assertEqual((saved["name"], saved["category"]), ("Omen of a Confirmed Type Audit", "Item"))

    def test_choice_does_not_clear_count_or_deferred_holds_or_change_verified_type(self):
        selector = self.read([self.unknown(category="Item", category_verified=True,
                                           count_needs_review=True, deferred=None)])
        self.choose(selector)
        table = self.window.ritual_table
        self.assertEqual(table.item(0, 0).text(), "Item")
        self.assertEqual(table.item(0, 2).text(), "")
        self.assertEqual(table.item(0, 5).checkState(), Qt.CheckState.PartiallyChecked)
        table.item(0, 0).setText("Omen")
        selector.lineEdit().selectAll()
        QTest.keyClicks(selector.lineEdit(), "A manually confirmed Omen")
        self.assertEqual(table.item(0, 0).text(), "Omen")
        with self.assertRaisesRegex(ValueError, "quantity"):
            self.window.review_ritual_row(table.cellWidget(0, 6), True)
        table.item(0, 2).setText("2")
        with self.assertRaisesRegex(ValueError, "Deferred"):
            self.window.review_ritual_row(table.cellWidget(0, 6), True)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_row_removal_keeps_remaining_selector_bound_and_removed_callback_inert(self):
        first = self.read([self.unknown(), self.unknown(15)])
        self.assertIsInstance(first, QComboBox)
        table = self.window.ritual_table
        remaining = table.cellWidget(1, 1)
        table.setCurrentCell(0, 1)
        QTest.mouseClick(self.window.ritual_remove_row_button, Qt.MouseButton.LeftButton)
        self.assertIs(table.cellWidget(0, 1), remaining)
        self.window._ritual_suggestion_changed(first, "Stale removed choice")
        self.assertEqual(table.item(0, 1).text(), "")
        self.choose(remaining, 1)
        self.assertEqual(table.item(0, 1).text(), "Omen of Refreshment")
        self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole)["grid_slots"], [15])
        self.approve_row()
        self.approve()
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["name"],
                         "Omen of Refreshment")

    def test_rescan_after_rejection_and_other_reviews_lock_or_retire_selectors(self):
        stale = self.read()
        self.assertIsInstance(stale, QComboBox)
        # A held page is never replaced by a rescan; it is rejected first.
        self.window.reject_review()
        callback = self.capture()
        self.window._ritual_suggestion_changed(stale, "Stale captured choice")
        self.assertEqual(self.window.ritual_table.rowCount(), 0)
        callback({"items": [self.unknown(15)], "raw_text": "New Ritual page"})
        current = self.window.ritual_table.cellWidget(0, 1)
        self.assertTrue(current.isEnabled())
        self.assertEqual(current.currentText(), "")
        self.window._ritual_suggestion_changed(stale, "Stale replaced choice")
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), "")
        self.window._review_pending("currency", "Another activity")
        self.assertFalse(current.isEnabled())
        self.read()
        current = self.window.ritual_table.cellWidget(0, 1)
        self.assertTrue(current.isEnabled())
        QTest.mouseClick(self.window.reject_scan_button, Qt.MouseButton.LeftButton)
        self.assertFalse(current.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_unnamed_candidate_rejected_on_approve_and_plain_manual_rows_still_work(self):
        selector = self.read()
        self.assertIsInstance(selector, QComboBox)
        QTest.mouseClick(self.window.ritual_add_row_button, Qt.MouseButton.LeftButton)
        table = self.window.ritual_table
        self.assertEqual(table.rowCount(), 2)
        self.assertIsNone(table.cellWidget(1, 1))
        table.item(1, 1).setText("A manually entered ring")
        self.assertEqual(table.item(1, 0).text(), "Item")
        QTest.mouseClick(self.window.ritual_add_row_button, Qt.MouseButton.LeftButton)
        table.item(2, 0).setText("")
        table.item(2, 1).setText("Omen of a Manual Row Audit")
        self.assertIsNone(table.cellWidget(2, 1))
        self.assertEqual(table.item(2, 0).text(), "Omen")
        self.approve_row(1)
        self.approve_row(2)
        self.approve()
        self.assertIn("Rejected: no name", table.item(0, 6).text())
        self.assertFalse(selector.isEnabled())
        self.assertEqual([item["name"] for item in logger.ritual_pages_for_map("M0001")[0]["items"]],
                         ["A manually entered ring", "Omen of a Manual Row Audit"])

    def test_unknown_placeholders_offer_no_dropdown_and_string_candidates_remain_supported(self):
        selector = self.read([self.unknown(candidate_names=" Omen of Whittling / Omen of Refreshment ")])
        self.assertIsInstance(selector, QComboBox)
        self.assertEqual(selector.count(), 2)
        for candidates in ([], ["Unknown omen", "Unrecognized item"], "Unidentified reward"):
            with self.subTest(candidates=candidates):
                self.window.reject_review()
                self.read([self.unknown(candidate_names=candidates)])
                self.assertIsNone(self.window.ritual_table.cellWidget(0, 1))
                self.assertEqual(self.window.ritual_table.item(0, 1).text(), "")


if __name__ == "__main__":
    unittest.main()
