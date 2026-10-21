# PoE2 Data Logger 1.3.2.24 Beta

Currency and Ritual scans now use a bundled, offline unique-item artwork catalog.

- Match 441 canonical unique names across 449 source records and 446 original artwork files. Full equipment footprints use one Item row with quantity 1.
- Keep complete equipment footprints when thin artwork leaves a nearly blank cell inside the item. Blank cells do not become ordinary stack rows.
- Offer name choices for shared or uncertain artwork, including ordinary bases that share a unique's artwork. Preserve grayscale Omen safeguards and local reviewed corrections.
- Save approved inventory uniques as Items through the existing Start/End snapshots, SQL backup, CSV and Excel exports. Preserve Start subtraction and latest-End totals.
- Keep complete equipment artwork for review previews and accepted correction learning. Rejection and Undo retain their existing behavior.
- Cache prepared artwork and exact image evidence with bounded memory. Scanning performs no network downloads.
- Verify the pinned catalog, credits and all original artwork hashes in the Windows build checks. Retain installation/update guards for earlier running beta clients.

HUD layout, existing dropdowns, historical observations, screenshot evidence, manual end-map entries and mandatory Propagation approval are preserved. Source artwork and game data belong to Grinding Gear Games; the [catalog notice](../PoE2_Data_Logger/unique_item_assets/NOTICE.txt) records RePoE provenance and credits.

## Validation

Before release preparation, local feature verification covered 2,149 regression cases. One obsolete assertion expected a captured Mageblood belt to be ignored; its replacement requires one complete Item review row, and its entire 186-test batch passed on rerun. Final coverage was 2,146 passed and 3 skipped. The opt-in 100-map workflow simulation passed separately, including SQL/CSV/XLSX, backup and restart checks. Two native Windows checks require a Windows desktop.

Release preparation additionally checked all 441 unique names through approved Currency and Ritual records into SQL, CSV and XLSX. Each logged name has its Item field and history rows; duplicate stacks, Start subtraction, the latest End snapshot and deferred Ritual offers retain their existing accounting. Thin-artwork regressions cover Oaksworn and Brynhand's Mark, including proof that the former occupancy gate missed their full footprints. Version, installer retention and frozen catalog checks also passed locally. Fresh Windows gates cover the final release source.

All 449 grayscale source renders retained the correct candidate without a wrong clear name. All 277 ordinary currency families were negative in both color and grayscale. These rendered controls do not establish accuracy for every game capture. The uploaded inventory shield remained one Redblade Banner review choice at three resampled scales; a genuine source scan also passed through Qt approval to Item fields in SQL, CSV and XLSX.

On the three-CPU Linux host with two OCR threads, that one captured inventory took 4.28 seconds for its first analysis and 0.218–0.223 seconds for identical repeats. This excludes physical capture and HUD delivery. Dense-inventory, physical GPU and native Windows performance are separate measurements. Original image assets add about 26.8 MiB before installer packaging.

Publication requires fresh checks on the exact committed release source: Windows fixtures, native HUD input, the 100-map simulation, the complete regression suite, frozen runtime/catalog verification, and installer/update/file-retention checks. Published release notes identify the verified source and workflow run.
