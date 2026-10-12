# PoE2 Data Logger 1.3.2.15 Beta

This beta keeps recognized scan results usable during database contention, connects applicable Atlas city-biome choices to the existing Biome selector, and makes Excel/SQLite exports self-contained for analysis. It includes the fixes and propagation recognition behavior shipped in [1.3.2.14](RELEASE_1.3.2.14.md).

## Changes

- Scan completion no longer waits on a competing SQLite writer. Recognized Currency/Ritual rows remain available for an approval retry. Unpresented remnant reservations retry briefly without rerunning OCR or replaying a saved callback; expiry leaves a rejectable failed review rather than an indefinite Reading state.
- Delayed remnant failures clear only their owned reservation and retain a newer independent propagation review. Failed Snapshot and shortcut changes restore their saved selection/registration.
- Currency, Item and Omen name registration performs category validation and writes under one writer reservation, preventing races with reference imports.
- Allocated, active city-biome Atlas choices fill the existing selector: Faridun → Desert/Mountain, Ezomyte → Grass/Swamp, Vaal → Forest/Water. These are additional classifications. Manual edits remain available; recorded maps keep their frozen Atlas. Undo restores automatic changes, including the older empty-map cancellation path.
- Analysis Context explains fields, units, joins, counting rules, Atlas node/choice effects and master perks, saved configuration contributions, per-map settings and source-scoped totals. Ordinary SQLite views retain the same explanations and facts in raw backups.
- Excel appends Family Database, Recipe Database, Seed Database and Reference Aliases after the original four sheets, all from the same database read snapshot. Recipe/family IDs join to current reference definitions and family ordinals. Retired identities and missing definitions are explicit.
- Refresh the project README, user/in-app guides, developer instructions, source index and documentation navigation. Extend frozen-build verification to the new export helpers and protect running 1.3.2.14 clients during installation/uninstallation.

HUD layout and controls, screenshot review, manual propagation dropdowns and explicit propagation approval are retained. OCR models, thresholds and mark-recognition rules are unchanged in this release.

## Interpretation limits

Inventory gains are map-level observations, not proven ground drops or rewards attributable to a particular chain step. Reference databases describe recipes; they do not add acquired loot. Percentages summed across maps and Atlas/tablet configuration contributions are not combined applied game bonuses. Missing data, edited historical references, unknown family origins, game stacking/caps and unrecorded outcomes remain explicit limits.

A step's effective rune list contains earlier propagated marks plus the full current recipe's combination; unmarked runes from earlier recipes are not carried forward. Recipe IDs retain identity, but an editable/deleted catalog cannot reconstruct a historical combination that was never frozen.

## Validation and publication

Focused local regression batches passed for completion/recovery, real SQLite contention, Snapshot and listener rollback, reference-category races, all six Atlas rules, frozen/future contexts, Undo, Context calculations, snapshot-consistent reference exports and independently reopened workbooks. The additional empty-map regression was reproduced before correction and covered by restart, first-map and older-profile cases. See [the targeted review](CHANGE_REGRESSION_REVIEW.md) for local scope and counts.

Publication requires the complete regression suite on this exact source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, and installer/update/saved-file-retention checks. The published release body identifies the verified source SHA and Windows run. Native HUD checks use a simulated game view; the 100-map workflow injects recognition results. These checks do not establish new live-game OCR accuracy; the existing captured-image recognition reports remain versioned with their original tested source.
