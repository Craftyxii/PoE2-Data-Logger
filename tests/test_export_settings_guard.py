"""Excel and CSV exports use saved data without redirecting or consuming Atlas drafts."""

import csv
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ExportSettingsGuardTests(unittest.TestCase):
    """Exercise export buttons and menu actions with valid and incomplete Atlas drafts."""
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

    def trigger_export(self, entrypoint, kind, *, navigate=True):
        """Activate the requested folder button or Save As menu action for an export format."""
        if navigate:
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

    def assert_exported_rarity(self, kind, expected):
        """Reopen the generated CSV or workbook and verify its saved gear rarity."""
        destination = Path(self.results[-1]["path"])
        self.assertTrue(destination.is_file())
        if kind == "csv":
            with destination.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows)
            self.assertEqual({row["Gear Item Rarity %"] for row in rows}, {str(expected)})
            atlas_path = Path(self.results[-1]["atlas_path"])
            history_path = Path(self.results[-1]["history_path"])
            self.assertTrue(atlas_path.is_file())
            self.assertTrue(history_path.is_file())
            with atlas_path.open(encoding="utf-8-sig", newline="") as stream:
                atlas_rows = list(csv.DictReader(stream))
            self.assertIn(str(expected), {row["Gear Item Rarity %"] for row in atlas_rows})
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
                        self.assertEqual(values, {str(expected)})
                    else:
                        self.assertIn(str(expected), values)
            workbook = load_workbook(destination, read_only=True)
            try:
                self.assertEqual(workbook.sheetnames,
                                 ["Export", "Atlas Character Settings", "Scan History", "Analysis Context",
                                  "Family Database", "Recipe Database", "Seed Database", "Reference Aliases"])
                for name in ("Export", "Atlas Character Settings"):
                    rows = workbook[name].iter_rows(values_only=True)
                    column = next(rows).index("Gear Item Rarity %")
                    values = {row[column] for row in rows}
                    self.assertIn(float(expected), values)
            finally:
                workbook.close()

    def check_export_draft(self, entrypoint, kind, *, incomplete=False):
        """Export saved data through the real action while retaining the page and Atlas draft."""
        self.edit_rarity()
        self.page.toggle_node("AtlasExpeditionNotable8")
        if incomplete:
            field = self.page.gear_rarity.lineEdit()
            field.selectAll()
            QTest.keyClicks(field, ".")
            self.assertEqual(field.text(), "." + self.page.gear_rarity.suffix())
        draft = self.page.settings()
        raw_rarity = self.page.gear_rarity.lineEdit().text()
        expected_page = 12 if incomplete else 5
        before = (logger.export_primary_csv(), logger.export_atlas_csv(), logger.export_all_csv())
        destination = self.folder / f"chosen.{kind}"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")) as chooser, \
                patch.object(QMessageBox, "question") as question:
            self.trigger_export(entrypoint, kind, navigate=not incomplete)
            question.assert_not_called()
            self.assertEqual(len(self.submissions), 1)
            self.assertTrue(self.page.dirty)
            self.assertEqual(self.window.tabs.currentIndex(), expected_page)
            self.assertIn("Export saved:", self.window.statusBar().currentMessage())
            self.assertEqual(self.page.settings(), draft)
            self.assertEqual(self.page.gear_rarity.lineEdit().text(), raw_rarity)
            self.assertEqual(logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"], 12)
            self.assertEqual((logger.export_primary_csv(), logger.export_atlas_csv(), logger.export_all_csv()), before)
            if entrypoint == "save_as":
                chooser.assert_called_once()
            else:
                chooser.assert_not_called()
            question.assert_not_called()
        self.assert_exported_rarity(kind, 12)

    def test_folder_csv_preserves_unsaved_atlas_without_redirecting(self):
        """Verify the CSV folder button creates the saved-data export with an Atlas draft."""
        self.check_export_draft("folder", "csv")

    def test_folder_xlsx_preserves_unsaved_atlas_without_redirecting(self):
        """Verify the Excel folder button creates a readable workbook with an Atlas draft."""
        self.check_export_draft("folder", "xlsx")

    def test_save_as_csv_preserves_unsaved_atlas_without_redirecting(self):
        """Verify the CSV menu creates the saved-data export with an Atlas draft."""
        self.check_export_draft("save_as", "csv")

    def test_save_as_csv_preserves_incomplete_rarity(self):
        """Export CSV from the Atlas page's File menu without consuming unfinished rarity text."""
        self.check_export_draft("save_as", "csv", incomplete=True)

    def test_save_as_xlsx_preserves_unsaved_atlas_without_redirecting(self):
        """Verify the Excel menu creates a readable workbook with an Atlas draft."""
        self.check_export_draft("save_as", "xlsx")

    def test_save_as_xlsx_preserves_incomplete_rarity(self):
        """Export from the Atlas page's File menu without consuming unfinished raw rarity text."""
        self.check_export_draft("save_as", "xlsx", incomplete=True)

    def test_cancelled_xlsx_keeps_page_and_unsaved_atlas(self):
        """Cancel the Excel chooser without writing files, redirecting or changing the draft."""
        self.edit_rarity()
        draft = self.page.settings()
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as chooser:
            self.trigger_export("save_as", "xlsx")
        chooser.assert_called_once()
        self.assertEqual(self.window.tabs.currentIndex(), 5)
        self.assertEqual(self.submissions, [])
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertTrue(self.page.dirty)
        self.assertEqual(self.page.settings(), draft)

    def test_explicit_save_is_included_in_next_xlsx(self):
        """Saving the preserved Atlas draft explicitly updates the next workbook's recorded rarity."""
        self.check_export_draft("folder", "xlsx")
        self.window.tabs.setCurrentWidget(self.page)
        self.page.save_button.click()
        self.assertFalse(self.page.dirty)
        self.trigger_export("folder", "xlsx")
        self.assertEqual(self.window.tabs.currentIndex(), 5)
        self.assert_exported_rarity("xlsx", 187.25)

    def check_real_worker_export(self, kind):
        """Complete a spreadsheet button's real worker and Qt callback with an unsaved Atlas draft."""
        self.edit_rarity()
        draft = self.page.settings()
        real_submit = LoggerWindow._submit.__get__(self.window, LoggerWindow)
        real_done = self.window._export_saved

        def record_done(result):
            """Retain the actual worker result and deliver the production export completion."""
            self.results.append(result)
            real_done(result)

        with patch.object(self.window, "_submit", real_submit), \
                patch.object(self.window, "_export_saved", side_effect=record_done):
            self.trigger_export("folder", kind)
            deadline = time.monotonic() + 15
            while self.window._pending_tasks and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(self.window._pending_tasks, {}, self.window.statusBar().currentMessage())
        self.assertIn("Export saved:", self.window.statusBar().currentMessage())
        self.assertEqual(self.window.tabs.currentIndex(), 5)
        self.assertTrue(self.page.dirty)
        self.assertEqual(self.page.settings(), draft)
        self.assertEqual(len(list(self.folder.iterdir())), 3 if kind == "csv" else 1)
        self.assertEqual(len(self.results), 1)
        self.assert_exported_rarity(kind, 12)

    def test_folder_xlsx_real_worker_creates_readable_workbook_without_redirecting(self):
        """Create and independently reopen all Excel sheets through the real worker and Qt callback."""
        self.check_real_worker_export("xlsx")

    def test_folder_csv_real_worker_creates_companions_without_redirecting(self):
        """Create all three CSV files through the real worker and Qt callback with an Atlas draft."""
        self.check_real_worker_export("csv")

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
