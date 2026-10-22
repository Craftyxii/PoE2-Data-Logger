# Frontend logging and snapshot sweep — 2026-10-22

Tested source: `489da8c06a99b912b0b5393bebf26671ff3c6ded` on `beta26-release`.
The installed beta26 tag remains `5e06ab49c71cbb7e3c8ed2e4c0de78f4bb0d9ba3`.
Later documentation/comments do not change the tested runtime behavior.

The user requested a bug sweep from the user's perspective, with confirmation
before classifying unexpected behavior and **no fixes**. The user then authorized
code annotations and maintenance notes. Both issues below were reproduced and
confirmed by the user. Neither is fixed by this documentation update.

## Confirmed issues

### Ritual: one gray equipment item becomes four review entries

- Source: [`264_tribute.png`](../tests/fixtures/ritual_grayscale/264_tribute.png),
  the original 684×660 capture documented in its [fixture README](../tests/fixtures/ritual_grayscale/README.md).
- The user confirmed the large gray armour at the lower left is **one 2×2 item**.
- Two real recognition runs produced separate footprints `[73]`, `[74]`, `[85]`,
  `[86]`. The real Qt review displayed all four entries. Three requested type/name;
  the fourth suggested `Breach Tablet` and requested reading confirmation.
- These were uncertain entries: the evidence does not show an incorrect
  automatic save. Quantity one on each fragment does not make the four fragments
  four independent items.
- Geometry originates in [`ritual_grid.py`](../PoE2_Data_Logger/ocr/ritual_grid.py)
  and is consumed by [`item_ocr.py`](../PoE2_Data_Logger/ocr/item_ocr.py).
  Do not solve this by hiding occupied cells or blindly accepting a guessed name.
- Existing grayscale tests preserve the old split geometry as a negative control.
  Their passing result does **not** establish correct grouping for this capture.
  Any future fix needs the original image as a regression for one whole item,
  with separate adjacent items checked independently.

### Currency: unnamed objects lose verified quantity-one evidence

- Sources: [`inventory_121050.png`](../tests/fixtures/currency_training/inventory_121050.png)
  and [`inventory_121548.png`](../tests/fixtures/currency_training/inventory_121548.png),
  inventory slots **2 and 49** in both captures.
- The production label reader returned `count=1`, `count_present=False`,
  `count_clipped=False`, `count_geometry_uncertain=False`, with no digit glyphs.
  The raw ordinary-unknown rows omitted quantity and count flags.
- The production Qt review consequently showed blank names/counts and required
  both name and count confirmation.
- The user confirmed the desired behavior: retain count **1** when no quantity
  marker is present and the count evidence is clear; ask only for the name.
  Identity remains uncertain and the row must remain in review.
- In `scan_inventory_grid` in [`item_ocr.py`](../PoE2_Data_Logger/ocr/item_ocr.py),
  ordinary unknown/ignored/tier branches bypass the named-stack quantity path.
  `_inventory_read` in [`native_desktop.py`](../PoE2_Data_Logger/ui/native_desktop.py)
  retains verified counts for proven `Item` footprints, but blanks ordinary
  unknown counts. Preserve genuine clipping, geometry and count ambiguity gates
  in any future fix; a missing marker alone must not erase verified single-count evidence.

## Validation

All **191 distinct targeted test cases** passed, with no failures, errors or
skips. One original Ritual workflow was repeated solely to retain UI/result
evidence; that repeat is not another distinct case.

| Scope | Distinct cases | Evidence exercised |
| --- | ---: | --- |
| Review and accounting UI | 62 | Currency/Ritual uncertain-only lists, hidden known rows retained for save, whole-item review, autosave, corrections, sequential pages, learned references, unique exports/Undo |
| Snapshot and navigation UI | 41 | Saved-preview locking, next-scan editing, map/phase ownership, real SQLite contention rollback/retry, stale callbacks, double approval, capture preview reuse |
| Logging, export and history | 73 | Chain/currency accounting, kills, Universal Undo, safe export destinations, independent backups, consistent CSV/XLSX/reference exports, draft cancellation |
| Actual screenshot recognition | 15 | Six transcribed Currency captures, all eleven inventory captures for footprints, Ritual 4K/grayscale examples, exact no-marker reward, original 264-Tribute recognition/review/exports/rescan |

A separate real-capture UI smoke used the production asynchronous isolated OCR
worker on `inventory_121050.png` and `inventory_121548.png`: 239 harness assertions
passed. It accepted and hid 37 and 48 clear stacks respectively, displayed 9 and
12 uncertain rows, exercised row rejection and final approval, checked saved
counts and independent Start/End subtraction, reopened the app, exported four CSV
companions through the UI action, and verified SQLite integrity. Those assertions
are not counted as 239 additional test cases. The unknown-count issue above was
identified by inspecting held rows beyond those assertions.

The separate opt-in **100-map session passed** in 643.53 seconds (10 minutes
44 seconds), with **2,587 commits and zero ledger mismatches**. It exercised
Start/End inventory and corrections, Ritual, waystones, tablets, Atlas settings,
remnants, seeds, propagation, chain completion/corrections, map totals and kills.
CSV/XLSX exports were reopened at maps 25/50/75/100; four app restarts retained
saved values, and the menu-generated SQLite backup was reopened independently
with integrity checked. This is one additional distinct automated case, making
**192 passed cases** overall, plus the separate real-capture smoke assertions.

## Reproduction and maintenance notes

The annotation audit found purpose headers in all **301 tracked first-party
Python modules**. This update fills **54** missing runtime class/function/method
docstrings; all **1,172** such definitions across the app and GPU worker now have
purpose notes. Existing build wrappers, PyInstaller specifications, installer
sections and release workflow already describe their responsibilities.
The [source index](../SOURCES.md) now includes all functional app modules and a
capture-to-export trace; the [developer guide](../CONTRIBUTING.md) identifies
invariants and existing tests to consult when maintaining those paths.

All changed Python files parsed and compiled successfully. Their executable ASTs
match the tested baseline after removing only docstrings; comments are ignored
by the parser. Tests, thresholds, type annotations, controls, layout, styling,
packaging and release tags were not changed. This verification establishes that
the annotation pass does not implement fixes for either issue above.

Use a disposable profile; never point these workflows at a user's database.
[`tools/run_tests.py`](../tools/run_tests.py) isolates ordinary test profiles.
See [CONTRIBUTING.md](../CONTRIBUTING.md) for prerequisites and the 100-map gate.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_tests.py --pattern test_uncertain_only_review.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_tests.py --pattern test_saved_snapshot_review.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_tests.py --pattern test_currency_training_captures.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_tests.py --pattern test_ritual_grayscale_workflow.py
POE2_RUN_HUNDRED_MAP_SESSION=1 POE2_SESSION_ARTIFACTS=/tmp/poe2-frontend-100-map QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_tests.py --pattern test_hundred_map_session.py
```

Workflow tests that inject recognition outputs establish review/accounting
behavior, not OCR accuracy. Actual screenshot tests establish behavior on their
fixtures, not every possible capture. Quantity transcriptions do not independently
prove catalog identities. The 100-map ledger is independent of saved app values,
but its OCR inputs are injected deliberately.

This sweep ran on Linux with offscreen Qt. It does not certify native Windows
hotkeys, physical pointer behavior, live game capture, game overlays or the
installer. No new Windows/game session was performed. CSV/XLSX outputs were parsed
programmatically, not opened manually in Excel. Keep these limits with the results
when reusing this report; passing checks do not negate the two confirmed issues.
