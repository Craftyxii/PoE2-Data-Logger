import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.atlas_catalog import catalog
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class AtlasIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-atlas-integration-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.page = self.window.atlas_settings_page

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def configure(self, rarity=125.5):
        node_id = "AtlasExpeditionNotable8"
        self.page.toggle_node(node_id)
        choice_id = catalog()["nodes"][node_id]["choices"][0]["id"]
        self.page.choice_combo.setCurrentIndex(self.page.choice_combo.findData(choice_id))
        self.page.gear_rarity.setValue(rarity)
        self.page.save_button.click()
        self.assertFalse(self.page.dirty)
        return node_id, choice_id

    def test_separate_navigation_and_save_signal(self):
        self.assertEqual(self.window.tabs.tabText(3), "Atlas Masters")
        self.assertEqual(self.window.tabs.tabText(12), "Atlas / Character Settings")
        self.window.nav_buttons[-1].click()
        self.assertEqual(self.window.tabs.currentIndex(), 12)
        self.assertEqual(self.window.nav_buttons[-1].property("active"), "true")
        node_id, choice_id = self.configure()
        config = logger.get_state()["settings"]["atlas_settings"]
        self.assertEqual(config["allocated"], [node_id])
        self.assertEqual(config["choices"], {node_id: choice_id})
        self.assertEqual(config["gear_item_rarity"], 125.5)
        self.assertIn("M0001", self.page.status.text())
        self.window.tabs.setCurrentIndex(7)
        self.assertEqual(self.window.page_title.text(), "Scan regions")
        self.window.tabs.setCurrentIndex(9)
        self.assertEqual(self.window.page_title.text(), "README")

    def test_atlas_source_notice_is_accessible_in_help(self):
        picker = self.window.license_picker
        notice = "atlas/NOTICE.txt"
        index = picker.findData(notice)
        self.assertGreaterEqual(index, 0)
        picker.setCurrentIndex(index)
        text = self.window.license_view.toPlainText()
        self.assertIn("Grinding Gear Games", text)
        self.assertIn("RePoE", text)

    def test_other_scan_refresh_keeps_unsaved_atlas_draft(self):
        self.page.toggle_node("AtlasExpeditionNotable8")
        self.page.gear_rarity.setValue(75)
        before = self.page.settings()
        logger.save_settings({"wisp": True})
        self.window.refresh()
        self.assertTrue(self.page.dirty)
        self.assertEqual(self.page.settings(), before)
        self.assertTrue(self.window.header_wisp.isChecked())

    def test_saved_change_applies_to_next_map_and_preserves_old_rarity(self):
        self.configure(100)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        self.page.gear_rarity.setValue(200)
        self.page.save_button.click()
        self.assertIn("M0002", self.page.status.text())
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 2}])
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        inventory = [row for row in rows if row["Map ID"] == "M0001" and row["Type"] == "Currency"]
        self.assertTrue(inventory)
        self.assertEqual({row["Gear Item Rarity %"] for row in inventory}, {"100"})
        logger.finish_map(0, 0, 0, 0)
        logger.start_map()
        self.window.refresh()
        self.assertEqual(logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"], 200)

    def test_typed_gear_rarity_saves_and_reaches_both_export_sheets(self):
        self.window.show()
        self.window.tabs.setCurrentIndex(12)
        self.app.processEvents()
        field = self.page.gear_rarity.lineEdit()
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        QTest.keyClicks(field, "187.25")
        QTest.mouseClick(self.page.save_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.page.dirty)
        self.assertEqual(logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"], 187.25)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        main = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        inventory = next(row for row in main if row["Type"] == "Currency")
        self.assertEqual(inventory["Gear Item Rarity %"], "187.25")
        atlas = list(csv.DictReader(io.StringIO(logger.export_atlas_csv().decode("utf-8-sig"))))
        matching = [row for row in atlas if row["Atlas Setup ID"] == inventory["Atlas Setup ID"]]
        self.assertTrue(matching)
        self.assertEqual({row["Gear Item Rarity %"] for row in matching}, {"187.25"})
        self.window.refresh()
        self.assertEqual(self.page.gear_rarity.value(), 187.25)

    def test_csv_save_as_writes_linked_companion(self):
        self.configure(0)
        destination = Path(self.tmp.name) / "my-log.csv"
        self.window._submit = lambda label, work, done: done(work())
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "CSV (*.csv)")):
            self.window.save_as("csv")
        main = list(csv.DictReader(io.StringIO(destination.read_text(encoding="utf-8-sig"))))
        companion = destination.with_name("my-log_Atlas.csv")
        atlas = list(csv.DictReader(io.StringIO(companion.read_text(encoding="utf-8-sig"))))
        record = next(row for row in main if row["Type"] == "Atlas settings")
        matching = [row for row in atlas if row["Atlas Setup ID"] == record["Atlas Setup ID"]]
        self.assertEqual(len(matching), 530)
        self.assertTrue(all("M0001" in row["Map IDs"] for row in matching))
        self.assertEqual({row["Gear Item Rarity %"] for row in matching}, {"0"})


if __name__ == "__main__":
    unittest.main()
