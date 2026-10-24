# Developing PoE2 Data Logger

Use the existing checkout and Python 3.12. The supported desktop and installer target is Windows. Linux with Qt offscreen can exercise backend and many UI regressions; it does not verify native Windows input, overlays or installer behavior.

## Setup

Create a virtual environment from the repository root and install [requirements-build.txt](requirements-build.txt). It includes runtime dependencies, PyInstaller and openpyxl used by export tests. Runtime-only execution needs [requirements.txt](requirements.txt).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

On Linux, use `python3.12 -m venv .venv` and `.venv/bin/python` for the same commands.

## Test without touching your profile

[tools/run_tests.py](tools/run_tests.py) creates temporary data directories and defaults Qt to offscreen. Its fixtures isolate logger profiles. Do not set `RUNESHAPE_SCAN_DATA_DIR` to a personal profile when testing destructive actions or running ad hoc reproductions.

```powershell
.venv\Scripts\python.exe tools/run_tests.py --pattern test_atlas_biome_sync.py
.venv\Scripts\python.exe tools/run_tests.py
```

Full discovery runs modules in bounded subprocess batches. `--shard K/N` runs every Nth batch from batch K, so N shards together run each module once; `--verify-shards` then checks their summary files (`--summary-file`) prove exactly that, with every shard passing. A selector matching zero tests fails. Check the runner's exit code and actual passed/failed/skipped counts. Record which revision and environment were tested; an older report does not validate newer edits.

When changing logger behavior, trace the upstream input, captured map/expedition/generation, writer transaction and exported result. Add a regression that fails on the original defect. For contention, use a real second SQLite connection. UI timing checks must include event-loop delivery and retain review drafts. Replaying a whole completion callback after a partial save can duplicate records.

Injected OCR results verify review and accounting behavior, not recognition accuracy. Captured-image OCR benchmarks are separate. The opt-in 100-map workflow uses `POE2_RUN_HUNDRED_MAP_SESSION=1`; it is a longer release gate. Native HUD checks run on Windows with `QT_QPA_PLATFORM=windows` and require real input proof. See [the release workflow](.github/workflows/release.yml) for exact commands and required artifacts.

## Code and resource layout

See [SOURCES.md](SOURCES.md) for module purposes and [PoE2_Data_Logger/README.md](PoE2_Data_Logger/README.md) for package directories. Keep purpose notes and docstrings current when behavior changes. Preserve captured-context ownership, explicit propagation approval, map history and existing controls unless the change specifically calls for different behavior.

Every first-party module should state its purpose, and functions/classes should explain their responsibility where it is not clear from the name. Keep these notes with the implementation when extracting or moving code. Document non-obvious invariants: image coordinate space and complete footprints, confidence versus candidate values, callback context ownership, transaction boundaries, cache keys/limits and fallback behavior. Explain why a threshold or guard exists instead of narrating each line. When changing an invariant, update its source note, relevant source-index entry and regression evidence together.

Use these starting points to find existing coverage before adding a regression:

| Change area | Existing checks |
| --- | --- |
| Quantity, grid and review decisions | `test_currency_count_coverage.py`, `test_inventory_grid_geometry.py`, `test_ritual_review_rows.py`, `test_review_learning_ui.py` |
| Worker lifecycle and reusable artwork | `test_desktop_ocr_workers.py`, `test_startup_artwork_cache.py` |
| Logging, exports and Undo | `test_logging_adversarial_workflows.py`, `test_reference_export_snapshot.py`, `test_export_settings_guard.py`, `test_universal_undo_ui.py` |
| Kill observations and propagation accounting | `test_kill_count_ui.py`, `test_unique_propagation_counts_ui.py`, `test_chain_currency_workflow.py` |

These are navigation pointers, not substitutes for the full release gates or captured-image checks. Test files live under [tests/](tests/); read their fixtures to distinguish real pixels, injected OCR, independent export parsing and native input.

[packaging/sources.py](packaging/sources.py) lists first-party modules and required resources. The PyInstaller specification checks source completeness. New runtime helpers must be added there; asset directories retain their relative paths. Keep upstream notices and licenses with third-party files.

## Build and release

Run [Build.bat](Build.bat) on Windows with Python 3.12 and NSIS. It stops on failed packaging, runtime/model verification or installer compilation. Installer, executable and window version metadata must agree before release.

Publication uses artifacts from an exact verified source revision. Full regression, captured-image recognition checks, native Windows HUD, 100-map workflow, and installer/update/file-retention checks are required by the existing release process. They run side by side as separate Windows jobs of one workflow run, the full suite as six shards whose coverage the `regression` job verifies; publication requires every job and its gate steps to pass in that run's first attempt. Do not describe skipped native tests or a local build as a verified release.

Keep [CHANGELOG.md](CHANGELOG.md) and release notes focused on final behavior and validation. Put pending work under **Unreleased** and retain historical reports with their original scope. A documentation cleanup must not relabel old test evidence as proof for current source.
