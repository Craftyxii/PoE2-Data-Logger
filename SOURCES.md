# Current source files

The application code below is included in the current v33 runtime. [packaging/sources.py](packaging/sources.py) declares the source and resource groups used by [the PyInstaller specification](packaging/PoE2-Data-Logger.spec).

## Logging, records and exports

| File | Purpose |
| --- | --- |
| [logger_store.py](PoE2_Data_Logger/logger_store.py) | Map settings, remnant records, inventory snapshots, Ritual records and review state |
| [store.py](PoE2_Data_Logger/store.py) | Scanner records, seed candidates and reviewed glyphs |
| [service.py](PoE2_Data_Logger/service.py) | Application actions and scan routing |
| [auto_commit.py](PoE2_Data_Logger/auto_commit.py) | Accepting clear remnant readings |
| [reference_pack.py](PoE2_Data_Logger/reference_pack.py) | Reference database import and export |
| [ritual_catalog.py](PoE2_Data_Logger/ritual_catalog.py) | Ritual reward catalog helpers |
| [workbook_export.py](PoE2_Data_Logger/workbook_export.py) | Excel workbook export |

## Scanners and item readers

| File | Purpose |
| --- | --- |
| [affix_capture.py](PoE2_Data_Logger/affix_capture.py) | Waystone and tablet affix capture |
| [currency_ocr.py](PoE2_Data_Logger/currency_ocr.py) | Currency and inventory OCR |
| [inventory_labels.py](PoE2_Data_Logger/inventory_labels.py) | Inventory item-label helpers |
| [item_ocr.py](PoE2_Data_Logger/item_ocr.py) | Item and Ritual reward readings |
| [item_text.py](PoE2_Data_Logger/item_text.py) | Tooltip text parsing |
| [opened_scan.py](PoE2_Data_Logger/opened_scan.py) | Opened remnant scanning and OCR model checks |
| [runehelper_ocr.py](PoE2_Data_Logger/runehelper_ocr.py) | RuneHelper OCR adapter |
| [scan.py](PoE2_Data_Logger/scan.py) | Visible remnant seed scanning |
| [prototype.py](PoE2_Data_Logger/prototype.py) | Rune-bar geometry and template matching |
| [cv_eval.py](PoE2_Data_Logger/cv_eval.py) | Socket feature extraction and decoding |
| [glyph_eval.py](PoE2_Data_Logger/glyph_eval.py) | Rune glyph feature vectors |

## Desktop, hotkeys and screen capture

| File | Purpose |
| --- | --- |
| [native_desktop.py](PoE2_Data_Logger/native_desktop.py) | Main desktop interface and review overlay |
| [hotkey.py](PoE2_Data_Logger/hotkey.py) | Global hotkeys and scan capture |
| [hover_copy.py](PoE2_Data_Logger/hover_copy.py) | Hovered tooltip copy helpers |
| [live_watch.py](PoE2_Data_Logger/live_watch.py) | Game-window checks used during capture |
| [region_select.py](PoE2_Data_Logger/region_select.py) | Capture-region selection and reference screenshots |

## Resources and third-party code

| Location | Contents |
| --- | --- |
| [PoE2_Data_Logger/](PoE2_Data_Logger/) | Required catalogs, bootstrap data, socket model, glyph vectors, scanner templates and branding |
| [third_party/runehelper/](PoE2_Data_Logger/third_party/runehelper/) | RuneHelper OCR model, charset, reference source and license |
| [third_party/currency_overlay/](PoE2_Data_Logger/third_party/currency_overlay/) | Currency and inventory JavaScript readers and icon data |
| [fonts/](PoE2_Data_Logger/fonts/) | DejaVu fonts and their license |
| [region_examples/](PoE2_Data_Logger/region_examples/) | Capture-region reference images used by the app |

## Build files

- [Build.bat](Build.bat) builds and verifies the Windows installer.
- [Start.bat](Start.bat) runs the desktop app from source.
- [packaging/sources.py](packaging/sources.py) groups the current build inputs.
- [packaging/PoE2-Data-Logger.spec](packaging/PoE2-Data-Logger.spec) packages the app and libraries.
- [packaging/installer.nsi](packaging/installer.nsi) defines the Windows installer.
- [tools/verify_build.py](tools/verify_build.py) checks the packaged Windows runtime.

The library list is in [DEPENDENCIES.md](DEPENDENCIES.md).
