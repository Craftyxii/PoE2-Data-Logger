# PoE2 Data Logger 1.3.2.18 Beta

This beta fixes the two confirmed map-context problems from the launch review and the subsequently reported white-waystone and manual-propagation save failures. It carries forward the features from [1.3.2.17](RELEASE_1.3.2.17.md).

## Changes

- Undo New map reopens the previous map's setup for correction. Saving a correction updates that map's saved snapshot and current export; advancing with New map freezes the corrected setup again. For example, correcting Waystone 40 to 99 after Undo now exports 99 for the reopened map. Earlier scan history and future Atlas, tablet and master settings remain separate from that correction. Ordinary recorded-map setup protection remains in place.
- Maps with Deli enabled include allocated Delirium coefficients in their applicable Atlas totals, independently of the selected primary Activity Type. The totals retain each source, unit and catalog condition; a conditional coefficient is not represented as an unconditional realized bonus.
- Accept verified Normal waystones with zero drop-chance bonus and zero modifiers. Omitted properties on an unverified or incomplete tooltip remain unknown and require review.
- Make manual propagation entry select a waiting recipe when no row is selected, and retain the selected recipe's available context when saving its manually entered runes. Existing row selection, explicit approval and stale-capture protection remain in place.

The added Delirium data can push a large Context entry past Excel's cell limit. Oversized Guidance now continues in clearly named columns after the existing four columns; concatenate those cells without separators to recover the exact SQL/CSV text. Ordinary entries retain their existing layout.

The application changes address these reported failures. The HUD layout, screenshot review, rune dropdowns and explicit propagation approval are retained. Installer, executable and window labels identify 1.3.2.18 Beta; update/uninstall guards also protect running 1.3.2.17 clients.

## Validation and publication

Targeted regressions cover the reproduced Undo/edit/advance/export sequence, saved Delirium totals, complete and incomplete Normal waystones and manual propagation saving, alongside the existing map, Atlas, Undo and export contracts. The published release notes record the completed Windows results for the final source.

Publication requires the complete regression suite on the exact release source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, installer/update/saved-file-retention checks and the installer checksum. The published notes will identify the verified source SHA, Windows run and measured test results. Earlier release evidence is not a substitute for these gates.

Waystone text handling changes to support verified zero-modifier Normal maps. OCR models and confidence thresholds are unchanged. Any referenced captured-image accuracy evidence retains its original scope and may be reused only after verifying that its recognizer, model, reference and input dependencies are unchanged. Injected-result HUD/session tests do not measure OCR accuracy.
