"""Check the frozen helper's separate runtime without loading DirectML into the app."""

from __future__ import annotations

from pathlib import Path
import struct

from PyInstaller.archive.readers import CArchiveReader

from tools.verify_build import pe_machine


def verify(directory):
    """Require Python 3.12 x64, DML DLLs, protocol data and no app/Qt OCR imports."""
    directory = Path(directory)
    executable = directory / "PoE2LabelGpu.exe"
    if pe_machine(executable) != 0x8664:
        raise ValueError("GPU helper must be a Windows x64 executable.")
    archive = CArchiveReader(str(executable))
    if [option for option in archive.options if option.startswith("pyi-contents-directory ")] != [
            "pyi-contents-directory _internal"]:
        raise ValueError("GPU helper must use its own nested _internal runtime.")
    with executable.open("rb") as source:
        source.seek(archive._end_offset - archive._COOKIE_LENGTH)
        cookie = struct.unpack(archive._COOKIE_FORMAT, source.read(archive._COOKIE_LENGTH))
    if cookie[4] != 312 or cookie[5].rstrip(b"\0") != b"python312.dll":
        raise ValueError("GPU helper must use its separate Python 3.12 runtime.")
    if "worker" not in archive.toc or "PYZ.pyz" not in archive.toc:
        raise ValueError("GPU helper is missing its worker entry point/module archive.")
    modules = archive.open_embedded_archive("PYZ.pyz")
    for module in ("numpy", "onnxruntime"):
        if module not in modules.toc:
            raise ValueError("GPU helper is missing " + module)
        modules.extract(module)
    if any(name.split(".", 1)[0] in {"PoE2_Data_Logger", "rapidocr", "cv2", "PySide6"}
           for name in modules.toc):
        raise ValueError("GPU helper must not bundle the main application/runtime imports.")
    runtime = directory / "_internal"
    for relative in ("python312.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll",
                     "base_library.zip", "gpu_label_protocol.py",
                     "onnxruntime/capi/DirectML.dll", "onnxruntime/capi/onnxruntime_pybind11_state.pyd"):
        path = runtime / relative
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("GPU helper is missing runtime file: " + relative)
    if not list((runtime / "numpy").rglob("_multiarray_umath*.pyd")):
        raise ValueError("GPU helper is missing NumPy's native array extension.")
    count = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.casefold() in {".exe", ".dll", ".pyd"}:
            if pe_machine(path) != 0x8664:
                raise ValueError("GPU helper contains a non-x64 native binary.")
            count += 1
    return {"python": [3, 12], "bits": 64, "native_files_checked": count,
            "main_runtime_imports_present": False}
