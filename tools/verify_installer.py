"""Exercise Windows installer metadata, repeat installation, running-client guards and file retention.

Native Qt test windows verify installer guards; they are not game-overlay
interaction tests. The installed app also runs its isolated smoke check."""

import hashlib
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
    """Read ProductName, ProductVersion and FileVersion from the Windows executable version
    resource.
    """
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
    """Require installer/runtime names and version strings to match the stable or beta release."""
    display = f"{version} Beta" if beta else version
    name = f"PoE2 Data Logger {display}"
    actual = version_strings(path)
    if actual != {"ProductName": name, "ProductVersion": display, "FileVersion": display}:
        raise RuntimeError(f"{path.name} version metadata does not match {display}.")


def launch(executable, arguments, timeout=120, env=None):
    """Run a quoted Windows executable within the timeout and fail on a nonzero exit code."""
    process = subprocess.run(f'"{executable}" {arguments}', timeout=timeout, env=env,
                             check=False)
    if process.returncode:
        raise RuntimeError(f"{executable.name} exited with code {process.returncode}.")


def verify_blocked_launch(executable, arguments, protected_files, check_saved):
    """Require a running-client rejection and unchanged protected runtime/user files."""
    def fingerprints():
        """Hash each required runtime file and fail if a guard check has removed one."""
        values = {}
        for path in protected_files:
            if not path.is_file():
                raise RuntimeError(f"Running-client guard lost a retained runtime file: {path.name}.")
            with path.open("rb") as stream:
                values[path] = hashlib.file_digest(stream, "sha256").digest()
        return values

    before = fingerprints()
    process = subprocess.run(f'"{executable}" {arguments}', timeout=20, check=False)
    if process.returncode == 0:
        raise RuntimeError(f"{executable.name} allowed an operation while a client was running.")
    if fingerprints() != before:
        raise RuntimeError("Running-client guard changed retained runtime files.")
    check_saved()


def running_client_titles(version, beta):
    """Probe prior beta/stable clients once each, ending with the current release title."""
    current = f"PoE2 Data Logger {version}{' Beta' if beta else ''}"
    prior = (
        "PoE2 Data Logger", "PoE2 Data Logger 1.2 Beta", "PoE2 Data Logger 1.2.1 Beta",
        "PoE2 Data Logger 1.2.2 Beta", "PoE2 Data Logger 1.3 Beta",
        "PoE2 Data Logger 1.3.1 Beta", "PoE2 Data Logger 1.3.1.1 Beta",
        "PoE2 Data Logger 1.3.1.2 Beta", "PoE2 Data Logger 1.3.1.3 Beta",
        "PoE2 Data Logger 1.3.2", "PoE2 Data Logger 1.3.2.4 Beta",
    )
    return tuple(dict.fromkeys(title for title in prior if title != current)) + (current,)


def verify_running_guards(installer, directory, check_saved, version, beta):
    """Create native Windows Qt windows for known client titles and verify install/uninstall
    refuses each without touching retained files.
    """
    import ctypes
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QWidget

    # Explicitly use the native backend, overriding CI's offscreen regression setting.
    app = QApplication.instance() or QApplication(["installer-guard-check", "-platform", "windows"])
    if app.platformName() != "windows":
        raise RuntimeError("Running-client guard checks require the native Windows Qt backend.")
    find_window = ctypes.windll.user32.FindWindowW
    find_window.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    find_window.restype = ctypes.c_void_p
    sentinel = directory / "_internal" / "installer-guard-sentinel.bin"
    sentinel.write_bytes(b"Runtime retained while a previous beta client is running")
    protected = (directory / "PoE2-Data-Logger.exe", directory / "Uninstall.exe", sentinel)
    window = QWidget()
    window.resize(320, 120)
    try:
        for title in running_client_titles(version, beta):
            window.setWindowTitle(title)
            window.show()
            deadline = time.monotonic() + 5
            while not find_window(None, title):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Could not create the previous-client test window: {title}.")
                app.processEvents()
                time.sleep(0.01)
            verify_blocked_launch(installer, f"/S /D={directory}", protected, check_saved)
            # _?= avoids the uninstaller spawning a temporary copy, so its exit is checked directly.
            verify_blocked_launch(directory / "Uninstall.exe", f"/S _?={directory}",
                                  protected, check_saved)
            print(f"Blocked update/uninstall and retained saved files for running {title}.")
    finally:
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        if sentinel.exists():
            sentinel.unlink()


def verify(installer):
    """Install and update into a temporary folder, verify labels/guards/startup and ensure
    uninstall retains planted user files.
    """
    match = re.fullmatch(r"PoE2-Data-Logger-Setup-v([0-9]+\.[0-9]+(?:\.[0-9]+){0,2})(-beta)?\.exe", installer.name)
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
            """Check that saved database/export bytes and the executable-search-path sentinel
            remain unchanged.
            """
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
                # Installed stable and beta labels both include their display version.
                expected_name = f"PoE2 Data Logger {display}"
                if winreg.QueryValueEx(key, "DisplayVersion")[0] != display:
                    raise RuntimeError("Installed version does not match the release.")
                if winreg.QueryValueEx(key, "DisplayName")[0] != expected_name:
                    raise RuntimeError("Installed application label does not match the release.")
        verify_running_guards(installer, directory, check_saved, version, beta)
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
        print("Installer, release labels, system tool resolution, update, running-client guards, startup, recognition and saved-file retention checks passed.")
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
