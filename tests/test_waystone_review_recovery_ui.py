"""User-navigation checks for correcting incomplete waystone and tablet scans."""

import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ItemReviewRecoveryTests(unittest.TestCase):
    """Exercise exposed correction fields and save controls against isolated SQLite data."""

    @classmethod
    def setUpClass(cls):
        """Create one Qt application for all navigation checks."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Start a visible logger with one configured tablet and automatic saving disabled."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-item-review-recovery-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.save_settings({"tablets_used": 1})
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1280, 800)
        self.window.show()
        output = io.BytesIO()
        Image.new("RGB", (500, 700), "tan").save(output, format="PNG")
        self.raw = output.getvalue()
        self.app.processEvents()

    def tearDown(self):
        """Stop this window's workers and restore the caller's data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def click(self, widget):
        """Scroll a displayed control into view and activate it with a mouse event."""
        parent = widget.parentWidget()
        while parent:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 8, 12)
            parent = parent.parentWidget()
        self.app.processEvents()
        self.assertTrue(widget.isVisible())
        self.assertTrue(widget.isEnabled())
        hit = self.window.childAt(widget.mapTo(self.window, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit))
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def tab(self, number):
        """Visit a page through its visible sidebar button and normal page-change callback."""
        nav = next(button for position, button in enumerate(self.window.nav_buttons)
                   if (position if button.property("page_index") is None else button.property("page_index")) == number)
        self.assertTrue(nav.isVisible())
        QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.tabs.currentIndex(), number)

    def edit(self, widget, text):
        """Replace a text field through keyboard events, including clearing empty values."""
        self.click(widget)
        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        if text:
            QTest.keyClicks(widget, text)
        else:
            QTest.keyClick(widget, Qt.Key.Key_Backspace)
        self.app.processEvents()
        self.assertEqual(widget.text(), text)

    def choose(self, widget, data):
        """Select a stored dropdown value through the keyboard."""
        self.click(widget)
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        target = widget.findData(data)
        self.assertGreaterEqual(target, 0)
        QTest.keyClick(widget, Qt.Key.Key_Home)
        for _ in range(target):
            QTest.keyClick(widget, Qt.Key.Key_Down)
        QTest.keyClick(widget, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(widget.currentData(), data)

    def scan_waystone(self, *, tier=None, percentage=None):
        """Inject a completed item reading with missing fields at the scanner boundary."""
        text = ("Item Class: Waystones\nRarity: Rare\nReview Waystone\n" +
                (f"Waystone (Tier {tier})\n" if tier is not None else "Waystone\n") +
                (f"Waystone Drop Chance: +{percentage}%\n" if percentage is not None else "") +
                "Item Level: 82\nMonsters have 20% increased maximum Life")
        result = parse_item_text(text, self.window.state["affixes"])
        result.update(logger.scan_context())
        self.window._hover_item_read(result, self.raw)
        self.app.processEvents()
        return result

    def scan_tablet(self, mods):
        """Inject a finished tablet reading while retaining the current capture context."""
        self.window._hover_item_read({"kind": "tablet", "source": "clipboard", "mods": mods,
                                      "matches": [], "uncertain": [], **logger.scan_context()}, self.raw)
        self.app.processEvents()

    def save_button(self):
        """Find the existing manual tablet-save action on the settings page."""
        return next(button for button in self.window.findChildren(QPushButton)
                    if button.text() == "Save tablet config")

    def test_missing_waystone_fields_can_be_corrected_without_developer_mode(self):
        """A held scan exposes required corrections and saves the user's exact modifier text."""
        result = self.scan_waystone()
        self.assertFalse(self.window.developer_mode.isChecked())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.click(self.window.review_edit_button)
        self.choose(self.window.tier, 16)
        self.edit(self.window.waystone, "93.5")
        self.edit(self.window.map_mods, "1")
        self.edit(self.window.waystone_mod_fields[0], "Monsters have 25% increased maximum Life")
        self.tab(0)
        self.click(self.window.approve_scan_button)
        saved = logger.get_state()
        self.assertEqual(saved["settings"]["tier"], 16)
        self.assertEqual(saved["settings"]["waystone"], 93.5)
        self.assertEqual([mod for mod in saved["settings"]["waystone_mods"] if mod],
                         ["Monsters have 25% increased maximum Life"])
        self.assertEqual(saved["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.tab(2)
        self.assertFalse(self.window.map_ocr_overrides.isVisible())
        self.assertEqual(result["fields"]["tier"], None)

    def test_rejecting_waystone_restores_saved_values_and_hides_review_overrides(self):
        """Reject discards corrections without exposing diagnostic fields outside the held scan."""
        self.scan_waystone(tier=16, percentage=85)
        self.click(self.window.review_edit_button)
        self.edit(self.window.waystone, "999")
        self.tab(0)
        self.click(self.window.reject_scan_button)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(self.window.waystone.text(), "0")
        self.tab(2)
        self.assertFalse(self.window.map_ocr_overrides.isVisible())

    def test_scanning_second_tablet_exposes_its_fields_at_the_new_capacity(self):
        """Increasing scan capacity immediately shows the slot whose fields need correction."""
        self.scan_tablet(["11% increased Pack Size in Map"])
        self.click(self.window.approve_scan_button)
        self.scan_tablet(["22% increased Pack Size in Map"])
        self.click(self.window.review_edit_button)
        self.assertEqual(self.window.tablets_used.currentData(), 2)
        self.assertTrue(self.window.tablet_groups[1].isVisible())
        self.edit(self.window.tablet_values[4], "23")
        self.tab(0)
        self.click(self.window.approve_scan_button)
        settings = logger.get_state()["settings"]
        self.assertEqual([settings["tablet_affixes"][index]["value"] for index in (0, 4)], [11, 23])
        self.assertEqual(settings["tablet_raw_mods"][:2],
                         [["11% increased Pack Size in Map"], ["22% increased Pack Size in Map"]])
        self.assertEqual(logger.tablet_next_slot(), 3)

    def test_unreadable_tablet_can_be_manually_corrected_and_approved(self):
        """Completed OCR with no matches still owns a slot that the user can correct."""
        self.scan_tablet(["The tooltip could not be read"])
        self.assertEqual(self.window._pending_tablet_slot, 1)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.click(self.window.review_edit_button)
        self.choose(self.window.tablet_affixes[0], "Pack Size")
        self.edit(self.window.tablet_values[0], "31")
        self.tab(0)
        self.click(self.window.approve_scan_button)
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablet_affixes"][0]["value"], 31)
        self.assertEqual(settings["tablet_raw_mods"][0], ["The tooltip could not be read"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(logger.tablet_next_slot(), 2)

    def test_unreadable_tablet_supports_existing_manual_save_action(self):
        """The manual save button persists a valid correction for the held empty reading."""
        self.scan_tablet(["The tooltip could not be read"])
        self.click(self.window.review_edit_button)
        self.choose(self.window.tablet_affixes[0], "Pack Size")
        self.edit(self.window.tablet_values[0], "41")
        self.click(self.save_button())
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"], 41)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)

    def test_empty_new_tablet_read_clears_old_draft_and_reject_restores_saved_config(self):
        """An empty replacement cannot approve an old draft, and rejecting preserves saved data."""
        pairs = list(logger.get_state()["settings"]["tablet_affixes"])
        pairs[0] = {"affix": "Pack Size", "value": 7}
        logger.save_settings({"tablet_affixes": pairs,
                              "tablet_raw_mods": [["7% increased Pack Size in Map"], [], [], []]})
        self.window.refresh()
        self.scan_tablet(["99% increased Pack Size in Map"])
        self.scan_tablet(["The tooltip could not be read"])
        self.assertEqual(self.window.tablet_affixes[0].currentData(), "")
        self.assertEqual(self.window.tablet_values[0].text(), "")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.click(self.window.reject_scan_button)
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablet_affixes"][0]["value"], 7)
        self.assertEqual(settings["tablet_raw_mods"][0], ["7% increased Pack Size in Map"])
        self.assertEqual(self.window.tablet_values[0].text(), "7")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.tablet_next_slot(), 1)

    def test_invalid_manual_tablet_value_does_not_advance_or_save(self):
        """Manual correction follows numeric validation before advancing the scan slot."""
        self.scan_tablet(["The tooltip could not be read"])
        self.click(self.window.review_edit_button)
        self.choose(self.window.tablet_affixes[0], "Pack Size")
        self.edit(self.window.tablet_values[0], "-1")
        self.tab(0)
        self.click(self.window.approve_scan_button)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["affix"], "")
        self.assertEqual(self.window.pending_review_kind, "tablet")

    def test_full_tablet_set_cannot_be_reenabled_by_manual_field_edits(self):
        """A fifth scan stays blocked until the saved four-tablet set is explicitly cleared."""
        for number in range(1, 5):
            self.scan_tablet([f"{number * 10}% increased Pack Size in Map"])
            self.click(self.window.approve_scan_button)
        self.scan_tablet(["50% increased Pack Size in Map"])
        self.assertIsNone(self.window._pending_tablet_slot)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.click(self.window.review_edit_button)
        self.edit(self.window.tablet_values[12], "99")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.click(self.save_button())
        self.assertEqual(logger.get_state()["scan_commit_count"], 4)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][12]["value"], 40)
        self.assertEqual(logger.tablet_next_slot(), 5)


if __name__ == "__main__":
    unittest.main()
