# PoE2 Data Logger 1.3.2.21 Beta

This beta continues from 1.3.2.20 with the confirmed review, capture and data-retention fixes.

- Recognize grayscale Ritual Omen shapes and offer complete supported names in editable dropdowns. Keep visually indistinguishable siblings ambiguous after learning a correction, retain screenshot evidence and manual approval, and detect Deferred separately.
- Preserve visual Waystone modifier rows after tooltip isolation so OCR fragments do not become extra saved modifiers. Retain supported Normal rarity headers at larger text scales.
- Protect screenshots accepted by another writer or retained by Undo when a failed reference import cleans up newly created files.
- Restore auto-save and HUD preference controls after failed settings writes while keeping review and manual-entry drafts.
- Use exact recipe identities for propagation rune choices, retaining distinct imported case variants and holding ambiguous names for review.
- Read selected kill-count regions as crops and restore a previously visible HUD after an explicit kill-scan failure. Preserve full-screen reading and unobtrusive successful snapshots.

The HUD layout, existing manual entries, historical logs and mandatory propagation approval are preserved. Runefather/Runebinder alloy corrections remain unapplied. Installer, executable and window labels identify 1.3.2.21 Beta; update and uninstall guards protect running 1.3.2.20 Beta clients alongside older versions and retain saved database files.

## Validation and publication gates

Before release-only version synchronization, the frozen source completed the Linux regression run with **1,919 tests: 1,917 passed, zero failures/errors and two native Windows skips**, across 64 subprocess batches. Its injected-output Qt workflow completed **100 maps and 2,587 commits with zero mismatches**, reopened CSV/XLSX exports at four milestones, and checked a SQLite backup and restart persistence.

Separately, the supplied Ritual image was checked at its original and doubled sizes through the real image reader, Qt dropdown review, approval, learned-reference storage, repeat-image ambiguity and CSV/XLSX exports. This focused captured-image proof and the injected workflow do not establish independent raw-corpus accuracy or live game review latency.

The release-only metadata checks do not replace the pending Windows gates. Publication requires the final committed source to pass Windows display/export fixtures, real native HUD input checks, the 100-map workflow, complete regression suite, packaged runtime verification and installer/update/saved-file-retention checks. The verified publisher binds those reports and the installer checksum to the exact source SHA and successful first-attempt build run. Skipped native tests do not count as release proof.
