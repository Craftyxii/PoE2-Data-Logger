"""Exercise suggested currency names without promoting guesses or moving row review."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class CurrencySuggestionDropdownTests(unittest.TestCase):
    """Keep selectors, canonical cells, approval, captured evidence and scrolling consistent."""

    @classmethod
    def setUpClass(cls):
        """Create the shared application for real dropdown and row-button interaction."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated visible logger with controlled scan callbacks and textured artwork."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-currency-suggestions-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1200, 800)
        self.window.show()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        pixels = np.random.default_rng(43).integers(25, 230, size=(270, 648, 3), dtype=np.uint8)
        self.grid = Image.fromarray(pixels)
        self.grid.info["poe2_inventory_aligned"] = True
        self.app.processEvents()

    def tearDown(self):
        """Close workers and restore local storage after each independent review."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def capture(self):
        """Queue an inventory reading owned by this map and return its callback."""
        self.window._inventory_captured(self.grid, live=False)
        return self.jobs[-1][1]

    def result(self, candidate="Regal Orb", slot=14):
        """Build an unnamed possible match whose count also needs manual confirmation."""
        return {"items": [], "unknown": [{"slot": slot, "candidate": candidate}]}

    def inventory(self, candidate="Regal Orb", slot=14):
        """Deliver one possible match and return its visible editable selector."""
        self.capture()(self.result(candidate, slot))
        self.app.processEvents()
        return self.window.inventory_table.cellWidget(0, 1)

    def controls(self, row=0):
        """Return the existing per-row approval controls."""
        return self.window.inventory_table.cellWidget(row, 3)

    def choose(self, selector, index=0):
        """Select a real popup option through the same mouse interaction as a user."""
        selector.showPopup()
        self.app.processEvents()
        model_index = selector.model().index(index, 0)
        QTest.mouseClick(selector.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=selector.view().visualRect(model_index).center())
        self.app.processEvents()

    def test_possible_match_is_visible_but_starts_blank_and_requires_manual_approval(self):
        """Choosing a suggestion supplies only the name, without approving or learning it."""
        selector = self.inventory()
        table = self.window.inventory_table
        self.assertIsInstance(selector, QComboBox)
        self.assertEqual(selector.objectName(), "currencyNameSuggestion")
        self.assertTrue(selector.isEditable())
        self.assertTrue(selector.isEnabled())
        self.assertEqual(selector.currentText(), "")
        self.assertEqual(table.item(0, 1).text(), "")
        self.assertEqual(table.item(0, 2).text(), "")
        self.assertEqual(selector.itemText(0), "Regal Orb")
        self.assertIn("Regal Orb", selector.lineEdit().placeholderText())
        original = table.item(0, 1).data(Qt.ItemDataRole.UserRole)
        self.choose(selector)
        self.assertEqual(table.item(0, 1).text(), "Regal Orb")
        self.assertEqual(self.controls().property("reviewStatus"), "pending")
        self.assertEqual(table.item(0, 1).data(Qt.ItemDataRole.UserRole), original)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.review_icons(), [])

    def test_shared_icon_tiers_are_separate_options_and_placeholders_have_no_selector(self):
        """Expose actual alternative labels while leaving unknown placeholders unnamed."""
        selector = self.inventory(" Regal Orb / Greater Transmutation Orb / regal orb / Unknown item ")
        self.assertEqual([selector.itemText(i) for i in range(selector.count())],
                         ["Regal Orb", "Greater Transmutation Orb"])
        self.choose(selector, 1)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "Greater Transmutation Orb")
        for placeholder in ("", "Unrecognized item", "Unknown item", "unidentified", "Unknown / Unidentified"):
            with self.subTest(placeholder=placeholder):
                self.inventory(placeholder)
                self.assertIsNone(self.window.inventory_table.cellWidget(0, 1))
                self.assertEqual(self.window.inventory_table.item(0, 1).text(), "")

    def test_manual_name_and_canonical_cell_edits_synchronize_without_signal_recursion(self):
        """Support free typing and the table-item API used by saving and existing callers."""
        selector = self.inventory()
        table = self.window.inventory_table
        QTest.keyClicks(selector.lineEdit(), "My corrected token")
        self.assertEqual(table.item(0, 1).text(), "My corrected token")
        self.assertEqual(selector.count(), 1)
        spy = QSignalSpy(selector.currentTextChanged)
        table.item(0, 1).setText("Another corrected token")
        self.assertEqual(selector.currentText(), "Another corrected token")
        self.assertEqual(spy.count(), 0)
        self.assertEqual(self.controls().property("reviewStatus"), "pending")
        table.item(0, 2).setText("4")
        self.controls().findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(self.controls().property("reviewStatus"), "approved")
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Another corrected token": 4})
        self.assertEqual([entry["name"] for entry in logger.review_icons()], ["Another corrected token"])
        self.assertFalse(selector.isEnabled())

    def test_changing_approved_name_or_count_requires_a_fresh_row_decision(self):
        """Each semantic edit invalidates approval without saving or teaching intermediate values."""
        selector = self.inventory("Regal Orb / Chaos Orb")
        table = self.window.inventory_table
        self.choose(selector)
        table.item(0, 2).setText("2")
        approve = self.controls().findChild(QPushButton, "approveCurrency")
        approve.click()
        self.assertEqual(self.controls().property("reviewStatus"), "approved")
        self.choose(selector, 1)
        self.assertEqual(self.controls().property("reviewStatus"), "pending")
        approve.click()
        table.item(0, 2).setText("3")
        self.assertEqual(self.controls().property("reviewStatus"), "pending")
        self.assertEqual(table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.review_icons(), [])

    def test_final_approve_rejects_selected_but_unapproved_suggestions(self):
        """A selected valid name and count cannot bypass explicit uncertain-row approval."""
        selector = self.inventory()
        self.choose(selector)
        self.window.inventory_table.item(0, 2).setText("3")
        self.window.approve_scan_button.click()
        self.assertEqual(self.controls().property("reviewStatus"), "rejected")
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(logger.review_icons(), [])
        self.assertFalse(selector.isEnabled())

    def test_rejection_and_replacement_scans_cannot_write_stale_selector_names(self):
        """Retire old controls and callbacks when a new capture replaces or rejects review."""
        older = self.capture()
        older(self.result())
        selector = self.window.inventory_table.cellWidget(0, 1)
        newer = self.capture()
        self.window._currency_suggestion_changed(selector, "Stale token")
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        newer(self.result("Chaos Orb", slot=60))
        older(self.result("Old token"))
        current = self.window.inventory_table.cellWidget(0, 1)
        self.assertEqual(current.itemText(0), "Chaos Orb")
        self.assertEqual(current.currentText(), "")
        self.assertEqual(self.window.inventory_table.item(0, 0).data(Qt.ItemDataRole.UserRole), 60)
        self.window.reject_review()
        self.assertFalse(current.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.inventory()
        self.assertTrue(self.window.inventory_table.cellWidget(0, 1).isEnabled())

    def test_approved_suggestion_remains_bound_to_its_capture_map(self):
        """An approved candidate cannot teach or commit after its captured map ends."""
        selector = self.inventory()
        self.choose(selector)
        self.window.inventory_table.item(0, 2).setText("3")
        self.window.review_currency_row(self.controls(), True)
        logger.finish_map(0, 0, 0, 0)
        with self.assertRaises(ValueError):
            self.window.save_inventory()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(logger.review_icons(), [])

    def test_sixty_candidates_do_not_load_the_full_catalog_for_each_row(self):
        """Keep dropdown construction bounded by the suggestions from each captured slot."""
        with patch.object(logger, "inventory_names", wraps=logger.inventory_names) as names:
            self.capture()({"items": [], "unknown": [
                {"slot": slot, "candidate": "Regal Orb / Chaos Orb"} for slot in range(1, 61)]})
        names.assert_not_called()
        self.assertEqual(self.window.inventory_table.rowCount(), 60)
        self.assertTrue(all(self.window.inventory_table.cellWidget(row, 1).count() == 2
                            for row in range(60)))

    def test_queued_currency_restore_does_not_move_a_replacement_review(self):
        """Retained old currency widgets cannot restore scroll over a newer activity review."""
        self.inventory()
        page = self.window.tabs.widget(0)
        bar = page.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        controls = self.controls()
        with patch("PoE2_Data_Logger.ui.native_desktop.QTimer.singleShot") as timer:
            self.window.review_currency_row(controls, False)
        restore = timer.call_args.args[-1]
        self.window._review_pending("ritual", "New Ritual review")
        self.app.processEvents()
        self.assertIs(self.controls(), controls)
        bar.setValue(0)
        restore()
        self.assertEqual(bar.value(), 0)

    def test_deep_row_approve_reject_preserve_table_page_scroll_and_selection(self):
        """Keep the same visible row when its focused action becomes disabled, even after layout."""
        self.capture()({"items": [], "unknown": [
            {"slot": slot, "candidate": "Regal Orb"} for slot in range(1, 61)]})
        table = self.window.inventory_table
        page = self.window.tabs.widget(0)
        table.setColumnWidth(3, 900)
        table.setColumnWidth(2, 200)
        self.app.processEvents()
        row = 55
        selector = table.cellWidget(row, 1)
        selector.setEditText("Regal Orb")
        table.item(row, 2).setText("2")
        table.setCurrentCell(row, 2)
        table.scrollToItem(table.item(row, 2))
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        page.ensureWidgetVisible(table.cellWidget(row, 3), 0, 12)
        self.app.processEvents()
        bars = [table.verticalScrollBar(), table.horizontalScrollBar(),
                page.verticalScrollBar(), page.horizontalScrollBar()]
        self.assertGreater(bars[0].value(), 0)
        self.assertGreater(bars[1].value(), 0)
        self.assertGreater(bars[2].value(), 0)
        positions = [bar.value() for bar in bars]
        selected_row = table.currentRow()
        slots = [table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(table.rowCount())]
        controls = table.cellWidget(row, 3)
        for name, state in (("approveCurrency", "approved"), ("rejectCurrency", "rejected")):
            action = controls.findChild(QPushButton, name)
            action.setFocus()
            # Snapshot focus-induced scrolling before the actual decision.
            for bar, position in zip(bars, positions):
                bar.setValue(position)
            QTest.mouseClick(action, Qt.MouseButton.LeftButton)
            self.assertEqual(controls.property("reviewStatus"), state)
            self.assertEqual([bar.value() for bar in bars], positions)
            self.app.processEvents()
            QTest.qWait(20)
            self.app.processEvents()
            self.assertEqual([bar.value() for bar in bars], positions)
            self.assertEqual(table.currentRow(), selected_row)
            self.assertEqual({index.row() for index in table.selectedIndexes()}, {selected_row})
            self.assertIs(self.app.focusWidget(), controls)
        table.item(row, 2).setText("3")
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.assertEqual([bar.value() for bar in bars], positions)
        self.assertEqual([table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(table.rowCount())], slots)


if __name__ == "__main__":
    unittest.main()
