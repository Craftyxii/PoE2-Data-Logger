# PoE2 Data Logger

A Windows desktop logger for Path of Exile 2. Record Expedition remnants and rune propagation, map and tablet settings, monster kills, inventory changes, and Ritual offers. Review captured screenshots and OCR suggestions, correct them, and export the saved data for analysis.

## Download and source status

This source targets [1.3.2.20 Beta](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.20-beta). Its [installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.20-beta/PoE2-Data-Logger-Setup-v1.3.2.20-beta.exe) becomes available after the required release checks pass. See the [release notes](docs/RELEASE_1.3.2.20.md). The [previous beta](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.19-beta) and [stable 1.3.2 release](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2) remain available.

This beta retains immediate Remnant-to-Propagation requests, caches exact OCR evidence, saves unique opened-family matches, and preserves Ritual pages and recognized readings during database contention. Currency and kill readings keep cumulative scan history per map; only their dedicated keys trigger those OCR modes. Manual end-map entries, full-family autofill, screenshot evidence and explicit propagation approval remain intact. See the [changelog](CHANGELOG.md#13220-beta). Installer, executable and window version labels identify 1.3.2.20 Beta.

## What you can record

- **Maps:** Biome, City Type, area level, map options, waystone and tablet modifiers, Atlas Master/perks, Atlas allocations, gear item rarity, and Normal/Magic/Rare/Unique kills. Map IDs connect records to saved settings.
- **Remnants:** visible seed bars, opened reward panels, or both stages linked through one Remnant ID.
- **Propagation:** recipe/family identity and marked runes in socket order. Every propagation scan requires manual approval. Existing rune dropdowns remain available for corrections. Shared family sockets can recover missing crowns from a complete mark or corroborating fragments.
- **Inventory:** one approved Start and End snapshot per map. Rescans replace that phase rather than adding it again. Session totals sum positive End-minus-Start gains; a missing Start is treated as empty. Inventory gains do not prove individual ground drops.
- **Ritual:** offered rewards, quantities, deferred state, tribute and rerolls. Offers do not add to acquired inventory totals.

Screenshot previews remain part of scan review. Possible Currency matches offer editable suggestions; approving or rejecting rows preserves the scroll position. Accepted corrections can teach local icon references. OCR Strictness is set separately for each scan type; it never enables automatic propagation approval.

**Undo** reverses the latest saved action, including scans, chain edits, settings, counts and New map. The last 100 undoable actions survive restarts. Expedition also provides saved-rune corrections, **Discard last chain step**, and **Discard chain**, without deleting remnant logs or actual detonation totals. Current-map log discard is available through the local service dispatcher; it supports Undo and has no additional desktop button. See [the correction notes](docs/CHAIN_DISCARD.md).

The Atlas editor saves allocations and numbered choices to a frozen Atlas Setup ID. In the working source, an allocated city-biome choice can fill the existing Biome selector, such as Faridun → Desert. This represents the Atlas effect's additional classification. Recorded maps retain their saved Atlas; later Atlas edits apply to the next map. Manual Biome editing remains available.

See the [user guide](docs/USER_GUIDE.md) for the full scan, review, chain and map workflow.

## Exports and analysis

The working-source Excel export contains eight worksheets:

| Worksheet | Contents |
| --- | --- |
| Export | Map totals, activity/recipe rows and acquired item columns |
| Atlas Character Settings | Saved Atlas setups, gear rarity and linked Map IDs |
| Scan History | Scan approvals, corrections, offers and audit records |
| Analysis Context | Field definitions, units, counting rules, joins, saved Atlas effects, scoped totals and analysis limits |
| Family Database | Current family membership, recipe IDs, family order and rune combinations |
| Recipe Database | Current recipe definitions and retained identities for retired recipes |
| Seed Database | Reference seed sockets, rune labels and reward mappings |
| Reference Aliases | Reference aliases and their recipe targets |

The first four sheet positions remain unchanged. All eight read one database snapshot. Reference rows explain the data; they are not logged loot. The Context explains how earlier propagated marks combine with a current recipe's full rune combination, as well as missing values, percentage units and limits on attributing loot to individual chain steps. Editable recipe catalogs cannot reconstruct deleted historical combinations or unrecorded outcomes.

CSV folder exports contain the three data files: Export, Atlas Character Settings and Scan History. A raw SQLite backup includes logs, reference tables, stored image bytes, and the SQL-readable Context table and views. Images stored as external files need a separate copy. Export filenames are timestamped to preserve earlier exports.

## Local data and game interaction

Processing and storage are local; the app does not upload your logs. The default profile is `~/Runeshape Scan`, including `scans.sqlite3` and external images. `RUNESHAPE_SCAN_DATA_DIR` selects a different profile. Back up your records before resetting a session or OCR references.

Live hotkey scans read screen captures while the game is in the foreground; saved images can also be reviewed offline. The optional tooltip-copy path sends an ordinary Ctrl+C and reads clipboard text. The app does not inject code, read game process memory, or modify game files. The optional Discord link opens your browser when clicked.

## Run from source

Use Python 3.12. On Windows, from the checkout root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m PoE2_Data_Logger
```

Alternatively, install the runtime requirements for the Python selected by `py -3.12`, then use [Start.bat](Start.bat). The installer already bundles its runtime and resources.

## Development and Windows builds

[CONTRIBUTING.md](CONTRIBUTING.md) describes the source layout, isolated test profiles, regression commands and release checks. [SOURCES.md](SOURCES.md) indexes the application modules; [DEPENDENCIES.md](DEPENDENCIES.md) lists libraries and notices.

For a Windows installer, install Python 3.12 and NSIS with `makensis` on PATH, then run [Build.bat](Build.bat). It installs build dependencies, packages the app, verifies bundled runtime/model assets, and creates the installer named by [installer.nsi](packaging/installer.nsi). Building alone does not complete the native HUD, full-suite, 100-map or installer/update release gates in [the release workflow](.github/workflows/release.yml).

## Documentation and licenses

The [documentation index](docs/README.md) separates current guides from historical release and audit evidence. Published release notes describe their specific source revisions; unreleased changes are listed separately.

Bundled components include RuneHelper, currency-overlay / Exiled Exchange 2, DejaVu fonts, and game-derived Atlas data/artwork. Their notices remain alongside the files. See [DEPENDENCIES.md](DEPENDENCIES.md) and the [branding notice](PoE2_Data_Logger/CRAFTYXII_ASSETS_LICENSE.txt).

PoE2 Data Logger is an independent tool and is not affiliated with or endorsed by Grinding Gear Games.
