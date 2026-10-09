# PoE2 Data Logger 1.3.2.4 Beta

- Add separate OCR Strictness sliders for each scan type in the OCR Sensitivity tab: 0 accepts more tentative matches, 50 retains previous behavior, and 100 requires manual confirmation. Keep semantic and capture-context safety checks.
- Save propagation chain parts directly from the recipe's Approve button; retain Complete chain for advancing the expedition and simplify the propagation review controls.
- Make the live Map # and Remnant # indicators larger and align beta app, installer and executable metadata.

# PoE2 Data Logger 1.3.2

- Promote the reviewed main-branch code to a stable installer with consistent executable, installer, window and release metadata.
- Provide recipe-only propagation dropdowns with rune artwork, confidence-grouped currency review and guarded linked seed corrections.
- Recover held waystone and tablet edits; keep Ritual raw evidence in developer tools.
- Fix fractional gear-rarity stepping and scaled scan-region resizing, and keep shared HUD controls accessible in smaller windows.
- Remove proven unused helpers and build triggers, retain old-data compatibility, and complete first-party function notes.

# PoE2 Data Logger 1.3.1.1 Beta

- Keep currency row approval separate from the final Start/End inventory commit; preserve the captured map and phase through review.
- Reconcile the currency counter and exports with the latest approved End snapshot, subtracting Start when present and treating a missing Start as empty.
- Export clean map and recipe records with named item columns, compact Atlas setups, and a separate scan history sheet. Log map totals and scanned modifiers once per map; preserve older folder exports.
- Require saved Atlas and gear settings before CSV or workbook export, and show clear choice badges and all numbered node effects.
- Preserve library and historical records when removing obsolete default currency cards; show found currencies first within their groups.
- Improve scaled inventory, Ritual rewards, deferred markers and seed capture recognition. Learn manually approved seed artwork without training rejected scans.
- Resolve propagation from three crown marks, their rune positions and the cursor-selected recipe, using the database for rune order. Gold frames and readable unmarked sockets are not prerequisites. Retain explicit recipe approval when the cursor is absent and keep unrelated scans independent.
- Add a 100-map GUI workflow gate with custom labels, inventory corrections, chain commits, automatic kill saves, restarts, backups and independently reopened CSV/XLSX exports.

# PoE2 Data Logger v33.3

- Prevent hotkey changes and region navigation from waiting on the logger's own window-title response.
- Restore existing shortcuts after a rejected assignment and keep the error visible.
- Cancel key capture when clearing a shortcut, leaving scan settings or hiding the HUD.
- Open the region selector directly from screenshot pixels without PNG compression.

# PoE2 Data Logger v33.2

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

# PoE2 Data Logger v33.1

- Preserve inventory phases and capture context through recognition and review; discard superseded and rejected results.
- Preserve unapproved waystone edits during refresh and correct deferred Omen and Ritual tribute associations.
- Keep partial-list family resolution and automatic approval for clear readings.
- Reuse inventory readers and cache unchanged icon matches without changing recognition thresholds.
- Correct numeric Excel fields and validate reference-pack round trips and imported screenshot data.
- Fix shortcut replacement, clipboard retries, capture size checks and scaled region selection.
- Serialize commit numbering and reject chain entries for finished maps.
- Correct packaged-module verification and installer resource paths; preserve saved Databases during uninstallation.

# PoE2 Data Logger v33

## Windows runtime

- The launcher locates the bundled Python 3.12 runtime in `_internal`.
- The package includes the complete PySide6 software-rendering DLL.
- The build checks the launcher, native DLLs and OCR models before creating the installer.
