import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback


def version_strings(path):
    import ctypes
    import struct

    api = ctypes.windll.version
    api.GetFileVersionInfoSizeW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p)
    api.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    api.GetFileVersionInfoW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32,
                                      ctypes.c_uint32, ctypes.c_void_p)
    api.GetFileVersionInfoW.restype = ctypes.c_int
    api.VerQueryValueW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p,
                                  ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_uint32))
    api.VerQueryValueW.restype = ctypes.c_int
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise RuntimeError(f"{path.name} is missing Windows version metadata.")
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise RuntimeError(f"Could not read {path.name} version metadata.")
    address, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not api.VerQueryValueW(data, "\\VarFileInfo\\Translation",
                             ctypes.byref(address), ctypes.byref(length)) or length.value < 4:
        raise RuntimeError(f"{path.name} is missing a version language table.")
    language, codepage = struct.unpack("<HH", ctypes.string_at(address.value, 4))
    strings = {}
    for key in ("ProductName", "ProductVersion", "FileVersion"):
        query = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{key}"
        if not api.VerQueryValueW(data, query, ctypes.byref(address), ctypes.byref(length)):
            raise RuntimeError(f"{path.name} is missing {key}.")
        strings[key] = ctypes.wstring_at(address.value, length.value).rstrip("\0")
    return strings


def check_version(path, version, beta):
    display = f"{version} Beta" if beta else version
    name = f"PoE2 Data Logger {display}" if beta else "PoE2 Data Logger"
    actual = version_strings(path)
    if actual != {"ProductName": name, "ProductVersion": display, "FileVersion": display}:
        raise RuntimeError(f"{path.name} version metadata does not match {display}.")


def launch(executable, arguments, timeout=120, env=None):
    process = subprocess.run(f'"{executable}" {arguments}', timeout=timeout, env=env,
                             check=False)
    if process.returncode:
        raise RuntimeError(f"{executable.name} exited with code {process.returncode}.")


def verify(installer):
    match = re.fullmatch(r"PoE2-Data-Logger-Setup-v([0-9]+\.[0-9]+(?:\.[0-9]+)?)(-beta)?\.exe", installer.name)
    if not match:
        raise RuntimeError("Installer filename does not contain a valid release version.")
    version, beta = match.group(1), bool(match.group(2))
    check_version(installer, version, beta)
    directory = Path(tempfile.mkdtemp(prefix="poe2-installer-"))
    try:
        data = directory / "Databases" / "saved-data.bin"
        data.parent.mkdir()
        data.write_bytes(b"saved database contents")
        user_file = directory / "saved-export.csv"
        user_file.write_bytes(b"saved export contents")
        planted_tool = directory / "icacls.exe"
        planted_tool.write_bytes(b"Executable search-path sentinel")
        def check_saved():
            if data.read_bytes() != b"saved database contents" or user_file.read_bytes() != b"saved export contents":
                raise RuntimeError("Installer changed saved user files.")
            if planted_tool.read_bytes() != b"Executable search-path sentinel":
                raise RuntimeError("Installer changed the executable search-path sentinel.")
        for _ in range(2):
            launch(installer, f"/S /D={directory}")
            check_saved()
            for name in ("PoE2-Data-Logger.exe", "Uninstall.exe", "CRAFTYXII_ASSETS_LICENSE.txt", "_internal"):
                if not (directory / name).exists():
                    raise RuntimeError(f"Installation is missing {name}.")
            check_version(directory / "PoE2-Data-Logger.exe", version, beta)
            check_version(directory / "Uninstall.exe", version, beta)
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
                display = f"{version} Beta" if beta else version
                expected_name = f"PoE2 Data Logger {display}" if beta else "PoE2 Data Logger"
                if winreg.QueryValueEx(key, "DisplayVersion")[0] != display:
                    raise RuntimeError("Installed version does not match the release.")
                if winreg.QueryValueEx(key, "DisplayName")[0] != expected_name:
                    raise RuntimeError("Installed application label does not match the release.")
        environment = dict(os.environ, QT_QPA_PLATFORM="windows",
                           POE2_SMOKE_REPORT=str(directory / "startup-check.txt"))
        try:
            launch(directory / "PoE2-Data-Logger.exe", "--smoke-test", timeout=60, env=environment)
        except Exception as error:
            report = directory / "startup-check.txt"
            if report.is_file():
                raise RuntimeError(report.read_text(encoding="utf-8")) from error
            raise
        launch(directory / "Uninstall.exe", "/S")
        deadline = time.monotonic() + 60
        while any((directory / name).exists() for name in ("PoE2-Data-Logger.exe", "_internal")):
            if time.monotonic() >= deadline:
                raise RuntimeError("Uninstaller did not remove the application runtime.")
            time.sleep(0.1)
        check_saved()
        print("Installer, release labels, system tool resolution, update, startup, recognition and saved-file retention checks passed.")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]).resolve())
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Installer verification::{detail}")
        raise SystemExit(1)
