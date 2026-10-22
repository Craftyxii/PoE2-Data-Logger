# Source files

[packaging/sources.py](packaging/sources.py) groups the application modules and resources used by [the PyInstaller specification](packaging/PoE2-Data-Logger.spec).

[PoE2_Data_Logger/__main__.py](PoE2_Data_Logger/__main__.py) handles the desktop entry point, frozen child processes and isolated smoke checks. Package markers and all functional modules are declared in `APP_SOURCES`; the build checks that list against the package's Python files.

## Logging, records and exports

[Browse core source](PoE2_Data_Logger/core/)

| File | Purpose |
| --- | --- |
| [logger_store.py](PoE2_Data_Logger/core/logger_store.py) | Map settings, remnant records, inventory snapshots, Ritual records and review state |
| [undo_store.py](PoE2_Data_Logger/core/undo_store.py) | Persistent transaction journals and atomic Universal Undo |
| [chain_gap.py](PoE2_Data_Logger/core/chain_gap.py) | Read-only timing and captured-context validation for the explicit same-chain question |
| [kill_counts.py](PoE2_Data_Logger/core/kill_counts.py) | Independent, undoable screenshot kill observations and their evidence/export fields |
| [store.py](PoE2_Data_Logger/core/store.py) | Scanner records, seed candidates and reviewed glyphs |
| [service.py](PoE2_Data_Logger/core/service.py) | Application actions and scan routing |
| [auto_commit.py](PoE2_Data_Logger/core/auto_commit.py) | Accepting clear remnant readings |
| [reference_pack.py](PoE2_Data_Logger/core/reference_pack.py) | Reference database import and export |
| [reference_updates.py](PoE2_Data_Logger/core/reference_updates.py) | Guarded bundled reference corrections that preserve local edits and recorded activity |
| [ritual_catalog.py](PoE2_Data_Logger/core/ritual_catalog.py) | Ritual reward catalog helpers |
| [ritual_rites_catalog.py](PoE2_Data_Logger/core/ritual_rites_catalog.py) | Source-traced optional Rite affix catalog |
| [ritual_rite_store.py](PoE2_Data_Logger/core/ritual_rite_store.py) | Explicit Rite membership, saved affixes and completed-map proliferation |
| [ritual_counters.py](PoE2_Data_Logger/core/ritual_counters.py) | First/final approved map Tribute and reroll observations |
| [catalog_repairs.py](PoE2_Data_Logger/core/catalog_repairs.py) | Guarded repairs for known legacy remnant family data |
| [ocr_runtime.py](PoE2_Data_Logger/core/ocr_runtime.py) | Frozen CPU thread and optional GPU label preferences |
| [ocr_workers.py](PoE2_Data_Logger/core/ocr_workers.py) | Bounded child recognition workers, cancellation, deadlines and recovery |
| [ocr_sensitivity.py](PoE2_Data_Logger/core/ocr_sensitivity.py) | Per-scan strictness preferences and acceptance thresholds |
| [review_learning.py](PoE2_Data_Logger/core/review_learning.py) | Validated local icon examples learned from accepted review corrections |
| [currency_display.py](PoE2_Data_Logger/core/currency_display.py) | Currency counter artwork from bundled and local references |
| [workbook_export.py](PoE2_Data_Logger/core/workbook_export.py) | Excel workbook export |
| [workbook_references.py](PoE2_Data_Logger/core/workbook_references.py) | Family, recipe, seed and alias worksheet exports on the log's read snapshot |
| [analysis_context.py](PoE2_Data_Logger/core/analysis_context.py) | Field definitions, joins, counting rules and analysis guidance |
| [analysis_context_data.py](PoE2_Data_Logger/core/analysis_context_data.py) | Ordinary SQLite views for frozen Atlas definitions and scoped observation totals |
| [analysis_context_rules.py](PoE2_Data_Logger/core/analysis_context_rules.py) | Verified source-to-display unit translations for Atlas stats |
| [atlas_effect_snapshot.py](PoE2_Data_Logger/core/atlas_effect_snapshot.py) | Combined applicable Atlas source totals for a map’s frozen activity, city and biome |
| [atlas_catalog.py](PoE2_Data_Logger/core/atlas_catalog.py) | Offline, versioned PoE2 atlas nodes, effects and choice options |
| [export_files.py](PoE2_Data_Logger/core/export_files.py) | Writes paired exports and restores earlier files if a write fails |

## Scanners and item readers

[Browse OCR source](PoE2_Data_Logger/ocr/)

| File | Purpose |
| --- | --- |
| [affix_capture.py](PoE2_Data_Logger/ocr/affix_capture.py) | Waystone and tablet affix capture |
| [artwork_cache.py](PoE2_Data_Logger/ocr/artwork_cache.py) | Bounded immutable artwork transforms preserving the pinned matcher's arithmetic |
| [currency_ocr.py](PoE2_Data_Logger/ocr/currency_ocr.py) | Currency and inventory OCR |
| [digit_kernels.py](PoE2_Data_Logger/ocr/digit_kernels.py) | Exact native arithmetic for the pinned upstream digit reader |
| [label_gpu.py](PoE2_Data_Logger/ocr/label_gpu.py) | Optional isolated GPU label inference and complete CPU retry |
| [gpu_label_protocol.py](PoE2_Data_Logger/ocr/gpu_label_protocol.py) | Pure protocol/model limits shared with the GPU helper |
| [inventory_labels.py](PoE2_Data_Logger/ocr/inventory_labels.py) | Inventory item-label helpers |
| [item_ocr.py](PoE2_Data_Logger/ocr/item_ocr.py) | Item and Ritual reward readings |
| [item_text.py](PoE2_Data_Logger/ocr/item_text.py) | Tooltip text parsing |
| [kill_counts.py](PoE2_Data_Logger/ocr/kill_counts.py) | Pixel-based skull-counter location and numeric readings, retaining uncertain evidence |
| [ritual_grid.py](PoE2_Data_Logger/ocr/ritual_grid.py) | Ritual reward-grid geometry and item footprints |
| [unique_items.py](PoE2_Data_Logger/ocr/unique_items.py) | Cached, full-footprint unique-item artwork matching for Currency and Ritual; shared artwork retains name choices |
| [opened_scan.py](PoE2_Data_Logger/ocr/opened_scan.py) | Opened remnant scanning and OCR model checks |
| [propagation_scan.py](PoE2_Data_Logger/ocr/propagation_scan.py) | Selected recipe and propagated rune marker recognition |
| [propagation_marks.py](PoE2_Data_Logger/ocr/propagation_marks.py) | Three-peak propagation mark detection against recipe tile positions |
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
| [currency_counter.py](PoE2_Data_Logger/ui/currency_counter.py) | Responsive session currency totals and item icons |
| [hotkey.py](PoE2_Data_Logger/platform/hotkey.py) | Global hotkeys and scan capture |
| [hover_copy.py](PoE2_Data_Logger/platform/hover_copy.py) | Hovered tooltip copy helpers |
| [live_watch.py](PoE2_Data_Logger/platform/live_watch.py) | Game-window checks used during capture |
| [region_select.py](PoE2_Data_Logger/ui/region_select.py) | Capture-region selection and reference screenshots |
| [atlas_settings.py](PoE2_Data_Logger/ui/atlas_settings.py) | Interactive atlas tree and gear item rarity settings |

## Trace a scan through the application

| Stage | Maintenance entry points and boundaries |
| --- | --- |
| Capture and queue | `native_desktop._inventory_captured` / `_ritual_captured` bind the image, map and session context before `_submit_scan` delegates to `core/ocr_workers.py`. Replacement, cancellation and deadline handling belong to that worker lifecycle. |
| Recognize | `ocr/item_ocr.py` combines grid geometry, count/tier evidence, catalog artwork and local references. Identity and quantity confidence are separate; whole equipment retains its footprint and image coordinates. |
| Review and learn | `native_desktop._inventory_read` / `_ritual_read` populate the existing review controls. Verified hidden rows remain part of the saved reading. `core/review_learning.py` validates captured artwork for accepted name corrections. |
| Persist and undo | `native_desktop.save_inventory` / `save_ritual` hand accepted data to `core/logger_store.py`. Captured context, canonical item categories, writes and reference learning must remain transactionally consistent; `core/undo_store.py` journals reversible state. |
| Export | `core/logger_store.py`, `workbook_export.py`, `workbook_references.py` and `export_files.py` preserve the selected database snapshot, frozen map context and companion files. Export is separate from recognition and review acceptance. |

Reusable artwork preparation starts in `core/ocr_workers._prepare_artwork` and delegates to `ocr/item_ocr.prepare_artwork`. Artwork transforms, unique-item banks and screenshot-result caches have different keys and lifetimes. Preserve their worker/thread ownership, memory bounds, reference invalidation and original-matcher fallback; preparing references must not create observations. See [startup cache validation](docs/STARTUP_ARTWORK_CACHE_VALIDATION.md) for the historical measurements and [CONTRIBUTING.md](CONTRIBUTING.md) for current test entry points.

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
- [gpu_worker/](gpu_worker/README.md), [tools/build_label_gpu.py](tools/build_label_gpu.py) and [tools/verify_label_gpu.py](tools/verify_label_gpu.py) build and verify the separate optional DirectML runtime.
- [tools/build_atlas_catalog.py](tools/build_atlas_catalog.py) rebuilds the bundled atlas from its versioned source data.
- [tools/build_unique_item_catalog.py](tools/build_unique_item_catalog.py) rebuilds the pinned [unique artwork catalog](PoE2_Data_Logger/unique_item_assets/NOTICE.txt), retaining source images, inventory dimensions and shared-art alternatives.
- [tools/run_tests.py](tools/run_tests.py) runs targeted tests or full discovery in isolated batches.
- [tools/audit_propagation_crowns.py](tools/audit_propagation_crowns.py) and [tools/audit_propagation_prefill.py](tools/audit_propagation_prefill.py) measure captured-image mark recognition and family filling.
- [tools/native_hud_input.py](tools/native_hud_input.py) exercises native Windows input for HUD validation.
- [tools/verify_installer.py](tools/verify_installer.py) checks installation, updates and saved-file retention.
- [tools/publish_verified_beta.py](tools/publish_verified_beta.py) validates exact-source release artifacts before publication.
- [.github/workflows/release.yml](.github/workflows/release.yml) defines the required Windows release gates.

Regression tests are in [tests/](tests/). See [CONTRIBUTING.md](CONTRIBUTING.md) for execution and environment limits and [docs/README.md](docs/README.md) for documentation navigation.

The library list is in [DEPENDENCIES.md](DEPENDENCIES.md).
