# Targeted review, normal-use and 4K scan fixes

Baseline: 1.3.1.2 Beta. These checks cover the pending fixes prepared for
1.3.1.3 Beta; installer verification is performed by the Windows release pipeline.
Changed functions include purpose docstrings or local notes; the function labels below identify the behavior being corrected.
Code notes describe function behavior, state handling and guards. Release/change notes remain in this document.

## Changes

- Cropped remnant panels are normalized before counting socket icons. The database's expected socket count cannot override visible geometry.
- Full-window captures check recipe-list completeness within the remnant panel. Complete single-reward Farrul's Rune of the Chase lists resolve to Family 76; incomplete captures remain ambiguous.
- Remnant and visible-seed reviews hide propagation entry and chain actions. An explicitly requested propagation scan displays a separate review while preserving the pending remnant's captured IDs and form. Returning to that existing review preserves accepted unsaved chain parts; a new unrelated scan still discards its unsaved draft and retains accepted counts.
- Automatic confirmation uses an opaque window and preserves maximized geometry. Normal application pages remain opaque; manually revealed HUDs use the configured opacity.
- Background screenshot preparation, including the GUI hide handoff and clipboard capture, runs outside the Windows hotkey listener. Capture reservation, cancellation and focus checks remain enforced.
- Propagation saves require verified rune spacing, the first tile's position and the recipe's socket span. Exposure-expanded crown frames cannot enlarge the spacing and shift the third marked rune to the second. Unproven positions hold for manual review. Three marks identify propagation; a gold frame remains optional.
- Each unclear recipe has an enabled **Enter runes** action. It selects that row and brings its manual fields into view; entering valid rune names changes the action to **Approve**. Switching or denying recipes clears their manual input. A scanned recipe list requires a waiting selected row before a manual part can be added.

| Function labels | Purpose notes for these review fixes |
| --- | --- |
| `_icon_count`, `_list_complete` | Socket counts and list completeness use the captured panel rather than the entire game window. |
| `_tile_layout`, `_row_reading`, `scan_propagation` | Verify observed rune spacing, first-tile position and complete socket span before assigning marked-rune positions. |
| `_review_pending`, `_review_controls`, `_show_independent_propagation_review` | Keep remnant and propagation review actions visibly separate while preserving a held remnant's captured identity. |
| `_propagation_recipe_selected`, `approve_propagation_recipe`, `_focus_manual_propagation`, `add_manual_propagation` | Attach manual rune input to one waiting recipe and keep those fields reachable below a long list. |
| `set_overlay_opacity`, `show_overlay`, `hide_overlay` | Reserve transparency for a manually revealed HUD; automatic confirmation and ordinary pages remain opaque. |
| `HotkeyManager.capture`, `HotkeyManager._capture` | Reserve one scan before starting its worker and keep screenshot preparation off the Windows message listener. |

## Ordinary-use corrections

| Function labels | Problem and corrected behavior |
| --- | --- |
| `start_manual_remnant_review`, `_clear_manual_propagation` | Returning from an unaccepted propagation scan left hidden choices blocking chain completion. Return now abandons those choices while retaining accepted parts, counts and the original pending remnant. |
| `_refresh_saved_chain_editor` | Browsing another expedition lost unsaved corrections. Corrections now stay attached to their session, map and expedition until saved; changing maps clears the drafts. |
| `update_chain_steps` | Some runes offered by the dropdown were rejected on Save. The validator now includes the known recipe-combination runes; invented names remain invalid. |
| `render_perks`, `save_perks` | Browsing another master lost unsaved perk selections. Drafts now remain attached to their master; Save commits only that master's selections. |
| `refresh` | An unrelated Save reset the chosen export folder. Refresh now preserves a changed folder field until export saves it. |
| `_map_setup_save_notice`, `save_map_settings` | A settings-save success could imply that an already logged map's frozen snapshot changed. Feedback now explicitly names the unchanged recorded map. Previous activity snapshots remain intact. |
| `GearRaritySpinBox.focusOutEvent`, `AtlasSettingsPage._request_save`, `_mark_dirty` | Starting a decimal with `.` could be erased by refresh or focus loss, turning `.75` into `75`. Intermediate text survives and cannot be saved as a different value; valid typed decimals still save. |

Ten focused GUI navigation checks exercise Return, completion, per-expedition correction drafts, new-map draft clearing, perk browsing, actual folder XLSX export and frozen-map feedback. Region-selection checks use a mocked native capture and dialog, including a mismatched unsaved resolution followed by recovery with Auto; the other navigation checks operate the Qt controls offscreen. Five new numeric-edit checks and the existing typed-number checks cover gear rarity.

## 4K capture and count corrections

| Function labels | Problem and corrected behavior |
| --- | --- |
| `region_for` | Legacy pixel crops have no source resolution or monitor metadata. At changed bounds the scanner now holds and requests reselection rather than capturing an unrelated area. Current normalized and default regions continue scaling against native game bounds. |
| `calibrate_region`, `save_regions`, `select_region_in_game` | Reselecting one region now saves that calibrated box without guessing dimensions for other old regions. Their legacy metadata remains intact until each is recalibrated. |
| `ScanRegionsPage.__init__`, `_resolution_changed`, `save_regions` | Added a **Game resolution** dropdown: Auto, 1080p, 1440p, 4K and common ultrawide sizes. The choice stays a draft until Save regions and survives reopening; changing it does not rewrite any region boxes. Bundled HD preview images remain usable with a 4K choice. |
| `validate_capture_resolution`, `region_for`, `HotkeyManager._capture`, `select_region_in_game.select_now` | Validate the full physical game-client size before cropping or capturing. A mismatched preset requests Auto or the correct game resolution. In-game selection validates the current unsaved dropdown value; cancelling does not persist it. No image is resampled to force a preset, and Windows DPI is not interpreted as game resolution. |
| `_native_monitor_bounds`, `_logical_capture_bounds` | The selector now matches the native display to its Qt screen and divides only offsets within that monitor by DPI. Capture and screenshot sizes must agree. Existing native window positioning is retained. |
| `count_crops`, `_inventory_labels`, `_ritual_cell_labels` | Fixed two-pixel glyph alignment could clip a large stack label to its first digit. Alignment scales with the cell, and verification requires a complete digit cluster. Agreement between OCR variants of the same clipped prefix cannot approve a count. |
| `scan_inventory_grid` | An available native glyph reading that disagrees with generic count OCR now keeps the count pending instead of silently trusting one reader. |
| `tier_crops` | Fixed gaps could split large II/III badges. Grouping now scales with the cell; unreadable badge evidence keeps shared-icon currency variants held for review. |
| `_ritual_grid_items` | A fixed one-pixel frame inset changed icon proportions at larger scale and lost known Ritual matches. Frame removal now scales with grid pitch for single-cell and learned multi-cell rewards. |
| `_footprint_pixels`, `_rewards_at_scale` | Dimmed large-item ornaments could split one Ritual item into several rows. A substantial consistent blue item backing now permits moderate exposure compensation for footprint geometry only. Item identity, count OCR and their confidence thresholds still use original capture pixels. |

Count regressions reconstruct labels from bundled captured game-digit glyphs and use the real RapidOCR count reader. Before the fix, a 135-pixel cell containing `30` was verified as `3`; after the fix it reads `30` at 54, 108 and 135 pixels. The corresponding `783` reconstruction reads in full at those sizes. A deliberately misaligned trailing `83` previously allowed an approved `7`; it now requires review and cannot auto-commit. That reconstruction is a guard regression, not a replay of the user's exact Verisium screenshot.

Seven focused count checks pass, including a Qt currency review with auto-commit enabled: disagreeing count readings show pending and create no inventory snapshot or commit. Existing empty/noise checks and a real populated-inventory check also pass. The badge regression verifies cropping geometry; it does not replay the user's blue-orb artwork.

Coordinate checks cover 4K bounds, negative and secondary-monitor origins, 100/125/150/200% DPI, partial legacy calibration and capture-size mismatch. These checks use mocked Windows monitor information and Qt offscreen geometry; native Windows rendering is not verified.
Six resolution checks cover Save/reopen, Auto at common and windowed sizes, matching manual presets, mismatch before legacy return, unsaved picker choices and early capture rejection. The additional GUI test exercises mismatch recovery using Auto without persisting a cancelled selection.

Ritual checks replay real bundled reward pixels at doubled scale. Before proportional frame removal, known Resurgence icons in `08.png` lost their names at doubled scale; both names and the equipment footprints now remain correct. Before the geometry correction, dimmed doubled `01.png` split 11 items into 23 rewards; exact item footprints are now retained in the focused cases at 70% and 50% brightness. A bounded probe also retained the expected footprints across all 15 bundled pages at original brightness/scale and doubled scale with 70% brightness.

The three new Ritual checks also retain actual reroll-button artwork while removing its numerals: no counter is then filled at original or doubled scale. Missing counter text in full-page OCR alone does not establish that the separately read counter was incorrect. Local-reference probes did not reproduce the latest screenshot's repeated Resurgence names on different artwork; that exact name mismatch remains unverified without the original capture and its local icon library.

The three focused Ritual checks, 13 existing grid-geometry checks and 13 existing grid-scanner checks passed after these changes. `git diff --check` is clean. These local checks do not build or run a Windows installer.

## Validation

Focused suites passed: opened-capture regressions, recognition contract, Farrul family repair, platform hotkeys, propagation hotkeys, listener responsiveness, overlay recovery, UI regressions, chain review visibility, chain draft lifecycle, hotkey UI, and remnant/propagation independence.

The propagation update passes 42 OCR tests, six mark-detection tests and 38 UI tests. A real bundled capture with its content scaled to 72% and brightness increased to 120% formerly accepted the second rune (Arcane); it now reads the marked third rune (Tidal). Related 74% and 75% variants hold rather than save a wrong rune when the first tile cannot be verified. A Family 65 fixture confirms **Orb of Alchemy x3 → third rune → Tidal**.

UI checks use the row buttons and editable dropdowns to select Prismatic Alloy, enter Opulent and approve only that part. Empty/invalid/denied selections cannot save; switching recipes cannot carry a stale rune into another reward. A clear rescan replaces the held review, retains the saved parts and enables completion. Separate propagation review and Return to remnant review retain the original pending IDs, form and recipe data.

A combined capture-to-HUD test feeds the real scaled capture through recognition and verifies the automatically saved Tidal part. An uncertain capture creates no extra count until the user selects its recipe and enters Tidal with the manual controls. Commit to chain retains the expedition ID, CSV contains the two corrected Tidal parts, and Complete chain advances the ID once.

The HUD session simulation completed 100 maps and 2,587 commits with zero ledger mismatches. CSV and XLSX were exported and independently reopened after maps 25, 50, 75 and 100. Coverage includes automatic commits, held reviews, item labels, currency corrections, chains, Atlas/gear setups, kills, restart persistence, and database backup.

The new currency confirmation test edits a quantity using the Qt editor and clicks Approve, then verifies the saved inventory and rolling total. Listener tests hold screenshot capture deliberately and verify that HUD keys and listener shutdown still respond.

## Limits

### Release-gate follow-ups

The first combined local run executed 894 tests and found five errors from an
older learned-artwork fixture missing the now-required Ritual grid pitch, two
outdated expectations exposing propagation entry in remnant/seed review, and a
real 720-pixel window-height failure after adding the resolution controls.

The learned-artwork fixture now supplies its actual 50-pixel lattice pitch.
Review visibility assertions enforce the requested activity separation and begin
the propagation case before staging unrelated remnant review. `RegionCanvas`
allows its preview to shrink to 160 pixels high, keeping region actions reachable
without forcing the whole desktop above 720 pixels. The small-screen check also
verifies the visible bounds of the resolution selector, canvas and Save regions.

The affected six-module run covered 68 cases; all but the revised fixture's
review-order case passed on that run. After correcting that test ordering, all 18
review-learning UI cases passed. The 16 installer metadata/source checks also
passed. The tagged Windows pipeline repeats the full regression suite before
building or publishing the installer.

### Skyfall / Celestial report

The user confirmed that Celestial itself carries the three propagation marks in the reported Skyfall capture. Saving Tempest is therefore incorrect; the mark belongs to Celestial, not an unmarked hovered tile.

The bundled database and current Runeshape source both list **Skyfall (Level 20)** as six sockets in this order: Tempest, Celestial, Protective, Ward, Wisdom, Oath. Recipe loading and persistence preserve that list order. `_row_reading` derives the propagated name from the marked tile's position in this list; it does not classify the marked glyph's identity. An incorrect origin or slot assignment can therefore save a valid but wrong rune name. This is a possible mechanism, not a reproduced cause for this capture.

Three checks in `tests/test_skyfall_propagation.py` pass. They compose actual bundled tile/crown pixels with a mark at the second position and supply controlled reward OCR. The clear case selects Celestial. Feeding that result through the review handler twice saves exactly one Celestial part and one receipt, exports Celestial once, counts one detonation, and retains the expedition ID. The bounded scaled/cropped cases return Celestial or hold for review.

These same bounded variants did not reproduce a wrong Tempest reading on either the published code or the working code. The tests verify the required second-slot behavior, but cannot establish that the reported failure is fixed. The exact original scan image and the user's recipe data were unavailable locally. No Skyfall recipe changes or additional recognition changes were made for this report.

The session simulation injects OCR results at scan ingress; recognition has separate capture regressions. The latest inline user screenshots were unavailable as local files, so their conditions were reproduced with an existing capture and controlled reward readings.

Qt offscreen checks cannot verify Windows game-overlay painting or native input/focus. A plain Qt window reproduces the offscreen activation limitation, so the editor test supplies that window-system activation before exercising real Qt input. Native Windows game-overlay confirmation remains unverified here.
