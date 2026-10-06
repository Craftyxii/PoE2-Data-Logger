import ast
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from tools.verify_installer import check_version, verify


class InstallerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("PoE2-Data-Logger-Setup-v33.34.1-beta.exe")
        self.metadata = {
            "ProductName": "PoE2 Data Logger 33.34.1 Beta",
            "ProductVersion": "33.34.1 Beta",
            "FileVersion": "33.34.1 Beta",
        }

    def test_beta_release_requires_beta_metadata(self):
        with patch("tools.verify_installer.version_strings", return_value=self.metadata):
            check_version(self.path, "33.34.1", True)

    def test_stable_metadata_is_rejected_for_beta_release(self):
        metadata = {"ProductName": "PoE2 Data Logger",
                    "ProductVersion": "33.34.1", "FileVersion": "33.34.1"}
        with patch("tools.verify_installer.version_strings", return_value=metadata):
            with self.assertRaisesRegex(RuntimeError, "version metadata does not match"):
                check_version(self.path, "33.34.1", True)

    def test_each_stale_version_label_is_rejected(self):
        for key in self.metadata:
            with self.subTest(key=key):
                metadata = dict(self.metadata, **{key: "33.3"})
                with patch("tools.verify_installer.version_strings", return_value=metadata):
                    with self.assertRaisesRegex(RuntimeError, "version metadata does not match"):
                        check_version(self.path, "33.34.1", True)

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

    def test_stable_and_beta_versions_with_or_without_patch_reach_metadata_check(self):
        for version in ("33.3", "33.34", "33.34.1"):
            for beta in (False, True):
                with self.subTest(version=version, beta=beta):
                    name = f"PoE2-Data-Logger-Setup-v{version}{'-beta' if beta else ''}.exe"
                    path = Path(name)
                    with patch("tools.verify_installer.check_version", side_effect=RuntimeError("stop before install")) as check:
                        with patch("tools.verify_installer.launch") as launch:
                            with self.assertRaisesRegex(RuntimeError, "stop before install"):
                                verify(path)
                            check.assert_called_once_with(path, version, beta)
                            launch.assert_not_called()

    def test_malformed_patch_version_is_rejected_before_metadata_or_launch(self):
        for version in ("33", "33.34.", "33.34.1.0", "33.34.1-rc", "33.34.1-beta-beta"):
            with self.subTest(version=version):
                with patch("tools.verify_installer.check_version") as check:
                    with patch("tools.verify_installer.launch") as launch:
                        with self.assertRaisesRegex(RuntimeError, "valid release version"):
                            verify(Path(f"PoE2-Data-Logger-Setup-v{version}.exe"))
                        check.assert_not_called()
                        launch.assert_not_called()


class ReleasePackagingTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_installer_and_executable_numeric_and_display_versions_agree(self):
        installer = (self.root / "packaging/installer.nsi").read_text(encoding="utf-8")
        version = re.search(r'^!define APP_VERSION "([0-9.]+)"$', installer, re.MULTILINE).group(1)
        numeric = re.search(r'^VIProductVersion "([^"]+)"$', installer, re.MULTILINE).group(1)
        numeric = tuple(int(part) for part in numeric.replace("${APP_VERSION}", version).split("."))
        self.assertEqual(len(numeric), 4)
        self.assertEqual(numeric, tuple(map(int, version.split("."))) + (0,))
        metadata = ast.parse((self.root / "packaging/version_info.txt").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(metadata) if isinstance(node, ast.Call)]
        fixed = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "FixedFileInfo")
        values = {item.arg: ast.literal_eval(item.value) for item in fixed.keywords}
        self.assertEqual(values["filevers"], numeric)
        self.assertEqual(values["prodvers"], numeric)
        strings = {ast.literal_eval(node.args[0]): ast.literal_eval(node.args[1])
                   for node in calls if isinstance(node.func, ast.Name) and node.func.id == "StringStruct"}
        self.assertEqual(strings["FileVersion"], f"{version} Beta")
        self.assertEqual(strings["ProductVersion"], f"{version} Beta")
        self.assertEqual(strings["ProductName"], f"PoE2 Data Logger {version} Beta")

    def test_release_workflow_accepts_patch_version_and_previous_two_part_versions(self):
        workflow = (self.root / ".github/workflows/release.yml").read_text(encoding="utf-8")
        pattern = next(line.split("-Pattern '", 1)[1].rsplit("'", 1)[0]
                       for line in workflow.splitlines() if "$match = Select-String" in line)
        for version in ("33.3", "33.34", "33.34.1"):
            with self.subTest(version=version):
                match = re.fullmatch(pattern, f'!define APP_VERSION "{version}"')
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), version)
        for version in ("33", "33.34.", "33.34.1.0", "33.34.1-beta"):
            with self.subTest(version=version):
                self.assertIsNone(re.fullmatch(pattern, f'!define APP_VERSION "{version}"'))


if __name__ == "__main__":
    unittest.main()
