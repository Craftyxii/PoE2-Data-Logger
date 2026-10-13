# Changelog

## 1.3.2.17 Beta

- Complete eligible propagation family autofill for weak-text or barless recipients; preserve per-recipe socket order, short-recipe limits, conflicts and manual approval.
- Reuse crown readings for accepted unchanged panels and currency screenshot/grid previews at result delivery; changed crops and new captures remain independent.
- Fix opened-remnant selection-arrow/first-frame anchors and corroborated broken-border socket counts, independently of database expectations.
- Recover the misread Level 20 and missing Level 19 Thaumaturgic Flux first rewards using bounded captured evidence and existing quantity/level/ambiguity guards.
- Add one Activity Type selection per map and a Map Scanned indicator that accepts saved manual setup or an applied waystone scan, resets on New Map and supports Undo.
- Save combined applicable Atlas source totals from frozen allocations/choices, scoped to city, biome, Ocean and activity, with source/unit/condition evidence and distinct targets retained.
- Export Activity Type, Map Scanned and Atlas Effects JSON through Excel/CSV/SQL without rewriting historical snapshots or recorded map percentages.
- Expand Context with sourced rune/keyword meanings and explanations of the new map metadata and Atlas aggregation.
- Verify the new snapshot helper in the frozen runtime and retain update/uninstall protection for running 1.3.2.16 clients.

See [release notes](docs/RELEASE_1.3.2.17.md) for validation scope and publication gates. Currency recognition, OCR models/thresholds, screenshot review and explicit propagation approval are retained.

## 1.3.2.16 Beta

- Automatically save valid pending Atlas / Character Settings through the existing save action before confirmed Excel/CSV folder exports and Save As. Export stays on the current page; failed or invalid saves retain the draft and report an error.
- Verify spreadsheet entrypoints and real workers, all eight Excel sheets, CSV companions, automatic Atlas saving, cancellation, validation/storage failure, clean drafts and existing frozen-map rules. SQLite backups keep their existing behavior.
- Retain complete Currency inventory count-of-1 evidence when wider retry crops include item artwork. Independent native agreement can restore that count; conflicts remain under manual review. Preserve valid multi-digit stacks and existing clipping recovery.
- Update installer and version labels while retaining running-client protection for 1.3.2.15.

Changes are limited to the requested spreadsheet export autosave and inventory quantity reconciliation. Context/reference sheets, other OCR modes and logging rules, export file generation and HUD controls are retained. Publication requires the established exact-source Windows gates; see [release notes](docs/RELEASE_1.3.2.16.md).

## 1.3.2.15 Beta

- Deliver scan completions without waiting on a competing database writer. Retain recognized Currency/Ritual rows for approval retry, and briefly retry unpresented remnant reservation work without rerunning OCR or replaying a saved result.
- Preserve capture ownership during delayed failure cleanup and retain independent propagation reviews. Roll back failed Snapshot and shortcut changes to their saved state.
- Serialize Currency, Item and Omen name registration with reference imports so category checks and writes are atomic.
- Autofill the existing Biome selector from an allocated, active Atlas city-biome choice. Preserve frozen map settings, manual edits and Undo; restore the prior Biome when the older empty-map cancellation path reverses map creation.
- Expand Excel Analysis Context and equivalent SQLite views with field meanings, Atlas node/choice and master-perk definitions, source-scoped totals and analysis limits. Append Family Database, Recipe Database, Seed Database and Reference Aliases worksheets after the original four.
- Refresh project, user and developer documentation and index historical release evidence separately.

OCR recognition rules and the HUD layout remain unchanged. Publication requires the exact-source Windows release gates described in [the release notes](docs/RELEASE_1.3.2.15.md).

## 1.3.2.14 Beta

- Fill the existing propagation rune dropdowns from verified shared family sockets instead of clearing the entire family because of duplicate anchors or gold glyph glints.
- Recover interrupted tile-frame contours from validated visible tile bodies and a verified sibling lattice; retain quantity, text, origin and conflicting-mark guards.
- Apply each shared socket only to recipes containing that socket, preserving valid selections on longer recipes without inventing sockets on shorter ones.
- Keep the manual review layout, screenshot preview, dropdown options and explicit propagation approval unchanged. Remnant logging and Analysis Context are retained.
- Hold incomplete decimal and malformed sign tokens in Tablet and Waystone readings for review instead of saving an incorrect positive value.
- Validate catalog relationships inside the writer transaction; resolve Unicode-equivalent icon labels consistently and enforce reference-export image budgets during collection.
- Release cancelled capture admission promptly while bounding unfinished capture adapters and reporting a retryable busy state.
- Fail promptly on contended user saves, retain drafts, refresh reference Undo controls and restored artwork, and finish shutdown cleanup after a listener error.
- Align region picker and Save minimum dimensions; roll back failed screenshot saves and remove newly created unreferenced images.

See [release notes](docs/RELEASE_1.3.2.14.md) for original-capture accuracy, regression coverage and installer gates.

## 1.3.2.13 Beta

- Recover from cancelled, timed-out or crashed native OCR through bounded reusable child processes, preserving the existing deadline and stale-result guards.
- Clear failed scan admissions without leaving Reading or cancelling an unrelated capture.
- Cancel discarded remnant readers and retire unrecoverable reservations on desktop restart so stale scans cannot keep blocking later work.
- Preserve active readers, watchdogs and review drafts when a database failure prevents Discard or Reject, allowing a safe retry.
- Hold malformed waystone property percentages for review and retain validated Ritual equipment under mild image compression.
- Recover propagation crown tips below overlapping recipe borders with corroborating evidence; preserve manual recipe/rune approval.
- Expand Analysis Context to version 3 with field definitions, units, catalog lookups and comparison guidance in workbook and SQLite exports.
- Retain all reviewed 1.3.2.12 fixes, screenshot previews, remnant logging and OCR strictness defaults.

Publication requires the captured-crown benchmark, full regression suite, native Windows HUD, 100-map workflow and installer/update checks described in the [release notes](docs/RELEASE_1.3.2.13.md).

## 1.3.2.12 Beta

- Preserve visible currency stack digits instead of accepting a truncated count. Uncertain quantities still require confirmation.
- Keep recognized Currency names filled in and request **Confirm count** when only the quantity needs review.
- Keep validated Ritual equipment in one reward row and join supported wrapped waystone modifiers.
- Retain seed corrections and rejected rows when switching review modes; preserve Family DB drafts and selected seed families during unrelated refreshes.
- Open catalog results in the correct editor after changing the next search type.
- Make reference reset, removal, import and automatic affix rediscovery compatible with Universal Undo. Group screenshot/name creation and seed approval/learning into single actions.
- Preserve restored retired names in reference packs, including names without icon examples.
- Correct Unicode recipe identity conflicts and related historical propagation records without changing remnant logging.

Screenshot previews, manual propagation approval, recipe-limited rune selectors and OCR strictness defaults remain unchanged.

Validation passed: the full regression suite, native Windows HUD input, 100-map logging/export workflow, installer/update and saved-file retention checks. See [release notes](docs/RELEASE_1.3.2.12.md) for counts and scope.

Versions 1.3.2.10 and 1.3.2.11 were unpublished builds. Their fixes are included in 1.3.2.12.

## 1.3.2.9 Beta

- Add persistent Universal Undo for saved scans, chains, counts, settings and New map; retain the latest 100 actions across restarts. Undoing New map reopens the previous map for editing.
- Add **Discard chain** and **Discard last chain step** on Expedition, with Undo support and retained remnant logs and detonation counts.
- Recover shared propagation socket positions from complete crowns or corroborating fragments across recipe rows. Keep conflicting evidence unresolved and require manual approval.
- Retain stable recipe and family IDs for propagated runes.
- Add an **Analysis Context** sheet to Excel exports and equivalent interpretation notes to the raw SQL database.
- Improve enlarged opened-remnant icon counting and reject false crown fragments from frame speckles.
- Clear Reading after scan submission failures or a 90-second timeout; prevent late results from replacing newer reviews or saving data.
- Preserve partial seed capture context after an implicit map start and reject test selectors that match no tests.

These changes were prepared in the unpublished 1.3.2.8 build and first shipped in 1.3.2.9.

## 1.3.2.7 Beta

- Clear stuck Reading states after invalid New map input and stop cancelled scans before subsequent capture, clipboard or OCR work.
- Report shortcut listener failures and clean up registrations.
- Keep resets and remnant writes consistent, without orphan detonation records or stale next-map markers.
- Improve opened-remnant recognition in enlarged captures and retain ambiguous tablet numbers for review.
- Preserve existing symbolic links when paired exports roll back.
- Recognize the previous beta during installation and single-instance checks.

## 1.3.2.6 Beta

- Require manual recipe/rune approval for every propagation scan; strictness adjusts recognition without enabling automatic chain saves.
- Retain source recipe/family attribution for approved propagation runes, including unresolved origins for later analysis.
- Add Currency suggestion dropdowns and keep the user's place after row approval or rejection.
- Avoid rebuilding unrelated controls on chain approval; reuse saved rune controls and retry SQLite contention without blocking the HUD or duplicating accepted parts.
- Restore the opaque app after external overlay activation or taskbar restore and repaint its client area without clearing held reviews.
- Compact shared layouts and Currency/Ritual previews; wrap long guidance and keep recipe rune dropdowns readable.
- Retain the map's remnant number across chain completion and preserve propagation corrections when independent remnant scans finish.
- Keep conflicting stack counts for manual review, and protect pending Currency/Ritual context from expedition changes.
- Add reference reset and complete raw SQLite export; preserve older backups/reference exports and block writes over the active database.
- Reduce redundant currency-card redraws and update previous-beta running-client guards.

## 1.3.2.5 Beta

- Enlarge live map/remnant counters and make Complete chain available in the shared header.
- Refresh saved Expedition runes immediately after recipe approval, retain independent manual drafts, and keep chain status visible.
- Size pages from the active tab to keep scan-region controls reachable in shorter windows.

## 1.3.2.4 Beta

- Add separate OCR Strictness sliders for each scan type in the OCR Sensitivity tab: 0 accepts more tentative matches, 50 retains previous behavior, and 100 requires manual confirmation. Keep semantic and capture-context safety checks.
- Save propagation chain parts directly from the recipe's Approve button; retain Complete chain for advancing the expedition and simplify the propagation review controls.
- Make the live Map # and Remnant # indicators larger and align beta app, installer and executable metadata.

## 1.3.2

- Promote the reviewed main-branch code to a stable installer with consistent executable, installer, window and release metadata.
- Provide recipe-only propagation dropdowns with rune artwork, confidence-grouped currency review and guarded linked seed corrections.
- Recover held waystone and tablet edits; keep Ritual raw evidence in developer tools.
- Fix fractional gear-rarity stepping and scaled scan-region resizing, and keep shared HUD controls accessible in smaller windows.
- Remove proven unused helpers and build triggers, retain old-data compatibility, and complete first-party function notes.

## 1.3.1.1 Beta

- Keep currency row approval separate from the final Start/End inventory commit; preserve the captured map and phase through review.
- Reconcile the currency counter and exports with the latest approved End snapshot, subtracting Start when present and treating a missing Start as empty.
- Export clean map and recipe records with named item columns, compact Atlas setups, and a separate scan history sheet. Log map totals and scanned modifiers once per map; preserve older folder exports.
- Require saved Atlas and gear settings before CSV or workbook export, and show clear choice badges and all numbered node effects.
- Preserve library and historical records when removing obsolete default currency cards; show found currencies first within their groups.
- Improve scaled inventory, Ritual rewards, deferred markers and seed capture recognition. Learn manually approved seed artwork without training rejected scans.
- Resolve propagation from three crown marks, their rune positions and the cursor-selected recipe, using the database for rune order. Gold frames and readable unmarked sockets are not prerequisites. Retain explicit recipe approval when the cursor is absent and keep unrelated scans independent.
- Add a 100-map GUI workflow gate with custom labels, inventory corrections, chain commits, automatic kill saves, restarts, backups and independently reopened CSV/XLSX exports.

## v33.3

- Prevent hotkey changes and region navigation from waiting on the logger's own window-title response.
- Restore existing shortcuts after a rejected assignment and keep the error visible.
- Cancel key capture when clearing a shortcut, leaving scan settings or hiding the HUD.
- Open the region selector directly from screenshot pixels without PNG compression.

## v33.2

- Preserve all tablet modifiers, including modifiers without a numeric percentage.
- Keep separate occurrences of the same Ritual Omen and their deferred states.
- Reuse icon matches for unchanged Ritual pages and skip matching uniformly empty captures.
- Honor corrected visible-seed reward mappings and keep remaining approvals usable after a new map starts.
- Save settings and tablet slots atomically, with rollback if their history commit fails.
- Cancel discarded captures, clear stale drafts after Undo, and prevent delayed references from linking to a different map or scan.
- Recover from failed recognition without leaving scans stuck or allowing stale results to replace a newer review.
- Fully decode and bound imported reference images, reject inflated rune vectors, and canonicalize recipe references across case differences.
- Match large inventory-reference collections in bounded batches and keep competing names visible when one item has several examples.
- Check foreground focus before copying and reading clipboard text.
- Resolve the installer's permission tool from the Windows system directory and check installation with a planted filename present.

## v33.1

- Preserve inventory phases and capture context through recognition and review; discard superseded and rejected results.
- Preserve unapproved waystone edits during refresh and correct deferred Omen and Ritual tribute associations.
- Keep partial-list family resolution and automatic approval for clear readings.
- Reuse inventory readers and cache unchanged icon matches without changing recognition thresholds.
- Correct numeric Excel fields and validate reference-pack round trips and imported screenshot data.
- Fix shortcut replacement, clipboard retries, capture size checks and scaled region selection.
- Serialize commit numbering and reject chain entries for finished maps.
- Correct packaged-module verification and installer resource paths; preserve saved Databases during uninstallation.

## v33

### Windows runtime

- The launcher locates the bundled Python 3.12 runtime in `_internal`.
- The package includes the complete PySide6 software-rendering DLL.
- The build checks the launcher, native DLLs and OCR models before creating the installer.
