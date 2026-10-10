"""Preserve export destination symlinks when a companion file cannot be saved."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import export_files


class ExportLinkRecoveryTests(unittest.TestCase):
    """Exercise rollback and recovery copies for linked export destinations."""

    def setUp(self):
        """Create a relative destination link and a companion export in an isolated folder."""
        temporary = tempfile.TemporaryDirectory(prefix="poe2-export-link-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "original.csv"
        self.target.write_bytes(b"old main")
        self.main = self.directory / "Export.csv"
        self.atlas = self.directory / "Export_Atlas.csv"
        self.atlas.write_bytes(b"old atlas")
        try:
            self.main.symlink_to(self.target.name)
        except OSError as error:
            self.skipTest(f"Symlinks are unavailable: {error}")
        self.data = {self.main: b"new main", self.atlas: b"new atlas"}
        self.real_replace = os.replace

    def fail_second_replacement(self, source, destination):
        """Reject the companion staged file while allowing replacement and rollback of the link."""
        if Path(destination) == self.atlas and Path(source).suffix == ".tmp":
            raise PermissionError("Atlas export is open")
        return self.real_replace(source, destination)

    def test_failed_companion_save_restores_relative_symlink(self):
        """Restore the original link itself so later target edits remain visible through it."""
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaisesRegex(export_files.ExportWriteError, "Previous exports were restored"):
                export_files.write_export_files(self.data)
        self.assertTrue(self.main.is_symlink())
        self.assertEqual(os.readlink(self.main), self.target.name)
        self.assertEqual(self.target.read_bytes(), b"old main")
        self.target.write_bytes(b"updated original")
        self.assertEqual(self.main.read_bytes(), b"updated original")
        self.assertEqual(self.atlas.read_bytes(), b"old atlas")
        self.assertEqual(set(self.directory.iterdir()), {self.target, self.main, self.atlas})

    def test_failed_link_rollback_retains_recoverable_symlink(self):
        """Retain the original link as the reported recovery copy if restoring it is blocked."""
        def fail_rollback(source, destination):
            """Reject companion replacement and restoration of the original export link."""
            if Path(source).suffix == ".recovery":
                raise PermissionError("Main export became locked")
            return self.fail_second_replacement(source, destination)

        with patch.object(export_files.os, "replace", side_effect=fail_rollback):
            with self.assertRaises(export_files.ExportWriteError) as raised:
                export_files.write_export_files(self.data)
        recovery = list(self.directory.glob("*.recovery"))
        self.assertEqual(len(recovery), 1)
        self.assertTrue(recovery[0].is_symlink())
        self.assertEqual(os.readlink(recovery[0]), self.target.name)
        self.assertIn(str(recovery[0]), str(raised.exception))
        self.real_replace(recovery[0], self.main)
        self.assertTrue(self.main.is_symlink())
        self.assertEqual(self.main.read_bytes(), b"old main")

    def test_failed_companion_save_restores_dangling_link(self):
        """Allow replacing a dangling link while preserving it if a later replacement fails."""
        self.target.unlink()
        with patch.object(export_files.os, "replace", side_effect=self.fail_second_replacement):
            with self.assertRaisesRegex(export_files.ExportWriteError, "Previous exports were restored"):
                export_files.write_export_files(self.data)
        self.assertTrue(self.main.is_symlink())
        self.assertEqual(os.readlink(self.main), self.target.name)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.atlas.read_bytes(), b"old atlas")
        self.assertEqual(set(self.directory.iterdir()), {self.main, self.atlas})


if __name__ == "__main__":
    unittest.main()
