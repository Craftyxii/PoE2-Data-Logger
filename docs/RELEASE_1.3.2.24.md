# PoE2 Data Logger 1.3.2.24 Beta

Currency and Ritual scans now use a bundled, offline unique-item artwork catalog.

- Match 441 canonical unique names across 449 source records and 446 original artwork files. Full equipment footprints use one Item row with quantity 1.
- Keep complete equipment footprints when thin artwork leaves a nearly blank cell inside the item. Blank cells do not become ordinary stack rows.
- Offer name choices for shared or uncertain artwork, including ordinary bases that share a unique's artwork. Preserve grayscale Omen safeguards and local reviewed corrections.
- Include all 441 bundled unique names in SQL export reference metadata and named CSV/Excel columns before they are found, retaining existing Currency/Omen category labels without creating approved observations or adding every name to the Currency page.
- Count inventory Items from the latest approved scan for the map, whether Start or End, without Start subtraction. Rescanning replaces that contribution; two separate occupied Item footprints count twice. Currency retains positive latest-End-minus-Start gains and an assumed empty Start when unscanned.
- Count Ritual Items per offer occurrence using the Omen rule: deferred offers contribute zero new finds, and non-deferred offers contribute their quantity. A correction to the same page replaces it; different pages remain independent. Ritual offers do not become inventory acquisitions.
- Keep complete equipment artwork for review previews and accepted correction learning. Rejection and Undo retain their existing behavior.
- Cache prepared artwork and exact image evidence with bounded memory. Scanning performs no network downloads.
- Verify the pinned catalog, credits and all original artwork hashes in the Windows build checks. Retain installation/update guards for earlier running beta clients.

HUD layout, existing dropdowns, historical observations, screenshot evidence, manual end-map entries and mandatory Propagation approval are preserved. Existing raw logs are not rewritten; new saves mark their Item counting policy. Source artwork and game data belong to Grinding Gear Games; the [catalog notice](../PoE2_Data_Logger/unique_item_assets/NOTICE.txt) records RePoE provenance and credits.

## Validation

Before release preparation, local feature verification covered 2,149 regression cases. One obsolete assertion expected a captured Mageblood belt to be ignored; its replacement requires one complete Item review row, and its entire 186-test batch passed on rerun. Final coverage was 2,146 passed and 3 skipped. The opt-in 100-map workflow simulation passed separately, including SQL/CSV/XLSX, backup and restart checks. Two native Windows checks require a Windows desktop.

The updated source passed 67 focused accounting, Currency, export and Analysis Context checks. A separate, overlapping 28-test batch passed source-artwork integration, all 441 observed and unobserved export names, independent Item counts, legacy records, restart, Undo and map-summary checks. Separate Headhunter footprints remain two Items; a Ritual deferred marker applies only to its own footprint. SQL backup, CSV and numeric XLSX values agree. Thin-artwork regressions cover Oaksworn and Brynhand's Mark, including proof that the former occupancy gate missed their full footprints. Version, installer retention and frozen catalog checks also passed locally. Exact-source Windows verification remains required before publication.

All 449 grayscale source renders retained the correct candidate without a wrong clear name. All 277 ordinary currency families were negative in both color and grayscale. These rendered controls do not establish accuracy for every game capture. The uploaded inventory shield remained one Redblade Banner review choice at three resampled scales; a genuine source scan also passed through Qt approval to Item fields in SQL, CSV and XLSX.

On the three-CPU Linux host with two OCR threads, that one captured inventory took 4.28 seconds for its first analysis and 0.218–0.223 seconds for identical repeats. This excludes physical capture and HUD delivery. Dense-inventory, physical GPU and native Windows performance are separate measurements. Original image assets add about 26.8 MiB before installer packaging.

Publication requires fresh checks on the exact committed release source: Windows fixtures, native HUD input, the 100-map simulation, the complete regression suite, frozen runtime/catalog verification, and installer/update/file-retention checks. The earlier build run was canceled after the requirements changed and does not qualify the new source. Published release notes identify the verified source and workflow run.
