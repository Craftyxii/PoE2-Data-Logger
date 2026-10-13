# PoE2 Data Logger 1.3.2.16 Beta

Excel and CSV export now automatically save a valid pending Atlas / Character Settings draft through the existing save action, then create the export without redirecting to the Atlas page. This applies to folder export buttons and Save As menu actions.

Automatic saving occurs only for a confirmed spreadsheet export with a pending Atlas draft. Clean settings create no additional Atlas save. Cancelling Save As or declining CSV companion overwrite leaves the draft unsaved. Unfinished/invalid Atlas values or a failed save retain the draft and report an error without queuing export.

Existing Atlas saving rules are retained: maps with recorded activity keep their frozen setup, and changes apply to the next eligible map. CSV companion generation, SQLite backups, OCR, other logging rules, HUD controls and the eight-sheet Context/reference workbook remain as in [1.3.2.15](RELEASE_1.3.2.15.md). The functional change is confined to saving pending Atlas edits at the spreadsheet export entrypoints.

Release metadata identifies 1.3.2.16 Beta and retains protection for running earlier clients, including 1.3.2.15.

## Validation and publication

Targeted tests exercise the actual Excel/CSV buttons and menu actions, automatic Atlas saving, independent reopening of all eight Excel sheets, CSV companions, real export workers, clean settings, cancellation, invalid drafts, storage failure, frozen-map behavior and SQLite backups.

Publication requires the complete regression suite on this exact source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, installer/update/saved-file-retention checks, and the installer checksum. The published release body identifies the verified source SHA and Windows run. Native HUD and session fixtures do not establish new live-game OCR accuracy; OCR recognition is unchanged in this release.
