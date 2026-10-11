# PoE2 Data Logger 1.3.2.12 Beta

This release fixes incorrect currency quantities, lost review edits and reference changes that could block Universal Undo.

## Fixes

- Preserve visible currency stack digits when icon fragments interfere with recognition. A captured stack of 14 no longer becomes an automatically approved 1; uncertain quantities remain pending.
- Keep recognized Currency names filled in. Show **Confirm count** when only the quantity needs review, without requiring the name to be typed again.
- Keep validated multi-cell Ritual equipment together instead of splitting one reward into several rows.
- Join supported wrapped waystone modifiers without counting the continuation as an extra modifier. Ambiguous boundaries still require review.
- Retain corrected and rejected seed rows when switching between Seed and Opened review. Preserve Family DB drafts and selected seed families during unrelated refreshes.
- Open displayed catalog results in the correct editor after changing the next search type.
- Make reference reset, removal and import compatible with Universal Undo. Group screenshot/name creation and seed approval/learning into single actions. Automatic affix rediscovery reverses with the latest saved action, without replacing its Undo label.
- Preserve explicitly restored retired names in reference packs, including names without saved icons.
- Correct Unicode recipe identity conflicts and related historical propagation records.

Screenshot previews, manual propagation approval, recipe-limited rune selectors, remnant logging and OCR strictness defaults remain unchanged. Changed code functions retain purpose notes.

## Validation

- Regression suite: **1,218 passed**. Two dedicated tests were skipped in that run and passed separately in the required native HUD and 100-map runs.
- Native Windows HUD: **22 phases and 902 Win32 input events**, including review, overlay/taskbar activation, reference reset and raw SQL export.
- Logging/export workflow: **100 maps, 2,587 expected saved commits and zero ledger mismatches**, including CSV/XLSX checks, backups and restart persistence.
- Installer, update, running-client guards, startup, recognition and saved-file retention checks passed. The published installer checksum was verified.

The 100-map workflow injects recognition results; captured-image OCR regressions are separate checks. Native HUD tests use a simulated game view.

[Download installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.12-beta/PoE2-Data-Logger-Setup-v1.3.2.12-beta.exe) · [Release validation](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/38116647068) · [Detailed audit](DEEP_BUG_AUDIT.md)

Builds 1.3.2.10 and 1.3.2.11 were not published. Their fixes first ship in this release.
