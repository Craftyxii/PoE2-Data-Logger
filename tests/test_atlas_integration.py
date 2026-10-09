"""Desktop integration checks for Atlas navigation, saved map snapshots, typed rarity and linked export outputs."""

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
    """Exercise Atlas settings navigation, persistence, map snapshots and linked desktop exports."""
    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by Atlas desktop widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated map database and desktop window with polling disabled."""
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
        """Close desktop workers, restore the data directory and remove temporary storage."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def configure(self, rarity=125.5):
        """Allocate an Expedition choice and save the requested character rarity through the UI."""
        node_id = "AtlasExpeditionNotable8"
        self.page.toggle_node(node_id)
        choice_id = catalog()["nodes"][node_id]["choices"][0]["id"]
        self.page.choice_combo.setCurrentIndex(self.page.choice_combo.findData(choice_id))
        self.page.gear_rarity.setValue(rarity)
        self.page.save_button.click()
        self.assertFalse(self.page.dirty)
        return node_id, choice_id

    def test_separate_navigation_and_save_signal(self):
        """Verify Atlas navigation titles, active buttons and saved settings reach the logger."""
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
        """Verify help exposes the Atlas source attribution notice."""
        picker = self.window.license_picker
        notice = "atlas/NOTICE.txt"
        index = picker.findData(notice)
        self.assertGreaterEqual(index, 0)
        picker.setCurrentIndex(index)
        text = self.window.license_view.toPlainText()
        self.assertIn("Grinding Gear Games", text)
        self.assertIn("RePoE", text)

    def test_other_scan_refresh_keeps_unsaved_atlas_draft(self):
        """Verify unrelated settings refreshes preserve unsaved Atlas edits."""
        self.page.toggle_node("AtlasExpeditionNotable8")
        self.page.gear_rarity.setValue(75)
        before = self.page.settings()
        logger.save_settings({"wisp": True})
        self.window.refresh()
        self.assertTrue(self.page.dirty)
        self.assertEqual(self.page.settings(), before)
        self.assertTrue(self.window.header_wisp.isChecked())

    def test_real_six_choice_node_keeps_effects_and_draft_across_pages(self):
        """Exercise every dropdown state through the shown HUD and reopen its saved settings."""
        self.window.show()
        self.window.nav_buttons[-1].click()
        self.app.processEvents()
        node_id = "AtlasExpeditionNotable1"
        node = catalog()["nodes"][node_id]
        self.page.activity_filter.setCurrentIndex(self.page.activity_filter.findData("Expedition"))
        self.app.processEvents()
        badge = self.page.node_items[node_id].choice_badge
        QTest.mouseClick(self.page.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.page.view.mapFromScene(badge.scenePos()))
        self.app.processEvents()
        self.assertIn(node_id, self.page.settings()["allocated"])
        combo = self.page.choice_combo
        self.assertEqual(combo.count(), 7)
        for number in range(7):
            with self.subTest(number=number):
                if not combo.view().isVisible():
                    QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=combo.rect().center())
                self.app.processEvents()
                QTest.keyClick(combo.view(), Qt.Key.Key_Home)
                for _ in range(number):
                    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
                QTest.keyClick(combo.view(), Qt.Key.Key_Return)
                self.app.processEvents()
                text = self.page.node_effects.toPlainText()
                for option_number, option in enumerate(node["choices"], 1):
                    self.assertIn(f"{option_number}. {option['name']}", text)
                    for effect in option["effects"]:
                        self.assertIn(effect, text)
                if number:
                    self.assertEqual(self.page.settings()["choices"][node_id],
                                     node["choices"][number - 1]["id"])
                    self.assertEqual(self.page.node_items[node_id].choice_number, number)
                else:
                    self.assertNotIn(node_id, self.page.settings()["choices"])
                    self.assertIsNone(self.page.node_items[node_id].choice_number)
        before = self.page.settings()
        self.window.tabs.setCurrentIndex(0)
        self.window.refresh()
        self.window.nav_buttons[-1].click()
        self.app.processEvents()
        self.assertEqual(self.page.settings(), before)
        self.assertTrue(self.page.dirty)
        QTest.mouseClick(self.page.save_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.page.dirty)
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.page = self.window.atlas_settings_page
        self.window.show()
        self.window.nav_buttons[-1].click()
        self.app.processEvents()
        self.assertEqual(self.page.settings(), before)
        self.assertEqual(self.page.node_items[node_id].choice_number, 6)

    def test_saved_change_applies_to_next_map_and_preserves_old_rarity(self):
        """Verify new rarity settings affect the next map while prior currency rows retain their snapshot."""
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
        """Verify typed rarity persists and links matching values across the main and Atlas exports."""
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

    def test_csv_save_as_writes_linked_companions(self):
        """Verify CSV Save As writes Atlas and scan-history companions linked to the main record."""
        self.configure(0)
        destination = Path(self.tmp.name) / "my-log.csv"
        self.window._submit = lambda label, work, done: done(work())
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "CSV (*.csv)")):
            self.window.save_as("csv")
        main = list(csv.DictReader(io.StringIO(destination.read_text(encoding="utf-8-sig"))))
        companion = destination.with_name("my-log_Atlas.csv")
        atlas = list(csv.DictReader(io.StringIO(companion.read_text(encoding="utf-8-sig"))))
        record = next(row for row in main if row["Map ID"] == "M0001")
        matching = [row for row in atlas if row["Atlas Setup ID"] == record["Atlas Setup ID"]]
        self.assertEqual(len(matching), 1)
        self.assertTrue(all("M0001" in row["Map IDs"] for row in matching))
        self.assertEqual({row["Gear Item Rarity %"] for row in matching}, {"0"})
        history = list(csv.DictReader(io.StringIO(destination.with_name("my-log_Scan_History.csv")
                                                 .read_text(encoding="utf-8-sig"))))
        saved = next(row for row in history if row["Type"] == "Atlas settings")
        self.assertEqual(saved["Atlas Setup ID"], record["Atlas Setup ID"])


if __name__ == "__main__":
    unittest.main()
