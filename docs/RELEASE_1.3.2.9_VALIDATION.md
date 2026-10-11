# 1.3.2.9 Beta validation

The complete local discovery exercised **1,152 tests** in six isolated batches. It found three failures: partial seed approval omitted the new chain revision from its returned context, and two Skyfall regressions exposed frame speckles being counted as crown fragments. Both causes were fixed. The unchanged failing assertions pass in the final targeted runs below. Four additional test modules added during this audit contribute **17 tests** beyond the initial discovery.

| Final check | Result |
| --- | --- |
| Propagation marks, OCR, Skyfall, review UI and rune strictness | 140 tests, no failures/errors/skips |
| Core context, shortcut dispatch, strictness and scan admission | 33 tests, no failures/errors/skips |
| Chain contention, scan watchdogs and HUD task recovery | 18 tests, no failures/errors/skips |
| Every scan-mode watchdog and HUD storage-failure recovery | 10 tests, no failures/errors/skips |
| Real inventory capture regressions | 3 tests, no failures/errors/skips |
| Real Ritual panels and rendered waystone/tablet OCR | 4 tests, no failures/errors/skips |
| Broad existing HUD audit | 126 tests, no failures/errors; native Windows acceptance skipped locally |
| Packaging, previous-client guards and test-runner coverage | 47 tests, no failures/errors/skips |
| 100-map end-user workflow | 100 maps, 2,587 commits, 2,783,880 comparisons, zero mismatches |
| Compilation, whitespace and changed-function purpose notes | Passed |

Counts in this table overlap and should not be added. The initial discovery skipped two native Windows checks and the opt-in 100-map session; the latter ran separately and passed. Native Windows checks and actual installer/update verification are mandatory in the tagged release workflow before publication.

## Windows publication recovery

The 1.3.2.8 Windows build stopped before installer generation because an older platform test acquired a raw capture lock without recording the capture owner. Its completion correctly refused to release an unowned reservation. The fixture now uses normal capture admission with recognition held at the adapter boundary. Cancellation, suppression of the result and release of the owned lock remain asserted. No application timeout or lock guard was weakened. The new tag reruns every Windows release gate.

Recovery checks passed locally: 42 platform, cancellation, timeout, contention and navigation tests; 19 installer/version/publication tests. Neither run had failures, errors or skips.

## Findings and corrections

- Rejected worker submissions could leave a scan busy forever. Submission errors now follow normal failure handling and release the matching reading state. A future completing immediately can no longer leave stale working text.
- File and shortcut recognition waits are bounded at 90 seconds. Tests cover currency, Ritual, opened, visible seed, combined remnant mode, propagation, hovered items, waystones and tablets as applicable to each capture route. Late results cannot save data, overwrite newer reviews or release another shortcut capture's reservation. Queued futures are cancelled; a native reader already running may finish later, with its result discarded. Reset and close release timeout callbacks.
- Storage errors during task admission/completion or remnant-reservation cleanup leave a visible failed review with approval disabled, instead of retaining Reading.
- Fragment consensus mistook two-pixel frame speckles amplified by exposure adjustments for a second marked socket. Scale-adjusted vertical support now excludes those speckles. Full crowns, two independent fragment rows, single-recipe fragments, mixed socket evidence, ambiguity holds and unchanged recipe dropdowns remain covered.
- The Windows source manifest omitted the new Undo and analysis-context modules. Both packaging declarations and frozen-build verification now require them.
- A misspelled test selector could previously report success with zero tests. Empty selectors and empty child summaries now fail validation.
- The short SQLite contention fixture could release its writer before Qt first pumped a timer under concurrent OCR load. The fixture now retains real contention for bounded heartbeat evidence; approval latency, heartbeat counts, cancellation and exactly-once saving assertions remain intact.

Completed chains remaining editable is an intentional correction feature. The 100-map test was updated to check correction/discard availability rather than its previous locked-dropdown expectation. Completion still cannot run twice.

## Coverage and limits

The OCR checks use the bundled seed/opened captures, captured crown pixels, real inventory pixels, all 15 supplied Ritual reward panels at native and doubled size, and additional scale/crop/exposure fixtures. Waystone and tablet image tests run the actual OCR engine on rendered tooltips at four scales; this repository has no real captured waystone/tablet tooltip library. Resampling a capture does not establish accuracy on every native 4K display.

The 100-map session drives real Qt controls, storage, exports, SQL backups and restart persistence against an independent ledger, but injects scanner results. It is logging/workflow evidence, separate from image recognition. Offscreen checks do not establish real Windows taskbar focus, native pointer input or live-game behavior.

The HUD audit exercised every page at widths 900, 1024, 1366 and 1920 with both Developer Mode states and found no whole-page horizontal overflow. Thirty 60-row currency reviews and twelve chain saves left widget counts stable and no pending tasks. Traced timings under concurrent OCR load showed that rebuilding a full review can still cause a brief pause; this is not a zero-latency guarantee.

Function-purpose docstrings were checked for every changed Python function. These changes add no game-process access or game hooks.
