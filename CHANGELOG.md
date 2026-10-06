# PoE2 Data Logger v33.2

- Preserve all tablet modifiers, including modifiers without a numeric percentage.
- Keep separate occurrences of the same Ritual Omen and their deferred states.
- Reuse icon matches for unchanged Ritual pages and skip matching uniformly empty captures.
- Honor corrected visible-seed reward mappings and keep remaining approvals usable after a new map starts.
- Save settings and tablet slots atomically, with rollback if their history commit fails.
- Cancel discarded captures, clear stale drafts after Undo, and prevent delayed references from linking to a different map or scan.
- Recover from failed recognition without leaving scans stuck or allowing stale results to replace a newer review.
- Fully decode and bound imported reference images, reject inflated rune vectors, and canonicalize recipe references across case differences.
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
