"""Qt checks separating locked saved currency/Ritual previews from the next editable scan review."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTableWidget

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class SavedSnapshotReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-saved-snapshot-")
        self.original_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.original_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def inventory(self, quantity=14, live=False):
        self.window._inventory_read({"items": [
            {"slot": 1, "name": "Simulacrum Splinter", "quantity": quantity}],
            "unknown": []}, live=live)
        self.app.processEvents()

    def ritual(self, quantity=1, live=False):
        self.window._ritual_read({"items": [
            {"category": "Item", "name": "Chaos Orb", "quantity": quantity,
             "tribute": 100, "source": "Chaos Orb", "score": 1}],
            "raw_text": "Chaos Orb", "unmatched": [],
            "tribute_available": 5430, "rerolls_remaining": 2}, live=live)
        self.app.processEvents()

    def assert_inventory_saved_preview(self, quantity):
        table = self.window.inventory_table
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertFalse(self.window.inventory_add_row_button.isEnabled())
        self.assertFalse(table.cellWidget(0, 3).isEnabled())
        self.assertTrue(self.window.approve_scan_button.isHidden())
        table.setCurrentCell(0, 2)
        table.setFocus()
        QTest.keyClicks(table, "15")
        self.app.processEvents()
        self.assertEqual(table.item(0, 2).text(), str(quantity))
        self.window.inventory_add_row_button.click()
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Simulacrum Splinter": quantity})

    def assert_ritual_saved_preview(self, quantity):
        table = self.window.ritual_table
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertFalse(self.window.ritual_add_row_button.isEnabled())
        self.assertFalse(self.window.ritual_remove_row_button.isEnabled())
        self.assertTrue(self.window.ritual_tribute.isReadOnly())
        self.assertTrue(self.window.ritual_rerolls.isReadOnly())
        self.assertFalse(table.item(0, 5).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        table.setCurrentCell(0, 5)
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(table.item(0, 5).checkState(), Qt.CheckState.Unchecked)
        self.window.ritual_add_row_button.click()
        self.window.ritual_remove_row_button.click()
        self.assertEqual(table.rowCount(), 1)
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual(pages[0]["items"][0]["quantity"], quantity)
        self.assertFalse(pages[0]["items"][0]["deferred"])

    def test_manual_inventory_save_locks_preview_and_next_scan_restores_editing(self):
        self.inventory()
        self.window.approve_scan_button.click()
        self.assert_inventory_saved_preview(14)
        self.inventory(16)
        self.assertNotEqual(self.window.inventory_table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertTrue(self.window.inventory_add_row_button.isEnabled())
        self.assertTrue(self.window.inventory_table.cellWidget(0, 3).isEnabled())
        self.window.inventory_table.item(0, 2).setText("17")
        self.window.approve_scan_button.click()
        self.assert_inventory_saved_preview(17)
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)

    def test_auto_inventory_save_locks_preview(self):
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.inventory(live=True)
        self.assert_inventory_saved_preview(14)

    def test_manual_ritual_save_locks_preview_and_next_scan_restores_editing(self):
        self.ritual()
        self.window.approve_scan_button.click()
        self.assert_ritual_saved_preview(1)
        self.ritual(2)
        table = self.window.ritual_table
        self.assertNotEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertTrue(self.window.ritual_add_row_button.isEnabled())
        self.assertTrue(self.window.ritual_remove_row_button.isEnabled())
        self.assertFalse(self.window.ritual_tribute.isReadOnly())
        self.assertFalse(self.window.ritual_rerolls.isReadOnly())
        self.assertTrue(table.item(0, 5).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        table.item(0, 5).setCheckState(Qt.CheckState.Checked)
        table.item(0, 2).setText("3")
        self.window.approve_scan_button.click()
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual(pages[-1]["items"][0]["quantity"], 3)
        self.assertTrue(pages[-1]["items"][0]["deferred"])

    def test_auto_ritual_save_locks_preview(self):
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.ritual(live=True)
        self.assert_ritual_saved_preview(1)

    def test_loading_and_switching_review_disable_unavailable_edits(self):
        self.inventory()
        self.window._review_pending("currency", "Reading inventory…", False)
        self.assertFalse(self.window.inventory_add_row_button.isEnabled())
        self.assertEqual(self.window.inventory_table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.ritual()
        self.assertFalse(self.window.inventory_add_row_button.isEnabled())
        self.assertTrue(self.window.ritual_add_row_button.isEnabled())
        self.window.reject_review()
        self.assertFalse(self.window.ritual_add_row_button.isEnabled())
        self.assertTrue(self.window.ritual_tribute.isReadOnly())


if __name__ == "__main__":
    unittest.main()
