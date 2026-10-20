"""Synthetic helper-build contracts; no Windows build, GPU or package install runs here."""

from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path
from runpy import run_path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyInstaller.archive.readers import CArchiveReader

from tools import build_label_gpu, verify_label_gpu
from tests.test_verify_build import windows_binary


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = run_path(str(ROOT / "packaging" / "gpu_helper.py"))


class GpuHelperPackagingTests(unittest.TestCase):
    """Keep runtime wheels isolated and reject stale or incomplete helper artifacts."""

    def setUp(self):
        """Prepare a tiny synthetic helper and source tree, never the real build directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-gpu-packaging-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "packaging").mkdir()
        shutil.copyfile(ROOT / "packaging" / "gpu_helper.py", self.root / "packaging" / "gpu_helper.py")
        for name in MANIFEST["SOURCE_FILES"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic " + name).encode())
        self.helper = self.root / "dist" / "PoE2LabelGpu"
        self.runtime = self.helper / "_internal"
        self.runtime.mkdir(parents=True)
        self.executable = self.helper / "PoE2LabelGpu.exe"
        self.executable.write_bytes(windows_binary())
        shutil.copyfile(self.root / "PoE2_Data_Logger/ocr/gpu_label_protocol.py",
                        self.runtime / "gpu_label_protocol.py")
        self.info = {"format": 1, "python": [3, 12], "bits": 64,
                     "runtime_versions": dict(MANIFEST["RUNTIME_VERSIONS"]),
                     "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
                     "source_sha256": {name: MANIFEST["sha256"](self.root / name)
                                       for name in MANIFEST["SOURCE_FILES"]},
                     "executable_sha256": MANIFEST["sha256"](self.executable)}
        self.write_info()

    def write_info(self):
        """Write test-only build metadata after a deliberately modified fixture."""
        (self.helper / "build-info.json").write_text(json.dumps(self.info), encoding="utf-8")

    def test_helper_data_uses_trusted_nested_destination(self):
        """Keep the separate runtime under label_gpu rather than the main import directory."""
        self.assertEqual(MANIFEST["helper_bundle_data"](self.root), [(str(self.helper), "label_gpu")])

    def test_missing_and_stale_helper_are_rejected(self):
        """A newly changed protocol cannot ship with an old helper."""
        protocol = self.root / "PoE2_Data_Logger/ocr/gpu_label_protocol.py"
        original = protocol.read_bytes()
        protocol.write_bytes(original + b" changed")
        with self.assertRaisesRegex(ValueError, "stale"):
            MANIFEST["helper_bundle_data"](self.root)
        protocol.write_bytes(original)
        self.executable.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "Build the isolated"):
            MANIFEST["helper_bundle_data"](self.root)

    def test_wrong_runtime_and_modified_executable_or_protocol_are_rejected(self):
        """Do not accept a main-runtime wheel substitution or altered binary/protocol."""
        self.info["runtime_versions"]["onnxruntime-directml"] = "1.22.1"
        self.write_info()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            MANIFEST["helper_bundle_data"](self.root)
        self.info["runtime_versions"] = dict(MANIFEST["RUNTIME_VERSIONS"])
        self.write_info()
        self.executable.write_bytes(self.executable.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            MANIFEST["helper_bundle_data"](self.root)
        self.info["executable_sha256"] = MANIFEST["sha256"](self.executable)
        self.write_info()
        (self.runtime / "gpu_label_protocol.py").write_bytes(b"old protocol")
        with self.assertRaisesRegex(ValueError, "does not match"):
            MANIFEST["helper_bundle_data"](self.root)

    def test_freeze_copies_full_helper_and_preserves_native_bytes(self):
        """Execute the main spec with inert freezer hooks and check helper placement/UPX exclusions."""
        sources = run_path(str(ROOT / "packaging" / "sources.py"))
        shutil.copyfile(ROOT / "packaging" / "sources.py", self.root / "packaging" / "sources.py")
        app = self.root / "PoE2_Data_Logger"
        for name in sources["APP_SOURCES"]:
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(b"")
        for name in sources["FILE_RESOURCES"]:
            (app / name).write_bytes(b"resource")
        for name in sources["DIRECTORY_RESOURCES"]:
            (app / name).mkdir(parents=True, exist_ok=True)
        dll = self.runtime / "DirectML.dll"
        dll.write_bytes(windows_binary())
        analysis = Mock(side_effect=lambda *args, **kwargs: SimpleNamespace(
            pure=[], scripts=[], binaries=kwargs["binaries"], datas=kwargs["datas"]))
        collect = Mock()
        with patch("PyInstaller.utils.hooks.collect_all", return_value=([], [], [])), \
                patch("PyInstaller.utils.hooks.collect_submodules", return_value=[]):
            run_path(str(ROOT / "packaging" / "PoE2-Data-Logger.spec"), init_globals={
                "SPECPATH": str(self.root / "packaging"), "Analysis": analysis,
                "PYZ": Mock(), "EXE": Mock(), "COLLECT": collect})
        self.assertIn((str(self.helper), "label_gpu"), analysis.call_args.kwargs["datas"])
        self.assertIn(str(self.executable), collect.call_args.kwargs["upx_exclude"])
        self.assertIn(str(dll), collect.call_args.kwargs["upx_exclude"])

    def test_helper_freeze_includes_licenses_without_main_app_imports(self):
        """Execute the helper spec with mocked freezing, retaining both runtime license metadata."""
        def version(name):
            if name == "onnxruntime":
                raise metadata.PackageNotFoundError(name)
            return {"numpy": "1.26.4", "onnxruntime-directml": "1.22.0"}[name]
        analysis, copies = Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])), []
        def license_data(name):
            copies.append(name)
            return [(name + "-licenses", name + ".dist-info")]
        with patch("sys.platform", "win32"), patch("importlib.metadata.version", side_effect=version), \
                patch("PyInstaller.utils.hooks.collect_all", return_value=([], [], [])), \
                patch("PyInstaller.utils.hooks.copy_metadata", side_effect=license_data):
            run_path(str(ROOT / "gpu_worker" / "PoE2LabelGpu.spec"), init_globals={
                "SPECPATH": str(self.root / "gpu_worker"), "Analysis": analysis,
                "PYZ": Mock(), "EXE": Mock(), "COLLECT": Mock()})
        self.assertEqual(copies, ["onnxruntime-directml", "numpy"])
        self.assertEqual(analysis.call_args.args[0], [str(self.root / "gpu_worker/worker.py")])
        self.assertIn("PoE2_Data_Logger", analysis.call_args.kwargs["excludes"])
        self.assertIn((str(self.root / "PoE2_Data_Logger/ocr/gpu_label_protocol.py"), "."),
                      analysis.call_args.kwargs["datas"])

    def test_build_install_commands_target_only_the_separate_venv(self):
        """Inspect the command plan without installing wheels or running a freezer."""
        environment = self.root / "build" / "label_gpu_env"
        python = environment / "Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"synthetic venv launcher")
        (environment / "pyvenv.cfg").write_text("synthetic isolated venv")
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            runtime = {key: self.info[key] for key in ("python", "bits", "runtime_versions", "available_providers")}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(runtime))
        self.addCleanup(lambda: sys.path.remove(str(self.root)) if str(self.root) in sys.path else None)
        with patch("sys.platform", "win32"), patch("subprocess.run", side_effect=run), \
                patch("tools.verify_label_gpu.verify", return_value={"synthetic": True}):
            result = build_label_gpu.build(self.root)
        self.assertEqual(result["runtime_versions"], MANIFEST["RUNTIME_VERSIONS"])
        self.assertTrue(all(command[0] == str(python) for command in calls))
        self.assertIn("sys.prefix", calls[0][2])
        installs = [command for command in calls if "pip" in command]
        self.assertEqual(len(installs), 1)
        self.assertIn("--require-virtualenv", installs[0])
        self.assertEqual(installs[0][-1], str(self.root / "gpu_worker/requirements.txt"))

    def test_non_windows_build_fails_before_subprocess_or_install(self):
        """Linux verification cannot be presented as a native Windows build."""
        with patch("sys.platform", "linux"), patch("subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "Windows Python"):
                build_label_gpu.build(self.root)
        run.assert_not_called()


class GpuHelperBinaryVerificationTests(unittest.TestCase):
    """Inspect synthetic PE/PyInstaller layouts without executing Windows code."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-gpu-binary-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "_internal"
        self.runtime.mkdir()
        self.executable = self.root / "PoE2LabelGpu.exe"
        cookie = struct.pack(CArchiveReader._COOKIE_FORMAT, CArchiveReader._COOKIE_MAGIC_PATTERN,
                             88, 0, 0, 312, b"python312.dll")
        self.executable.write_bytes(windows_binary() + cookie)
        self.modules = Mock(); self.modules.toc = {"numpy": None, "onnxruntime": None}
        self.archive = Mock()
        self.archive.options = ["pyi-contents-directory _internal"]
        self.archive.toc = {"worker": None, "PYZ.pyz": None}
        self.archive._end_offset = self.executable.stat().st_size
        self.archive._COOKIE_LENGTH, self.archive._COOKIE_FORMAT = CArchiveReader._COOKIE_LENGTH, CArchiveReader._COOKIE_FORMAT
        self.archive.open_embedded_archive.return_value = self.modules
        reader = patch("tools.verify_label_gpu.CArchiveReader", return_value=self.archive)
        reader.start(); self.addCleanup(reader.stop)
        for name in ("python312.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll",
                     "onnxruntime/capi/DirectML.dll", "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
                     "numpy/core/_multiarray_umath.cp312-win_amd64.pyd"):
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(windows_binary())
        (self.runtime / "base_library.zip").write_bytes(b"stdlib")
        (self.runtime / "gpu_label_protocol.py").write_bytes(b"protocol constants")

    def test_separate_complete_x64_runtime_passes(self):
        self.assertEqual(verify_label_gpu.verify(self.root)["native_files_checked"], 7)

    def test_missing_directml_numpy_or_protocol_is_rejected(self):
        for name in ("onnxruntime/capi/DirectML.dll", "numpy/core/_multiarray_umath.cp312-win_amd64.pyd",
                     "gpu_label_protocol.py"):
            path = self.runtime / name; previous = path.read_bytes(); path.unlink()
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_label_gpu.verify(self.root)
            path.write_bytes(previous)

    def test_main_runtime_imports_or_wrong_native_architecture_are_rejected(self):
        self.modules.toc["PoE2_Data_Logger.ocr.opened_scan"] = None
        with self.assertRaisesRegex(ValueError, "main application"):
            verify_label_gpu.verify(self.root)
        del self.modules.toc["PoE2_Data_Logger.ocr.opened_scan"]
        (self.runtime / "onnxruntime/capi/DirectML.dll").write_bytes(windows_binary(machine=0x14C))
        with self.assertRaisesRegex(ValueError, "non-x64"):
            verify_label_gpu.verify(self.root)


if __name__ == "__main__":
    unittest.main()
