import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback


def launch(executable, arguments, timeout=120, env=None):
    process = subprocess.run(f'"{executable}" {arguments}', timeout=timeout, env=env,
                             check=False)
    if process.returncode:
        raise RuntimeError(f"{executable.name} exited with code {process.returncode}.")


def verify(installer):
    directory = Path(tempfile.mkdtemp(prefix="poe2-installer-"))
    try:
        data = directory / "Databases" / "saved-data.bin"
        data.parent.mkdir()
        data.write_bytes(b"saved database contents")
        user_file = directory / "saved-export.csv"
        user_file.write_bytes(b"saved export contents")
        def check_saved():
            if data.read_bytes() != b"saved database contents" or user_file.read_bytes() != b"saved export contents":
                raise RuntimeError("Installer changed saved user files.")
        for _ in range(2):
            launch(installer, f"/S /D={directory}")
            check_saved()
            for name in ("PoE2-Data-Logger.exe", "Uninstall.exe", "CRAFTYXII_ASSETS_LICENSE.txt", "_internal"):
                if not (directory / name).exists():
                    raise RuntimeError(f"Installation is missing {name}.")
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
        print("Installer, update, startup, recognition and saved-file retention checks passed.")
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
