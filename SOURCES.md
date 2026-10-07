# Source files

[packaging/sources.py](packaging/sources.py) groups the application modules and resources used by [the PyInstaller specification](packaging/PoE2-Data-Logger.spec).

## Logging, records and exports

[Browse core source](PoE2_Data_Logger/core/)

| File | Purpose |
| --- | --- |
| [logger_store.py](PoE2_Data_Logger/core/logger_store.py) | Map settings, remnant records, inventory snapshots, Ritual records and review state |
| [store.py](PoE2_Data_Logger/core/store.py) | Scanner records, seed candidates and reviewed glyphs |
| [service.py](PoE2_Data_Logger/core/service.py) | Application actions and scan routing |
| [auto_commit.py](PoE2_Data_Logger/core/auto_commit.py) | Accepting clear remnant readings |
| [reference_pack.py](PoE2_Data_Logger/core/reference_pack.py) | Reference database import and export |
| [ritual_catalog.py](PoE2_Data_Logger/core/ritual_catalog.py) | Ritual reward catalog helpers |
| [workbook_export.py](PoE2_Data_Logger/core/workbook_export.py) | Excel workbook export |
| [atlas_catalog.py](PoE2_Data_Logger/core/atlas_catalog.py) | Offline, versioned PoE2 atlas nodes, effects and choice options |
| [export_files.py](PoE2_Data_Logger/core/export_files.py) | Writes paired exports and restores earlier files if a write fails |

## Scanners and item readers

[Browse OCR source](PoE2_Data_Logger/ocr/)

| File | Purpose |
| --- | --- |
| [affix_capture.py](PoE2_Data_Logger/ocr/affix_capture.py) | Waystone and tablet affix capture |
| [currency_ocr.py](PoE2_Data_Logger/ocr/currency_ocr.py) | Currency and inventory OCR |
| [inventory_labels.py](PoE2_Data_Logger/ocr/inventory_labels.py) | Inventory item-label helpers |
| [item_ocr.py](PoE2_Data_Logger/ocr/item_ocr.py) | Item and Ritual reward readings |
| [item_text.py](PoE2_Data_Logger/ocr/item_text.py) | Tooltip text parsing |
| [opened_scan.py](PoE2_Data_Logger/ocr/opened_scan.py) | Opened remnant scanning and OCR model checks |
| [runehelper_ocr.py](PoE2_Data_Logger/ocr/runehelper_ocr.py) | RuneHelper OCR adapter |
| [scan.py](PoE2_Data_Logger/ocr/scan.py) | Visible remnant seed scanning |
| [prototype.py](PoE2_Data_Logger/ocr/prototype.py) | Rune-bar geometry and template matching |
| [cv_eval.py](PoE2_Data_Logger/ocr/cv_eval.py) | Socket feature extraction and decoding |
| [glyph_eval.py](PoE2_Data_Logger/ocr/glyph_eval.py) | Rune glyph feature vectors |

## Desktop, hotkeys and screen capture

[Browse interface source](PoE2_Data_Logger/ui/) · [Browse platform source](PoE2_Data_Logger/platform/)

| File | Purpose |
| --- | --- |
| [native_desktop.py](PoE2_Data_Logger/ui/native_desktop.py) | Main desktop interface and review overlay |
| [hotkey.py](PoE2_Data_Logger/platform/hotkey.py) | Global hotkeys and scan capture |
| [hover_copy.py](PoE2_Data_Logger/platform/hover_copy.py) | Hovered tooltip copy helpers |
| [live_watch.py](PoE2_Data_Logger/platform/live_watch.py) | Game-window checks used during capture |
| [region_select.py](PoE2_Data_Logger/ui/region_select.py) | Capture-region selection and reference screenshots |
| [atlas_settings.py](PoE2_Data_Logger/ui/atlas_settings.py) | Interactive atlas tree and gear item rarity settings |

## Resources and third-party code

| Location | Contents |
| --- | --- |
| [PoE2_Data_Logger/](PoE2_Data_Logger/) | Required catalogs, bootstrap data, socket model, glyph vectors, scanner templates and branding |
| [third_party/runehelper/](PoE2_Data_Logger/third_party/runehelper/) | RuneHelper OCR model, charset, reference source and license |
| [third_party/currency_overlay/](PoE2_Data_Logger/third_party/currency_overlay/) | Currency and inventory JavaScript readers and icon data |
| [fonts/](PoE2_Data_Logger/fonts/) | DejaVu fonts and their license |
| [region_examples/](PoE2_Data_Logger/region_examples/) | Capture-region reference images used by the app |
| [atlas/](PoE2_Data_Logger/atlas/) | PoE2 atlas catalog, rebuild inputs, node artwork and provenance notices |

## Build files

- [Build.bat](Build.bat) builds and verifies the Windows installer.
- [Start.bat](Start.bat) runs the desktop app from source.
- [packaging/sources.py](packaging/sources.py) groups the build inputs.
- [packaging/PoE2-Data-Logger.spec](packaging/PoE2-Data-Logger.spec) packages the app and libraries.
- [packaging/installer.nsi](packaging/installer.nsi) defines the Windows installer.
- [tools/verify_build.py](tools/verify_build.py) checks the packaged Windows runtime.
- [tools/build_atlas_catalog.py](tools/build_atlas_catalog.py) rebuilds the bundled atlas from its versioned source data.

The library list is in [DEPENDENCIES.md](DEPENDENCIES.md).
