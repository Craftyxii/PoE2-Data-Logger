# PoE2 Data Logger

PoE2 Data Logger is a Windows desktop app for recording Expedition remnant recipe groups and rune propagation chains, map and tablet modifiers, monster kill counts, inventory currency and items, and Ritual rewards. It uses local OCR for supported scans, alongside manual controls for map settings, kill counts and chain runes.

It combines your selected tablets, Atlas Master, waystone affixes, area level, biome and city type with your logged Ritual rewards, currency found and other results. Every map receives a unique **Map ID**, and all its records stay linked to that ID. Export the log to an **Excel or CSV spreadsheet** for analysis.

Every remnant receives its own unique **Remnant ID**. On City maps, each expedition and its rune propagation chain are linked to the map through a unique **Expedition ID**. Chain steps retain their order, and each saved chain has a unique **Scan Commit number**.

Currency tracking keeps one current starting inventory and one current ending inventory per map. Repeated scans replace the corresponding totals instead of adding them again. Exports include **before and after counts and net changes**.

**Kills / Currency** shows a clean session counter with each found item's icon, name and total. Approved end scans update the positive gains from each map's start/end pair; repeated scans replace that map's contribution. Resetting IDs clears the counter. Local inventory-reference tools are available in **Data export → Developer Mode**.

Scans and records are processed and stored locally. **The app does not upload your data**; you choose whether to export and share it.

## Download

Download **PoE2-Data-Logger-Setup-v1.2.2-beta.exe** from the [1.2.2 Beta release page](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.2.2-beta). The installer includes the libraries and resources needed to run the app.

The separate **Atlas / Character Settings** tab provides a clickable PoE2 atlas with numbered effect dropdowns, immediate hover descriptions, Autofill and gear item rarity. Excel exports include a companion atlas worksheet linked to each map through its saved Atlas Setup ID.

Kills are recorded separately for Normal, Magic, Rare and Unique monsters. Each accepted propagation scan adds one remnant detonation to its expedition; **Commit chain** saves the ordered runes and advances that map's expedition number.

Approved name corrections in Currency and Ritual review save captured artwork as local recognition examples and create dedicated item-count export columns. Unnamed rows are rejected. Remnant review stays tied to its captured expedition while propagation continues. **Scan settings** offers 1, 2, 4 or 6 CPU OCR threads, applied after restarting the app.

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

Install Python 3.12 and NSIS, add `makensis` to PATH, then run **Build.bat** from the repository folder. It installs the build dependencies, packages the app, checks the bundled runtime and OCR models, and creates **PoE2-Data-Logger-Setup-v1.2.2-beta.exe**.

The source groups in [packaging/sources.py](packaging/sources.py) feed the PyInstaller specification. Add a new app module or resource there when changing the build.

## Credits and licenses

The app incorporates RuneHelper OCR material, currency-overlay / Exiled Exchange 2 material, DejaVu fonts, and game-derived atlas data and artwork. Their original notices and provenance are included beside those files. See [DEPENDENCIES.md](DEPENDENCIES.md) and [the branding notice](PoE2_Data_Logger/CRAFTYXII_ASSETS_LICENSE.txt).

PoE2 Data Logger is an independent tool and is not affiliated with or endorsed by Grinding Gear Games.
