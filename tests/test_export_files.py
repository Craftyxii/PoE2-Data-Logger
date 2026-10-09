"""Filesystem fault-injection checks for staging, replacement and rollback across companion export files."""

import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import export_files


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


if __name__ == "__main__":
    unittest.main()
