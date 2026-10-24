# PoE2 Data Logger

A Windows desktop logger for Path of Exile 2. Record Expedition remnants and rune propagation, map and tablet settings, monster kills, inventory changes, and Ritual offers. Review captured screenshots and OCR suggestions, correct them, and export the saved data for analysis.

## Download and source status

This source is the **1.3.2.31 Beta candidate**. Its installer is published only after the required Windows checks pass on the exact release source. See the [candidate release notes](docs/RELEASE_1.3.2.31.md). The latest published [1.3.2.30 Beta](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.30-beta) and its [installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.30-beta/PoE2-Data-Logger-Setup-v1.3.2.30-beta.exe), plus the [stable 1.3.2 release](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2), remain available.

This candidate fixes Ritual pages that 1.3.2.30 dropped without a word, confirms every Ritual scan, never logs deferred rewards, names unique flasks on Ritual pages, and reads Rite map names and biomes, on top of 1.3.2.30 Beta. See the [changelog](CHANGELOG.md). Installer, executable and window version labels target 1.3.2.31 Beta; the existing HUD controls, layout and styling are retained.

## What you can record

- **Maps:** Biome, City Type, area level, map options, waystone and tablet modifiers, Atlas Master/perks, Atlas allocations, gear item rarity, and Normal/Magic/Rare/Unique kills. Map IDs connect records to saved settings.
- **Remnants:** visible seed bars, opened reward panels, or both stages linked through one Remnant ID.
- **Propagation:** recipe/family identity and marked runes in socket order. Every propagation scan requires manual approval. Existing rune dropdowns remain available for corrections. Shared family sockets can recover missing crowns from a complete mark or corroborating fragments.
- **Inventory:** approved scans retain map-local IDs and history. Currency totals use positive latest-End-minus-Start gains; a missing Start is treated as empty. Item totals use the latest approved inventory scan for the map, whether Start or End, without subtraction. Rescanning replaces that map's contribution; two separate occupied Item footprints count twice. Inventory counts do not prove individual ground drops.
- **Ritual:** offered rewards, quantities, deferred state, tribute and rerolls. Each non-deferred offer contributes its quantity to new finds; each deferred occurrence contributes zero. Correcting the same page replaces it, while different pages remain independent. Offers do not add to acquired inventory totals.

Screenshot previews remain part of scan review. Possible Currency matches offer editable suggestions; approving or rejecting rows preserves the scroll position. Accepted corrections can teach local icon references. OCR Strictness is set separately for each scan type; it never enables automatic propagation approval.

**Undo** reverses the latest saved action, including scans, chain edits, settings, counts and New map. The last 100 undoable actions survive restarts. Expedition also provides saved-rune corrections, **Discard last chain step**, and **Discard chain**, without deleting remnant logs or actual detonation totals. Current-map log discard is available through the local service dispatcher; it supports Undo and has no additional desktop button. See [the correction notes](docs/CHAIN_DISCARD.md).

The Atlas editor saves allocations and numbered choices to a frozen Atlas Setup ID. In the working source, an allocated city-biome choice can fill the existing Biome selector, such as Faridun → Desert. This represents the Atlas effect's additional classification. Recorded maps retain their saved Atlas; later Atlas edits apply to the next map. Manual Biome editing remains available.

See the [user guide](docs/USER_GUIDE.md) for the full scan, review, chain and map workflow.

## Exports and analysis

The working-source Excel export contains nine worksheets:

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
| Kill Count Readings | Cumulative OCR observations, screenshot evidence and latest-reading status per map |

The first four sheet positions remain unchanged. All nine read one database snapshot. All 441 bundled unique names have named columns in CSV and Excel and SQL reference metadata before they are found. Existing Currency/Omen category labels are retained. This catalog coverage does not create approved observations or register every name on the Currency page. Reference rows explain the data; they are not logged loot. The Context explains how earlier propagated marks combine with a current recipe's full rune combination, as well as missing values, percentage units and limits on attributing loot to individual chain steps. Editable recipe catalogs cannot reconstruct deleted historical combinations or unrecorded outcomes.

CSV folder exports contain four data files: Export, Atlas Character Settings, Scan History and Kill Counts. A raw SQLite backup includes logs, reference tables, stored image bytes, and the SQL-readable Context table and views. Images stored as external files need a separate copy. Export filenames are timestamped to preserve earlier exports.

New saves identify the Item counting policy they use. Existing raw logs remain intact; older records are not rewritten as new observations.

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
