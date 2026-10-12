# Working-source regression review

This review was recorded before release preparation. It covers the then-uncommitted scan-completion, Snapshot/shortcut, catalog-registration, Atlas/Biome and Context/reference-export changes based on published source `a7f7965f0576cf5795fa21dc94d4c6c88bfec091`, plus the additional correction below. It is not a release validation report or a full guarantee of application correctness. See [1.3.2.15 release notes](RELEASE_1.3.2.15.md) for subsequent release preparation and gate requirements.

## Confirmed new regression and correction

The older `logger_store.undo_empty_map()` backend function returned to the previous map but retained the Biome automatically applied while starting the cancelled map.

Reproduction on a disposable profile:

1. Allocate Faridun's Desert choice and create recorded activity on M0001.
2. Set the editable Biome to Island and finish M0001.
3. Start M0002: its Atlas classification correctly fills Desert.
4. Cancel that activity-free map with `undo_empty_map()`.
5. Before the correction, the active map returned to M0001 while editable Biome stayed Desert instead of Island.

The current desktop **Undo** uses `undo_last_action()` and already restored Island correctly. This defect affected the older backend cancellation function; no new control or changed desktop layout was required.

`start_map()` now retains the prior Biome in the same transaction. Empty-map cancellation restores that value, session reset clears it, and Universal Undo journals its metadata. Profiles without the new value retain their existing selection rather than inventing a prior Biome. Earlier recorded map snapshots are preserved.

Three added regression cases cover cancellation after restart, cancelling the first empty map, and an older profile missing the new metadata. The previous-map restoration assertion failed before the fix and passed afterward.

## Verification

| Check | Outcome | Scope |
| --- | --- | --- |
| Completion, Snapshot/shortcut, catalog race and Atlas/Biome regressions | 42 passed | Review of the prior change set, before the additional empty-map correction |
| Atlas/Biome, empty-map Undo, map setup, Universal Undo and Unique kills | 74 passed | After the correction; includes the three new cases |
| Context, unit translations, observation totals, reference worksheets, packaging checks, hotkeys and Universal Undo UI | 79 passed | After the correction |
| Documentation links | 153 local links checked, none broken | Updated README, guides, source/dependency index and changelog |
| Source index | All functional modules in the packaging manifest indexed | Package-marker files are excluded from the functional index check |
| Whitespace/patch check | Passed | `git diff --check` |

Counts in different batches overlap and must not be added as a distinct-test total. Tests use disposable profiles. SQLite contention cases use real competing writers; recognition results are controlled where testing completion and review behavior. Those cases do not measure live OCR accuracy.

Source tracing checked captured map/expedition/generation and reservation ownership, bounded presentation retries, callback replay boundaries, saved-vs-attempted shortcut registrations, catalog validation inside writer transactions, frozen Atlas contexts, read-snapshot exports and ordinary-SQL Context views. No additional confirmed new regressions were found in this targeted pass.

Checks ran on Linux with Qt offscreen. Native Windows focus/overlay input and installer/update gates were not executed for this working source. No commit, version bump, build or publication was performed.

## Documentation cleanup

The project README now separates the published installer from pending source changes, explains all eight workbook sheets and distinguishes inventory gains from proven drops. The user guide and in-app help describe current review, chain correction and export behavior. Developer setup and release instructions are in `CONTRIBUTING.md`; `docs/README.md` indexes current guides and historical release/audit evidence. The source index includes the new Context/reference helpers and existing worker/Undo modules. Third-party notices and historical release records are retained.
