"""User-input checks for the two added map-context fields in the existing header."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class MapActivityHeaderUITests(unittest.TestCase):
    """Exercise saved map context and normal approval/Undo through visible Qt controls."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication needed for real keyboard, mouse and popup events."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open a disposable empty map, stopping polling and retaining synthetic capture pixels."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-header-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1280, 800)
        self.window.show()
        capture = io.BytesIO()
        Image.new("RGB", (500, 400), "tan").save(capture, format="PNG")
        self.raw = capture.getvalue()
        self.app.processEvents()

    def tearDown(self):
        """Stop this window's workers and restore the caller's profile after each scenario."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def expose(self, widget):
        """Scroll the actual containing view so a user can reach the requested control."""
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 8, 12)
            parent = parent.parentWidget()
        self.app.processEvents()
        self.assertTrue(widget.isVisible(), widget.accessibleName() or widget.objectName())
        hit = self.window.childAt(widget.mapTo(self.window, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit),
                        f"Control is obscured: {widget.accessibleName() or widget.objectName()}")

    def click(self, widget):
        """Activate a reachable enabled control using a real mouse event."""
        self.expose(widget)
        self.assertTrue(widget.isEnabled())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def edit(self, widget, text):
        """Replace a normal entry through keyboard events rather than calling its save handler."""
        self.click(widget)
        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(widget, text)
        QTest.keyClick(widget, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertEqual(widget.text(), text)

    def choose_activity(self, selected):
        """Navigate the open popup and confirm once, creating one saved selection action."""
        control = self.window.header_activity
        self.click(control)
        target = control.findData(selected)
        self.assertGreaterEqual(target, 0)
        previous = control.currentData()
        # Send navigation to the popup view, retaining the saved selection
        # until Enter; keys sent to a closed combo would save each intermediate item.
        QTest.keyClick(control.view(), Qt.Key.Key_Home)
        for _ in range(target):
            QTest.keyClick(control.view(), Qt.Key.Key_Down)
        self.assertEqual(control.currentData(), previous)
        QTest.keyClick(control.view(), Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(control.currentData(), selected)

    def assert_map_scanned(self, expected):
        """Compare the read-only displayed indicator with the persisted current-map setup state."""
        self.assertEqual(self.window.header_map_scanned.text(),
                         "Map scanned: " + ("Yes" if expected else "No"))
        self.assertEqual(logger.get_state()["current_map_setup_saved"], expected)
        self.assertIsInstance(self.window.header_map_scanned, QLabel)

    def map_snapshot(self, map_id="M0001"):
        """Read the stored map context independently of the current form widgets."""
        with logger._connect() as db:
            saved = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
        self.assertIsNotNone(saved)
        return json.loads(saved[0])

    def stage_waystone(self, *, complete=True):
        """Deliver parser output at the completed OCR boundary without changing any scanner code."""
        source = ("Item Class: Waystones\nRarity: Rare\nHeader Test Waystone\n" +
                  ("Waystone (Tier 16)\nWaystone Drop Chance: +85%\n" if complete else "Waystone\n") +
                  "Item Level: 82\nMonsters have 20% increased maximum Life")
        reading = parse_item_text(source, self.window.state["affixes"])
        reading.update(logger.scan_context())
        self.window._hover_item_read(reading, self.raw)
        self.app.processEvents()
        return reading

    def test_activity_options_save_one_tag_and_single_undo_restores_previous_tag(self):
        """A replacement activity saves one map tag; Undo does not erase unrelated map context."""
        control = self.window.header_activity
        self.assertEqual([control.itemData(index) for index in range(control.count())],
                         ["None", "Breach", "Expedition", "Grand Expedition", "Abyss", "Ritual"])
        self.assertEqual(control.accessibleName(), "Map activity")
        self.assert_map_scanned(False)
        for activity in ("Breach", "Expedition", "Grand Expedition", "Abyss", "Ritual", "None"):
            with self.subTest(activity=activity):
                self.choose_activity(activity)
                self.assertEqual(logger.get_state()["settings"]["activity_type"], activity)
                self.assertEqual(self.map_snapshot()["activity_type"], activity)
                self.assert_map_scanned(False)
        self.choose_activity("Breach")
        self.choose_activity("Grand Expedition")
        self.click(self.window.undo_button)
        self.assertEqual(control.currentData(), "Breach")
        self.assertEqual(self.map_snapshot()["activity_type"], "Breach")
        self.assert_map_scanned(False)

    def test_manual_setup_new_map_and_undo_show_saved_status_for_the_correct_map(self):
        """Saved manual stats count as Yes; New map resets to No and Undo restores the previous Yes."""
        self.assert_map_scanned(False)
        self.window.tabs.setCurrentIndex(2)
        self.app.processEvents()
        self.edit(self.window.waystone, "85")
        self.assert_map_scanned(False)
        save = next(button for button in self.window.findChildren(QPushButton)
                    if button.text() == "Save map settings")
        self.click(save)
        self.assert_map_scanned(True)
        self.assertEqual(self.map_snapshot()["waystone"], 85)
        new_map = next(button for button in self.window.findChildren(QPushButton)
                       if button.text() == "+ New map")
        self.click(new_map)
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assert_map_scanned(False)
        self.click(self.window.undo_button)
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assert_map_scanned(True)
        self.assertEqual(float(self.window.waystone.text()), 85)

    def test_held_waystone_stays_no_until_explicit_approval_and_undo_restores_no(self):
        """Reading and editing a captured waystone do not indicate saved stats before Approve."""
        self.window.set_auto_all(False)
        self.stage_waystone()
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.assert_map_scanned(False)
        self.window.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.click(self.window.approve_scan_button)
        self.assertIsNone(self.window.pending_review_kind)
        self.assert_map_scanned(True)
        self.assertEqual(self.map_snapshot()["waystone"], 85)
        self.click(self.window.undo_button)
        self.assert_map_scanned(False)

    def test_automatic_waystone_only_sets_yes_when_the_reading_is_applied(self):
        """Existing automatic-save guards hold incomplete OCR at No and set Yes after a clear save."""
        self.window.set_auto_all(True)
        self.stage_waystone(complete=False)
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.assertIn("held", self.window.map_scan_status.text())
        self.assert_map_scanned(False)
        self.window.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.click(self.window.reject_scan_button)
        self.assert_map_scanned(False)
        self.stage_waystone()
        self.assertIsNone(self.window.pending_review_kind)
        self.assert_map_scanned(True)
        self.assertEqual(self.map_snapshot()["waystone"], 85)

    def test_tablet_and_remnant_approval_do_not_claim_waystone_setup_saved(self):
        """Other scan approvals can log normally while the current map's setup indicator remains No."""
        self.window.set_auto_all(False)
        tablet = {"kind": "tablet", "source": "clipboard", "mods": ["11% increased Pack Size in Map"],
                  "matches": [], "uncertain": [], **logger.scan_context()}
        self.window._hover_item_read(tablet, self.raw)
        self.window.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assert_map_scanned(False)
        self.click(self.window.approve_scan_button)
        self.assertEqual(logger.tablet_next_slot(), 2)
        self.assert_map_scanned(False)
        opened = {"mode": "opened", "status": "Review the opened rewards before logging.", "can_use": True,
                  "family": "Family 28", "candidates": [28], "sockets": 7, "recipe_sockets": 7,
                  "socket_source": "opened icons", "first_line_gap": 60, "list_complete": True,
                  "first_recipe": "Transcendent Alloy", "next_recipe": None,
                  "opened_recipes": [{"recipe": "Transcendent Alloy", "raw": "1x Transcendent Alloy",
                                      "ocr_score": .99, "match_score": 1}], **logger.scan_context()}
        self.window._scan_done("opened", opened, self.raw)
        self.app.processEvents()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assert_map_scanned(False)
        self.click(self.window.approve_scan_button)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertTrue(logger.get_state()["current_remnant_id"])
        self.assert_map_scanned(False)

    def test_header_controls_and_long_activity_popup_fit_supported_window_widths(self):
        """The two additions remain separate and usable beside the existing header controls."""
        self.choose_activity("Grand Expedition")
        new_map = next(button for button in self.window.findChildren(QPushButton)
                       if button.text() == "+ New map")
        controls = (self.window.header_activity, self.window.header_map_scanned,
                    self.window.page_title, self.window.page_subtitle,
                    self.window.normal, self.window.magic, self.window.rare, self.window.unique,
                    self.window.header_map_id, self.window.header_remnant_id,
                    self.window.header_expedition, self.window.header_complete_chain_button,
                    self.window.undo_button, new_map)
        for width in (900, 1024, 1366, 1920):
            with self.subTest(width=width):
                self.window.resize(width, 800)
                self.app.processEvents()
                for control in controls:
                    self.expose(control)
                rectangles = [QRect(control.mapTo(self.window, QPoint()), control.size())
                              for control in controls]
                for index, rectangle in enumerate(rectangles):
                    for later in range(index + 1, len(rectangles)):
                        self.assertFalse(rectangle.intersects(rectangles[later]),
                                         f"Header controls {index} and {later} overlap at {width}px")
                activity = self.window.header_activity
                self.expose(activity)
                self.assertGreaterEqual(activity.width(),
                                        activity.fontMetrics().horizontalAdvance("Grand Expedition") + 36)
                self.click(activity)
                self.assertTrue(activity.view().isVisible())
                longest = activity.findData("Grand Expedition")
                item_rect = activity.view().visualRect(activity.model().index(longest, 0))
                self.assertGreaterEqual(item_rect.width(),
                                        activity.fontMetrics().horizontalAdvance("Grand Expedition") + 12)
                self.assertTrue(activity.view().viewport().rect().contains(item_rect.center()))
                QTest.keyClick(activity.view(), Qt.Key.Key_Escape)
                self.app.processEvents()
                self.assertEqual(activity.currentData(), "Grand Expedition")


if __name__ == "__main__":
    unittest.main()
