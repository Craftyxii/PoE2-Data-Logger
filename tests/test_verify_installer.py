"""Installer metadata, source-guard and mocked launch checks; native installation runs in the separate Windows verification tool."""

import ast
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.verify_installer import check_version, running_client_titles, verify, verify_blocked_launch


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

    def test_stable_and_beta_two_to_four_part_versions_reach_metadata_check(self):
        for version in ("1.2", "1.1", "33.3", "33.34", "33.34.1", "1.3.1", "1.3.1.1", "33.34.1.0"):
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
        for version in ("33", "33.34.", "33.34.1.0.1", "1..3.1", "1.3.1.1.",
                        "33.34.1-rc", "33.34.1-beta-beta"):
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
        parts = tuple(map(int, version.split(".")))
        self.assertEqual(numeric, parts + (0,) * (4 - len(parts)))
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

    def test_release_workflow_accepts_two_to_four_part_versions(self):
        workflow = (self.root / ".github/workflows/release.yml").read_text(encoding="utf-8")
        pattern = next(line.split("-Pattern '", 1)[1].rsplit("'", 1)[0]
                       for line in workflow.splitlines() if "$match = Select-String" in line)
        for version in ("1.2", "1.1", "33.3", "33.34", "33.34.1", "1.3.1", "1.3.1.1", "33.34.1.0"):
            with self.subTest(version=version):
                match = re.fullmatch(pattern, f'!define APP_VERSION "{version}"')
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), version)
        for version in ("33", "33.34.", "33.34.1.0.1", "1..3.1", "1.3.1.1.", "33.34.1-beta"):
            with self.subTest(version=version):
                self.assertIsNone(re.fullmatch(pattern, f'!define APP_VERSION "{version}"'))

    def test_install_and_uninstall_guard_previous_activity_beta_clients(self):
        installer = (self.root / "packaging/installer.nsi").read_text(encoding="utf-8")
        for name in (".onInit", "un.onInit"):
            with self.subTest(function=name):
                body = installer.split(f"Function {name}\n", 1)[1].split("FunctionEnd", 1)[0]
                for title in ("PoE2 Data Logger 1.2 Beta", "PoE2 Data Logger 1.2.1 Beta", "PoE2 Data Logger 1.2.2 Beta", "PoE2 Data Logger 1.3 Beta", "PoE2 Data Logger 1.3.1 Beta", "PoE2 Data Logger 1.3.1.1 Beta", "PoE2 Data Logger 1.3.1.2 Beta", "${APP_NAME}"):
                    self.assertIn(f'FindWindowW(p 0, w "{title}")', body)
                running = body.split("  running:\n", 1)[1].split("  ready:", 1)[0]
                self.assertIn("/SD IDOK", running)
                self.assertIn("SetErrorLevel 2\n    Abort", running)

    def test_native_guard_probe_includes_previous_release_and_current_beta(self):
        titles = running_client_titles("1.3.1.2", True)
        self.assertEqual(titles, (
            "PoE2 Data Logger 1.2 Beta", "PoE2 Data Logger 1.2.1 Beta",
            "PoE2 Data Logger 1.2.2 Beta", "PoE2 Data Logger 1.3 Beta",
            "PoE2 Data Logger 1.3.1 Beta", "PoE2 Data Logger 1.3.1.1 Beta",
            "PoE2 Data Logger 1.3.1.2 Beta",
        ))
        self.assertEqual(running_client_titles("1.3.1.2", False)[-1], "PoE2 Data Logger")
        prior = running_client_titles("1.3.1", True)
        self.assertEqual(prior.count("PoE2 Data Logger 1.3.1 Beta"), 1)
        current = running_client_titles("1.3.1.3", True)
        self.assertIn("PoE2 Data Logger 1.3.1.2 Beta", current)
        self.assertEqual(current[-1], "PoE2 Data Logger 1.3.1.3 Beta")


class RunningClientGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.executable = self.directory / "installer.exe"
        self.protected = (self.directory / "client.exe", self.directory / "runtime-sentinel.bin")
        for path in self.protected:
            path.write_bytes(b"retained client runtime")
        self.check_saved = Mock()

    def test_blocked_operation_requires_nonzero_exit_and_retains_runtime_and_saved_files(self):
        with patch("tools.verify_installer.subprocess.run", return_value=Mock(returncode=2)) as run:
            verify_blocked_launch(self.executable, "/S", self.protected, self.check_saved)
        run.assert_called_once_with(f'"{self.executable}" /S', timeout=20, check=False)
        self.check_saved.assert_called_once_with()

    def test_successful_operation_with_a_running_client_is_rejected(self):
        with patch("tools.verify_installer.subprocess.run", return_value=Mock(returncode=0)):
            with self.assertRaisesRegex(RuntimeError, "allowed an operation"):
                verify_blocked_launch(self.executable, "/S", self.protected, self.check_saved)
        self.check_saved.assert_not_called()

    def test_runtime_replacement_or_deletion_is_rejected_even_after_nonzero_exit(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                self.protected[1].write_bytes(b"retained client runtime")
                def mutate(*args, **kwargs):
                    if remove:
                        self.protected[1].unlink()
                    else:
                        self.protected[1].write_bytes(b"changed runtime")
                    return Mock(returncode=2)
                with patch("tools.verify_installer.subprocess.run", side_effect=mutate):
                    with self.assertRaisesRegex(RuntimeError, "retained runtime"):
                        verify_blocked_launch(self.executable, "/S", self.protected, self.check_saved)
        self.check_saved.assert_not_called()

    def test_saved_file_damage_is_rejected(self):
        self.check_saved.side_effect = RuntimeError("saved user files changed")
        with patch("tools.verify_installer.subprocess.run", return_value=Mock(returncode=2)):
            with self.assertRaisesRegex(RuntimeError, "saved user files changed"):
                verify_blocked_launch(self.executable, "/S", self.protected, self.check_saved)

    def test_hung_operation_times_out(self):
        with patch("tools.verify_installer.subprocess.run", side_effect=subprocess.TimeoutExpired("installer", 20)):
            with self.assertRaises(subprocess.TimeoutExpired):
                verify_blocked_launch(self.executable, "/S", self.protected, self.check_saved)
        self.check_saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
