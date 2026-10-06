# Libraries and bundled components

These are the dependency declarations for the current v33 source. Exact versions and version ranges below come from [requirements.txt](requirements.txt) and [requirements-build.txt](requirements-build.txt).

## Runtime libraries

| Library | Declared version | Used for |
| --- | --- | --- |
| Pillow | `>=10` | Reading, cropping and preparing screenshots |
| NumPy | `1.26.4` | Image arrays and scanner feature vectors |
| SciPy | `1.14.1` | Image filters and scanner features |
| scikit-learn | `1.8.0` | Loading and running the socket classifier |
| joblib | `>=1.3` | Loading the bundled socket model |
| RapidOCR | `3.9.2` | General text OCR and model verification |
| ONNX Runtime | `1.22.1` | OCR model inference |
| OpenCV | `opencv-python==4.10.0.84` | OCR image processing |
| OmegaConf | `2.3.0` | RapidOCR configuration dependency |
| ANTLR Python runtime | `4.9.3` | OmegaConf configuration dependency |
| PySide6 | `6.9.3` | Desktop interface, review overlay and capture-region editor |
| QuickJS | `1.19.4` | Running the bundled currency and inventory readers |

The application also uses Python 3.12's standard library, including SQLite, CSV, JSON, XML and ZIP support. Excel export is written by [workbook_export.py](PoE2_Data_Logger/workbook_export.py); it does not require openpyxl at runtime.

## Build tools

| Tool | Requirement | Used for |
| --- | --- | --- |
| Python | `3.12` | Source execution and the Windows build |
| PyInstaller | `>=6.15,<7` | Packaging the Python runtime, application and libraries |
| NSIS | `makensis` on PATH | Building the Windows installer |

## Bundled third-party files

| Component | Included material | Notices and provenance |
| --- | --- | --- |
| RuneHelper | Converted English OCR model, charset and panel-preparation source | [README](PoE2_Data_Logger/third_party/runehelper/README.txt), [MIT license](PoE2_Data_Logger/third_party/runehelper/LICENSE.txt) |
| Currency overlay / Exiled Exchange 2 | Currency and inventory readers, icon matching and reference data | [README](PoE2_Data_Logger/third_party/currency_overlay/README.txt), [currency-overlay license](PoE2_Data_Logger/third_party/currency_overlay/LICENSE), [Exiled Exchange 2 license](PoE2_Data_Logger/third_party/currency_overlay/EXILED-EXCHANGE-2-LICENSE.txt) |
| DejaVu fonts | Regular and bold fonts used by the app | [Font license](PoE2_Data_Logger/fonts/LICENSE.txt) |

Library dependency declarations above are separate from these bundled files. Source revision details are recorded in each component's README.
