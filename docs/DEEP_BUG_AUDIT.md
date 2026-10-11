# Deep bug audit

Audit baseline: `b1deccd4c36e1ea0ce6ccf38b2179cfc2f9c3be8` (`1.3.2.9 Beta`). Fixes below were prepared in `1.3.2.10 Beta` and published as `1.3.2.12 Beta` after correcting two test-harness failures.

## Confirmed defects

| Defect and user-visible effect | Cause and correction |
| --- | --- |
| A fully visible currency stack of **14** could become an automatically approved **1**. | Nearby icon fragments invalidated the complete digit crop and erased evidence that a count existed. Preserve anchored digit evidence; uncertain counts remain pending. Actual captured pixels now produce 14 and require confirmation. |
| A wrapped waystone modifier could count as an extra modifier and pass automatic approval. | The old parser joined only very short continuations. Join syntactically supported wraps; hold unproven boundaries for modifier-count confirmation. Independent modifiers retain separate rows. |
| A Ritual staff became seven separate rewards after modest desaturation. | One dark interior cell failed the ordinary occupancy test, discarding an otherwise validated equipment rectangle. Substantial item backing can fill that interior cell only inside independently verified paired ornate corners. |
| Switching Seed → Opened → Seed revived rejected rows and erased manual corrections. | The retained capture still held the original OCR queue. Retain the edited/rejected queue in its capture immediately; a fresh scan still starts a fresh queue. |
| An unrelated setting save or export completion erased Family DB drafts and changed the selected seed family. | Refresh rebuilt selectors and unconditionally loaded the family form. Preserve dirty family fields and the seed-family selection; deliberate family changes still load the selected stored record. |
| Double-clicking displayed catalog results after changing the next search type used the wrong editor or raised an exception. | Loading consulted the current search selector instead of the displayed result type. Bind each result set to the type used for its search. |
| Resetting, removing or importing OCR references could strand older scan approvals in Universal Undo. | Those reference writes changed saved approval row images without entering the Undo chronology. Journal supported reference mutations, capture SQLite REPLACE deletions, and ignore transactions with unchanged final row images. Seed approval plus learning and Add screenshot plus its new name each remain one command. Automatic waystone/tablet affix discovery appends its inverse to the latest saved action without replacing its label. Undo reverses that discovery together with the action, including a deleted affix rediscovered after reset. Raw screenshot bytes are retained. |
| Reference sharing lost explicitly restored retired item/Omen names without icons. | Packs omitted the metadata distinguishing user-restored names from obsolete bundled defaults. Transfer validated optional revival metadata transactionally; older packs keep their original retirement behavior. |
| Unicode case variants could log the wrong recipe ID; historical distinct variants could not roundtrip through reference packs. | SQLite NOCASE handles ASCII while deterministic IDs used Unicode casefold. Resolve canonical names consistently; preserve distinct exact legacy identities and repair demonstrably wrong historical origins, retry receipts, discard snapshots and Undo images together. Exact tombstones preserve re-added identities. Ambiguous nonexact names are rejected. |
| Currency asked to confirm an already recognized saved name when only its count was uncertain. | Pending rows used generic name/count instructions. Track the fields awaiting confirmation and request only those fields; the recognized name remains editable and needs no retyping. A real saved-reference rescan reproduces this path. Ambiguous suggestions and strictness holds still require review. |

## Intended behavior preserved

Propagation remains manually reviewed with the existing recipe-limited selectors. Completion advances the expedition without resetting remnant numbering. Discard and Undo retain their separate purposes. Existing uncertain scans, unknown names, review flags and explicit manual holds remain supported workflows. No game integration, scan modes or sensitivity defaults were changed.

## Validation

The published 1.3.2.12 Beta passed all required Windows release gates:

- Full regression suite: 1,218 passed. The two dedicated native HUD/session tests were skipped in this suite and passed in their separate required runs.
- Native Windows HUD: 22 phases and 902 Win32 input events, with reference-reset preservation and raw SQL export checks.
- 100-map logging/export workflow: 2,587 expected saved commits and zero independent-ledger mismatches; CSV/XLSX exports, backups and restart persistence verified.
- Installer/update, running-client guards, startup, recognition and saved-file retention checks passed. The downloaded installer matched its published SHA-256 checksum.

See [the successful release workflow](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/38116647068) for logs and artifacts.

### Development checks

- Dedicated regressions reproduce the failures above and verify their corrected results, including actual captured inventory/Ritual pixels and rendered tooltip OCR.
- HUD audit exercised 498 control targets across compact and large windows. Modal crop selection, keyboard edits, rejected-row state and actual export callbacks were tested through Qt events.
- A blocked-reader probe exercised 12 timeout/retry cycles: two bounded worker threads, no stranded Reading state, no stale saves, and successful recovery with quantity 292.
- Fifty subsequent review cycles retained exactly 2,474 widgets, zero pending tasks and zero watchdogs. Idle CPU was 0.0178 seconds over 1.501 seconds.
- The local 100-map Qt session passed with 2,587 expected saved commits and zero independent-ledger mismatches, including restart, backup, reference sharing, discarded steps, Undo and spreadsheet/CSV exports (1,223.55 seconds). This run preceded the final affix-Undo correction; the Windows release gate repeats the session on the release commit.
- Final Currency checks passed: 12 dropdown/review tests plus one real saved-reference scanner-to-UI test. Twenty-two reference security/Unicode identity tests passed after preserving the existing duplicate-record rejection wording.
- Full regression discovery ran 1,210 checks across six isolated batches (1,748.14 seconds; three opt-in/platform skips). Two failures were resolved: existing import-error wording compatibility and automatic affix discovery taking over Undo while a waystone was still pending. The affected importer/identity modules passed all 22 checks; UI, Universal Undo, reference Undo and waystone recovery modules passed all 76 checks after correction. Additional regressions cover deliberate versus automatic affix learning and the pending-tablet path. The late Add screenshot command grouping also passed its 13 catalog/revival checks.

The final release review additionally reproduced reset → automatic affix rediscovery → Undo through the held-tablet UI. The correction journals implicit discovery with the latest action; dedicated regressions also cover repeated discoveries and discovery without prior Undo history. All 89 affected UI, Undo, reference and waystone-recovery checks passed after that correction. An independent HUD replay verified both reset/rediscovery/Undo and New map/discovery/Undo. All 19 installer/version checks passed; all 148 changed Python functions have purpose notes.

Local HUD tests use Qt offscreen on Linux; the separate Windows release gates above passed on the published source commit. Native interaction tests and OCR fixtures cannot exhaust every user action sequence or establish perfect live-game recognition.

The first native release run stopped at the reference-reset preservation assertion: it included `undo_actions` and `sqlite_sequence`, both intentionally changed by the new reset inverse. The corrected assertion checks exactly one reset action, unchanged earlier inverses and unchanged unrelated sequences, while retaining all logging/settings preservation checks. No 1.3.2.10 installer was published.

The 1.3.2.11 Windows run passed native HUD interaction (902 Win32 input events), reference reset preservation, raw SQL export and the 100-map logging/export session. The full suite ran 1,220 checks with one failure: the family-draft export test's repeated `QTest.qWait` polling left its Python worker pending. A scheduler diagnostic with the real export reproduced starvation; event processing plus a brief Python sleep completed all three CSV files and retained the draft in 0.13 seconds. The test now yields to its worker while retaining the same 10-second deadline. No application export logic changed, and no 1.3.2.11 installer was published.

A captured-image HUD probe also verified that screenshot previews remain visible. The earlier native Currency/Ritual correction fixtures injected recognition results without attaching images; their screenshots were not evidence that the application's preview had been removed.
