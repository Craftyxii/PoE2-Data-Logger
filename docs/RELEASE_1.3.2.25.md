# PoE2 Data Logger 1.3.2.25 Beta

Currency and Ritual artwork now prepares at desktop startup in the same caches
used by scans. Scanning reuses those prepared banks instead of building a second
startup copy.

- Prepare both existing OCR workers, including stock artwork and scale candidates,
  inventory/Ritual reference transforms, local learned references, Ritual fallback
  icons and all eight unique-item footprints.
- Keep screenshot-dependent result caches separate. A first actual transform query
  still proves exact agreement with the original matcher; thresholds and review
  decisions are unchanged.
- Retain content-based reference invalidation, source-integrity checks and bounded
  memory. The unique footprint limit rises from 32 to 64 MiB so all current banks
  remain available after startup.
- Keep preparation asynchronous and inside the existing worker admission,
  cancellation, deadline and shutdown lifecycle. Replacement Currency/Ritual
  workers prepare within the original scan deadline. Optional bulk-preparation
  failures leave normal on-demand recognition available.
- Protect the previous beta's running client during installation and removal.

HUD layout, screenshots, dropdowns, historical logs, exports, manual end-map
entries, scan-review delivery rules and mandatory Propagation approval remain
unchanged. Preparation takes no screenshots, loads no OCR models or GPU helpers,
and performs no network downloads.

## Validation and measurements

The implementation source passed the full Linux suite: **2,181 passed, three
skipped, zero failures or errors** across 2,184 cases in 12 isolated batches.
The skipped gates were native Windows hotkey APIs, native Windows HUD input and
the opt-in 100-map workflow. Separate Windows release checks are required on the
exact committed release source before publication.

Tests forbid reference PNG decoding, scale construction and reference-transform
construction during warm matching and verify the same cache object identities.
Cold/warm complete scan results agree, all eight unique footprints reuse their
banks, and reference edits and corrupt-source fallback remain covered. Real
process tests cover distinct warmed workers, repeat startup, context changes,
cancellation, replacement, original deadlines, preparation errors and shutdown.

On the Linux test host, both workers finished preparation in **5.019 seconds**.
Combined worker RSS was about **824 MiB before OCR models**, using a fresh profile
without learned references. These are startup-artwork measurements, not Windows
capture-to-review latency or GPU benchmarks. Database table contents, settings
and CSV bytes were identical before and after preparation; no scan observations
were created. See [the validation report](STARTUP_ARTWORK_CACHE_VALIDATION.md).

Published release notes identify the verified source and Windows workflow run.
Publication requires Windows fixtures, native HUD input, the 100-map simulation,
the complete regression suite, frozen-runtime checks, installer/update/file
retention checks and the installer checksum.
