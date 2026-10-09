"""Filesystem fault-injection checks for staging, replacement and rollback across companion export files."""

import errno
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import export_files
from PoE2_Data_Logger.core import logger_store as logger, store


class RelatedExportFilesTests(unittest.TestCase):
    """Inject filesystem failures to check atomic writes and recovery of companion exports."""
    def setUp(self):
        """Create temporary main/Atlas export paths with replacement byte payloads."""
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.main = self.directory / "Export.csv"
        self.atlas = self.directory / "Export_Atlas.csv"
        self.data = {self.main: b"new main", self.atlas: b"new atlas"}

    def tearDown(self):
        """Remove the temporary export directory and its remaining files."""
        self.temporary.cleanup()

    def write_originals(self):
        """Write recognizable original bytes to both companion export destinations."""
        self.main.write_bytes(b"old main")
        self.atlas.write_bytes(b"old atlas")

    def assert_originals(self):
        """Assert both original exports survive with no staging or recovery files."""
        self.assertEqual(self.main.read_bytes(), b"old main")
        self.assertEqual(self.atlas.read_bytes(), b"old atlas")
        self.assertEqual(set(self.directory.iterdir()), {self.main, self.atlas})

    def fail_second_replacement(self, source, destination):
        """Reject staging replacement of the Atlas export while allowing other renames."""
        if Path(destination) == self.atlas and Path(source).suffix == ".tmp":
            raise PermissionError("Atlas export is open in a spreadsheet")
        return self.real_replace(source, destination)

    def test_success_updates_both_files_and_cleans_staging(self):
        """Verify success updates both files and cleans staging."""
        self.write_originals()
        export_files.write_export_files(self.data)
        self.assertEqual(self.main.read_bytes(), b"new main")
        self.assertEqual(self.atlas.read_bytes(), b"new atlas")
        self.assertEqual(set(self.directory.iterdir()), {self.main, self.atlas})

    def test_locked_second_destination_restores_both_original_files(self):
        """Verify locked second destination restores both original files."""
        self.write_originals()
        self.real_replace = os.replace
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaisesRegex(export_files.ExportWriteError, "Previous exports were restored"):
                export_files.write_export_files(self.data)
        self.assert_originals()

    def test_failed_new_pair_leaves_no_partial_export(self):
        """Verify failed new pair leaves no partial export."""
        self.real_replace = os.replace
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaises(export_files.ExportWriteError):
                export_files.write_export_files(self.data)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_failed_mixed_pair_restores_existing_main(self):
        """Verify failed mixed pair restores existing main."""
        self.main.write_bytes(b"old main")
        self.real_replace = os.replace
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaises(export_files.ExportWriteError):
                export_files.write_export_files(self.data)
        self.assertEqual(self.main.read_bytes(), b"old main")
        self.assertEqual(list(self.directory.iterdir()), [self.main])

    def test_staging_disk_full_does_not_replace_any_destination(self):
        """Verify staging disk full does not replace any destination."""
        self.write_originals()
        stage = export_files._stage_bytes

        def fail_second_stage(destination, data):
            """Raise disk-full during Atlas staging while allowing the main file to stage."""
            if destination == self.atlas:
                raise OSError(errno.ENOSPC, "Disk full")
            return stage(destination, data)

        with patch.object(export_files, "_stage_bytes", side_effect=fail_second_stage):
            with self.assertRaisesRegex(export_files.ExportWriteError, "No export files were replaced"):
                export_files.write_export_files(self.data)
        self.assert_originals()

    def test_backup_disk_full_cleans_all_staged_data(self):
        """Verify backup disk full cleans all staged data."""
        self.write_originals()
        real_copy = export_files.shutil.copy2

        def fail_second_backup(source, destination):
            """Raise disk-full while backing up the Atlas destination."""
            if source == self.atlas:
                raise OSError(errno.ENOSPC, "Disk full while making a recovery copy")
            return real_copy(source, destination)

        with patch.object(export_files.shutil, "copy2", side_effect=fail_second_backup):
            with self.assertRaises(export_files.ExportWriteError):
                export_files.write_export_files(self.data)
        self.assert_originals()

    def test_failed_rollback_retains_and_reports_original_recovery_copy(self):
        """Verify failed rollback retains and reports original recovery copy."""
        self.write_originals()
        real_replace = os.replace

        def locked_destination(source, destination):
            """Reject Atlas replacement and recovery renames to simulate a failed rollback."""
            if Path(destination) == self.atlas:
                raise PermissionError("Atlas locked")
            if Path(source).suffix == ".recovery":
                raise PermissionError("Main became locked")
            return real_replace(source, destination)

        with patch.object(export_files.os, "replace", side_effect=locked_destination):
            with self.assertRaises(export_files.ExportWriteError) as raised:
                export_files.write_export_files(self.data)
        recovery = list(self.directory.glob("*.recovery"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].read_bytes(), b"old main")
        self.assertIn(str(recovery[0]), str(raised.exception))
        self.assertEqual(self.main.read_bytes(), b"new main")
        self.assertEqual(self.atlas.read_bytes(), b"old atlas")
        self.assertEqual(set(self.directory.iterdir()), {self.main, self.atlas, recovery[0]})

    def test_equivalent_destinations_rejected_before_writing(self):
        """Verify equivalent destinations rejected before writing."""
        alternate = self.directory / "child" / ".." / self.main.name
        with self.assertRaisesRegex(ValueError, "different destination"):
            export_files.write_export_files({self.main: b"first", alternate: b"second"})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_all_inputs_validated_before_staging(self):
        """Verify all inputs validated before staging."""
        with self.assertRaisesRegex(TypeError, "bytes"):
            export_files.write_export_files({self.main: b"main", self.atlas: "atlas"})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_new_export_cannot_overwrite_a_file_created_before_reservation(self):
        """Verify new export cannot overwrite a file created before reservation."""
        self.atlas.write_bytes(b"other export")
        with self.assertRaises(export_files.ExportWriteError) as raised:
            export_files.write_export_files(self.data, replace=False)
        self.assertIsInstance(raised.exception.__cause__, FileExistsError)
        self.assertEqual(self.atlas.read_bytes(), b"other export")
        self.assertEqual(list(self.directory.iterdir()), [self.atlas])

    def test_failed_new_export_removes_all_reserved_names(self):
        """Verify failed new export removes all reserved names."""
        self.real_replace = os.replace
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaises(export_files.ExportWriteError):
                export_files.write_export_files(self.data, replace=False)
        self.assertEqual(list(self.directory.iterdir()), [])


class SQLiteFolderExportTests(unittest.TestCase):
    """Check complete database exports and preservation of previously reserved filenames."""

    def setUp(self):
        """Initialize an isolated database and configure a separate export directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-sqlite-export-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "data"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        self.directory = Path(self.temporary.name) / "exports"
        self.directory.mkdir()
        logger.save_export_folder(str(self.directory))

    def tearDown(self):
        """Restore the application profile and remove the temporary export files."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def test_sqlite_export_preserves_all_tables_and_spreadsheet_sources(self):
        """Verify the saved file retains activity logs, references, settings and all workbook sources."""
        logger.save_settings({"tier": 16, "waystone": 87, "ocean": True})
        logger.start_map()
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.commit_chain("Rage", "Time")
        logger.complete_chain()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 9}])
        logger.save_ritual_page([
            {"category": "Omen", "name": "Omen of Whittling", "quantity": 2, "tribute": 4000}],
            raw_text="Saved Ritual evidence")
        logger.finish_map(10, 2, 3, 7, unique=4)
        sources = (logger.export_primary_csv, logger.export_atlas_csv, logger.export_all_csv)
        expected_exports = [source() for source in sources]
        with logger._connect() as db:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            expected = {table: sorted((tuple(row) for row in db.execute(f'SELECT * FROM "{table}"')),
                                      key=repr) for table in tables}
        for table in ("new_export", "maps", "map_unique_kills", "expeditions", "chain_completions",
                      "currency_snapshots", "ritual_pages", "commits", "meta"):
            self.assertTrue(expected[table], table)
        result = logger.save_export_file("sqlite3")
        destination = Path(result["path"])
        # Windows may return the long form of a temporary directory's 8.3 alias.
        self.assertEqual(destination.parent.resolve(), self.directory.resolve())
        self.assertEqual(destination.suffix, ".sqlite3")
        self.assertEqual(result["bytes"], destination.stat().st_size)
        self.assertEqual(set(result), {"path", "bytes"})
        with closing(sqlite3.connect(destination)) as restored:
            restored.row_factory = sqlite3.Row
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(tables, [row[0] for row in restored.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
            for table in tables:
                self.assertEqual(expected[table], sorted(
                    (tuple(row) for row in restored.execute(f'SELECT * FROM "{table}"')), key=repr), table)
            self.assertEqual(expected_exports, [source(_db=restored) for source in sources])

    def test_repeated_sqlite_exports_preserve_previous_backup_in_same_second(self):
        """Save changed inventories with one timestamp without replacing the first backup."""
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 7}])
        with patch.object(logger, "export_filename", return_value="PoE2_Export_fixed.sqlite3"):
            first = logger.save_export_file("sqlite3")
            original = Path(first["path"]).read_bytes()
            logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 11}])
            second = logger.save_export_file("sqlite3")
        self.assertEqual(Path(first["path"]).name, "PoE2_Export_fixed.sqlite3")
        self.assertEqual(Path(second["path"]).name, "PoE2_Export_fixed_2.sqlite3")
        self.assertEqual(Path(first["path"]).read_bytes(), original)
        for result, quantity in ((first, 7), (second, 11)):
            with closing(sqlite3.connect(result["path"])) as db:
                items = json.loads(db.execute(
                    "SELECT items_json FROM currency_snapshots WHERE phase='end'").fetchone()[0])
                self.assertEqual(items["Chaos Orb"], quantity)

    def test_sqlite_export_skips_existing_symlink(self):
        """Preserve a dangling symlink even when its destination does not exist."""
        reserved = self.directory / "PoE2_Export_fixed.sqlite3"
        target = self.directory / "missing.sqlite3"
        try:
            reserved.symlink_to(target)
        except OSError as error:
            self.skipTest(f"Symlinks are unavailable: {error}")
        with patch.object(logger, "export_filename", return_value=reserved.name):
            result = logger.save_export_file("sqlite3")
        self.assertEqual(Path(result["path"]).name, "PoE2_Export_fixed_2.sqlite3")
        self.assertTrue(reserved.is_symlink())
        self.assertFalse(target.exists())

    def test_sqlite_export_retries_name_reserved_by_another_writer(self):
        """Keep a competing file created between the existence check and exclusive reservation."""
        write_files = export_files.write_export_files
        competing = self.directory / "PoE2_Export_fixed.sqlite3"

        def reserve_then_write(files, *, replace=True):
            """Simulate one competing reservation before using the actual grouped writer."""
            # Compare filesystem identities after resolving Windows 8.3 aliases.
            if any(Path(path).resolve() == competing.resolve() for path in files):
                competing.write_bytes(b"Other export")
            return write_files(files, replace=replace)

        with patch.object(logger, "export_filename", return_value=competing.name), \
                patch.object(export_files, "write_export_files", side_effect=reserve_then_write):
            result = logger.save_export_file("sqlite3")
        self.assertEqual(competing.read_bytes(), b"Other export")
        self.assertEqual(Path(result["path"]).name, "PoE2_Export_fixed_2.sqlite3")
        with closing(sqlite3.connect(result["path"])) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertFalse(list(self.directory.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
