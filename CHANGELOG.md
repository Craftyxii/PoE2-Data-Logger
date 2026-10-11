# Changelog

## 1.3.2.13 Beta — draft

- Recover from cancelled, timed-out or crashed native OCR through bounded reusable child processes, preserving the existing deadline and stale-result guards.
- Clear failed scan admissions without leaving Reading or cancelling an unrelated capture.
- Hold malformed waystone property percentages for review and retain validated Ritual equipment under mild image compression.
- Recover propagation crown tips below overlapping recipe borders with corroborating evidence; preserve manual recipe/rune approval.
- Expand Analysis Context to version 3 with field definitions, units, catalog lookups and comparison guidance in workbook and SQLite exports.
- Retain all reviewed 1.3.2.12 fixes, screenshot previews, remnant logging and OCR strictness defaults.

Validation is pending; [draft release notes](docs/RELEASE_1.3.2.13.md) identify the required benchmark and release checks.

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
