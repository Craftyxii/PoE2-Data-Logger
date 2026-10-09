"""End-user export destinations must never replace the active logging database."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ActiveLoggingDatabaseExportTests(unittest.TestCase):
    """Exercise real Save As actions against an isolated profile with approved data."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-active-db-export-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7}],
                                     "unknown": []}, live=False)
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def trigger_save_as(self, kind, destination):
        labels = {"csv": "Save Export CSV as…", "xlsx": "Save Export XLSX as…",
                  "backup": "Save database backup as…"}
        menu_action = self.window.menuBar().actions()[0]
        file_menu = menu_action.menu()
        action = next(action for action in file_menu.actions() if action.text() == labels[kind])
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            action.trigger()
        self.app.processEvents()

    def assert_active_database_preserved(self, destination):
        before = (store.DATA_DIR / "scans.sqlite3").read_bytes()
        for kind in ("backup", "csv", "xlsx"):
            with self.subTest(kind=kind):
                self.trigger_save_as(kind, destination)
                self.assertEqual(self.jobs, [], "An export must reject the active database before queueing")
                self.assertIn("database", self.window.statusBar().currentMessage().lower())
                self.assertEqual((store.DATA_DIR / "scans.sqlite3").read_bytes(), before)
                self.assertEqual(logger.get_state()["scan_commit_count"], 1)
                self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})

    def test_save_as_rejects_active_database_for_every_format(self):
        """A backup snapshot or spreadsheet must not overwrite data still receiving logs."""
        self.assert_active_database_preserved(store.DATA_DIR / "scans.sqlite3")

    def test_save_as_rejects_active_database_through_directory_alias(self):
        """A directory alias resolves to the same database and needs the same protection."""
        alias = Path(self.temporary.name) / "profile-alias"
        try:
            alias.symlink_to(store.DATA_DIR, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks are unavailable: {error}")
        self.assert_active_database_preserved(alias / "scans.sqlite3")

    def test_separate_database_backup_preserves_later_live_approvals(self):
        """A chosen backup is an independent snapshot while ongoing logging remains writable."""
        destination = Path(self.temporary.name) / "approved-backup.sqlite3"
        self.trigger_save_as("backup", destination)
        self.assertEqual(len(self.jobs), 1)
        work, done = self.jobs.pop()
        result = work()
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 9}],
                                     "unknown": []}, live=False)
        self.window.approve_scan_button.click()
        done(result)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 9})
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        # SQLite transaction contexts do not close handles; Windows needs the
        # explicit close before the temporary backup can be removed.
        with closing(sqlite3.connect(destination)) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT count(*) FROM commits").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
