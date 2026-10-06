from pathlib import Path
import unittest
from unittest.mock import patch

from tools.verify_installer import check_version, verify


class InstallerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("PoE2-Data-Logger-Setup-v33.34-beta.exe")
        self.metadata = {
            "ProductName": "PoE2 Data Logger 33.34 Beta",
            "ProductVersion": "33.34 Beta",
            "FileVersion": "33.34 Beta",
        }

    def test_beta_release_requires_beta_metadata(self):
        with patch("tools.verify_installer.version_strings", return_value=self.metadata):
            check_version(self.path, "33.34", True)

    def test_stable_metadata_is_rejected_for_beta_release(self):
        metadata = {"ProductName": "PoE2 Data Logger",
                    "ProductVersion": "33.34", "FileVersion": "33.34"}
        with patch("tools.verify_installer.version_strings", return_value=metadata):
            with self.assertRaisesRegex(RuntimeError, "version metadata does not match"):
                check_version(self.path, "33.34", True)

    def test_each_stale_version_label_is_rejected(self):
        for key in self.metadata:
            with self.subTest(key=key):
                metadata = dict(self.metadata, **{key: "33.3"})
                with patch("tools.verify_installer.version_strings", return_value=metadata):
                    with self.assertRaisesRegex(RuntimeError, "version metadata does not match"):
                        check_version(self.path, "33.34", True)

    def test_wrong_installer_filename_is_rejected_before_launch(self):
        with patch("tools.verify_installer.launch") as launch:
            with self.assertRaisesRegex(RuntimeError, "valid release version"):
                verify(Path("PoE2-Data-Logger-Setup.exe"))
            launch.assert_not_called()

    def test_tagged_installer_version_is_checked_before_install(self):
        with patch("tools.verify_installer.version_strings", return_value=self.metadata):
            with patch("tools.verify_installer.launch") as launch:
                with self.assertRaisesRegex(RuntimeError, "version metadata does not match 33.3 Beta"):
                    verify(Path("PoE2-Data-Logger-Setup-v33.3-beta.exe"))
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
