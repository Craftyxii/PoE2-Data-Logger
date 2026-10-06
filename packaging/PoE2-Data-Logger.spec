from pathlib import Path
from runpy import run_path
from PyInstaller.utils.hooks import collect_all, collect_submodules

project = Path(SPECPATH).resolve().parent
app = project / "PoE2_Data_Logger"
sources = run_path(str(project / "packaging" / "sources.py"))
expected = set(sources["APP_SOURCES"])
actual = {path.relative_to(app).as_posix() for path in app.rglob("*.py")}
if expected != actual:
    raise ValueError(f"Update packaging/sources.py for added or removed modules: {expected ^ actual}")
datas = [(str(app / name), "PoE2_Data_Logger") for name in sources["FILE_RESOURCES"]]
datas += [(str(app / name), "PoE2_Data_Logger/" + name) for name in sources["DIRECTORY_RESOURCES"]]
for source in sources["APP_SOURCES"]:
    if not (app / source).is_file():
        raise FileNotFoundError(app / source)
for source, target in datas:
    if not Path(source).exists():
        raise FileNotFoundError(source)
binaries = []
hiddenimports = collect_submodules("sklearn")
for package in ("rapidocr", "onnxruntime", "quickjs", "cv2"):
    files, libraries, imports = collect_all(package)
    datas += files
    binaries += libraries
    hiddenimports += imports

analysis = Analysis(
    [str(app / "__main__.py")],
    pathex=[str(project)], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
              "PySide6.QtWebView", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets"],
    noarchive=False, optimize=0,
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="PoE2-Data-Logger",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    contents_directory="_internal",
    icon=str(app / "power_rune.ico"), disable_windowed_traceback=False,
    version=str(project / "packaging" / "version_info.txt"),
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=True,
               upx_exclude=[], name="PoE2-Data-Logger")
