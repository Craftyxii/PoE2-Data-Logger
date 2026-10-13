# PoE2 Data Logger 1.3.2.16 Beta

This beta fixes Excel export being blocked by an unsaved Atlas / Character Settings draft and redirecting to the Atlas page before creating a file. Both **Save XLSX to folder** and **Save Export XLSX as…** now export saved data while retaining the current page and the Atlas editor draft.

The functional change is limited to Excel's two export admission checks. Excel continues to use each map's recorded Atlas setup and the saved database; exporting does not save pending Atlas edits. Explicitly saving an Atlas draft still records it through the existing save action. CSV and SQLite exports, OCR, logging, HUD controls and the eight-sheet Context/reference workbook are retained from [1.3.2.15](RELEASE_1.3.2.15.md).

Release metadata identifies 1.3.2.16 Beta and retains existing protection for running earlier clients, including 1.3.2.15.

## Validation and publication

Targeted tests exercise the actual Excel button and File-menu action with unsaved Atlas edits, independent reopening of all eight sheets, unfinished rarity text from the File menu, cancellation, explicit Atlas saving, and the real export worker's Qt callback. Existing CSV save requirements and SQLite backups are checked for regressions.

Publication requires the complete regression suite on this exact source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, installer/update/saved-file-retention checks, and the installer checksum. The published release body identifies the verified source SHA and Windows run. Native HUD and session fixtures do not establish new live-game OCR accuracy; OCR recognition is unchanged in this release.
