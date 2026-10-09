"""Qt text-entry checks ensuring incomplete or decimal gear-rarity drafts cannot save a different number."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class AtlasNumericDraftTests(unittest.TestCase):
    """Exercise decimal and invalid gear-rarity drafts through the Atlas editor."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication needed by these widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open the Atlas rarity editor on a fresh temporary logger map."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-atlas-numeric-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.window.tabs.setCurrentIndex(12)
        self.app.processEvents()
        self.page = self.window.atlas_settings_page
        self.field = self.page.gear_rarity.lineEdit()

    def tearDown(self):
        """Close the window and worker pool, restore storage, and remove test data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def click_save(self):
        """Click the Atlas save button and process the resulting Qt events."""
        QTest.mouseClick(self.page.save_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def saved_rarity(self):
        """Read the persisted gear item rarity from logger settings."""
        return logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"]

    def prepare_rarity(self, amount):
        """Save a starting rarity value and assert that the editor is clean."""
        self.page.gear_rarity.setValue(amount)
        self.click_save()
        self.assertFalse(self.page.dirty)

    def test_refresh_between_decimal_characters_preserves_and_saves_point_seventy_five(self):
        """Verify refresh between decimal characters preserves and saves point seventy five."""
        QTest.mouseClick(self.field, Qt.MouseButton.LeftButton)
        QTest.keyClicks(self.field, ".")
        self.assertTrue(self.page.dirty)
        text = self.field.text()
        self.window.refresh()
        self.assertEqual(self.field.text(), text)
        QTest.keyClicks(self.field, "75")
        self.click_save()
        self.assertEqual(self.saved_rarity(), .75)
        self.assertFalse(self.page.dirty)

    def test_unchanged_zero_with_decimal_draft_survives_refresh_until_number_is_complete(self):
        """Verify unchanged zero with decimal draft survives refresh until number is complete."""
        self.prepare_rarity(0)
        QTest.mouseClick(self.field, Qt.MouseButton.LeftButton)
        QTest.keyClick(self.field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(self.field, "0.")
        self.assertTrue(self.page.dirty)
        text = self.field.text()
        self.window.refresh()
        self.assertEqual(self.field.text(), text)
        QTest.keyClicks(self.field, "75")
        self.click_save()
        self.assertEqual(self.saved_rarity(), .75)

    def test_mouse_save_does_not_normalize_incomplete_input_into_the_previous_saved_value(self):
        """Verify mouse save does not normalize incomplete input into the previous saved value."""
        self.prepare_rarity(125)
        before = logger.get_state()["scan_commit_count"]
        QTest.mouseClick(self.field, Qt.MouseButton.LeftButton)
        QTest.keyClick(self.field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(self.field, ".")
        text = self.field.text()
        self.click_save()
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.assertEqual(self.saved_rarity(), 125)
        self.assertEqual(self.field.text(), text)
        self.assertTrue(self.page.dirty)
        self.assertIn("gear item rarity", self.page.status.text().lower())
        self.window.refresh()
        self.assertEqual(self.field.text(), text)
        QTest.keyClicks(self.field, "75")
        self.click_save()
        self.assertEqual(self.saved_rarity(), .75)

    def test_invalid_or_empty_text_cannot_emit_settings_after_focus_loss(self):
        """Verify invalid or empty text cannot emit settings after focus loss."""
        self.prepare_rarity(25)
        for text in (" %", "-0.50 %", "wrong %", "10000 %"):
            with self.subTest(text=text):
                self.page.set_settings(logger.get_state()["settings"]["atlas_settings"], force=True)
                QTest.mouseClick(self.field, Qt.MouseButton.LeftButton)
                self.field.setText(text)
                before = logger.get_state()["scan_commit_count"]
                self.click_save()
                self.assertEqual(logger.get_state()["scan_commit_count"], before)
                self.assertEqual(self.saved_rarity(), 25)
                self.assertEqual(self.field.text(), text)
                self.assertTrue(self.page.dirty)

    def test_explicit_reload_clears_incomplete_draft_and_allows_unknown_value_to_save(self):
        """Verify explicit reload clears incomplete draft and allows unknown value to save."""
        QTest.mouseClick(self.field, Qt.MouseButton.LeftButton)
        QTest.keyClicks(self.field, ".")
        self.assertTrue(self.page.dirty)
        self.page.set_settings({}, force=True)
        self.assertEqual(self.field.text(), "Not set")
        self.assertFalse(self.page.dirty)
        before = logger.get_state()["scan_commit_count"]
        self.click_save()
        self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)
        self.assertIsNone(self.saved_rarity())
        self.assertFalse(self.page.dirty)


if __name__ == "__main__":
    unittest.main()
