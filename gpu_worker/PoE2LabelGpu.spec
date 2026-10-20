"""Freeze the DirectML worker with its own Python/runtime and no app imports."""

from importlib import metadata
import struct
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


project = Path(SPECPATH).resolve().parent
if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
    raise ValueError("Build the label GPU helper with isolated 64-bit Windows Python 3.12.")
for distribution, wanted in (("numpy", "1.26.4"), ("onnxruntime-directml", "1.22.0")):
    if metadata.version(distribution) != wanted:
        raise ValueError("GPU helper build environment has the wrong " + distribution + " version.")
try:
    metadata.version("onnxruntime")
except metadata.PackageNotFoundError:
    pass
else:
    raise ValueError("Do not install the CPU-only ORT wheel in the isolated DirectML build environment.")

datas, binaries, imports = collect_all("onnxruntime")
datas += copy_metadata("onnxruntime-directml") + copy_metadata("numpy")
datas += [(str(project / "PoE2_Data_Logger" / "ocr" / "gpu_label_protocol.py"), ".")]
analysis = Analysis(
    [str(project / "gpu_worker" / "worker.py")],
    pathex=[str(project / "gpu_worker")], binaries=binaries, datas=datas, hiddenimports=imports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PoE2_Data_Logger", "rapidocr", "cv2", "PySide6"],
    noarchive=False, optimize=0,
)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="PoE2LabelGpu",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          contents_directory="_internal", disable_windowed_traceback=False,
          argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False,
               upx_exclude=[], name="PoE2LabelGpu")
