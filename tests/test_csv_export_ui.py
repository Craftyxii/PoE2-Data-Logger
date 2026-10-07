from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QFileDialog, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


@contextmanager
def export_database():
    database = sqlite3.connect(":memory:")
    try:
        yield database
    finally:
        database.close()


class CsvExportConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-csv-save-as-")
        self.destination = Path(self.temporary.name) / "research.csv"
        self.companion = self.destination.with_name("research_Atlas.csv")
        self.submissions = []
        self.saved = []
        self.window = SimpleNamespace(_export_saved=self.saved.append)
        self.window._submit = self.submit

    def tearDown(self):
        self.temporary.cleanup()

    def submit(self, label, work, done):
        self.submissions.append(label)
        done(work())

    def test_declining_companion_overwrite_preserves_both_files_and_submits_no_task(self):
        self.destination.write_bytes(b"previous main")
        self.companion.write_bytes(b"previous Atlas")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.destination), "CSV (*.csv)")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            LoggerWindow.save_as(self.window, "csv")
        self.assertEqual(self.destination.read_bytes(), b"previous main")
        self.assertEqual(self.companion.read_bytes(), b"previous Atlas")
        self.assertEqual(self.submissions, [])
        self.assertEqual(self.saved, [])
        question.assert_called_once()
        arguments = question.call_args.args
        self.assertIn(str(self.companion), arguments[2])
        self.assertEqual(arguments[4], QMessageBox.StandardButton.No)

    def test_approving_existing_companion_writes_both_exports(self):
        self.destination.write_bytes(b"previous main")
        self.companion.write_bytes(b"previous Atlas")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.destination), "CSV (*.csv)")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question, \
                patch.object(logger, "_connect", export_database), \
                patch.object(logger, "export_all_csv", return_value=b"new main") as main_export, \
                patch.object(logger, "export_atlas_csv", return_value=b"new Atlas") as atlas_export:
            LoggerWindow.save_as(self.window, "csv")
        question.assert_called_once()
        self.assertEqual(self.destination.read_bytes(), b"new main")
        self.assertEqual(self.companion.read_bytes(), b"new Atlas")
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(self.saved[0]["atlas_path"], str(self.companion))
        self.assertIs(main_export.call_args.kwargs["_db"], atlas_export.call_args.kwargs["_db"])

    def test_new_pair_is_saved_without_overwrite_question(self):
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.destination), "CSV (*.csv)")) as chooser, \
                patch.object(QMessageBox, "question") as question, \
                patch.object(logger, "_connect", export_database), \
                patch.object(logger, "export_all_csv", return_value=b"new main"), \
                patch.object(logger, "export_atlas_csv", return_value=b"new Atlas"):
            LoggerWindow.save_as(self.window, "csv")
        question.assert_not_called()
        self.assertIn("Atlas companion", chooser.call_args.args[1])
        self.assertEqual(self.destination.read_bytes(), b"new main")
        self.assertEqual(self.companion.read_bytes(), b"new Atlas")

    def test_canceling_file_chooser_does_not_prompt_or_submit(self):
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), \
                patch.object(QMessageBox, "question") as question:
            LoggerWindow.save_as(self.window, "csv")
        question.assert_not_called()
        self.assertEqual(self.submissions, [])


if __name__ == "__main__":
    unittest.main()
