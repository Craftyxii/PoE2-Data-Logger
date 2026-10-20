"""Build DirectML in a separate venv; never install it into the main app environment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from runpy import run_path
import struct
import subprocess
import sys


ENVIRONMENT_PROBE = r'''
import json,struct,sys
from importlib import metadata
import numpy,onnxruntime
try:
    metadata.version("onnxruntime")
except metadata.PackageNotFoundError:
    pass
else:
    raise SystemExit("CPU-only ORT must not coexist with the DirectML wheel.")
versions={key:metadata.version(key) for key in ("numpy","onnxruntime-directml")}
if versions!={"numpy":"1.26.4","onnxruntime-directml":"1.22.0"}:
    raise SystemExit("Wrong isolated runtime versions.")
if onnxruntime.__version__!="1.22.0" or numpy.__version__!="1.26.4":
    raise SystemExit("Runtime imports disagree with distribution metadata.")
if sys.version_info[:2]!=(3,12) or struct.calcsize("P")!=8 or sys.prefix==sys.base_prefix:
    raise SystemExit("Use an isolated x64 Python 3.12 environment.")
providers=onnxruntime.get_available_providers()
if "DmlExecutionProvider" not in providers:
    raise SystemExit("The isolated build runtime lacks DirectML.")
print(json.dumps({"python":[3,12],"bits":64,"runtime_versions":versions,
                  "available_providers":providers}))
'''


def build(project, *, skip_install=False):
    """Build/verify the helper before the main spec copies its complete directory."""
    project = Path(project).resolve()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise ValueError("The helper build requires 64-bit Windows Python 3.12.")
    manifest = run_path(str(project / "packaging" / "gpu_helper.py"))
    environment = project / "build" / "label_gpu_env"
    python = environment / "Scripts" / "python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(environment)], cwd=project, check=True)
    if not (environment / "pyvenv.cfg").is_file() or python.resolve() == Path(sys.executable).resolve():
        raise ValueError("Refusing to install GPU dependencies outside the separate build venv.")
    subprocess.run([str(python), "-c",
                    "import pathlib,sys; expected=pathlib.Path(sys.argv[1]).resolve(); "
                    "assert pathlib.Path(sys.prefix).resolve()==expected and sys.prefix!=sys.base_prefix, "
                    "'Refusing dependency installation outside the isolated helper venv.'",
                    str(environment)], cwd=project, check=True)
    if not skip_install:
        subprocess.run([str(python), "-m", "pip", "install", "--require-virtualenv", "--no-input",
                        "--disable-pip-version-check", "-r", str(project / "gpu_worker" / "requirements.txt")],
                       cwd=project, check=True)
    checked = subprocess.run([str(python), "-c", ENVIRONMENT_PROBE], cwd=project, check=True,
                             text=True, stdout=subprocess.PIPE)
    runtime = json.loads(checked.stdout)
    subprocess.run([str(python), "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--workpath", str(project / "build" / "label_gpu_freeze"),
                    "--distpath", str(project / "dist"),
                    str(project / "gpu_worker" / "PoE2LabelGpu.spec")], cwd=project, check=True)
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from tools.verify_label_gpu import verify
    destination = project / "dist" / manifest["HELPER_NAME"]
    verification = verify(destination)
    info = {"format": 1, "built_utc": datetime.now(timezone.utc).isoformat(), **runtime,
            "source_sha256": {name: manifest["sha256"](project / name) for name in manifest["SOURCE_FILES"]},
            "executable_sha256": manifest["sha256"](destination / "PoE2LabelGpu.exe"),
            "verification": verification}
    (destination / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    manifest["helper_bundle_data"](project)
    return info


def main(argv=None):
    """Keep the helper's dependency installation explicit and confined to its build venv."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-install", action="store_true", help="Use an already provisioned isolated helper venv")
    args = parser.parse_args(argv)
    info = build(Path(__file__).resolve().parents[1], skip_install=args.skip_install)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
