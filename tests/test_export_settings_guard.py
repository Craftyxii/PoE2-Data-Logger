"""Qt export checks requiring Atlas/rarity drafts to be saved before CSV/XLSX while allowing database backups."""

import csv
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ExportSettingsGuardTests(unittest.TestCase):
    """Check CSV and workbook exports require saving Atlas drafts while database backups remain available."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger with saved rarity, inline export tasks and a temporary export folder."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-export-settings-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        logger.save_atlas_settings({"allocated": [], "choices": {}, "gear_item_rarity": 12})
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.page = self.window.atlas_settings_page
        self.submissions = []
        self.results = []
        self.window._submit = self.submit
        self.folder = Path(self.temporary.name) / "exports"
        self.folder.mkdir()
        self.window.export_folder.setText(str(self.folder))

    def tearDown(self):
        """Close the logger window, stop workers and restore the original data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def submit(self, label, work, done):
        """Record and run an export task inline, retaining its result before invoking the callback."""
        self.submissions.append(label)
        result = work()
        self.results.append(result)
        done(result)

    def edit_rarity(self):
        """Type a new rarity draft and assert it differs from the persisted Atlas setting."""
        self.window.tabs.setCurrentWidget(self.page)
        self.app.processEvents()
        field = self.page.gear_rarity.lineEdit()
        field.selectAll()
        QTest.keyClicks(field, "187.25")
        self.assertTrue(self.page.dirty)
        self.assertEqual(self.page.settings()["gear_item_rarity"], 187.25)
        self.assertEqual(logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"], 12)

    def trigger_export(self, entrypoint, kind):
        """Activate the requested folder button or Save As menu action for an export format."""
        self.window.tabs.setCurrentIndex(5)
        if entrypoint == "folder":
            self.window.export_folder.setText(str(self.folder))
            button = next(button for button in self.window.findChildren(QPushButton)
                          if button.text() == f"Save {kind.upper()} to folder")
            button.click()
        else:
            menu_action = self.window.menuBar().actions()[0]
            menu = menu_action.menu()
            action = next(action for action in menu.actions()
                          if action.text() == f"Save Export {kind.upper()} as…")
            action.trigger()

    def assert_exported_rarity(self, kind):
        """Read the generated CSV or workbook and assert it contains the newly saved gear rarity."""
        destination = Path(self.results[-1]["path"])
        self.assertTrue(destination.is_file())
        if kind == "csv":
            with destination.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows)
            self.assertEqual({row["Gear Item Rarity %"] for row in rows}, {"187.25"})
        else:
            with ZipFile(destination) as archive:
                for number in (1, 2):
                    sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    rows = sheet.findall("s:sheetData/s:row", ns)
                    headers = {"".join(cell.itertext()): re.sub(r"\d+$", "", cell.get("r"))
                               for cell in rows[0].findall("s:c", ns)}
                    column = headers["Gear Item Rarity %"]
                    values = {"".join(cell.itertext()) for row in rows[1:]
                              for cell in row.findall("s:c", ns)
                              if re.sub(r"\d+$", "", cell.get("r")) == column}
                    if number == 1:
                        self.assertEqual(values, {"187.25"})
                    else:
                        self.assertIn("187.25", values)

    def check_export_guard(self, entrypoint, kind):
        """Assert a rarity draft blocks export, then save it and verify the resulting export."""
        self.edit_rarity()
        destination = self.folder / f"chosen.{kind}"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")) as chooser, \
                patch.object(QMessageBox, "question") as question:
            self.trigger_export(entrypoint, kind)
            chooser.assert_not_called()
            question.assert_not_called()
            self.assertEqual(self.submissions, [])
            self.assertEqual(list(self.folder.iterdir()), [])
            self.assertTrue(self.page.dirty)
            self.assertIs(self.window.tabs.currentWidget(), self.page)
            self.assertIn("Save Atlas / Character Settings", self.window.statusBar().currentMessage())
            self.page.save_button.click()
            self.assertFalse(self.page.dirty)
            self.trigger_export(entrypoint, kind)
            self.assertEqual(len(self.submissions), 1)
            if entrypoint == "save_as":
                chooser.assert_called_once()
            else:
                chooser.assert_not_called()
            question.assert_not_called()
        self.assert_exported_rarity(kind)

    def test_folder_csv_requires_saving_current_rarity(self):
        """Verify folder CSV requires saving current rarity."""
        self.check_export_guard("folder", "csv")

    def test_folder_xlsx_requires_saving_current_rarity(self):
        """Verify folder XLSX requires saving current rarity."""
        self.check_export_guard("folder", "xlsx")

    def test_save_as_csv_requires_saving_current_rarity(self):
        """Verify save as CSV requires saving current rarity."""
        self.check_export_guard("save_as", "csv")

    def test_save_as_xlsx_requires_saving_current_rarity(self):
        """Verify save as XLSX requires saving current rarity."""
        self.check_export_guard("save_as", "xlsx")

    def test_database_backup_remains_available_with_unsaved_settings(self):
        """Verify database backup remains available with unsaved settings."""
        self.edit_rarity()
        destination = self.folder / "backup.sqlite3"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            self.window.save_as("backup")
        self.assertEqual(len(self.submissions), 1)
        self.assertTrue(destination.is_file())
        self.assertTrue(self.page.dirty)


if __name__ == "__main__":
    unittest.main()
