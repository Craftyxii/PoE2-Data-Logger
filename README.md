# PoE2 Data Logger

PoE2 Data Logger is a Windows desktop app for recording Expedition remnant recipe groups and rune propagation chains, map and tablet modifiers, monster kill counts, inventory currency and items, and Ritual rewards. It uses local OCR for supported scans, alongside manual controls for map settings, kill counts and chain runes.

It combines your selected tablets, Atlas Master, waystone affixes, area level, biome and city type with your logged Ritual rewards, currency found and other results. Every map receives a unique **Map ID**, and all its records stay linked to that ID. Export the log to an **Excel or CSV spreadsheet** for analysis.

Every remnant receives its own unique **Remnant ID**. On City maps, each expedition and its rune propagation chain are linked to the map through a unique **Expedition ID**. Chain steps retain their order, and each saved chain has a unique **Scan Commit number**.

Currency tracking keeps one current starting inventory and one current ending inventory per map. Repeated scans replace the corresponding totals instead of adding them again. Exports include **before and after counts and net changes**.

**Currency** shows grouped item icons, names and session totals, with found items first. Approved End scans update the positive gains from each map's Start/End pair; missing Start scans mean an empty inventory, and repeated scans replace that map's contribution. Resetting IDs clears the counter. The shared header holds kill counts, which save and clear on **New map**. Local inventory-reference tools are available in **Data export → Developer Mode**.

Scans and records are processed and stored locally. **The app does not upload your data**; you choose whether to export and share it.

## Download

Download the [**1.3.2.6 Beta installer**](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.6-beta/PoE2-Data-Logger-Setup-v1.3.2.6-beta.exe) for the changes in the [beta release notes](docs/RELEASE_1.3.2.6.md). The [1.3.2 stable release](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2) remains available. Both installers include the libraries and resources needed to run the app.

The separate **Atlas / Character Settings** tab provides a clickable PoE2 atlas with numbered effect dropdowns, immediate hover descriptions, Autofill and gear item rarity. Excel exports include a companion atlas worksheet linked to each map through its saved Atlas Setup ID.

Kills are recorded separately for Normal, Magic, Rare and Unique monsters. The live HUD shows centered **Map #** and **Remnant #** indicators at 64 px in wide layouts and 48 px in compact layouts. Each accepted propagation scan adds one remnant detonation to its expedition. Clear scans save their chain parts automatically; **Approve** beside a propagation recipe directly saves that part without changing the Expedition ID and refreshes the editable saved-rune dropdowns on Expedition. Accepted scan parts save while preserving any separate manual draft. Correct saved runes through the Expedition dropdowns, then use **Complete chain** in the header, Review or Expedition to finish the saved chain and advance to the next Expedition ID. Completion waits for an unrelated pending scan to resolve so its captured context stays intact. Propagation review keeps the recipe rows, Approve and Complete controls together. Expedition hides empty manual-draft controls until explicit manual entry is requested, and chain status remains visible when optional guidance is off.

Approved name corrections in Currency and Ritual review save captured artwork as local recognition examples and create dedicated item-count export columns. Unnamed rows are rejected. Remnant review stays tied to its captured expedition while propagation continues. The **OCR Sensitivity** tab has a separate **OCR Strictness** slider for each scan type: **0** accepts more tentative matches, **50** retains the previous behavior, and **100** requires manual confirmation before OCR results save. Quantity, level, family, marked-position and capture-context checks remain enforced. **Scan settings** offers 1, 2, 4 or 6 CPU OCR threads, applied after restarting the app.

See the [user guide](docs/USER_GUIDE.md) for map setup, hotkeys, scans, reviews and exporting your data.

## Source and libraries

| Location | Contents |
| --- | --- |
| [PoE2_Data_Logger/core/](PoE2_Data_Logger/core/) | Logging, records, reference databases and exports |
| [PoE2_Data_Logger/ocr/](PoE2_Data_Logger/ocr/) | OCR adapters, item readers and rune scanning |
| [PoE2_Data_Logger/ui/](PoE2_Data_Logger/ui/) | Desktop interface, review overlay and region editor |
| [PoE2_Data_Logger/platform/](PoE2_Data_Logger/platform/) | Hotkeys, clipboard capture and game-window checks |
| [PoE2_Data_Logger/third_party/](PoE2_Data_Logger/third_party/) | Bundled library code, models, reference data and licenses |
| [packaging/sources.py](packaging/sources.py) | Source and resource groups used by the build |
| [SOURCES.md](SOURCES.md) | Code index, grouped by what each file does |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Runtime libraries, build tools and bundled third-party components |
| [requirements.txt](requirements.txt) | Runtime dependency declarations |
| [requirements-build.txt](requirements-build.txt) | Build dependency declarations |
| [packaging/](packaging/) | PyInstaller and NSIS packaging |
| [tools/](tools/) | Build verification |
| [docs/](docs/) | User guide |

## Run from source

Install Python 3.12. From the repository folder:

```text
py -3.12 -m pip install -r requirements.txt
Start.bat
```

To run without the batch file:

```text
py -3.12 -m PoE2_Data_Logger
```

## Build on Windows

Install Python 3.12 and NSIS, add `makensis` to PATH, then run **Build.bat** from the repository folder. It installs the build dependencies, packages the app, checks the bundled runtime and OCR models, and creates **PoE2-Data-Logger-Setup-v1.3.2.6-beta.exe**.

The source groups in [packaging/sources.py](packaging/sources.py) feed the PyInstaller specification. Add a new app module or resource there when changing the build.

## Credits and licenses

The app incorporates RuneHelper OCR material, currency-overlay / Exiled Exchange 2 material, DejaVu fonts, and game-derived atlas data and artwork. Their original notices and provenance are included beside those files. See [DEPENDENCIES.md](DEPENDENCIES.md) and [the branding notice](PoE2_Data_Logger/CRAFTYXII_ASSETS_LICENSE.txt).

PoE2 Data Logger is an independent tool and is not affiliated with or endorsed by Grinding Gear Games.
