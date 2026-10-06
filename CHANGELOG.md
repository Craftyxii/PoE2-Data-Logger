# PoE2 Data Logger v33

## Windows runtime

- The launcher locates the bundled Python 3.12 runtime in `_internal`.
- The package includes the complete PySide6 software-rendering DLL.
- The build checks the launcher, native DLLs and OCR models before creating the installer.

## Current source

- Application code and required resources are under `PoE2_Data_Logger/`.
- `packaging/sources.py` groups the source and resources used by the build.
- `SOURCES.md` and `DEPENDENCIES.md` list the code, libraries and bundled third-party components.
- Old tests, test dependencies, logs, generated databases, build output and the unused full-size icon are excluded.
- Runtime code, OCR models, reference data and third-party notices are unchanged.
