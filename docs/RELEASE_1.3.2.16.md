# PoE2 Data Logger 1.3.2.16 Beta

This beta fixes Excel and CSV exports being blocked by an unsaved Atlas / Character Settings draft and redirecting to the Atlas page before creating a file. The **Save XLSX/CSV to folder** buttons and **Save Export XLSX/CSV as…** menu actions now export saved data while retaining the current page and the Atlas editor draft.

The functional change is limited to the two shared spreadsheet export admission checks. Excel and CSV continue to use each map's recorded Atlas setup and the saved database; exporting does not save pending Atlas edits. Explicitly saving an Atlas draft still records it through the existing save action. CSV companion generation, SQLite backups, OCR, logging, HUD controls and the eight-sheet Context/reference workbook are retained from [1.3.2.15](RELEASE_1.3.2.15.md).

Release metadata identifies 1.3.2.16 Beta and retains existing protection for running earlier clients, including 1.3.2.15.

## Validation and publication

Targeted tests exercise the actual Excel/CSV buttons and File-menu actions with unsaved Atlas edits, independent reopening of all eight sheets, unfinished rarity text from the File menu, cancellation, explicit Atlas saving, and the real export workers' Qt callbacks. CSV companion writes, overwrite/cancellation decisions and SQLite backups are checked for regressions.

Publication requires the complete regression suite on this exact source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, installer/update/saved-file-retention checks, and the installer checksum. The published release body identifies the verified source SHA and Windows run. Native HUD and session fixtures do not establish new live-game OCR accuracy; OCR recognition is unchanged in this release.
