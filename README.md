# PoE2 Data Logger

PoE2 Data Logger records Expedition remnants and rune chains, map and tablet modifiers, monster counts, inventory currency and items, and Ritual rewards. Everything is saved on your computer, with Excel and CSV exports for analysis.

## Download

Download **PoE2-Data-Logger-Setup-v33.1.exe** from the [release page](https://github.com/Craftyxii/PoE2-Data-Logger/releases/latest). The installer includes the libraries and resources needed to run the app.

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

Install Python 3.12 and NSIS, add `makensis` to PATH, then run **Build.bat** from the repository folder. It installs the build dependencies, packages the app, checks the bundled runtime and OCR models, and creates **PoE2-Data-Logger-Setup-v33.1.exe**.

The source groups in [packaging/sources.py](packaging/sources.py) feed the PyInstaller specification. Add a new app module or resource there when changing the build.

## Credits and licenses

The app incorporates RuneHelper OCR material, currency-overlay / Exiled Exchange 2 material, and DejaVu fonts. Their original notices and provenance are included beside those files. See [DEPENDENCIES.md](DEPENDENCIES.md) and [the branding notice](PoE2_Data_Logger/CRAFTYXII_ASSETS_LICENSE.txt).

PoE2 Data Logger is an independent tool and is not affiliated with or endorsed by Grinding Gear Games.
